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


def test_topic_tag_is_validated_and_sent_with_the_container():
    from src.agents import threads_publisher as tp

    assert tp.normalize_topic_tag("#AI Threads") == "AI Threads"
    assert tp.normalize_topic_tag("a.b") == "" and tp.normalize_topic_tag("a&b") == ""
    assert tp.normalize_topic_tag("x" * 51) == "" and tp.normalize_topic_tag("  ") == ""

    seen = []

    class R:
        def json(self):
            return {"id": "c"}

    def fake_post(url, params=None, timeout=None):
        seen.append(params)
        return R()

    with patch("src.agents.threads_publisher.httpx.post", fake_post):
        tp._create_carousel_container(["1", "2"], "cap", "AI Threads")
        tp._create_carousel_container(["1", "2"], "cap", "bad.tag")
    assert seen[0]["topic_tag"] == "AI Threads"
    assert "topic_tag" not in seen[1]


def test_cross_post_passes_the_ai_threads_tag_by_default(tmp_path, monkeypatch):
    folder = tmp_path / "set"
    _set_cards(folder, ["card_01.png", "card_02.png"])
    monkeypatch.setenv("THREADS_ACCESS_TOKEN", "t")
    monkeypatch.setenv("THREADS_USER_ID", "1")
    monkeypatch.setenv("IG_IMAGE_BASE_URL", "https://example.test")
    monkeypatch.delenv("THREADS_TOPIC_TAG", raising=False)
    with patch("src.agents.threads_publisher.publish", return_value="p") as pub:
        tc.cross_post(str(folder))
    assert pub.call_args.kwargs["topic_tag"] == "AI Threads"


def _row(status="published", image_dir="C:/out/set1"):
    return {"id": 28, "status": status, "image_dir": image_dir, "topic": "주제"}


def test_queue_item_cross_post_records_the_post_and_refuses_duplicates(monkeypatch):
    recorded = []
    posts = []
    monkeypatch.setattr("src.db.get_queue_row", lambda qid: _row())
    monkeypatch.setattr("src.db.get_posts", lambda platform=None, limit=50, include_deleted=False: posts)
    monkeypatch.setattr("src.db.insert_post", lambda **kw: recorded.append(kw) or 1)
    monkeypatch.setattr(tc, "cross_post", lambda image_dir: "th-1")
    monkeypatch.setattr(tc, "_log", lambda message: None)

    ok, message = tc.cross_post_queue_item(28)
    assert ok and "th-1" in message
    assert recorded[0]["platform"] == "threads" and recorded[0]["post_id"] == "th-1"

    posts.append({"image_dir": "C:/out/set1", "post_id": "th-1"})   # now it is in the posts table
    ok, message = tc.cross_post_queue_item(28)
    assert not ok and "이미 Threads" in message
    assert len(recorded) == 1                                          # no second post, no second record


def test_queue_item_cross_post_only_after_instagram_and_reports_failures(monkeypatch):
    logged = []
    monkeypatch.setattr("src.db.get_queue_row", lambda qid: _row(status="ready"))
    monkeypatch.setattr("src.db.get_posts", lambda **kw: [])
    ok, message = tc.cross_post_queue_item(28)
    assert not ok and "Instagram에 게시되지 않아" in message

    monkeypatch.setattr("src.db.get_queue_row", lambda qid: _row())
    monkeypatch.setattr(tc, "_log", logged.append)

    def boom(image_dir):
        raise RuntimeError("Threads 미디어 처리 타임아웃")

    monkeypatch.setattr(tc, "cross_post", boom)
    ok, message = tc.cross_post_queue_item(28)
    assert not ok and "Instagram은 게시됨" in message and "타임아웃" in message
    assert logged and "타임아웃" in logged[0]                           # a durable trace exists


def test_credentials_come_from_the_env_file_not_the_process_environment(tmp_path, monkeypatch):
    from src import threads_credentials as creds

    env_file = tmp_path / ".env"
    env_file.write_text("THREADS_ACCESS_TOKEN=algo-token\nTHREADS_USER_ID=111\n", encoding="utf-8")
    monkeypatch.setattr(creds, "ENV_PATH", env_file)
    monkeypatch.setenv("THREADS_ACCESS_TOKEN", "OTHER-PROJECT-TOKEN")   # e.g. a Windows user-level variable
    monkeypatch.setenv("THREADS_USER_ID", "999")
    assert creds.load() == ("algo-token", "111")


def test_a_token_from_the_wrong_account_never_posts(monkeypatch):
    from src import threads_credentials as creds

    class R:
        def __init__(self, data):
            self._d = data

        def json(self):
            return self._d

    monkeypatch.setattr(creds, "expected_username", lambda: "algo__kr")
    monkeypatch.setattr(creds.httpx, "get", lambda *a, **k: R({"id": "28185725201104923", "username": "onetool_maker"}))
    with pytest.raises(ValueError, match="달라"):
        creds.assert_expected_account("tok", "28185725201104923")
    monkeypatch.setattr(creds.httpx, "get", lambda *a, **k: R({"id": "111", "username": "algo__kr"}))
    assert creds.assert_expected_account("tok", "111") == "algo__kr"
    with pytest.raises(ValueError):                                     # right name but a different id
        creds.assert_expected_account("tok", "222")
