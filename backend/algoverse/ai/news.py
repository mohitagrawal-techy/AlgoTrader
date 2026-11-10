"""Fetches public market headlines. All content here is untrusted."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from html import unescape
from xml.etree.ElementTree import ParseError

import httpx
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from algoverse.ai.schemas import AiConfig, Headline

log = logging.getLogger(__name__)
MAX_FEED_BYTES = 3_000_000
MAX_TITLE = 200
MAX_SUMMARY = 240
TAG = re.compile(r"<[^>]+>")


def _text(raw: str | None, limit: int) -> str:
    return " ".join(unescape(TAG.sub(" ", raw or "")).split())[:limit]


def _parse_date(raw: str | None) -> datetime | None:
    try:
        parsed = parsedate_to_datetime(raw) if raw else None
    except (TypeError, ValueError):
        return None
    return parsed if parsed is None or parsed.tzinfo else None


def parse_feed(
    content: bytes, now: datetime, max_age_hours: int
) -> list[tuple[datetime, Headline]]:
    """Return recent items. Undated, stale, future and non-web-link items are dropped."""
    try:
        root = ElementTree.fromstring(content)
    except (ParseError, DefusedXmlException):
        return []
    oldest = now - timedelta(hours=max_age_hours)
    items = []
    for item in root.iter("item"):
        title = _text(item.findtext("title"), MAX_TITLE)
        link = (item.findtext("link") or "").strip()
        published = _parse_date(item.findtext("pubDate"))
        if not title or not link.startswith(("https://", "http://")) or published is None:
            continue
        if oldest <= published <= now + timedelta(minutes=5):
            summary = _text(item.findtext("description"), MAX_SUMMARY)
            items.append((published, Headline(title, link, published.isoformat(), summary)))
    return items


def fetch_headlines(
    config: AiConfig, now: datetime, client: httpx.Client | None = None
) -> list[Headline]:
    """Return the newest unique headlines. A failing feed is skipped."""
    http = client or httpx.Client(
        timeout=10, follow_redirects=True, headers={"User-Agent": "AlgoVerse/0.1"}
    )
    found: list[tuple[datetime, Headline]] = []
    try:
        for url in config.news_feeds:
            try:
                response = http.get(url)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                log.warning("News feed failed: %s (%s)", url, type(exc).__name__)
                continue
            if len(response.content) <= MAX_FEED_BYTES:
                found += parse_feed(response.content, now, config.news_max_age_hours)
    finally:
        if client is None:
            http.close()
    found.sort(key=lambda pair: pair[0], reverse=True)
    unique: dict[str, Headline] = {}
    for _, headline in found:
        unique.setdefault(headline.title.lower(), headline)
    return list(unique.values())[: config.news_max_headlines]
