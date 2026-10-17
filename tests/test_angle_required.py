from __future__ import annotations

import inspect

from src import pipeline
from src.agents import angle_selector
from src.agents.angle_selector import (
    CANONICAL_ANGLES,
    DEFAULT_ANGLE,
    SelectedAngle,
    ensure_valid_angle,
    fallback_angle,
)


def _angle(name: str) -> SelectedAngle:
    return SelectedAngle(angle=name, cover_title="c", hook="h", reasoning="r")


def test_default_angle_is_a_canonical_angle():
    assert DEFAULT_ANGLE in CANONICAL_ANGLES


def test_fallback_angle_is_never_empty():
    angle = fallback_angle("엔데버 카탈리스트 3억 2천만 달러 펀드 조성", "RateLimitError")

    assert angle.angle == DEFAULT_ANGLE
    assert "RateLimitError" in angle.reasoning
    assert angle.cover_title  # derived from the topic, never blank


def test_valid_angle_is_kept_as_selected():
    selected = _angle("몰랐던사실")

    assert ensure_valid_angle(selected, "topic") is selected


def test_unknown_or_missing_angle_falls_back_to_default():
    assert ensure_valid_angle(None, "topic").angle == DEFAULT_ANGLE
    assert ensure_valid_angle(_angle(""), "topic").angle == DEFAULT_ANGLE
    assert ensure_valid_angle(_angle("엉뚱한앵글"), "topic").angle == DEFAULT_ANGLE


def test_every_documented_angle_is_accepted():
    for name in ("리스트형", "Before/After", "즉시실행", "몰랐던사실",
                 "공포", "공감", "이익", "사회증거"):
        assert ensure_valid_angle(_angle(name), "topic").angle == name


def test_pipeline_no_longer_gates_angle_on_topic_keywords():
    source = inspect.getsource(pipeline._run_once)

    # The old gate skipped angle selection for topics without trigger keywords.
    assert "should_select_angle" not in source
    assert "ensure_valid_angle(selected_angle, topic)" in source
    assert "fallback_angle(topic" in source


def test_selector_exports_used_by_the_pipeline_exist():
    assert callable(angle_selector.ensure_valid_angle)
    assert callable(angle_selector.fallback_angle)
