"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import signal
import threading
from collections import Counter
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import duckdb
import httpx
import pandas as pd

from algoverse.backtest.bhavcopy import import_files, load_lot_sizes
from algoverse.backtest.config import load_backtest_config
from algoverse.backtest.engine import ReplaySpec
from algoverse.backtest.fills import fit_spread_model
from algoverse.backtest.upstox_history import (
    CaptureRefused,
    capture_session,
    import_expired,
)
from algoverse.backtest.walkforward import run_backtest, run_holdout, run_walk_forward
from algoverse.brokers.base import AuthExpiredError, MarketDataError, PlanRequiredError
from algoverse.brokers.upstox import RateLimiter, UpstoxMarketData
from algoverse.brokers.upstox_feed import UpstoxFeed
from algoverse.data.collector import backfill_daily, collect_snapshots
from algoverse.data.holidays import fetch_holidays, load_holidays, refresh_holidays, save_holidays
from algoverse.data.instruments import download_master, parse_future_contracts
from algoverse.data.options import compact_chains
from algoverse.data.stream import (
    StreamService,
    build_subscription,
    load_stream_config,
    read_status,
)
from algoverse.data.ticks import stored_days
from algoverse.execution.cycle import run_cycle
from algoverse.execution.ledger import CLOSED, LIVE_STATUSES
from algoverse.runtime import Runtime, organise_storage
from algoverse.settings import get_settings

IST = ZoneInfo("Asia/Kolkata")
log = logging.getLogger("algoverse")
# Kept under the Upstox limits so a long import leaves room for the running collector.
IMPORT_LIMITS = ((20, 1.0), (300, 60.0), (1500, 1800.0))


def cmd_serve(_: argparse.Namespace) -> None:
    import uvicorn

    from algoverse.api.app import create_app

    organise_storage()
    uvicorn.run(create_app(), host=get_settings().host, port=8000)


def cmd_instruments(_: argparse.Namespace, out=print) -> None:
    count = Runtime().refresh_contracts(datetime.now(IST))
    out(f"Stored {count} option contracts.")


def cmd_collect_once(_: argparse.Namespace) -> None:
    rt = Runtime()
    now = datetime.now(IST)
    result = collect_snapshots(rt.market, rt.store, rt.universe, rt.contracts(now), now)
    print(f"Saved {result.saved} chains. Failures: {result.failures or 'none'}")


def cmd_backfill(args: argparse.Namespace, out=print) -> None:
    rt = Runtime()
    now = datetime.now(IST)
    result = backfill_daily(
        rt.market,
        rt.store,
        rt.universe,
        rt.contracts(now),
        now.date(),
        getattr(args, "start", None),
    )
    out(f"Updated {result.saved} series. Failures: {result.failures or 'none'}")


def _describe_legs(idea) -> str:
    sides = {1: "+", -1: "-"}
    return " ".join(
        f"{sides[leg.side]}{leg.ratio if leg.ratio > 1 else ''}{leg.strike:g}{leg.option_type}"
        for leg in idea.candidate.legs
    )


def _print_idea(idea) -> None:
    structure = idea.structure
    candidate = idea.candidate
    score = idea.card.composite
    label = "n/a" if score is None else f"{score:5.1f}"
    max_loss = f"{structure.max_loss:,.0f}" if structure.bounded else "undefined"
    print(
        f"{label}  {candidate.symbol:<10}{candidate.strategy:<17}{candidate.front_expiry} "
        f"{_describe_legs(idea):<36} debit {structure.entry_debit:>10,.0f}  "
        f"max loss {max_loss:>10}  POP {structure.pop:4.0%}  EV {structure.net_ev:>8,.0f}"
    )


def cmd_ideas(args: argparse.Namespace) -> None:
    rt = Runtime()
    now = datetime.now(IST)
    contracts = rt.contracts(now)
    collected = collect_snapshots(rt.market, rt.store, rt.universe, contracts, now)
    result = run_cycle(rt.deps, collected.chains, contracts, now, trade=False)
    tradable = [idea for idea in result.ideas if idea.tradable]
    rejected = [idea for idea in result.ideas if not idea.tradable]
    print(f"{len(tradable)} tradable, {len(rejected)} rejected. Analysis only, not advice.")
    for idea in tradable[: args.top]:
        _print_idea(idea)
    if rejected:
        print("\nMost common rejection reasons:")
        reasons = Counter(
            (idea.rejections or idea.risk.reasons)[0].split(":")[0].split(" (")[0]
            for idea in rejected
        )
        for reason, count in reasons.most_common(8):
            print(f"{count:>4}  {reason}")


def cmd_paper_status(_: argparse.Namespace) -> None:
    rt = Runtime()
    live = rt.ledger.positions(LIVE_STATUSES)
    closed = rt.ledger.positions((CLOSED,))
    realized = sum(p.realized_pnl for p in closed)
    print(f"Capital {rt.deps.risk.capital:,.0f}. Realized P&L {realized:,.2f}.")
    print(f"{len(live)} live positions, {len(closed)} closed.")
    for p in live:
        flag = " (stale quotes)" if p.stale else ""
        print(
            f"#{p.id} {p.status:<8}{p.symbol:<10}{p.strategy:<17}x{p.lots} "
            f"unrealized {p.unrealized_pnl:,.2f} target {p.target_pnl:,.0f} "
            f"stop {p.stop_pnl:,.0f}{flag}"
        )


def _print_report(report: dict) -> None:
    overall = report["summary"]["overall"]
    window = report.get("window") or {}
    print(f"Report {report['id']}: {report['verdict']}")
    print(f"Window {window.get('start')} to {window.get('end')}")
    for key in ("trades", "net_pnl", "win_rate", "expectancy", "sharpe", "max_drawdown"):
        print(f"  {key}: {overall.get(key)}")
    if report.get("double_slippage"):
        print(f"  net_pnl at 2x slippage: {report['double_slippage']['net_pnl']}")
    print(f"  trials logged to date: {report['trials_to_date']}")
    for warning in report["summary"]["warnings"]:
        print(f"Warning: {warning}")
    print("Open the Backtests tab in the dashboard for the full report.")


def cmd_history_import(args: argparse.Namespace) -> None:
    settings = get_settings()
    config = load_backtest_config(settings.config_dir)
    result = import_files(
        args.paths, settings.data_dir, config.symbols, load_lot_sizes(settings.config_dir)
    )
    if result.imported:
        first, last = result.imported[0], result.imported[-1]
        print(f"Imported {len(result.imported)} sessions, {first} to {last}.")
    for name, reason in result.failed.items():
        print(f"Skipped {name}: {reason}")


def cmd_history_files(args: argparse.Namespace) -> None:
    from algoverse.backtest.candle_files import import_candle_files
    from algoverse.data.store import SnapshotStore

    settings = get_settings()
    config = load_backtest_config(settings.config_dir)
    store = SnapshotStore(settings.data_dir)
    try:
        result = import_candle_files(
            args.folder,
            settings.data_dir,
            args.symbol,
            store.read_candles,
            config.expiries_per_underlying,
            config.strike_range,
            progress=print,
            dry_run=args.dry_run,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    verb = "Would write" if args.dry_run else "Wrote"
    print(f"Read {result.expiries} expiries, {result.contracts} contracts.")
    if result.written:
        first, last = result.written[0], result.written[-1]
        print(f"{verb} {len(result.written)} {args.symbol} sessions, {first} to {last}.")
    if result.kept_existing:
        print(f"Kept {len(result.kept_existing)} sessions already imported from NSE files.")
    if result.incomplete:
        print(
            f"Skipped {len(result.incomplete)} sessions without all "
            f"{config.expiries_per_underlying} nearest expiries in the files "
            f"({result.incomplete[0]} to {result.incomplete[-1]})."
        )
    for name, reason in result.problems.items():
        print(f"Skipped expiry {name}: {reason}")
    if args.intraday and not args.dry_run:
        from algoverse.backtest.candle_files import import_intraday

        print("Building 5-minute option bars for the intraday backtester.")
        intraday = import_intraday(args.folder, settings.data_dir, args.symbol, progress=print)
        print(
            f"Wrote {intraday.rows:,} bars from {intraday.expiries} expiries; "
            f"{intraday.sessions} sessions have intraday bars."
        )


def _backfill_minutes(rt: Runtime, start: date, progress=print, include_today: bool = False):
    from algoverse.data.minute_history import backfill_minutes

    config = load_backtest_config(rt.settings.config_dir)
    names = [*config.symbols, *rt.universe.reference_indices]
    keys = {name: rt.store.aliases[name] for name in names if name in rt.store.aliases}
    market = UpstoxMarketData(rt.tokens, limiter=RateLimiter(IMPORT_LIMITS))

    def sessions_between(first, last) -> int:
        days = (first + timedelta(days=i) for i in range((last - first).days + 1))
        return sum(1 for day in days if rt.calendar.is_trading_day(day))

    today = datetime.now(IST).date()
    # Minutes are fetched up to the day before `today`; after the close today is complete too.
    if include_today:
        today += timedelta(days=1)
    return keys, backfill_minutes(
        market, rt.store.candles, keys, start, today, sessions_between, progress=progress
    )


def cmd_backfill_minutes(args: argparse.Namespace, out=print) -> None:
    from algoverse.backtest.dataview import History

    rt = Runtime()
    if args.start:
        start = args.start
    else:
        sessions = History(rt.settings.data_dir).sessions
        first = sessions[0] if sessions else datetime.now(IST).date()
        # Indicators on minute candles need some history before the first tested session.
        start = first - timedelta(days=60)
    out(f"Fetching one-minute candles from {start}.")
    keys, result = _backfill_minutes(rt, start, progress=out)
    for name in keys:
        fetched, kept = result.fetched.get(name, 0), result.kept.get(name, 0)
        out(f"{name}: fetched {fetched} months, {kept} already complete.")
    for label, reason in result.failed.items():
        out(f"Failed {label}: {reason}")


def _capture(rt: Runtime, now: datetime):
    config = load_backtest_config(rt.settings.config_dir)
    futures = parse_future_contracts(download_master(), set(config.symbols))
    return capture_session(
        rt.market, rt.settings.data_dir, config, rt.contracts(now), futures, now.date()
    )


def cmd_history_capture(_: argparse.Namespace, out=print) -> None:
    try:
        path = _capture(Runtime(), datetime.now(IST))
    except CaptureRefused as exc:
        raise SystemExit(f"Nothing saved: {exc}") from exc
    out(f"Saved {path.name}.")


def cmd_history_upstox(_: argparse.Namespace, out=print) -> None:
    rt = Runtime()
    now = datetime.now(IST)
    config = load_backtest_config(rt.settings.config_dir)
    market = UpstoxMarketData(rt.tokens, limiter=RateLimiter(IMPORT_LIMITS))
    futures = parse_future_contracts(download_master(), set(config.symbols))
    try:
        result = import_expired(
            market, rt.settings.data_dir, config, rt.contracts(now), futures, now.date(), out
        )
    except PlanRequiredError as exc:
        raise SystemExit(
            "Upstox refused: expired contract data needs the Upstox Plus plan on this account. "
            "Nothing was saved. The collector still adds one session after each close."
        ) from exc
    out(f"Fetched {result.contracts} contracts. Wrote {len(result.written)} sessions.")
    if result.written:
        out(f"Sessions {result.written[0]} to {result.written[-1]}.")
    if result.kept_existing:
        out(f"Kept {len(result.kept_existing)} sessions already imported from NSE files.")
    for name, reason in result.failed.items():
        out(f"Incomplete {name}: {reason}. Run the command again to retry.")


def _fill_expired(rt: Runtime, market, config, now: datetime, plan_only: bool, out=print) -> bool:
    """Holes filled from expired contracts. False when the account has no Upstox Plus."""
    from algoverse.backtest.upstox_expired_gaps import fill_expired

    candles = {symbol: rt.store.read_candles(symbol) for symbol in config.symbols}
    try:
        result = fill_expired(
            market,
            rt.settings.data_dir,
            config,
            rt.contracts(now),
            candles,
            now.date(),
            out,
            dry_run=plan_only,
        )
    except PlanRequiredError:
        out("Upstox Plus is not active, so expired contracts cannot be fetched.")
        return False
    missing = ", ".join(f"{s} {n}" for s, n in result.needed.items())
    out(f"Expiries missing from the history: {missing or 'none'}.")
    if plan_only:
        return True
    if result.written:
        out(
            f"Wrote {len(result.written)} missing sessions, "
            f"{result.written[0]} to {result.written[-1]}."
        )
    if result.extended:
        out(f"Added contracts to {len(result.extended)} sessions that lacked them.")
    if result.intraday:
        out(f"Wrote 5-minute option bars for {len(result.intraday)} sessions.")
    if result.completed:
        out(f"{len(result.completed)} sessions are now complete and trade normally again.")
    for day, gaps in sorted(result.incomplete.items()):
        lost = "; ".join(f"{symbol} {', '.join(e)}" for symbol, e in gaps.items())
        out(f"{day}: still incomplete ({lost}). Backtests open no new trades there.")
    for name, reason in result.failed.items():
        out(f"Failed {name}: {reason}. Run again to retry.")
    out(f"{result.requests} requests to Upstox.")
    return True


def cmd_history_fill(args: argparse.Namespace, out=print) -> None:
    from algoverse.backtest.upstox_gaps import fill_gaps

    rt = Runtime()
    now = datetime.now(IST)
    config = load_backtest_config(rt.settings.config_dir)
    market = UpstoxMarketData(rt.tokens, limiter=RateLimiter(IMPORT_LIMITS))
    try:
        _fill_expired(rt, market, config, now, args.plan, out=out)
    except AuthExpiredError as exc:
        raise SystemExit(
            "Upstox session expired. Log in from the dashboard and run again."
        ) from exc
    if args.plan:
        return
    futures = parse_future_contracts(download_master(), set(config.symbols))
    try:
        result = fill_gaps(
            market, rt.settings.data_dir, config, rt.contracts(now), futures, now.date(), out
        )
    except AuthExpiredError as exc:
        raise SystemExit(
            "Upstox session expired. Log in from the dashboard and run again."
        ) from exc
    if not result.days and not result.intraday and not result.failed:
        out("No missing sessions in the last quarter.")
        return
    for name, reason in result.failed.items():
        out(f"Failed {name}: {reason}. Nothing was written; run again to retry.")
    if result.written:
        out(f"Wrote {len(result.written)} sessions, {result.written[0]} to {result.written[-1]}.")
    if result.kept_existing:
        out(f"Kept {len(result.kept_existing)} sessions already imported from other sources.")
    if result.intraday:
        out(f"Wrote 5-minute option bars for {len(result.intraday)} sessions.")
    for day, gaps in sorted(result.incomplete.items()):
        lost = "; ".join(f"{symbol} {', '.join(e)}" for symbol, e in gaps.items())
        out(f"{day}: expired contracts unavailable ({lost}). Backtests open no new trades there.")


def cmd_holidays(_: argparse.Namespace, out=print) -> None:
    settings = get_settings()
    now = datetime.now(IST)
    save_holidays(settings.data_dir, fetch_holidays())
    closed, sessions = load_holidays(settings.data_dir)
    out("Upcoming days F&O is closed:")
    for day in sorted(d for d in closed if d >= now.date()):
        out(f"  {day} {day:%a}")
    out("Upcoming listed sessions (these do trade):")
    for day, (start, end) in sorted(d for d in sessions.items() if d[0] >= now.date()):
        out(f"  {day} {day:%a} {start:%H:%M} to {end:%H:%M}")


def cmd_stream(_: argparse.Namespace) -> None:
    rt = Runtime()
    config = load_stream_config(rt.settings.config_dir)
    stop = threading.Event()
    for signal_number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signal_number, lambda *_: stop.set())
    if not config.enabled:
        log.info("The live feed is turned off in config/stream.yaml.")
        stop.wait()
        return

    def subscribe(now: datetime) -> dict[str, dict]:
        symbols = set(rt.universe.underlyings)
        futures = parse_future_contracts(download_master(), symbols)
        return build_subscription(
            rt.market, rt.universe, rt.contracts(now), futures, config, now.date()
        )

    before = timedelta(minutes=config.minutes_before_open)
    after = timedelta(minutes=config.minutes_after_close)

    def is_session(now: datetime) -> bool:
        return rt.calendar.within_session(now, before, after)

    log.info("Live feed service running. It connects around each market session.")
    StreamService(
        rt.settings.data_dir,
        config,
        UpstoxFeed(rt.tokens),
        subscribe,
        is_session,
        lambda now: rt.tokens.current(now) is not None,
        stop=stop,
    ).run()


def cmd_stream_status(_: argparse.Namespace) -> None:
    data_dir = get_settings().data_dir
    print(f"Feed status: {read_status(data_dir) or 'not started'}")
    for day in stored_days(data_dir):
        size = day["bytes"] / 1e6
        print(f"{day['date']}  {day['rows']:>12,} ticks  {size:>9,.1f} MB  {day['files']} files")


def cmd_history_s3(args: argparse.Namespace) -> None:
    # Imported here so other commands do not need the AWS library.
    from algoverse.backtest.s3_history import (
        S3AccessError,
        load_s3_config,
        make_client,
        pull_history,
        stored_sessions,
    )

    settings = get_settings()
    config = load_backtest_config(settings.config_dir)
    s3 = load_s3_config(settings.config_dir)
    try:
        result = pull_history(
            make_client(s3),
            s3,
            settings.data_dir,
            config.symbols,
            load_lot_sizes(settings.config_dir),
            args.dry_run,
            args.limit,
            print,
        )
    except S3AccessError as exc:
        raise SystemExit(str(exc)) from exc
    print(
        f"{result.listed} objects found: {result.pending} to import, "
        f"{result.unchanged} unchanged, {result.ignored} not data files."
    )
    if args.dry_run:
        return
    if result.sessions:
        first, last = result.sessions[0], result.sessions[-1]
        print(f"Imported {len(result.sessions)} sessions, {first} to {last}.")
    for key, reason in list(result.failed.items())[:20]:
        print(f"Skipped {key}: {reason}")
    if len(result.failed) > 20:
        print(f"...and {len(result.failed) - 20} more skipped.")
    print(f"{stored_sessions(settings.data_dir)} sessions are now stored.")


def cmd_backtest(args: argparse.Namespace) -> None:
    settings = get_settings()
    spec = ReplaySpec(args.start, args.end, args.min_score, tuple(args.disable))
    now = datetime.now(IST)
    _print_report(run_backtest(settings.data_dir, settings.config_dir, now, spec))


def cmd_walk_forward(_: argparse.Namespace) -> None:
    settings = get_settings()
    _print_report(run_walk_forward(settings.data_dir, settings.config_dir, datetime.now(IST)))


def cmd_holdout(args: argparse.Namespace) -> None:
    settings = get_settings()
    report = run_holdout(
        settings.data_dir,
        settings.config_dir,
        datetime.now(IST),
        args.min_score,
        tuple(args.disable),
        args.allow_reuse,
    )
    _print_report(report)


def cmd_calibrate_spreads(args: argparse.Namespace) -> None:
    rt = Runtime()
    today = datetime.now(IST).date()
    start = today - timedelta(days=args.days)
    contracts = rt.contracts(datetime.now(IST))
    frames = []
    for symbol in rt.universe.underlyings:
        chains = rt.store.read_chains(symbol, start, today)
        lots = contracts.loc[contracts["underlying_symbol"] == symbol, "lot_size"]
        if not chains.empty and not lots.empty:
            frames.append(chains.assign(volume=chains["volume"] / float(lots.iloc[0])))
    # Volume above is already in lots, so the model is fitted with a lot size of one.
    fitted = fit_spread_model(pd.concat(frames, ignore_index=True), 1.0) if frames else None
    if fitted is None:
        print("Not enough collected quotes yet. Leave the collector running and try again.")
        return
    print("Suggested values for the spread section of config/backtest.yaml:")
    for key, value in fitted.items():
        print(f"  {key}: {value:.4g}")


def cmd_forward_start(args: argparse.Namespace) -> None:
    run = Runtime().forward.start(
        args.name,
        datetime.now(IST),
        args.role,
        args.min_score,
        tuple(args.disable),
        args.report,
        args.holdout,
    )
    print(f"Started forward run #{run.id}: {run.name} v{run.version} as {run.role}.")
    print("Its code, weights, filters, limits and AI prompts are now locked.")


def cmd_forward_attach(args: argparse.Namespace) -> None:
    run = Runtime().forward.attach(args.run, args.report, args.holdout)
    print(f"Run #{run.id}: report {run.report_id}, holdout {run.holdout_id}.")


def cmd_forward_stop(args: argparse.Namespace) -> None:
    forward = Runtime().forward
    run = forward.store.run(args.run)
    if run is None or run.status != "active":
        print(f"Run #{args.run} is not active.")
        return
    forward.stop(run, datetime.now(IST), "Stopped by user")
    print(f"Stopped run #{run.id}. Open positions were closed at their last marks.")


def cmd_forward_status(_: argparse.Namespace) -> None:
    overview = Runtime().forward.overview(datetime.now(IST))
    if not overview["runs"]:
        print("No forward runs yet.")
    for run in overview["runs"]:
        met = sum(gate["passed"] for gate in run["gates"])
        print(
            f"#{run['id']} {run['name']} v{run['version']} {run['status']} ({run['role']}): "
            f"{run['stats'].get('trades', 0)} trades, "
            f"net {run['stats'].get('net_pnl', 0.0):,.2f}, gates {met}/{len(run['gates'])}"
        )


def _print_lab(lab) -> None:
    for row in lab.overview()["strategies"]:
        result = row["result"]
        if result:
            test = result["test"]
            print(
                f"{result['verdict']:<22}{row['spec']['name'][:34]:<36}"
                f"test trades {test['trades']:>4}  net {test['net_pnl']:>10,.0f}  "
                f"at 2x {result['test_2x']['net_pnl'] or 0:>10,.0f}"
            )


def cmd_lab_test(_: argparse.Namespace) -> None:
    lab = Runtime().lab
    lab.run_backtest(None, datetime.now(IST), print)
    _print_lab(lab)


def cmd_lab_ladder(args: argparse.Namespace) -> None:
    lab = Runtime().lab
    report = lab.run_ladder(args.start, datetime.now(IST), print)
    for node in report["lineage"]:
        print(f"Rung {node['rung']} ({node['level']}): {node['step'] or node['name']}")
    if report.get("final"):
        print(f"Final verdict: {report['final']['verdict']}")


def cmd_lab_generate(args: argparse.Namespace) -> None:
    lab = Runtime().lab
    outcome = lab.run_generate(datetime.now(IST), args.rounds, args.per_round, args.guidance, print)
    for dropped in outcome["dropped"]:
        print(f"Dropped: {dropped}")
    _print_lab(lab)


def cmd_collect(_: argparse.Namespace) -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler

    organise_storage()
    rt = Runtime()
    scheduler = BlockingScheduler(timezone=IST)

    def snapshot_job() -> None:
        now = datetime.now(IST)
        if not rt.calendar.is_open(now):
            return
        contracts = rt.contracts(now)
        try:
            result = collect_snapshots(rt.market, rt.store, rt.universe, contracts, now)
        except AuthExpiredError:
            log.error("Upstox session expired. Log in at http://127.0.0.1:8000/auth/login")
            return
        log.info("Saved %s chains. Failures: %s", result.saved, result.failures or "none")
        rt.deps.events = rt.live_events(now.date())
        cycle = run_cycle(rt.deps, result.chains, contracts, now)
        log.info(
            "Paper: %s opened, %s closed, %s exits triggered, %s queued, %s tradable ideas%s",
            len(cycle.report.opened),
            len(cycle.report.closed),
            len(cycle.report.exits_triggered),
            len(cycle.submitted),
            sum(idea.tradable for idea in cycle.ideas),
            ", HALTED for the day" if cycle.report.halted else "",
        )
        try:
            forward = rt.forward.on_cycle(result.chains, contracts, now)
        except Exception:  # Forward tests must never interrupt collection.
            log.exception("Forward testing failed this cycle")
        else:
            if forward:
                log.info("Forward: processed %s active runs", len(forward))

    def settlement_job() -> None:
        now = datetime.now(IST)
        rt.forward.after_close(now)
        report = rt.deps.broker.step(now, {})
        log.info("Settlement check: %s positions closed", len(report.closed))

    def backfill_job() -> None:
        now = datetime.now(IST)
        if not rt.calendar.is_trading_day(now.date()):
            return
        try:
            result = backfill_daily(rt.market, rt.store, rt.universe, rt.contracts(now), now.date())
        except AuthExpiredError:
            log.error("Upstox session expired. Daily backfill skipped.")
            return
        log.info("Backfilled %s series. Failures: %s", result.saved, result.failures or "none")

    def minutes_job() -> None:
        now = datetime.now(IST)
        if not rt.calendar.is_trading_day(now.date()):
            return
        try:
            # The current month is unfinished, so it is fetched again with today's session.
            _, result = _backfill_minutes(
                rt, now.date() - timedelta(days=7), log.info, include_today=True
            )
        except AuthExpiredError:
            log.error("Upstox session expired. Minute history not updated.")
            return
        if result.failed:
            log.warning("Minute history failures: %s", result.failed)

    def history_capture_job() -> None:
        now = datetime.now(IST)
        if not rt.calendar.is_trading_day(now.date()):
            return
        try:
            path = _capture(rt, now)
        except AuthExpiredError:
            log.error("Upstox session expired. Today's history session was not saved.")
        except (CaptureRefused, MarketDataError, httpx.HTTPError) as exc:
            log.error("History session not saved: %s", exc)
        else:
            log.info("Saved history session %s", path.name)

    scheduler.add_job(
        snapshot_job, "interval", minutes=rt.universe.snapshot_interval_minutes, max_instances=1
    )

    def holiday_job() -> None:
        try:
            if refresh_holidays(rt.settings.data_dir, datetime.now(IST)):
                log.info("Updated the market holiday list from Upstox")
        except (MarketDataError, httpx.HTTPError, OSError, ValueError, KeyError) as exc:
            log.warning("Could not update the holiday list: %s", exc)

    # Hourly so a failed download soon retries. It only downloads when the copy is 12 hours old.
    scheduler.add_job(holiday_job, "interval", hours=1, next_run_time=datetime.now(IST))
    # Upstox lists some sessions closing at 15:40, so these wait until after that.
    scheduler.add_job(settlement_job, "cron", hour=15, minute=45)
    # Upstox drops a contract the day after it expires, so this cannot wait for the next day.
    scheduler.add_job(history_capture_job, "cron", hour=15, minute=45)
    scheduler.add_job(backfill_job, "cron", hour=16, minute=0)
    scheduler.add_job(minutes_job, "cron", hour=16, minute=10)

    def archive_job() -> None:
        try:
            compacted = compact_chains(rt.settings.data_dir, datetime.now(IST))
            if compacted.days:
                log.info("Compacted %s option chain snapshots", compacted.snapshots)
        except (OSError, duckdb.Error) as exc:
            log.error("Option chain compaction failed: %s", exc)
        try:
            result = _run_archive()
        except Exception as exc:  # A failed upload must never stop the collector.
            log.error("S3 upload failed: %s", exc)
            return
        if result is not None:
            log.info("S3 upload: %s", _archive_summary(result))

    # After the feed merges the day's ticks; the evening run retries anything that failed.
    scheduler.add_job(archive_job, "cron", hour="16,20", minute=30, max_instances=1)
    scheduler.add_job(
        archive_job, "date", run_date=datetime.now(IST) + timedelta(minutes=2), max_instances=1
    )
    log.info("Collector running. Snapshots run only during market hours.")
    scheduler.start()


def _run_archive(dry_run: bool = False):
    # Imported here so other commands do not need the AWS library.
    from algoverse.data.archive import archive, load_archive_config, make_client

    settings = get_settings()
    config = load_archive_config(settings.config_dir)
    if not config.enabled and not dry_run:
        log.info("S3 upload is turned off in config/archive_s3.yaml.")
        return None
    return archive(make_client(config), config, settings.data_dir, datetime.now(IST), dry_run)


def _archive_summary(result, dry_run: bool = False) -> str:
    size = f"{result.uploaded} files ({result.uploaded_bytes / 1e6:,.1f} MB)"
    text = (
        f"would upload {size}, {result.unchanged} already in S3"
        if dry_run
        else f"{size} uploaded, {result.unchanged} already in S3, "
        f"{result.databases} databases copied"
    )
    if result.waiting_days:
        text += f"; waiting for {', '.join(sorted(result.waiting_days))} to finish"
    if result.pruned_days:
        text += f"; local ticks removed for {', '.join(result.pruned_days)}"
    if result.failures:
        text += f"; {len(result.failures)} failed, first: {next(iter(result.failures.items()))}"
    return text


def cmd_archive(args: argparse.Namespace) -> None:
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        result = _run_archive(args.dry_run)
    except (BotoCoreError, ClientError) as exc:
        raise SystemExit(f"S3 upload failed: {exc}") from exc
    if result is not None:
        print(_archive_summary(result, args.dry_run).capitalize())


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(prog="algoverse")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, handler, help_text in (
        ("serve", cmd_serve, "Run the local API and login flow"),
        ("instruments", cmd_instruments, "Download today's contract master"),
        ("collect-once", cmd_collect_once, "Save one round of chain snapshots"),
        ("collect", cmd_collect, "Run the snapshot and backfill schedule"),
        ("paper-status", cmd_paper_status, "Show paper positions and P&L"),
        ("holidays", cmd_holidays, "Download and show market holidays"),
        ("stream", cmd_stream, "Run the live feed and save every tick"),
        ("stream-status", cmd_stream_status, "Show the live feed and stored ticks"),
    ):
        commands.add_parser(name, help=help_text).set_defaults(handler=handler)
    backfill = commands.add_parser("backfill", help="Refresh daily price history")
    backfill.add_argument(
        "--start",
        type=date.fromisoformat,
        help="First day (YYYY-MM-DD). Upstox has index candles from 2000. Default: 10 years",
    )
    backfill.set_defaults(handler=cmd_backfill)
    archive_parser = commands.add_parser(
        "archive", help="Upload finished days of recorded data to S3"
    )
    archive_parser.add_argument(
        "--dry-run", action="store_true", help="List what would be uploaded, upload nothing"
    )
    archive_parser.set_defaults(handler=cmd_archive)
    fstart = commands.add_parser("forward-start", help="Lock a configuration and forward test it")
    fstart.add_argument("--name", required=True)
    fstart.add_argument("--role", choices=["champion", "challenger"], default="challenger")
    fstart.add_argument("--min-score", type=float)
    fstart.add_argument("--disable", nargs="*", default=[], help="Profiles not to trade")
    fstart.add_argument("--report", help="Walk-forward report with the expected results")
    fstart.add_argument("--holdout", help="Holdout report for the same configuration")
    fstart.set_defaults(handler=cmd_forward_start)
    fattach = commands.add_parser("forward-attach", help="Link backtest reports to a run")
    fattach.add_argument("--run", type=int, required=True)
    fattach.add_argument("--report")
    fattach.add_argument("--holdout")
    fattach.set_defaults(handler=cmd_forward_attach)
    fstop = commands.add_parser("forward-stop", help="End a forward run")
    fstop.add_argument("--run", type=int, required=True)
    fstop.set_defaults(handler=cmd_forward_stop)
    commands.add_parser("forward-status", help="Summarise forward runs").set_defaults(
        handler=cmd_forward_status
    )
    commands.add_parser("lab-test", help="Backtest every strategy in the lab").set_defaults(
        handler=cmd_lab_test
    )
    ladder = commands.add_parser("lab-ladder", help="Build a strategy from simple to complex")
    ladder.add_argument("--start", help="Id of a saved strategy to start from")
    ladder.set_defaults(handler=cmd_lab_ladder)
    generate = commands.add_parser("lab-generate", help="Let the AI design and test strategies")
    generate.add_argument("--rounds", type=int)
    generate.add_argument("--per-round", type=int)
    generate.add_argument("--guidance", default="")
    generate.set_defaults(handler=cmd_lab_generate)
    history = commands.add_parser("history-import", help="Import NSE files you downloaded")
    history.add_argument("paths", nargs="+", help="CSV or zip files, or folders of them")
    history.set_defaults(handler=cmd_history_import)
    minutes = commands.add_parser(
        "backfill-minutes", help="Fetch one-minute index history for intraday rules"
    )
    minutes.add_argument("--start", type=date.fromisoformat, help="First day (YYYY-MM-DD)")
    minutes.set_defaults(handler=cmd_backfill_minutes)
    files = commands.add_parser(
        "history-files", help="Import option minute candles saved as files (one underlying)"
    )
    files.add_argument(
        "folder", help="Folder with candles/1minute/<expiry>/ and expired_contracts/"
    )
    files.add_argument("--symbol", default="NIFTY", help="Underlying the files are for")
    files.add_argument("--dry-run", action="store_true", help="Report without writing")
    files.add_argument(
        "--intraday", action="store_true", help="Also build 5-minute bars for intraday tests"
    )
    files.set_defaults(handler=cmd_history_files)
    s3_pull = commands.add_parser("history-s3", help="Pull historical data from the S3 bucket")
    s3_pull.add_argument("--dry-run", action="store_true", help="List what would be imported")
    s3_pull.add_argument("--limit", type=int, help="Import at most this many files")
    s3_pull.set_defaults(handler=cmd_history_s3)
    commands.add_parser(
        "history-upstox", help="Import expired contracts from Upstox (needs Upstox Plus)"
    ).set_defaults(handler=cmd_history_upstox)
    commands.add_parser(
        "history-capture", help="Save today's session after the close"
    ).set_defaults(handler=cmd_history_capture)
    fill = commands.add_parser(
        "history-fill",
        help="Fill holes in the option history from Upstox (expired contracts need Plus)",
    )
    fill.add_argument(
        "--plan", action="store_true", help="Only count the missing expiries; fetch nothing"
    )
    fill.set_defaults(handler=cmd_history_fill)
    commands.add_parser(
        "walk-forward", help="Tune on past data and test on unseen windows"
    ).set_defaults(handler=cmd_walk_forward)
    single = commands.add_parser("backtest", help="Run one configuration over one window")
    single.add_argument("--start", type=date.fromisoformat, required=True)
    single.add_argument("--end", type=date.fromisoformat, required=True)
    single.add_argument("--min-score", type=float, default=60.0)
    single.add_argument("--disable", nargs="*", default=[], help="Profiles not to trade")
    single.set_defaults(handler=cmd_backtest)
    holdout = commands.add_parser("holdout", help="Spend the final holdout on one configuration")
    holdout.add_argument("--min-score", type=float, required=True)
    holdout.add_argument("--disable", nargs="*", default=[], help="Profiles not to trade")
    holdout.add_argument("--allow-reuse", action="store_true")
    holdout.set_defaults(handler=cmd_holdout)
    calibrate = commands.add_parser("calibrate-spreads", help="Fit the spread model to snapshots")
    calibrate.add_argument("--days", type=int, default=30)
    calibrate.set_defaults(handler=cmd_calibrate_spreads)
    ideas = commands.add_parser("ideas", help="Rank ideas now, without paper trading")
    ideas.add_argument("--top", type=int, default=20)
    ideas.set_defaults(handler=cmd_ideas)
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
