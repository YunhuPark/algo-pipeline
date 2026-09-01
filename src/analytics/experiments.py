from __future__ import annotations

from pathlib import Path
from typing import Any

from src.analytics.db_experiments import tracking_db_path
from src.db_factory import get_connection


TRACKING_DB_PATH: Path | None = None

VALID_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"APPROVED"},
    "APPROVED": {"RUNNING"},
    "RUNNING": {"PAUSED", "COMPLETED", "ROLLED_BACK"},
    "PAUSED": {"RUNNING", "COMPLETED", "ROLLED_BACK"},
    "COMPLETED": set(),
    "ROLLED_BACK": set(),
}


class ExperimentTransitionError(ValueError):
    pass


def _path() -> Path:
    return Path(TRACKING_DB_PATH) if TRACKING_DB_PATH is not None else tracking_db_path()


def list_experiments() -> list[dict[str, Any]]:
    with get_connection(_path()) as conn:
        rows = conn.execute(
            """
            SELECT experiment_id, name, description, status, allocation_percent,
                   metric_definition_version, created_at, updated_at
            FROM experiments
            ORDER BY created_at DESC, experiment_id ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def transition_experiment(
    experiment_id: str,
    target_state: str,
    *,
    actor_type: str,
    actor_id: str,
    reason: str,
    expected_version: int | None = None,
    approval_scope: str = "",
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    target_state = target_state.strip().upper()
    if not reason.strip():
        raise ExperimentTransitionError("Transition reason is required")

    with get_connection(_path()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status FROM experiments WHERE experiment_id = ?",
            (experiment_id,),
        ).fetchone()
        if row is None:
            raise ExperimentTransitionError("Experiment not found")

        current_state = str(row["status"]).upper()
        version = conn.execute(
            "SELECT COUNT(*) FROM experiment_state_events WHERE experiment_id = ?",
            (experiment_id,),
        ).fetchone()[0]
        if expected_version is not None and expected_version != version:
            raise ExperimentTransitionError(
                f"Optimistic concurrency failure: expected {expected_version}, current {version}"
            )

        if idempotency_key:
            existing = conn.execute(
                """
                SELECT to_status, actor_type, actor_id
                FROM experiment_state_events
                WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()
            if existing:
                return {
                    "experiment_id": experiment_id,
                    "previous_state": current_state,
                    "new_state": existing["to_status"],
                    "actor_type": existing["actor_type"],
                    "actor_id": existing["actor_id"],
                    "version": version,
                    "idempotent": True,
                }

        if target_state not in VALID_TRANSITIONS.get(current_state, set()):
            raise ExperimentTransitionError(
                f"Invalid transition: {current_state} -> {target_state}"
            )

        conn.execute(
            """
            INSERT INTO experiment_state_events (
                experiment_id, from_status, to_status, actor_type, actor_id,
                reason, approval_scope, idempotency_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                experiment_id,
                current_state,
                target_state,
                actor_type,
                actor_id,
                reason.strip(),
                approval_scope,
                idempotency_key,
            ),
        )
        conn.execute(
            """
            UPDATE experiments
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE experiment_id = ?
            """,
            (target_state, experiment_id),
        )

    return {
        "experiment_id": experiment_id,
        "previous_state": current_state,
        "new_state": target_state,
        "actor_type": actor_type,
        "actor_id": actor_id,
        "version": version + 1,
        "idempotent": False,
    }

