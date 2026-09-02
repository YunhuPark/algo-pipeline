from pathlib import Path


RUNBOOK = (
    Path(__file__).parents[1] / "docs" / "runbooks" / "fact-checker-v2.md"
).read_text(encoding="utf-8")


def test_runbook_documents_mandatory_v2_gates_and_double_check():
    assert "SourceLineage" in RUNBOOK
    assert "schema version `2.0`" in RUNBOOK
    assert "DeterministicVerifier" in RUNBOOK
    assert "SemanticCritic" in RUNBOOK
    assert "again immediately before the publisher call" in RUNBOOK
    assert "`fact_check=False`" in RUNBOOK
    assert "does not bypass verification" in RUNBOOK


def test_runbook_documents_fail_closed_operations():
    assert "publisher must receive zero calls" in RUNBOOK
    assert "`CRITIC_PARSE_ERROR`" in RUNBOOK
    assert "`FACT_CHECK_REPORT_INVALID`" in RUNBOOK
    assert "Do not retry a malformed or unsupported claim automatically" in RUNBOOK
    assert "Do not re-enable a legacy fact-check adapter" in RUNBOOK


def test_runbook_verification_uses_isolated_tests_only():
    assert '$env:ALGO_ENV = "test"' in RUNBOOK
    assert "tests/test_fact_checker_guards.py" in RUNBOOK
    assert "must not call live LLM, search, Instagram, or Meta APIs" in RUNBOOK
    assert "no repository or production SQLite database" in RUNBOOK
