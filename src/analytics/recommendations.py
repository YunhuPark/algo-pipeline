from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.analytics.benchmark import run_benchmark
from src.analytics.db_experiments import tracking_db_path
from src.db_factory import get_connection


TRACKING_DB_PATH: Path | None = None


def _path() -> Path:
    return Path(TRACKING_DB_PATH) if TRACKING_DB_PATH is not None else tracking_db_path()


def generate_recommendation_draft(experiment_id: str) -> dict[str, Any] | None:
    """Create a review draft only; this function never activates a policy."""

    result = run_benchmark(experiment_id)
    if result["status"] != "ready":
        return None

    with get_connection(_path()) as conn:
        variants = conn.execute(
            """
            SELECT variant_id, is_baseline, canonical_hash
            FROM experiment_variants
            WHERE experiment_id = ?
            ORDER BY variant_id
            """,
            (experiment_id,),
        ).fetchall()
        existing = conn.execute(
            """
            SELECT * FROM recommendation_drafts
            WHERE experiment_id = ? AND status = 'DRAFT'
            ORDER BY created_at DESC LIMIT 1
            """,
            (experiment_id,),
        ).fetchone()
        if existing:
            return dict(existing)

        baseline = next((row for row in variants if row["is_baseline"]), None)
        candidates = [row for row in variants if not row["is_baseline"]]
        if baseline is None or not candidates:
            return None

        summaries = result["variants"]
        baseline_summary = summaries.get(f"all_topics::{baseline['variant_id']}")
        eligible = [
            (row, summaries.get(f"all_topics::{row['variant_id']}"))
            for row in candidates
        ]
        eligible = [(row, summary) for row, summary in eligible if summary]
        if baseline_summary is None or not eligible:
            return None

        candidate, candidate_summary = min(
            eligible,
            key=lambda item: item[1]["avg_editorial_effort"],
        )
        if (
            candidate_summary["avg_editorial_effort"]
            >= baseline_summary["avg_editorial_effort"]
        ):
            return None

        draft_id = str(uuid.uuid4())
        justification = json.dumps(
            {
                "metric": "avg_editorial_effort",
                "baseline": baseline_summary["avg_editorial_effort"],
                "candidate": candidate_summary["avg_editorial_effort"],
                "sample_size": candidate_summary["sample_size"],
                "automatic_activation": False,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        conn.execute(
            """
            INSERT INTO recommendation_drafts (
                draft_id, experiment_id, recommended_variant_id,
                baseline_variant_id, candidate_policy_version,
                justification, status
            ) VALUES (?, ?, ?, ?, ?, ?, 'DRAFT')
            """,
            (
                draft_id,
                experiment_id,
                candidate["variant_id"],
                baseline["variant_id"],
                candidate["canonical_hash"],
                justification,
            ),
        )

    return {
        "draft_id": draft_id,
        "experiment_id": experiment_id,
        "recommended_variant_id": candidate["variant_id"],
        "baseline_variant_id": baseline["variant_id"],
        "candidate_policy_version": candidate["canonical_hash"],
        "justification": justification,
        "status": "DRAFT",
    }


def review_recommendation(
    draft_id: str,
    actor_id: str,
    decision: str,
    reason: str,
) -> dict[str, Any]:
    """Record a human review without changing policy or allocation."""

    decision = decision.strip().upper()
    if decision not in {"APPROVED", "REJECTED"}:
        raise ValueError("decision must be APPROVED or REJECTED")
    if not actor_id.strip() or not reason.strip():
        raise ValueError("actor_id and review reason are required")

    with get_connection(_path()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM recommendation_drafts WHERE draft_id = ?",
            (draft_id,),
        ).fetchone()
        if row is None:
            raise ValueError("recommendation draft not found")
        if row["status"] == decision:
            result = dict(row)
            result["message"] = "Already reviewed (idempotent)"
            return result
        if row["status"] != "DRAFT":
            raise ValueError("recommendation has already been reviewed")

        reviewed_at = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            UPDATE recommendation_drafts
            SET status = ?, reviewer_id = ?, review_reason = ?, reviewed_at = ?
            WHERE draft_id = ? AND status = 'DRAFT'
            """,
            (decision, actor_id.strip(), reason.strip(), reviewed_at, draft_id),
        )

    return {
        **dict(row),
        "status": decision,
        "reviewer_id": actor_id.strip(),
        "review_reason": reason.strip(),
        "reviewed_at": reviewed_at,
    }
