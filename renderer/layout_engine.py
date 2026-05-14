"""한국어 텍스트 줄바꿈 및 카드 레이아웃 엔진"""
from __future__ import annotations

from PIL import ImageDraw, ImageFont

from content.models import Card, StyleProfile
from renderer.effects import (
    draw_accent_box,
    draw_circle_badge,
    draw_horizontal_line,
    draw_pill_badge,
    hex_to_rgb,
)

# 금칙처리: 이 문자로 줄 시작 금지
NO_LINE_START = set("）」』】〕〉》。、，．！？‥・…─—〜～」』）]};:,")
# 이 문자로 줄 끝 금지
NO_LINE_END = set("（「『【〔〈《([{「")


def wrap_korean_text(
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
    draw: ImageDraw.ImageDraw,
) -> list[str]:
    """픽셀 단위 한국어 텍스트 줄바꿈 (금칙처리 포함)"""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            lines.append("")
            continue

        current = ""
        for char in paragraph:
            test = current + char
            if draw.textlength(test, font=font) <= max_width:
                current = test
            else:
                # 금칙처리: 현재 줄의 마지막 문자가 줄 끝 금지 문자면 다음 줄로
                while current and current[-1] in NO_LINE_END:
                    char = current[-1] + char
                    current = current[:-1]
                # 금칙처리: 새 문자가 줄 시작 금지 문자면 이전 줄에 붙임
                if char in NO_LINE_START and current:
                    current += char
                    char = ""

                if current:
                    lines.append(current)
                current = char

        if current:
            lines.append(current)

    return lines


def measure_text_block(
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    draw: ImageDraw.ImageDraw,
    line_spacing: float = 1.5,
) -> tuple[int, int]:
    """텍스트 블록의 (최대 너비, 전체 높이) 반환"""
    if not lines:
        return 0, 0
    _, _, _, line_h = draw.textbbox((0, 0), "가나다", font=font)
    total_h = int(line_h * line_spacing * len(lines))
    max_w = max((int(draw.textlength(ln, font=font)) for ln in lines), default=0)
    return max_w, total_h


def draw_text_block(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    x: int,
    y: int,
    font: ImageFont.FreeTypeFont,
    color: str,
    line_spacing: float = 1.5,
    align: str = "left",
    max_width: int | None = None,
) -> int:
    """텍스트 블록 렌더링. 마지막 줄 아래 y 좌표 반환."""
    rgb = hex_to_rgb(color)
    _, _, _, line_h = draw.textbbox((0, 0), "가나다", font=font)
    step = int(line_h * line_spacing)

    current_y = y
    for line in lines:
        if align == "center" and max_width:
            line_w = draw.textlength(line, font=font)
            draw_x = x + (max_width - line_w) // 2
        else:
            draw_x = x
        draw.text((draw_x, current_y), line, font=font, fill=rgb)
        current_y += step

    return current_y


def auto_layout(
    card: Card,
    profile: StyleProfile,
    image,
    draw: ImageDraw.ImageDraw,
    fonts: dict[str, ImageFont.FreeTypeFont],
    handle: str = "",
    hashtags: list[str] | None = None,
) -> None:
    """card_type에 따라 적절한 레이아웃을 적용한다."""
    if card.card_type == "cover":
        _layout_cover(card, profile, image, draw, fonts)
    elif card.card_type == "cta":
        _layout_cta(card, profile, image, draw, fonts, handle, hashtags or [])
    else:
        _layout_content(card, profile, image, draw, fonts)


# ─── 개별 레이아웃 ─────────────────────────────────────────────────────

def _layout_cover(card, profile, image, draw, fonts):
    W, H = image.size
    pad = profile.layout_padding
    text_w = W - pad * 2

    y = int(H * 0.28)

    # 이모지
    if card.emoji:
        emoji_font = fonts.get("emoji") or fonts["headline"]
        draw.text((pad, y), card.emoji, font=emoji_font, fill=hex_to_rgb(profile.accent_color))
        y += int(emoji_font.size * 1.4)

    # 헤드라인
    hl_lines = wrap_korean_text(card.headline, fonts["headline"], text_w, draw)
    y = draw_text_block(draw, hl_lines, pad, y, fonts["headline"], profile.primary_text_color, profile.line_spacing)
    y += 20

    # 구분선
    if profile.divider_color:
        draw_horizontal_line(draw, pad, W - pad, y, profile.divider_color, width=3)
        y += 24

    # 서브헤드라인
    if card.subheadline:
        sub_lines = wrap_korean_text(card.subheadline, fonts["subheadline"], text_w, draw)
        y = draw_text_block(draw, sub_lines, pad, y, fonts["subheadline"], profile.secondary_text_color, profile.line_spacing)
        y += 16

    # 바디 텍스트
    if card.body_text:
        body_lines = wrap_korean_text(card.body_text, fonts["body"], text_w, draw)
        draw_text_block(draw, body_lines, pad, y, fonts["body"], profile.secondary_text_color, profile.line_spacing)


def _layout_content(card, profile, image, draw, fonts):
    W, H = image.size
    pad = profile.layout_padding
    text_w = W - pad * 2

    y = pad

    # 카드 번호
    if profile.has_card_number:
        num_text = str(card.card_number)
        num_font = fonts.get("number") or fonts["body"]
        if profile.number_style == "circle":
            radius = 28
            bg = profile.number_bg_color or profile.accent_color
            draw_circle_badge(draw, (pad + radius, y + radius), radius, bg, num_text, num_font, profile.number_color)
            y += radius * 2 + 24
        elif profile.number_style == "pill":
            bg = profile.number_bg_color or profile.accent_color
            draw_pill_badge(draw, (pad, y), num_text, num_font, bg, profile.number_color)
            y += num_font.size + 36
        else:  # plain
            draw.text((pad, y), num_text, font=num_font, fill=hex_to_rgb(profile.accent_color))
            y += num_font.size + 20

    # 헤드라인
    hl_lines = wrap_korean_text(card.headline, fonts["headline"], text_w, draw)
    y = draw_text_block(draw, hl_lines, pad, y, fonts["headline"], profile.primary_text_color, profile.line_spacing)
    y += 16

    # 구분선
    if profile.divider_color:
        draw_horizontal_line(draw, pad, pad + 120, y, profile.divider_color, width=4)
        y += 28

    # 강조 수치 / accent_text
    if card.accent_text:
        a_font = fonts.get("accent") or fonts["subheadline"]
        a_w = int(draw.textlength(card.accent_text, font=a_font))
        a_h = a_font.size
        box_pad = 20
        box = (pad, y, pad + a_w + box_pad * 2, y + a_h + box_pad)
        draw_accent_box(draw, box, profile.accent_color, radius=10)
        draw.text((pad + box_pad, y + box_pad // 2), card.accent_text, font=a_font, fill=hex_to_rgb(profile.primary_text_color))
        y += a_h + box_pad + 20

    # 바디 텍스트
    if card.body_text:
        body_lines = wrap_korean_text(card.body_text, fonts["body"], text_w, draw)
        draw_text_block(draw, body_lines, pad, y, fonts["body"], profile.secondary_text_color, profile.line_spacing)


def _layout_cta(card, profile, image, draw, fonts, handle, hashtags):
    W, H = image.size
    pad = profile.layout_padding
    text_w = W - pad * 2

    y = int(H * 0.22)

    # 이모지
    if card.emoji:
        emoji_font = fonts.get("emoji") or fonts["headline"]
        draw.text((W // 2 - 30, y), card.emoji, font=emoji_font, fill=hex_to_rgb(profile.accent_color), anchor="mm")
        y += int(emoji_font.size * 1.5)

    # 헤드라인 (중앙 정렬)
    hl_lines = wrap_korean_text(card.headline, fonts["headline"], text_w, draw)
    y = draw_text_block(draw, hl_lines, pad, y, fonts["headline"], profile.primary_text_color, profile.line_spacing, align="center", max_width=text_w)
    y += 16

    # 바디
    if card.body_text:
        body_lines = wrap_korean_text(card.body_text, fonts["body"], text_w, draw)
        y = draw_text_block(draw, body_lines, pad, y, fonts["body"], profile.secondary_text_color, profile.line_spacing, align="center", max_width=text_w)
        y += 32

    # 구분선
    if profile.divider_color:
        draw_horizontal_line(draw, pad + 100, W - pad - 100, y, profile.divider_color, width=2)
        y += 24

    # 핸들
    if handle:
        handle_font = fonts.get("subheadline") or fonts["body"]
        handle_w = draw.textlength(handle, font=handle_font)
        draw.text((W // 2 - handle_w // 2, y), handle, font=handle_font, fill=hex_to_rgb(profile.accent_color))
        y += handle_font.size + 24

    # 해시태그 (작은 폰트)
    if hashtags:
        tag_font = fonts.get("tag") or fonts["body"]
        tag_line_w = text_w
        tag_lines: list[str] = []
        current_line = ""
        for tag in hashtags[:20]:
            test = (current_line + " " + tag).strip()
            if draw.textlength(test, font=tag_font) <= tag_line_w:
                current_line = test
            else:
                if current_line:
                    tag_lines.append(current_line)
                current_line = tag
        if current_line:
            tag_lines.append(current_line)

        # 최대 4줄
        for line in tag_lines[:4]:
            lw = draw.textlength(line, font=tag_font)
            draw.text((W // 2 - lw // 2, y), line, font=tag_font, fill=hex_to_rgb(profile.secondary_text_color))
            y += int(tag_font.size * 1.4)
