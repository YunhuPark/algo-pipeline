from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageChops

import src.agents.design_renderer as renderer
from src.schemas.card_news import Slide


def _slide(visual_type: str, body: str, values=None, labels=None) -> Slide:
    return Slide(
        slide_number=3,
        slide_type="content",
        title="제목",
        body=body,
        visual_type=visual_type,
        visual_values=values or [],
        visual_labels=labels or [],
    )


def _resolve(slide: Slide):
    return renderer._resolve_visual_layout(
        slide, list(slide.visual_values), list(slide.visual_labels)
    )


# ── process: needs real steps ─────────────────────────────


def test_single_sentence_is_not_split_into_process_steps():
    body = (
        "주요 SaaS 플랫폼에 AI 기술이 결합되면서 DevSecOps와 AIOps 등 "
        "IT 운영 모델 전반의 생산성이 대폭 향상되고 있습니다."
    )

    assert len(body) > 44  # the old code halved any sentence longer than this
    assert renderer._split_verified_clauses(body) == [body.rstrip(".")]
    assert _resolve(_slide("process", body)) == ("entity", [], [])


def test_real_steps_keep_the_process_layout():
    body = "자료를 수집하고, 근거를 검증한 뒤, 결과를 발표했습니다."

    assert _resolve(_slide("process", body))[0] == "process"


# ── hero_stat: exactly one number ─────────────────────────


def test_single_value_keeps_the_hero_layout():
    slide = _slide("hero_stat", "매출이 48억 달러였습니다.", ["48억 달러"], ["매출"])

    assert _resolve(slide) == ("hero_stat", ["48억 달러"], ["매출"])


def test_duplicate_values_count_as_one():
    slide = _slide("hero_stat", "생산성이 40% 올랐습니다.", ["40%", "40%"], ["a", "b"])

    assert _resolve(slide)[0:2] == ("hero_stat", ["40%", "40%"])


@pytest.mark.parametrize(
    "body",
    [
        "생산성이 30%에서 40% 수준까지 향상됐습니다.",
        "생산성이 30%~40% 향상됐습니다.",
        "생산성이 30%-40% 향상됐습니다.",
    ],
)
def test_a_stated_range_is_shown_as_one_range(body):
    slide = _slide("hero_stat", body, ["40%", "30%"], ["라벨", "라벨"])

    assert _resolve(slide) == ("hero_stat", ["30~40%"], ["라벨"])


def test_two_unrelated_values_do_not_silently_drop_one():
    # Old behaviour showed only values[0] and hid the other number.
    slide = _slide(
        "hero_stat",
        "기업가치는 48억 달러이고 투자금은 20억 달러입니다.",
        ["48억 달러", "20억 달러"],
        ["가치", "투자"],
    )

    assert _resolve(slide) == ("entity", [], [])


def test_values_with_different_units_are_never_merged_into_a_range():
    slide = _slide(
        "hero_stat", "만족도는 30%에서 40점까지 올랐습니다.", ["30%", "40점"], ["a", "b"]
    )

    assert _resolve(slide)[0] == "entity"


def test_other_layouts_are_untouched():
    slide = _slide("comparison", "A와 B를 비교했습니다.", ["1", "2"], ["a", "b"])

    assert _resolve(slide) == ("comparison", ["1", "2"], ["a", "b"])


# ── vertical balance ──────────────────────────────────────


def test_content_block_is_centered_below_the_card_middle():
    group_h = 300

    top = renderer._balanced_top(group_h, floor=0)

    assert top == (renderer.H - group_h) // 2 + renderer.OPTICAL_CENTER_SHIFT


def test_block_never_rises_into_the_headline():
    assert renderer._balanced_top(300, floor=900) == 900


def test_block_never_falls_into_the_footer():
    group_h = 1000
    bottom = renderer.H - 92

    top = renderer._balanced_top(group_h, floor=0, bottom=bottom)

    assert top + group_h <= bottom


def test_tall_blocks_still_respect_the_floor_over_the_footer():
    # If the block cannot fit at all, the headline clearance wins (no overlap).
    assert renderer._balanced_top(1200, floor=300) == 300


def test_fitted_body_panel_is_shorter_than_the_legacy_box():
    legacy_height = (renderer.H - 92) - 850
    short = "근거 문장이 짧은 경우입니다."
    long = "근거 문장이 길어서 여러 줄로 이어지는 경우입니다. " * 4

    assert renderer._body_panel_height(short) < legacy_height
    assert renderer._body_panel_height(short) < renderer._body_panel_height(long)


# ── rendering ─────────────────────────────────────────────


def _use_test_fonts(monkeypatch):
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


def test_fallback_card_renders_exactly_like_the_plain_statement_layout(monkeypatch):
    _use_test_fonts(monkeypatch)
    background = Image.new("RGB", (renderer.W, renderer.H), (11, 13, 29))
    body = (
        "주요 SaaS 플랫폼에 AI 기술이 결합되면서 DevSecOps와 AIOps 등 "
        "IT 운영 모델 전반의 생산성이 대폭 향상되고 있습니다."
    )

    process = renderer._render_content(background, _slide("process", body), 6, "@algo")
    statement = renderer._render_content(background, _slide("entity", body), 6, "@algo")

    assert ImageChops.difference(process, statement).getbbox() is None
