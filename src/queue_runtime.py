from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.db import init_db, resolve_algo_db_path
from src.db_migration_queue import (
    MIGRATION_CHECKSUM,
    MIGRATION_ID,
    QUEUE_COLUMNS,
    migrate_queue_lineage_v2,
)


@dataclass(frozen=True)
class QueueRuntimePreparation:
    db_path: Path
    backup_path: Path | None
    migrated: bool


def _migration_state(path: Path) -> tuple[bool, bool]:
    """Return (current, has_v2_markers) without mutating the database."""
    required = {name for name, _ in QUEUE_COLUMNS}
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    try:
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        queue_columns = (
            {str(row[1]) for row in conn.execute("PRAGMA table_info(queue)")}
            if "queue" in tables
            else set()
        )
        applied = None
        if "schema_migrations" in tables:
            applied = conn.execute(
                "SELECT checksum FROM schema_migrations WHERE migration_id=?",
                (MIGRATION_ID,),
            ).fetchone()
        current = bool(
            applied
            and applied[0] == MIGRATION_CHECKSUM
            and required <= queue_columns
        )
        has_markers = bool(applied or (required & queue_columns))
        return current, has_markers
    finally:
        conn.close()


def _backup_database(path: Path) -> Path:
    backup_dir = path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = backup_dir / f"{path.stem}.pre-{MIGRATION_ID}.{stamp}{path.suffix}"
    source = sqlite3.connect(path)
    target = sqlite3.connect(backup_path)
    try:
        source.backup(target)
        target.commit()
    except Exception:
        backup_path.unlink(missing_ok=True)
        raise
    finally:
        target.close()
        source.close()
    return backup_path


def prepare_queue_runtime(db_path: str | Path | None = None) -> QueueRuntimePreparation:
    """Create/validate Queue V2 explicitly, backing up every legacy DB first."""
    path = Path(db_path) if db_path is not None else resolve_algo_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    existed = path.exists()
    current = False
    has_markers = False
    backup_path: Path | None = None
    if existed:
        current, has_markers = _migration_state(path)
        if not current:
            backup_path = _backup_database(path)

    # A partial/untracked/checksum-mismatched V2 database must fail before
    # init_db can perform any unrelated startup writes.
    if existed and has_markers and not current:
        migrate_queue_lineage_v2(path)

    init_db(path)
    migrate_queue_lineage_v2(path)
    return QueueRuntimePreparation(
        db_path=path,
        backup_path=backup_path,
        migrated=not current,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare and validate Queue Lineage V2")
    parser.add_argument("--db", type=Path, default=None, help="SQLite DB path")
    args = parser.parse_args()
    result = prepare_queue_runtime(args.db)
    print(f"Queue V2 ready: {result.db_path}")
    if result.backup_path:
        print(f"Backup: {result.backup_path}")
    else:
        print("Backup: not required (schema already current or new DB)")


if __name__ == "__main__":
    main()
