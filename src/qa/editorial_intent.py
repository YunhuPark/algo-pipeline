"""Small deterministic helpers for editorial intent classification."""
from __future__ import annotations

import re


_ROUNDUP_PATTERNS = (
    r"요약",
    r"총정리",
    r"핵심\s*정리",
    r"한눈에",
    r"모아\s*보기",
    r"정리해",
    r"정리해줘",
    r"summary",
    r"roundup",
    r"recap",
    r"overview",
)


def is_roundup_topic(topic: str) -> bool:
    """Return True when the user explicitly asks for broad recap/roundup coverage.

    The classifier intentionally does not treat generic words such as ``최신`` or
    ``트렌드`` as roundup intent.  Those requests may still be better served by a
    single strong article.  Explicit recap language means the user expects several
    distinct points from the event/topic instead of four cards about one subtopic.
    """

    text = re.sub(r"\s+", " ", (topic or "").strip()).lower()
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in _ROUNDUP_PATTERNS)
