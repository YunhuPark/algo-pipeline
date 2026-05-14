from __future__ import annotations

import json
from pathlib import Path

from content.models import StyleProfile

PROFILES_DIR = Path(__file__).parent / "profiles"

# 주제 키워드 → 스타일 매핑
TOPIC_STYLE_MAP: dict[str, str] = {
    # AI / 테크
    "ai": "bold_gradient",
    "인공지능": "bold_gradient",
    "gpt": "bold_gradient",
    "chatgpt": "bold_gradient",
    "llm": "bold_gradient",
    "딥러닝": "bold_gradient",
    "머신러닝": "bold_gradient",
    "생성ai": "bold_gradient",
    "tech": "dark_modern",
    "테크": "dark_modern",
    "it": "dark_modern",
    "보안": "dark_modern",
    "사이버": "dark_modern",
    "코딩": "dark_modern",
    "개발": "dark_modern",
    "스타트업": "dark_modern",
    # 사회 / 환경
    "환경": "editorial",
    "사회": "editorial",
    "정치": "editorial",
    "시사": "editorial",
    "뉴스": "editorial",
    "기후": "editorial",
    "역사": "editorial",
    # 경제 / 비즈니스
    "경제": "minimalist",
    "비즈니스": "minimalist",
    "금융": "minimalist",
    "주식": "minimalist",
    "부동산": "minimalist",
    "창업": "minimalist",
    # 라이프 / 뷰티 / 음식
    "라이프": "pastel_soft",
    "뷰티": "pastel_soft",
    "음식": "pastel_soft",
    "여행": "pastel_soft",
    "건강": "pastel_soft",
    "다이어트": "pastel_soft",
    "패션": "pastel_soft",
    "육아": "pastel_soft",
}


def load_profile(name: str) -> StyleProfile:
    path = PROFILES_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"스타일 프로필을 찾을 수 없습니다: {name}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return StyleProfile(**data)


def select_style(topic: str, mood: str = "auto", style_override: str | None = None) -> StyleProfile:
    if style_override:
        return load_profile(style_override)

    topic_lower = topic.lower().replace(" ", "")
    for keyword, style_name in TOPIC_STYLE_MAP.items():
        if keyword in topic_lower:
            return load_profile(style_name)

    # mood 기반 폴백
    mood_map = {
        "professional": "minimalist",
        "casual": "pastel_soft",
        "energetic": "bold_gradient",
        "soft": "pastel_soft",
        "dark": "dark_modern",
    }
    if mood in mood_map:
        return load_profile(mood_map[mood])

    return load_profile("bold_gradient")


def list_available() -> list[str]:
    return [p.stem for p in PROFILES_DIR.glob("*.json")]
