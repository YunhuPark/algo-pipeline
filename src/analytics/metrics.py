from __future__ import annotations

from pathlib import Path
from typing import Any

from src.analytics.db_experiments import tracking_db_path
from src.db_factory import get_connection


TRACKING_DB_PATH: Path | None = None
MIN_EXPERIMENT_SAMPLE = 30


def _path() -> Path:
    return Path(TRACKING_DB_PATH) if TRACKING_DB_PATH is not None else tracking_db_path()


def get_experiment_metrics(experiment_id: str) -> dict[str, Any]:
    with get_connection(_path()) as conn:
        total = conn.execute(
            """
            SELECT COUNT(*) FROM experiment_assignments
            WHERE experiment_id = ?
            """,
            (experiment_id,),
        ).fetchone()[0]
        variants = conn.execute(
            """
            SELECT variant_id, COUNT(*) AS sample_size
            FROM experiment_assignments
            WHERE experiment_id = ?
            GROUP BY variant_id
            ORDER BY variant_id
            """,
            (experiment_id,),
        ).fetchall()

    return {
        "experiment_id": experiment_id,
        "total_sample": total,
        "minimum_sample": MIN_EXPERIMENT_SAMPLE,
        "insufficient_sample": total < MIN_EXPERIMENT_SAMPLE,
        "variants": [dict(row) for row in variants],
    }

