"""Claude API를 사용한 카드뉴스 카피 생성"""
from __future__ import annotations

import json

import anthropic

from content.models import Card, CardNewsSet

COPY_SYSTEM_PROMPT = """당신은 한국 인스타그램 카드뉴스 전문 카피라이터입니다.
트렌드 키워드와 주제를 받아 바이럴 카드뉴스 콘텐츠를 생성합니다.

## 카피 작성 규칙
- 헤드라인: 최대 20자, 임팩트 있게, 숫자나 의문형 활용
- 서브헤드라인: 최대 30자
- 바디 텍스트: 최대 80자, \\n으로 줄바꿈 가능
- 이모지: 카드당 1개, 분위기에 맞게
- 말투: {voice_tone}
- 모든 텍스트는 한국어로 작성

## 카드 구조
- 1번 카드: cover (훅/표지) — "이걸 모르면 뒤처진다" 느낌의 강렬한 첫인상
- 2~N-1번 카드: content — 핵심 정보를 카드당 1가지 포인트로
- 마지막 카드: cta — 팔로우 유도, 공유 요청

## 응답 형식
반드시 아래 JSON 스키마를 정확히 따르세요. JSON 외 다른 텍스트 없이 순수 JSON만 반환:
{
  "topic": "string",
  "style_profile": "string",
  "trend_keywords": ["string"],
  "hashtags": ["#string", ...],
  "cards": [
    {
      "card_number": 1,
      "card_type": "cover",
      "headline": "string",
      "subheadline": "string or null",
      "body_text": "string",
      "emoji": "string or null",
      "visual_hint": "default",
      "accent_text": "string or null"
    }
  ]
}
"""


def generate_card_content(
    topic: str,
    themes: list[str],
    style_profile: str,
    num_cards: int,
    brand_voice: str,
    trend_keywords: list[str],
    api_key: str,
) -> CardNewsSet:
    client = anthropic.Anthropic(api_key=api_key)

    system = COPY_SYSTEM_PROMPT.replace("{voice_tone}", brand_voice)

    user_content = f"""주제: {topic}
핵심 테마: {', '.join(themes)}
트렌드 키워드: {', '.join(trend_keywords)}
카드 수: {num_cards}장 (표지 1 + 내용 {num_cards-2} + CTA 1)
스타일: {style_profile}
해시태그: 15~20개, 관련성 높은 것 우선"""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        temperature=0.8,
        system=system,
        messages=[{"role": "user", "content": user_content}],
    )

    raw = response.content[0].text.strip()

    # JSON 블록 추출 (마크다운 코드블록 대비)
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    data = json.loads(raw)
    return CardNewsSet(**data)
