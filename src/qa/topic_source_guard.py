"""Deterministic guard that rejects obviously unrelated source evidence.

The trend selector is LLM-assisted and may be unavailable or pick the highest
scoring candidate after a provider failure.  Before spending the expensive
claim/editorial retry budget, require the selected SourceLineage to contain the
user topic's meaningful anchors.  The guard is intentionally conservative: it
is disabled when the topic has no useful anchors and it never invents aliases
beyond a small explicit bilingual map.
"""
from __future__ import annotations

import re
import unicodedata

from src.qa.deterministic_verifier import QualityGateError
from src.schemas.card_news import SourceLineage


_GENERIC_TOPIC_TOKENS = {
    "ai",
    # 이 계정의 후보 기사 대부분이 AI·IT·비즈니스 카테고리라 "it"이 거의
    # 모든 기사에 등장한다 — 다른 기사와 구분해주는 고유명사가 아니라
    # 카테고리 자체를 가리키는 일반 단어이므로 "ai"처럼 앵커에서 제외한다.
    "it",
    "핵심",
    "요약",
    "정리",
    "총정리",
    "최신",
    "뉴스",
    "트렌드",
    "동향",
    "현황",
    "분석",
    "전망",
    "이슈",
    "리뷰",
    "가이드",
    "방법",
    "발표",
    "공개",
    "출시",
    "업데이트",
    "기능",
    "변화",
    "서비스",
    "아이디어",
    "summary",
    "roundup",
    "latest",
    "news",
    "trend",
    "trends",
    "analysis",
    "overview",
    "review",
    "guide",
    "update",
    "updates",
    "launch",
    "release",
}

_TOPIC_ALIASES: dict[str, tuple[str, ...]] = {
    "애플": ("apple",),
    "구글": ("google",),
    "마이크로소프트": ("microsoft",),
    "오픈ai": ("openai",),
    "오픈에이아이": ("openai",),
    "앤트로픽": ("anthropic",),
    "엔비디아": ("nvidia",),
    "메타": ("meta",),
    "아마존": ("amazon",),
    "비트코인": ("bitcoin",),
    "이더리움": ("ethereum",),
    "제미나이": ("gemini",),
    "클로드": ("claude",),
    "에이전트": ("agent", "agents"),
    "칩": ("chip", "chips"),
    # Apple uses both the WWDC abbreviation and the spelled-out event name
    # across Newsroom/Developer pages. Treat them as the same deterministic
    # anchor so an official source is not rejected merely for using the long form.
    "wwdc": (
        "worldwide developers conference",
        "wwdc26",
        "wwdc 26",
        "wwdc2026",
        "wwdc 2026",
    ),
}

# 한글 뒤에 영문이 공백 없이 바로 붙는 고유명사("오픈AI", "챗GPT")가 흔한데,
# 아래 순수 한글/영문 패턴은 각각 따로만 매칭해 "오픈"+"AI"로 쪼개버린다 —
# _TOPIC_ALIASES의 "오픈ai" 같은 키와 절대 일치할 수 없어 강한 앵커가 하나도
# 안 남고 가드 자체가 무력화된다. 붙여쓴 한글+영문(또는 영문+한글) 조합을
# 먼저 시도해 하나의 토큰으로 묶는다.
_TOKEN_RE = re.compile(
    r"[가-힣]+[A-Za-z][A-Za-z0-9+._-]*"
    r"|[A-Za-z][A-Za-z0-9+._-]*[가-힣]+"
    r"|[A-Za-z][A-Za-z0-9+._-]{1,}"
    r"|[가-힣]{2,}"
    r"|20\d{2}"
)
_ASCII_WORD_RE_TEMPLATE = r"(?<![a-z0-9]){token}(?![a-z0-9])"

# Large amounts/quantities (fines, prices, counts) tend to survive Korean/English
# translation as the same magnitude even when every surrounding descriptor word is
# translated away. Used as a fail-closed fallback anchor only when the topic has no
# ASCII/alias anchor at all, so ordinary translated-but-numberless topics are unaffected.
_NUMBER_RE = re.compile(
    r"\$?\s*(?P<num>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>천|만|억|조|thousand|million|billion|trillion)?",
    re.IGNORECASE,
)
_NUMBER_MAGNITUDE = {
    "천": 1e3,
    "만": 1e4,
    "억": 1e8,
    "조": 1e12,
    "thousand": 1e3,
    "million": 1e6,
    "billion": 1e9,
    "trillion": 1e12,
}
_NUMBER_ANCHOR_MIN = 1000
_NUMBER_ANCHOR_RELATIVE_TOLERANCE = 0.02


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").casefold()


def _topic_anchor_groups(topic: str) -> list[tuple[str, ...]]:
    groups: list[tuple[str, ...]] = []
    seen: set[tuple[str, ...]] = set()

    for raw in _TOKEN_RE.findall(topic or ""):
        token = _normalize(raw).strip("._-")
        if not token or token in _GENERIC_TOPIC_TOKENS or re.fullmatch(r"20\d{2}", token):
            continue
        variants = tuple(dict.fromkeys((token, *(_TOPIC_ALIASES.get(token) or ()))))
        if variants in seen:
            continue
        seen.add(variants)
        groups.append(variants)

    return groups


def _is_strong_anchor_group(variants: tuple[str, ...]) -> bool:
    """Return whether an anchor is reliable enough for fail-closed matching.

    ASCII identifiers and explicitly mapped bilingual entities/events are
    stable across Korean/English source text. Free-form Korean descriptor words
    are intentionally not used to block generation because exact lexical
    matching creates false positives for otherwise valid translated sources.
    """

    token = variants[0]
    return token.isascii() or token in _TOPIC_ALIASES


def _contains_variant(haystack: str, variant: str) -> bool:
    if variant.isascii():
        pattern = _ASCII_WORD_RE_TEMPLATE.format(token=re.escape(variant))
        return re.search(pattern, haystack) is not None
    return variant in haystack


def _numeric_anchors(text: str) -> list[float]:
    """Extract large, translation-stable numeric magnitudes from ``text``.

    Bare 4-digit years (2026, 2025, ...) are excluded so they don't collide
    with the year exclusion already applied to lexical anchors. Small counts
    are excluded via ``_NUMBER_ANCHOR_MIN`` since they are common noise
    (page counts, list sizes) rather than reliable topical anchors.
    """

    values: list[float] = []
    for match in _NUMBER_RE.finditer(text or ""):
        raw = match.group("num")
        unit = (match.group("unit") or "").lower()
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue

        multiplier = _NUMBER_MAGNITUDE.get(unit, 1)
        if multiplier == 1 and "." not in raw and re.fullmatch(r"(19|20)\d{2}", raw):
            continue

        value *= multiplier
        if value >= _NUMBER_ANCHOR_MIN:
            values.append(value)

    return values


def _numbers_close(a: float, b: float) -> bool:
    return abs(a - b) <= max(a, b) * _NUMBER_ANCHOR_RELATIVE_TOLERANCE


def topic_matches_source(topic: str, source_title: str, evidence_text: str) -> bool:
    """Return whether source text contains enough meaningful topic anchors.

    The fail-closed gate only relies on strong anchors that survive common
    Korean/English translation differences. One strong anchor must match; two
    or more strong anchors require at least two. This keeps ``Apple WWDC`` from
    accepting a generic Apple article while avoiding false rejection from weak
    descriptor words such as a translated category or editorial phrase.

    When the topic has no ASCII/alias anchor at all, a large translation-stable
    number (a fine, a price, a headcount) is used as a fallback anchor instead
    of unconditionally passing: purely descriptive Korean topics without any
    such number keep the prior permissive behavior.
    """

    anchors = _topic_anchor_groups(topic)
    strong_anchors = [group for group in anchors if _is_strong_anchor_group(group)]

    if strong_anchors:
        haystack = _normalize(f"{source_title}\n{evidence_text}")
        matched = sum(
            1
            for variants in strong_anchors
            if any(_contains_variant(haystack, variant) for variant in variants)
        )
        required = 1 if len(strong_anchors) == 1 else 2
        return matched >= required

    topic_numbers = _numeric_anchors(topic)
    if not topic_numbers:
        return True

    source_numbers = _numeric_anchors(f"{source_title}\n{evidence_text}")
    return any(
        _numbers_close(topic_value, source_value)
        for topic_value in topic_numbers
        for source_value in source_numbers
    )


def topic_anchors_present(topic: str, text: str) -> bool:
    """Return whether text mentions at least one strong topic anchor.

    Unlike ``topic_matches_source``, only a single anchor match is required.
    ``topic_matches_source`` is a fail-closed gate for the evidence actually
    used to write claims, where two-of-two anchors avoids false positives.
    Supplementary content such as a YouTube candidate pool needs a much looser
    floor: it only has to rule out results that share no real connection to
    the topic at all (for example an unrelated same-name product), not
    demand full topical coverage.
    """

    anchors = _topic_anchor_groups(topic)
    strong_anchors = [group for group in anchors if _is_strong_anchor_group(group)]
    if not strong_anchors:
        return True

    haystack = _normalize(text)
    return any(
        _contains_variant(haystack, variant)
        for variants in strong_anchors
        for variant in variants
    )


def assert_source_lineage_matches_topic(lineage: SourceLineage) -> None:
    """Fail closed before claim generation when selected evidence is unrelated."""

    evidence_text = "\n".join(item.text for item in lineage.evidence_passages)
    if topic_matches_source(lineage.topic, lineage.source_title, evidence_text):
        return

    raise QualityGateError(
        "SOURCE_TOPIC_MISMATCH",
        (
            "선택된 원문/근거가 요청 주제의 핵심 앵커를 충분히 포함하지 않습니다. "
            "무관한 최고점 기사를 사용해 생성을 계속하지 않고 중단합니다."
        ),
    )
