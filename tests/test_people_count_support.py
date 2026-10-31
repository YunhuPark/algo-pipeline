from __future__ import annotations

from types import SimpleNamespace as N

from src.qa.deterministic_verifier import _number_supported_by_evidence as supported

HUMANS = (
    "Amazon is the second-largest corporate employer in the world, with more than "
    "1.5 million full-time and part-time human workers, according to its annual report."
)


def _num(raw, unit="people", value=1500000):
    return N(raw_text=raw, unit=unit, normalized_value=value)


def test_headcount_with_modifiers_before_the_noun_is_supported():
    # Regression: "150만 명" was rejected 15+ times because the evidence says
    # "1.5 million full-time and part-time human workers".
    assert supported(_num("150만 명"), HUMANS)
    assert supported(_num("1.5 million", unit=""), HUMANS)


def test_same_value_for_a_non_person_thing_is_still_rejected():
    robots = "Amazon has deployed more than 1.5 million robots across its warehouses."
    assert not supported(_num("150만 명"), robots)


def test_a_different_value_is_still_rejected():
    assert not supported(_num("200만 명", value=2000000), HUMANS)


def test_a_people_noun_far_from_the_number_does_not_count():
    far = "It reached 1.5 million units shipped last quarter across many different regions and the company also has workers."
    assert not supported(_num("150만 명"), far)


def test_hyphenated_unit_in_an_english_compound_modifier_is_read():
    # Regression: "a 10-minute conversation" was parsed without its unit, so the claim "10분" was
    # rejected as unsupported on every retry (13 paid retries on one article).
    from src.qa.deterministic_verifier import _extract_number_mentions

    assert list(_extract_number_mentions("a 10-minute conversation"))[0][1] == "minute"
    evidence = "McKinsey asks candidates to have a 10-minute conversation with an AI-simulated client."
    assert supported(N(raw_text="10분", unit="minutes", normalized_value=10), evidence)


def test_number_ranges_and_dates_are_not_changed_by_the_hyphen_rule():
    from decimal import Decimal

    from src.qa.deterministic_verifier import _extract_number_mentions

    values = [v for v, _unit in _extract_number_mentions("from 10-20 percent in 2025-2026")]
    assert Decimal("10") in values and Decimal("20") in values
    assert Decimal("2025") in values and Decimal("2026") in values
