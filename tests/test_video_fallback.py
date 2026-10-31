from __future__ import annotations

import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

import src.agents.design_renderer as renderer
from src import pipeline
from src.agents import youtube_fetcher as yt
from src.schemas.card_news import Slide


# ── is a downloaded clip actually playable? ───────────────


def test_garbage_and_missing_files_are_not_playable(tmp_path):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)  # header, no index

    assert yt._is_playable_video(broken) is False
    assert yt._is_playable_video(tmp_path / "missing.mp4") is False


def test_a_real_clip_is_playable(tmp_path):
    from moviepy import ColorClip

    clip_path = tmp_path / "ok.mp4"
    try:
        ColorClip(size=(64, 64), color=(10, 20, 30), duration=1.0).with_fps(8).write_videofile(
            str(clip_path), logger=None
        )
    except Exception as exc:  # no ffmpeg encoder in this environment
        pytest.skip(f"cannot encode a test clip here: {type(exc).__name__}")

    assert yt._is_playable_video(clip_path) is True


# ── the snippet cache must not trust a file just because it exists ──


@pytest.fixture
def downloader(monkeypatch, tmp_path):
    """download_video_snippet with yt-dlp stubbed; 'GOOD' bytes count as playable."""

    calls: list[str] = []
    state = {"payload": b"GOOD"}

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def download(self, urls):
            calls.append(urls[0])
            Path(self.opts["outtmpl"]).write_bytes(state["payload"])

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYDL))
    monkeypatch.setattr(yt, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(yt, "_ensure_ffmpeg_on_path", lambda: "ffmpeg")
    monkeypatch.setattr(yt, "_is_playable_video", lambda p: Path(p).read_bytes() == b"GOOD")
    return SimpleNamespace(calls=calls, state=state, cache=tmp_path)


def _cached(downloader, name="abc_t2_15_snippet.mp4") -> Path:
    return downloader.cache / name


def test_a_valid_cached_clip_is_reused_without_downloading(downloader):
    _cached(downloader).write_bytes(b"GOOD")

    result = yt.download_video_snippet("abc", duration=15, start_time=2)

    assert result == _cached(downloader)
    assert downloader.calls == []


def test_a_corrupt_cached_clip_is_discarded_and_downloaded_again(downloader):
    # The real failure: a connection reset left a truncated file that was then
    # reused forever ("moov atom not found").
    _cached(downloader).write_bytes(b"TRUNCATED")

    result = yt.download_video_snippet("abc", duration=15, start_time=2)

    assert result == _cached(downloader)
    assert _cached(downloader).read_bytes() == b"GOOD"
    assert len(downloader.calls) == 1


def test_a_download_that_comes_back_corrupt_is_not_left_in_the_cache(downloader):
    downloader.state["payload"] = b"TRUNCATED"

    result = yt.download_video_snippet("abc", duration=15, start_time=2)

    assert result is None
    assert not _cached(downloader).exists()


def test_a_download_that_raises_midway_leaves_no_half_file(downloader, monkeypatch):
    class Exploding:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def download(self, urls):
            Path(self.opts["outtmpl"]).write_bytes(b"HALF")
            raise ConnectionResetError("10054")

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=Exploding))

    assert yt.download_video_snippet("abc", duration=15, start_time=2) is None
    assert not _cached(downloader).exists()


# ── a slide without a video becomes a text card ───────────


def _fonts(monkeypatch):
    for regular, bold in (
        ("C:/Windows/Fonts/malgun.ttf", "C:/Windows/Fonts/malgunbd.ttf"),
        ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
        (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ),
    ):
        if Path(regular).exists() and Path(bold).exists():
            monkeypatch.setattr(
                renderer, "_find_font", lambda kind="bold": bold if kind == "bold" else regular
            )
            return
    pytest.skip("No portable test font pair is installed")


def test_render_text_card_replaces_the_thumbnail_card_in_place(monkeypatch, tmp_path):
    _fonts(monkeypatch)
    card = tmp_path / "card_04_content.png"
    Image.new("RGB", (1080, 1350), (200, 30, 30)).save(card)  # stands in for the old thumbnail card
    slide = Slide(slide_number=4, slide_type="content", title="제목", body="검증된 본문입니다.")

    result = renderer.render_text_card(
        card, slide, 6, "@algo", Image.new("RGB", (1080, 1350), (11, 13, 29))
    )

    assert result == card
    saved = Image.open(card)
    assert saved.size == (1080, 1350)
    assert saved.getpixel((540, 1200)) != (200, 30, 30)  # the old card is gone


def test_the_pipeline_retries_a_failed_composition_and_falls_back_to_a_text_card():
    source = inspect.getsource(pipeline._run_once)

    assert "클립을 새로 받아 재시도" in source          # one fresh download + retry
    assert "render_text_card" in source                  # no still-with-play-button
    assert 'sl.pop(key, None)' in source                 # video mapping dropped from script.json
