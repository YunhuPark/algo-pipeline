"""Deterministic editorial checks applied after factual verification."""
from __future__ import annotations

import re
from collections import Counter
from difflib import SequenceMatcher
from typing import Iterable

from src.qa.deterministic_verifier import QualityGateError
from src.qa.editorial_intent import is_roundup_topic
from src.schemas.card_news import (
    Claim,
    MAX_CONTENT_BODY_CHARS,
    MIN_CONTENT_BODY_CHARS,
)


_EDITORIAL_ROLES = {
    "context",
    "change",
    "mechanism",
    "evidence",
    "limitation",
    "impact",
}

_GENERIC_TOPIC_TOKENS = {
    "ai",
    "인공지능",
    "최신",
    "뉴스",
    "트렌드",
    "필수",
    "용어",
    "알아야",
    "정복",
    "요약",
    "핵심",
    "당신이",
    "기술",
    "새로운",
    "관련",
    "이번",
    "발표",
    "공개",
    "기능",
    "추가",
    "사용",
    "사용자",
    "지원",
    "제공",
    "내용",
    "확인",
    "서비스",
    "데이터",
    "정보",
    "설계",
    "변화",
    "영향",
    "결과",
    "중요",
    "설명",
}


def _normalized_copy(text: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", (text or "").lower())


def _pairs(items: list[Claim]) -> Iterable[tuple[Claim, Claim]]:
    for index, left in enumerate(items):
        for right in items[index + 1:]:
            yield left, right


def _topic_tokens(text: str) -> set[str]:
    normalized = (text or "").lower()
    for korean, canonical in {
        "오픈ai": "openai",
        "구글": "google",
        "애플": "apple",
        "메타": "meta",
        "앤트로픽": "anthropic",
        "엔비디아": "nvidia",
    }.items():
        normalized = normalized.replace(korean, canonical)

    tokens: set[str] = set()
    for token in re.findall(r"[a-z][a-z0-9-]{2,}|[가-힣]{2,}", normalized):
        if re.fullmatch(r"[가-힣]+", token):
            for suffix in (
                "으로",
                "에서",
                "부터",
                "까지",
                "됐다",
                "했다",
                "한다",
                "하는",
                "라는",
                "이며",
                "들이",
                "들은",
                "들을",
                "들",
                "은",
                "는",
                "이",
                "가",
                "을",
                "를",
                "의",
                "에",
                "도",
            ):
                if token.endswith(suffix) and len(token) - len(suffix) >= 2:
                    token = token[: -len(suffix)]
                    break
        tokens.add(token)
    return {token for token in tokens if token not in _GENERIC_TOPIC_TOKENS}


def _validate_roundup_theme_diversity(topic: str, selected: list[Claim]) -> None:
    """Prevent explicit recap requests from collapsing into one narrow feature.

    Pairwise duplicate checks catch near-identical copy, but they do not catch a
    four-card recap where every card discusses a different angle of the same
    subfeature.  For explicit roundup intent, a non-topic token appearing in at
    least 75% of the selected cards is treated as a concentration signal.

    Topic anchor tokens (for example ``apple`` and ``wwdc``) are excluded so a
    legitimate event recap is not penalized for staying on-topic.
    """

    if not is_roundup_topic(topic) or len(selected) < 4:
        return

    topic_tokens = _topic_tokens(topic)
    document_frequency: Counter[str] = Counter()
    for claim in selected:
        claim_tokens = _topic_tokens(
            " ".join(
                (
                    claim.display_title,
                    claim.claim_text,
                    *claim.entities,
                )
            )
        )
        document_frequency.update(claim_tokens - topic_tokens)

    threshold = max(3, (len(selected) * 3 + 3) // 4)
    dominant = sorted(
        (
            (token, count)
            for token, count in document_frequency.items()
            if count >= threshold
        ),
        key=lambda item: (-item[1], item[0]),
    )
    if dominant:
        token, count = dominant[0]
        raise QualityGateError(
            "EDITORIAL_COVERAGE_INSUFFICIENT",
            "Roundup coverage is too narrow: "
            f"subtopic '{token}' dominates {count}/{len(selected)} content cards. "
            "Choose distinct announcements, features, limitations, or impacts "
            "from the available evidence instead of repeatedly expanding one subtopic.",
        )


def _validate_roundup_evidence_spread(topic: str, selected: list[Claim]) -> None:
    """Require broad recap cards to originate from more than one evidence item.

    Lexical diversity alone can be gamed accidentally: four cards can discuss
    privacy, beta status, memory handling and availability while all expanding
    the same narrow feature article. For an explicit roundup with four or more
    content cards, at least two different evidence passages must be the primary
    cited support across the selected cards. This strengthens editorial coverage
    only; every claim still has to pass the existing factual/semantic gates.
    """

    if not is_roundup_topic(topic) or len(selected) < 4:
        return

    primary_evidence_ids = {
        claim.evidence_ids[0]
        for claim in selected
        if claim.evidence_ids
    }
    if len(primary_evidence_ids) >= 2:
        return

    raise QualityGateError(
        "EDITORIAL_COVERAGE_INSUFFICIENT",
        "Roundup coverage is concentrated in one evidence passage. "
        "Use at least two independently cited evidence items for the content cards "
        "so the recap covers distinct announcements instead of four angles of one feature.",
    )


def validate_claim_editorial_quality(
    claims: list[Claim],
    *,
    topic: str,
    target_content_slides: int,
    source_title: str = "",
) -> None:
    """Reject thin, repetitive claim sets before rendering expensive images."""

    content_claims = [claim for claim in claims if claim.claim_type != "cta"]
    if len(content_claims) < target_content_slides:
        raise QualityGateError(
            "EDITORIAL_COVERAGE_INSUFFICIENT",
            f"Need {target_content_slides} distinct content claims, got {len(content_claims)}.",
        )

    selected = content_claims[:target_content_slides]
    topic_tokens = _topic_tokens(topic) | _topic_tokens(source_title)
    claim_tokens = _topic_tokens(
        " ".join(
            [
                part
                for claim in selected
                for part in (
                    claim.display_title,
                    claim.claim_text,
                    *claim.entities,
                )
            ]
        )
    )
    if not topic_tokens or not (topic_tokens & claim_tokens):
        raise QualityGateError(
            "EDITORIAL_TOPIC_MISMATCH",
            "The selected claims do not directly explain the requested topic.",
        )

    for claim in selected:
        title = claim.display_title.strip()
        if not title:
            raise QualityGateError(
                "EDITORIAL_HEADLINE_MISSING",
                "Every content claim needs a standalone display_title.",
                claim.claim_id,
            )
        if title.endswith(("…", "...")) or len(title) > 22:
            raise QualityGateError(
                "EDITORIAL_HEADLINE_INVALID",
                f"Headline must be complete and at most 22 characters: {title}",
                claim.claim_id,
            )

        # Match the renderer verifier's visible-character convention: spaces
        # count toward line length while line breaks do not.
        body_length = len(claim.claim_text.replace("\n", ""))
        if (
            body_length < MIN_CONTENT_BODY_CHARS
            or body_length > MAX_CONTENT_BODY_CHARS
        ):
            raise QualityGateError(
                "EDITORIAL_COPY_LENGTH_INVALID",
                "Claim copy must be readable on one card "
                f"({MIN_CONTENT_BODY_CHARS}-{MAX_CONTENT_BODY_CHARS} chars), "
                f"got {body_length}.",
                claim.claim_id,
            )

    required_role_count = min(3, target_content_slides)
    roles = {claim.editorial_role for claim in selected} & _EDITORIAL_ROLES
    if len(roles) < required_role_count:
        raise QualityGateError(
            "EDITORIAL_ROLE_DIVERSITY_INSUFFICIENT",
            f"Need {required_role_count} distinct editorial roles, got {sorted(roles)}.",
        )

    _validate_roundup_theme_diversity(topic, selected)
    _validate_roundup_evidence_spread(topic, selected)

    for left, right in _pairs(selected):
        left_title = _normalized_copy(left.display_title)
        right_title = _normalized_copy(right.display_title)
        left_body = _normalized_copy(left.claim_text)
        right_body = _normalized_copy(right.claim_text)
        title_similarity = SequenceMatcher(None, left_title, right_title).ratio()
        body_similarity = SequenceMatcher(None, left_body, right_body).ratio()
        if (
            left_title == right_title
            or title_similarity >= 0.88
            or body_similarity >= 0.84
        ):
            raise QualityGateError(
                "EDITORIAL_CLAIM_REDUNDANT",
                f"Claims {left.claim_id} and {right.claim_id} repeat the same card point.",
                right.claim_id,
            )
