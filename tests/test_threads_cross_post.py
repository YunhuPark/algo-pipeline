from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from src import threads_cross_post as tc
from src import token_refresh as tr


def _set_cards(folder: Path, names):
    folder.mkdir(parents=True, exist_ok=True)
    for n in names:
        (folder / n).write_bytes(b"x")
    (folder / "caption.txt").write_text("첫 줄\n출처: 테스트\n#태그", encoding="utf-8")


def test_cross_post_sends_the_reviewed_caption_and_folder_url(tmp_path, monkeypatch):
    folder = tmp_path / "20261020_set"
    _set_cards(folder, ["card_01.png", "card_02.png"])
    monkeypatch.setenv("THREADS_ACCESS_TOKEN", "t")
    monkeypatch.setenv("THREADS_USER_ID", "1")
    monkeypatch.setenv("IG_IMAGE_BASE_URL", "https://example.test")
    with patch("src.agents.threads_publisher.publish", return_value="post-1") as pub:
        assert tc.cross_post(str(folder)) == "post-1"
    kwargs = pub.call_args.kwargs
    assert kwargs["base_url"] == "https://example.test/output_img/20261020_set"
    assert kwargs["caption"].startswith("첫 줄")


def test_video_cards_go_to_threads_like_instagram(tmp_path, monkeypatch):
    folder = tmp_path / "set"
    _set_cards(folder, ["card_01.png", "card_02.png", "card_02.mp4"])
    monkeypatch.setenv("THREADS_ACCESS_TOKEN", "t")
    monkeypatch.setenv("THREADS_USER_ID", "1")
    monkeypatch.setenv("IG_IMAGE_BASE_URL", "https://example.test")
    with patch("src.agents.threads_publisher.publish", return_value="p") as pub:
        tc.cross_post(str(folder))
    names = [m.name for m in pub.call_args.args[0]]
    assert names == ["card_01.png", "card_02.mp4"]   # the video replaces its still, as on Instagram


def test_threads_publisher_creates_video_items():
    from src.agents import threads_publisher as tp

    seen = []

    class R:
        def json(self):
            return {"id": "c1"}

    def fake_post(url, params=None, timeout=None):
        seen.append(params)
        return R()

    with patch("src.agents.threads_publisher.httpx.post", fake_post):
        tp._create_carousel_item("https://x/a.mp4", is_video=True)
        tp._create_carousel_item("https://x/a.png")
    assert seen[0]["media_type"] == "VIDEO" and "video_url" in seen[0] and "image_url" not in seen[0]
    assert seen[1]["media_type"] == "IMAGE" and "image_url" in seen[1]


def test_missing_config_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("THREADS_ACCESS_TOKEN", "t")
    monkeypatch.setenv("THREADS_USER_ID", "1")
    plain = tmp_path / "plain"
    _set_cards(plain, ["card_01.png"])
    monkeypatch.setenv("IG_IMAGE_BASE_URL", "catbox")
    with pytest.raises(ValueError, match="https"):
        tc.cross_post(str(plain))


def test_caption_is_trimmed_to_the_threads_limit(tmp_path):
    folder = tmp_path / "long"
    folder.mkdir()
    (folder / "caption.txt").write_text("\n".join("가" * 60 for _ in range(20)), encoding="utf-8")
    text = tc._caption_for(folder)
    assert 0 < len(text) <= tc.THREADS_MAX_CHARS


def test_auto_refresh_only_when_due(monkeypatch):
    monkeypatch.setenv("IG_ACCESS_TOKEN", "x")
    monkeypatch.delenv("THREADS_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("IG_TOKEN_REFRESHED", date.today().isoformat())
    with patch("scripts.refresh_ig_token.main") as run:
        assert tr.ensure_fresh(force=True) == []
        run.assert_not_called()          # fresh token: nothing to do
    from datetime import timedelta
    monkeypatch.setenv("IG_TOKEN_REFRESHED", (date.today() - timedelta(days=55)).isoformat())
    with patch("scripts.refresh_ig_token.main", return_value=0) as run:
        notes = tr.ensure_fresh(force=True)
        run.assert_called_once()
        assert notes and "완료" in notes[0]
    with patch("scripts.refresh_ig_token.main", return_value=2):
        assert "실패" in tr.ensure_fresh(force=True)[0]
