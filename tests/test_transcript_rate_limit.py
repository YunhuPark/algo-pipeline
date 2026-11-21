"""YouTube answers 429 when subtitles are requested too fast. The fetcher must back off, pause instead of
hammering, and never remember a rate-limited video as "has no transcript"."""
import os
import sys
import types

import pytest

from src.agents import youtube_fetcher as yf

VTT = "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nHello world\n\n00:00:03.000 --> 00:00:05.000\nSecond line\n"


@pytest.fixture
def fake(monkeypatch, tmp_path):
    state = types.SimpleNamespace(calls=0, fail_times=0, error="ERROR: HTTP Error 429: Too Many Requests", sleeps=[])

    class FakeDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def download(self, urls):
            state.calls += 1
            if state.calls <= state.fail_times:
                raise Exception(state.error)
            folder = os.path.dirname(self.opts["outtmpl"])
            with open(os.path.join(folder, "vid.en.vtt"), "w", encoding="utf-8") as fh:
                fh.write(VTT)

    module = types.ModuleType("yt_dlp")
    module.YoutubeDL = FakeDL
    monkeypatch.setitem(sys.modules, "yt_dlp", module)
    monkeypatch.setattr(yf, "_TRANSCRIPT_CACHE_DIR", tmp_path)
    monkeypatch.setattr(yf, "_transcript_lang", lambda video_id: "en")
    monkeypatch.setattr(yf, "_TRANSCRIPT_INTERVAL", 0)
    monkeypatch.setattr(yf, "_sleep", state.sleeps.append)
    monkeypatch.setattr(yf, "_rate_limited_until", 0.0)
    monkeypatch.setattr(yf, "_transcript_cache", {})
    return state


def test_a_single_429_is_waited_out_and_the_transcript_is_still_fetched(fake):
    fake.fail_times = 1
    text = yf._get_video_transcript("vidA")
    assert "Hello world" in text
    assert fake.sleeps == [20] and fake.calls == 2


def test_persistent_429_starts_a_cooldown_and_other_videos_are_not_requested(fake):
    fake.fail_times = 99
    with pytest.raises(RuntimeError):
        yf._get_video_transcript("vidA")
    assert fake.sleeps == [20, 60] and fake.calls == 3            # tried, backed off twice, gave up
    assert yf._cooldown_remaining() > 0

    before = fake.calls
    with pytest.raises(RuntimeError, match="paused"):
        yf._get_video_transcript("vidB")
    assert fake.calls == before                                   # no request while the pause lasts


def test_a_rate_limited_video_is_not_remembered_as_having_no_transcript(fake, tmp_path):
    fake.fail_times = 99
    with pytest.raises(RuntimeError):
        yf._get_video_transcript("vidA")
    assert "vidA" not in yf._transcript_cache and not (tmp_path / "vidA.txt").exists()

    fake.fail_times = 0
    yf._rate_limited_until = 0.0                                  # the pause is over
    assert "Hello world" in yf._get_video_transcript("vidA")      # fetched now instead of failing for good


def test_other_errors_are_not_retried_and_are_remembered(fake, tmp_path):
    fake.fail_times = 99
    fake.error = "ERROR: Video unavailable"
    with pytest.raises(RuntimeError):
        yf._get_video_transcript("vidC")
    assert fake.calls == 1 and fake.sleeps == []                  # no pointless waiting
    assert yf._cooldown_remaining() == 0
    assert (tmp_path / "vidC.txt").exists()                       # a real failure is cached as before
