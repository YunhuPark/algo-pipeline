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


def test_stat_box_uses_the_same_korean_notation_as_the_body():
    from types import SimpleNamespace as N

    from src.qa.script_assembler import _localized_stat

    number = N(raw_text="1 million", normalized_value=1000000, subject="robots", unit="")
    body = "아마존은 100만 대 이상의 로봇을 운영하며 매일 배송을 처리합니다."
    assert _localized_stat(number, body) == ("100만 대", "로봇")


def test_stat_box_keeps_the_source_notation_when_the_body_has_no_matching_figure():
    from types import SimpleNamespace as N

    from src.qa.script_assembler import _localized_stat

    number = N(raw_text="1 million", normalized_value=1000000, subject="robots", unit="")
    assert _localized_stat(number, "로봇을 운영합니다.") == ("1 million", "robots")


def test_a_bare_figure_is_not_a_complete_cover_phrase():
    from src.qa.script_assembler import has_complete_figure

    assert not has_complete_figure("80%")
    assert not has_complete_figure("100만")
    assert has_complete_figure("100만 대 이상의 로봇")
    assert has_complete_figure("맥킨지 신입 80% 반영")


def test_a_figure_with_no_object_after_it_is_not_used_as_the_cover_title():
    from types import SimpleNamespace as N

    from src.qa.script_assembler import _lead_figure_phrase

    claim = N(claim_text="AI 시뮬레이션은 컨설턴트가 직면하는 시나리오의 80%를 반영하도록 설계되었습니다.")
    assert _lead_figure_phrase([claim]) == ""


def test_a_year_followed_by_a_particle_is_never_a_cover_phrase():
    # Regression found by the regression harness: "2026년에는 ..." produced the cover title "2026년 에".
    from types import SimpleNamespace as N

    from src.qa.script_assembler import _lead_figure_phrase

    claim = N(claim_text="2026년에는 취약점 악용 사례의 70% 이상이 공개 당일에 공격으로 이어졌다.")
    assert _lead_figure_phrase([claim]) == ""


def test_stacked_particles_are_stripped_before_a_noun_is_accepted():
    from types import SimpleNamespace as N

    from src.qa.script_assembler import _lead_figure_phrase

    assert _lead_figure_phrase([N(claim_text="아마존은 100만 대 이상의 로봇을 운영한다.")]) == "100만 대 이상의 로봇"
    assert _lead_figure_phrase([N(claim_text="지원자 300명에는 면접이 면제된다.")]) == ""
