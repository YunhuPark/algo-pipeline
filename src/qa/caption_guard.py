"""Catch numbers in a caption that the cards do not support.

Card text goes through claim verification; the Instagram caption is free text
from a language model and does not. A real caption said "1.5억 달러 기업가치"
for a company valued at $1.5B (15억 달러) — a tenfold error that would have
been posted as-is. This module compares every figure the caption states with
the figures on the cards and reports the ones it cannot match.

Figures are compared by value, not spelling: "15억", "1.5 billion" and "$1.5B"
are the same number, and "2,400만" equals "24 million". Years, plain small
counts and hashtags are ignored so ordinary wording is not flagged.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Longest alternatives first so "천만" is not read as "천" + "만".
_UNITS: dict[str, float] = {
    "조": 1e12,
    "억": 1e8,
    "천만": 1e7,
    "백만": 1e6,
    "만": 1e4,
    "천": 1e3,
    "trillion": 1e12,
    "billion": 1e9,
    "million": 1e6,
    "thousand": 1e3,
    "bn": 1e9,
}
# One-letter suffixes are only valid glued to the digits ("$1.5B", "24M"), never
# "Series B" or a standalone letter.
_LETTER_UNITS: dict[str, float] = {"t": 1e12, "b": 1e9, "m": 1e6, "k": 1e3}

_NUMBER = re.compile(
    r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)"
    r"(?:\s*(%|퍼센트|천만|백만|조|억|만|천|trillion|billion|million|thousand|bn)"
    r"|([TBMKtbmk])(?![A-Za-z]))?",
    re.IGNORECASE,
)
_HASHTAG = re.compile(r"#\S+")
_RELATIVE_TOLERANCE = 0.01


@dataclass(frozen=True)
class Figure:
    kind: str  # "pct" or "num"
    value: float
    text: str  # as written, for messages

    def matches(self, other: "Figure") -> bool:
        if self.kind != other.kind:
            return False
        if self.value == other.value:
            return True
        scale = max(abs(self.value), abs(other.value))
        return scale > 0 and abs(self.value - other.value) / scale <= _RELATIVE_TOLERANCE


def figures(text: str) -> list[Figure]:
    """Every checkable figure in `text` (hashtags excluded)."""

    found: list[Figure] = []
    for match in _NUMBER.finditer(_HASHTAG.sub(" ", text or "")):
        raw = match.group(1).replace(",", "")
        try:
            base = float(raw)
        except ValueError:
            continue
        word = (match.group(2) or "").lower()
        letter = (match.group(3) or "").lower()
        written = match.group(0).strip()

        if word in {"%", "퍼센트"}:
            found.append(Figure("pct", base, written))
            continue
        multiplier = _UNITS.get(word) or _LETTER_UNITS.get(letter)
        if multiplier:
            found.append(Figure("num", base * multiplier, written))
            continue
        # Plain numbers: years and small counts ("3가지", "2026년") are not claims.
        if base < 100 or 1900 <= base <= 2100:
            continue
        found.append(Figure("num", base, written))
    return found


# Superlatives a caption tends to add for punch. A card only says what its source
# says, so a strong word that appears in the caption but on no card is the
# caption overstating it ("데이터 보안을 유지하며" became "데이터 보안도 완벽!").
HYPE_WORDS = (
    "완벽", "압도적", "역대급", "혁명", "획기적", "독보적", "최고", "최초", "최강",
    "대박", "충격", "폭발적", "무조건", "반드시",
    # 여론·반응을 묘사하는 말도 카드에 없으면 캡션이 지어낸 것이다("여러 의견이 팽팽해!").
    "팽팽", "갑론을박", "논란", "뜨거운", "난리", "열광", "들썩", "술렁",
)


def unsupported_hype(caption: str, supporting_texts: list[str]) -> list[str]:
    """Strong words used in the caption that none of the cards use."""

    support = " ".join(supporting_texts)
    body = _HASHTAG.sub(" ", caption or "")
    return [word for word in HYPE_WORDS if word in body and word not in support]


# 숫자 바로 뒤에 붙어 숫자의 "의미"를 바꾸는 말("80%의 변화!"). 카드에서 같은 숫자 옆에 그 말이 없으면
# 캡션이 숫자의 뜻을 바꿔 쓴 것이다("시나리오의 80%를 반영"을 "80%의 변화"로).
MEANING_WORDS = (
    "변화", "증가", "감소", "상승", "하락", "성장", "급증", "급감", "절감", "인상", "인하",
    "확대", "축소", "폭등", "폭락", "개선", "악화",
)
_FIGURE_TOKEN = re.compile(r"\d[\d,\.]*\s?(?:%|퍼센트|만|억|조|배|명|개|대|달러|원)")


def figure_meaning_mismatch(caption: str, supporting_texts: list[str]) -> list[str]:
    """Figure+meaning pairs in the caption (e.g. "80%의 변화") that no card states."""

    support = " ".join(supporting_texts)
    problems: list[str] = []
    for match in _FIGURE_TOKEN.finditer(_HASHTAG.sub(" ", caption or "")):
        window = caption[match.end(): match.end() + 8]
        word = next((w for w in MEANING_WORDS if w in window), "")
        if not word:
            continue
        figure = re.sub(r"\s+", "", match.group(0))
        stated = any(
            word in re.sub(r"\s+", "", support[max(0, m.start() - 12): m.end() + 12])
            for m in _FIGURE_TOKEN.finditer(support)
            if re.sub(r"\s+", "", m.group(0)) == figure
        )
        label = f"{figure}의 {word}"
        if not stated and label not in problems:
            problems.append(label)
    return problems


def unsupported_figures(caption: str, supporting_texts: list[str]) -> list[str]:
    """Figures in `caption` that no supporting text states (as written)."""

    support = [fig for text in supporting_texts for fig in figures(text)]
    missing: list[str] = []
    for fig in figures(caption):
        if not any(fig.matches(known) for known in support) and fig.text not in missing:
            missing.append(fig.text)
    return missing
