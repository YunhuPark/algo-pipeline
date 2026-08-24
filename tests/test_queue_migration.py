from __future__ import annotations

import sqlite3

import pytest

from src.db_migration_queue import (
    MIGRATION_CHECKSUM,
    MIGRATION_ID,
    QUEUE_COLUMNS,
    QueueMigrationError,
    migrate_queue_lineage_v2,
)


def _db(tmp_path):
    path = tmp_path / "algo-test.db"
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version=1")
        conn.execute("CREATE TABLE queue (id INTEGER PRIMARY KEY, topic TEXT NOT NULL)")
        conn.execute("INSERT INTO queue(topic) VALUES ('legacy')")
        conn.execute("CREATE TABLE sentinel (value TEXT)")
        conn.execute("INSERT INTO sentinel VALUES ('keep')")
    return path


def _columns(path):
    with sqlite3.connect(path) as conn:
        return {row[1] for row in conn.execute("PRAGMA table_info(queue)")}


def test_success_preserves_legacy_rows_and_user_version(tmp_path):
    path = _db(tmp_path)
    migrate_queue_lineage_v2(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert conn.execute("SELECT topic FROM queue").fetchone()[0] == "legacy"
        assert conn.execute("SELECT value FROM sentinel").fetchone()[0] == "keep"
        assert conn.execute(
            "SELECT checksum FROM schema_migrations WHERE migration_id=?", (MIGRATION_ID,)
        ).fetchone()[0] == MIGRATION_CHECKSUM
    assert {name for name, _ in QUEUE_COLUMNS} <= _columns(path)


def test_idempotent_and_preserves_other_migrations(tmp_path):
    path = _db(tmp_path)
    migrate_queue_lineage_v2(path)
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO schema_migrations VALUES ('other', 'abc', 'now')")
    migrate_queue_lineage_v2(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 2


def test_checksum_mismatch_fails_closed(tmp_path):
    path = _db(tmp_path)
    migrate_queue_lineage_v2(path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE schema_migrations SET checksum='bad' WHERE migration_id=?", (MIGRATION_ID,)
        )
    with pytest.raises(QueueMigrationError, match="checksum mismatch"):
        migrate_queue_lineage_v2(path)


@pytest.mark.parametrize("count", [1, len(QUEUE_COLUMNS)])
def test_untracked_partial_or_full_schema_fails_closed(tmp_path, count):
    path = _db(tmp_path)
    with sqlite3.connect(path) as conn:
        for name, definition in QUEUE_COLUMNS[:count]:
            conn.execute(f"ALTER TABLE queue ADD COLUMN {name} {definition}")
    with pytest.raises(QueueMigrationError, match="untracked"):
        migrate_queue_lineage_v2(path)


def test_applied_but_missing_column_fails_closed(tmp_path):
    path = _db(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE schema_migrations (migration_id TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO schema_migrations VALUES (?,?,?)", (MIGRATION_ID, MIGRATION_CHECKSUM, "now")
        )
    with pytest.raises(QueueMigrationError, match="missing columns"):
        migrate_queue_lineage_v2(path)


def test_failure_rolls_back_columns_and_migration_row(tmp_path):
    path = _db(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """CREATE TABLE schema_migrations (
                   migration_id TEXT PRIMARY KEY,
                   checksum TEXT NOT NULL CHECK (checksum = 'forced-failure'),
                   applied_at TEXT NOT NULL
               )"""
        )
    with pytest.raises(sqlite3.IntegrityError):
        migrate_queue_lineage_v2(path)
    assert _columns(path) == {"id", "topic"}
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 0
