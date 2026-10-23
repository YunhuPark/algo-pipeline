"""Post an already-approved, already-published card set to Threads (opt-in, after Instagram)."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

THREADS_MAX_CHARS = 500
DEFAULT_TOPIC_TAG = "AI Threads"


def threads_ready() -> bool:
    from src import threads_credentials

    token, user_id = threads_credentials.load()
    return bool(token and user_id)


def threads_caption(folder: Path) -> str:
    """A short, clean Threads text built from the reviewed card content (no hashtags, no handle,
    no emoji; Threads uses one topic tag instead): headline, the verified key points, the source.

    Returns "" when the set has no usable script.json, so the caller can fall back to the Instagram
    caption.
    """
    import json

    from src.qa.text_style import strip_sentence_periods

    try:
        data = json.loads((folder / "script.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    headline = strip_sentence_periods(str(data.get("topic") or "").strip())
    # Key points: the Instagram caption's own bullets (same friendly tone, already checked against the
    # cards). Only when it has none, fall back to the verified card sentences.
    points: list[str] = []
    try:
        for line in (folder / "caption.txt").read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("·"):
                points.append("· " + strip_sentence_periods(line.strip().lstrip("·").strip()))
    except OSError:
        pass
    if not points:
        points = [
            "· " + strip_sentence_periods(str(s.get("body") or "").strip()).replace("\n", " ")
            for s in data.get("slides", [])
            if s.get("slide_type") == "content" and str(s.get("body") or "").strip()
        ]
    if not headline or not points:
        return ""
    note = data.get("source_note") or {}
    source_lines = []
    if note.get("outlet"):
        date_text = str(note.get("published") or "").replace("-", ".")
        source_lines.append(f"출처: {note['outlet']}" + (f" · {date_text}" if date_text else ""))
    if note.get("url"):
        source_lines.append(str(note["url"]))
    tail = "\n".join(source_lines)

    def assemble(n: int) -> str:
        parts = [headline, "\n".join(points[:n])]
        if tail:
            parts.append(tail)
        return "\n\n".join(parts)

    count = len(points)
    while count > 1 and len(assemble(count)) > THREADS_MAX_CHARS:
        count -= 1                      # drop the last key point rather than cut a sentence
    return assemble(count)


def _caption_for(folder: Path) -> str:
    """The Threads text; the reviewed Instagram caption (cut at a line boundary) if it cannot be built."""
    own = threads_caption(folder)
    if own:
        return own
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
    # the publisher reads credentials at import time; pick up a token refreshed since then (.env only)
    from src import threads_credentials

    tp.THREADS_ACCESS_TOKEN, tp.THREADS_USER_ID = threads_credentials.load()
    return tp.publish(
        media, hook="", hashtags=[],
        base_url=f"{base}/output_img/{quote(folder.name)}",
        caption=_caption_for(folder),
        topic_tag=os.getenv("THREADS_TOPIC_TAG", DEFAULT_TOPIC_TAG),
    )


def _log(message: str) -> None:
    """Keep a durable trace; the dashboard's job log disappears with the server process."""
    from datetime import datetime

    try:
        path = Path(__file__).resolve().parents[1] / "logs" / "threads_cross_post.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n")
    except OSError:
        pass


def cross_post_queue_item(queue_id: int) -> tuple[bool, str]:
    """Post a published queue item to Threads once; returns (ok, human-readable message).

    Refuses items that are not published on Instagram yet and items already posted to Threads
    (matched by the card folder), so a retry can never create a duplicate.
    """
    import json

    from src.db import get_posts, get_queue_row, insert_post

    row = get_queue_row(queue_id)
    if row is None:
        return False, f"큐 항목 #{queue_id}을 찾지 못했습니다"
    if row["status"] != "published":
        return False, f"#{queue_id}은 아직 Instagram에 게시되지 않아 Threads에 올리지 않습니다"
    image_dir = row["image_dir"] or ""
    for post in get_posts(platform="threads", limit=200):
        if post["image_dir"] == image_dir and post["post_id"]:
            return False, f"#{queue_id}은 이미 Threads에 게시되었습니다 ({post['post_id']})"
    try:
        post_id = cross_post(image_dir)
    except Exception as exc:
        message = f"Threads 게시 실패(Instagram은 게시됨): {type(exc).__name__}: {str(exc)[:300]}"
        _log(f"#{queue_id} {message}")
        return False, message
    hook = ""
    try:
        hook = json.loads((Path(image_dir) / "script.json").read_text(encoding="utf-8")).get("hook", "")
    except (OSError, ValueError):
        pass
    insert_post(platform="threads", topic=row["topic"], post_id=post_id, hook=hook, image_dir=image_dir)
    _log(f"#{queue_id} Threads 게시 완료 {post_id}")
    return True, f"Threads 게시 완료: {post_id}"
