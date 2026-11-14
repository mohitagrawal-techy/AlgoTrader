import gzip
import hashlib
import io
import json
import zipfile
from datetime import date
from pathlib import Path

import boto3
import pandas as pd
import pytest
from algoverse.backtest.bhavcopy import COLUMNS, history_dir, store_day
from algoverse.backtest.dataview import History
from algoverse.backtest.s3_history import (
    S3AccessError,
    S3Config,
    load_s3_config,
    parse_object,
    pull_history,
)
from botocore.exceptions import ClientError, NoCredentialsError
from botocore.response import StreamingBody
from botocore.stub import Stubber
from conftest import CONFIG_DIR
from test_backtest import UDIFF_HEADER, UDIFF_ROW

CONFIG = S3Config(bucket="data-bucket", prefix="fo/")
SYMBOLS = ["NIFTY"]
UDIFF = (UDIFF_HEADER + UDIFF_ROW).encode()
DAY = date(2026, 11, 3)
OTHER_DAY = date(2026, 11, 4)


def store_rows(day=DAY, symbol="NIFTY"):
    return pd.DataFrame(
        [
            {
                "date": day.isoformat(),
                "symbol": symbol,
                "instrument": "OPT",
                "expiry": "2026-11-10",
                "strike": 24000,
                "option_type": "CE",
                "open": 100,
                "high": 110,
                "low": 90,
                "close": 105,
                "settle": 105,
                "volume": 6500,
                "oi": 13000,
                "oi_change": 650,
                "lot_size": 65,
                "underlying_price": 24010,
            },
            {
                "date": day.isoformat(),
                "symbol": symbol,
                "instrument": "FUT",
                "expiry": "2026-11-24",
                "strike": 0,
                "option_type": "XX",
                "open": 24000,
                "high": 24050,
                "low": 23950,
                "close": 24020,
                "settle": 24020,
                "volume": 650,
                "oi": 1300,
                "oi_change": 0,
                "lot_size": 65,
                "underlying_price": 24010,
            },
        ],
        columns=COLUMNS,
    )


def parquet_bytes(frame):
    buffer = io.BytesIO()
    frame.to_parquet(buffer, index=False)
    return buffer.getvalue()


def zipped(content, name="file.csv"):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, content)
    return buffer.getvalue()


class FakeS3:
    def __init__(self, objects, page_size=2):
        self.objects = dict(objects)
        self.page_size = page_size
        self.downloads = []
        self.footers = []

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        keys = sorted(key for key in self.objects if key.startswith(Prefix))
        start = int(ContinuationToken or 0)
        chunk = keys[start : start + self.page_size]
        more = start + self.page_size < len(keys)
        page = {
            "Contents": [
                {
                    "Key": key,
                    "ETag": hashlib.md5(self.objects[key]).hexdigest(),
                    "Size": len(self.objects[key]),
                }
                for key in chunk
            ],
            "IsTruncated": more,
        }
        if more:
            page["NextContinuationToken"] = str(start + self.page_size)
        return page

    def get_object(self, Bucket, Key, Range=None):
        if Range:
            self.footers.append(Key)
            return {"Body": io.BytesIO(self.objects[Key][int(Range.split("-")[-1]) * -1 :])}
        self.downloads.append(Key)
        return {"Body": io.BytesIO(self.objects[Key])}


def pull(client, data_dir, **options):
    return pull_history(client, CONFIG, data_dir, SYMBOLS, {}, **options)


def test_bhavcopy_csv_is_imported(tmp_path):
    result = pull(FakeS3({"fo/a.csv": UDIFF}), tmp_path)
    assert result.failed == {} and len(result.sessions) == 1
    assert History(tmp_path).sessions == result.sessions


@pytest.mark.parametrize(
    ("key", "content"),
    [
        ("fo/a.zip", zipped(UDIFF)),
        ("fo/a.csv.gz", gzip.compress(UDIFF)),
        ("fo/A.CSV", UDIFF),
    ],
)
def test_compressed_and_upper_case_files_work(tmp_path, key, content):
    result = pull(FakeS3({key: content}), tmp_path)
    assert result.failed == {} and len(result.sessions) == 1


def test_parquet_in_store_layout_is_split_by_date(tmp_path):
    frame = pd.concat([store_rows(DAY), store_rows(OTHER_DAY)], ignore_index=True)
    result = pull(FakeS3({"fo/all.parquet": parquet_bytes(frame)}), tmp_path)
    assert result.sessions == [DAY, OTHER_DAY]
    day = History(tmp_path).day(DAY)
    assert list(day.columns) == COLUMNS and len(day) == 2
    assert day.loc[day["instrument"] == "FUT", "option_type"].isna().all()
    assert not History(tmp_path).bars("NIFTY").empty


def test_csv_in_store_layout_is_accepted(tmp_path):
    result = pull(FakeS3({"fo/a.csv": store_rows().to_csv(index=False).encode()}), tmp_path)
    assert result.sessions == [DAY]


def test_other_symbols_are_left_out():
    frames = parse_object("a.parquet", parquet_bytes(store_rows(symbol="NIFTY")), SYMBOLS, {})
    assert set(frames[0]["symbol"]) == {"NIFTY"}
    with pytest.raises(ValueError, match="no rows"):
        parse_object("a.parquet", parquet_bytes(store_rows(symbol="RELIANCE")), SYMBOLS, {})


def test_all_pages_of_a_listing_are_read(tmp_path):
    objects = {f"fo/n{i}.txt": b"x" for i in range(5)}
    result = pull(FakeS3(objects), tmp_path, dry_run=True)
    assert result.listed == 5 and result.ignored == 5


def test_only_the_prefix_is_read(tmp_path):
    result = pull(FakeS3({"fo/a.csv": UDIFF, "other/b.csv": UDIFF}), tmp_path, dry_run=True)
    assert result.listed == 1


def test_dry_run_downloads_and_writes_nothing(tmp_path):
    client = FakeS3({"fo/a.csv": UDIFF})
    result = pull(client, tmp_path, dry_run=True)
    assert result.pending == 1 and client.downloads == []
    assert not history_dir(tmp_path).exists()


def test_unchanged_files_are_skipped_on_the_next_pull(tmp_path):
    client = FakeS3({"fo/a.csv": UDIFF})
    pull(client, tmp_path)
    again = pull(client, tmp_path)
    assert again.unchanged == 1 and again.pending == 0 and len(client.downloads) == 1


def test_a_changed_file_is_imported_again(tmp_path):
    client = FakeS3({"fo/a.csv": UDIFF})
    pull(client, tmp_path)
    client.objects["fo/a.csv"] = UDIFF + b"\n"
    assert pull(client, tmp_path).pending == 1


def test_one_bad_file_stops_only_itself_and_is_retried(tmp_path):
    client = FakeS3({"fo/bad.csv": b"nope,nothing\n1,2\n", "fo/good.csv": UDIFF})
    result = pull(client, tmp_path)
    assert len(result.sessions) == 1 and "fo/bad.csv" in result.failed
    assert pull(client, tmp_path).pending == 1


@pytest.mark.parametrize(
    ("key", "content", "reason"),
    [
        ("fo/a.zip", b"not a zip", "damaged"),
        ("fo/a.csv.gz", b"not gzip", "damaged"),
        ("fo/a.zip", zipped(b"x", "notes.txt"), "exactly one CSV"),
        ("fo/a.parquet", b"not parquet", "not a readable"),
        ("fo/a.parquet", parquet_bytes(pd.DataFrame({"x": [1]})), "missing columns"),
    ],
)
def test_bad_files_explain_themselves(tmp_path, key, content, reason):
    result = pull(FakeS3({key: content}), tmp_path)
    assert reason in result.failed[key]


def test_limit_imports_a_sample_and_the_rest_later(tmp_path):
    client = FakeS3({"fo/a.csv": UDIFF, "fo/b.csv": UDIFF})
    pull(client, tmp_path, limit=1)
    assert len(client.downloads) == 1
    assert pull(client, tmp_path).unchanged == 1


def test_bucket_data_is_protected_from_the_daily_capture(tmp_path):
    frame = store_rows()
    written = tmp_path / "history" / "upstox" / "written.json"
    written.parent.mkdir(parents=True)
    written.write_text(json.dumps([DAY.isoformat(), "2026-11-05"]))
    store_day(tmp_path, frame.assign(date=DAY, expiry=date(2026, 11, 10)))
    pull(FakeS3({"fo/a.parquet": parquet_bytes(frame)}), tmp_path)
    assert json.loads(written.read_text()) == ["2026-11-05"]


def test_missing_bucket_setting_is_explained(tmp_path):
    with pytest.raises(S3AccessError, match="Set bucket"):
        pull_history(FakeS3({}), S3Config(), tmp_path, SYMBOLS, {})


class FailingS3(FakeS3):
    def __init__(self, error):
        super().__init__({})
        self.error = error

    def list_objects_v2(self, **_):
        raise self.error


@pytest.mark.parametrize(
    ("code", "text"),
    [
        ("AccessDenied", "s3:ListBucket"),
        ("NoSuchBucket", "does not exist"),
        ("InvalidAccessKeyId", "access key"),
        ("PermanentRedirect", "region"),
    ],
)
def test_access_problems_are_explained(tmp_path, code, text):
    error = ClientError({"Error": {"Code": code}}, "ListObjectsV2")
    with pytest.raises(S3AccessError, match=text):
        pull(FailingS3(error), tmp_path)


def test_missing_credentials_are_explained(tmp_path):
    with pytest.raises(S3AccessError, match="No AWS credentials"):
        pull(FailingS3(NoCredentialsError()), tmp_path)


def test_a_real_client_sends_the_expected_requests(tmp_path):
    client = boto3.client(
        "s3", region_name="ap-south-1", aws_access_key_id="test", aws_secret_access_key="test"
    )
    with Stubber(client) as stub:
        stub.add_response(
            "list_objects_v2",
            {
                "Contents": [{"Key": "fo/a.csv", "ETag": '"abc"', "Size": len(UDIFF)}],
                "IsTruncated": False,
            },
            {"Bucket": "data-bucket", "Prefix": "fo/"},
        )
        stub.add_response(
            "get_object",
            {"Body": StreamingBody(io.BytesIO(UDIFF), len(UDIFF))},
            {"Bucket": "data-bucket", "Key": "fo/a.csv"},
        )
        result = pull(client, tmp_path)
    assert result.failed == {} and len(result.sessions) == 1


def test_config_file_holds_no_credentials():
    config = load_s3_config(CONFIG_DIR)
    assert config.region
    text = (CONFIG_DIR / "history_s3.yaml").read_text()
    assert "aws_secret" not in text.split("\n", 2)[2].lower()


def test_this_module_never_writes_to_s3():
    source = (
        Path(__file__).resolve().parents[1] / "backend/algoverse/backtest/s3_history.py"
    ).read_text()
    for method in ("put_object", "delete_object", "upload_file", "copy_object"):
        assert method not in source


def test_a_candle_dataset_is_refused_from_its_footer_without_downloading(tmp_path):
    frame = pd.DataFrame(
        {
            "candle_time": pd.to_datetime(["2024-12-31 03:45:00"]),
            "instrument_key": ["NSE_EQ|INE0LLY01014"],
            "timeframe": ["1m"],
            "open": [1.0],
            "high": [1.0],
            "low": [1.0],
            "close": [1.0],
            "volume": [1],
            "open_interest": [0],
        }
    )
    client = FakeS3({"fo/part-0.snappy.parquet": parquet_bytes(frame)})
    result = pull(client, tmp_path)
    assert client.downloads == [] and client.footers == ["fo/part-0.snappy.parquet"]
    assert "per-instrument candles" in result.failed["fo/part-0.snappy.parquet"]


def test_a_store_layout_parquet_passes_the_footer_check(tmp_path):
    client = FakeS3({"fo/all.parquet": parquet_bytes(store_rows())})
    assert pull(client, tmp_path).sessions == [DAY] and client.downloads == ["fo/all.parquet"]
