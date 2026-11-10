"""Upstox OAuth login and local token storage."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx

from algoverse.brokers.base import MarketDataError
from algoverse.settings import Settings

IST = ZoneInfo("Asia/Kolkata")
AUTHORIZE_URL = "https://api.upstox.com/v2/login/authorization/dialog"
TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"
TOKEN_RESET = time(3, 30)


def token_expiry(issued_at: datetime) -> datetime:
    """Return the first 03:30 IST after issue, the earliest reading of Upstox's stated validity."""
    local = issued_at.astimezone(IST)
    expiry = datetime.combine(local.date(), TOKEN_RESET, tzinfo=IST)
    return expiry if local < expiry else expiry + timedelta(days=1)


@dataclass(frozen=True)
class StoredToken:
    access_token: str
    expires_at: datetime

    def is_valid(self, now: datetime) -> bool:
        return now < self.expires_at


class TokenStore:
    """Keeps the access token in a file only the current user can read."""

    def __init__(self, path: Path):
        self.path = path

    def save(self, token: StoredToken) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"access_token": token.access_token, "expires_at": token.expires_at.isoformat()}
        )
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(payload)
        os.replace(tmp, self.path)

    def load(self) -> StoredToken | None:
        try:
            raw = json.loads(self.path.read_text())
        except FileNotFoundError:
            return None
        return StoredToken(raw["access_token"], datetime.fromisoformat(raw["expires_at"]))

    def current(self, now: datetime | None = None) -> StoredToken | None:
        """Return the stored token only if it is still valid."""
        token = self.load()
        if token is None or not token.is_valid(now or datetime.now(IST)):
            return None
        return token


def token_store(settings: Settings) -> TokenStore:
    return TokenStore(settings.data_dir / "upstox_token.json")


def authorize_url(settings: Settings, state: str) -> str:
    query = urlencode(
        {
            "client_id": settings.upstox_api_key,
            "redirect_uri": settings.upstox_redirect_uri,
            "response_type": "code",
            "state": state,
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def exchange_code(settings: Settings, code: str, client: httpx.Client | None = None) -> StoredToken:
    """Swap a single-use authorization code for an access token."""
    http = client or httpx.Client(timeout=15)
    try:
        response = http.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.upstox_api_key,
                "client_secret": settings.upstox_api_secret.get_secret_value(),
                "redirect_uri": settings.upstox_redirect_uri,
                "grant_type": "authorization_code",
            },
            headers={"Accept": "application/json"},
        )
    finally:
        if client is None:
            http.close()
    if response.status_code != 200:
        raise MarketDataError(f"Upstox token exchange failed with HTTP {response.status_code}")
    access_token = response.json().get("access_token")
    if not access_token:
        raise MarketDataError("Upstox token response did not include an access token")
    now = datetime.now(IST)
    return StoredToken(access_token=access_token, expires_at=token_expiry(now))
