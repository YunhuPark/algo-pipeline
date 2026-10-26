from __future__ import annotations

import numpy as np
import pytest

from src.agents.youtube_fetcher import has_visible_motion


def _write_clip(path, make_frame, duration=15.0, fps=24):
    from moviepy import VideoClip

    clip = VideoClip(make_frame, duration=duration)
    clip.write_videofile(str(path), fps=fps, codec="libx264", logger=None, audio=False)
    clip.close()


def test_frozen_title_card_clip_has_no_motion(tmp_path):
    """Reproduces the exact real-world bug: a YouTube chapter timestamp whose
    transcript matches the slide but whose picture is a static title card for
    the whole downloaded segment - has_visible_motion() must catch this so
    the pipeline falls back to an image instead of publishing a frozen
    "video"."""
    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    frame[:, :] = (30, 60, 90)

    def make_frame(t):
        return frame

    path = tmp_path / "frozen.mp4"
    _write_clip(path, make_frame)

    assert has_visible_motion(path) is False


def test_clip_with_real_motion_passes(tmp_path):
    def make_frame(t):
        frame = np.zeros((64, 64, 3), dtype=np.uint8)
        # 시간에 따라 확실히 달라지는 밝기 - 실제 화면 변화를 흉내낸다
        value = int((t * 20) % 255)
        frame[:, :] = (value, value, value)
        return frame

    path = tmp_path / "moving.mp4"
    _write_clip(path, make_frame)

    assert has_visible_motion(path) is True


def test_missing_file_defaults_to_pass_through(tmp_path):
    """If the check itself fails (corrupt/missing file), don't block the
    existing behavior - let the caller's normal error handling take over."""
    assert has_visible_motion(tmp_path / "does-not-exist.mp4") is True
