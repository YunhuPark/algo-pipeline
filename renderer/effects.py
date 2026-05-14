"""시각 효과: 그라디언트, 박스, 오버레이 등"""
from __future__ import annotations

from PIL import Image, ImageDraw


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def draw_gradient_background(
    image: Image.Image,
    color1: str,
    color2: str,
    direction: str = "vertical",
) -> None:
    """두 색상 사이의 선형 그라디언트를 배경에 그린다."""
    w, h = image.size
    r1, g1, b1 = hex_to_rgb(color1)
    r2, g2, b2 = hex_to_rgb(color2)
    pixels = image.load()

    for y in range(h):
        for x in range(w):
            if direction == "vertical":
                t = y / h
            elif direction == "horizontal":
                t = x / w
            else:  # diagonal
                t = (x / w + y / h) / 2

            r = int(r1 + (r2 - r1) * t)
            g = int(g1 + (g2 - g1) * t)
            b = int(b1 + (b2 - b1) * t)
            pixels[x, y] = (r, g, b)


def draw_solid_background(image: Image.Image, color: str) -> None:
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, image.width, image.height], fill=hex_to_rgb(color))


def draw_rounded_rect(
    draw: ImageDraw.ImageDraw,
    bbox: tuple[int, int, int, int],
    radius: int,
    fill: str | tuple,
    outline: str | None = None,
    outline_width: int = 2,
) -> None:
    fill_rgb = hex_to_rgb(fill) if isinstance(fill, str) else fill
    outline_rgb = hex_to_rgb(outline) if isinstance(outline, str) else outline

    draw.rounded_rectangle(bbox, radius=radius, fill=fill_rgb, outline=outline_rgb, width=outline_width)


def draw_accent_box(
    draw: ImageDraw.ImageDraw,
    bbox: tuple[int, int, int, int],
    accent_color: str,
    radius: int = 12,
) -> None:
    """강조 배경 박스 (반투명 느낌 — 약간 밝은 악센트 색상)"""
    r, g, b = hex_to_rgb(accent_color)
    # 20% 밝게
    fill = (min(r + 40, 255), min(g + 20, 255), min(b + 40, 255))
    draw_rounded_rect(draw, bbox, radius=radius, fill=fill)
    draw_rounded_rect(draw, bbox, radius=radius, fill=accent_color, outline=accent_color)


def draw_horizontal_line(
    draw: ImageDraw.ImageDraw,
    x1: int,
    x2: int,
    y: int,
    color: str,
    width: int = 3,
) -> None:
    rgb = hex_to_rgb(color)
    draw.line([(x1, y), (x2, y)], fill=rgb, width=width)


def draw_circle_badge(
    draw: ImageDraw.ImageDraw,
    center: tuple[int, int],
    radius: int,
    fill: str,
    text: str,
    font,
    text_color: str,
) -> None:
    """카드 번호용 원형 배지"""
    cx, cy = center
    bbox = (cx - radius, cy - radius, cx + radius, cy + radius)
    draw_rounded_rect(draw, bbox, radius=radius, fill=fill)

    text_rgb = hex_to_rgb(text_color)
    draw.text((cx, cy), text, font=font, fill=text_rgb, anchor="mm")


def draw_pill_badge(
    draw: ImageDraw.ImageDraw,
    position: tuple[int, int],
    text: str,
    font,
    fill: str,
    text_color: str,
    padding: tuple[int, int] = (20, 10),
) -> None:
    """알약 모양 배지"""
    px, py = padding
    bbox_width, bbox_height = draw.textlength(text, font=font), font.size
    x, y = position
    bbox = (x, y, x + int(bbox_width) + px * 2, y + int(bbox_height) + py * 2)
    draw_rounded_rect(draw, bbox, radius=20, fill=fill)

    text_rgb = hex_to_rgb(text_color)
    draw.text((x + px, y + py), text, font=font, fill=text_rgb)
