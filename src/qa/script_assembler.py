import re
from typing import List

from src.schemas.card_news import Claim, CardNewsScript, Slide


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


def _claim_headline(claim: Claim) -> str:
    """Derive a specific headline without generating any new factual text."""

    if claim.display_title.strip():
        return claim.display_title.strip()
    first_clause = re.split(r"[.!?\n]", claim.claim_text, maxsplit=1)[0]
    return _shorten(first_clause, 22)


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

        # 1. Cover slide
        slides.append(Slide(
            slide_number=1,
            slide_type="cover",
            title=_fit_cover_title(topic, 22),
            body=_ANGLE_HOOKS.get(
                editorial_angle,
                "원문 근거로 핵심과 의미를 정리했습니다.",
            ),
            emoji="📰"
        ))

        # 2. Content slides
        content_claims = [c for c in verified_claims if c.claim_type != "cta"]
        max_content_slides = max(1, (num_cards or 6) - 2)
        for c in content_claims[:max_content_slides]:
            accent = ""
            if c.numbers:
                accent = c.numbers[0].raw_text
            elif c.entities:
                accent = c.entities[0]

            slides.append(Slide(
                slide_number=len(slides) + 1,
                slide_type="content",
                title=_claim_headline(c),
                body=c.claim_text,
                accent=accent
            ))

        # 3. CTA slide
        cta_claims = [c for c in verified_claims if c.claim_type == "cta"]
        cta_body = cta_claims[0].claim_text if cta_claims else "더 자세한 내용은 원문을 참고해 주세요."

        slides.append(Slide(
            slide_number=len(slides) + 1,
            slide_type="cta",
            title="어떤 변화가 중요할까요?",
            body=cta_body,
            emoji="👇"
        ))

        # Ensure exact requirements for CardNewsScript
        return CardNewsScript(
            topic=topic,
            hook=_ANGLE_HOOKS.get(
                editorial_angle,
                f"{_shorten(topic, 18)}, 핵심만 확인하세요",
            ),
            slides=slides,
            hashtags=_hashtags(topic, verified_claims),
        )
