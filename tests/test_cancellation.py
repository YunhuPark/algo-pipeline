from __future__ import annotations

import pytest

from src import cancellation
from src.cancellation import GenerationCancelled


@pytest.fixture(autouse=True)
def _clean_flag():
    cancellation.reset()
    yield
    cancellation.reset()


def test_flag_lifecycle():
    assert not cancellation.is_requested()
    cancellation.raise_if_requested()  # no-op while not requested
    cancellation.request_cancel()
    assert cancellation.is_requested()
    with pytest.raises(GenerationCancelled):
        cancellation.raise_if_requested()
    cancellation.reset()
    assert not cancellation.is_requested()


def test_cancellation_is_not_swallowed_by_except_exception():
    def noisy_stage():
        try:
            cancellation.raise_if_requested()
        except Exception:  # the pipeline has many of these
            return "swallowed"
        return "ran"

    cancellation.request_cancel()
    with pytest.raises(GenerationCancelled):
        noisy_stage()


def test_llm_call_is_blocked_before_any_spending(monkeypatch):
    from src import llm_provider

    called = []
    monkeypatch.setattr(
        llm_provider.ProviderAwareChatOpenAI,
        "_primary_generate_marker",
        lambda *a, **k: called.append(1),
        raising=False,
    )
    model = llm_provider.ProviderAwareChatOpenAI(model="gpt-4o-mini", api_key="sk-test")
    cancellation.request_cancel()
    with pytest.raises(GenerationCancelled):
        model._generate([])
    assert called == []


def test_cancel_endpoint_rejects_when_idle(monkeypatch):
    from src.dashboard import app as dashboard

    monkeypatch.setattr(dashboard, "_ACTIVE_QUEUE_JOB", None)
    resp = dashboard.app.test_client().post("/queue/cancel")
    assert resp.status_code == 409
    assert not cancellation.is_requested()


def test_cancel_endpoint_refuses_to_stop_a_publish(monkeypatch):
    from src.dashboard import app as dashboard

    monkeypatch.setattr(dashboard, "_ACTIVE_QUEUE_JOB", {"job_id": "x", "mode": "publish"})
    resp = dashboard.app.test_client().post("/queue/cancel")
    assert resp.status_code == 409
    assert not cancellation.is_requested()


def test_cancel_endpoint_flags_a_running_prepare(monkeypatch):
    from src.dashboard import app as dashboard

    monkeypatch.setattr(dashboard, "_ACTIVE_QUEUE_JOB", {"job_id": "j1", "mode": "prepare"})
    resp = dashboard.app.test_client().post("/queue/cancel")
    assert resp.status_code == 200
    assert cancellation.is_requested()
