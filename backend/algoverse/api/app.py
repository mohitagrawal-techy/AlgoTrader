"""Local web API and dashboard. Binds to localhost only, see the `serve` CLI command."""

from __future__ import annotations

import logging
import secrets
import threading
from dataclasses import asdict
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from algoverse.backtest.dataview import History
from algoverse.backtest.registry import Registry
from algoverse.brokers import auth
from algoverse.brokers.base import AuthExpiredError, MarketDataError
from algoverse.dashboard import IDEAS_FILE, MARKET_FILE, clean, read_published
from algoverse.data.collector import collect_snapshots
from algoverse.data.stream import live_chain
from algoverse.execution.cycle import run_cycle
from algoverse.execution.ledger import CANCELLED, CLOSED, EXITING, LIVE_STATUSES, OPEN
from algoverse.lab.service import LabBusy, LabError
from algoverse.market.assistant import AssistantError, UnknownTarget
from algoverse.market.sectors import SectorListError, parse_sector_csv, save_sector_list
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


class LabBacktestRequest(BaseModel):
    ids: list[str] | None = Field(default=None, max_length=300)


class LabGenerateRequest(BaseModel):
    rounds: int | None = Field(default=None, ge=1, le=10)
    per_round: int | None = Field(default=None, ge=1, le=8)
    guidance: str = Field(default="", max_length=500)


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
                "live": [_position_payload(p) for p in ledger.positions(LIVE_STATUSES)],
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
            }
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

    @app.get("/api/overview/comparison")
    def overview_comparison() -> dict:
        return runtime.overview.comparison(datetime.now(IST))

    @app.get("/api/overview/institutions")
    def overview_institutions() -> dict:
        return runtime.overview.institutions(datetime.now(IST))

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

    @app.post("/api/lab/strategies")
    def lab_save(body: dict, x_algoverse_client: str | None = Header(default=None)) -> dict:
        _require_dashboard(x_algoverse_client)
        return _lab(lambda: runtime.lab.save(body, datetime.now(IST)).model_dump())

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
