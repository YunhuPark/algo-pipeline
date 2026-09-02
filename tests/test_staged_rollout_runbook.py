from pathlib import Path


ROOT = Path(__file__).parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
RUNBOOK = (ROOT / "docs" / "runbooks" / "staged-rollout.md").read_text(
    encoding="utf-8"
)


def test_readme_documents_fail_closed_automation_defaults():
    assert "AGENT_AUTO_UPLOAD=false" in README
    assert "AGENT_DRY_RUN=true" in README
    assert "staged-rollout.md" in README


def test_rollout_requires_migration_and_supervised_publish_first():
    assert "python -m src.queue_runtime --db data/algo.db" in RUNBOOK
    assert "python main.py --queue 1" in RUNBOOK
    assert "python main.py --queue-publish --publish" in RUNBOOK
    assert RUNBOOK.index("Phase 1") < RUNBOOK.index("Phase 3")
    assert RUNBOOK.index("Phase 3") < RUNBOOK.index("Phase 5")


def test_rollout_never_authorizes_automatic_attempt_reset():
    assert "Never clear attempt fields to retry" in RUNBOOK
    assert "unattended publication must remain disabled" in RUNBOOK
