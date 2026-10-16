from __future__ import annotations

import pytest

from src.agents import design_renderer as dr


class _Flaky:
    def __init__(self, failures):
        self.failures = failures
        self.calls = 0

    def invoke(self, _prompt):
        self.calls += 1
        if self.calls <= self.failures:
            raise ConnectionError("Connection error.")
        return "ok"


def test_transient_connection_errors_are_retried(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    llm = _Flaky(failures=2)
    assert dr._invoke_with_retry(llm, "p") == "ok"
    assert llm.calls == 3


def test_a_persistent_error_is_raised_after_the_retries(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    llm = _Flaky(failures=10)
    with pytest.raises(ConnectionError):
        dr._invoke_with_retry(llm, "p")
    assert llm.calls == 3
