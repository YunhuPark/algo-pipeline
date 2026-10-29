import json
from pathlib import Path

import pytest

from scripts import make_devlog
from src.agents import devlog_cards
from src.qa.output_standard import blocking, check_output
from tests.test_visual_layout_rules import _use_test_fonts

EPISODE = Path(__file__).resolve().parents[1] / "docs" / "devlog" / "ep01.json"


@pytest.fixture
def episode():
    return json.loads(EPISODE.read_text(encoding="utf-8"))


def test_the_caption_follows_the_house_style(episode):
    caption = devlog_cards.caption_for(episode)
    assert caption.splitlines()[0] == episode["topic"]
    assert "출처: 알고 운영 기록 · 2026.10.29" in caption
    bullets = [line for line in caption.splitlines() if line.startswith("· ")]
    assert len(bullets) == len(episode["caption_bullets"]) >= 2
    assert "." not in caption.replace("2026.10.29", "")          # no sentence-final periods
    assert "#알고" in caption and len(episode["hashtags"]) <= 10


def test_an_episode_renders_a_reviewable_set_that_passes_the_output_standard(tmp_path, monkeypatch, episode):
    _use_test_fonts(monkeypatch)
    folder = devlog_cards.render_devlog(episode, tmp_path)
    cards = sorted(folder.glob("card_*.png"))
    assert len(cards) == 2 + len(episode["slides"])
    assert cards[0].name.endswith("_cover.png") and cards[-1].name.endswith("_cta.png")
    for name in ("caption.txt", "script.json", "layout.json", "evidence.json"):
        assert (folder / name).exists()
    assert blocking(check_output(folder)) == []


def test_every_checkable_figure_must_match_live_data(monkeypatch, episode):
    monkeypatch.setattr(make_devlog, "_live_retries", lambda: (202, 115))
    assert make_devlog.verify(episode) == []
    monkeypatch.setattr(make_devlog, "_live_retries", lambda: (230, 131))      # the numbers moved on
    problems = make_devlog.verify(episode)
    assert len(problems) == 2 and all("현재" in p for p in problems)


def test_each_headline_figure_has_a_recorded_source(episode):
    sources = " ".join(episode["evidence"])
    for figure in ("279", "115", "64%", "55%", "도달 0", "팔로워 2명"):
        assert figure in sources, f"{figure} has no recorded source"
