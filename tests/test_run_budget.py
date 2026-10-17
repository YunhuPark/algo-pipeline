from __future__ import annotations

import pytest

from src import usage_tracker
from src.cancellation import GenerationCancelled, RunBudgetExceeded


@pytest.fixture(autouse=True)
def _tracking_on(monkeypatch):
    monkeypatch.setenv("ALGO_USAGE_TRACKING", "on")   # tracking is off by default under pytest
    monkeypatch.delenv("MAX_RUN_COST_USD", raising=False)


def test_default_cap_is_20_cents(monkeypatch):
    assert usage_tracker.max_run_cost() == 0.20
    monkeypatch.setenv("MAX_RUN_COST_USD", "0.5")
    assert usage_tracker.max_run_cost() == 0.5
    monkeypatch.setenv("MAX_RUN_COST_USD", "garbage")
    assert usage_tracker.max_run_cost() == 0.20


def test_over_the_cap_stops_new_paid_calls(monkeypatch):
    monkeypatch.setattr(usage_tracker, "current_run_spend", lambda: 0.69)
    with pytest.raises(RunBudgetExceeded):
        usage_tracker.check_run_budget()


def test_under_the_cap_and_disabled_cap_do_nothing(monkeypatch):
    monkeypatch.setattr(usage_tracker, "current_run_spend", lambda: 0.05)
    usage_tracker.check_run_budget()
    monkeypatch.setattr(usage_tracker, "current_run_spend", lambda: 5.0)
    monkeypatch.setenv("MAX_RUN_COST_USD", "0")
    usage_tracker.check_run_budget()


def test_budget_stop_is_not_swallowed_by_except_exception(monkeypatch):
    monkeypatch.setattr(usage_tracker, "current_run_spend", lambda: 1.0)

    def retrying_step():
        try:
            usage_tracker.check_run_budget()
        except Exception:      # the pipeline has many of these "log and retry" blocks
            return "swallowed"
        return "ran"

    with pytest.raises(GenerationCancelled):
        retrying_step()


def test_llm_call_is_refused_before_spending_when_over_budget(monkeypatch):
    from src import llm_provider

    monkeypatch.setattr(usage_tracker, "current_run_spend", lambda: 0.9)
    model = llm_provider.ProviderAwareChatOpenAI(model="gpt-4o-mini", api_key="sk-test")
    with pytest.raises(RunBudgetExceeded):
        model._generate([])


def test_spend_is_only_counted_for_an_active_generation_run(monkeypatch):
    assert usage_tracker.current_run_spend() == 0.0   # no scope -> nothing to cap


def test_score_failures_are_told_apart_from_formatting_rule_failures():
    from src.agents.content_creator import is_editorial_score_failure
    from src.qa.deterministic_verifier import QualityGateError

    score = QualityGateError("EDITORIAL_QUALITY_FAILED", "다음 항목이 기준(7.0점) 미달입니다: 후킹력(6.0점)")
    rule = QualityGateError("EDITORIAL_QUALITY_FAILED", "다음 규칙을 지켜서 다시 작성해주세요: - 슬라이드6 제목이 말줄임표로 끝남")
    other = QualityGateError("CLAIM_NUMBER_UNSUPPORTED", "숫자 오류")

    assert is_editorial_score_failure(score)
    assert not is_editorial_score_failure(rule)      # cheap to fix -> keeps its normal retries
    assert not is_editorial_score_failure(other)
