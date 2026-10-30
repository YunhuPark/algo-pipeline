"""A weak "what people are searching today" signal from Google Trends Korea (free RSS).

Used only to annotate candidates for the topic selector. It never admits an article by itself: the AI
scope, freshness and evidence checks decide that. Any failure returns no terms, and selection goes on.
"""
from __future__ import annotations

import re
import time

import httpx

FEED_URL = "https://trends.google.com/trending/rss?geo=KR"
CACHE_SECONDS = 3600
_cache: dict = {"at": 0.0, "terms": []}


def parse_terms(xml_text: str) -> list[tuple[str, str]]:
    """[(keyword, approx traffic)] from the feed text; empty when it has no items."""
    terms: list[tuple[str, str]] = []
    for item in re.findall(r"<item>(.*?)</item>", xml_text or "", flags=re.S):
        title = re.search(r"<title>(.*?)</title>", item, flags=re.S)
        traffic = re.search(r"approx_traffic>(.*?)<", item)
        if title and title.group(1).strip():
            terms.append((re.sub(r"<!\[CDATA\[|\]\]>", "", title.group(1)).strip(), traffic.group(1).strip() if traffic else ""))
    return terms


def fetch_terms(force: bool = False) -> list[str]:
    """Today's trending keywords (cached for an hour). [] when the feed cannot be read."""
    if not force and _cache["terms"] and time.time() - _cache["at"] < CACHE_SECONDS:
        return _cache["terms"]
    try:
        response = httpx.get(FEED_URL, timeout=15, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
        terms = [t for t, _ in parse_terms(response.text)]
    except (httpx.HTTPError, ValueError):
        return _cache["terms"]
    _cache.update(at=time.time(), terms=terms)
    return terms


def matches(text: str, terms: list[str]) -> list[str]:
    """Trending terms (2+ characters) that appear in the text, case-insensitively."""
    haystack = (text or "").casefold()
    return [t for t in terms if len(t.strip()) >= 2 and t.casefold() in haystack]
