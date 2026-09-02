from __future__ import annotations

import sqlite3

import pytest

from src.db_migration_queue import MIGRATION_ID, QueueMigrationError
from src.queue_runtime import prepare_queue_runtime


def _legacy_db(path):
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version=1")
        conn.execute("CREATE TABLE queue (id INTEGER PRIMARY KEY, topic TEXT NOT NULL)")
        conn.execute("INSERT INTO queue(topic) VALUES ('legacy')")


def test_prepare_backs_up_legacy_db_and_is_idempotent(tmp_path, monkeypatch):
    path = tmp_path / "queue-runtime.db"
    _legacy_db(path)
    monkeypatch.setenv("ALGO_ENV", "test")
    monkeypatch.setenv("ALGO_DB_PATH", str(path))

    first = prepare_queue_runtime(path)
    assert first.migrated is True
    assert first.backup_path is not None and first.backup_path.exists()
    with sqlite3.connect(first.backup_path) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 1
        assert backup.execute("SELECT topic FROM queue").fetchone()[0] == "legacy"

    second = prepare_queue_runtime(path)
    assert second.migrated is False
    assert second.backup_path is None
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE migration_id=?",
            (MIGRATION_ID,),
        ).fetchone()[0] == 1


def test_prepare_new_db_requires_no_backup(tmp_path, monkeypatch):
    path = tmp_path / "new-runtime.db"
    monkeypatch.setenv("ALGO_ENV", "test")
    monkeypatch.setenv("ALGO_DB_PATH", str(path))

    result = prepare_queue_runtime(path)
    assert result.migrated is True
    assert result.backup_path is None


def test_prepare_backs_up_then_rejects_untracked_partial_schema(tmp_path, monkeypatch):
    path = tmp_path / "partial-runtime.db"
    _legacy_db(path)
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE queue ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'")
    monkeypatch.setenv("ALGO_ENV", "test")
    monkeypatch.setenv("ALGO_DB_PATH", str(path))

    with pytest.raises(QueueMigrationError, match="untracked partial"):
        prepare_queue_runtime(path)
    backups = list((path.parent / "backups").glob("*.db"))
    assert len(backups) == 1
