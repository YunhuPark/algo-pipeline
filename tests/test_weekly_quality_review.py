from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.db_factory import get_connection


@pytest.fixture
def review_db(monkeypatch, tmp_path):
    path = tmp_path / "weekly-review.db"
    monkeypatch.setenv("ALGO_ENV", "test")
    monkeypatch.setenv("TRACKING_DB_PATH", str(path))

    from src import db_tracking
    from src.analytics import db_experiments, weekly_review

    monkeypatch.setattr(db_tracking, "TRACKING_DB_PATH", path)
    monkeypatch.setattr(db_experiments, "TRACKING_DB_PATH", path)
    monkeypatch.setattr(weekly_review, "TRACKING_DB_PATH", path)
    db_tracking.init_tracking_db(path)
    return path


def _window():
    now = datetime.now(timezone.utc)
    return now - timedelta(days=1), now + timedelta(days=1)


def test_weekly_review_fails_closed_when_data_is_insufficient(review_db):
    from src.analytics.weekly_review import run_weekly_quality_review

    start, end = _window()
    report = run_weekly_quality_review(week_start=start, week_end=end)

    assert report["status"] == "INSUFFICIENT_DATA"
    assert report["experiment_proposal"] is None
    assert report["safety"] == {
        "published": False,
        "policy_changed": False,
        "experiment_activated": False,
    }


def test_weekly_review_initializes_a_clean_tracking_database(monkeypatch, tmp_path):
    path = tmp_path / "clean-weekly-review.db"
    monkeypatch.setenv("ALGO_ENV", "test")
    monkeypatch.setenv("TRACKING_DB_PATH", str(path))

    from src import db_tracking
    from src.analytics import db_experiments, weekly_review

    monkeypatch.setattr(db_tracking, "TRACKING_DB_PATH", path)
    monkeypatch.setattr(db_experiments, "TRACKING_DB_PATH", path)
    monkeypatch.setattr(weekly_review, "TRACKING_DB_PATH", path)

    start, end = _window()
    report = weekly_review.run_weekly_quality_review(
        week_start=start,
        week_end=end,
    )

    assert report["status"] == "INSUFFICIENT_DATA"
    with get_connection(path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {"content_runs", "weekly_quality_reviews"} <= tables


def test_weekly_review_uses_real_mature_data_and_is_idempotent(review_db):
    from src.analytics.weekly_review import run_weekly_quality_review

    start, end = _window()
    current = datetime.now(timezone.utc)
    now = current.isoformat()
    earlier = (current - timedelta(minutes=1)).isoformat()
    later = (current + timedelta(minutes=1)).isoformat()
    with get_connection(review_db) as conn:
        for index in range(3):
            conn.execute(
                """INSERT INTO content_runs (
                       run_id, topic, status, origin, latency_sec,
                       grounded_claim_rate, retry_count, created_at
                   ) VALUES (?, ?, 'SUCCESS', 'real_pipeline', 12, 1.0, ?, ?)""",
                (f"real-{index}", f"topic-{index}", index, now),
            )
        conn.execute(
            """INSERT INTO content_runs (
                   run_id, topic, status, origin, latency_sec, created_at
               ) VALUES ('synthetic-1', 'ignore', 'SUCCESS', 'synthetic_demo', 1, ?)""",
            (now,),
        )
        conn.execute(
            """INSERT INTO editorial_feedback_events (
                   content_id, run_id, editor_id, approval_decision,
                   edit_reason_category, text_edit_ratio,
                   claim_correction_count, editorial_effort_score,
                   review_duration_sec, created_at
               ) VALUES ('content-1', 'real-0', 'human', 'APPROVED',
                         'factual_correction', 0.25, 1, 1.25, 90, ?)""",
            (now,),
        )
        conn.execute(
            """INSERT INTO editorial_feedback_events (
                   content_id, run_id, editor_id, approval_decision,
                   review_duration_sec, created_at
               ) VALUES ('content-1', 'real-0', 'human', 'REJECTED', 20, ?)""",
            (earlier,),
        )
        conn.execute(
            """INSERT INTO quality_checks (run_id, check_type, passed, reason)
               VALUES ('real-1', 'NUMBER_UNSUPPORTED', 0, 'unsupported number')"""
        )
        conn.execute(
            """INSERT INTO performance_snapshots (
                   publication_id, measured_at, publication_at, reach, saves,
                   shares, source_type, metric_definition_version,
                   import_idempotency_key, is_provisional
               ) VALUES ('pub-mature', ?, ?, 1000, 30, 10, 'instagram', 'v1',
                         'mature-1', 0)""",
            (now, (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()),
        )
        conn.execute(
            """INSERT INTO content_runs (
                   run_id, topic, status, origin, latency_sec, created_at
               ) VALUES ('old-real', 'old', 'SUCCESS', 'real_pipeline', 1, ?)""",
            ((start - timedelta(days=1)).isoformat(),),
        )
        conn.execute(
            """INSERT INTO performance_snapshots (
                   publication_id, measured_at, publication_at, reach, saves,
                   shares, source_type, metric_definition_version,
                   import_idempotency_key, is_provisional
               ) VALUES ('pub-old-run', ?, ?, 100000, 99999, 99999,
                         'instagram', 'v1', 'old-run-mature-1', 0)""",
            (now, (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()),
        )
        conn.execute(
            """INSERT INTO run_publications (run_id, publication_id, published_at)
               VALUES ('old-real', 'pub-old-run', ?)""",
            (now,),
        )
        conn.execute(
            """INSERT INTO run_publications (run_id, publication_id, published_at)
               VALUES ('real-0', 'pub-mature', ?)""",
            (now,),
        )
        conn.execute(
            """INSERT INTO performance_snapshots (
                   publication_id, measured_at, publication_at, reach, saves,
                   shares, source_type, metric_definition_version,
                   import_idempotency_key, is_provisional
               ) VALUES ('pub-mature', ?, ?, 2000, 80, 20, 'instagram', 'v1',
                         'mature-2', 0)""",
            (later, (current - timedelta(days=3)).isoformat()),
        )
        conn.execute(
            """INSERT INTO performance_snapshots (
                   publication_id, measured_at, publication_at, reach, saves,
                   shares, source_type, metric_definition_version,
                   import_idempotency_key, is_provisional
               ) VALUES ('pub-provisional', ?, ?, 100000, 99999, 99999,
                         'instagram', 'v1', 'provisional-1', 1)""",
            (now, now),
        )
        conn.execute(
            """INSERT INTO performance_snapshots (
                   publication_id, measured_at, publication_at, reach, saves,
                   shares, source_type, metric_definition_version,
                   import_idempotency_key, is_provisional
               ) VALUES ('pub-unlinked', ?, ?, 100000, 99999, 99999,
                         'instagram', 'v1', 'unlinked-mature-1', 0)""",
            (now, (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()),
        )

    first = run_weekly_quality_review(week_start=start, week_end=end)
    second = run_weekly_quality_review(week_start=start, week_end=end)

    assert first["status"] == "READY"
    assert first["metrics"]["real_run_count"] == 3
    assert first["metrics"]["mature_snapshot_count"] == 1
    assert first["metrics"]["save_rate"] == 0.04
    assert first["metrics"]["review_decision_count"] == 1
    assert first["metrics"]["approval_rate"] == 1.0
    assert first["experiment_proposal"]["status"] == "DRAFT"
    assert first["experiment_proposal"]["automatic_activation"] is False
    assert second["review_id"] == first["review_id"]
    assert second["idempotent"] is True

    with get_connection(review_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM weekly_quality_reviews").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM recommendation_drafts").fetchone()[0] == 0
