from __future__ import annotations

import pytest
from PIL import Image

import src.agents.design_renderer as renderer
from src.schemas.card_news import Slide
from tests.test_visual_layout_rules import _use_test_fonts

BODY = "근거로 확인된 문장입니다. " * 3


def _slide(visual_type, values=None, labels=None, body=BODY):
    return Slide(
        slide_number=3, slide_type="content", title="제목 예시", body=body,
        visual_type=visual_type, visual_values=values or [], visual_labels=labels or [],
    )


def _bg():
    return renderer._apply_background(Image.new("RGB", (1080, 1350), (90, 120, 160)))


def _top_of(render):
    renderer._BODY_TOP_PROBE.clear()
    render()
    assert renderer._BODY_TOP_PROBE, "layout never drew a body box"
    return renderer._BODY_TOP_PROBE[-1]


def test_every_card_layout_starts_its_body_at_the_same_height(monkeypatch):
    """Regression: the body box sat higher on some cards than others, so reading
    across a set made the eye jump. All layouts must start at BODY_TOP."""

    _use_test_fonts(monkeypatch)
    thumb = Image.new("RGB", (1080, 607), (30, 30, 30))
    tops = {
        "statement (entity)": _top_of(lambda: renderer._render_content(_bg(), _slide("entity", body=BODY), 6, "@h")),
        "statement (short)": _top_of(lambda: renderer._render_content(_bg(), _slide("impact", body="짧은 문장."), 6, "@h")),
        "hero_stat": _top_of(lambda: renderer._render_content(_bg(), _slide("hero_stat", ["1 million"], ["robots"]), 6, "@h")),
        "comparison": _top_of(lambda: renderer._render_content(
            _bg(), _slide("comparison", ["1 million", "1.5 million"], ["robots", "workers"]), 6, "@h")),
        "process": _top_of(lambda: renderer._render_content(
            _bg(), _slide("process", ["40%"], ["tokens per megawatt"],
                          body="첫째 단계입니다. 둘째 단계입니다. 셋째 단계입니다. 넷째 단계입니다."), 6, "@h")),
        "split (video)": _top_of(lambda: renderer._render_split(_bg(), _slide("auto"), 6, "@h", thumb)),
    }
    video = tops.pop("split (video)")
    assert video == renderer.BODY_TOP, video               # 영상 카드: 영상 아래
    assert set(tops.values()) == {renderer.TEXT_BODY_TOP}, tops   # 영상 없는 카드는 모두 같은 줄


def test_content_background_is_dimmed_and_blurred_not_brightened():
    base = Image.new("RGB", (1080, 1350), (200, 200, 200))
    out = renderer._apply_background(base)
    mean = sum(out.convert("L").resize((16, 16)).getdata()) / 256
    assert mean < 130, mean   # a bright photo must not stay bright behind the text


def test_title_and_body_type_is_the_same_on_every_card(monkeypatch):
    """Regression: titles and body text came out in different sizes and shapes
    depending on the card type. Short and typical cards must share one size."""

    _use_test_fonts(monkeypatch)
    from PIL import ImageDraw

    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    sizes = set()
    for title, body in (
        ("짧은 제목", "짧은 본문입니다."),
        ("조금 더 긴 제목 예시입니다", "근거로 확인된 문장입니다. " * 4),
        ("제목", "중간 길이의 본문 문장입니다. " * 3),
    ):
        title_font, _, _, body_font, _, _, _ = renderer._text_panel_layout(title, body, draw)
        sizes.add((title_font.size, body_font.size))
    assert sizes == {(renderer.TITLE_FONT_SIZE, renderer.BODY_FONT_SIZE)}, sizes
