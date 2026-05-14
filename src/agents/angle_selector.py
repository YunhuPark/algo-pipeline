"""
AngleSelector — 8가지 마케팅 앵글 + 저장률 기반 자동 선택
────────────────────────────────────────────────────────
인스타그램 고조회수 AI 카드뉴스의 실제 패턴에서 도출한 앵글 시스템.

저장률 높은 순 (실제 인스타 패턴):
  리스트형   — "N가지 정리" 형식. 저장해두고 하나씩 써먹음 (저장률 1위)
  Before/After — "기존 vs AI" 대비. 바로 적용하고 싶어짐 (저장률 2위)
  즉시실행   — "지금 바로 복붙" 느낌. 프롬프트·도구 소개 (저장률 3위)
  몰랐던사실 — "나만 모르면 안 돼" FOMO 유발 (조회율 1위)
  공포       — "이거 모르면 손해" 긴박감/손실 회피
  공감       — "나도 이런 경험 있었는데..." 공감 유발
  이익       — "알면 이런 게 좋아진다" 실질적 이득 강조
  사회증거   — "이미 XX만 명이 주목하는" 트렌드/대중성
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from src.config import OPENAI_API_KEY, LLM_MODEL
from src.persona import Persona, load_persona


# ── Pydantic 스키마 ───────────────────────────────────────

class _AngleVariant(BaseModel):
    angle: str        # 앵글 이름
    cover_title: str  # 커버 제목 (20자 이하)
    hook: str         # 인스타 캡션 첫 줄 (30자 이하)
    reasoning: str    # 이 앵글이 효과적인 이유 (한 줄)
    expected_save_rate: str = Field(default="보통", description="예상 저장률: 높음/보통/낮음")


class _AngleVariants(BaseModel):
    variants: list[_AngleVariant]  # 정확히 5개
    best_index: int = Field(default=0, description="가장 저장률·조회율 높을 앵글 번호 (1~5)")
    best_reason: str = Field(default="", description="best 선택 이유 한 줄")


@dataclass
class SelectedAngle:
    angle: str
    cover_title: str
    hook: str
    reasoning: str
    expected_save_rate: str = "보통"


# ── 프롬프트 ─────────────────────────────────────────────

_SYSTEM = """
당신은 인스타그램 AI·테크 카드뉴스 계정의 마케팅 전문가입니다.
같은 뉴스 주제를 5가지 마케팅 앵글로 커버 카피를 작성하고,
인스타그램 저장률·조회수가 가장 높을 앵글을 선택합니다.

=== 사용 가능한 앵글 (저장률 높은 순) ===

1. 리스트형 (저장률 최고)
   - "AI 도구 N가지 정리", "ChatGPT 활용법 5가지" 형식
   - 독자가 "나중에 하나씩 써봐야지"해서 저장함
   - cover_title 예: "ChatGPT 꿀기능 5가지 정리" / "무료 AI 도구 7선"
   - hook 예: "이거 모르면 AI 1% 활용도 못 한 거" / "저장해두면 나중에 다 씀"

2. Before/After (저장률 2위)
   - "기존 방식 vs AI 방식" 명확한 대비, 시간·비용·품질 차이
   - 독자가 "나도 이렇게 해봐야겠다"해서 저장함
   - cover_title 예: "3시간 → 30분, 진짜 됨?" / "코딩 0줄로 앱 만든 후기"
   - hook 예: "30초 만에 진짜로 되는지 확인해봄" / "써보고 충격받은 기능"

3. 즉시실행 (저장률 3위)
   - "지금 바로 복붙해서 쓸 수 있는" 프롬프트·도구·방법론
   - 독자가 "당장 써봐야지"해서 저장함
   - cover_title 예: "지금 바로 써먹는 AI 프롬프트" / "복붙하면 되는 GPT 명령어"
   - hook 예: "이 프롬프트 하나로 보고서 끝" / "ChatGPT에게 이렇게 물어봐"

4. 몰랐던사실 (조회율 1위)
   - "90%가 모르는", "아직 모르는 사람 많은" 독점 정보 느낌
   - FOMO("나만 뒤처지면 안 돼") 자극
   - cover_title 예: "GPT 유저 90%가 모르는 기능" / "아직도 모르면 손해인 AI"
   - hook 예: "이거 알면 주변에 자랑하게 됨" / "찾아봐도 잘 안 나오는 기능"

5. 공포 (클릭률 높음)
   - "이거 모르면 뒤처진다" 긴박감·손실 회피 자극
   - 경쟁·일자리·돈과 연결될 때 효과적
   - cover_title 예: "AI 못 쓰면 5년 후 이렇게 됨" / "개발자도 대체되는 일 생겼다"
   - hook 예: "지금 모르면 3년 뒤 후회함" / "이 직군, AI가 이미 대체 시작"

6. 공감 (시청 지속률 높음)
   - "나도 이런 경험 있었는데..." 공감에서 시작
   - 독자가 "맞아 나도 그래서 불편했는데"라며 계속 읽음
   - cover_title 예: "ChatGPT 써도 결과물이 별로일 때" / "AI 쓰는데 왜 난 안 되지?"
   - hook 예: "나도 처음엔 이거 몰라서 시간 낭비했음" / "써봤는데 실망했던 분들께"

7. 이익 (전환율 높음)
   - "알면 이런 점이 달라진다" 실질적 이득 강조
   - 돈·시간·취업·생산성과 직결될 때 효과적
   - cover_title 예: "AI로 월 50만원 버는 법 공개" / "이 기술 하나로 면접 합격"
   - hook 예: "이거 배우면 연봉 협상에 쓸 수 있음" / "시간당 수입이 달라지는 툴"

8. 사회증거 (신뢰도 높음)
   - "이미 X만 명이 쓰는" 트렌드·대중성 강조
   - 검증된 것을 확인하고 싶을 때 효과적
   - cover_title 예: "1,000만 명이 지금 쓰는 AI" / "OpenAI가 드디어 터뜨렸다"
   - hook 예: "1주일 만에 100만 다운로드 찍은 이유" / "지금 모두가 쓰는 그 도구"

=== 앵글 선택 규칙 ===
- 5가지 앵글을 선택해서 각각 cover_title·hook 작성
- best_index: 5가지 중 이 주제에서 인스타그램 저장률이 가장 높을 앵글 번호 (1~5)
- 실용적·구체적 주제 → 리스트형/Before-After/즉시실행 우선
- 뉴스성 강한 주제 → 몰랐던사실/사회증거 우선
- 취업·돈 관련 → 이익/공포 우선

규칙:
- cover_title: 최대 20자, 한국어, 반드시 숫자 또는 고유명사 포함
- hook: 최대 30자, 캡션 첫 줄용. 숫자 또는 고유명사로 시작
- 브랜드 '{brand_name}'({handle})의 MZ 뉴스 큐레이터 톤 유지
"""

_HUMAN = """
주제: {topic}

트렌드 요약:
{trend_summary}

위 주제로 5가지 앵글 커버 카피를 만들어주세요.
이 주제에서 인스타그램 저장률이 가장 높을 앵글을 best_index로 알려주세요.
"""


# ── 생성 함수 ─────────────────────────────────────────────

def generate_angles(
    topic: str,
    trend_summary: str,
    persona: Persona | None = None,
) -> tuple[list[_AngleVariant], int, str]:
    """앵글 5개 생성 + best 추천 인덱스 반환"""
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
    best_idx = max(0, min(result.best_index - 1, len(result.variants) - 1))
    return result.variants, best_idx, result.best_reason


# ── 터미널 선택 UI ────────────────────────────────────────

_ANGLE_EMOJI = {
    "리스트형":    "📋",
    "Before/After": "🔄",
    "즉시실행":    "⚡",
    "몰랐던사실":  "🔍",
    "공포":        "⚠️",
    "공감":        "🤝",
    "이익":        "💰",
    "사회증거":    "📊",
    # 구버전 호환
    "편의":        "✨",
}


def select_angle(
    topic: str,
    trend_summary: str,
    persona: Persona | None = None,
    auto: bool = False,
) -> SelectedAngle:
    """
    5가지 앵글 생성 → 터미널에 출력 → 사용자 선택.
    auto=True 이면 저장률 가장 높을 앵글 자동 선택 (이전: 항상 1번).
    """
    print("\n  [AngleSelector] 5가지 마케팅 앵글 생성 중...")
    variants, best_idx, best_reason = generate_angles(topic, trend_summary, persona)

    if auto:
        v = variants[best_idx]
        print(f"  [AngleSelector] 자동 선택: {v.angle} — {v.cover_title}")
        if best_reason:
            print(f"  [AngleSelector] 선택 이유: {best_reason}")
        return SelectedAngle(
            angle=v.angle,
            cover_title=v.cover_title,
            hook=v.hook,
            reasoning=v.reasoning,
            expected_save_rate=getattr(v, "expected_save_rate", "보통"),
        )

    # ── 터미널 출력 ───────────────────────────────────────
    print()
    print("  ┌─────────────────────────────────────────────────┐")
    print(f"  │  📰 '{topic}' — 5가지 앵글 (★=추천)")
    print("  ├─────────────────────────────────────────────────┤")

    for i, v in enumerate(variants, 1):
        emoji = _ANGLE_EMOJI.get(v.angle, "▪")
        star = " ★추천" if i - 1 == best_idx else ""
        save = getattr(v, "expected_save_rate", "")
        print(f"  │  [{i}] {emoji} {v.angle}{star}  저장률:{save}")
        print(f"  │      커버: {v.cover_title}")
        print(f"  │      훅:   {v.hook}")
        print(f"  │      이유: {v.reasoning}")
        if i < len(variants):
            print("  │")

    print("  └─────────────────────────────────────────────────┘")
    if best_reason:
        print(f"\n  ★ 추천 이유: {best_reason}")
    print()

    # ── 입력 받기 ─────────────────────────────────────────
    while True:
        try:
            choice = input(f"  사용할 앵글 번호를 선택하세요 (1~{len(variants)}, Enter=추천): ").strip()
            if choice == "":
                idx = best_idx
            else:
                idx = int(choice) - 1
            if 0 <= idx < len(variants):
                v = variants[idx]
                print(f"\n  ✓ 선택됨: [{idx+1}] {v.angle} — {v.cover_title}\n")
                return SelectedAngle(
                    angle=v.angle,
                    cover_title=v.cover_title,
                    hook=v.hook,
                    reasoning=v.reasoning,
                    expected_save_rate=getattr(v, "expected_save_rate", "보통"),
                )
            else:
                print(f"  1~{len(variants)} 사이 숫자를 입력하세요.")
        except (ValueError, KeyboardInterrupt):
            print(f"\n  추천 앵글({best_idx+1}번)로 진행합니다.")
            v = variants[best_idx]
            return SelectedAngle(
                angle=v.angle,
                cover_title=v.cover_title,
                hook=v.hook,
                reasoning=v.reasoning,
                expected_save_rate=getattr(v, "expected_save_rate", "보통"),
            )
