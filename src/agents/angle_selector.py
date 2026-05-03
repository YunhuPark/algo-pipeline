"""
AngleSelector — 5가지 마케팅 앵글 동시 생성 + 사용자 선택
────────────────────────────────────────────────────────
같은 주제로 5가지 각도의 커버 카피를 만들고,
사용자가 가장 마음에 드는 앵글을 선택한다.

앵글 종류:
  공감   — "나도 이런 경험 있었는데..." 공감 유발
  공포   — "이거 모르면 뒤처진다" 긴박감/손실 회피
  이익   — "알면 이런 게 좋아진다" 실질적 이득
  편의   — "이렇게 하면 훨씬 쉬워진다" 간편함
  사회증거 — "이미 XX명이 주목하는" 트렌드/대중성
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel

from src.config import OPENAI_API_KEY, LLM_MODEL
from src.persona import Persona, load_persona


# ── Pydantic 스키마 ───────────────────────────────────────

class _AngleVariant(BaseModel):
    angle: str        # 앵글 이름
    cover_title: str  # 커버 제목 (20자 이하)
    hook: str         # 인스타 캡션 첫 줄 (30자 이하)
    reasoning: str    # 이 앵글이 효과적인 이유 (한 줄)


class _AngleVariants(BaseModel):
    variants: list[_AngleVariant]  # 정확히 5개


@dataclass
class SelectedAngle:
    angle: str
    cover_title: str
    hook: str
    reasoning: str


# ── 프롬프트 ─────────────────────────────────────────────

_SYSTEM = """
당신은 인스타그램 카드뉴스 마케팅 전문가입니다.
같은 뉴스 주제를 5가지 마케팅 앵글로 커버 카피를 작성합니다.

앵글별 특성:
- 공감: "나도 겪었던 그 상황..." 공감에서 시작
- 공포: "이거 모르면 손해" 긴박감/손실 회피 자극
- 이익: "알면 이런 점이 달라진다" 실질적 이득 강조
- 편의: "이렇게 하면 훨씬 쉬워진다" 간편함/효율
- 사회증거: "이미 XX만 명이 주목하는" 트렌드/대중성

규칙:
- cover_title: 최대 20자, 한국어, 강렬한 후킹
- hook: 최대 30자, 인스타 캡션 첫 줄용, 스크롤 멈추는 문구
- 5개 variants 정확히 반환
- 브랜드 '{brand_name}'({handle})의 MZ 뉴스 큐레이터 톤 유지
"""

_HUMAN = """
주제: {topic}

트렌드 요약:
{trend_summary}

위 주제로 5가지 앵글 커버 카피를 만들어주세요.
"""


# ── 생성 함수 ─────────────────────────────────────────────

def generate_angles(
    topic: str,
    trend_summary: str,
    persona: Persona | None = None,
) -> list[_AngleVariant]:
    p = persona or load_persona()
    llm = ChatOpenAI(model=LLM_MODEL, temperature=0.8, api_key=OPENAI_API_KEY)
    structured = llm.with_structured_output(_AngleVariants)
    prompt = ChatPromptTemplate.from_messages([
        ("system", _SYSTEM),
        ("human",  _HUMAN),
    ])
    chain = prompt | structured
    result = chain.invoke({
        "brand_name":    p.brand_name,
        "handle":        p.handle,
        "topic":         topic,
        "trend_summary": trend_summary[:500] if trend_summary else "트렌드 데이터 없음",
    })
    return result.variants


# ── 터미널 선택 UI ────────────────────────────────────────

_ANGLE_EMOJI = {
    "공감":    "🤝",
    "공포":    "⚡",
    "이익":    "💰",
    "편의":    "✨",
    "사회증거": "📊",
}


def select_angle(
    topic: str,
    trend_summary: str,
    persona: Persona | None = None,
    auto: bool = False,
) -> SelectedAngle:
    """
    5가지 앵글 생성 → 터미널에 출력 → 사용자 선택.
    auto=True 이면 자동으로 1번(공감) 선택.
    """
    print("\n  [AngleSelector] 5가지 마케팅 앵글 생성 중...")
    variants = generate_angles(topic, trend_summary, persona)

    if auto:
        v = variants[0]
        print(f"  [AngleSelector] 자동 선택: {v.angle} — {v.cover_title}")
        return SelectedAngle(**v.model_dump())

    # ── 터미널 출력 ───────────────────────────────────────
    print()
    print("  ┌─────────────────────────────────────────────────┐")
    print(f"  │  📰 '{topic}' — 5가지 앵글")
    print("  ├─────────────────────────────────────────────────┤")

    for i, v in enumerate(variants, 1):
        emoji = _ANGLE_EMOJI.get(v.angle, "▪")
        print(f"  │  [{i}] {emoji} {v.angle}")
        print(f"  │      커버: {v.cover_title}")
        print(f"  │      훅:   {v.hook}")
        print(f"  │      이유: {v.reasoning}")
        if i < len(variants):
            print("  │")

    print("  └─────────────────────────────────────────────────┘")
    print()

    # ── 입력 받기 ─────────────────────────────────────────
    while True:
        try:
            choice = input(f"  사용할 앵글 번호를 선택하세요 (1~{len(variants)}): ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(variants):
                v = variants[idx]
                print(f"\n  ✓ 선택됨: [{idx+1}] {v.angle} — {v.cover_title}\n")
                return SelectedAngle(**v.model_dump())
            else:
                print(f"  1~{len(variants)} 사이 숫자를 입력하세요.")
        except (ValueError, KeyboardInterrupt):
            print(f"\n  기본값(1번)으로 진행합니다.")
            v = variants[0]
            return SelectedAngle(**v.model_dump())
