from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from src import llm_provider, usage_tracker


@pytest.fixture
def tracking(monkeypatch, tmp_path):
    path = tmp_path / "tracking.db"
    monkeypatch.setenv("ALGO_ENV", "test")
    monkeypatch.setenv("TRACKING_DB_PATH", str(path))
    monkeypatch.setenv("ALGO_USAGE_TRACKING", "on")  # off by default under pytest
    monkeypatch.delenv("LLM_PRICES_JSON", raising=False)

    from src import db_tracking

    monkeypatch.setattr(db_tracking, "TRACKING_DB_PATH", path)
    db_tracking.init_tracking_db(path)
    return path


def _rows(path: Path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM api_usage ORDER BY id").fetchall()
    finally:
        conn.close()


def _result(tokens_in: int, tokens_out: int) -> ChatResult:
    message = AIMessage(
        content="ok",
        usage_metadata={
            "input_tokens": tokens_in,
            "output_tokens": tokens_out,
            "total_tokens": tokens_in + tokens_out,
        },
    )
    return ChatResult(generations=[ChatGeneration(message=message)])


# ── pricing ───────────────────────────────────────────────


def test_known_models_are_priced_and_dated_snapshots_use_the_longest_match():
    assert usage_tracker.price_for("gpt-4o-mini") == (0.15, 0.60)
    assert usage_tracker.price_for("gpt-4o") == (2.50, 10.00)
    # Must not be priced as "gpt-4o" (16x more expensive on input).
    assert usage_tracker.price_for("gpt-4o-mini-2024-07-18") == (0.15, 0.60)


def test_unknown_model_has_no_price_rather_than_a_zero_price():
    assert usage_tracker.price_for("gemini-3.8-flash") is None
    assert usage_tracker.estimate_cost("gemini-3.8-flash", 1000, 1000) is None


def test_cost_is_computed_per_million_tokens():
    assert usage_tracker.estimate_cost("gpt-4o-mini", 1_000_000, 1_000_000) == 0.75
    assert usage_tracker.estimate_cost("gpt-4o-mini", 1000, 500) == pytest.approx(0.00045)


def test_missing_token_counts_mean_unknown_cost_not_zero():
    assert usage_tracker.estimate_cost("gpt-4o-mini", None, None) is None


def test_prices_can_be_overridden_and_bad_overrides_are_ignored(monkeypatch):
    monkeypatch.setenv("LLM_PRICES_JSON", '{"gemini-3.8-flash": [0.0, 0.0]}')
    assert usage_tracker.estimate_cost("gemini-3.8-flash", 5000, 5000) == 0.0

    monkeypatch.setenv("LLM_PRICES_JSON", "{not json")
    assert usage_tracker.price_for("gpt-4o-mini") == (0.15, 0.60)


# ── reading usage ─────────────────────────────────────────


def test_usage_is_read_from_message_metadata_or_llm_output():
    assert usage_tracker.usage_from_result(_result(120, 30)) == (120, 30)

    legacy = SimpleNamespace(
        generations=[], llm_output={"token_usage": {"prompt_tokens": 7, "completion_tokens": 3}}
    )
    assert usage_tracker.usage_from_result(legacy) == (7, 3)
    assert usage_tracker.usage_from_result(SimpleNamespace(generations=[], llm_output=None)) == (None, None)
    assert usage_tracker.usage_from_result(None) == (None, None)


# ── recording ─────────────────────────────────────────────


def test_a_priced_call_is_stored_with_its_cost_and_scope(tracking):
    with usage_tracker.usage_scope("generation", "run-1"):
        usage_tracker.record_llm_call(
            provider="openai", model="gpt-4o-mini",
            input_tokens=1000, output_tokens=500, stage="agents.x.fn",
        )

    (row,) = _rows(tracking)
    assert (row["scope"], row["run_id"], row["stage"]) == ("generation", "run-1", "agents.x.fn")
    assert row["cost_usd"] == pytest.approx(0.00045)
    assert row["ok"] == 1


def test_an_unpriced_call_keeps_its_tokens_and_a_null_cost(tracking):
    usage_tracker.record_llm_call(
        provider="fallback", model="gemini-3.8-flash",
        input_tokens=2000, output_tokens=100, stage="s",
    )

    (row,) = _rows(tracking)
    assert (row["input_tokens"], row["output_tokens"]) == (2000, 100)
    assert row["cost_usd"] is None


def test_a_failed_call_is_recorded_without_a_cost(tracking):
    usage_tracker.record_llm_call(
        provider="openai", model="gpt-4o-mini", ok=False, error="RateLimitError", stage="s"
    )

    (row,) = _rows(tracking)
    assert (row["ok"], row["error"], row["cost_usd"]) == (0, "RateLimitError", None)


def test_scope_is_restored_and_visible_from_worker_threads(tracking):
    with usage_tracker.usage_scope("collection"):
        with usage_tracker.usage_scope("generation", "run-2"):
            worker = threading.Thread(
                target=usage_tracker.record_llm_call,
                kwargs={"provider": "openai", "model": "gpt-4o", "stage": "t",
                        "input_tokens": 1, "output_tokens": 1},
            )
            worker.start()
            worker.join()
        usage_tracker.record_llm_call(provider="openai", model="gpt-4o", stage="t",
                                      input_tokens=1, output_tokens=1)
    usage_tracker.record_llm_call(provider="openai", model="gpt-4o", stage="t",
                                  input_tokens=1, output_tokens=1)

    assert [(r["scope"], r["run_id"]) for r in _rows(tracking)] == [
        ("generation", "run-2"),
        ("collection", None),
        ("", None),
    ]


def test_stage_is_the_first_src_function_above_the_llm_layer(monkeypatch, tracking):
    monkeypatch.setattr(
        usage_tracker, "_SRC_ROOT", os.path.normcase(str(Path(__file__).resolve().parent))
    )

    usage_tracker.record_llm_call(provider="openai", model="gpt-4o", input_tokens=1, output_tokens=1)

    assert _rows(tracking)[0]["stage"] == "test_usage_tracker.test_stage_is_the_first_src_function_above_the_llm_layer"


def test_recording_never_raises_even_without_a_database(monkeypatch):
    monkeypatch.setenv("ALGO_ENV", "test")
    monkeypatch.setenv("ALGO_USAGE_TRACKING", "on")
    monkeypatch.delenv("TRACKING_DB_PATH", raising=False)

    usage_tracker.record_llm_call(provider="openai", model="gpt-4o")  # must not raise


def test_nothing_is_recorded_during_tests_unless_a_test_opts_in(monkeypatch, tmp_path):
    # Guards the production database: this very mistake once wrote a mocked
    # RateLimitError from test_llm_provider_fallback into data/tracking.db.
    path = tmp_path / "tracking.db"
    monkeypatch.setenv("ALGO_ENV", "test")
    monkeypatch.setenv("TRACKING_DB_PATH", str(path))
    monkeypatch.delenv("ALGO_USAGE_TRACKING", raising=False)
    from src import db_tracking

    monkeypatch.setattr(db_tracking, "TRACKING_DB_PATH", path)
    db_tracking.init_tracking_db(path)

    assert usage_tracker.tracking_enabled() is False
    usage_tracker.record_llm_call(provider="openai", model="gpt-4o", stage="s")

    assert _rows(path) == []


def test_the_switch_can_force_tracking_on_or_off(monkeypatch):
    monkeypatch.setenv("ALGO_USAGE_TRACKING", "on")
    assert usage_tracker.tracking_enabled() is True
    monkeypatch.setenv("ALGO_USAGE_TRACKING", "off")
    assert usage_tracker.tracking_enabled() is False


# ── per-run totals ────────────────────────────────────────


def test_run_total_is_a_number_only_when_every_call_was_priced(tracking):
    with usage_tracker.usage_scope("generation", "run-a"):
        usage_tracker.record_llm_call(provider="openai", model="gpt-4o-mini",
                                      input_tokens=1000, output_tokens=500, stage="s")
        usage_tracker.record_llm_call(provider="openai", model="gpt-4o-mini",
                                      input_tokens=1000, output_tokens=500, stage="s")
    totals = usage_tracker.run_totals("run-a")
    assert totals["calls"] == 2
    assert totals["cost_usd"] == pytest.approx(0.0009)
    assert totals["unpriced_calls"] == 0

    with usage_tracker.usage_scope("generation", "run-b"):
        usage_tracker.record_llm_call(provider="openai", model="gpt-4o-mini",
                                      input_tokens=1000, output_tokens=500, stage="s")
        usage_tracker.record_llm_call(provider="fallback", model="gemini-3.8-flash",
                                      input_tokens=9000, output_tokens=900, stage="s")
    mixed = usage_tracker.run_totals("run-b")
    assert mixed["unpriced_calls"] == 1
    assert mixed["cost_usd"] is None                       # a partial sum would understate it
    assert mixed["priced_cost_usd"] == pytest.approx(0.00045)
    assert mixed["input_tokens"] == 10000


def test_a_run_with_no_recorded_calls_reports_nothing(tracking):
    totals = usage_tracker.run_totals("never-ran")

    assert totals["calls"] == 0
    assert totals["cost_usd"] is None


def test_end_of_run_stores_the_real_cost_not_the_old_default_zero(tracking):
    from src import db_tracking
    from src.schemas.content_package import PipelineResult
    from src.schemas.queue_schemas import PublishAttemptState
    from src.services.generation_service import _finish_tracking

    result = PipelineResult(
        image_paths=[], generation_succeeded=True, publish_requested=False,
        publish_succeeded=False, ig_post_id=None, permalink=None, failure_stage=None,
        error_code=None, publish_attempt_state=PublishAttemptState.NOT_ATTEMPTED,
        publish_attempt_id=None,
    )

    priced = db_tracking.start_run("t-priced")
    with usage_tracker.usage_scope("generation", priced):
        usage_tracker.record_llm_call(provider="openai", model="gpt-4o",
                                      input_tokens=1_000_000, output_tokens=0, stage="s")
    unpriced = db_tracking.start_run("t-unpriced")
    with usage_tracker.usage_scope("generation", unpriced):
        usage_tracker.record_llm_call(provider="fallback", model="gemini-3.8-flash",
                                      input_tokens=10, output_tokens=10, stage="s")
    silent = db_tracking.start_run("t-silent")  # no calls recorded at all

    for run_id in (priced, unpriced, silent):
        _finish_tracking(run_id, result, 1.0, "strategy")

    conn = sqlite3.connect(tracking)
    cost = {
        run_id: conn.execute("SELECT cost_usd FROM content_runs WHERE run_id=?", (run_id,)).fetchone()[0]
        for run_id in (priced, unpriced, silent)
    }
    conn.close()
    assert cost[priced] == pytest.approx(2.50)
    assert cost[unpriced] is None
    assert cost[silent] is None


# ── the LLM choke point ───────────────────────────────────


def _llm():
    return llm_provider.ProviderAwareChatOpenAI(model="gpt-4o-mini", api_key="test-key")


def test_a_successful_openai_call_is_recorded(monkeypatch, tracking):
    monkeypatch.setattr(
        llm_provider._OriginalChatOpenAI, "_generate", lambda self, *a, **k: _result(1000, 500)
    )

    _llm().invoke("hello")

    (row,) = _rows(tracking)
    assert (row["provider"], row["model"], row["ok"]) == ("openai", "gpt-4o-mini", 1)
    assert (row["input_tokens"], row["output_tokens"]) == (1000, 500)
    assert row["cost_usd"] == pytest.approx(0.00045)


class _RateLimit(Exception):
    """Named so llm_provider treats it as a provider failure."""


def test_a_failure_without_a_fallback_is_recorded_then_re_raised(monkeypatch, tracking):
    def boom(self, *a, **k):
        raise _RateLimit("rate limit exceeded")

    monkeypatch.setattr(llm_provider._OriginalChatOpenAI, "_generate", boom)
    monkeypatch.setattr(llm_provider, "fallback_is_configured", lambda: False)
    monkeypatch.setattr(llm_provider, "_clear_primary_outage", lambda: None)
    monkeypatch.setattr(llm_provider, "_mark_primary_down", lambda: None)

    with pytest.raises(_RateLimit):
        _llm().invoke("hello")

    (row,) = _rows(tracking)
    assert (row["ok"], row["error"]) == (0, "_RateLimit")


def test_openai_failure_then_fallback_success_records_both_calls(monkeypatch, tracking):
    def boom(self, *a, **k):
        raise _RateLimit("rate limit exceeded")

    fallback = SimpleNamespace(
        model_name="gemini-3.8-flash", _generate=lambda *a, **k: _result(4000, 200)
    )
    monkeypatch.setattr(llm_provider._OriginalChatOpenAI, "_generate", boom)
    monkeypatch.setattr(llm_provider, "_primary_likely_down", lambda: False)
    monkeypatch.setattr(llm_provider, "_mark_primary_down", lambda: None)
    monkeypatch.setattr(
        llm_provider.ProviderAwareChatOpenAI, "_fallback_model", lambda self: fallback
    )

    _llm().invoke("hello")

    primary, secondary = _rows(tracking)
    assert (primary["provider"], primary["ok"]) == ("openai", 0)
    assert (secondary["provider"], secondary["model"], secondary["ok"]) == (
        "fallback", "gemini-3.8-flash", 1,
    )
    assert secondary["input_tokens"] == 4000
    assert secondary["cost_usd"] is None   # Gemini has no configured price


def test_tracking_failures_cannot_break_an_llm_call(monkeypatch):
    monkeypatch.setattr(
        llm_provider._OriginalChatOpenAI, "_generate", lambda self, *a, **k: _result(1, 1)
    )
    monkeypatch.setenv("ALGO_ENV", "test")
    monkeypatch.delenv("TRACKING_DB_PATH", raising=False)  # recording will fail

    assert _llm().invoke("hello").content == "ok"
