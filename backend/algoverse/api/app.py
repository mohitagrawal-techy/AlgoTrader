"""Local web API and dashboard. Binds to localhost only, see the `serve` CLI command."""

from __future__ import annotations

import logging
import secrets
import threading
from dataclasses import asdict
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from algoverse.backtest.dataview import History
from algoverse.backtest.jobs import BacktestBusy, BacktestError
from algoverse.backtest.registry import Registry
from algoverse.brokers import auth
from algoverse.brokers.base import AuthExpiredError, MarketDataError
from algoverse.dashboard import IDEAS_FILE, MARKET_FILE, clean, read_published
from algoverse.data.collector import collect_snapshots
from algoverse.data.stream import live_chain
from algoverse.execution.cycle import run_cycle
from algoverse.execution.ledger import CANCELLED, CLOSED, EXITING, LIVE_STATUSES, OPEN
from algoverse.lab.chart import ChartRequest
from algoverse.lab.service import LabBusy, LabError
from algoverse.market.assistant import AssistantError, UnknownTarget
from algoverse.market.calendar_events import (
    CalendarError,
    NewEvent,
    OwnerEvents,
    calendar_view,
)
from algoverse.market.index_details import save_constituents
from algoverse.market.sectors import SectorListError, parse_sector_csv, save_sector_list
from algoverse.market.strike import strike_history
from algoverse.market.underlying import underlying_view
from algoverse.research.service import ResearchError
from algoverse.risk.monitor import exposure_by_symbol, portfolio_stress
from algoverse.runtime import Runtime
from algoverse.settings import REPO_ROOT, Settings, get_settings

IST = ZoneInfo("Asia/Kolkata")
log = logging.getLogger(__name__)
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"
CLOSED_LIMIT = 100
CLIENT_HEADER_VALUE = "dashboard"


def _position_payload(position) -> dict:
    return asdict(position) | {"front_expiry": position.front_expiry}


def _limits(state, risk) -> list[dict]:
    return [
        {
            "key": "daily_loss",
            "label": "Loss today",
            "used": max(-state.day_pnl, 0.0),
            "limit": risk.amount(risk.max_daily_loss_pct),
            "unit": "money",
        },
        {
            "key": "open_risk",
            "label": "Open planned risk",
            "used": state.open_risk,
            "limit": risk.amount(risk.max_open_risk_pct),
            "unit": "money",
        },
        {
            "key": "margin",
            "label": "Estimated margin",
            "used": state.margin_used,
            "limit": risk.amount(risk.max_margin_use_pct),
            "unit": "money",
        },
        {
            "key": "positions",
            "label": "Open positions",
            "used": state.position_count,
            "limit": risk.max_open_positions,
            "unit": "count",
        },
        {
            "key": "delta",
            "label": "Net delta exposure",
            "used": abs(state.delta_notional),
            "limit": risk.capital * risk.max_delta_notional_multiple,
            "unit": "money",
        },
        {
            "key": "vega",
            "label": "Net vega, per volatility point",
            "used": abs(state.vega),
            "limit": risk.amount(risk.max_vega_pct),
            "unit": "money",
        },
        {
            "key": "gamma",
            "label": "Gamma P&L on a 1% move",
            "used": state.gamma_1pct,
            "limit": risk.amount(risk.max_gamma_1pct_pct),
            "unit": "money",
        },
    ]


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=40)


class SingleBacktestRequest(BaseModel):
    start: date
    end: date
    min_score: float = Field(default=0.0, ge=0, le=100)
    disabled_profiles: list[str] = Field(default_factory=list, max_length=20)


class LabBacktestRequest(BaseModel):
    ids: list[str] | None = Field(default=None, max_length=300)


class LabLadderRequest(BaseModel):
    start_id: str | None = None


class LabGenerateRequest(BaseModel):
    rounds: int | None = Field(default=None, ge=1, le=10)
    per_round: int | None = Field(default=None, ge=1, le=8)
    guidance: str = Field(default="", max_length=500)


class LabTuneRequest(BaseModel):
    start_id: str | None = None
    session_id: str | None = None
    node_id: str | None = None
    rounds: int | None = Field(default=None, ge=1, le=15)
    per_round: int | None = Field(default=None, ge=1, le=10)
    guidance: str = Field(default="", max_length=500)
    use_ai: bool = True


class DataJobRequest(BaseModel):
    action: str = Field(min_length=1, max_length=40)
    start: date | None = None


class ReviewedRequest(BaseModel):
    through: date | None = None


class ResearchMlRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    horizon: int = Field(default=1, ge=1, le=20)


class LabSearchRequest(BaseModel):
    start_id: str = Field(min_length=1, max_length=64)
    params: list[str] = Field(min_length=1, max_length=4)
    method: Literal["grid", "random", "bayesian"] = "bayesian"
    max_trials: int | None = Field(default=None, ge=4, le=400)


class LabReplayRequest(BaseModel):
    spec: dict
    symbol: str = Field(min_length=1, max_length=20)
    sessions: int = Field(default=21, ge=1, le=250)


class LabAutopilotRequest(BaseModel):
    cycles: int = Field(default=20, ge=1, le=200)
    hours: float = Field(default=8, gt=0, le=24)
    per_cycle: int = Field(default=4, ge=1, le=8)
    tune_rounds: int = Field(default=3, ge=1, le=15)
    guidance: str = Field(default="", max_length=500)


class LabTuneSaveRequest(BaseModel):
    node_id: str


class ExplainRequest(BaseModel):
    component: str = Field(max_length=40)
    title: str = Field(max_length=120)
    params: dict[str, str] = Field(default_factory=dict)


def _require_dashboard(header: str | None) -> None:
    # A custom header cannot be sent cross-origin without CORS, which is not enabled.
    if header != CLIENT_HEADER_VALUE:
        raise HTTPException(403, "Requests must come from the dashboard")


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or get_settings()
    runtime = Runtime(config)
    app = FastAPI(title="AlgoVerse")
    store = runtime.tokens
    pending_states: set[str] = set()
    refresh_lock = threading.Lock()

    @app.get("/api/health")
    def health() -> dict:
        now = datetime.now(IST)
        token = store.current()
        ideas = read_published(config.data_dir, IDEAS_FILE) or {}
        return {
            "upstox_configured": bool(config.upstox_api_key),
            "broker_session_valid": token is not None,
            "broker_session_expires_at": token.expires_at.isoformat() if token else None,
            "market_open": runtime.calendar.is_open(now),
            "data_as_of": ideas.get("generated_at"),
            "server_time": now.isoformat(),
        }

    @app.get("/api/ideas")
    def ideas() -> dict:
        return read_published(config.data_dir, IDEAS_FILE) or {"generated_at": None, "ideas": []}

    @app.get("/api/market")
    def market() -> dict:
        return read_published(config.data_dir, MARKET_FILE) or {"generated_at": None, "symbols": {}}

    @app.get("/api/market/live")
    def market_live(symbol: str, expiry: str) -> dict:
        return clean(live_chain(config.data_dir, symbol, expiry, datetime.now(IST)))

    @app.get("/api/market/strike")
    def market_strike(
        symbol: str, expiry: str, strike: float, option_type: str, day: str | None = None
    ) -> dict:
        try:
            return clean(strike_history(config.data_dir, symbol, expiry, strike, option_type, day))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/market/underlying")
    def market_underlying(symbol: str, day: str | None = None, expiry: str | None = None) -> dict:
        try:
            return clean(
                underlying_view(config.data_dir, symbol, runtime.store.read_candles, day, expiry)
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/portfolio")
    def portfolio() -> dict:
        now = datetime.now(IST)
        ledger = runtime.ledger
        risk = runtime.deps.risk
        state = runtime.deps.broker.portfolio_state(now)
        realized = ledger.realized_total()
        unrealized = sum(
            p.unrealized_pnl - p.entry_costs for p in ledger.positions((OPEN, EXITING))
        )
        closed = ledger.positions((CLOSED,))
        live = ledger.positions(LIVE_STATUSES)
        return clean(
            {
                "capital": risk.capital,
                "realized": realized,
                "unrealized": unrealized,
                "day_pnl": state.day_pnl,
                "halted": state.halted,
                "auto_trade": risk.paper.auto_trade,
                "min_score": risk.paper.min_score,
                "limits": _limits(state, risk),
                "live": [_position_payload(p) for p in live],
                "by_symbol": exposure_by_symbol(live, risk),
                "stress": portfolio_stress(live, risk, runtime.deps.scoring.risk_free_rate, now),
                "closed": [_position_payload(p) for p in closed[-CLOSED_LIMIT:][::-1]],
                "closed_total": len(closed),
                "wins": sum((p.realized_pnl or 0.0) > 0 for p in closed),
                "unfilled_total": len(ledger.positions((CANCELLED,))),
                "equity": [{"ts": ts, "pnl": pnl} for ts, pnl in ledger.equity_series()],
                "activity": [
                    {"ts": ts, "position_id": pid, "kind": kind, "detail": detail}
                    for ts, pid, kind, detail in ledger.activity()
                ],
            }
        )

    @app.get("/api/backtests")
    def backtests() -> dict:
        sessions = History(config.data_dir).sessions
        return clean(
            {
                "history": {
                    "sessions": len(sessions),
                    "start": sessions[0] if sessions else None,
                    "end": sessions[-1] if sessions else None,
                },
                "reports": Registry(config.data_dir).list_reports(),
                "options": runtime.backtest_jobs.options(),
                "job": runtime.backtest_jobs.job,
            }
        )

    def _backtest_job(action):
        try:
            return clean(action())
        except BacktestBusy as exc:
            raise HTTPException(409, str(exc)) from exc
        except BacktestError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/backtests/walk-forward")
    def backtest_walk_forward(x_algoverse_client: str | None = Header(default=None)) -> dict:
        _require_dashboard(x_algoverse_client)
        return _backtest_job(runtime.backtest_jobs.start_walk_forward)

    @app.post("/api/backtests/run")
    def backtest_single(
        body: SingleBacktestRequest, x_algoverse_client: str | None = Header(default=None)
    ) -> dict:
        _require_dashboard(x_algoverse_client)
        return _backtest_job(
            lambda: runtime.backtest_jobs.start_single(
                body.start, body.end, body.min_score, body.disabled_profiles
            )
        )

    @app.get("/api/backtests/{report_id}")
    def backtest_report(report_id: str) -> dict:
        report = Registry(config.data_dir).read_report(report_id)
        if report is None:
            raise HTTPException(404, "Report not found")
        return report

    @app.get("/api/overview")
    def overview() -> dict:
        return runtime.overview.snapshot(datetime.now(IST))

    @app.get("/api/overview/history")
    def overview_history(name: str) -> dict:
        result = runtime.overview.history(name, datetime.now(IST))
        if result is None:
            raise HTTPException(404, "Unknown index")
        return result

    @app.get("/api/overview/intraday")
    def overview_intraday(name: str) -> dict:
        result = runtime.overview.intraday(name, datetime.now(IST))
        if result is None:
            raise HTTPException(404, "Unknown index")
        return result

    @app.get("/api/overview/intraday-comparison")
    def overview_intraday_comparison() -> dict:
        return runtime.overview.intraday_comparison(datetime.now(IST))

    @app.get("/api/overview/comparison")
    def overview_comparison() -> dict:
        return runtime.overview.comparison(datetime.now(IST))

    @app.get("/api/overview/institutions")
    def overview_institutions() -> dict:
        return runtime.overview.institutions(datetime.now(IST))

    @app.get("/api/overview/details")
    def overview_details(name: str) -> dict:
        result = runtime.overview.details(name, datetime.now(IST))
        if result is None:
            raise HTTPException(404, "Unknown index")
        return result

    @app.get("/api/overview/constituents")
    def overview_constituents() -> dict:
        return {"indices": runtime.overview.constituent_lists()}

    @app.post("/api/overview/constituents")
    async def upload_constituents(
        name: str, request: Request, x_algoverse_client: str | None = Header(default=None)
    ) -> dict:
        if x_algoverse_client != CLIENT_HEADER_VALUE:
            raise HTTPException(403, "Uploads must come from the dashboard")
        if not runtime.overview.accepts_constituents(name):
            raise HTTPException(404, "Unknown index")
        try:
            stocks = parse_sector_csv(await request.body())
        except SectorListError as exc:
            raise HTTPException(400, str(exc)) from exc
        save_constituents(config.data_dir, name, stocks)
        runtime.overview.forget_member_quotes(name)
        return {"stocks": len(stocks)}

    @app.post("/api/overview/sectors")
    async def upload_sectors(
        request: Request, x_algoverse_client: str | None = Header(default=None)
    ) -> dict:
        if x_algoverse_client != CLIENT_HEADER_VALUE:
            raise HTTPException(403, "Uploads must come from the dashboard")
        body = await request.body()
        try:
            stocks = parse_sector_csv(body)
        except SectorListError as exc:
            raise HTTPException(400, str(exc)) from exc
        save_sector_list(config.data_dir, stocks)
        runtime.overview.invalidate()
        return {"stocks": len(stocks)}

    def _assistant_response(open_stream) -> StreamingResponse:
        try:
            stream = open_stream()
        except UnknownTarget as exc:
            raise HTTPException(400, str(exc)) from exc
        except AssistantError as exc:
            raise HTTPException(503, str(exc)) from exc
        return StreamingResponse(
            stream,
            media_type="text/plain; charset=utf-8",
            headers={
                "X-AlgoVerse-Model": runtime.assistant.model,
                "Cache-Control": "no-store",
            },
        )

    @app.post("/api/assistant/explain")
    def assistant_explain(
        body: ExplainRequest, x_algoverse_client: str | None = Header(default=None)
    ) -> StreamingResponse:
        _require_dashboard(x_algoverse_client)
        now = datetime.now(IST)
        return _assistant_response(
            lambda: runtime.assistant.explain(body.component, body.title, body.params, now)
        )

    @app.post("/api/assistant/chat")
    def assistant_chat(
        body: ChatRequest, x_algoverse_client: str | None = Header(default=None)
    ) -> StreamingResponse:
        _require_dashboard(x_algoverse_client)
        if body.messages[-1].role != "user":
            raise HTTPException(400, "The last message must be from the user.")
        history = [message.model_dump() for message in body.messages]
        now = datetime.now(IST)
        return _assistant_response(lambda: runtime.assistant.chat(history, now))

    def _lab(action):
        try:
            return action()
        except LabBusy as exc:
            raise HTTPException(409, str(exc)) from exc
        except LabError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/lab")
    def lab_overview() -> dict:
        return runtime.lab.overview()

    @app.get("/api/lab/strategies/{spec_id}/backtest")
    def lab_backtest_detail(spec_id: str) -> dict:
        return _lab(lambda: runtime.lab.backtest_detail(spec_id))

    @app.get("/api/lab/history")
    def lab_history(
        kind: str | None = None, q: str = "", limit: int = 100, offset: int = 0
    ) -> dict:
        return runtime.lab.test_history(kind, q, max(1, min(limit, 500)), max(0, offset))

    @app.get("/api/lab/history/{entry_id}")
    def lab_history_entry(entry_id: str) -> dict:
        return _lab(lambda: runtime.lab.test_history_entry(entry_id))

    @app.get("/api/lab/strategies/{spec_id}/replay")
    def lab_backtest_replay(spec_id: str, window: str = "test", symbol: str = "NIFTY") -> dict:
        """A saved backtest's trades with each position's value over time. Read-only."""
        return _lab(lambda: runtime.lab.backtest_replay(spec_id, window, symbol))

    @app.post("/api/lab/strategies")
    def lab_save(body: dict, x_algoverse_client: str | None = Header(default=None)) -> dict:
        _require_dashboard(x_algoverse_client)
        return _lab(lambda: runtime.lab.save(body, datetime.now(IST)).model_dump(mode="json"))

    @app.delete("/api/lab/strategies/{spec_id}")
    def lab_delete(spec_id: str, x_algoverse_client: str | None = Header(default=None)) -> dict:
        _require_dashboard(x_algoverse_client)
        _lab(lambda: runtime.lab.delete(spec_id))
        return {"deleted": spec_id}

    @app.post("/api/lab/backtest")
    def lab_backtest(
        body: LabBacktestRequest, x_algoverse_client: str | None = Header(default=None)
    ) -> dict:
        _require_dashboard(x_algoverse_client)
        return _lab(lambda: runtime.lab.start_backtest(body.ids, datetime.now(IST)))

    @app.post("/api/lab/ladder")
    def lab_ladder(
        body: LabLadderRequest, x_algoverse_client: str | None = Header(default=None)
    ) -> dict:
        _require_dashboard(x_algoverse_client)
        return _lab(lambda: runtime.lab.start_ladder(body.start_id, datetime.now(IST)))

    @app.post("/api/lab/generate")
    def lab_generate(
        body: LabGenerateRequest, x_algoverse_client: str | None = Header(default=None)
    ) -> dict:
        _require_dashboard(x_algoverse_client)
        return _lab(
            lambda: runtime.lab.start_generate(
                datetime.now(IST), body.rounds, body.per_round, body.guidance
            )
        )

    @app.post("/api/lab/chart")
    def lab_chart(body: ChartRequest) -> dict:
        """Read-only: candles, indicator lines and where entry rules held."""
        return _lab(lambda: runtime.lab.chart(body))

    @app.post("/api/lab/replay")
    def lab_replay(
        body: LabReplayRequest, x_algoverse_client: str | None = Header(default=None)
    ) -> dict:
        """Backtest a draft strategy for the chart's replay. Saves nothing."""
        _require_dashboard(x_algoverse_client)
        return _lab(lambda: runtime.lab.replay(body.spec, body.symbol, body.sessions))

    @app.post("/api/lab/autopilot")
    def lab_autopilot(
        body: LabAutopilotRequest, x_algoverse_client: str | None = Header(default=None)
    ) -> dict:
        _require_dashboard(x_algoverse_client)
        return _lab(
            lambda: runtime.lab.start_autopilot(
                datetime.now(IST),
                body.cycles,
                body.hours,
                body.per_cycle,
                body.tune_rounds,
                body.guidance,
            )
        )

    @app.post("/api/lab/autopilot/stop")
    def lab_autopilot_stop(x_algoverse_client: str | None = Header(default=None)) -> dict:
        _require_dashboard(x_algoverse_client)
        return _lab(runtime.lab.stop_autopilot)

    @app.get("/api/lab/tune")
    def lab_tuning() -> dict:
        return runtime.lab.tuning_overview()

    @app.get("/api/lab/tune/{session_id}")
    def lab_tuning_session(session_id: str) -> dict:
        return _lab(lambda: runtime.lab.tuning_session(session_id))

    @app.post("/api/lab/tune")
    def lab_tune(
        body: LabTuneRequest, x_algoverse_client: str | None = Header(default=None)
    ) -> dict:
        _require_dashboard(x_algoverse_client)
        return _lab(
            lambda: runtime.lab.start_tuning(
                body.start_id,
                datetime.now(IST),
                body.session_id,
                body.node_id,
                body.rounds,
                body.per_round,
                body.guidance,
                body.use_ai,
            )
        )

    @app.post("/api/lab/tune/{session_id}/save")
    def lab_tune_save(
        session_id: str,
        body: LabTuneSaveRequest,
        x_algoverse_client: str | None = Header(default=None),
    ) -> dict:
        _require_dashboard(x_algoverse_client)
        saved = _lab(lambda: runtime.lab.save_tuned(session_id, body.node_id, datetime.now(IST)))
        return {"id": saved}

    @app.get("/api/data")
    def data_inventory() -> dict:
        """What is stored, how far back it goes, and the loaders that can add to it."""
        return {
            "inventory": runtime.data_jobs.inventory(),
            "job": runtime.data_jobs.job,
            "session_valid": store.current() is not None,
        }

    @app.get("/api/data/job")
    def data_job() -> dict:
        return {"job": runtime.data_jobs.job}

    @app.post("/api/data/jobs")
    def data_start(
        body: DataJobRequest, x_algoverse_client: str | None = Header(default=None)
    ) -> dict:
        _require_dashboard(x_algoverse_client)
        try:
            return runtime.data_jobs.start(body.action, body.start, store.current() is not None)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    def _calendar(action):
        try:
            return action()
        except CalendarError as exc:
            raise HTTPException(400, str(exc)) from exc

    def _calendar_symbols() -> list[str]:
        return list(runtime.universe.underlyings)

    @app.get("/api/calendar")
    def calendar(start: date | None = None, end: date | None = None) -> dict:
        """Owner, configured and AI events, holidays and expiries between two dates."""
        today = datetime.now(IST).date()
        first = start or today.replace(day=1)
        last = end or first + timedelta(days=62)
        return clean(
            _calendar(
                lambda: calendar_view(
                    config.config_dir, config.data_dir, _calendar_symbols(), first, last, today
                )
            )
        )

    @app.post("/api/calendar/events")
    def calendar_add(body: NewEvent, x_algoverse_client: str | None = Header(default=None)) -> dict:
        _require_dashboard(x_algoverse_client)
        added = _calendar(lambda: OwnerEvents(config.data_dir).add(body, _calendar_symbols()))
        return added.model_dump(mode="json")

    @app.delete("/api/calendar/events/{event_id}")
    def calendar_delete(
        event_id: str, x_algoverse_client: str | None = Header(default=None)
    ) -> dict:
        _require_dashboard(x_algoverse_client)
        _calendar(lambda: OwnerEvents(config.data_dir).delete(event_id))
        return {"deleted": event_id}

    @app.put("/api/calendar/reviewed")
    def calendar_reviewed(
        body: ReviewedRequest, x_algoverse_client: str | None = Header(default=None)
    ) -> dict:
        """The owner confirms the calendar is complete through a date. Short premium may then
        be held up to it, outside high-severity events."""
        _require_dashboard(x_algoverse_client)
        OwnerEvents(config.data_dir).set_reviewed(body.through)
        return {"reviewed_through": body.through}

    def _research(action):
        try:
            return action()
        except ResearchError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/research")
    def research(symbol: str = "NIFTY") -> dict:
        """Read-only market studies from stored candles."""
        return _research(lambda: runtime.research.studies(symbol))

    @app.get("/api/research/job")
    def research_job() -> dict:
        return {"job": runtime.research.job}

    @app.post("/api/research/ml")
    def research_ml(
        body: ResearchMlRequest, x_algoverse_client: str | None = Header(default=None)
    ) -> dict:
        _require_dashboard(x_algoverse_client)
        return _research(
            lambda: runtime.research.start_ml(body.symbol, body.horizon, datetime.now(IST))
        )

    @app.get("/api/lab/job")
    def lab_job() -> dict:
        return {"job": runtime.lab.job()}

    @app.get("/api/lab/strategies/{spec_id}/search")
    def lab_search_space(spec_id: str) -> dict:
        return _lab(lambda: runtime.lab.search_space(spec_id))

    @app.get("/api/lab/search/{search_id}")
    def lab_search(search_id: str) -> dict:
        return _lab(lambda: runtime.lab.search(search_id))

    @app.post("/api/lab/search")
    def lab_start_search(
        body: LabSearchRequest, x_algoverse_client: str | None = Header(default=None)
    ) -> dict:
        _require_dashboard(x_algoverse_client)
        return _lab(
            lambda: runtime.lab.start_search(
                body.start_id, body.params, body.method, body.max_trials, datetime.now(IST)
            )
        )

    @app.get("/api/lab/strategies/{spec_id}/dossier")
    def lab_dossier(spec_id: str) -> dict:
        return _lab(lambda: runtime.lab.dossier(spec_id))

    @app.get("/api/lab/strategies/{spec_id}/dossier.md", response_class=PlainTextResponse)
    def lab_dossier_markdown(spec_id: str) -> PlainTextResponse:
        name, text = _lab(lambda: runtime.lab.dossier_markdown(spec_id))
        return PlainTextResponse(
            text,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @app.post("/api/lab/strategies/{spec_id}/dossier")
    def lab_dossier_notes(
        spec_id: str, x_algoverse_client: str | None = Header(default=None)
    ) -> dict:
        _require_dashboard(x_algoverse_client)
        return _lab(lambda: runtime.lab.start_dossier_notes(spec_id, datetime.now(IST)))

    @app.get("/api/forward")
    def forward() -> dict:
        return runtime.forward.overview(datetime.now(IST))

    @app.post("/api/refresh")
    def refresh(x_algoverse_client: str | None = Header(default=None)) -> dict:
        """Rank ideas from fresh chains. This never places or fills a paper trade."""
        # A custom header cannot be sent cross-origin without CORS, which is not enabled.
        if x_algoverse_client != CLIENT_HEADER_VALUE:
            raise HTTPException(403, "Refresh must come from the dashboard")
        if not refresh_lock.acquire(blocking=False):
            raise HTTPException(409, "A refresh is already running")
        try:
            now = datetime.now(IST)
            contracts = runtime.contracts(now)
            collected = collect_snapshots(
                runtime.market, runtime.store, runtime.universe, contracts, now
            )
            runtime.deps.events = runtime.live_events(now.date())
            result = run_cycle(runtime.deps, collected.chains, contracts, now, trade=False)
        except AuthExpiredError as exc:
            raise HTTPException(401, "Upstox session expired. Log in again.") from exc
        except (MarketDataError, httpx.HTTPError) as exc:
            log.warning("Refresh failed: %s", exc)
            raise HTTPException(502, "Could not fetch market data from Upstox.") from exc
        finally:
            refresh_lock.release()
        return {
            "saved": collected.saved,
            "failures": collected.failures,
            "ideas": len(result.ideas),
        }

    @app.get("/auth/login")
    def login() -> RedirectResponse:
        if not config.upstox_api_key:
            raise HTTPException(500, "ALGOVERSE_UPSTOX_API_KEY is not configured")
        state = secrets.token_urlsafe(24)
        pending_states.add(state)
        return RedirectResponse(auth.authorize_url(config, state))

    @app.get("/auth/callback", response_class=HTMLResponse)
    def callback(code: str, state: str) -> str:
        if state not in pending_states:
            raise HTTPException(400, "Unknown login state. Start again at /auth/login.")
        pending_states.discard(state)
        try:
            store.save(auth.exchange_code(config, code))
        except MarketDataError as exc:
            raise HTTPException(502, str(exc)) from exc
        return (
            '<meta http-equiv="refresh" content="0; url=/">'
            '<p>Upstox login complete. <a href="/">Open the dashboard</a>.</p>'
        )

    if FRONTEND_DIST.exists():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="dashboard")
    return app
