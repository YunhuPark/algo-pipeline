import re
from typing import List

from src.schemas.card_news import Claim, CardNewsScript, NormalizedNumber, Slide


_ANGLE_HOOKS = {
    "리스트형": "핵심만 모아 저장해두세요",
    "Before/After": "무엇이 달라졌는지 확인하세요",
    "즉시실행": "적용 전에 핵심부터 확인하세요",
    "몰랐던사실": "놓치기 쉬운 핵심을 짚었습니다",
    "공포": "과장 없이 실제 변화를 확인하세요",
    "공감": "복잡한 소식을 쉽게 풀었습니다",
    "이익": "우리에게 미칠 영향을 확인하세요",
    "사회증거": "지금 주목받는 이유를 확인하세요",
}

_GENERIC_TOPIC_SUFFIX_RE = re.compile(
    r"(?:\s*[-:|·]\s*)?(?:핵심\s*)?(?:요약|정리|총정리|한눈에\s*보기)\s*$",
    re.IGNORECASE,
)


def _shorten(text: str, limit: int) -> str:
    clean = re.sub(r"\s+", " ", text).strip().rstrip(".!?")
    if len(clean) <= limit:
        return clean
    return clean[: max(1, limit - 1)].rstrip() + "…"


def _fit_cover_title(text: str, limit: int = 22) -> str:
    """Fit a cover title without leaving a visibly truncated headline."""

    clean = re.sub(r"\s+", " ", text).strip().rstrip(".!?")
    if len(clean) <= limit:
        return clean

    first_clause = re.split(r"[:：|·—–]", clean, maxsplit=1)[0].strip()
    if 6 <= len(first_clause) <= limit:
        return first_clause

    fitted: list[str] = []
    for word in clean.split():
        candidate = " ".join([*fitted, word])
        if len(candidate) > limit:
            break
        fitted.append(word)
    return " ".join(fitted) if fitted else clean[:limit].rstrip()


def _topic_subject(topic: str) -> str:
    """Remove generic packaging words while keeping the factual topic intact."""

    clean = re.sub(r"\s+", " ", topic or "").strip().rstrip(".!?")
    subject = _GENERIC_TOPIC_SUFFIX_RE.sub("", clean).strip(" -:|·")
    return subject or clean


def _cover_title(topic: str, limit: int = 22) -> str:
    """Create a curiosity-led cover without inventing a factual assertion."""

    subject = _topic_subject(topic)
    candidates = (
        f"{subject} 뭐가 핵심?",
        f"{subject} 왜 주목할까?",
        f"{subject} 핵심만 짚기",
    )
    for candidate in candidates:
        if len(candidate) <= limit:
            return candidate

    # Preserve a complete subject instead of cutting through a word. Long
    # topics can still rely on the separate hook line for editorial energy.
    return _fit_cover_title(subject, limit)


def _default_hook(topic: str) -> str:
    subject = _topic_subject(topic)
    compact_subject = _shorten(subject, 12)
    return f"{compact_subject}, 지금 볼 포인트만 골랐어요"


def _contextual_cta(topic: str) -> tuple[str, str]:
    subject = _topic_subject(topic)
    compact_subject = _shorten(subject, 18)
    return (
        "가장 눈에 띈 포인트는?",
        f"{compact_subject}에서 가장 눈에 들어온 포인트는 뭐였나요? "
        "저장해두고 필요할 때 핵심만 다시 확인해보세요.",
    )


def _claim_headline(claim: Claim) -> str:
    """Derive a specific headline without generating any new factual text."""

    if claim.display_title.strip():
        return claim.display_title.strip()
    first_clause = re.split(r"[.!?\n]", claim.claim_text, maxsplit=1)[0]
    return _shorten(first_clause, 22)


_APPROXIMATE_ENGLISH_LABELS = {
    "thousands": "수천",
    "tens of thousands": "수만",
    "hundreds of thousands": "수십만",
    "millions": "수백만",
    "tens of millions": "수천만",
    "hundreds of millions": "수억",
    "billions": "수십억",
    "tens of billions": "수백억",
    "hundreds of billions": "수천억",
    "trillions": "수조",
}


def _display_number(number: NormalizedNumber) -> str:
    """Render an approximate English source label naturally in Korean."""

    raw = re.sub(r"\s+", " ", number.raw_text).strip()
    lowered = raw.lower()
    is_dollars = bool(
        re.search(r"\b(?:dollars?|usd)\b", lowered)
        or number.unit.strip().lower() in {"달러", "dollar", "dollars", "usd"}
    )
    magnitude = re.sub(
        r"\s+of\s+(?:dollars?|usd)\s*$",
        "",
        lowered,
    ).strip()
    localized = _APPROXIMATE_ENGLISH_LABELS.get(magnitude)
    if localized:
        return f"{localized} 달러" if is_dollars else localized
    return raw


def _primary_number(claim: Claim) -> NormalizedNumber | None:
    """Prefer the verified number named by the headline, then the largest value."""

    if not claim.numbers:
        return None
    normalized_title = re.sub(r"\s+", "", claim.display_title)
    for number in claim.numbers:
        normalized_raw = re.sub(r"\s+", "", number.raw_text)
        if normalized_raw and normalized_raw in normalized_title:
            return number
    return max(claim.numbers, key=lambda item: abs(item.normalized_value))


def _visual_type(claim: Claim) -> str:
    if claim.editorial_role == "mechanism":
        return "process"
    if claim.editorial_role == "limitation":
        return "warning"
    if claim.editorial_role == "impact":
        return "impact"
    if claim.editorial_role == "change" and len(claim.numbers) >= 2:
        return "comparison"
    if claim.numbers:
        return "hero_stat"
    return "entity"


def _hashtags(topic: str, claims: List[Claim]) -> list[str]:
    tags = ["#알고", "#카드뉴스", "#뉴스분석", "#팩트체크", "#인사이트"]
    candidates = [topic]
    candidates.extend(entity for claim in claims for entity in claim.entities)
    for item in candidates:
        normalized = re.sub(r"[^0-9A-Za-z가-힣_]", "", item)
        if normalized and len(normalized) <= 24:
            tag = f"#{normalized}"
            if tag not in tags:
                tags.append(tag)
    return tags[:10]


class ScriptAssembler:
    """
    Assembles a CardNewsScript strictly from verified claims without invoking the LLM to rewrite them,
    preventing re-hallucination.
    """

    @staticmethod
    def assemble(
        topic: str,
        claims: List[Claim],
        *,
        num_cards: int | None = None,
        editorial_angle: str = "",
    ) -> CardNewsScript:
        verified_claims = [c for c in claims if c.verification_status == "verified"]

        # We need at least some content
        if not verified_claims:
            raise ValueError("No verified claims available to assemble script.")

        slides = []

        # 1. Cover slide: frame the factual topic as a safe question instead of
        # copying generic input such as "... 핵심 요약" verbatim.
        cover_hook = _ANGLE_HOOKS.get(editorial_angle, _default_hook(topic))
        slides.append(Slide(
            slide_number=1,
            slide_type="cover",
            title=_cover_title(topic, 22),
            body=cover_hook,
            emoji="📰"
        ))

        # 2. Content slides
        content_claims = [c for c in verified_claims if c.claim_type != "cta"]
        max_content_slides = max(1, (num_cards or 6) - 2)
        for c in content_claims[:max_content_slides]:
            primary_number = _primary_number(c)
            accent = _display_number(primary_number) if primary_number else ""
            if not accent and c.entities:
                accent = c.entities[0]
            visual_numbers = sorted(
                c.numbers,
                key=lambda item: item is not primary_number,
            )[:3]
            visual_values = [_display_number(item) for item in visual_numbers]
            visual_labels = [item.subject.strip() for item in visual_numbers]
            if not visual_values and c.entities:
                visual_values = [c.entities[0]]

            slides.append(Slide(
                slide_number=len(slides) + 1,
                slide_type="content",
                title=_claim_headline(c),
                body=c.claim_text,
                accent=accent,
                visual_type=_visual_type(c),
                visual_values=visual_values,
                visual_labels=visual_labels,
            ))

        # 3. CTA slide. An evidence-backed CTA is preserved when supplied; the
        # normal Claim pipeline does not generate CTA claims, so the fallback is
        # contextual engagement copy that makes no new factual assertion.
        cta_claims = [c for c in verified_claims if c.claim_type == "cta"]
        contextual_title, contextual_body = _contextual_cta(topic)
        cta_body = cta_claims[0].claim_text if cta_claims else contextual_body

        slides.append(Slide(
            slide_number=len(slides) + 1,
            slide_type="cta",
            title=contextual_title,
            body=cta_body,
            emoji="👇"
        ))

        # Ensure exact requirements for CardNewsScript
        return CardNewsScript(
            topic=topic,
            hook=cover_hook,
            slides=slides,
            hashtags=_hashtags(topic, verified_claims),
        )
