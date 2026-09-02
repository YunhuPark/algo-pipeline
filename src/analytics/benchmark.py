from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from src.analytics.db_experiments import tracking_db_path
from src.analytics.stats import wilson_interval
from src.db_factory import get_connection


TRACKING_DB_PATH: Path | None = None
MIN_TOTAL_SAMPLE = 30
MIN_STRATUM_SAMPLE = 15


def _path() -> Path:
    return Path(TRACKING_DB_PATH) if TRACKING_DB_PATH is not None else tracking_db_path()


def _variant_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    sample_size = len(rows)
    effort = sum(float(row["editorial_effort_score"] or 0.0) for row in rows)
    corrections = sum(int(row["claim_correction_count"] or 0) > 0 for row in rows)
    approved = sum(str(row["approval_decision"]).upper() == "APPROVED" for row in rows)
    correction_rate = corrections / sample_size
    approval_rate = approved / sample_size
    return {
        "sample_size": sample_size,
        "avg_editorial_effort": effort / sample_size,
        "factual_correction_rate": correction_rate,
        "factual_correction_ci": wilson_interval(corrections, sample_size),
        "approval_rate": approval_rate,
        "approval_rate_ci": wilson_interval(approved, sample_size),
    }


def run_benchmark(
    experiment_id: str,
    *,
    stratify_by: str = "variant_id",
) -> dict[str, Any]:
    """Measure only successful real-pipeline runs; synthetic data is excluded."""

    if stratify_by not in {"variant_id", "topic_and_variant"}:
        raise ValueError("unsupported benchmark stratification")

    with get_connection(_path()) as conn:
        records = conn.execute(
            """
            SELECT f.variant_id, f.editorial_effort_score,
                   f.claim_correction_count, f.approval_decision, r.topic
            FROM editorial_feedback_events AS f
            JOIN content_runs AS r ON r.run_id = f.run_id
            WHERE f.experiment_id = ?
              AND lower(r.status) = 'success'
              AND r.origin = 'real_pipeline'
              AND f.variant_id IS NOT NULL
            ORDER BY f.feedback_id
            """,
            (experiment_id,),
        ).fetchall()

    rows = [dict(row) for row in records]
    if len(rows) < MIN_TOTAL_SAMPLE:
        return {
            "experiment_id": experiment_id,
            "status": "insufficient_sample",
            "minimum_sample": MIN_TOTAL_SAMPLE,
            "total_sample": len(rows),
            "variants": {},
        }

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (
            f"{row['topic']}::{row['variant_id']}"
            if stratify_by == "topic_and_variant"
            else f"all_topics::{row['variant_id']}"
        )
        grouped[key].append(row)

    undersized = {
        key: len(group_rows)
        for key, group_rows in grouped.items()
        if len(group_rows) < MIN_STRATUM_SAMPLE
    }
    if undersized:
        return {
            "experiment_id": experiment_id,
            "status": "insufficient_sample",
            "minimum_sample": MIN_TOTAL_SAMPLE,
            "minimum_stratum_sample": MIN_STRATUM_SAMPLE,
            "total_sample": len(rows),
            "undersized_strata": undersized,
            "variants": {},
        }

    return {
        "experiment_id": experiment_id,
        "status": "ready",
        "minimum_sample": MIN_TOTAL_SAMPLE,
        "minimum_stratum_sample": MIN_STRATUM_SAMPLE,
        "total_sample": len(rows),
        "variants": {
            key: _variant_summary(group_rows)
            for key, group_rows in sorted(grouped.items())
        },
    }
