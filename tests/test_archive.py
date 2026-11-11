import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from algoverse.data.archive import ArchiveConfig, archive, load_archive_config

IST = ZoneInfo("Asia/Kolkata")
EVENING = datetime(2026, 11, 11, 17, 0, tzinfo=IST)
MORNING = datetime(2026, 11, 11, 11, 0, tzinfo=IST)
CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


class FakeS3:
    def __init__(self, fail=()):
        self.objects: dict[str, bytes] = {}
        self.fail = set(fail)
        self.deleted = False

    def upload_file(self, filename, bucket, key, ExtraArgs=None):
        assert bucket == "bucket" and ExtraArgs == {"ServerSideEncryption": "AES256"}
        if any(part in key for part in self.fail):
            raise OSError("network down")
        self.objects[key] = Path(filename).read_bytes()

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise KeyError(Key)
        return {"ContentLength": len(self.objects[Key])}

    def delete_object(self, **_):
        self.deleted = True


def config(**overrides):
    return ArchiveConfig(bucket="bucket", prefix="algoverse", **overrides)


def write(path: Path, text="x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def seed(data: Path) -> None:
    write(data / "ticks/date=2026-11-10/day.parquet", "yesterday ticks")
    write(data / "ticks/date=2026-11-11/day.parquet", "today ticks")
    write(data / "chains/underlying=NIFTY/date=2026-11-11/101500_2026-11-17.parquet", "chain")
    write(data / "candles/NIFTY.parquet", "candles")
    write(data / "upstox_token.json", "secret")
    write(data / "live/latest.parquet", "live")
    write(data / "dashboard/market.json", "cache")
    with sqlite3.connect(data / "paper.sqlite") as db:
        db.execute("CREATE TABLE t (x)")
        db.execute("INSERT INTO t VALUES (1)")


def test_finished_days_and_other_data_are_uploaded_under_the_prefix(tmp_path):
    seed(tmp_path)
    s3 = FakeS3()
    result = archive(s3, config(), tmp_path, EVENING)
    assert set(s3.objects) == {
        "algoverse/ticks/date=2026-11-10/day.parquet",
        "algoverse/ticks/date=2026-11-11/day.parquet",
        "algoverse/chains/underlying=NIFTY/date=2026-11-11/101500_2026-11-17.parquet",
        "algoverse/candles/NIFTY.parquet",
        "algoverse/db/date=2026-11-11/paper.sqlite",
    }
    assert result.uploaded == 4 and result.databases == 1 and not result.failures
    assert not s3.deleted


def test_secrets_and_caches_are_never_uploaded(tmp_path):
    seed(tmp_path)
    s3 = FakeS3()
    archive(s3, config(), tmp_path, EVENING)
    assert not any(
        k.endswith(("upstox_token.json", "latest.parquet", "market.json")) for k in s3.objects
    )


def test_today_waits_until_the_day_is_finished(tmp_path):
    seed(tmp_path)
    s3 = FakeS3()
    result = archive(s3, config(), tmp_path, MORNING)
    assert "algoverse/ticks/date=2026-11-10/day.parquet" in s3.objects
    assert not any("2026-11-11" in k and "/db/" not in k for k in s3.objects)
    assert result.waiting_days == {"2026-11-11"}


def test_a_day_with_unmerged_ticks_waits(tmp_path):
    seed(tmp_path)
    write(tmp_path / "ticks/date=2026-11-10/part-1.parquet")
    s3 = FakeS3()
    result = archive(s3, config(), tmp_path, EVENING)
    assert not any("2026-11-10" in k for k in s3.objects)
    assert "2026-11-10" in result.waiting_days


def test_second_run_uploads_only_changes(tmp_path):
    seed(tmp_path)
    s3 = FakeS3()
    archive(s3, config(), tmp_path, EVENING)
    write(tmp_path / "candles/NIFTY.parquet", "more candles")
    result = archive(s3, config(), tmp_path, EVENING)
    assert result.uploaded == 1 and result.unchanged == 3
    assert s3.objects["algoverse/candles/NIFTY.parquet"] == b"more candles"


def test_failed_files_are_retried_next_time(tmp_path):
    seed(tmp_path)
    result = archive(FakeS3(fail={"candles"}), config(), tmp_path, EVENING)
    assert list(result.failures) == ["candles/NIFTY.parquet"]
    s3 = FakeS3()
    assert archive(s3, config(), tmp_path, EVENING).uploaded == 1
    assert "algoverse/candles/NIFTY.parquet" in s3.objects


def test_old_ticks_are_removed_only_once_s3_confirms_them(tmp_path):
    seed(tmp_path)
    s3 = FakeS3()
    later = datetime(2026, 11, 12, 17, 0, tzinfo=IST)
    result = archive(s3, config(keep_tick_days=1), tmp_path, later)
    assert result.pruned_days == ["2026-11-10"]
    assert not (tmp_path / "ticks/date=2026-11-10").exists()
    assert (tmp_path / "ticks/date=2026-11-11").exists()
    assert (tmp_path / "chains/underlying=NIFTY/date=2026-11-11").exists()


def test_ticks_missing_from_s3_are_kept(tmp_path):
    seed(tmp_path)
    later = datetime(2026, 11, 12, 17, 0, tzinfo=IST)
    result = archive(
        FakeS3(fail={"ticks/date=2026-11-10"}), config(keep_tick_days=1), tmp_path, later
    )
    assert result.pruned_days == []
    assert (tmp_path / "ticks/date=2026-11-10/day.parquet").exists()


def test_dry_run_uploads_nothing(tmp_path):
    seed(tmp_path)
    s3 = FakeS3()
    result = archive(s3, config(), tmp_path, EVENING, dry_run=True)
    assert result.uploaded == 4 and s3.objects == {}


@pytest.mark.parametrize("prefix", ["", "/", "ohlc_new", "ohlc_new/algoverse"])
def test_upload_folder_must_avoid_the_history_source(prefix):
    with pytest.raises(ValueError):
        ArchiveConfig(bucket="bucket", prefix=prefix)


def test_config_file_targets_its_own_folder():
    loaded = load_archive_config(CONFIG_DIR)
    assert loaded.prefix == "algoverse/" and loaded.keep_tick_days == 30
    assert "ohlc_new/" in loaded.protected_prefixes
