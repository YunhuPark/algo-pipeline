"""Helpers for preserving article text embedded in RSS/Atom entries."""
from __future__ import annotations

from html import unescape
import re
from typing import Any
from urllib.parse import urlparse


# A listing/section/tag page has a non-root path too (e.g.
# "https://techcrunch.com/category/artificial-intelligence/") but is still
# not a single article - it's a page of teaser links to many different
# stories. A crawler scraping it would stitch "facts" together from
# unrelated headlines, the same failure shape as scraping a bare homepage.
_LISTING_PATH_SEGMENTS = {
    "category", "categories", "tag", "tags", "topic", "topics",
    "section", "sections", "archive", "archives", "list", "lists",
    "index", "feed", "feeds", "rss", "author", "authors", "page",
    "search", "collection", "collections",
}

# A section front page is sometimes just one bare word ("/tech", "/it",
# "/politics") with none of the structural keywords above in the path at
# all - the word itself IS the section name, which varies by outlet and
# language. This is only checked when it's the *entire* path (see below):
# a multi-segment path that merely contains "tech" as one component (e.g.
# "/tech/apple-ships-new-iphone") is a normal article filed under that
# section, not a listing page, so this list must never be checked against
# individual segments the way _LISTING_PATH_SEGMENTS is.
_BARE_SECTION_NAMES = {
    "tech", "technology", "it", "politics", "sports", "business", "world",
    "opinion", "science", "economy", "society", "entertainment", "culture",
    "health", "money", "finance", "market", "markets", "news",
    "national", "international", "local",
}


def looks_like_article_url(url: str) -> bool:
    """Reject a bare homepage or listing/section URL masquerading as an article.

    A search API can return a media outlet's front page itself (e.g.
    "https://yozm.wishket.com", no path) as a "result". A crawler then
    scrapes that page's nav menu, footer, and "popular this week" widget as
    if it were an article body, and an LLM can extract plausible-looking
    numbers/names from that site chrome even though none of it is actual
    news. A real article link always has a path beyond the domain root and
    that path isn't just a category/tag/section listing of other stories.
    """
    path = urlparse(url).path.strip("/")
    if not path:
        return False
    parts = [segment.lower() for segment in path.split("/") if segment]
    segments = set(parts)
    if segments & _LISTING_PATH_SEGMENTS:
        return False
    if len(parts) == 1 and parts[0] in _BARE_SECTION_NAMES:
        return False
    return True


def _field(value: Any, name: str, default: Any = "") -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _plain_text(raw: str) -> str:
    if not raw:
        return ""
    try:
        from bs4 import BeautifulSoup

        text = BeautifulSoup(raw, "html.parser").get_text(separator=" ", strip=True)
    except ImportError:
        text = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", unescape(text)).strip()


def extract_feed_entry_content(entry: Any, limit: int = 5000) -> str:
    """Return the richest text carried by a feed entry, not its short preview."""

    candidates: list[str] = []
    for item in _field(entry, "content", []) or []:
        value = _field(item, "value", "")
        if isinstance(value, str):
            candidates.append(_plain_text(value))

    for name in ("summary", "description"):
        value = _field(entry, name, "")
        if isinstance(value, str):
            candidates.append(_plain_text(value))

    richest = max(candidates, key=len, default="")
    return richest[:limit]
