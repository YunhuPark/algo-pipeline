"""
CardNews Verifier — 자기검증 루프
────────────────────────────────────────────────────────
Phase 1. 규칙 검증 (Python)
  - 슬라이드 수 / 타입 순서
  - 제목 20자 이하 / 본문 100자 이하
  - 빈 필드 없음

Phase 2. AI 자기평가 (GPT-4o-mini)
  - 후킹력, 가독성, 브랜드 톤, 정보 전달력, 완성도 각 10점
  - 평균 7.0점 이상 → 통과
  - 미달 → 구체적 피드백 반환 (재생성에 주입)

사용:
    result = verify(script, persona)
    if result.passed:
        # 렌더링 진행
    else:
        # result.feedback 를 content_creator에 다시 주입
"""
from __future__ import annotations

from dataclasses import dataclass, field

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel

from src.config import OPENAI_API_KEY
from src.schemas.card_news import CardNewsScript, Slide
from src.persona import Persona

PASS_THRESHOLD = 7.0   # 평균 점수 기준
MAX_TITLE_LEN  = 22
MAX_BODY_LEN   = 130
MIN_BODY_LEN   = 60    # 80 → 60: "짧고 자연스러운 문장" 스타일과 충돌 방지


# ── 검증 결과 ─────────────────────────────────────────────

@dataclass
class VerifyResult:
    passed: bool
    score: float               # AI 평균 점수 (0~10), 규칙 실패 시 0.0
    rule_errors: list[str] = field(default_factory=list)
    ai_scores: dict[str, float] = field(default_factory=dict)
    feedback: str = ""         # 재생성 시 프롬프트에 주입할 구체적 피드백

    def summary(self) -> str:
        if self.rule_errors:
            return f"규칙 실패: {' / '.join(self.rule_errors)}"
        sc = ", ".join(f"{k} {v:.1f}" for k, v in self.ai_scores.items())
        status = "✓ 통과" if self.passed else "✗ 재생성 필요"
        return f"{status} | 평균 {self.score:.1f}점 ({sc})"


# ── Phase 1: 규칙 검증 ────────────────────────────────────

def _rule_check(script: CardNewsScript, expected_count: int) -> list[str]:
    errors: list[str] = []

    # 슬라이드 수
    if len(script.slides) != expected_count:
        errors.append(f"슬라이드 {len(script.slides)}장 (기대: {expected_count}장)")

    # 타입 순서: cover → content... → cta
    types = [s.slide_type for s in script.slides]
    if types and types[0] != "cover":
        errors.append("첫 슬라이드가 cover 타입이 아님")
    if types and types[-1] != "cta":
        errors.append("마지막 슬라이드가 cta 타입이 아님")

    for slide in script.slides:
        label = f"슬라이드{slide.slide_number}"
        # 빈 필드
        if not slide.title.strip():
            errors.append(f"{label} 제목 없음")
        if not slide.body.strip():
            errors.append(f"{label} 본문 없음")
        # 글자 수
        if len(slide.title) > MAX_TITLE_LEN:
            errors.append(f"{label} 제목 {len(slide.title)}자 (최대 {MAX_TITLE_LEN}자)")
        if len(slide.body) > MAX_BODY_LEN:
            errors.append(f"{label} 본문 {len(slide.body)}자 (최대 {MAX_BODY_LEN}자)")
        # content 슬라이드만 최소 길이 체크 (cover/cta는 짧아도 됨)
        if slide.slide_type == "content" and len(slide.body.replace("\n", "")) < MIN_BODY_LEN:
            errors.append(f"{label} 본문 {len(slide.body)}자 (최소 {MIN_BODY_LEN}자)")

    # 해시태그
    if len(script.hashtags) < 5:
        errors.append(f"해시태그 {len(script.hashtags)}개 (최소 5개)")

    # 막연한 표현 금지 (content 슬라이드)
    vague_patterns = ["될 전망", "예상된다", "주목된다", "기대된다", "전망이다", "될 것으로"]
    for slide in script.slides:
        if slide.slide_type == "content":
            for pat in vague_patterns:
                if pat in slide.body or pat in slide.title:
                    errors.append(f"슬라이드{slide.slide_number} 막연한 표현 '{pat}' 사용")
                    break

    # AI 냄새 문체 감지 (content + cover 슬라이드)
    ai_patterns = [
        "이를 통해", "이를 활용", "이에 따라", "이에 따른",
        "해당 서비스", "해당 기능", "해당 기업", "해당 기술",
        "함으로써", "됨으로써",
        "것으로 나타났", "것으로 알려졌", "에 따르면",
        "다양한 분야", "여러 방면", "혁신적인", "획기적인",
        "중요성이 부각", "주목받고 있",
        "~할 수 있습니다",
    ]
    for slide in script.slides:
        if slide.slide_type in ("content", "cover"):
            for pat in ai_patterns:
                text = slide.title + " " + slide.body
                if pat in text:
                    errors.append(f"슬라이드{slide.slide_number} AI 문체 감지: '{pat}'")
                    break

    return errors


# ── Phase 2: AI 자기평가 ──────────────────────────────────

class _AIScore(BaseModel):
    hook_power: float        # 후킹력: 커버 제목이 스크롤을 멈추게 하는가 (1~10)
    readability: float       # 가독성: 각 슬라이드가 한눈에 읽히는가 (1~10)
    brand_tone: float        # 브랜드 톤: 알고의 MZ 뉴스 큐레이터 느낌인가 (1~10)
    info_quality: float      # 정보 전달력: 내용이 정확하고 유익한가 (1~10)
    naturalness: float       # 자연스러움: AI 냄새 없이 사람이 쓴 것처럼 읽히는가 (1~10)
    completeness: float      # 완성도: 커버→내용→CTA 흐름이 자연스럽고 빠진 내용 없는가 (1~10)
    feedback: str            # 점수가 낮은 항목에 대한 구체적 개선 피드백 (한국어)


_EVAL_SYSTEM = """
당신은 인스타그램 카드뉴스 품질 심사위원입니다.
'알고'(@algo.kr) 계정의 뉴스 카드뉴스 스크립트를 평가합니다.
각 항목을 1~10점으로 채점하고, 개선이 필요한 부분에 대한 구체적 피드백을 한국어로 작성하세요.

채점 기준:
- hook_power (후킹력): 커버 제목이 MZ세대의 스크롤을 멈추게 하는가
- readability (가독성): 각 슬라이드가 3초 안에 핵심이 전달되는가
- brand_tone (브랜드 톤): '알고'의 친근하고 트렌디한 뉴스 큐레이터 톤인가
- info_quality (정보 전달력): 뉴스 내용이 정확하고 핵심이 잘 담겼는가
- naturalness (자연스러움): AI 냄새 없이 사람이 쓴 것처럼 읽히는가
    · 감점 요인: "이를 통해", "해당 기능", "~함으로써", "혁신적인", "다양한 분야",
                 "것으로 나타났다", "이에 따라", 긴 복합문 (~고 ~며 ~서 3개 이상 연결)
    · 가점 요인: 짧고 끊어지는 문장, 수치·고유명사 문장 앞 배치, 역접·반전, 구어체
- completeness (완성도): 커버→내용→CTA 흐름이 자연스럽고 빠진 핵심 내용 없는가

feedback: 7점 미만인 항목에 대해 "슬라이드N: 구체적으로 어떻게 바꿔야 하는지" 형식으로 작성.
          모든 항목이 7점 이상이면 "전반적으로 양호합니다."
"""

_EVAL_HUMAN = """
주제: {topic}

=== 카드뉴스 스크립트 ===
커버 제목: {cover_title}
커버 후킹 문구: {hook}

슬라이드 목록:
{slides_text}

해시태그: {hashtags}
===

위 스크립트를 평가해주세요.
"""


def _ai_evaluate(script: CardNewsScript) -> _AIScore:
    slides_text = "\n".join(
        f"[{s.slide_number}] ({s.slide_type}) 제목: {s.title} | 본문: {s.body}"
        for s in script.slides
    )
    cover = next((s for s in script.slides if s.slide_type == "cover"), script.slides[0])

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2, api_key=OPENAI_API_KEY)
    structured = llm.with_structured_output(_AIScore)
    prompt = ChatPromptTemplate.from_messages([
        ("system", _EVAL_SYSTEM),
        ("human",  _EVAL_HUMAN),
    ])
    chain = prompt | structured
    return chain.invoke({
        "topic":       script.topic,
        "cover_title": cover.title,
        "hook":        script.hook,
        "slides_text": slides_text,
        "hashtags":    " ".join(script.hashtags[:10]),
    })


# ── 공개 API ──────────────────────────────────────────────

def verify(
    script: CardNewsScript,
    persona: Persona,
    expected_count: int | None = None,
) -> VerifyResult:
    """
    카드뉴스 스크립트를 규칙 검증 + AI 자기평가로 검증.

    Returns:
        VerifyResult.passed  = True 이면 업로드 진행 가능
        VerifyResult.feedback = 재생성 시 프롬프트에 주입할 피드백
    """
    n = expected_count or len(script.slides)

    # ── Phase 1: 규칙 검증 ─────────────────────────────
    rule_errors = _rule_check(script, n)
    if rule_errors:
        feedback = "다음 규칙을 지켜서 다시 작성해주세요:\n" + "\n".join(f"- {e}" for e in rule_errors)
        return VerifyResult(
            passed=False,
            score=0.0,
            rule_errors=rule_errors,
            feedback=feedback,
        )

    # ── Phase 2: AI 자기평가 ────────────────────────────
    ai = _ai_evaluate(script)
    scores = {
        "후킹력": ai.hook_power,
        "가독성": ai.readability,
        "브랜드톤": ai.brand_tone,
        "정보전달": ai.info_quality,
        "자연스러움": ai.naturalness,
        "완성도": ai.completeness,
    }
    avg = sum(scores.values()) / len(scores)
    passed = avg >= PASS_THRESHOLD

    feedback = ""
    if not passed:
        low = [f"{k}({v:.1f}점)" for k, v in scores.items() if v < PASS_THRESHOLD]
        feedback = (
            f"다음 항목이 기준({PASS_THRESHOLD}점) 미달입니다: {', '.join(low)}\n\n"
            f"개선 방향:\n{ai.feedback}"
        )

    return VerifyResult(
        passed=passed,
        score=avg,
        ai_scores=scores,
        feedback=feedback,
    )
