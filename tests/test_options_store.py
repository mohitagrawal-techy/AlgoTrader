from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
from algoverse.data.archive import ArchiveConfig, day_finished
from algoverse.data.options import (
    compact_chains,
    migrate_old_layout,
    read_chains,
    read_contracts,
    snapshot_files,
    write_contracts,
    write_snapshot,
)
from algoverse.data.store import SnapshotStore

IST = ZoneInfo("Asia/Kolkata")
DAY = date(2026, 11, 11)
DURING = datetime(2026, 11, 11, 11, 0, tzinfo=IST)
AFTER_CLOSE = datetime(2026, 11, 11, 16, 0, tzinfo=IST)
FOLDER = "options/chains/segment=NSE_FO/underlying=NIFTY/date=2026-11-11"


def chain(ts: datetime, expiry: str = "2026-11-17", strikes=(24000.0, 24100.0)) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts": pd.Timestamp(ts),
            "instrument_key": [f"NSE_FO|{int(s)}" for s in strikes],
            "expiry": date.fromisoformat(expiry),
            "strike": list(strikes),
            "option_type": "CE",
            "ltp": 100.0,
        }
    )


def snap(tmp_path, hour: int, minute: int, expiry: str = "2026-11-17"):
    ts = datetime(2026, 11, 11, hour, minute, tzinfo=IST)
    return write_snapshot(tmp_path, "NIFTY", ts, chain(ts, expiry), expiry)


def test_snapshots_are_filed_by_segment_underlying_and_day(tmp_path):
    path = snap(tmp_path, 9, 20)
    assert path.relative_to(tmp_path).as_posix() == f"{FOLDER}/snapshot-092000-2026-11-17.parquet"


def test_finished_days_compact_into_one_sorted_file(tmp_path):
    snap(tmp_path, 9, 25, "2026-11-23")
    snap(tmp_path, 9, 20)
    snap(tmp_path, 9, 25)
    result = compact_chains(tmp_path, AFTER_CLOSE)
    folder = tmp_path / FOLDER
    assert result.snapshots == 3 and snapshot_files(folder) == []
    day = pd.read_parquet(folder / "part.parquet")
    assert len(day) == 6
    assert list(day["ts"].dt.strftime("%H%M")) == ["0920"] * 2 + ["0925"] * 4


def test_the_session_in_progress_is_not_compacted(tmp_path):
    snap(tmp_path, 9, 20)
    assert compact_chains(tmp_path, DURING).snapshots == 0
    assert len(snapshot_files(tmp_path / FOLDER)) == 1


def test_late_snapshots_fold_into_the_compacted_day(tmp_path):
    snap(tmp_path, 9, 20)
    compact_chains(tmp_path, AFTER_CLOSE)
    snap(tmp_path, 18, 0)
    compact_chains(tmp_path, datetime(2026, 11, 12, 9, 0, tzinfo=IST))
    assert len(pd.read_parquet(tmp_path / FOLDER / "part.parquet")) == 4


def test_reads_combine_compacted_and_loose_snapshots(tmp_path):
    snap(tmp_path, 9, 20)
    compact_chains(tmp_path, AFTER_CLOSE)
    snap(tmp_path, 18, 0)
    frame = read_chains(tmp_path, "NIFTY", DAY, DAY)
    assert len(frame) == 4 and "segment" not in frame and set(frame["underlying"]) == {"NIFTY"}
    assert read_chains(tmp_path, "BANKNIFTY", DAY, DAY).empty


def test_contracts_are_point_in_time(tmp_path):
    write_contracts(tmp_path, date(2026, 1, 1), pd.DataFrame({"lot_size": [75]}))
    write_contracts(tmp_path, date(2026, 2, 1), pd.DataFrame({"lot_size": [65]}))
    assert read_contracts(tmp_path, date(2026, 1, 20))["lot_size"].iloc[0] == 75
    assert (tmp_path / "options/contracts/date=2026-02-01/part.parquet").exists()
    assert SnapshotStore(tmp_path).has_contracts(date(2026, 2, 1))


def test_old_layout_moves_into_options(tmp_path):
    old = tmp_path / "chains/underlying=NIFTY/date=2026-11-11"
    old.mkdir(parents=True)
    chain(DURING).to_parquet(old / "092000_2026-11-17.parquet", index=False)
    (tmp_path / "contracts").mkdir()
    pd.DataFrame({"lot_size": [65]}).to_parquet(tmp_path / "contracts/2026-11-11.parquet")
    assert migrate_old_layout(tmp_path) == 2
    assert (tmp_path / FOLDER / "snapshot-092000-2026-11-17.parquet").exists()
    assert read_contracts(tmp_path, DAY)["lot_size"].iloc[0] == 65
    assert not (tmp_path / "chains").exists() and not (tmp_path / "contracts").exists()
    assert migrate_old_layout(tmp_path) == 0


def test_upload_waits_until_the_day_is_compacted(tmp_path):
    config = ArchiveConfig(bucket="b", prefix="algoverse")
    snap(tmp_path, 9, 20)
    evening = datetime(2026, 11, 11, 17, 0, tzinfo=IST)
    assert not day_finished(tmp_path, DAY, evening, config)
    compact_chains(tmp_path, evening)
    assert day_finished(tmp_path, DAY, evening, config)
