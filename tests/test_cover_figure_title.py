from __future__ import annotations

from types import SimpleNamespace as N

from src.qa.script_assembler import _hashtags, _lead_figure_phrase


def _claim(text):
    return N(claim_text=text, entities=[])


def test_figure_phrase_is_cut_from_the_verified_claim_text():
    claims = [_claim("아마존은 전 세계에서 가장 많은 로봇을 운영하며, 100만 대 이상의 로봇이 매일 배송을 처리합니다.")]
    assert _lead_figure_phrase(claims) == "100만 대 이상의 로봇"


def test_headcount_phrase():
    assert _lead_figure_phrase([_claim("아마존은 150만 명 이상의 인간 노동자를 고용하고 있습니다.")]) == "150만 명 이상의 인간 노동자"


def test_no_figure_returns_nothing():
    assert _lead_figure_phrase([_claim("로봇 도입으로 작업 환경이 바뀝니다.")]) == ""


def test_person_name_is_not_a_hashtag():
    claim = N(entities=["Brian Greenwood", "Amazon", "Maia 300"])
    tags = _hashtags("아마존 로봇", [claim])
    assert "#BrianGreenwood" not in tags
    assert "#Amazon" in tags


def test_unnatural_korean_figures_are_normalized():
    from src.qa.script_assembler import _natural_korean_figures

    assert _natural_korean_figures("아마존 로봇 1백만 대") == "아마존 로봇 100만 대"
    assert _natural_korean_figures("2천만 명") == "2000만 명"
    assert _natural_korean_figures("100만 대") == "100만 대"


def test_a_bare_number_without_its_unit_is_not_a_figure_phrase():
    from src.qa.script_assembler import _FIGURE_PHRASE_RE

    assert not _FIGURE_PHRASE_RE.search("로봇 운영 규모 100만")
    assert _FIGURE_PHRASE_RE.search("100만 대 이상의 로봇")
