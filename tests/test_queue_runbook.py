from pathlib import Path


RUNBOOK = (
    Path(__file__).parents[1]
    / "docs"
    / "runbooks"
    / "queue-lineage-v2-migration.md"
).read_text(encoding="utf-8")


def test_runbook_documents_backup_migration_and_rollback():
    assert "python -m src.queue_runtime --db data/algo.db" in RUNBOOK
    assert "data/backups/" in RUNBOOK
    assert "checksum mismatch" in RUNBOOK
    assert "## Rollback" in RUNBOOK


def test_runbook_never_recommends_automatic_uncertain_retry():
    assert "UNCERTAIN_EMPTY_POST_ID" in RUNBOOK
    assert "REMOTE_PUBLISH_PERSISTENCE_UNCERTAIN" in RUNBOOK
    assert "do not retry it automatically" in RUNBOOK
