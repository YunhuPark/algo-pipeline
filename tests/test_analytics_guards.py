from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from src.analytics.benchmark import run_benchmark
from src.analytics.db_experiments import init_experiment_db
from src.analytics.feedback import log_editorial_feedback
from src.analytics.import_snapshot import import_performance_snapshot
from src.analytics.recommendations import (
    generate_recommendation_draft,
    review_recommendation,
)
from src.db_factory import get_connection


@pytest.fixture
def analytics_db(monkeypatch, tmp_path):
    monkeypatch.setenv("ALGO_ENV", "test")
    db_path = tmp_path / "tracking.db"
    modules = (
        "src.analytics.db_experiments.TRACKING_DB_PATH",
        "src.analytics.feedback.TRACKING_DB_PATH",
        "src.analytics.import_snapshot.TRACKING_DB_PATH",
        "src.analytics.benchmark.TRACKING_DB_PATH",
        "src.analytics.recommendations.TRACKING_DB_PATH",
    )
    patches = [patch(name, db_path) for name in modules]
    for active_patch in patches:
        active_patch.start()
    try:
        init_experiment_db()
        with get_connection(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE content_runs (
                    run_id TEXT PRIMARY KEY,
                    topic TEXT NOT NULL,
                    status TEXT NOT NULL,
                    origin TEXT NOT NULL
                )
                """
            )
        yield db_path
    finally:
        for active_patch in reversed(patches):
            active_patch.stop()


def test_feedback_rejects_invalid_ratio(analytics_db):
    with pytest.raises(ValueError, match="text_edit_ratio"):
        log_editorial_feedback(
            content_id="content-1",
            run_id="run-1",
            editor_id="editor-1",
            approval_decision="APPROVED",
            text_edit_ratio=1.1,
        )


def test_snapshot_is_idempotent_and_rejects_negative_metrics(analytics_db):
    measured = datetime(2026, 9, 1, 12, 0, 0)
    kwargs = {
        "measured_at": measured,
        "publication_at": measured - timedelta(hours=50),
        "reach": 100,
        "saves": 2,
        "shares": 1,
        "likes": 5,
        "comments": 1,
        "source_type": "imported_fixture",
        "metric_definition_version": "v1",
        "import_idempotency_key": "snapshot-1",
    }

    first = import_performance_snapshot("publication-1", **kwargs)
    second = import_performance_snapshot("publication-1", **kwargs)

    assert first > 0
    assert second == -1
    with pytest.raises(ValueError, match="cannot be negative"):
        import_performance_snapshot(
            "publication-2",
            **{**kwargs, "reach": -1, "import_idempotency_key": "snapshot-2"},
        )


def _seed_experiment(db_path, baseline_effort: float, candidate_effort: float):
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO experiments (
                experiment_id, name, status, metric_definition_version
            ) VALUES ('exp-guard', 'Guard', 'RUNNING', 'v1')
            """
        )
        conn.executemany(
            """
            INSERT INTO experiment_variants (
                variant_id, experiment_id, is_baseline, canonical_hash, config_json
            ) VALUES (?, 'exp-guard', ?, ?, '{}')
            """,
            [
                ("baseline", 1, "hash-baseline"),
                ("candidate", 0, "hash-candidate"),
            ],
        )
        for index in range(60):
            variant = "baseline" if index < 30 else "candidate"
            effort = baseline_effort if variant == "baseline" else candidate_effort
            conn.execute(
                """
                INSERT INTO content_runs (run_id, topic, status, origin)
                VALUES (?, 'Topic', 'SUCCESS', 'real_pipeline')
                """,
                (f"run-{index}",),
            )
            conn.execute(
                """
                INSERT INTO editorial_feedback_events (
                    content_id, run_id, experiment_id, variant_id,
                    editorial_effort_score, editor_id, approval_decision
                ) VALUES (?, ?, 'exp-guard', ?, ?, 'editor', 'APPROVED')
                """,
                (f"content-{index}", f"run-{index}", variant, effort),
            )


def test_benchmark_requires_minimum_sample_per_variant(analytics_db):
    _seed_experiment(analytics_db, baseline_effort=1.0, candidate_effort=0.5)
    with get_connection(analytics_db) as conn:
        conn.execute(
            """
            DELETE FROM editorial_feedback_events
            WHERE variant_id = 'candidate' AND feedback_id NOT IN (
                SELECT feedback_id FROM editorial_feedback_events
                WHERE variant_id = 'candidate' ORDER BY feedback_id LIMIT 1
            )
            """
        )

    result = run_benchmark("exp-guard")

    assert result["status"] == "insufficient_sample"
    assert result["undersized_strata"] == {"all_topics::candidate": 1}


def test_recommendation_requires_candidate_improvement(analytics_db):
    _seed_experiment(analytics_db, baseline_effort=1.0, candidate_effort=1.0)

    assert generate_recommendation_draft("exp-guard") is None


def test_review_rejects_conflicting_second_decision(analytics_db):
    with get_connection(analytics_db) as conn:
        conn.execute(
            """
            INSERT INTO experiments (
                experiment_id, name, status, metric_definition_version
            ) VALUES ('exp-review', 'Review', 'RUNNING', 'v1')
            """
        )
        conn.execute(
            """
            INSERT INTO recommendation_drafts (
                draft_id, experiment_id, recommended_variant_id,
                baseline_variant_id, candidate_policy_version,
                justification, status
            ) VALUES (
                'draft-1', 'exp-review', 'candidate', 'baseline',
                'hash-candidate', 'review me', 'DRAFT'
            )
            """
        )

    review_recommendation("draft-1", "admin", "APPROVED", "evidence reviewed")
    with pytest.raises(ValueError, match="already been reviewed"):
        review_recommendation("draft-1", "admin", "REJECTED", "changed mind")
