"""한국어 텍스트 Word Wrap 및 폰트 유틸"""
from __future__ import annotations

from PIL import ImageDraw, ImageFont


def wrap_text(
    text: str,
    font: ImageFont.FreeTypeFont,
    draw: ImageDraw.ImageDraw,
    max_width: int,
) -> list[str]:
    """
    픽셀 단위 한국어 자동 줄바꿈.
    - \\n: 강제 줄바꿈 우선 처리
    - 영문: 단어 단위 break
    - 한국어: 문자 단위 break
    """
    lines: list[str] = []

    for paragraph in text.split("\n"):
        if not paragraph.strip():
            lines.append("")
            continue

        current = ""
        words = paragraph.split(" ")

        for word in words:
            # 단어 단위로 먼저 시도
            test = (current + " " + word).strip() if current else word
            if draw.textlength(test, font=font) <= max_width:
                current = test
            else:
                # 단어 자체가 너무 길면 문자 단위 break
                if not current:
                    for char in word:
                        test_char = current + char
                        if draw.textlength(test_char, font=font) <= max_width:
                            current = test_char
                        else:
                            if current:
                                lines.append(current)
                            current = char
                else:
                    lines.append(current)
                    current = word

        if current:
            lines.append(current)

    return lines


def fit_font_size(
    text: str,
    font_path: str,
    max_width: int,
    max_height: int,
    draw: ImageDraw.ImageDraw,
    size_max: int = 72,
    size_min: int = 16,
) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """
    주어진 영역(max_width × max_height)에 텍스트가 들어가도록
    폰트 크기를 자동으로 줄여가며 최적 크기를 반환한다.
    """
    for size in range(size_max, size_min - 1, -2):
        font = ImageFont.truetype(font_path, size)
        lines = wrap_text(text, font, draw, max_width)
        _, _, _, line_h = draw.textbbox((0, 0), "가나다", font=font)
        total_h = line_h * len(lines) * 1.35
        if total_h <= max_height:
            return font, lines
    # 최소 크기로 반환
    font = ImageFont.truetype(font_path, size_min)
    return font, wrap_text(text, font, draw, max_width)


def measure_block_height(
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    draw: ImageDraw.ImageDraw,
    line_spacing: float = 1.4,
) -> int:
    if not lines:
        return 0
    _, _, _, line_h = draw.textbbox((0, 0), "가나다", font=font)
    return int(line_h * line_spacing * len(lines))
