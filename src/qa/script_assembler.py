import re
from typing import List

from pydantic import BaseModel

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


class _CoverCopy(BaseModel):
    cover_title: str
    hook: str
    cta_title: str
    cta_body: str


_COVER_COPY_SYSTEM = """
당신은 인스타그램 뉴스 카드뉴스의 커버/후크/CTA 문구를 쓰는 카피라이터입니다.
아래 "카드 핵심 요점"만 참고해 스타일리시한 포장 문구를 쓰세요.
새로운 사실·수치·고유명사·평가·전망은 절대 추가하거나 추론하지 마세요 —
이미 검증된 카드 내용을 소개/마무리하는 문구만 씁니다.

작성 규칙:
- cover_title: 22자 이내, 호기심을 유발하는 완결된 문장/구 (말줄임표 금지)
- hook: 40자 이내, 커버 바로 아래 붙는 한 줄 후크
- cta_title: 20자 이내, 마지막 장 제목 (질문형 또는 저장 유도형)
- cta_body: 45~130자, 마지막 장 본문 (자연스러운 참여 유도 문구)

"개선 피드백"이 주어지면 그 지적을 반드시 반영해 이전과 다르게 쓰세요.
"""

_COVER_COPY_HUMAN = """
주제: {topic}

카드 핵심 요점(참고용, 새 사실 추가 금지):
{claim_headlines}

개선 피드백: {feedback}
"""


def _generate_cover_copy(
    topic: str,
    claim_headlines: list[str],
    validation_feedback: str = "",
) -> "_CoverCopy | None":
    """LLM으로 커버/후크/CTA 문구를 생성. 실패 시 None (호출부가 템플릿으로 대체)."""
    try:
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_openai import ChatOpenAI

        from src.config import OPENAI_API_KEY

        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.5, api_key=OPENAI_API_KEY)
        structured = llm.with_structured_output(_CoverCopy)
        prompt = ChatPromptTemplate.from_messages(
            [("system", _COVER_COPY_SYSTEM), ("human", _COVER_COPY_HUMAN)]
        )
        chain = prompt | structured
        result = chain.invoke(
            {
                "topic": topic,
                "claim_headlines": "\n".join(f"- {h}" for h in claim_headlines)
                or "(없음)",
                "feedback": validation_feedback.strip() or "(없음)",
            }
        )
        if not (result.cover_title.strip() and result.hook.strip()
                and result.cta_title.strip() and result.cta_body.strip()):
            raise ValueError("cover copy response has an empty field")
        return result
    except Exception as e:
        print(
            "  [ScriptAssembler] 커버/CTA 생성 실패, 기본 템플릿 사용: "
            f"{type(e).__name__}"
        )
        return None


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
    Assembles a CardNewsScript from verified claims. Claim text itself is never
    rewritten by an LLM, preventing re-hallucination of facts. The cover/hook/CTA
    slide copy is the one exception: it carries no factual claim of its own, so
    it may be phrased by an LLM (constrained to the existing claim headlines) so
    that editorial repair feedback can actually change it on retry; a fixed
    template can never respond to that feedback. See `_generate_cover_copy`.
    """

    @staticmethod
    def assemble(
        topic: str,
        claims: List[Claim],
        *,
        num_cards: int | None = None,
        editorial_angle: str = "",
        validation_feedback: str = "",
    ) -> CardNewsScript:
        verified_claims = [c for c in claims if c.verification_status == "verified"]

        # We need at least some content
        if not verified_claims:
            raise ValueError("No verified claims available to assemble script.")

        content_claims = [c for c in verified_claims if c.claim_type != "cta"]
        max_content_slides = max(1, (num_cards or 6) - 2)

        # 1. Cover + CTA copy. Generated by an LLM constrained to the actual
        # card headlines (no new facts) so that editorial repair feedback
        # (e.g. "hook_power 낮음, 커버 제목을 더 자극적으로") can actually change
        # this text on retry. `_ANGLE_HOOKS`/`_default_hook`/`_contextual_cta`
        # remain the fail-safe fallback if the LLM call fails.
        cover_copy = _generate_cover_copy(
            topic,
            [_claim_headline(c) for c in content_claims[:max_content_slides]],
            validation_feedback,
        )
        if cover_copy is not None:
            cover_title = _fit_cover_title(cover_copy.cover_title, 22)
            cover_hook = _shorten(cover_copy.hook, 40)
            cta_title, generated_cta_body = (
                _shorten(cover_copy.cta_title, 20),
                cover_copy.cta_body.strip(),
            )
        else:
            cover_title = _cover_title(topic, 22)
            cover_hook = _ANGLE_HOOKS.get(editorial_angle, _default_hook(topic))
            cta_title, generated_cta_body = _contextual_cta(topic)

        slides = []

        # 1a. Cover slide: frame the factual topic as a safe question instead
        # of copying generic input such as "... 핵심 요약" verbatim.
        slides.append(Slide(
            slide_number=1,
            slide_type="cover",
            title=cover_title,
            body=cover_hook,
            emoji="📰"
        ))

        # 2. Content slides
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
        # the generated (or templated) contextual engagement copy above, which
        # makes no new factual assertion.
        cta_claims = [c for c in verified_claims if c.claim_type == "cta"]
        cta_body = cta_claims[0].claim_text if cta_claims else generated_cta_body

        slides.append(Slide(
            slide_number=len(slides) + 1,
            slide_type="cta",
            title=cta_title,
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
