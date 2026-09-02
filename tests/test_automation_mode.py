from __future__ import annotations

import pytest

from src.automation_mode import resolve_automation_mode


def test_automation_defaults_fail_closed(monkeypatch):
    for name in ("ALGO_ENV", "AGENT_AUTO_UPLOAD", "AGENT_DRY_RUN"):
        monkeypatch.delenv(name, raising=False)

    mode = resolve_automation_mode()

    assert mode.auto_upload is False
    assert mode.dry_run is True
    assert mode.live_publish is False


def test_live_automation_requires_production(monkeypatch):
    monkeypatch.setenv("ALGO_ENV", "test")
    monkeypatch.setenv("AGENT_AUTO_UPLOAD", "true")
    monkeypatch.setenv("AGENT_DRY_RUN", "false")

    with pytest.raises(RuntimeError, match="ALGO_ENV=production"):
        resolve_automation_mode()


def test_live_automation_requires_all_explicit_flags(monkeypatch):
    monkeypatch.setenv("ALGO_ENV", "production")
    monkeypatch.setenv("AGENT_AUTO_UPLOAD", "true")
    monkeypatch.setenv("AGENT_DRY_RUN", "false")

    mode = resolve_automation_mode()

    assert mode.live_publish is True


def test_invalid_boolean_fails_closed(monkeypatch):
    monkeypatch.setenv("AGENT_AUTO_UPLOAD", "sometimes")

    with pytest.raises(RuntimeError, match="explicit boolean"):
        resolve_automation_mode()
