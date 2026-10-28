"""The key-figure box ("핵심 수치 / 80%", "광고 서비스 / 14개국") must hold its content centred.

Regression: the content sat ~14px low in its box because its position came from fixed offsets
(+42, +30, ...) instead of the measured glyph height. These tests measure the rendered pixels.
"""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

import src.agents.design_renderer as renderer
from src.schemas.card_news import Slide
from tests.test_visual_layout_rules import _use_test_fonts

BODY = "근거로 확인된 문장입니다. " * 2


def _slide(visual_type, values, labels):
    return Slide(
        slide_number=3, slide_type="content", title="제목 예시", body=BODY,
        visual_type=visual_type, visual_values=values, visual_labels=labels,
    )


def _render(slide):
    renderer._VISUAL_PROBE.clear()
    bg = renderer._apply_background(Image.new("RGB", (1080, 1350), (20, 24, 40)))
    img = renderer._render_content(bg, slide, 6, "@h")
    assert renderer._VISUAL_PROBE, "the layout never recorded its box"
    return img, renderer._VISUAL_PROBE[-1]


def _measured_offset(img, box) -> float:
    """Pixel centre of the content inside the box minus the box's centre (px; 0 = centred)."""
    top, bottom = box
    area = np.asarray(img.convert("RGB"), dtype=np.int16)[top + 8:bottom - 8, renderer.PAD + 24:1080 - renderer.PAD - 24]
    rows = np.where(area.max(axis=(1, 2)) - area.min(axis=(1, 2)) > 70)[0]      # rows holding text or the pill
    assert rows.size, "no content found inside the box"
    ink_top, ink_bottom = rows.min() + top + 8, rows.max() + top + 8
    return (ink_top + ink_bottom) / 2 - (top + bottom) / 2


@pytest.mark.parametrize(
    "values,labels",
    [
        (["80%"], ["핵심 수치"]),
        (["14개국"], ["광고 서비스"]),
        (["10억 달러"], ["연간 환산 매출"]),
        (["1,130만 달러 절감액"], ["Topgolf"]),         # a long figure that wraps or shrinks
    ],
)
def test_single_figure_box_is_vertically_centred(monkeypatch, values, labels):
    _use_test_fonts(monkeypatch)
    img, probe = _render(_slide("hero_stat", values, labels))
    planned = (probe["content"][0] + probe["content"][1]) / 2 - (probe["box"][0] + probe["box"][1]) / 2
    assert abs(planned) <= 2, f"the plan itself is off-centre by {planned}px"
    assert abs(_measured_offset(img, probe["box"])) <= 5, "the rendered figure box is off-centre"


@pytest.mark.parametrize(
    "values,labels",
    [
        (["89개국", "26개국"], ["광고 서비스", "추가"]),
        (["150만 명", "100만 대"], ["직원", "로봇"]),
        (["3.5배", "12%"], ["성능 향상 폭이 큰 신형 칩", "전력 절감"]),   # one label wraps to two lines
    ],
)
def test_two_figure_box_is_vertically_centred(monkeypatch, values, labels):
    _use_test_fonts(monkeypatch)
    img, probe = _render(_slide("comparison", values, labels))
    planned = (probe["content"][0] + probe["content"][1]) / 2 - (probe["box"][0] + probe["box"][1]) / 2
    assert abs(planned) <= 2, f"the plan itself is off-centre by {planned}px"
    assert abs(_measured_offset(img, probe["box"])) <= 5, "the rendered comparison box is off-centre"
