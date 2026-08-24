from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src import db
from src.agents import content_queue
from src.db_migration_queue import migrate_queue_lineage_v2
from src.schemas.queue_schemas import CollectionMethod, QueueMetadataV2
from src.schemas.content_package import PipelineResult
from src.schemas.queue_schemas import PublishAttemptState


@pytest.fixture
def queue_db(tmp_path, monkeypatch):
    path = tmp_path / "queue-test.db"
    monkeypatch.setenv("ALGO_ENV", "test")
    monkeypatch.setenv("ALGO_DB_PATH", str(path))
    db.init_db(path)
    migrate_queue_lineage_v2(path)
    return path


def metadata():
    return QueueMetadataV2(
        topic="검증된 AI 뉴스",
        source_title="원문 기사",
        source_url="https://example.com/article",
        context="검증에 사용할 충분한 기사 문맥",
        evidence=[{"title": "원문 기사", "url": "https://example.com/article"}],
    )


def test_enqueue_v2_and_dequeue(queue_db):
    row_id = db.enqueue_v2(metadata(), CollectionMethod.NEWS_COLLECTOR)
    row = db.dequeue_next()
    assert row["id"] == row_id
    assert row["lineage_hash"] == metadata().lineage_hash()


def test_legacy_quarantine_keeps_status_and_retry(queue_db):
    with sqlite3.connect(queue_db) as conn:
        row_id = conn.execute("INSERT INTO queue(topic) VALUES ('legacy')").lastrowid
    with patch.object(content_queue, "_run_full_pipeline") as publisher:
        assert content_queue.publish_next() is None
        publisher.assert_not_called()
    with sqlite3.connect(queue_db) as conn:
        row = conn.execute("SELECT * FROM queue WHERE id=?", (row_id,)).fetchone()
        assert row[6] == "pending"
        columns = [r[1] for r in conn.execute("PRAGMA table_info(queue)")]
        values = dict(zip(columns, row))
        assert values["publish_error_code"] == "LEGACY_UNSUPPORTED"
        assert values["retry_count"] == 0
    assert db.dequeue_next() is None


@pytest.mark.parametrize(
    "column,value,error",
    [
        ("lineage_hash", "bad", "HASH_MISMATCH"),
        ("metadata_json", "{", "JSON_PARSE_ERROR"),
        ("metadata_schema_version", 0, "MISSING_SCHEMA_VERSION"),
        ("collection_method", "SYNTHETIC", "UNPUBLISHABLE_METHOD"),
    ],
)
def test_permanent_validation_errors_never_publish_or_retry(queue_db, column, value, error):
    row_id = db.enqueue_v2(metadata(), CollectionMethod.NEWS_COLLECTOR)
    with sqlite3.connect(queue_db) as conn:
        conn.execute(f"UPDATE queue SET {column}=? WHERE id=?", (value, row_id))
    with patch.object(content_queue, "_run_full_pipeline") as publisher:
        assert content_queue.publish_next() is None
        publisher.assert_not_called()
    with sqlite3.connect(queue_db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM queue WHERE id=?", (row_id,)).fetchone()
        assert row["status"] == "pending"
        assert row["publish_error_code"] == error
        assert row["retry_count"] == 0


def test_news_without_evidence_enqueues_nothing(queue_db):
    news = SimpleNamespace(topic="T", context="context", source_items=[])
    with patch.object(content_queue, "_collect_news", return_value=news):
        assert content_queue.bulk_generate(1) == []
    assert db.queue_count() == 0


def test_manual_topic_is_blocked(queue_db):
    with pytest.raises(ValueError, match="evidence"):
        content_queue.add_topic("출처 없는 주제")


def _result(*, succeeded=False, post_id=None, state=PublishAttemptState.NOT_ATTEMPTED, error=None):
    return PipelineResult(
        image_paths=[Path("card.png")],
        generation_succeeded=True,
        publish_requested=True,
        publish_succeeded=succeeded,
        ig_post_id=post_id,
        permalink=None,
        failure_stage=None if succeeded else "publisher",
        error_code=error,
        publish_attempt_state=state,
        publish_attempt_id="attempt-test",
    )


def test_attempt_is_committed_before_remote_and_id_is_committed_before_published(queue_db):
    row_id = db.enqueue_v2(metadata(), CollectionMethod.NEWS_COLLECTOR)

    def simulate(topic, context, angle, publish, attempt_id, before_publish, on_remote_id):
        before_publish(attempt_id)
        with sqlite3.connect(queue_db) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM queue WHERE id=?", (row_id,)).fetchone()
            assert row["publish_attempt_state"] == "STARTED"
            assert row["publish_error_code"] == "PUBLISH_IN_PROGRESS"
        on_remote_id(attempt_id, "ig-123")
        return _result(
            succeeded=True,
            post_id="ig-123",
            state=PublishAttemptState.REMOTE_ID_CONFIRMED,
        )

    with patch.object(content_queue, "_run_full_pipeline", side_effect=simulate) as publisher:
        result = content_queue.publish_next()
        publisher.assert_called_once()
    assert result["id"] == row_id
    with sqlite3.connect(queue_db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM queue WHERE id=?", (row_id,)).fetchone()
        assert row["status"] == "published"
        assert row["ig_post_id"] == "ig-123"
        assert row["publish_error_code"] is None


@pytest.mark.parametrize("error", ["UNCERTAIN_EMPTY_POST_ID", "REMOTE_PUBLISH_PERSISTENCE_UNCERTAIN"])
def test_uncertain_remote_result_is_never_retried(queue_db, error):
    row_id = db.enqueue_v2(metadata(), CollectionMethod.NEWS_COLLECTOR)

    def simulate(topic, context, angle, publish, attempt_id, before_publish, on_remote_id):
        before_publish(attempt_id)
        return _result(state=PublishAttemptState.UNKNOWN, error=error)

    with patch.object(content_queue, "_run_full_pipeline", side_effect=simulate) as publisher:
        assert content_queue.publish_next() is None
        publisher.assert_called_once()
        assert content_queue.publish_next() is None
        publisher.assert_called_once()
    with sqlite3.connect(queue_db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM queue WHERE id=?", (row_id,)).fetchone()
        assert row["status"] == "pending"
        assert row["retry_count"] == 0
        assert row["publish_attempt_id"] is not None
        assert row["publish_error_code"] == error


def test_explicit_pre_publish_transient_retries_only_until_limit(queue_db):
    row_id = db.enqueue_v2(metadata(), CollectionMethod.NEWS_COLLECTOR)
    transient = _result(
        state=PublishAttemptState.NOT_ATTEMPTED,
        error="NETWORK_TIMEOUT_BEFORE_PUBLISH",
    )
    with patch.object(content_queue, "_run_full_pipeline", return_value=transient) as pipeline:
        assert content_queue.publish_next() is None
        assert content_queue.publish_next() is None
        assert content_queue.publish_next() is None
        assert content_queue.publish_next() is None
        assert pipeline.call_count == 3
    with sqlite3.connect(queue_db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM queue WHERE id=?", (row_id,)).fetchone()
        assert row["retry_count"] == 3


def test_existing_remote_id_is_never_dequeued(queue_db):
    row_id = db.enqueue_v2(metadata(), CollectionMethod.NEWS_COLLECTOR)
    with sqlite3.connect(queue_db) as conn:
        conn.execute("UPDATE queue SET ig_post_id='existing' WHERE id=?", (row_id,))
    with patch.object(content_queue, "_run_full_pipeline") as publisher:
        assert content_queue.publish_next() is None
        publisher.assert_not_called()
