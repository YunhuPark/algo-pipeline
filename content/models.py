from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


# ─── 카드뉴스 콘텐츠 모델 ──────────────────────────────────────────────

class Card(BaseModel):
    card_number: int
    card_type: Literal["cover", "content", "cta"]
    headline: str = Field(..., description="메인 헤드라인 (최대 20자)")
    subheadline: str | None = Field(None, description="서브 헤드라인 (최대 30자)")
    body_text: str = Field(..., description="본문 텍스트 (최대 80자, \\n 줄바꿈 허용)")
    emoji: str | None = Field(None, description="장식용 이모지 1개")
    visual_hint: str = Field(
        default="default",
        description="레이아웃 힌트: default | stat_callout | list_3_items | quote | image_focus",
    )
    accent_text: str | None = Field(None, description="강조 수치나 인용문 (예: '73% 증가')")


class CardNewsSet(BaseModel):
    topic: str
    style_profile: str
    cards: list[Card]
    hashtags: list[str] = Field(..., description="15~20개 해시태그 (#포함)")
    generated_at: datetime = Field(default_factory=datetime.now)
    trend_keywords: list[str] = Field(default_factory=list)


# ─── 스타일 프로필 모델 ────────────────────────────────────────────────

class StyleProfile(BaseModel):
    name: str
    display_name: str
    description: str

    # 배경
    background_type: Literal["solid", "gradient"]
    background_color: str = "#FFFFFF"
    gradient_colors: list[str] | None = None
    gradient_direction: Literal["vertical", "horizontal", "diagonal"] = "vertical"

    # 텍스트 색상
    primary_text_color: str
    secondary_text_color: str
    accent_color: str
    secondary_accent: str | None = None

    # 타이포그래피
    headline_font: str = "NotoSansKR-VF.ttf"
    headline_size: int = 72
    subheadline_size: int = 44
    body_font: str = "NotoSansKR-VF.ttf"
    body_size: int = 38

    # 레이아웃
    layout_padding: int = 80
    line_spacing: float = 1.4
    corner_radius: int = 0

    # 카드 번호
    has_card_number: bool = True
    number_style: Literal["circle", "pill", "plain"] = "circle"
    number_color: str = "#FFFFFF"
    number_bg_color: str | None = None

    # 기타 요소
    divider_color: str | None = None
    logo_position: Literal["top_left", "top_right", "bottom_right", "none"] = "bottom_right"
    logo_size: int = 36


# ─── 조사 결과 모델 ────────────────────────────────────────────────────

class TrendItem(BaseModel):
    keyword: str
    score: int = 0
    is_rising: bool = False


class NewsItem(BaseModel):
    title: str
    summary: str
    url: str = ""
    published_date: str = ""


class Theme(BaseModel):
    name: str
    description: str
    supporting_keywords: list[str] = Field(default_factory=list)
    angle_suggestion: str = ""


# ─── 실행 결과 모델 ────────────────────────────────────────────────────

class RunResult(BaseModel):
    success: bool
    output_paths: list[Path] = Field(default_factory=list)
    topic: str = ""
    num_cards: int = 0
    style_used: str = ""
    summary_message: str = ""
    error: str | None = None
