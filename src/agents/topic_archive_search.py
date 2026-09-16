"""Small first-party archive fallback for explicit event/product topics.

The normal TrendAnalyzer is intentionally biased toward very recent news. That
is the wrong retrieval mode for requests such as ``Apple WWDC 2026 summary``
when the event happened months earlier. This module is used only after recent
candidates fail the deterministic topic/source guard.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import re

import feedparser

from src.qa.topic_source_guard import topic_matches_source
from src.schemas.card_news import TrendResult
from src.utils.rss_content import extract_feed_entry_content


_APPLE_ARCHIVE_FEEDS: tuple[tuple[str, str], ...] = (
    ("Apple Newsroom", "https://www.apple.com/newsroom/rss-feed.rss"),
    ("Apple Developer News", "https://developer.apple.com/news/rss/news.rss"),
    ("Apple Developer Releases", "https://developer.apple.com/news/releases/rss/releases.rss"),
)


def _topic_year(topic: str) -> int | None:
    match = re.search(r"\b(20\d{2})\b", topic or "")
    return int(match.group(1)) if match else None


def _apple_archive_requested(topic: str) -> bool:
    lowered = (topic or "").casefold()
    return any(token in lowered for token in ("apple", "애플", "wwdc"))


def _published_at(entry) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        raw = getattr(entry, attr, None)
        if raw:
            try:
                return datetime(*raw[:6])
            except Exception:
                continue
    return None


def collect_archive_matches(topic: str, max_results: int = 8) -> list[TrendResult]:
    """Return topic-matching first-party archive items, newest first.

    Currently Apple is supported because it is the concrete historical-event
    gap exposed by production. Unsupported topics simply return an empty list;
    callers remain fail-closed rather than broadening to unrelated sources.
    """

    if not _apple_archive_requested(topic):
        return []

    requested_year = _topic_year(topic)
    now = datetime.now()
    if requested_year:
        earliest = datetime(requested_year, 1, 1)
        latest = datetime(requested_year + 1, 1, 1)
    else:
        earliest = now - timedelta(days=365)
        latest = now + timedelta(days=1)

    candidates: list[tuple[datetime, TrendResult]] = []
    seen_urls: set[str] = set()

    for source_name, feed_url in _APPLE_ARCHIVE_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
        except Exception:
            continue

        for entry in feed.entries[:120]:
            url = str(getattr(entry, "link", "") or "").strip()
            title = str(getattr(entry, "title", "") or "").strip()
            if not url or not title or url in seen_urls:
                continue

            published = _published_at(entry)
            if published and not (earliest <= published < latest):
                continue

            content = extract_feed_entry_content(entry).strip()
            # The first-party feed identity establishes Apple as the publisher;
            # WWDC/product anchors must still occur in the actual title/summary.
            selection_text = f"Apple {title}"
            if content:
                selection_text += f"\n{content}"
            if not topic_matches_source(topic, selection_text, content):
                continue

            seen_urls.add(url)
            score = 3.0 if "developer.apple.com" in url else 2.8
            candidates.append(
                (
                    published or earliest,
                    TrendResult(
                        title=title,
                        url=url,
                        content=content or title,
                        score=score,
                    ),
                )
            )

    candidates.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in candidates[:max_results]]
