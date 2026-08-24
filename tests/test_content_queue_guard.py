from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src import db
from src.agents import content_queue
from src.db_migration_queue import migrate_queue_lineage_v2
from src.schemas.queue_schemas import CollectionMethod, QueueMetadataV2


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
