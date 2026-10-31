"""Turn a finished card set into a vertical (9:16) Reel. No AI calls, no music, nothing is posted.

Design (the old `render_reels` letterboxed 4:5 cards on black, which reads as a slideshow):
  * a blurred, darkened copy of the card fills the 1080x1920 frame, so there is no dead black;
  * the card sits full width in the middle and drifts in slowly (a 4% zoom) so each slide moves;
  * a segmented progress bar at the top shows where the viewer is in the set;
  * each slide stays as long as its text needs to be read, not a fixed 3.5 seconds;
  * slides cross-fade; video cards keep their own clip (capped) and sound.
There is no music on purpose: licensed audio cannot be attached through the publishing API, and
unlicensed music would risk the account. Add trending audio in the app if wanted.
"""
from __future__ import annotations

import json
from pathlib import Path

REEL_SIZE = (1080, 1920)
FPS = 24
FADE = 0.25
ZOOM = 0.04
MAX_SECONDS = 60          # a Reel may run to 90s; short sets keep people watching to the end
MAX_VIDEO_CARD_SECONDS = 8
BAR_H, BAR_GAP, BAR_MARGIN = 8, 8, 40

COVER_SECONDS = 2.5
CTA_SECONDS = 3.5
MIN_CONTENT, MAX_CONTENT = 3.5, 7.0


def slide_seconds(slide_type: str, text_chars: int) -> float:
    """How long a slide stays: reading time for content cards, fixed short holds for cover and CTA."""
    if slide_type == "cover":
        return COVER_SECONDS
    if slide_type == "cta":
        return CTA_SECONDS
    return round(min(MAX_CONTENT, max(MIN_CONTENT, 2.0 + text_chars / 14)), 2)


def plan_durations(slides: list[tuple[str, int]], limit: float = MAX_SECONDS) -> list[float]:
    """Per-slide seconds, scaled down together when the set would run past the limit."""
    seconds = [slide_seconds(kind, chars) for kind, chars in slides]
    total = sum(seconds)
    if total > limit:
        seconds = [round(s * limit / total, 2) for s in seconds]
    return seconds


def _text_sizes(folder: Path, n_cards: int) -> list[tuple[str, int]]:
    """(slide type, characters of text) per card, from script.json when it is there."""
    try:
        slides = json.loads((folder / "script.json").read_text(encoding="utf-8")).get("slides", [])
    except (OSError, ValueError):
        slides = []
    sizes = [
        (str(s.get("slide_type") or "content"), len(str(s.get("title") or "")) + len(str(s.get("body") or "")))
        for s in slides
    ]
    if len(sizes) != n_cards:                                  # unreadable or mismatched: use plain defaults
        sizes = [("cover", 0)] + [("content", 60)] * max(0, n_cards - 2) + [("cta", 0)] if n_cards >= 2 else [("content", 60)] * n_cards
    return sizes


def _background(card_path: Path, size: tuple[int, int], index: int, total: int):
    """Blurred, darkened full-frame copy of the card with the progress bar baked in."""
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

    width, height = size
    card = Image.open(card_path).convert("RGB")
    scale = max(width / card.width, height / card.height)
    big = card.resize((int(card.width * scale) + 1, int(card.height * scale) + 1), Image.LANCZOS)
    left, top = (big.width - width) // 2, (big.height - height) // 2
    bg = big.crop((left, top, left + width, top + height)).filter(ImageFilter.GaussianBlur(max(2, width // 27)))
    bg = ImageEnhance.Brightness(bg).enhance(0.42)
    draw = ImageDraw.Draw(bg, "RGBA")
    bar_w = (width - 2 * BAR_MARGIN - BAR_GAP * (total - 1)) // total
    for i in range(total):
        x0 = BAR_MARGIN + i * (bar_w + BAR_GAP)
        fill = (255, 255, 255, 235) if i <= index else (255, 255, 255, 70)
        scale_h = max(2, BAR_H * width // 1080)
        draw.rounded_rectangle((x0, BAR_MARGIN, x0 + bar_w, BAR_MARGIN + scale_h), radius=scale_h // 2, fill=fill)
    return bg


def _still_clip(card_path: Path, seconds: float, size: tuple[int, int], index: int, total: int):
    """A still card with a slow drift-in. The blurred background and the progress bar are drawn once;
    only the card area is re-sampled each frame (a crop-and-resize), which is what keeps this fast."""
    import numpy as np
    from moviepy import VideoClip
    from PIL import Image

    width, height = size
    card = Image.open(card_path).convert("RGB")
    card = card.resize((width, round(card.height * width / card.width)), Image.LANCZOS)
    top = (height - card.height) // 2
    base = np.array(_background(card_path, size, index, total))
    cw, ch = card.size

    def make_frame(t: float):
        zoom = 1 + ZOOM * min(1.0, t / max(seconds, 0.1))
        dx, dy = cw * (1 - 1 / zoom) / 2, ch * (1 - 1 / zoom) / 2
        view = card.resize((cw, ch), Image.BILINEAR, box=(dx, dy, cw - dx, ch - dy))
        frame = base.copy()
        frame[top:top + ch, :cw] = np.asarray(view)
        return frame

    return VideoClip(make_frame, duration=seconds)


def _video_card_clip(card_path: Path, size: tuple[int, int], index: int, total: int):
    """A video card keeps its own clip (and sound) over the same blurred background."""
    import numpy as np
    from moviepy import CompositeVideoClip, ImageClip, VideoFileClip

    width, height = size
    card = VideoFileClip(str(card_path))
    seconds = min(card.duration, MAX_VIDEO_CARD_SECONDS)
    card = card.subclipped(0, seconds).resized(width=width).with_position("center")
    background = ImageClip(np.array(_background(card_path, size, index, total))).with_duration(seconds)
    return CompositeVideoClip([background, card], size=size).with_duration(seconds)


def _slide_clip(card_path: Path, seconds: float, size: tuple[int, int], index: int, total: int):
    from moviepy import vfx

    if card_path.suffix.lower() == ".mp4":
        clip = _video_card_clip(card_path, size, index, total)
    else:
        clip = _still_clip(card_path, seconds, size, index, total)
    return clip.with_effects([vfx.CrossFadeIn(FADE)])


def build_reel(
    folder: Path,
    out_name: str = "reel.mp4",
    size: tuple[int, int] = REEL_SIZE,
    fps: int = FPS,
) -> Path | None:
    """Build <folder>/reel.mp4 from the folder's card files; returns the path, or None on failure."""
    from moviepy import concatenate_videoclips

    from src.agents.content_queue import _select_render_media

    folder = Path(folder)
    cards = _select_render_media(folder)
    if len(cards) < 2:
        return None
    seconds = plan_durations(_text_sizes(folder, len(cards)))
    clips = []
    try:
        for i, (card, secs) in enumerate(zip(cards, seconds)):
            clips.append(_slide_clip(card, secs, size, i, len(cards)))
        final = concatenate_videoclips(clips, method="compose", padding=-FADE)
        out_path = folder / out_name
        final.write_videofile(
            str(out_path), fps=fps, codec="libx264", audio_codec="aac", preset="veryfast",
            ffmpeg_params=["-pix_fmt", "yuv420p", "-movflags", "+faststart"], logger=None,
        )
        final.close()
        return out_path
    except Exception as exc:                                    # a failed Reel must never block the carousel
        print(f"  [ReelBuilder] 릴스 생성 실패: {type(exc).__name__}: {exc}")
        return None
    finally:
        for clip in clips:
            clip.close()


def reel_issues(path: Path, size: tuple[int, int] = REEL_SIZE) -> list[str]:
    """Why a Reel file is not fit to post (empty = fine): size, length, file size."""
    from moviepy import VideoFileClip

    path = Path(path)
    if not path.exists():
        return ["릴스 파일이 없습니다"]
    issues: list[str] = []
    clip = VideoFileClip(str(path))
    try:
        if tuple(clip.size) != tuple(size):
            issues.append(f"해상도 {clip.size[0]}x{clip.size[1]} (필요 {size[0]}x{size[1]})")
        if not (3 <= clip.duration <= 90):
            issues.append(f"길이 {clip.duration:.1f}초 (3~90초여야 함)")
    finally:
        clip.close()
    if path.stat().st_size > 100 * 1024 * 1024:
        issues.append("파일이 100MB를 넘습니다")
    return issues
