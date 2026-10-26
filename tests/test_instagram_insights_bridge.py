from __future__ import annotations

from datetime import datetime, timezone

from src.agents import analytics
from src.analytics import db_experiments, import_snapshot


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "data": [
                {"name": "likes", "values": [{"value": 12}]},
                {"name": "comments", "total_value": {"value": 3}},
                {"name": "saved", "values": [{"value": 7}]},
                {"name": "reach", "total_value": {"value": 100}},
                {"name": "shares", "values": [{"value": 4}]},
                {"name": "views", "total_value": {"value": 130}},
            ]
        }


def test_fetch_post_insights_supports_values_and_total_value(monkeypatch):
    monkeypatch.setattr(analytics, "IG_ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(analytics.httpx, "get", lambda *args, **kwargs: _Response())

    result = analytics.fetch_post_insights("ig-post-1")

    assert result.fetch_succeeded is True
    assert (result.likes, result.comments, result.saves) == (12, 3, 7)
    assert (result.reach, result.shares, result.impressions) == (100, 4, 130)


def test_sync_all_insights_writes_provenance_snapshot(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        analytics,
        "get_posts",
        lambda **kwargs: [
            {
                "post_id": "ig-post-1",
                "posted_at": "2026-09-01 09:30:00",
            }
        ],
    )
    monkeypatch.setattr(
        analytics,
        "fetch_post_insights",
        lambda post_id: analytics.PostInsights(
            post_id=post_id,
            likes=12,
            comments=3,
            saves=7,
            reach=100,
            impressions=130,
            shares=4,
            fetch_succeeded=True,
        ),
    )
    monkeypatch.setattr(analytics, "insert_analytics", lambda **kwargs: captured.update(legacy=kwargs))
    monkeypatch.setattr(db_experiments, "init_tracking_db", lambda: None)
    monkeypatch.setattr(
        import_snapshot,
        "import_performance_snapshot",
        lambda **kwargs: captured.update(snapshot=kwargs) or 1,
    )

    assert analytics.sync_all_insights() == 1
    assert captured["legacy"]["impressions"] == 130
    assert captured["snapshot"]["publication_id"] == "ig-post-1"
    assert captured["snapshot"]["shares"] == 4
    assert captured["snapshot"]["source_type"] == "instagram_graph_api"
    assert captured["snapshot"]["publication_at"].tzinfo == timezone.utc
    assert len(captured["snapshot"]["import_idempotency_key"]) == 64


def test_sync_all_insights_does_not_record_failed_fetch(monkeypatch):
    writes: list[str] = []
    monkeypatch.setattr(
        analytics,
        "get_posts",
        lambda **kwargs: [{"post_id": "ig-post-1", "posted_at": "2026-09-01 09:30:00"}],
    )
    monkeypatch.setattr(
        analytics,
        "fetch_post_insights",
        lambda post_id: analytics.PostInsights(post_id=post_id),
    )
    monkeypatch.setattr(analytics, "insert_analytics", lambda **kwargs: writes.append("legacy"))
    monkeypatch.setattr(db_experiments, "init_tracking_db", lambda: None)
    monkeypatch.setattr(
        import_snapshot,
        "import_performance_snapshot",
        lambda **kwargs: writes.append("snapshot"),
    )

    assert analytics.sync_all_insights() == 0
    assert writes == []


def test_snapshot_key_is_stable_within_an_hour():
    first = datetime(2026, 9, 7, 1, 5, tzinfo=timezone.utc)
    second = datetime(2026, 9, 7, 1, 59, tzinfo=timezone.utc)

    assert analytics._snapshot_key("ig-post-1", first) == analytics._snapshot_key(
        "ig-post-1", second
    )
