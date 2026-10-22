"""House style for card and caption text: no sentence-final periods."""
from __future__ import annotations

import re

_BEFORE = r"(?<=[가-힣A-Za-z\)\]\"'”’%])"
_CLOSERS = r"[\"'”’\)\]]*"
# A period that ends a line/text. A period closing a quotation ("...다." 라고 했다) is only dropped,
# since the sentence goes on after the quote.
_END_PERIOD = re.compile(
    _BEFORE + r"\.(?=" + _CLOSERS + r"[ \t]*(?:\n|$)|[\"'”’\)\]])"
)
# A period between two sentences on one line: the next sentence moves to its own line instead.
_MID_PERIOD = re.compile(_BEFORE + r"\.[ \t]+(?=\S)")


def strip_sentence_periods(text: str) -> str:
    """Remove sentence-final periods; sentences that shared a line are split onto separate lines.

    Decimals (3.5), dates (2026.10.22), domains (aitimes.com) and ellipses ("...") are untouched,
    since a period only counts when it follows a letter/closing mark and is followed by a space
    or the end of the line.
    """
    if not text:
        return text
    return _END_PERIOD.sub("", _MID_PERIOD.sub("\n", text))
