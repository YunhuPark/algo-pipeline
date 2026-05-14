"""메인 Pillow 카드 렌더링 엔진"""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from content.models import Card, CardNewsSet, StyleProfile
from renderer.effects import (
    draw_gradient_background,
    draw_solid_background,
    hex_to_rgb,
)
from renderer.layout_engine import auto_layout
from styles.style_manager import load_profile

# 폰트 경로 후보 (Windows)
FONT_SEARCH_PATHS = [
    Path("styles/assets/fonts"),
    Path("C:/Windows/Fonts"),
    Path("C:/Users") / Path.home().name / "AppData/Local/Microsoft/Windows/Fonts",
]

FONT_FILENAMES = {
    "regular": ["NotoSansKR-VF.ttf", "NotoSansKR-Regular.ttf", "malgun.ttf", "arial.ttf"],
    "bold": ["NotoSansKR-VF.ttf", "NotoSansKR-Bold.ttf", "malgunbd.ttf", "arialbd.ttf"],
}


def find_font(filename: str) -> Path | None:
    for base in FONT_SEARCH_PATHS:
        candidate = base / filename
        if candidate.exists():
            return candidate
    return None


def resolve_font_path(filename: str, bold: bool = False) -> Path:
    """폰트 파일 경로 탐색. 없으면 기본 폰트 후보 순서대로 시도."""
    direct = find_font(filename)
    if direct:
        return direct

    candidates = FONT_FILENAMES["bold"] if bold else FONT_FILENAMES["regular"]
    for name in candidates:
        p = find_font(name)
        if p:
            return p

    raise FileNotFoundError(
        f"한국어 폰트를 찾을 수 없습니다. '{filename}'을 "
        f"{FONT_SEARCH_PATHS[0]} 에 복사하거나 시스템에 설치하세요."
    )


def load_fonts(profile: StyleProfile) -> dict[str, ImageFont.FreeTypeFont]:
    font_path = resolve_font_path(profile.headline_font, bold=True)
    body_path = resolve_font_path(profile.body_font, bold=False)

    return {
        "headline": ImageFont.truetype(str(font_path), profile.headline_size),
        "subheadline": ImageFont.truetype(str(font_path), profile.subheadline_size),
        "body": ImageFont.truetype(str(body_path), profile.body_size),
        "accent": ImageFont.truetype(str(font_path), profile.subheadline_size),
        "subtext": ImageFont.truetype(str(body_path), max(24, profile.body_size - 8)),
        "number": ImageFont.truetype(str(font_path), 28),
        "tag": ImageFont.truetype(str(body_path), max(22, profile.body_size - 12)),
        "emoji": ImageFont.truetype(str(font_path), profile.headline_size),
    }


def _make_output_dir(base_dir: Path, topic: str) -> Path:
    date_str = datetime.now().strftime("%Y-%m-%d")
    safe_topic = re.sub(r"[^\w가-힣]", "_", topic)[:20]
    folder = base_dir / f"{date_str}_{safe_topic}"

    if folder.exists():
        for i in range(2, 100):
            candidate = base_dir / f"{date_str}_{safe_topic}_{i}"
            if not candidate.exists():
                folder = candidate
                break

    folder.mkdir(parents=True, exist_ok=True)
    return folder


def render_card(
    card: Card,
    profile: StyleProfile,
    fonts: dict,
    handle: str = "",
    hashtags: list[str] | None = None,
    width: int = 1080,
    height: int = 1350,
) -> Image.Image:
    image = Image.new("RGB", (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)

    # 배경
    if profile.background_type == "gradient" and profile.gradient_colors:
        draw_gradient_background(image, profile.gradient_colors[0], profile.gradient_colors[1], profile.gradient_direction)
    else:
        draw_solid_background(image, profile.background_color)

    # 레이아웃
    auto_layout(card, profile, image, draw, fonts, handle=handle, hashtags=hashtags)

    # 핸들 워터마크 (커버·내용 카드 하단)
    if handle and card.card_type != "cta":
        wm_font = fonts["tag"]
        wm_w = int(draw.textlength(handle, font=wm_font))
        pad = profile.layout_padding
        draw.text(
            (width - pad - wm_w, height - pad - wm_font.size),
            handle,
            font=wm_font,
            fill=(*hex_to_rgb(profile.secondary_text_color), 140),  # 반투명
        )

    return image


def render_card_set(
    card_news: CardNewsSet,
    output_base_dir: str | Path = "output",
    handle: str = "",
    width: int = 1080,
    height: int = 1350,
) -> list[Path]:
    base = Path(output_base_dir)
    out_dir = _make_output_dir(base, card_news.topic)

    profile = load_profile(card_news.style_profile)
    fonts = load_fonts(profile)
    hashtags = card_news.hashtags

    output_paths: list[Path] = []

    for card in card_news.cards:
        image = render_card(card, profile, fonts, handle=handle, hashtags=hashtags, width=width, height=height)
        suffix = card.card_type
        filename = f"card_{card.card_number:02d}_{suffix}.png"
        file_path = out_dir / filename
        image.save(str(file_path), "PNG", optimize=True)
        output_paths.append(file_path)

    # metadata.json 저장
    meta_path = out_dir / "metadata.json"
    meta_path.write_text(card_news.model_dump_json(indent=2), encoding="utf-8")

    # hashtags.txt 저장 (복붙용)
    tags_path = out_dir / "hashtags.txt"
    tags_path.write_text(" ".join(card_news.hashtags), encoding="utf-8")

    return output_paths
