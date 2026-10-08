from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.db_factory import get_connection


@pytest.fixture
def tracking(monkeypatch, tmp_path):
    path = tmp_path / "tracking.db"
    monkeypatch.setenv("ALGO_ENV", "test")
    monkeypatch.setenv("TRACKING_DB_PATH", str(path))

    from src import db_tracking
    from src.analytics import db_experiments, feedback, weekly_review

    for module in (db_tracking, db_experiments, feedback, weekly_review):
        monkeypatch.setattr(module, "TRACKING_DB_PATH", path)
    db_tracking.init_tracking_db(path)
    return path


def _window():
    now = datetime.now(timezone.utc)
    return now - timedelta(days=1), now + timedelta(days=1)


def _seed_runs(path, count=3):
    now = datetime.now(timezone.utc).isoformat()
    with get_connection(path) as conn:
        for index in range(count):
            conn.execute(
                """INSERT INTO content_runs (
                       run_id, topic, status, origin, created_at
                   ) VALUES (?, ?, 'SUCCESS', 'real_pipeline', ?)""",
                (f"run-{index}", f"topic-{index}", now),
            )


def test_review_leaves_insufficient_data_once_a_decision_is_recorded(tracking):
    from src.analytics.feedback import log_editorial_feedback
    from src.analytics.weekly_review import run_weekly_quality_review

    start, end = _window()
    _seed_runs(tracking)

    first = run_weekly_quality_review(week_start=start, week_end=end)
    assert first["status"] == "INSUFFICIENT_DATA"
    assert first["metrics"]["review_decision_count"] == 0

    log_editorial_feedback(
        content_id="card-1",
        run_id="run-0",
        editor_id="dashboard_user",
        approval_decision="APPROVED",
        idempotency_key="review:card-1:APPROVED",
    )

    second = run_weekly_quality_review(week_start=start, week_end=end)
    assert second["status"] == "READY"
    assert second["metrics"]["review_decision_count"] == 1
    assert second["experiment_proposal"]["automatic_activation"] is False
    # The stale INSUFFICIENT_DATA report is replaced in place, not duplicated.
    assert second["review_id"] == first["review_id"]
    with get_connection(tracking) as conn:
        rows = conn.execute(
            "SELECT status FROM weekly_quality_reviews"
        ).fetchall()
    assert [row["status"] for row in rows] == ["READY"]


def test_ready_review_stays_idempotent(tracking):
    from src.analytics.feedback import log_editorial_feedback
    from src.analytics.weekly_review import run_weekly_quality_review

    start, end = _window()
    _seed_runs(tracking)
    log_editorial_feedback(
        content_id="card-1", run_id="run-0", editor_id="dashboard_user",
        approval_decision="APPROVED", idempotency_key="review:card-1:APPROVED",
    )

    first = run_weekly_quality_review(week_start=start, week_end=end)
    second = run_weekly_quality_review(week_start=start, week_end=end)

    assert first["status"] == second["status"] == "READY"
    assert second["idempotent"] is True


def _app_with_card(monkeypatch, tmp_path, tracking, *, status, run_id="run-0"):
    from src.dashboard import app as dashboard

    card = tmp_path / "20260101_0000_card"
    card.mkdir()
    meta = {"editorial_validation_status": status}
    if run_id:
        meta["run_id"] = run_id
    (card / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    monkeypatch.setattr(
        "src.db.get_queue_row", lambda queue_id: {"image_dir": str(card)}
    )
    monkeypatch.setattr(
        dashboard, "_resolve_output_file", lambda dir_name, filename: card / filename
    )
    return dashboard


def _decisions(path):
    with get_connection(path) as conn:
        return [
            (row["content_id"], row["approval_decision"])
            for row in conn.execute(
                "SELECT content_id, approval_decision FROM editorial_feedback_events"
            )
        ]


def test_queue_approval_is_recorded_once(monkeypatch, tmp_path, tracking):
    dashboard = _app_with_card(
        monkeypatch, tmp_path, tracking, status="ORIGINAL_VERIFIED"
    )

    assert dashboard._record_queue_approval(9) is True
    assert dashboard._record_queue_approval(9) is True  # duplicate is ignored

    assert _decisions(tracking) == [("20260101_0000_card", "APPROVED")]


def test_queue_approval_is_skipped_for_unverified_cards(monkeypatch, tmp_path, tracking):
    dashboard = _app_with_card(monkeypatch, tmp_path, tracking, status="UNVERIFIED")

    assert dashboard._record_queue_approval(9) is False
    assert _decisions(tracking) == []


def test_queue_rejection_is_recorded_even_for_an_unverified_card(monkeypatch, tmp_path, tracking):
    # Approving needs verified evidence; declining a card never does.
    dashboard = _app_with_card(monkeypatch, tmp_path, tracking, status="UNVERIFIED")

    assert dashboard._record_queue_review(9, "REJECTED") is True
    assert dashboard._record_queue_review(9, "REJECTED") is True  # duplicate ignored

    assert _decisions(tracking) == [("20260101_0000_card", "REJECTED")]


def test_approval_and_rejection_of_the_same_card_are_separate_decisions(
    monkeypatch, tmp_path, tracking
):
    dashboard = _app_with_card(
        monkeypatch, tmp_path, tracking, status="ORIGINAL_VERIFIED"
    )

    dashboard._record_queue_review(9, "APPROVED")
    dashboard._record_queue_review(9, "REJECTED")

    assert sorted(d for _, d in _decisions(tracking)) == ["APPROVED", "REJECTED"]


def test_rejecting_a_card_that_was_never_rendered_records_nothing(
    monkeypatch, tmp_path, tracking
):
    from src.dashboard import app as dashboard

    monkeypatch.setattr("src.db.get_queue_row", lambda queue_id: {"image_dir": ""})

    assert dashboard._record_queue_review(9, "REJECTED") is False
    assert _decisions(tracking) == []


def test_queue_approval_is_skipped_without_run_id(monkeypatch, tmp_path, tracking):
    dashboard = _app_with_card(
        monkeypatch, tmp_path, tracking, status="ORIGINAL_VERIFIED", run_id=""
    )

    assert dashboard._record_queue_approval(9) is False
    assert _decisions(tracking) == []
