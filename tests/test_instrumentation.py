import pytest
from src.db_factory import get_connection
from src.db_tracking import start_run, end_run
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
