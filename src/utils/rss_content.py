"""Helpers for preserving article text embedded in RSS/Atom entries."""
from __future__ import annotations

from html import unescape
import re
from typing import Any


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
