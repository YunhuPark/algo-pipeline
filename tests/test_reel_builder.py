import json

import pytest
from PIL import Image

from src.agents import reel_builder as rb


def test_slides_stay_as_long_as_their_text_needs():
    assert rb.slide_seconds("cover", 999) == rb.COVER_SECONDS
    assert rb.slide_seconds("cta", 999) == rb.CTA_SECONDS
    short = rb.slide_seconds("content", 10)
    long = rb.slide_seconds("content", 400)
    assert rb.MIN_CONTENT <= short < long <= rb.MAX_CONTENT


def test_a_long_set_is_scaled_down_together_to_the_limit():
    slides = [("cover", 20)] + [("content", 400)] * 12 + [("cta", 30)]
    seconds = rb.plan_durations(slides)
    assert sum(seconds) <= rb.MAX_SECONDS + 0.5
    assert seconds[0] < seconds[1]                      # relative lengths are kept
    assert rb.plan_durations([("cover", 0), ("content", 60), ("cta", 0)]) == [
        rb.COVER_SECONDS, rb.slide_seconds("content", 60), rb.CTA_SECONDS,
    ]


def test_text_sizes_come_from_script_json_and_fall_back_when_missing(tmp_path):
    folder = tmp_path / "set"
    folder.mkdir()
    assert rb._text_sizes(folder, 3) == [("cover", 0), ("content", 60), ("cta", 0)]
    slides = [
        {"slide_type": "cover", "title": "표지", "body": "후크"},
        {"slide_type": "content", "title": "소제목", "body": "본문" * 20},
        {"slide_type": "cta", "title": "질문", "body": "저장"},
    ]
    (folder / "script.json").write_text(json.dumps({"slides": slides}, ensure_ascii=False), encoding="utf-8")
    sizes = rb._text_sizes(folder, 3)
    assert [kind for kind, _ in sizes] == ["cover", "content", "cta"] and sizes[1][1] == 3 + 40


def _card_set(folder, n=3):
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(1, n + 1):
        kind = "cover" if i == 1 else "cta" if i == n else "content"
        Image.new("RGB", (108, 135), (30 * i, 40, 90)).save(folder / f"card_{i:02d}_{kind}.png")


def test_a_reel_is_built_at_the_target_size_and_passes_the_checks(tmp_path):
    folder = tmp_path / "set"
    _card_set(folder)
    path = rb.build_reel(folder, size=(108, 192), fps=8)
    assert path is not None and path.exists()
    assert rb.reel_issues(path, size=(108, 192)) == []
    assert rb.reel_issues(path) != []                  # the real 1080x1920 requirement is not met by this tiny file


def test_fewer_than_two_cards_make_no_reel(tmp_path):
    folder = tmp_path / "one"
    _card_set(folder, n=1)
    assert rb.build_reel(folder, size=(108, 192)) is None


def test_a_missing_reel_is_reported(tmp_path):
    assert rb.reel_issues(tmp_path / "nope.mp4") == ["릴스 파일이 없습니다"]
