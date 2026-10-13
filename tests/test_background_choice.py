from __future__ import annotations

from PIL import Image

from src.agents.image_searcher import _photo_score


def _flat(color):
    return Image.new("RGB", (48, 48), color)


def test_vivid_midtone_beats_near_black_and_washed_out():
    vivid = _photo_score(_flat((40, 140, 220)))[0]
    black = _photo_score(_flat((8, 8, 14)))[0]
    white = _photo_score(_flat((245, 245, 245)))[0]
    assert vivid > black
    assert vivid > white


def test_color_beats_gray_at_the_same_brightness():
    gray = _photo_score(_flat((120, 120, 120)))[0]
    color = _photo_score(_flat((60, 120, 200)))[0]
    assert color > gray


def test_dark_is_the_default_background_style(monkeypatch):
    from src.agents.image_searcher import _bright_style

    monkeypatch.delenv("BACKGROUND_STYLE", raising=False)
    assert _bright_style() is False
    monkeypatch.setenv("BACKGROUND_STYLE", "bright")
    assert _bright_style() is True
