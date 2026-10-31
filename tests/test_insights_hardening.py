from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from src.agents import analytics
from src.analytics import db_experiments, import_snapshot
from src.db_factory import get_connection


class _Response:
    def __init__(self, metrics: dict[str, int]):
        self._metrics = metrics

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "data": [
                {"name": name, "values": [{"value": value}]}
                for name, value in self._metrics.items()
            ]
        }


ALL_ZERO = {
    "likes": 0, "comments": 0, "saved": 0, "reach": 0, "shares": 0, "views": 0,
}


def _fetch(monkeypatch, metrics: dict[str, int]):
    monkeypatch.setattr(analytics, "IG_ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(
        analytics.httpx, "get", lambda *args, **kwargs: _Response(metrics)
    )
    return analytics.fetch_post_insights("ig-post-1")


def test_real_zero_is_a_successful_fetch(monkeypatch):
    result = _fetch(monkeypatch, ALL_ZERO)

    assert result.fetch_succeeded is True
    assert result.error == ""
    assert (result.reach, result.saves, result.likes) == (0, 0, 0)


def test_missing_metric_is_a_failure_not_a_zero(monkeypatch):
    partial = {k: v for k, v in ALL_ZERO.items() if k != "saved"}

    result = _fetch(monkeypatch, partial)

    assert result.fetch_succeeded is False
    assert result.error == "INCOMPLETE_METRICS:saved"


def test_http_error_records_status_code(monkeypatch):
    class _Failing:
        status_code = 400

        def raise_for_status(self) -> None:
            raise analytics.httpx.HTTPStatusError(
                "bad", request=None, response=self  # type: ignore[arg-type]
            )

    monkeypatch.setattr(analytics, "IG_ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(analytics.httpx, "get", lambda *a, **k: _Failing())

    result = analytics.fetch_post_insights("ig-post-1")

    assert result.fetch_succeeded is False
    assert result.error == "HTTP_400"


def test_sync_report_separates_failures_from_empty_run(monkeypatch):
    monkeypatch.setattr(
        analytics,
        "get_posts",
        lambda **kwargs: [
            {"post_id": "ok", "posted_at": "2026-09-01 09:30:00"},
            {"post_id": "bad", "posted_at": "2026-09-01 09:30:00"},
        ],
    )

    def fake_fetch(post_id):
        if post_id == "bad":
            return analytics.PostInsights(post_id=post_id, error="HTTP_400")
        return analytics.PostInsights(post_id=post_id, fetch_succeeded=True)

    monkeypatch.setattr(analytics, "fetch_post_insights", fake_fetch)
    monkeypatch.setattr(analytics, "insert_analytics", lambda **kwargs: None)
    monkeypatch.setattr(db_experiments, "init_tracking_db", lambda: None)
    monkeypatch.setattr(
        import_snapshot, "import_performance_snapshot", lambda **kwargs: 1
    )
    monkeypatch.setattr(import_snapshot, "compact_performance_snapshots", lambda: 0)

    assert analytics.sync_all_insights() == 1
    assert analytics.LAST_SYNC_REPORT["updated"] == 1
    assert analytics.LAST_SYNC_REPORT["failed"] == 1
    assert analytics.LAST_SYNC_REPORT["failures"] == [
        {"post_id": "bad", "reason": "HTTP_400"}
    ]


@pytest.fixture
def tracking_db(monkeypatch, tmp_path):
    monkeypatch.setenv("ALGO_ENV", "test")
    path = tmp_path / "tracking.db"
    with patch("src.analytics.db_experiments.TRACKING_DB_PATH", path, create=True), \
         patch("src.analytics.import_snapshot.TRACKING_DB_PATH", path, create=True), \
         patch("src.db_tracking.TRACKING_DB_PATH", path, create=True):
        import src.db_tracking

        src.db_tracking.init_tracking_db()
        db_experiments.init_experiment_db()
        yield path


def _snapshot(publication_id: str, hours_after_publish: int, reach: int, key: str):
    published = datetime(2026, 9, 1, tzinfo=timezone.utc)
    import_snapshot.import_performance_snapshot(
        publication_id,
        measured_at=published + timedelta(hours=hours_after_publish),
        publication_at=published,
        reach=reach, saves=0, shares=0, likes=0, comments=0,
        source_type="instagram_graph_api",
        metric_definition_version="v1",
        import_idempotency_key=key,
    )


def test_compaction_keeps_only_the_newest_row_per_post(tracking_db):
    for hours, reach in ((1, 0), (30, 1), (60, 2)):
        _snapshot("post-a", hours, reach, f"a-{hours}")
    _snapshot("post-b", 5, 7, "b-5")

    removed = import_snapshot.compact_performance_snapshots()

    assert removed == 2
    with get_connection(tracking_db) as conn:
        rows = conn.execute(
            "SELECT publication_id, reach FROM performance_snapshots "
            "ORDER BY publication_id"
        ).fetchall()
    assert [(r["publication_id"], r["reach"]) for r in rows] == [
        ("post-a", 2),
        ("post-b", 7),
    ]


def test_compaction_is_a_noop_when_already_one_row_per_post(tracking_db):
    _snapshot("post-a", 60, 2, "a-60")

    assert import_snapshot.compact_performance_snapshots() == 0
