"""Post a card folder that is not a queue item (e.g. a devlog episode) to Instagram, once.

Same guarantees as the Threads helper: the token must belong to the expected account (checked before
anything is sent), a folder that was already posted is refused, and the outcome is logged and recorded
in the posts table.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

EXPECTED_USERNAME = "algo__kr"


def _log(message: str) -> None:
    from datetime import datetime

    try:
        path = Path(__file__).resolve().parents[1] / "logs" / "instagram_folder_post.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n")
    except OSError:
        pass


def preview(image_dir: str) -> tuple[str, str]:
    """(account username, caption) that a post would use; raises when the account is not the expected one."""
    from src.agents import publisher

    info = publisher.verify_instagram_account()
    username = str(info.get("username") or "")
    expected = os.getenv("IG_EXPECTED_USERNAME", EXPECTED_USERNAME)
    if username != expected:
        raise ValueError(f"토큰의 Instagram 계정(@{username or '?'})이 알고 계정(@{expected})과 달라 게시를 중단합니다.")
    return username, (Path(image_dir) / "caption.txt").read_text(encoding="utf-8").strip()


def post_folder(image_dir: str) -> tuple[bool, str]:
    """Publish the folder's cards as one Instagram carousel; returns (ok, message)."""
    from src.agents import publisher
    from src.agents.content_queue import _select_render_media
    from src.db import get_posts, insert_post

    folder = Path(image_dir)
    for post in get_posts(platform="instagram", limit=200):
        if post["image_dir"] == image_dir and post["post_id"]:
            return False, f"이 폴더는 이미 Instagram에 게시되었습니다 ({post['post_id']})"
    try:
        _, caption = preview(image_dir)
        script = json.loads((folder / "script.json").read_text(encoding="utf-8"))
        media = _select_render_media(folder)
        if not media:
            raise ValueError("게시할 카드 이미지를 찾지 못했습니다.")
        post_id = publisher.publish(
            media, hook=script.get("hook", ""), hashtags=script.get("hashtags", []), caption=caption,
        )
    except Exception as exc:
        message = f"Instagram 게시 실패: {type(exc).__name__}: {str(exc)[:300]}"
        _log(f"{folder.name} {message}")
        return False, message
    insert_post(
        platform="instagram", topic=script.get("topic", folder.name), post_id=str(post_id),
        hook=script.get("hook", ""), hashtags=script.get("hashtags", []), image_dir=image_dir,
    )
    _log(f"{folder.name} Instagram 게시 완료 {post_id}")
    return True, f"Instagram 게시 완료: {post_id}"
