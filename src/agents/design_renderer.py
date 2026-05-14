"""
Phase 3: Design Renderer — 중앙 정렬 / 이모지 수정 / 섹션 레이블 제거
────────────────────────────────────────────────────────────────────
레이아웃:
  커버   : 배경 + 강한 하단 그라디언트 / 중앙 하단에 큰 타이틀 + 서브텍스트
  컨텐츠 : 배지 / 중앙 타이틀 / 구분선 / 본문
  Split  : 상단 45% 썸네일 / 하단 텍스트 중앙 정렬
  CTA    : 이모지 + 타이틀 + 본문 + 핸들 + 해시태그 (전체 중앙)
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

from src.config import FONTS_DIR, OUTPUT_DIR
from src.schemas.card_news import CardNewsScript, Slide
from src.utils.text_utils import wrap_text
from src.persona import Persona, load_persona

W, H = 1080, 1350
PAD = 96


# ── 색상/스타일 ────────────────────────────────────────────

def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))  # type: ignore


def _build_style(persona: Persona) -> dict:
    acc  = _hex_to_rgb(persona.primary_color)
    acc2 = _hex_to_rgb(persona.accent_color)
    return {
        "overlay_alpha":  persona.overlay_darkness,
        "overlay_color":  (4, 4, 12),
        "text_primary":   (255, 255, 255),
        "text_secondary": (215, 215, 235),
        "text_muted":     (155, 155, 185),
        "accent":         acc,
        "accent2":        acc2,
        "divider":        acc,
        "tag_color":      (140, 140, 175),
    }


STYLE = _build_style(load_persona())


# ── 폰트 ───────────────────────────────────────────────────

_FONT_CANDIDATES = [
    FONTS_DIR,
    Path("C:/Windows/Fonts"),
    Path("C:/Users") / Path.home().name / "AppData/Local/Microsoft/Windows/Fonts",
]
_FONT_FILES = {
    "bold":    ["NotoSansKR-VF.ttf", "NotoSansKR-Bold.ttf", "malgunbd.ttf", "arialbd.ttf"],
    "regular": ["NotoSansKR-VF.ttf", "NotoSansKR-Regular.ttf", "malgun.ttf", "arial.ttf"],
}


def _find_font(kind: str = "bold") -> str:
    for base in _FONT_CANDIDATES:
        for name in _FONT_FILES[kind]:
            p = base / name
            if p.exists():
                return str(p)
    raise FileNotFoundError("한국어 폰트를 찾을 수 없습니다.")


def _find_emoji_font() -> str | None:
    for p in [Path("C:/Windows/Fonts/seguiemj.ttf"), Path("C:/Windows/Fonts/seguisym.ttf")]:
        if p.exists():
            return str(p)
    return None


def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_find_font("bold" if bold else "regular"), size)


def _auto_font(text: str, base_size: int, bold: bool = True,
               steps: tuple = ((15, 1.0), (22, 0.87), (30, 0.75), (40, 0.65))) -> ImageFont.FreeTypeFont:
    """텍스트 길이에 따라 폰트 크기 자동 축소 (최소 28px 보장)"""
    ratio = 1.0
    for char_limit, r in sorted(steps, key=lambda x: x[0]):
        if len(text) >= char_limit:
            ratio = r
    return _font(max(28, int(base_size * ratio)), bold=bold)


# 이모지 유니코드 범위를 제거 — 한국어 폰트로 그리면 ✖로 나오기 때문
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F9FF"   # 일반 이모지
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\u2600-\u26FF"           # 기호
    "\u2700-\u27BF"
    "\U00002702-\U000027B0"
    "\u200d"                  # ZWJ
    "\ufe0f"                  # variation selector
    "]+",
    flags=re.UNICODE,
)


def _clean(text: str) -> str:
    """텍스트에서 이모지 제거 후 앞뒤 공백 정리"""
    return _EMOJI_RE.sub("", text).strip()


# ── 이모지: RGBA 레이어 방식 (X 버그 수정) ───────────────────

def _paste_emoji(img: Image.Image, emoji_char: str, center_x: int, y: int, size: int = 72) -> tuple[Image.Image, int]:
    """
    RGBA 투명 레이어에 이모지를 그린 뒤 원본에 합성.
    center_x: 이모지를 가로 중앙 기준으로 배치 (textbbox로 실제 너비 계산).
    반환: (새 이미지, 블록 높이)
    """
    ep = _find_emoji_font()
    if not ep or not emoji_char:
        return img, 0
    try:
        ef = ImageFont.truetype(ep, size)
        tmp = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(tmp)
        # U+FE0F variation selector는 Pillow에서 빈 advance width로 처리돼 bb가 2배가 됨 → 제거
        draw_char = emoji_char.replace("️", "")
        bb = d.textbbox((0, 0), draw_char, font=ef, embedded_color=True)
        x = center_x - (bb[0] + bb[2]) // 2
        actual_y = y - bb[1]
        d.text((x, actual_y), draw_char, font=ef, embedded_color=True)
        result = Image.alpha_composite(img.convert("RGBA"), tmp).convert("RGB")
        eh = bb[3] - bb[1]
        return result, eh + 8
    except Exception:
        return img, 0


# ── 배경 ───────────────────────────────────────────────────

def _apply_background(base: Image.Image, for_cover: bool = False) -> Image.Image:
    img = base.copy().convert("RGB").resize((W, H), Image.LANCZOS)
    if for_cover:
        # 커버: 블러 최소화 + 채도 유지 → 배경이 더 생생하게 보임
        img = img.filter(ImageFilter.GaussianBlur(radius=1))
        img = ImageEnhance.Contrast(img).enhance(0.90)
        img = ImageEnhance.Color(img).enhance(0.85)
        # 오버레이를 일반보다 20 연하게 → 배경 사진이 더 살아남
        alpha = max(60, STYLE["overlay_alpha"] - 30)
    else:
        img = img.filter(ImageFilter.GaussianBlur(radius=2))
        img = ImageEnhance.Contrast(img).enhance(0.80)
        img = ImageEnhance.Color(img).enhance(0.65)
        alpha = STYLE["overlay_alpha"]
    overlay = Image.new("RGBA", (W, H), (*STYLE["overlay_color"], alpha))
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def _draw_gradient_bottom(img: Image.Image, strength: int = 230) -> Image.Image:
    """하단에서 올라오는 강한 그라디언트"""
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    steps = 520
    for i in range(steps):
        alpha = int(strength * (i / steps) ** 1.5)
        draw.line([(0, H - steps + i), (W, H - steps + i)],
                  fill=(*STYLE["overlay_color"], alpha))
    return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")


def _draw_gradient_top(img: Image.Image, strength: int = 120) -> Image.Image:
    """상단에서 내려오는 부드러운 그라디언트 (커버 상단 대비 강화)"""
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    steps = 320
    for i in range(steps):
        alpha = int(strength * ((steps - i) / steps) ** 2.0)
        draw.line([(0, i), (W, i)], fill=(*STYLE["overlay_color"], alpha))
    return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")


def _draw_brand_pill(img: Image.Image, text: str, y: int = 110) -> Image.Image:
    """상단 중앙에 브랜드/카테고리 레이블 pill"""
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    f = _font(22, bold=True)
    tw = int(ld.textlength(text, font=f))
    px, py = 18, 8
    bw = tw + px * 2
    bx = (W - bw) // 2
    # accent 컬러 pill 배경
    ld.rounded_rectangle(
        [bx, y, bx + bw, y + 38],
        radius=19,
        fill=(*STYLE["accent"], 220),
    )
    result = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")
    draw = ImageDraw.Draw(result)
    draw.text((bx + px, y + py), text, font=f, fill=(255, 255, 255))
    return result


def _draw_swipe_hint(img: Image.Image) -> Image.Image:
    """하단에 '스와이프 →' 힌트 텍스트"""
    draw = ImageDraw.Draw(img)
    f = _font(20, bold=False)
    hint = "스와이프해서 더 보기  →"
    hw = int(draw.textlength(hint, font=f))
    draw.text(((W - hw) // 2, H - 52), hint, font=f, fill=(*STYLE["text_muted"][:3], 180)
              if len(STYLE["text_muted"]) == 3 else STYLE["text_muted"])
    return img


def _draw_accent_bar(img: Image.Image, y: int, width: int = 60, height: int = 5) -> Image.Image:
    """accent 색상 굵은 short bar (구분선 대용 — 더 임팩트 있음)"""
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    x0 = (W - width) // 2
    ld.rounded_rectangle(
        [x0, y, x0 + width, y + height],
        radius=height // 2,
        fill=(*STYLE["accent"], 255),
    )
    # accent2 포인트 점
    ld.ellipse(
        [x0 + width + 10, y, x0 + width + 10 + height, y + height],
        fill=(*STYLE["accent2"], 200),
    )
    return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")


# ── 헬퍼: 텍스트 중앙 x 계산 ─────────────────────────────

def _cx(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    """텍스트를 W 기준 중앙 정렬할 x 좌표"""
    return (W - int(draw.textlength(text, font=font))) // 2


# ── 상단 배지 ──────────────────────────────────────────────

def _draw_badge(img: Image.Image, slide_num: int, total: int, handle: str) -> Image.Image:
    """슬라이드 번호 배지(좌) + 핸들(우) — RGBA 레이어 합성"""
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    y = 46

    # 번호 배지
    f = _font(24, bold=True)
    badge = f"{slide_num:02d}/{total:02d}"
    bw = int(ld.textlength(badge, font=f)) + 24
    bh = 38
    ld.rounded_rectangle([PAD, y, PAD + bw, y + bh], radius=8,
                          fill=(*STYLE["accent"], 210))

    result = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")
    draw = ImageDraw.Draw(result)
    draw.text((PAD + 12, y + 7), badge, font=f, fill=(255, 255, 255))

    # 핸들
    if handle:
        hf = _font(24, bold=False)
        hw = int(draw.textlength(handle, font=hf))
        draw.text((W - PAD - hw, y + 8), handle, font=hf, fill=STYLE["text_muted"])

    return result


def _draw_divider_line(draw: ImageDraw.ImageDraw, y: int, width: int = 80) -> None:
    x0 = (W - width) // 2
    draw.line([(x0, y), (x0 + width, y)], fill=STYLE["divider"], width=3)


def _draw_accent_box(img: Image.Image, text: str, cx_center: int, y: int) -> tuple[Image.Image, int]:
    """강조 박스 — 중앙 정렬. 반환: (new_img, bottom_y)"""
    if not text:
        return img, y
    f = _font(34, bold=True)
    draw = ImageDraw.Draw(img)
    tw = int(draw.textlength(text, font=f))
    bpad_x, bpad_y = 20, 10
    bw = tw + bpad_x * 2
    bx = (W - bw) // 2
    box = [bx, y, bx + bw, y + f.size + bpad_y * 2]

    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    ld.rounded_rectangle(box, radius=10, fill=(*STYLE["accent"], 200))
    img = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")
    draw = ImageDraw.Draw(img)
    draw.text((bx + bpad_x, y + bpad_y), text, font=f, fill=(255, 255, 255))
    return img, box[3] + 16


def _bottom_accent_line(draw: ImageDraw.ImageDraw) -> None:
    draw.line([(0, H - 6), (W, H - 6)], fill=STYLE["accent2"], width=6)


# ── 커버 ───────────────────────────────────────────────────

def _render_cover(img: Image.Image, slide: Slide, total: int,
                   handle: str, hook: str = "") -> Image.Image:
    """
    커버 (리뉴얼):
      상단 그라디언트 → 브랜드 pill → 이모지
      → accent bar → 대형 타이틀 → hook 문장
      → accent 수치 박스 (있을 때)
      → 하단: 날짜(좌) + 핸들(우) + 스와이프 힌트 + accent line
    """
    img = _draw_gradient_bottom(img.copy(), strength=245)
    img = _draw_gradient_top(img, strength=130)

    # ── 상단: 슬라이드 번호 배지 + 핸들 ──────────────────
    img = _draw_badge(img, 1, total, handle)

    # ── 브랜드 카테고리 pill (배지 아래) ─────────────────
    now_str = datetime.now().strftime("%Y.%m.%d")
    pill_text = f"AI 인사이트  ·  {now_str}"
    img = _draw_brand_pill(img, pill_text, y=110)

    draw = ImageDraw.Draw(img)
    text_w = W - PAD * 2

    # ── 이모지 — 중앙 크게 (세로 35%~45% 위치) ──────────
    y = int(H * 0.35)
    if slide.emoji:
        img, eh = _paste_emoji(img, slide.emoji, W // 2, y, size=100)
        draw = ImageDraw.Draw(img)
        y += eh + 20
    else:
        y = int(H * 0.42)

    # ── accent bar (구분선 대신) ──────────────────────────
    img = _draw_accent_bar(img, y, width=56, height=5)
    draw = ImageDraw.Draw(img)
    y += 24

    # ── 타이틀 — 가장 크고 임팩트 있게 ──────────────────
    clean_title = _clean(slide.title)
    tf = _auto_font(clean_title, 84, bold=True,
                    steps=((12, 1.0), (18, 0.88), (26, 0.76), (36, 0.65)))
    t_lines = wrap_text(clean_title, tf, draw, text_w)
    _, _, _, lh = draw.textbbox((0, 0), "가나다", font=tf)
    for line in t_lines:
        draw.text((_cx(draw, line, tf), y), line, font=tf, fill=STYLE["text_primary"])
        y += int(lh * 1.18)
    y += 18

    # ── hook 문장 ─────────────────────────────────────────
    sub = _clean(hook or slide.body or "")
    if sub:
        bf = _auto_font(sub, 38, bold=False,
                        steps=((20, 1.0), (35, 0.87), (50, 0.76)))
        b_lines = wrap_text(sub, bf, draw, text_w)
        _, _, _, lh2 = draw.textbbox((0, 0), "가나다", font=bf)
        for line in b_lines:
            draw.text((_cx(draw, line, bf), y), line, font=bf,
                      fill=STYLE["text_secondary"])
            y += int(lh2 * 1.4)
        y += 16

    # ── accent 수치 강조 박스 (있을 때) ──────────────────
    if slide.accent:
        img, y = _draw_accent_box(img, _clean(slide.accent), W // 2, y)
        draw = ImageDraw.Draw(img)

    # ── 하단 스와이프 힌트 ────────────────────────────────
    img = _draw_swipe_hint(img)
    _bottom_accent_line(ImageDraw.Draw(img))
    return img


# ── 컨텐츠 카드 ────────────────────────────────────────────

def _render_content(img: Image.Image, slide: Slide, total: int, handle: str) -> Image.Image:
    """
    컨텐츠: 상단 gradient → 배지 → 이모지 → 타이틀 → accent bar
            → 좌측 accent 세로선 + 본문(첫줄 강조) → accent box
    콘텐츠 블록을 배지 하단~하단 accent line 사이 수직 중앙에 배치.
    """
    img = _draw_gradient_top(img.copy(), strength=40)
    # 컨텐츠 카드 오버레이를 커버보다 조금 연하게 → 배경이 더 살아남
    img_arr = img.convert("RGBA")
    ov = Image.new("RGBA", (W, H), (*STYLE["overlay_color"], max(0, STYLE["overlay_alpha"] - 30)))
    img = Image.alpha_composite(img_arr, ov).convert("RGB")
    img = _draw_badge(img, slide.slide_number, total, handle)
    draw = ImageDraw.Draw(img)
    text_w = W - PAD * 2

    # 폰트/줄 수 사전 계산 (수직 중앙 정렬을 위해)
    _clean_title = _clean(slide.title)
    tf = _auto_font(_clean_title, 62, bold=True)
    t_lines = wrap_text(_clean_title, tf, draw, text_w)
    _, _, _, lh = draw.textbbox((0, 0), "가나다", font=tf)

    bf = _font(36, bold=False)
    b_lines = wrap_text(_clean(slide.body), bf, draw, text_w)
    _, _, _, lh2 = draw.textbbox((0, 0), "가나다", font=bf)

    content_h = 0
    if slide.emoji:
        content_h += 80 + 4
    content_h += len(t_lines) * int(lh * 1.15) + 14
    content_h += 28
    content_h += len(b_lines) * int(lh2 * 1.5) + 22
    if slide.accent:
        content_h += 70

    # 배지 하단(108px) ~ 하단 accent line(H-60) 사이 수직 중앙
    avail_h = H - 108 - 60
    y = 108 + max(0, (avail_h - content_h) // 2)

    # 이모지 — RGBA 합성
    if slide.emoji:
        img, eh = _paste_emoji(img, slide.emoji, W // 2, y, size=80)
        draw = ImageDraw.Draw(img)
        y += eh + 4

    # 타이틀 — 중앙 정렬
    for line in t_lines:
        draw.text((_cx(draw, line, tf), y), line, font=tf, fill=STYLE["text_primary"])
        y += int(lh * 1.15)
    y += 14

    # accent bar
    img = _draw_accent_bar(img, y, width=56, height=5)
    draw = ImageDraw.Draw(img)
    y += 28

    # 본문
    body_total_h = len(b_lines) * int(lh2 * 1.5)

    # 좌측 accent 세로선
    bar_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bar_layer)
    bar_x = PAD - 18
    bd.rounded_rectangle(
        [bar_x, y, bar_x + 4, y + body_total_h],
        radius=2,
        fill=(*STYLE["accent"], 165),
    )
    img = Image.alpha_composite(img.convert("RGBA"), bar_layer).convert("RGB")
    draw = ImageDraw.Draw(img)

    # 본문 — 좌측 정렬. 첫 줄 text_primary(밝게), 나머지 text_secondary
    for i, line in enumerate(b_lines):
        color = STYLE["text_primary"] if i == 0 else STYLE["text_secondary"]
        draw.text((PAD, y), line, font=bf, fill=color)
        y += int(lh2 * 1.5)
    y += 22

    if slide.accent:
        img, _ = _draw_accent_box(img, _clean(slide.accent), W // 2, y)
        draw = ImageDraw.Draw(img)

    _bottom_accent_line(ImageDraw.Draw(img))
    return img


# ── Split 레이아웃 ─────────────────────────────────────────

def _render_split(
    img: Image.Image,
    slide: Slide,
    total: int,
    handle: str,
    thumbnail: Image.Image,
    video_url: str = "",
) -> Image.Image:
    """
    상단 45% = YouTube 썸네일 (재생버튼 + URL 오버레이)
    하단 55% = 텍스트 중앙 정렬
    """
    img = img.copy().convert("RGB")
    thumb_h = int(H * 0.45)

    # 썸네일 붙이기
    thumb = thumbnail.copy().convert("RGB").resize((W, thumb_h), Image.LANCZOS)
    ov = Image.new("RGBA", (W, thumb_h), (0, 0, 0, 55))
    merged = Image.alpha_composite(thumb.convert("RGBA"), ov).convert("RGB")
    img.paste(merged, (0, 0))

    # 썸네일 상단 미세 어둠 (배지 가독성 확보)
    top_fade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    tfd = ImageDraw.Draw(top_fade)
    for fi in range(70):
        a = int(90 * ((70 - fi) / 70) ** 1.8)
        tfd.line([(0, fi), (W, fi)], fill=(*STYLE["overlay_color"], a))
    img = Image.alpha_composite(img.convert("RGBA"), top_fade).convert("RGB")

    # 재생 버튼 (중앙) — 그림자 + 버튼
    cx, cy, r = W // 2, thumb_h // 2, 52
    btn = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(btn)
    bd.ellipse([cx - r - 2, cy - r - 2, cx + r + 2, cy + r + 2], fill=(0, 0, 0, 90))
    bd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(220, 30, 30, 230))
    bd.polygon([(cx - 14, cy - 22), (cx - 14, cy + 22), (cx + 28, cy)], fill=(255, 255, 255))
    img = Image.alpha_composite(img.convert("RGBA"), btn).convert("RGB")

    # 썸네일 → 텍스트 영역: 하드 라인 대신 그라디언트 페이드
    fade_h = 100
    fade_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    fl = ImageDraw.Draw(fade_layer)
    for fi in range(fade_h):
        a = int(245 * (fi / fade_h) ** 1.6)
        fl.line([(0, thumb_h - fade_h + fi), (W, thumb_h - fade_h + fi)],
                fill=(*STYLE["overlay_color"], a))
    img = Image.alpha_composite(img.convert("RGBA"), fade_layer).convert("RGB")

    # accent 포인트 라인 (페이드 경계 아래 얇게)
    draw = ImageDraw.Draw(img)
    draw.line([(PAD, thumb_h + 3), (W - PAD, thumb_h + 3)], fill=STYLE["accent"], width=2)

    # 배지 (썸네일 위)
    img = _draw_badge(img, slide.slide_number, total, handle)
    draw = ImageDraw.Draw(img)

    # 영상 URL 오버레이 (썸네일 우하단)
    if video_url:
        uf = _font(21, bold=False)
        url_text = f"▶ {video_url}"
        uw = int(draw.textlength(url_text, font=uf))
        ux, uy = W - uw - 14, thumb_h - 34
        url_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ul = ImageDraw.Draw(url_layer)
        ul.rounded_rectangle([ux - 6, uy - 4, ux + uw + 6, uy + 26],
                              radius=5, fill=(0, 0, 0, 170))
        img = Image.alpha_composite(img.convert("RGBA"), url_layer).convert("RGB")
        draw = ImageDraw.Draw(img)
        draw.text((ux, uy), url_text, font=uf, fill=(0, 229, 255))

    # 텍스트 영역 (썸네일 아래 55% — 세로 중앙 정렬)
    text_w = W - PAD * 2
    text_zone_top = thumb_h + 8
    text_zone_bot = H - 30   # 하단 여백 확보

    _split_title = _clean(slide.title)
    tf = _auto_font(_split_title, 52, bold=True)
    bf = _font(33, bold=False)
    t_lines = wrap_text(_split_title, tf, draw, text_w)
    b_lines = wrap_text(_clean(slide.body), bf, draw, text_w)
    _, _, _, tlh = draw.textbbox((0, 0), "가나다", font=tf)
    _, _, _, blh = draw.textbbox((0, 0), "가나다", font=bf)
    divider_gap = 32  # 구분선 위아래 여백
    accent_h = 60 if slide.accent else 0

    total_h = (
        len(t_lines) * int(tlh * 1.15)
        + divider_gap
        + len(b_lines) * int(blh * 1.45)
        + accent_h
    )
    # 세로 중앙 시작점
    y = text_zone_top + max(0, (text_zone_bot - text_zone_top - total_h) // 2)

    # 타이틀 — 이모지 제거 + 중앙
    for line in t_lines:
        draw.text((_cx(draw, line, tf), y), line, font=tf, fill=STYLE["text_primary"])
        y += int(tlh * 1.15)
    y += 10

    _draw_divider_line(draw, y)
    y += divider_gap - 10

    # 본문 — 이모지 제거 + 중앙
    for line in b_lines:
        draw.text((_cx(draw, line, bf), y), line, font=bf, fill=STYLE["text_secondary"])
        y += int(blh * 1.45)
    y += 16

    if slide.accent:
        img, _ = _draw_accent_box(img, _clean(slide.accent), W // 2, y)
        draw = ImageDraw.Draw(img)

    _bottom_accent_line(ImageDraw.Draw(img))
    return img


# ── CTA ────────────────────────────────────────────────────

def _render_cta(img: Image.Image, slide: Slide, total: int,
                handle: str, hashtags: list[str]) -> Image.Image:
    """
    CTA: 상단+하단 gradient → 배지 → 이모지 → accent bar → 타이틀
         → 본문 → handle pill → 해시태그 pill 배지들
    y 오버플로 방지: 각 요소가 해시태그 영역과 겹치면 크기/간격 자동 축소
    """
    img = _draw_gradient_bottom(img.copy(), strength=200)
    img = _draw_gradient_top(img, strength=70)
    img = _draw_badge(img, total, total, handle)
    draw = ImageDraw.Draw(img)
    text_w = W - PAD * 2

    # 해시태그 영역 상단 예약 (pill 2줄 공간 보장)
    HASHTAG_RESERVE = 100   # 하단에서 이 위치까지 해시태그 영역
    CONTENT_BOTTOM  = H - HASHTAG_RESERVE

    y = int(H * 0.20)

    # 이모지 — 정확한 중앙 (size 기준으로 계산)
    if slide.emoji:
        img, eh = _paste_emoji(img, slide.emoji, W // 2, y, size=90)
        draw = ImageDraw.Draw(img)
        y += eh + 12

    # accent bar
    img = _draw_accent_bar(img, y, width=56, height=5)
    draw = ImageDraw.Draw(img)
    y += 26

    # 타이틀
    _cta_title = _clean(slide.title)
    tf = _auto_font(_cta_title, 54, bold=True)
    t_lines = wrap_text(_cta_title, tf, draw, text_w)
    _, _, _, lh = draw.textbbox((0, 0), "가나다", font=tf)
    for line in t_lines:
        if y + int(lh) < CONTENT_BOTTOM - 160:   # 본문·handle 공간 남긴 채
            draw.text((_cx(draw, line, tf), y), line, font=tf, fill=STYLE["text_primary"])
        y += int(lh * 1.2)
    y += 16

    # 본문
    bf = _font(34, bold=False)
    b_lines = wrap_text(_clean(slide.body), bf, draw, text_w)
    _, _, _, lh2 = draw.textbbox((0, 0), "가나다", font=bf)
    for line in b_lines:
        if y + int(lh2) < CONTENT_BOTTOM - 80:   # handle 공간 남긴 채
            draw.text((_cx(draw, line, bf), y), line, font=bf, fill=STYLE["text_secondary"])
        y += int(lh2 * 1.5)
    y += 20

    # 핸들 — pill 배경 + accent2 텍스트 (CONTENT_BOTTOM 초과 시 생략)
    if handle and y + 60 < CONTENT_BOTTOM:
        hf = _font(36, bold=True)
        hw = int(draw.textlength(handle, font=hf))
        hpad = 22
        hx = (W - hw - hpad * 2) // 2
        hy = y
        handle_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        hd = ImageDraw.Draw(handle_layer)
        hd.rounded_rectangle(
            [hx, hy, hx + hw + hpad * 2, hy + 52],
            radius=26, fill=(*STYLE["accent2"], 45),
        )
        hd.rounded_rectangle(
            [hx, hy, hx + hw + hpad * 2, hy + 52],
            radius=26, outline=(*STYLE["accent2"], 160), width=2,
        )
        img = Image.alpha_composite(img.convert("RGBA"), handle_layer).convert("RGB")
        draw = ImageDraw.Draw(img)
        # 텍스트를 pill 높이(52px) 기준으로 수직 중앙 정렬
        bb = draw.textbbox((0, 0), handle, font=hf)
        text_h = bb[3] - bb[1]
        text_top = hy + (52 - text_h) // 2
        draw.text((hx + hpad, text_top), handle, font=hf, fill=STYLE["accent2"])

    # 해시태그 — pill 배지, 항상 하단에 고정 배치
    if hashtags:
        tgf = _font(19, bold=False)
        pill_h = 32
        gap_x, gap_y = 7, 6

        tag_positions: list[tuple[str, int, int, int]] = []
        tx = PAD
        ty = H - HASHTAG_RESERVE + 10   # 예약 영역 상단에서 시작
        for tag in hashtags[:15]:
            tw_px = int(draw.textlength(tag, font=tgf))
            pill_w = tw_px + 20
            if tx + pill_w > W - PAD:
                tx = PAD
                ty += pill_h + gap_y
            if ty + pill_h > H - 12:   # 화면 하단 경계
                break
            tag_positions.append((tag, tx, ty, pill_w))
            tx += pill_w + gap_x

        pill_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        pd = ImageDraw.Draw(pill_layer)
        for tag, px, py, pw in tag_positions:
            pd.rounded_rectangle(
                [px, py, px + pw, py + pill_h],
                radius=pill_h // 2,
                fill=(*STYLE["accent"], 50),
            )
        img = Image.alpha_composite(img.convert("RGBA"), pill_layer).convert("RGB")
        draw = ImageDraw.Draw(img)
        for tag, px, py, pw in tag_positions:
            draw.text((px + 10, py + 6), tag, font=tgf, fill=STYLE["accent2"])

    _bottom_accent_line(ImageDraw.Draw(img))
    return img


# ── 캡션 생성 ─────────────────────────────────────────────

def _generate_caption(script: CardNewsScript, handle: str) -> str:
    """GPT-4o-mini로 카드 내용 기반 인스타그램 캡션 동적 생성 — 사람이 쓴 것처럼"""
    slides_summary = "\n".join(
        f"  [{s.slide_type}] {s.title}: {s.body[:120]}"
        for s in script.slides
    )
    hashtag_str = " ".join(script.hashtags[:15])

    prompt = (
        f"인스타그램 캡션 작성.\n\n"
        f"주제: {script.topic}\n"
        f"훅: {script.hook}\n"
        f"슬라이드:\n{slides_summary}\n\n"
        f"━━━ 작성 규칙 ━━━\n"
        f"1. 첫 줄: 숫자/고유명사로 시작하는 임팩트 문장 (hook과 다른 각도, 30자 이내)\n"
        f"2. 빈 줄 + 핵심 인사이트 2~3개 (· 시작, 각 1줄, 카드 본문을 그대로 쓰지 말 것)\n"
        f"3. 빈 줄 + CTA (저장/팔로우/댓글 중 내용에 가장 자연스러운 것 1문장)\n"
        f"4. 이모지 2~4개, 총 120~180자 (해시태그 제외)\n\n"
        f"━━━ 절대 금지 표현 ━━━\n"
        f"'이를 통해', '해당', '~함으로써', '다양한', '혁신적인', '~것으로 나타났다'\n"
        f"'주목받고 있다', '중요성이', '~에 따르면'\n\n"
        f"━━━ 권장 스타일 ━━━\n"
        f"- 친한 친구에게 흥미로운 뉴스 알려주듯 씀\n"
        f"- 짧고 끊어지는 문장, 구어체 자연스럽게\n"
        f"- 독자가 '아 진짜?' '나도 써봐야겠다' 반응하게\n\n"
        f"마지막 줄: {handle}\n"
        f"해시태그: {hashtag_str}\n\n"
        f"캡션만 출력. 설명 불필요."
    )

    try:
        from langchain_openai import ChatOpenAI
        from src.config import OPENAI_API_KEY
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.75, api_key=OPENAI_API_KEY)
        result = llm.invoke(prompt)
        body = result.content.strip()
        if hashtag_str and hashtag_str[:10] not in body:
            body = f"{body}\n\n{hashtag_str}"
        return body
    except Exception as e:
        print(f"  [Renderer] AI 캡션 생성 실패({e}), 기본 캡션 사용")
        lines = [f"⚡ {script.hook}", ""]
        for s in [x for x in script.slides if x.slide_type == "content"]:
            lines.append(f"· {s.title}: {s.body.split(chr(10))[0][:60]}")
        lines += ["", "저장해두고 나중에 써먹어요 💾", "", handle, "", hashtag_str]
        return "\n".join(lines)


# ── 메인 ──────────────────────────────────────────────────

def render_card_set(
    script: CardNewsScript,
    background: Image.Image,
    handle: str = "",
    output_subdir: str | None = None,
    persona: Persona | None = None,
    youtube_keyword: str = "",
    video_infos: list | None = None,
) -> list[Path]:
    p = persona or load_persona()
    active_handle = handle or p.handle
    global STYLE
    STYLE = _build_style(p)

    bg = _apply_background(background)             # 일반 슬라이드용
    bg_cover = _apply_background(background, for_cover=True)  # 커버 전용 (더 선명)

    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    safe_topic = re.sub(r"[^\w가-힣]", "_", script.topic)[:20]
    out_dir = OUTPUT_DIR / (output_subdir or f"{date_str}_{safe_topic}")
    out_dir.mkdir(parents=True, exist_ok=True)

    # content 슬라이드 → 썸네일 매핑
    content_slides = [s for s in script.slides if s.slide_type == "content"]
    thumb_map: dict[int, tuple[Image.Image, str]] = {}
    if video_infos:
        for i, slide in enumerate(content_slides):
            if i < len(video_infos):
                vi = video_infos[i]
                if vi is not None and vi.thumbnail:
                    # start_seconds > 0이면 URL에 ?t= 파라미터 추가
                    vid_url_with_t = vi.url
                    if getattr(vi, "start_seconds", 0) > 0:
                        vid_url_with_t = f"youtu.be/{vi.video_id}?t={vi.start_seconds}"
                    thumb_map[slide.slide_number] = (vi.thumbnail, vid_url_with_t)

    # content 슬라이드 중 YouTube 썸네일 없는 것 → 슬라이드별 개별 Pexels 이미지 준비
    slide_bg_map: dict[int, Image.Image] = {}   # {slide_number: bg_image}
    no_thumb_content = [s for s in content_slides if s.slide_number not in thumb_map]
    if no_thumb_content:
        try:
            from src.agents.image_searcher import search_pexels, _resize_and_crop
            import random as _random
            _used_pages: set[int] = set()
            for s_idx, slide in enumerate(no_thumb_content):
                # 슬라이드 title을 키워드로 Pexels 검색 (각각 다른 페이지)
                page = s_idx + 1
                kw = f"{slide.title} {script.topic}"[:50]
                alt_bg = search_pexels(kw, page=page)
                if alt_bg:
                    slide_bg_map[slide.slide_number] = _apply_background(alt_bg)
                    print(f"  [Renderer] 슬라이드 {slide.slide_number}: 개별 배경 적용")
        except Exception as e:
            print(f"  [Renderer] 개별 배경 이미지 스킵 ({e})")

    total = len(script.slides)
    paths: list[Path] = []

    for slide in script.slides:
        # 슬라이드별 배경 선택 (개별 > 공통)
        slide_bg = slide_bg_map.get(slide.slide_number, bg)

        if slide.slide_type == "cover":
            rendered = _render_cover(bg_cover, slide, total, active_handle, hook=script.hook)
        elif slide.slide_type == "cta":
            rendered = _render_cta(bg, slide, total, active_handle, script.hashtags)
        elif slide.slide_number in thumb_map:
            thumb, vid_url = thumb_map[slide.slide_number]
            print(f"  [Renderer] 슬라이드 {slide.slide_number}: split ({vid_url})")
            rendered = _render_split(slide_bg, slide, total, active_handle, thumb, vid_url)
        else:
            rendered = _render_content(slide_bg, slide, total, active_handle)

        fname = f"card_{slide.slide_number:02d}_{slide.slide_type}.png"
        fpath = out_dir / fname
        rendered.save(str(fpath), "PNG", optimize=True)
        paths.append(fpath)
        print(f"  [Renderer] 저장: {fname}")

    # 배경 이미지 저장 — 슬라이드 부분 수정 시 재사용
    bg_save = background.convert("RGB") if background.mode != "RGB" else background.copy()
    bg_save.save(str(out_dir / "background.png"), "PNG")

    (out_dir / "hashtags.txt").write_text(" ".join(script.hashtags), encoding="utf-8")
    caption = _generate_caption(script, active_handle)
    (out_dir / "caption.txt").write_text(caption, encoding="utf-8")
    print(f"  [Renderer] 캡션 저장: caption.txt")

    # script.json 저장 — 슬라이드 부분 수정 기능에서 사용
    import json as _json
    script_data = {
        "topic": script.topic,
        "hook": script.hook,
        "hashtags": script.hashtags,
        "slides": [
            {
                "slide_number": s.slide_number,
                "slide_type": s.slide_type,
                "title": s.title,
                "body": s.body,
            }
            for s in script.slides
        ],
    }
    (out_dir / "script.json").write_text(
        _json.dumps(script_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return paths
