import json

import pytest

from src import instagram_folder_post as ig


def _folder(tmp_path):
    folder = tmp_path / "set"
    folder.mkdir()
    (folder / "card_01_cover.png").write_bytes(b"x")
    (folder / "card_02_cta.png").write_bytes(b"x")
    (folder / "caption.txt").write_text("운영일지 1화\n\n· 항목", encoding="utf-8")
    (folder / "script.json").write_text(
        json.dumps({"topic": "운영일지 1화", "hook": "후크", "hashtags": ["#알고"]}, ensure_ascii=False), encoding="utf-8",
    )
    return folder


def test_a_token_from_another_account_is_refused_before_anything_is_sent(tmp_path, monkeypatch):
    from src.agents import publisher

    sent = []
    monkeypatch.setattr(publisher, "verify_instagram_account", lambda *a, **k: {"username": "someone_else"})
    monkeypatch.setattr(publisher, "publish", lambda *a, **k: sent.append(1))
    monkeypatch.setattr("src.db.get_posts", lambda **kw: [])
    ok, message = ig.post_folder(str(_folder(tmp_path)))
    assert not ok and "알고 계정" in message and not sent


def test_the_folder_is_posted_once_recorded_and_never_duplicated(tmp_path, monkeypatch):
    from src.agents import publisher

    folder = str(_folder(tmp_path))
    recorded, posts, calls = [], [], []
    monkeypatch.setattr(publisher, "verify_instagram_account", lambda *a, **k: {"username": "algo__kr"})
    monkeypatch.setattr(publisher, "publish", lambda media, **kw: calls.append((len(media), kw["caption"])) or "ig-1")
    monkeypatch.setattr("src.db.get_posts", lambda **kw: posts)
    monkeypatch.setattr("src.db.insert_post", lambda **kw: recorded.append(kw) or 1)
    monkeypatch.setattr(ig, "_log", lambda m: None)

    ok, message = ig.post_folder(folder)
    assert ok and "ig-1" in message
    assert calls == [(2, "운영일지 1화\n\n· 항목")]                  # the reviewed caption, both cards
    assert recorded[0]["platform"] == "instagram" and recorded[0]["post_id"] == "ig-1"

    posts.append({"image_dir": folder, "post_id": "ig-1"})
    ok, message = ig.post_folder(folder)
    assert not ok and "이미" in message and len(calls) == 1          # second attempt: nothing sent


def test_a_failed_publish_is_reported_and_logged_without_recording(tmp_path, monkeypatch):
    from src.agents import publisher

    logged, recorded = [], []
    monkeypatch.setattr(publisher, "verify_instagram_account", lambda *a, **k: {"username": "algo__kr"})

    def boom(*a, **k):
        raise RuntimeError("container error")

    monkeypatch.setattr(publisher, "publish", boom)
    monkeypatch.setattr("src.db.get_posts", lambda **kw: [])
    monkeypatch.setattr("src.db.insert_post", lambda **kw: recorded.append(kw))
    monkeypatch.setattr(ig, "_log", logged.append)
    ok, message = ig.post_folder(str(_folder(tmp_path)))
    assert not ok and "container error" in message and logged and not recorded
