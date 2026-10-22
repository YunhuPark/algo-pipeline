"""Post an already-approved, already-published card set to Threads (opt-in, after Instagram)."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

THREADS_MAX_CHARS = 500
DEFAULT_TOPIC_TAG = "AI Threads"


def threads_ready() -> bool:
    return bool(os.getenv("THREADS_ACCESS_TOKEN", "").strip() and os.getenv("THREADS_USER_ID", "").strip())


def _caption_for(folder: Path) -> str:
    """The reviewed Instagram caption, cut at a line boundary to fit the Threads limit."""
    path = folder / "caption.txt"
    text = path.read_text(encoding="utf-8").strip() if path.exists() else ""
    if len(text) <= THREADS_MAX_CHARS:
        return text
    kept: list[str] = []
    size = 0
    for line in text.splitlines():
        if size + len(line) + 1 > THREADS_MAX_CHARS:
            break
        kept.append(line)
        size += len(line) + 1
    return "\n".join(kept).strip()


def cross_post(image_dir: str) -> str:
    """Publish the reviewed cards (images and video cards, same selection as Instagram) to Threads.

    Raises ValueError when the set cannot go out as-is, so the caller can say so instead of
    posting a different set.
    """
    from src.agents import threads_publisher as tp
    from src.agents.content_queue import _select_render_media

    folder = Path(image_dir)
    media = _select_render_media(folder)
    if not media:
        raise ValueError("게시할 카드 이미지를 찾지 못했습니다.")
    if not threads_ready():
        raise ValueError("THREADS_ACCESS_TOKEN/THREADS_USER_ID가 .env에 없습니다.")
    base = os.getenv("IG_IMAGE_BASE_URL", "").strip().rstrip("/")
    if not base.lower().startswith("https://"):
        raise ValueError("IG_IMAGE_BASE_URL(공개 https 주소)이 필요합니다.")
    # the publisher reads credentials at import time; pick up a token refreshed since then
    tp.THREADS_ACCESS_TOKEN = os.getenv("THREADS_ACCESS_TOKEN", "")
    tp.THREADS_USER_ID = os.getenv("THREADS_USER_ID", "")
    return tp.publish(
        media, hook="", hashtags=[],
        base_url=f"{base}/output_img/{quote(folder.name)}",
        caption=_caption_for(folder),
        topic_tag=os.getenv("THREADS_TOPIC_TAG", DEFAULT_TOPIC_TAG),
    )
