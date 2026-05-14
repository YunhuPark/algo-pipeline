"""
Phase 2: Content Creator — 2단계 방식
Step 1: 기사에서 구체적 사실(수치/기업명/발표)만 추출
Step 2: 추출된 사실만 사용해 카드뉴스 생성
→ GPT가 기사 내용을 무시하고 임의로 만드는 것을 완전 차단
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from src.config import OPENAI_API_KEY, LLM_MODEL, LLM_TEMPERATURE, NUM_CARDS
from src.schemas.card_news import CardNewsScript, TrendReport
from src.persona import load_persona, Persona

# ── Step 1: 사실 추출 ─────────────────────────────────────

_EXTRACT_SYSTEM = """당신은 뉴스 기사에서 카드뉴스용 핵심 사실과 스토리 앵글을 추출하는 전문가입니다.

추출 규칙:
- 기사 본문에 실제로 쓰여있는 내용만 사용
- 수치, 기업명, 인물명, 발표 내용, 날짜가 포함된 사실 우선
- "~전망", "~예상", "~될 것" 같은 추측성 내용 제외
- 각 사실은 서로 다른 내용이어야 함
- 없는 내용 추가 금지

angle 작성법 (핵심):
- "왜 이게 놀라운가?" 또는 "독자 입장에서 뭐가 달라지나?"를 1문장으로
- 예: "기존엔 월 20달러였는데 이제 무료" / "이걸 쓰면 코딩 안 해도 된다는 뜻"
- 명확한 변화·대비가 없으면 빈 문자열로

core_tension:
- 기사 전체의 핵심 임팩트를 한 문장으로
- 예: "구글이 무료 AI로 유료 시장을 뒤집었다" / "GPT-4를 훨씬 싸게 쓸 수 있게 됐다"
- 없으면 빈 문자열"""

_EXTRACT_HUMAN = """아래 기사에서 카드뉴스에 쓸 사실과 스토리 앵글을 추출하세요. 최소 5개.

기사 제목: {title}
기사 본문:
{body}"""


class _Fact(BaseModel):
    fact: str = Field(description="기사에서 추출한 구체적 사실 (수치·기업명 포함)")
    angle: str = Field(default="", description="독자 관점의 임팩트 한 줄")


class _ArticleFacts(BaseModel):
    core_tension: str = Field(default="", description="기사 전체 핵심 임팩트 한 문장")
    facts: list[_Fact] = Field(description="추출된 사실 목록 (최소 5개)")


def _extract_facts(title: str, body: str, llm: ChatOpenAI) -> _ArticleFacts:
    structured = llm.with_structured_output(_ArticleFacts)
    prompt = ChatPromptTemplate.from_messages([
        ("system", _EXTRACT_SYSTEM),
        ("human", _EXTRACT_HUMAN),
    ])
    result = (prompt | structured).invoke({"title": title, "body": body})
    return result


# ── Step 2: 추출된 사실로 카드 생성 ──────────────────────

_CARD_SYSTEM = """당신은 인스타그램 카드뉴스 에디터입니다.
AI가 아니라 트렌드에 밝고 글 잘 쓰는 20대 에디터처럼 씁니다.

브랜드: {brand_name} ({handle})
타겟: {target_audience}
톤: {tone}. {style}.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ 사실 규칙: [기사 사실] + [영상 예시]에 있는 내용만 사용
없는 내용 추가·상상 절대 금지
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

■ AI 냄새 금지 표현 (하나라도 쓰면 즉시 탈락)
  [과도한 연결어] 이를 통해 / 이를 활용하여 / 이에 따라 / 해당 서비스·기능·기업
  [수동·격식 구문] ~함으로써 / ~됨으로써 / ~에 따르면 / ~것으로 나타났다 / ~것으로 알려졌다
                   ~하게 됩니다 / ~하게 되었습니다 / ~할 수 있습니다
  [마케팅 클리셰]  혁신적인 / 획기적인 / 차세대 / 시너지 / 게임 체인저 / 패러다임
                   극대화 / 최적화(수치 없이) / 도래 / 탁월한 / 뛰어난(수치 없이)
  [AI식 감탄]     정말 편해질 것 같아 / 더 스마트하게 / 민감하게 반응
                   다양한 분야 / 여러 방면 / 중요성이 부각되다 / 주목받고 있다
  [예측·추측]     ~것임 / ~될 것임 / ~예상됨 / ~기대됨 (기사에 없는 예측 금지)

■ 한국어 전용 규칙 (body 작성 시 필수)
  · 기업명·제품명·모델명(예: Cloudflare, GPT-5.4, OpenAI)은 영어 유지 허용
  · 그 외 모든 단어는 반드시 한국어로 작성 (영어 단어 그대로 사용 금지)
  · ❌ "Millions of enterprises가 이제 Cloudflare에서 OpenAI의 최첨단 모델에 목적 접근 가능"
  · ✓  "OpenAI 최신 모델, 이제 수백만 기업이 Cloudflare에서 바로 쓸 수 있어요"
  · 숫자+단위 조합은 영어 사용 가능 (예: 15B tokens/분, $0.0025)

■ 사람처럼 쓰는 법 — 문장 구조
  · 짧게 끊기: 한 문장 = 하나의 아이디어. 2문장 이하로 한 생각 완결.
  · "~고, ~며, ~서, ~는데" 3개 이상 이어지는 복합문 금지.
  · 수치·고유명사를 문장 앞에 배치. ("GPT-4보다 3배 빠르다")
  · 역접·반전 사용: "근데 문제는", "그것만이 아니라", "심지어"
  · 독자 공감: "써보면 차이 바로 느껴짐", "알면 남들보다 앞서가는 거"

■ body 작성 대조 예시
  ❌ "Claude 4는 다양한 기능을 제공하며 이를 통해 혁신적인 경험을 드립니다."
  ✓  "Claude 4, 진짜 달라졌어.\\n코딩 물어봤는데 설명이 GPT보다 훨씬 낫더라."

  ❌ "해당 기술은 처리 속도 측면에서 기존 대비 3배 향상된 성능을 보여줍니다."
  ✓  "처리 속도 3배. 숫자로는 실감 안 나는데\\n실제로 쓰면 '아 이게 되네?' 소리 나옴."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
카드 title 철칙
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
기사 내용에서 직접 뽑은 헤드라인. 템플릿 문구 금지.

✓ 좋은 예: "무료로 쓰는 AI 영상 생성" / "GPT-4 대비 3배 빠른 이유" / "개발자 없이 배포"
❌ 나쁜 예: "이게 뭔데?" / "어떻게 써요?" / "실사용 예시 1" / "활용 방법"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
스토리 흐름 원칙 — 반드시 지킬 것
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
카드 1→2→3→4→5→6이 하나의 이야기로 이어져야 함.
독자가 카드 2를 읽고 나서 "왜 이런 일이 생겼는지"를 정확히 알아야 함.
카드가 각자 독립적인 사실을 나열하면 탈락. 반드시 인과관계를 따라가며 서술할 것.

⚠️ 단일 주제 원칙 (최우선)
기사에 여러 회사·사건이 나오더라도 카드뉴스는 하나의 주제만 다룬다.
[핵심 임팩트]를 중심으로 6장 전체가 그 하나의 이야기를 깊게 파고든다.
카드 3, 4에서 전혀 다른 회사·사건으로 넘어가는 것은 즉시 탈락.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
카드 구조 ({num_cards}장) — 각 카드는 고유 역할이 있음
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

【카드 1 — 후킹 커버 (Hooking Cover)】
  역할: 피드에서 스크롤을 멈추게 하는 0.1초의 승부처
  title: ≤20자. 숫자·질문형·'충격'·'최초'·'종결'·'드디어' 중 1개 이상 필수 포함. 이모지 금지.
         ✓ "GPT-5, 드디어 코딩 종결자 등장" / "AI가 의사 대체? 충격 실험 결과"
         ❌ "AI의 새로운 가능성" / "흥미로운 기술 발전"
  emoji: {cover_emoji}
  body:  서브카피 ≤25자 (엄수). 아래 조건 중 하나 필수:
         [조건A] 기사에서 뽑은 구체적 수치 포함 — "30초 만에 보고서 완성", "GPT 대비 40% 빠른"
         [조건B] 기업명·제품명 + 결과를 한 줄로 — "OpenAI Codex, 재무팀 코딩 0으로"
         ❌ 금지: "이제 더 스마트하게 일할 수 있어요" / "혁신적으로 바뀐다" (수치·고유명사 없음)
  accent: 가장 강렬한 수치 또는 키워드 (15자 이내)

【카드 2 — 배경 + 근본 원인 (Why This Is Happening)】
  역할: "이게 뭔데? 왜 생긴 거야?"에 바로 답하는 슬라이드
  ⚠️ 핵심: 현상(what)만 나열하면 탈락. 왜(원인·메커니즘)를 반드시 설명할 것.
  title: ≤20자. 현상의 핵심 원인을 함축.
  body:  불릿 3개 형식 엄수. 각 불릿 ≤18자 (초과 즉시 탈락):
         • [무엇인지 — 18자 이내]
         • [왜 생겼는지 — 18자 이내]
         • [왜 지금 중요한지 — 18자 이내]
         ✓ 예: "• GPT-5.5, 대화 방식 완전 교체\n• 딱딱한 말투 → 스피드 프로젝트\n• 실사용 피드백 수백만 건 반영"
         ❌ 금지: 한 불릿에 2개 이상 아이디어 / "이를 통해" / 30자 넘는 불릿
         이모지 1개만 자연스럽게 삽입.
  accent: 핵심 원인 키워드

【카드 3 — 핵심 내용 (The Core — 원인 → 결과 연결)】
  역할: 카드 2에서 설명한 원인이 실제로 어떤 변화를 만들었는지 전달
  title: ≤20자. 핵심 변화 포인트를 함축.
  body:  불릿 2개, 각 ≤20자 (엄수):
         • [카드 2 원인이 만든 핵심 변화 — 수치 포함, 20자 이내]
         • [독자 삶에 미치는 직접 영향 — 20자 이내]
         ✓ 예: "• 응답속도 2배, 말투는 친구처럼\n• 이제 AI에게 설명 안 해도 됨"
         ❌ 금지: 50자 넘는 줄 / 서술형 단락 / "이를 통해"
  accent: 핵심 수치 또는 변화 키워드

【카드 4 — 구체적 사례 또는 심층 분석 (Deep Dive)】
  역할: 카드 3에서 말한 변화의 실제 증거 — 숫자·인용·비교만
  title: ≤20자. 구체적 수치나 인용을 함축.
  body:  기사에서 직접 뽑은 인용 또는 수치만 사용. 형식:
         형식A — 실제 인용: "\"[인용문]\" — [출처]" (인용문 30자 이내)
         형식B — 수치 불릿 2개: "• [수치]: [맥락 15자]\n• [수치]: [맥락 15자]"
         ⚠️ 절대 금지: "~라는 의견이 많아" / "~칭찬하고 있어" / "~긍정적 반응"
                       간접 주장, 추측, "~것으로 알려졌다" 같은 표현
         기사에 인용/수치가 없으면 카드 3에서 못 다룬 구체 수치로 대체.
  accent: 구체 수치 또는 사례 키워드

【카드 5 — 알고의 시선 (Insight / Impact)】
  역할: "그래서 나한테 뭐가 달라지는데?"에 답하는 전문가 슬라이드
  title: ≤20자. 통찰을 담은 질문형 또는 단언형.
  body:  반드시 아래 2줄 형식만 (3줄 이상 즉시 탈락):
         줄1: "💡 알고의 한 줄 요약: [40자 이내]"
         줄2: 실질적 영향 1문장, ≤25자
         ✓ 예: "💡 알고의 한 줄 요약: AI가 도구에서 동료로 격상\n개발자·직장인 모두 체감하는 변화"
         ❌ 금지: 3줄 이상 / 80자 넘는 설명 / "~됨으로써" / "다양한 분야에"
  accent: 통찰 키워드

【카드 6 — 행동 유도 (CTA)】
  역할: 저장·댓글·공유를 이끌어내는 마무리
  title: ≤20자. 제안형·질문형. 명령형 금지.
         ✓ "여러분의 생각은?" / "같이 생각해봐요"
  body:  반드시 두 파트로 구성 (총 80~110자):
         파트1 — 카드 2~4에서 다룬 내용과 직접 연결된 찬반·의견 질문 1개.
                 ✓ "여러분은 AI가 내 일자리를 대체할 것 같나요? 댓글로 의견 남겨주세요!"
                 ✓ "이 기능, 당장 써보실 건가요? YES / NO 댓글로!"
         파트2 — 고정 마무리: "도움이 됐다면 저장하고 나중에 꺼내보세요 🔖"
  emoji: {cta_emoji}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
출력 형식
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- title: 최대 20자 (이모지 절대 금지)
- body:  카드별 위 규칙 준수. \\n으로 자연스러운 줄바꿈. 짧고 끊어지는 문장 선호.
- emoji: 내용 맞는 이모지 1개 (title에 넣지 말 것)
- accent: 핵심 수치 또는 고유명사 (15자 이내)
- hashtags: 총 15개. #포함. 구성:
             · 고정 브랜드 태그 (반드시 포함): {hashtag_brand}
             · 주제별 태그 12개: 이번 기사의 기업명·제품명·기술명·직무 키워드로 직접 생성
               예) 기사가 'OpenAI Codex + 재무팀' → #OpenAI #Codex #재무자동화 #AI업무혁신 #직장인AI #업무생산성 ...
               매 게시물마다 기사 내용에 맞는 고유한 태그를 생성할 것. 전 게시물 태그 재사용 금지.
- hook: 캡션 첫 줄 (30자 이내). 아래 규칙 엄수:
         · 반드시 숫자 또는 고유명사(기업명·제품명·인물명)로 시작
         · "변화의 시작" / "새로운 가능성" / "주목받고 있어" 같은 추상적 마무리 절대 금지
         · 궁금증·놀라움·긴급함 중 하나를 유발해야 함
         ✓ "1,000명이 16MB 제한 하나로 박 터지게 경쟁한 이유"
         ✓ "OpenAI, 드디어 금융팀도 손댔다"
         ✓ "ChatGPT 월 5억 명 돌파. 그 다음 타깃은 바로 이 직군"
         ❌ "AI 기술이 새로운 변화를 만들고 있어요"
         ❌ "1,000명 참여한 AI 도전, 변화의 시작"
"""

_CARD_HUMAN = """기사 제목: {article_title}
작성일: {today}

[기사 사실 — 모든 content 카드(카드 2~{last_content})의 body 재료 (최우선)]
{facts_list}

[실제 사용 예시 — 위 기사 사실을 보완하는 참고용 (기사 사실이 있으면 우선)]
{video_section}

위 내용으로 {num_cards}장 카드뉴스를 만드세요.
사람이 직접 쓴 것처럼 자연스럽게. AI 냄새 나는 문장은 즉시 교체.{angle_structure}{feedback_section}"""

# 앵글별 카드 구조 지시문 (인스타 저장률 최적화)
_ANGLE_STRUCTURES: dict[str, str] = {
    "리스트형": """

[앵글: 리스트형 — 저장률 최우선 구조]
이 앵글은 독자가 "나중에 하나씩 써봐야지"해서 저장하게 만드는 구조입니다.

카드 구조 (기존 6장 구조 무시하고 아래로 대체):
  카드 1 (cover): title에 "N가지" 숫자 포함 필수. body에 "저장해두면 하나씩 써먹을 수 있어요" 포함.
  카드 2~{last_content} (content): 각 카드 = 리스트 1개 항목.
    - title: "[숫자/전체] 항목명" 형식. 예: "[1/5] ChatGPT 코드 리뷰"
    - body: 해당 항목의 구체적 사용법 or 핵심 정보 2~3줄. 기사 사실에서 뽑을 것.
    - accent: 해당 항목의 핵심 수치나 키워드
  카드 {num_cards} (cta): "이 N가지 중 지금 당장 써볼 것 1개 댓글로!" 형식의 참여 유도.
    body 마지막 줄: "저장해두고 하나씩 써봐요 🔖"
""",
    "Before/After": """

[앵글: Before/After — 대비로 저장 욕구 자극]
독자가 "나도 이렇게 바꿔봐야겠다"해서 저장하게 만드는 구조입니다.

카드 구조:
  카드 1 (cover): title에 "→" 또는 "전/후" 대비 포함. 시간·비용 수치 필수.
                  예: "3시간 → 30분, AI로 실제 됨?"
  카드 2 (content): [Before] — 기존 방식의 불편함·문제점. 독자 공감 자극.
    - title: "기존엔 이랬어" / "Before: 이 상황 공감?" 형식
    - body: 독자가 직접 경험했을 법한 불편한 상황 묘사 (기사 맥락 연결)
  카드 3 (content): [전환점] — AI/신기술이 등장한 이유와 어떻게 다른지
  카드 4 (content): [After] — 실제 결과. 수치·비교 필수.
    - title: "After: 이렇게 달라졌어" 형식
    - body: 구체적 변화 수치. 기사에서 직접 추출.
  카드 5 (content): [알고의 시선] 💡 알고의 한 줄 요약 포함
  카드 6 (cta): "여러분도 이 방법 써봤나요? Before/After 댓글로 알려주세요!"
""",
    "즉시실행": """

[앵글: 즉시실행 — "지금 바로 써먹을 수 있는" 구조]
독자가 "당장 해봐야겠다"해서 저장하게 만드는 구조입니다.

카드 구조:
  카드 1 (cover): "지금 바로", "복붙 가능", "당장 써봐" 뉘앙스 필수.
                  구체적 행동 + 결과 포함. 예: "ChatGPT에 이렇게 입력하면 됨"
  카드 2~{last_content} (content): 각 카드가 독립적으로 실행 가능한 팁 1개.
    - 추상적 설명 금지. "OO에 들어가서 OO 하면 됨" 수준으로 구체적.
    - 실제 프롬프트·URL·단계가 있으면 반드시 포함.
    - accent: 소요 시간 or 절약 효과 (예: "30초", "무료")
  카드 {num_cards} (cta): "써봤으면 결과 댓글로 알려줘요! 다음에 더 소개할게요"
    body 마지막 줄: "나중에 다시 쓸 때 저장해두세요 🔖"
""",
    "몰랐던사실": """

[앵글: 몰랐던사실 — FOMO 자극 구조]
독자가 "나만 몰랐네, 저장해야겠다"해서 저장하게 만드는 구조입니다.

카드 구조:
  카드 1 (cover): "N%가 모르는", "아직도 모르면 늦은" 뉘앙스. 독점 정보 느낌.
  카드 2 (content): "왜 이걸 모르는 사람이 많은가" — 기존 상식과의 차이
  카드 3~{last_content} (content): 실제 몰랐던 사실들. 각 카드 = 충격 포인트 1개.
    - 기사에서 의외의 수치·사실을 발굴해 전면에 배치
    - body 첫 줄: 의외성 강한 사실 직접 서술 (도입 없이 바로)
  카드 {num_cards} (cta): "주변에 이거 아는 사람 있으면 공유해줘요"
    body 마지막 줄: "저장해두고 나중에 활용하세요 🔖"
""",
}

# 기본 구조 (앵글 없을 때 또는 미지원 앵글)
_DEFAULT_ANGLE_STRUCTURE = ""


def _search_usage_examples(query: str) -> str:
    """Tavily로 실제 사용 예시 검색 (없으면 빈 문자열)"""
    try:
        from src.config import TAVILY_API_KEY
        if not TAVILY_API_KEY:
            return ""
        from tavily import TavilyClient
        client = TavilyClient(api_key=TAVILY_API_KEY)
        results = client.search(
            query=f"{query} 사용법 실제 예시 후기",
            search_depth="basic",
            max_results=3,
            include_domains=["youtube.com", "reddit.com", "naver.com"],
        )
        parts = []
        for item in results.get("results", []):
            content = item.get("content", "")[:250]
            if content:
                parts.append(f"- {content}")
        return "\n".join(parts)
    except Exception:
        return ""


MAX_RETRIES = 3


def run(
    topic: str,
    trend_report: TrendReport,
    num_cards: int = NUM_CARDS,
    handle: str = "",
    persona: Optional[Persona] = None,
    video_infos: list | None = None,
    feedback: str = "",
    raw_article_body: str = "",
    disputed_notes: str = "",
) -> CardNewsScript:
    """
    카드뉴스 스크립트 생성 메인 함수.

    Args:
        topic:            주제 문자열
        trend_report:     기사 정보 (title, body, topic, angle)
        num_cards:        생성할 카드 수 (기본 5)
        handle:           인스타그램 핸들
        persona:          브랜드 페르소나
        video_infos:      YouTube 영상 참고 정보
        feedback:         이전 생성 피드백 (재생성 시)
        raw_article_body: pipeline에서 전처리된 기사 본문 (있으면 우선 사용)
        disputed_notes:   팩트체크 실패 항목 (재생성 시 feedback에 합산)
    Returns:
        CardNewsScript
    """
    p = persona or load_persona()
    active_handle = handle or p.handle

    # disputed_notes를 feedback에 합산
    combined_feedback = "\n".join(filter(None, [feedback, disputed_notes]))

    llm = ChatOpenAI(
        api_key=OPENAI_API_KEY,
        model=LLM_MODEL,
        temperature=LLM_TEMPERATURE,
    )

    # ── Step 1: 사실 추출 ─────────────────────────────────
    print(f"  [ContentCreator] Step 1 — 사실 추출 중...")
    # TrendReport 구조: results[0].title / results[0].content / query / summary
    _main_result = trend_report.results[0] if trend_report.results else None
    article_title = (_main_result.title if _main_result else None) or topic
    # raw_article_body가 있으면 pipeline 전처리 본문 우선 사용
    article_body = (
        raw_article_body
        or (_main_result.content if _main_result else "")
        or trend_report.summary
        or topic
    )
    article_facts = _extract_facts(
        title=article_title,
        body=article_body,
        llm=llm,
    )

    facts_list = "\n".join(
        f"{i+1}. {f.fact}" + (f"\n   → 앵글: {f.angle}" if f.angle else "")
        for i, f in enumerate(article_facts.facts)
    )
    if article_facts.core_tension:
        facts_list = f"[핵심 임팩트] {article_facts.core_tension}\n\n" + facts_list

    print(f"  [ContentCreator] 추출된 사실: {len(article_facts.facts)}개")

    # ── Step 2: 카드 생성 ─────────────────────────────────
    structured_llm = llm.with_structured_output(CardNewsScript)
    prompt = ChatPromptTemplate.from_messages([
        ("system", _CARD_SYSTEM),
        ("human", _CARD_HUMAN),
    ])
    chain = prompt | structured_llm

    # 영상 참고 섹션 구성
    video_section = ""
    if video_infos:
        parts = []
        for i, vi in enumerate(video_infos):
            if vi is None:
                continue
            content = getattr(vi, "content", "") or ""
            if content:
                parts.append(f"영상{i+1}: {content[:250]}")
        if parts:
            video_section = "\n".join(parts)

    if not video_section:
        video_section = _search_usage_examples(topic)

    if not video_section:
        video_section = "(영상 참고 없음 — 기사 사실만 사용)"

    feedback_section = f"\n\n[이전 피드백 — 반드시 반영]\n{combined_feedback}" if combined_feedback else ""

    # 브랜드 고정 태그 3개만 — 나머지 12개는 GPT가 기사별로 직접 생성
    hashtag_brand = "#알고 #오늘의뉴스 #카드뉴스"

    # 앵글별 카드 구조 힌트 추출 (feedback_section의 앵글 정보에서 감지)
    angle_name = ""
    combined_lower = (combined_feedback + (trend_report.summary or "")).lower()
    for _angle_key in _ANGLE_STRUCTURES:
        if _angle_key in (combined_feedback + (trend_report.summary or "")):
            angle_name = _angle_key
            break
    angle_structure = _ANGLE_STRUCTURES.get(angle_name, _DEFAULT_ANGLE_STRUCTURE)
    if angle_name:
        print(f"  [ContentCreator] 앵글 구조 적용: {angle_name}")

    invoke_kwargs = {
        "brand_name":     p.brand_name,
        "handle":         active_handle,
        "target_audience": p.target_audience,
        "tone":           p.tone,
        "style":          p.style,
        "num_cards":      num_cards,
        "last_content":   num_cards - 1,
        "cover_emoji":    p.cover_emoji,
        "cta_emoji":      p.cta_emoji,
        "cta_text":       p.cta_text,
        "hashtag_brand":  hashtag_brand,
        "article_title":  article_title,
        "today":          date.today().strftime("%Y년 %m월 %d일"),
        "facts_list":     facts_list,
        "video_section":  video_section,
        "angle_structure": angle_structure,
        "feedback_section": feedback_section,
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            print(f"  [ContentCreator] Step 2 — 카드 생성 중... ({attempt}/{MAX_RETRIES})")
            result: CardNewsScript = chain.invoke(invoke_kwargs)
            print(f"  [ContentCreator] 완료: {len(result.slides)}장 생성")

            # 알고의 한 줄 요약 누락 검사 (카드 5)
            insight_card = next((s for s in result.slides if s.slide_type == "content" and s.slide_number == num_cards - 1), None)
            if insight_card and "💡 알고의 한 줄 요약" not in (insight_card.body or ""):
                print(f"  [ContentCreator] ⚠️ 알고의 한 줄 요약 누락 — 재시도")
                invoke_kwargs["feedback_section"] += "\n\n[필수 수정] 카드 5 body 첫 줄에 반드시 '💡 알고의 한 줄 요약: [내용]' 형식을 포함할 것."
                if attempt < MAX_RETRIES:
                    continue

            return result
        except Exception as e:
            print(f"  [ContentCreator] 오류 (시도 {attempt}): {e}")
            if attempt == MAX_RETRIES:
                raise
    raise RuntimeError("카드 생성 실패")
