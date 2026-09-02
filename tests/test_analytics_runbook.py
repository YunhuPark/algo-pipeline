from pathlib import Path


RUNBOOK = (
    Path(__file__).parents[1] / "docs" / "runbooks" / "analytics-v2.md"
).read_text(encoding="utf-8")


def test_runbook_documents_isolated_database_and_api_guards():
    assert '$env:ALGO_ENV = "test"' in RUNBOOK
    assert "$env:TRACKING_DB_PATH" in RUNBOOK
    assert "Missing or invalid tokens return 401" in RUNBOOK
    assert "missing or invalid origins return 403" in RUNBOOK
    assert "optimistic concurrency conflicts return 409" in RUNBOOK


def test_runbook_never_recommends_automatic_policy_activation():
    assert "does not call `activate_policy`" in RUNBOOK
    assert "alter allocation" in RUNBOOK
    assert "There is no implicit retry" in RUNBOOK
    assert "publisher, policy activation, allocation mutation" in RUNBOOK


def test_runbook_documents_statistical_boundaries_and_rollback():
    assert "at least 30 total records and 15 records" in RUNBOOK
    assert "provisional before 48 hours" in RUNBOOK
    assert "## Rollback" in RUNBOOK
    assert "Do not delete state events" in RUNBOOK
