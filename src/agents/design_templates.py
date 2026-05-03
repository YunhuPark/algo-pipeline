"""
Design Templates — 카드뉴스 디자인 템플릿 정의 모듈
────────────────────────────────────────────────────────
5가지 템플릿:
  dark      — 다크 배경, 보라/시안 포인트 (기본값)
  light     — 밝은 배경, 남색/주황 포인트
  bold      — 다크 + 레드/옐로우, 강렬한 포인트
  minimal   — 거의 투명 오버레이, 모노크롬
  gradient  — 보라→핑크 그라디언트 느낌

사용법:
  from src.agents.design_templates import get_template, list_templates, get_template_for_topic
"""
from __future__ import annotations

# ── 템플릿 정의 ──────────────────────────────────────────

TEMPLATES: dict[str, dict] = {
    "dark": {
        "name": "다크",
        "description": "다크 배경에 보라/시안 포인트. 기술·IT 콘텐츠에 최적.",
        "overlay_alpha": 175,
        "overlay_color": (0, 0, 0),
        "accent": (91, 79, 232),          # #5B4FE8 — primary (보라)
        "accent2": (0, 229, 255),         # #00E5FF — secondary (시안)
        "text_primary": (255, 255, 255),
        "text_secondary": (200, 200, 200),
        "badge_bg": (91, 79, 232),
        "badge_text": (255, 255, 255),
        "tag_bg": (40, 40, 60),
        "tag_text": (0, 229, 255),
        "divider": (91, 79, 232),
        "accent_box_bg": (20, 10, 40, 180),
        "tag_color": (150, 150, 180),
        "title_font_size": 72,
        "body_font_size": 36,
        "accent_font_size": 38,
    },
    "light": {
        "name": "라이트",
        "description": "밝은 배경에 남색/주황 포인트. 라이프스타일·문화 콘텐츠에 어울림.",
        "overlay_alpha": 80,
        "overlay_color": (240, 240, 250),
        "accent": (26, 26, 46),           # #1A1A2E — primary (딥 네이비)
        "accent2": (255, 107, 53),        # #FF6B35 — secondary (주황)
        "text_primary": (20, 20, 40),
        "text_secondary": (70, 70, 90),
        "badge_bg": (26, 26, 46),
        "badge_text": (255, 255, 255),
        "tag_bg": (230, 230, 245),
        "tag_text": (255, 107, 53),
        "divider": (255, 107, 53),
        "accent_box_bg": (230, 230, 250, 200),
        "tag_color": (100, 100, 130),
        "title_font_size": 68,
        "body_font_size": 36,
        "accent_font_size": 36,
    },
    "bold": {
        "name": "볼드",
        "description": "강렬한 레드/옐로우 포인트. 경제·주식·이슈 콘텐츠에 최적.",
        "overlay_alpha": 185,
        "overlay_color": (5, 0, 0),
        "accent": (255, 45, 85),          # #FF2D55 — primary (레드)
        "accent2": (255, 214, 10),        # #FFD60A — secondary (옐로우)
        "text_primary": (255, 255, 255),
        "text_secondary": (220, 210, 210),
        "badge_bg": (255, 45, 85),
        "badge_text": (255, 255, 255),
        "tag_bg": (50, 10, 20),
        "tag_text": (255, 214, 10),
        "divider": (255, 45, 85),
        "accent_box_bg": (40, 5, 10, 200),
        "tag_color": (180, 150, 150),
        "title_font_size": 76,            # 굵고 크게
        "body_font_size": 38,
        "accent_font_size": 44,           # 숫자/강조에 큰 폰트
    },
    "minimal": {
        "name": "미니멀",
        "description": "거의 투명한 오버레이, 모노크롬 팔레트. 감성·철학 콘텐츠에 어울림.",
        "overlay_alpha": 50,
        "overlay_color": (10, 10, 10),
        "accent": (34, 34, 34),           # #222222 — primary (다크 그레이)
        "accent2": (102, 102, 102),       # #666666 — secondary (미드 그레이)
        "text_primary": (240, 240, 240),
        "text_secondary": (190, 190, 190),
        "badge_bg": (34, 34, 34),
        "badge_text": (240, 240, 240),
        "tag_bg": (30, 30, 30),
        "tag_text": (160, 160, 160),
        "divider": (102, 102, 102),
        "accent_box_bg": (20, 20, 20, 140),
        "tag_color": (140, 140, 140),
        "title_font_size": 66,
        "body_font_size": 34,
        "accent_font_size": 36,
    },
    "gradient": {
        "name": "그라디언트",
        "description": "보라→핑크 그라디언트 느낌. 트렌드·뷰티·엔터테인먼트 콘텐츠에 최적.",
        "overlay_alpha": 160,
        "overlay_color": (20, 5, 30),
        "accent": (139, 92, 246),         # #8B5CF6 — primary (바이올렛)
        "accent2": (236, 72, 153),        # #EC4899 — secondary (핑크)
        "text_primary": (255, 255, 255),
        "text_secondary": (220, 200, 230),
        "badge_bg": (139, 92, 246),
        "badge_text": (255, 255, 255),
        "tag_bg": (40, 10, 50),
        "tag_text": (236, 72, 153),
        "divider": (139, 92, 246),
        "accent_box_bg": (30, 5, 40, 180),
        "tag_color": (180, 150, 200),
        "title_font_size": 70,
        "body_font_size": 36,
        "accent_font_size": 40,
    },
}

# ── 주제 키워드 → 템플릿 매핑 ──────────────────────────

_TOPIC_KEYWORD_MAP: list[tuple[list[str], str]] = [
    # (키워드 목록, 템플릿 이름) — 우선순위 높은 항목을 앞에
    (["AI", "인공지능", "기술", "IT", "스타트업", "빅테크", "반도체", "로봇", "우주", "SW", "GPT", "LLM"], "dark"),
    (["경제", "주식", "주가", "금융", "투자", "코인", "부동산", "재테크", "ETF", "S&P", "나스닥", "증시", "환율", "금리"], "bold"),
    (["라이프스타일", "건강", "음식", "여행", "패션", "뷰티", "인테리어", "취미", "문화"], "light"),
    (["트렌드", "SNS", "유튜브", "인플루언서", "엔터", "연예", "K-pop", "게임", "콘텐츠"], "gradient"),
    (["철학", "심리", "명언", "감성", "에세이", "독서", "책", "자기계발", "마음"], "minimal"),
]


# ── 공개 API ──────────────────────────────────────────────

def get_template(name: str) -> dict:
    """
    템플릿 이름으로 템플릿 dict 반환.
    존재하지 않는 이름이면 "dark" 반환.

    Args:
        name: 템플릿 이름 ("dark", "light", "bold", "minimal", "gradient")

    Returns:
        템플릿 딕셔너리
    """
    return TEMPLATES.get(name, TEMPLATES["dark"])


def list_templates() -> list[str]:
    """
    사용 가능한 템플릿 이름 목록 반환.

    Returns:
        ["dark", "light", "bold", "minimal", "gradient"]
    """
    return list(TEMPLATES.keys())


def get_template_for_topic(topic: str) -> str:
    """
    주제 키워드를 분석해 가장 적합한 템플릿 이름 자동 선택.
    1차: 키워드 매핑 테이블
    2차: 매칭 없으면 GPT-4o-mini 감성 분석 fallback

    매핑 기준:
      - 경제/주식/금융  → "bold"
      - 라이프스타일   → "light"
      - 트렌드/엔터     → "gradient"
      - 감성/철학       → "minimal"
      - AI/기술/IT      → "dark" (기본)

    Args:
        topic: 카드뉴스 주제 문자열

    Returns:
        템플릿 이름 문자열
    """
    topic_lower = topic.lower()

    for keywords, template_name in _TOPIC_KEYWORD_MAP:
        for kw in keywords:
            if kw.lower() in topic_lower:
                return template_name

    # 1차 매핑 실패 → GPT-4o-mini 감성 분석으로 선택
    return _gpt_select_template(topic)


def _gpt_select_template(topic: str) -> str:
    """키워드 매핑 실패 시 GPT-4o-mini로 주제 감성/분위기 분석해 템플릿 선택"""
    try:
        from openai import OpenAI
        import os

        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            return "dark"

        client = OpenAI(api_key=api_key)
        template_desc = "\n".join(
            f"- {k}: {v['description']}"
            for k, v in TEMPLATES.items()
        )
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=10,
            temperature=0,
            messages=[{
                "role": "user",
                "content": (
                    f"다음 카드뉴스 주제에 가장 어울리는 디자인 템플릿을 골라주세요.\n\n"
                    f"주제: {topic}\n\n"
                    f"템플릿 목록:\n{template_desc}\n\n"
                    f"위 목록 중 하나의 템플릿 ID만 출력하세요 (예: dark)."
                )
            }]
        )
        chosen = resp.choices[0].message.content.strip().lower()
        if chosen in TEMPLATES:
            print(f"  [Templates] GPT 감성 분석 → '{chosen}' 템플릿 선택")
            return chosen
    except Exception:
        pass
    return "dark"


def get_template_info() -> list[dict]:
    """
    모든 템플릿의 이름·설명 요약 반환 (CLI/로그 출력용).

    Returns:
        [{"id": "dark", "name": "다크", "description": "..."}, ...]
    """
    return [
        {
            "id": key,
            "name": tmpl["name"],
            "description": tmpl["description"],
            "accent_hex": "#{:02X}{:02X}{:02X}".format(*tmpl["accent"]),
            "accent2_hex": "#{:02X}{:02X}{:02X}".format(*tmpl["accent2"]),
        }
        for key, tmpl in TEMPLATES.items()
    ]


if __name__ == "__main__":
    print("== 사용 가능한 템플릿 ==")
    for info in get_template_info():
        print(f"  [{info['id']}] {info['name']} — {info['description']}")
        print(f"         accent: {info['accent_hex']}  accent2: {info['accent2_hex']}")

    print("\n== 주제 자동 매핑 테스트 ==")
    test_topics = [
        "삼성전자 주식 전망",
        "AI 반도체 전쟁",
        "SNS 트렌드 2025",
        "미니멀 라이프스타일",
        "K-pop 해외 진출",
        "인생 명언 10가지",
    ]
    for t in test_topics:
        tmpl = get_template_for_topic(t)
        print(f"  '{t}' → {tmpl} ({TEMPLATES[tmpl]['name']})")
