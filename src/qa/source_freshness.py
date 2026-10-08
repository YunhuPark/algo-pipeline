"""Keep stale articles out of a news account.

The collector already meant to take only the last 24 hours (HOURS_LIMIT), but the
rule was skipped for any article without a parsed date and never ran for
Tavily results — so a vendor report from July 2025 reached the queue in
October 2026 and read like fresh news.

This module makes the rule real: every article needs a known publication date,
read from the feed, or from the article page's own metadata when the feed has
none, and it must be recent. An unknown date fails closed at collection time.

Dates are compared by calendar day. Pages usually expose only a date
("2026-10-07"), and a UTC date can sit a day away from the Korean one, so the
default window is two days rather than 24 hours.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable

DEFAULT_MAX_AGE_DAYS = 2
_ENV_NAME = "SOURCE_MAX_AGE_DAYS"

_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(r"([\w:.-]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')")
_TIME_TAG_RE = re.compile(r"<time\b[^>]*\bdatetime\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)
_JSONLD_RE = re.compile(
    r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL
)
_META_NAMES = (
    "article:published_time",
    "og:article:published_time",
    "og:published_time",
    "datepublished",
    "publishdate",
    "pubdate",
    "date",
    "dc.date.issued",
    "dc.date",
    "parsely-pub-date",
    "sailthru.date",
)


def max_age_days() -> int:
    """Allowed article age in calendar days (env SOURCE_MAX_AGE_DAYS, default 2)."""

    try:
        value = int(os.getenv(_ENV_NAME, "").strip())
    except ValueError:
        return DEFAULT_MAX_AGE_DAYS
    return value if value >= 0 else DEFAULT_MAX_AGE_DAYS


def _to_date(text: str) -> date | None:
    match = _DATE_RE.search(text or "")
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def parse_date(text: str) -> date | None:
    """First YYYY-MM-DD in `text` (ISO dates and datetimes), else None."""

    return _to_date(text)


def _jsonld_dates(blob: str) -> list[date]:
    found: list[date] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            for key in ("datePublished", "dateCreated"):
                value = node.get(key)
                if isinstance(value, str) and (parsed := _to_date(value)):
                    found.append(parsed)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    try:
        walk(json.loads(blob))
    except ValueError:
        # Malformed JSON-LD is common; fall back to a plain pattern match.
        for match in re.finditer(r'"datePublished"\s*:\s*"([^"]+)"', blob):
            if parsed := _to_date(match.group(1)):
                found.append(parsed)
    return found


def extract_published_date(html: str) -> date | None:
    """Best-effort publication date from a page's own metadata.

    Order: JSON-LD datePublished, then <meta> tags, then the first <time>.
    Modification dates are deliberately ignored — a page can be re-served
    yesterday and still describe a year-old report.
    """

    for blob in _JSONLD_RE.findall(html or ""):
        dates = _jsonld_dates(blob)
        if dates:
            return min(dates)

    for tag in _META_TAG_RE.findall(html or ""):
        attrs = {
            m.group(1).lower(): (m.group(2) if m.group(2) is not None else m.group(3))
            for m in _ATTR_RE.finditer(tag)
        }
        name = (attrs.get("property") or attrs.get("name") or attrs.get("itemprop") or "").lower()
        if name in _META_NAMES and (parsed := _to_date(attrs.get("content", ""))):
            return parsed

    match = _TIME_TAG_RE.search(html or "")
    return _to_date(match.group(1)) if match else None


def fetch_published_date(
    url: str,
    *,
    timeout: float = 15.0,
    getter: Callable[..., object] | None = None,
) -> date | None:
    """Read the publication date from the article page; None if unavailable."""

    if not url.startswith(("http://", "https://")):
        return None
    try:
        if getter is None:
            import httpx

            getter = httpx.get
        response = getter(
            url,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; algo-news-bot)"},
        )
        return extract_published_date(getattr(response, "text", "") or "")
    except Exception:
        return None


@dataclass(frozen=True)
class SourceFreshness:
    ok: bool
    code: str = ""
    detail: str = ""
    published: date | None = None
    age_days: int | None = None

    @property
    def error_code(self) -> str:
        return f"SOURCE_UNSUITABLE_{self.code}" if self.code else ""


def check_freshness(
    published: date | datetime | None,
    *,
    today: date | None = None,
    max_days: int | None = None,
    allow_unknown: bool = False,
) -> SourceFreshness:
    """Fail when the article is older than the window or its date is unknown.

    allow_unknown=True is for rows queued before the date was recorded: a
    known-old article is still blocked, but an undated legacy row is not.
    """

    limit = max_age_days() if max_days is None else max_days
    if published is None:
        if allow_unknown:
            return SourceFreshness(True)
        return SourceFreshness(
            False,
            "PUBLISHED_DATE_UNKNOWN",
            "기사 발행일을 확인할 수 없어 최신 기사인지 보장할 수 없습니다.",
        )

    published_day = published.date() if isinstance(published, datetime) else published
    age = ((today or datetime.now().date()) - published_day).days
    if age > limit:
        return SourceFreshness(
            False,
            "SOURCE_TOO_OLD",
            f"기사가 {age}일 전({published_day.isoformat()}) 자료입니다 (허용 {limit}일).",
            published_day,
            age,
        )
    return SourceFreshness(True, published=published_day, age_days=age)


def resolve_published(
    feed_published: datetime | date | None,
    url: str,
    *,
    fetcher: Callable[[str], date | None] | None = None,
) -> date | None:
    """Prefer the feed's date; otherwise read it from the article page."""

    if feed_published is not None:
        return feed_published.date() if isinstance(feed_published, datetime) else feed_published
    return (fetcher or fetch_published_date)(url)


__all__ = [
    "DEFAULT_MAX_AGE_DAYS",
    "SourceFreshness",
    "check_freshness",
    "extract_published_date",
    "fetch_published_date",
    "max_age_days",
    "parse_date",
    "resolve_published",
]
