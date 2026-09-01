from __future__ import annotations

import hashlib
from pathlib import Path

from src.analytics.db_experiments import tracking_db_path
from src.db_factory import get_connection


TRACKING_DB_PATH: Path | None = None


def _path() -> Path:
    return Path(TRACKING_DB_PATH) if TRACKING_DB_PATH is not None else tracking_db_path()


def assign_variant(experiment_id: str, opportunity_id: str) -> str:
    """Deterministically assign an opportunity and persist the first decision."""

    with get_connection(_path()) as conn:
        existing = conn.execute(
            """
            SELECT variant_id FROM experiment_assignments
            WHERE experiment_id = ? AND publication_opportunity_id = ?
            """,
            (experiment_id, opportunity_id),
        ).fetchone()
        if existing:
            return existing["variant_id"]

        variants = conn.execute(
            """
            SELECT variant_id, canonical_hash FROM experiment_variants
            WHERE experiment_id = ? ORDER BY variant_id
            """,
            (experiment_id,),
        ).fetchall()
        if not variants:
            raise ValueError("Experiment has no variants")
        digest = hashlib.sha256(
            f"{experiment_id}\n{opportunity_id}".encode("utf-8")
        ).digest()
        chosen = variants[int.from_bytes(digest[:8], "big") % len(variants)]
        conn.execute(
            """
            INSERT INTO experiment_assignments (
                experiment_id, publication_opportunity_id, variant_id, canonical_hash
            ) VALUES (?, ?, ?, ?)
            """,
            (
                experiment_id,
                opportunity_id,
                chosen["variant_id"],
                chosen["canonical_hash"],
            ),
        )
        return chosen["variant_id"]

