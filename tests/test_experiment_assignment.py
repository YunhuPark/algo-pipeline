from unittest.mock import patch

from src.analytics.assignment import assign_variant
from src.analytics.db_experiments import init_experiment_db
from src.db_factory import get_connection


def test_assignment_is_deterministic_and_persisted(monkeypatch, tmp_path):
    monkeypatch.setenv("ALGO_ENV", "test")
    db_path = tmp_path / "tracking.db"

    with patch("src.analytics.db_experiments.TRACKING_DB_PATH", db_path), patch(
        "src.analytics.assignment.TRACKING_DB_PATH", db_path
    ):
        init_experiment_db()
        with get_connection(db_path) as conn:
            conn.execute(
                """
                INSERT INTO experiments (
                    experiment_id, name, status, metric_definition_version
                ) VALUES ('exp-1', 'Assignment', 'RUNNING', 'v1')
                """
            )
            conn.executemany(
                """
                INSERT INTO experiment_variants (
                    variant_id, experiment_id, is_baseline, canonical_hash, config_json
                ) VALUES (?, 'exp-1', ?, ?, '{}')
                """,
                [
                    ("baseline", 1, "hash-baseline"),
                    ("candidate", 0, "hash-candidate"),
                ],
            )

        first = assign_variant("exp-1", "publication-1")
        second = assign_variant("exp-1", "publication-1")

        assert first == second
        with get_connection(db_path) as conn:
            row = conn.execute(
                """
                SELECT variant_id, canonical_hash, COUNT(*) AS row_count
                FROM experiment_assignments
                WHERE experiment_id = 'exp-1'
                  AND publication_opportunity_id = 'publication-1'
                """
            ).fetchone()
        assert row["row_count"] == 1
        assert row["variant_id"] == first
        assert row["canonical_hash"] in {"hash-baseline", "hash-candidate"}
