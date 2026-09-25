import pytest
from src.db_factory import get_connection
from src.db_tracking import end_run, link_run_publication, start_run
import time

def test_instrumentation_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("ALGO_ENV", "test")
    db_path = tmp_path / "tracking.db"
    monkeypatch.setenv("TRACKING_DB_PATH", str(db_path))

    from src.db_tracking import init_tracking_db, resolve_tracking_db_path
    init_tracking_db(db_path)
    db_path = resolve_tracking_db_path()

    run_id = start_run(topic="Fake Instrumentation Test")
    assert run_id is not None

    time.sleep(0.1)

    end_run(
        run_id=run_id,
        status="SUCCESS",
        cost=0.015,
        latency=0.1,
        retry_count=2,
        error=None,
        grounded_claim_rate=1.0,
        step_failure_rate=0.0
    )

    # Verify in DB
    conn = get_connection(db_path)
    c = conn.cursor()
    c.execute("SELECT cost_usd, latency_sec, retry_count, status FROM content_runs WHERE run_id = ?", (run_id,))
    row = c.fetchone()

    assert row is not None
    assert row[0] == 0.015
    assert row[1] == 0.1
    assert row[2] == 2
    assert row[3] == "SUCCESS"

    conn.close()


def test_run_publication_link_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("ALGO_ENV", "test")
    db_path = tmp_path / "tracking-links.db"
    monkeypatch.setenv("TRACKING_DB_PATH", str(db_path))

    from src import db_tracking
    from src.analytics import db_experiments

    monkeypatch.setattr(db_tracking, "TRACKING_DB_PATH", db_path)
    monkeypatch.setattr(db_experiments, "TRACKING_DB_PATH", db_path)
    db_tracking.init_tracking_db(db_path)
    run_id = start_run(topic="게시 연결", origin="real_pipeline")

    link_run_publication(run_id, "ig-123")
    link_run_publication(run_id, "ig-123")

    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT run_id, publication_id FROM run_publications"
        ).fetchall()
    assert [(item["run_id"], item["publication_id"]) for item in row] == [
        (run_id, "ig-123")
    ]


def test_tracking_init_migrates_legacy_content_runs(monkeypatch, tmp_path):
    monkeypatch.setenv("ALGO_ENV", "test")
    db_path = tmp_path / "legacy-tracking.db"
    monkeypatch.setenv("TRACKING_DB_PATH", str(db_path))

    from src import db_tracking
    from src.analytics import db_experiments

    monkeypatch.setattr(db_tracking, "TRACKING_DB_PATH", db_path)
    monkeypatch.setattr(db_experiments, "TRACKING_DB_PATH", db_path)
    with get_connection(db_path) as conn:
        conn.execute(
            """CREATE TABLE content_runs (
                   run_id TEXT PRIMARY KEY,
                   topic TEXT NOT NULL,
                   status TEXT NOT NULL,
                   cost_usd REAL DEFAULT 0.0,
                   latency_sec REAL DEFAULT 0.0,
                   error_msg TEXT DEFAULT '',
                   created_at TEXT NOT NULL
               )"""
        )

    db_tracking.init_tracking_db(db_path)

    with get_connection(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(content_runs)")}
    assert {
        "origin",
        "strategy_id",
        "grounded_claim_rate",
        "step_failure_rate",
        "retry_count",
    } <= columns


def test_analytics_tracking_entrypoint_initializes_base_and_feedback_tables(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("ALGO_ENV", "test")
    db_path = tmp_path / "combined-tracking.db"
    monkeypatch.setenv("TRACKING_DB_PATH", str(db_path))

    from src import db_tracking
    from src.analytics import db_experiments

    monkeypatch.setattr(db_tracking, "TRACKING_DB_PATH", db_path)
    monkeypatch.setattr(db_experiments, "TRACKING_DB_PATH", db_path)
    db_experiments.init_tracking_db(db_path)

    with get_connection(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    assert "content_runs" in tables
    assert "editorial_feedback_events" in tables
    assert "performance_snapshots" in tables
