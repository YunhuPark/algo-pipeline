from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


MIGRATION_ID = "queue_lineage_v2"
QUEUE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("metadata_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("collection_method", "TEXT NOT NULL DEFAULT 'LEGACY_UNVERIFIED'"),
    ("lineage_hash", "TEXT DEFAULT NULL"),
    ("metadata_schema_version", "INTEGER NOT NULL DEFAULT 0"),
    ("ig_post_id", "TEXT DEFAULT NULL"),
    ("retry_count", "INTEGER NOT NULL DEFAULT 0"),
    ("publish_error_code", "TEXT DEFAULT NULL"),
    ("publish_attempt_id", "TEXT DEFAULT NULL"),
    ("publish_started_at", "TEXT DEFAULT NULL"),
    ("publish_attempt_state", "TEXT NOT NULL DEFAULT 'NOT_ATTEMPTED'"),
)

CANONICAL_SQL = "\n".join(
    [
        "CREATE TABLE schema_migrations (migration_id TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)",
        *(f"ALTER TABLE queue ADD COLUMN {name} {definition}" for name, definition in QUEUE_COLUMNS),
    ]
)
MIGRATION_CHECKSUM = hashlib.sha256(CANONICAL_SQL.encode("utf-8")).hexdigest()


class QueueMigrationError(RuntimeError):
    pass


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def migrate_queue_lineage_v2(db_path: str | Path) -> None:
    path = Path(db_path)
    if not path.exists():
        raise QueueMigrationError(f"database does not exist: {path}")

    conn = sqlite3.connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if "queue" not in {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }:
            raise QueueMigrationError("queue table is missing")

        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                   migration_id TEXT PRIMARY KEY,
                   checksum TEXT NOT NULL,
                   applied_at TEXT NOT NULL
               )"""
        )
        applied = conn.execute(
            "SELECT checksum FROM schema_migrations WHERE migration_id=?", (MIGRATION_ID,)
        ).fetchone()
        existing = _columns(conn, "queue")
        required = {name for name, _ in QUEUE_COLUMNS}

        if applied:
            if applied[0] != MIGRATION_CHECKSUM:
                raise QueueMigrationError("migration checksum mismatch")
            missing = required - existing
            if missing:
                raise QueueMigrationError(f"applied migration has missing columns: {sorted(missing)}")
            conn.commit()
            return

        present = required & existing
        if present:
            state = "full" if present == required else "partial"
            raise QueueMigrationError(f"untracked {state} queue lineage schema")

        for name, definition in QUEUE_COLUMNS:
            conn.execute(f"ALTER TABLE queue ADD COLUMN {name} {definition}")

        missing = required - _columns(conn, "queue")
        if missing:
            raise QueueMigrationError(f"schema verification failed: {sorted(missing)}")

        conn.execute(
            "INSERT INTO schema_migrations (migration_id, checksum, applied_at) VALUES (?,?,?)",
            (MIGRATION_ID, MIGRATION_CHECKSUM, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
