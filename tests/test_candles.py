from datetime import date
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from algoverse.data.candles import (
    CandleStore,
    Target,
    migrate_flat_layout,
    resolve_name,
)
from algoverse.data.store import SnapshotStore

IST = ZoneInfo("Asia/Kolkata")
NIFTY = "NSE_INDEX|Nifty 50"


def daily(start: str, days: int, close: float = 100.0) -> pd.DataFrame:
    ts = pd.date_range(start, periods=days, freq="D", tz=IST)
    return pd.DataFrame({"ts": ts, "open": close, "high": close, "low": close, "close": close})


def minutes(day: str, count: int) -> pd.DataFrame:
    ts = pd.date_range(f"{day} 09:15", periods=count, freq="min", tz=IST)
    return pd.DataFrame({"ts": ts, "close": range(count)})


def test_daily_candles_are_split_by_year_under_segment_and_symbol(tmp_path):
    store = CandleStore(tmp_path)
    paths = store.write(NIFTY, daily("2025-12-30", 4))
    base = "candles/interval=1d/segment=NSE_INDEX/symbol=Nifty_50"
    assert [p.relative_to(tmp_path).as_posix() for p in paths] == [
        f"{base}/year=2025/part.parquet",
        f"{base}/year=2026/part.parquet",
    ]
    assert len(store.read(NIFTY)) == 4


def test_minute_candles_are_split_by_month(tmp_path):
    store = CandleStore(tmp_path)
    store.write(NIFTY, minutes("2026-10-30", 3), "1m")
    store.write(NIFTY, minutes("2026-11-11", 5), "1m")
    folder = tmp_path / "candles/interval=1m/segment=NSE_INDEX/symbol=Nifty_50"
    assert sorted(p.name for p in folder.iterdir()) == ["month=2026-10", "month=2026-11"]
    one_day = store.read(NIFTY, "1m", start=date(2026, 11, 11), end=date(2026, 11, 11))
    assert len(one_day) == 5


def test_rewrites_replace_matching_times_and_keep_the_rest(tmp_path):
    store = CandleStore(tmp_path)
    store.write(NIFTY, daily("2026-11-01", 3, 100.0))
    store.write(NIFTY, daily("2026-11-03", 2, 200.0))
    assert list(store.read(NIFTY)["close"]) == [100.0, 100.0, 200.0, 200.0]


def test_stocks_and_commodities_get_their_own_segments(tmp_path):
    store = CandleStore(tmp_path)
    store.write("NSE_EQ|INE002A01018", daily("2026-11-01", 1))
    store.write("MCX_FO|432293", daily("2026-11-01", 1))
    segments = sorted(p.name for p in (tmp_path / "candles/interval=1d").iterdir())
    assert segments == ["segment=MCX_FO", "segment=NSE_EQ"]


def test_unknown_interval_is_refused(tmp_path):
    with pytest.raises(ValueError):
        CandleStore(tmp_path).write(NIFTY, daily("2026-11-01", 1), "7m")


def test_short_names_and_instrument_keys_share_one_folder(tmp_path):
    store = SnapshotStore(tmp_path)
    store.upsert_candles("NIFTY", daily("2026-11-01", 2))
    store.upsert_candles(NIFTY, daily("2026-11-03", 1))
    assert len(store.read_candles("NIFTY")) == 3
    assert len(list((tmp_path / "candles/interval=1d/segment=NSE_INDEX").iterdir())) == 1


def test_iv_names_become_a_derived_series(tmp_path):
    store = SnapshotStore(tmp_path)
    store.upsert_candles("iv_NIFTY", daily("2026-11-01", 2)[["ts", "close"]])
    assert (tmp_path / "series/name=atm_iv/symbol=NIFTY/year=2026/part.parquet").exists()
    assert len(store.read_candles("iv_NIFTY")) == 2


def test_config_aliases_are_used(tmp_path):
    store = SnapshotStore(tmp_path, {"RELIANCE": "NSE_EQ|INE002A01018"})
    store.upsert_candles("RELIANCE", daily("2026-11-01", 1))
    assert (tmp_path / "candles/interval=1d/segment=NSE_EQ/symbol=INE002A01018").is_dir()


@pytest.mark.parametrize(
    ("name", "target"),
    [
        ("NIFTY", Target(instrument_key=NIFTY)),
        ("index_Nifty_50", Target(instrument_key=NIFTY)),
        ("index_NIFTY_CONSR_DURBL", Target(instrument_key="NSE_INDEX|NIFTY CONSR DURBL")),
        ("iv_BANKNIFTY", Target(series="atm_iv", symbol="BANKNIFTY")),
        ("X", Target(instrument_key="CUSTOM|X")),
    ],
)
def test_old_names_resolve_to_the_new_layout(name, target):
    store = SnapshotStore(".")
    assert resolve_name(name, store.aliases) == target


def write_old(tmp_path, name: str, frame: pd.DataFrame, folder: str = "candles"):
    path = tmp_path / folder / f"{name}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)


def resolver(tmp_path):
    aliases = SnapshotStore(tmp_path).aliases
    return lambda name: resolve_name(name, aliases)


def test_migration_merges_duplicates_and_removes_old_files(tmp_path):
    write_old(tmp_path, "NIFTY", daily("2016-11-15", 3))
    write_old(tmp_path, "index_Nifty_50", daily("2026-11-01", 2))
    write_old(tmp_path, "iv_NIFTY", daily("2026-11-10", 2)[["ts", "close"]])
    write_old(tmp_path, "index_Nifty_50", minutes("2026-11-11", 4), "intraday/date=2026-11-11")
    result = migrate_flat_layout(tmp_path, resolver(tmp_path))
    assert result.moved == 4 and result.unknown == []
    store = SnapshotStore(tmp_path)
    assert len(store.read_candles("NIFTY")) == 5
    assert len(store.read_candles(NIFTY, "1m")) == 4
    assert len(store.read_candles("iv_NIFTY")) == 2
    assert not list((tmp_path / "candles").glob("*.parquet"))
    assert not (tmp_path / "intraday").exists()


def test_migration_keeps_newer_data_and_can_run_again(tmp_path):
    store = SnapshotStore(tmp_path)
    store.upsert_candles("NIFTY", daily("2026-11-01", 1, 200.0))
    write_old(tmp_path, "NIFTY", daily("2026-11-01", 1, 100.0))
    migrate_flat_layout(tmp_path, resolver(tmp_path))
    assert list(store.read_candles("NIFTY")["close"]) == [200.0]
    assert migrate_flat_layout(tmp_path, resolver(tmp_path)).moved == 0


def test_unrecognised_files_are_left_alone(tmp_path):
    write_old(tmp_path, "mystery", daily("2026-11-01", 1))
    result = migrate_flat_layout(tmp_path, lambda name: None)
    assert result.unknown == ["candles/mystery.parquet"]
    assert (tmp_path / "candles/mystery.parquet").exists()
