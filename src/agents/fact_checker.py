"""
Phase 2.5 (선택): Fact Checker — 2-Layer 팩트체크
────────────────────────────────────────────────────────
Layer 1 (원문 대조): 카드 문장이 원문에서 나온 것인지 GPT-4o로 확인
  → 원문에 없는 내용 생성(hallucination) 감지

Layer 2 (외부 검증): Tavily 검색으로 수치·사실 교차 검증
  → 원문 자체가 틀렸거나 outdated인 경우 대비

사용:
    from src.agents.fact_checker import check_script

    report = check_script(script, source_text="...")  # source_text = 원문 본문
    if not report.passed:
        for item in report.flagged_items:
            print(item.claim, item.verdict, item.note)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel
from tavily import TavilyClient

from src.config import OPENAI_API_KEY, TAVILY_API_KEY, LLM_MODEL
from src.schemas.card_news import CardNewsScript


# ── 결과 데이터클래스 ─────────────────────────────────────

@dataclass
class ClaimResult:
    """단일 claim 검증 결과"""
    claim: str
    verdict: Literal["confirmed", "disputed", "unverifiable"]
    confidence: float          # 0.0 ~ 1.0
    note: str = ""             # 판단 근거 한 줄 요약


@dataclass
class FactCheckReport:
    """전체 팩트체크 보고서"""
    total: int
    confirmed: int
    disputed: int
    unverifiable: int
    flagged_items: list[ClaimResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """disputed가 0개이면 통과"""
        return self.disputed == 0

    def summary(self) -> str:
        status = "통과" if self.passed else "경고"
        return (
            f"[팩트체크 {status}] 총 {self.total}건 — "
            f"확인 {self.confirmed} / 논란 {self.disputed} / 확인불가 {self.unverifiable}"
        )


# ── GPT 구조화 출력 스키마 ────────────────────────────────

class _ClaimList(BaseModel):
    claims: list[str]


class _Verdict(BaseModel):
    verdict: Literal["confirmed", "disputed", "unverifiable"]
    confidence: float   # 0.0 ~ 1.0
    note: str           # 판단 근거 (한국어, 1~2문장)


# ── LLM 프롬프트 ──────────────────────────────────────────

_EXTRACT_SYSTEM = """
당신은 팩트체킹 전문가입니다.
카드뉴스 스크립트에서 외부 검색으로 검증 가능한 핵심 수치·사실만 추출합니다.

추출 기준 (좁게 적용):
- 구체적인 숫자/통계 (예: "95% 실패율", "1억 명 사용자", "1조 달러 규모")
- 특정 날짜와 결합된 발표 (예: "2024년 12월 출시")
- 기업이나 인물의 발표 내용 중 수치 포함된 것

추출 제외:
- 일반적 설명, 의견, 평가
- "빠르다", "많다", "높다" 같은 상대적 표현
- 미래 예측 ("~할 전망", "~예상")
- 원문에서 직접 인용된 것이 명확한 수치

최대 5개까지 추출하세요. 명확한 검증 대상이 없으면 빈 리스트를 반환합니다.
"""

_EXTRACT_HUMAN = """
아래 카드뉴스 스크립트에서 검증 필요한 수치·사실을 추출하세요.

주제: {topic}
후킹 문구: {hook}

슬라이드:
{slides_text}
"""

_VERIFY_SYSTEM = """
당신은 팩트체킹 전문가입니다.
주어진 claim과 검색 결과를 비교해 사실 여부를 판단합니다.

판단 기준:
- confirmed: 검색 결과가 claim을 명확히 뒷받침함 (confidence >= 0.7)
- disputed:  검색 결과가 claim과 상충하거나 수치가 크게 다름
- unverifiable: 검색 결과가 부족하거나 관련 정보가 없음

confidence는 0.0~1.0 사이 소수로, 판단의 확신 정도를 나타냅니다.
note는 한국어로 1~2문장 이내로 작성합니다.
"""

_VERIFY_HUMAN = """
검증할 claim:
"{claim}"

검색 결과 (최대 3건):
{search_results}

위 검색 결과를 바탕으로 claim의 사실 여부를 판단하세요.
"""


# ── Phase 1: Claims 추출 ──────────────────────────────────

def extract_claims(script: CardNewsScript) -> list[str]:
    """
    GPT-4o-mini로 스크립트에서 검증 필요한 수치·사실을 추출.

    Returns:
        검증 대상 claim 문자열 목록 (최대 10개)
    """
    slides_text = "\n".join(
        f"[{s.slide_number}] {s.title} — {s.body}"
        + (f" ({s.accent})" if s.accent else "")
        for s in script.slides
    )

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.0, api_key=OPENAI_API_KEY)
    structured = llm.with_structured_output(_ClaimList)
    prompt = ChatPromptTemplate.from_messages([
        ("system", _EXTRACT_SYSTEM),
        ("human", _EXTRACT_HUMAN),
    ])
    chain = prompt | structured
    result: _ClaimList = chain.invoke({
        "topic": script.topic,
        "hook": script.hook,
        "slides_text": slides_text,
    })
    return result.claims


# ── Phase 2: 단일 Claim 검증 ─────────────────────────────

def verify_claim(claim: str) -> ClaimResult:
    """
    Tavily 검색으로 claim을 검색한 뒤 GPT-4o-mini가 사실 여부 판단.

    Args:
        claim: 검증할 수치·사실 문자열

    Returns:
        ClaimResult (verdict, confidence, note 포함)
    """
    # 1. Tavily 검색
    client = TavilyClient(api_key=TAVILY_API_KEY)
    try:
        search_resp = client.search(
            query=claim,
            search_depth="basic",
            max_results=3,
            include_answer=True,
        )
        results = search_resp.get("results", [])
        # 검색 결과 텍스트 조합 (제목 + 내용 일부)
        search_text = ""
        for i, r in enumerate(results[:3], 1):
            title = r.get("title", "")
            content = r.get("content", "")[:300]
            url = r.get("url", "")
            search_text += f"{i}. [{title}]\n{content}\n출처: {url}\n\n"

        if not search_text.strip():
            search_text = "관련 검색 결과를 찾을 수 없습니다."

    except Exception as e:
        # Tavily 오류 시 unverifiable 처리
        return ClaimResult(
            claim=claim,
            verdict="unverifiable",
            confidence=0.0,
            note=f"검색 중 오류 발생: {e}",
        )

    # 2. GPT-4o-mini로 판단
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.0, api_key=OPENAI_API_KEY)
    structured = llm.with_structured_output(_Verdict)
    prompt = ChatPromptTemplate.from_messages([
        ("system", _VERIFY_SYSTEM),
        ("human", _VERIFY_HUMAN),
    ])
    chain = prompt | structured
    try:
        verdict_obj: _Verdict = chain.invoke({
            "claim": claim,
            "search_results": search_text,
        })
        return ClaimResult(
            claim=claim,
            verdict=verdict_obj.verdict,
            confidence=max(0.0, min(1.0, verdict_obj.confidence)),
            note=verdict_obj.note,
        )
    except Exception as e:
        return ClaimResult(
            claim=claim,
            verdict="unverifiable",
            confidence=0.0,
            note=f"AI 판단 중 오류: {e}",
        )


# ── Layer 1: 원문 대조 hallucination 감지 ────────────────

class _HallucinationCheck(BaseModel):
    hallucinated_slides: list[int]   # 원문 근거 없는 슬라이드 번호 목록
    notes: list[str]                 # 각 슬라이드별 문제점 설명


_HALLUCINATION_SYSTEM = """당신은 AI 카드뉴스 팩트체커입니다.
카드뉴스 각 슬라이드의 내용이 [원문 기사]에 실제로 나온 내용인지 검사합니다.

판단 기준 (엄격하게 적용하지 말 것):
- 원문에 명시된 사실 → 통과 ✓
- 원문에서 자연스럽게 유추·요약 가능한 내용 → 통과 ✓
- 원문 내용을 카드뉴스 형식으로 쉽게 풀어 쓴 것 → 통과 ✓
- 일반 상식 수준의 배경 설명 → 통과 ✓
- 원문에 완전히 없는 구체적 수치·기업명·인물명·기능명 → hallucination (문제) ✗
- 원문과 정반대 의미로 왜곡된 내용 → hallucination (문제) ✗

주의: 카드뉴스는 요약본이므로 원문에 없는 표현도 의미가 동일하면 통과.
오직 사실 관계가 명백히 틀리거나 원문에 전혀 없는 구체적 수치/고유명사만 문제로 분류.

hallucinated_slides: 명백히 문제 있는 슬라이드 번호만 (없으면 빈 리스트)
notes: hallucinated_slides와 같은 길이, 각 문제점을 한 문장으로"""

_HALLUCINATION_HUMAN = """[원문 기사]
{source_text}

[카드뉴스 슬라이드]
{slides_text}

각 슬라이드가 원문 기사에 근거한 내용인지 검사하세요."""


def _check_hallucination(script: CardNewsScript, source_text: str) -> list[ClaimResult]:
    """Layer 1: 카드 내용 vs 원문 직접 대조"""
    if not source_text or len(source_text) < 100:
        return []

    # CTA 슬라이드는 팔로우 유도 문구이므로 원문 대조 제외
    fact_slides = [s for s in script.slides if s.slide_type != "cta"]
    slides_text = "\n".join(
        f"[슬라이드{s.slide_number}] {s.title} / {s.body}"
        + (f" / 강조: {s.accent}" if s.accent else "")
        for s in fact_slides
    )

    llm = ChatOpenAI(model=LLM_MODEL, temperature=0, api_key=OPENAI_API_KEY)
    structured = llm.with_structured_output(_HallucinationCheck)
    prompt = ChatPromptTemplate.from_messages([
        ("system", _HALLUCINATION_SYSTEM),
        ("human", _HALLUCINATION_HUMAN),
    ])
    try:
        result: _HallucinationCheck = (prompt | structured).invoke({
            "source_text": source_text[:8000],   # 4000 → 8000: 다중 기사 합산 시 충분한 컨텍스트
            "slides_text": slides_text,
        })
        claims: list[ClaimResult] = []
        for slide_num, note in zip(result.hallucinated_slides, result.notes):
            slide = next((s for s in script.slides if s.slide_number == slide_num), None)
            claim_text = f"[슬라이드{slide_num}] {slide.body[:80] if slide else '?'}"
            claims.append(ClaimResult(
                claim=claim_text,
                verdict="disputed",
                confidence=0.9,
                note=f"[원문 불일치] {note}",
            ))
            print(f"    ⚠️  슬라이드{slide_num} 원문 불일치: {note}")
        return claims
    except Exception as e:
        print(f"  [FactChecker] hallucination 검사 실패: {e}")
        return []


# ── 공개 API ──────────────────────────────────────────────

def check_script(
    script: CardNewsScript,
    source_text: str = "",   # 원문 본문 (있으면 Layer 1 실행)
) -> FactCheckReport:
    """
    2-Layer 팩트체크.

    Layer 1: 원문 대조 hallucination 감지 (source_text 있을 때)
    Layer 2: Tavily 검색 외부 교차 검증

    Returns:
        FactCheckReport
    """
    all_results: list[ClaimResult] = []
    confirmed = disputed = unverifiable = 0

    # ── Layer 1: 원문 대조 ─────────────────────────────────
    if source_text:
        print("  [FactChecker] Layer 1 — 원문 대조 hallucination 검사...")
        hal_results = _check_hallucination(script, source_text)
        all_results.extend(hal_results)
        disputed += len(hal_results)
        if hal_results:
            print(f"  [FactChecker] Layer 1: {len(hal_results)}개 원문 불일치 감지")
        else:
            print("  [FactChecker] Layer 1: 원문 일치 ✓")

    # ── Layer 2: 외부 검증 ─────────────────────────────────
    print("  [FactChecker] Layer 2 — claim 추출 중...")
    claims = extract_claims(script)

    if not claims:
        print("  [FactChecker] Layer 2: 검증할 수치·사실 없음")
    else:
        print(f"  [FactChecker] {len(claims)}건 추출 → 외부 교차 검증 시작")
        for i, claim in enumerate(claims, 1):
            print(f"  [FactChecker] ({i}/{len(claims)}) {claim[:60]}...")
            cr = verify_claim(claim)
            all_results.append(cr)
            if cr.verdict == "confirmed":
                confirmed += 1
            elif cr.verdict == "disputed":
                disputed += 1
                print(f"    ⚠️  논란: {cr.note}")
            else:
                unverifiable += 1

    report = FactCheckReport(
        total=len(all_results),
        confirmed=confirmed,
        disputed=disputed,
        unverifiable=unverifiable,
        flagged_items=all_results,
    )
    print(f"  [FactChecker] {report.summary()}")
    return report
