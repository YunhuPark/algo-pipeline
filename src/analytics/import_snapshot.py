from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from src.analytics.db_experiments import tracking_db_path
from src.db_factory import get_connection


TRACKING_DB_PATH: Path | None = None
MATURE_WINDOW_HOURS = 48


def _path() -> Path:
    return Path(TRACKING_DB_PATH) if TRACKING_DB_PATH is not None else tracking_db_path()


def import_performance_snapshot(
    publication_id: str,
    *,
    measured_at: datetime,
    publication_at: datetime,
    reach: int,
    saves: int,
    shares: int,
    likes: int,
    comments: int,
    source_type: str,
    metric_definition_version: str,
    import_idempotency_key: str,
    experiment_id: str | None = None,
    variant_id: str | None = None,
) -> int:
    """Import a non-live performance snapshot with an explicit provenance key."""

    if measured_at < publication_at:
        raise ValueError("measured_at cannot precede publication_at")
    metrics = (reach, saves, shares, likes, comments)
    if any(value < 0 for value in metrics):
        raise ValueError("performance metrics cannot be negative")
    if not publication_id.strip() or not import_idempotency_key.strip():
        raise ValueError("publication_id and import_idempotency_key are required")
    if not source_type.strip() or not metric_definition_version.strip():
        raise ValueError("source_type and metric_definition_version are required")

    age_hours = (measured_at - publication_at).total_seconds() / 3600.0
    is_provisional = int(age_hours < MATURE_WINDOW_HOURS or reach <= 0)

    try:
        with get_connection(_path()) as conn:
            cursor = conn.execute(
                """
                INSERT INTO performance_snapshots (
                    publication_id, measured_at, publication_at,
                    reach, saves, shares, likes, comments,
                    source_type, metric_definition_version,
                    import_idempotency_key, experiment_id, variant_id,
                    is_provisional
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    publication_id.strip(),
                    measured_at.isoformat(),
                    publication_at.isoformat(),
                    reach,
                    saves,
                    shares,
                    likes,
                    comments,
                    source_type.strip(),
                    metric_definition_version.strip(),
                    import_idempotency_key.strip(),
                    experiment_id,
                    variant_id,
                    is_provisional,
                ),
            )
            return int(cursor.lastrowid)
    except sqlite3.IntegrityError as exc:
        if "unique" in str(exc).lower():
            return -1
        raise
