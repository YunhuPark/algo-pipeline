from __future__ import annotations

import sqlite3
from pathlib import Path

from src.analytics.db_experiments import tracking_db_path
from src.db_factory import get_connection


TRACKING_DB_PATH: Path | None = None


def _path() -> Path:
    return Path(TRACKING_DB_PATH) if TRACKING_DB_PATH is not None else tracking_db_path()


def log_editorial_feedback(
    *,
    content_id: str,
    run_id: str,
    editor_id: str,
    approval_decision: str,
    edit_reason_category: str = "",
    text_edit_ratio: float = 0.0,
    claim_correction_count: int = 0,
    editorial_effort_score: float | None = None,
    experiment_id: str | None = None,
    variant_id: str | None = None,
    idempotency_key: str | None = None,
) -> int:
    """Record one human editorial event; duplicate imports are ignored."""

    if not all(value.strip() for value in (content_id, run_id, editor_id)):
        raise ValueError("content_id, run_id, and editor_id are required")
    if not 0.0 <= text_edit_ratio <= 1.0:
        raise ValueError("text_edit_ratio must be between 0 and 1")
    if claim_correction_count < 0:
        raise ValueError("claim_correction_count cannot be negative")
    effort = (
        float(editorial_effort_score)
        if editorial_effort_score is not None
        else float(text_edit_ratio + claim_correction_count)
    )
    if effort < 0:
        raise ValueError("editorial_effort_score cannot be negative")

    try:
        with get_connection(_path()) as conn:
            cursor = conn.execute(
                """
                INSERT INTO editorial_feedback_events (
                    content_id, run_id, editor_id, approval_decision,
                    edit_reason_category, text_edit_ratio,
                    claim_correction_count, editorial_effort_score,
                    experiment_id, variant_id, idempotency_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    content_id.strip(),
                    run_id.strip(),
                    editor_id.strip(),
                    approval_decision.strip().upper(),
                    edit_reason_category.strip(),
                    text_edit_ratio,
                    claim_correction_count,
                    effort,
                    experiment_id,
                    variant_id,
                    idempotency_key,
                ),
            )
            return int(cursor.lastrowid)
    except sqlite3.IntegrityError as exc:
        if idempotency_key and "idempotency" in str(exc).lower():
            return -1
        if idempotency_key and "unique" in str(exc).lower():
            return -1
        raise
