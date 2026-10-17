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
    monkeypatch.setattr(content_queue, "_validate_publish_configuration", lambda: None)
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


def test_queue_metadata_builds_verified_source_lineage():
    lineage = metadata().to_source_lineage(CollectionMethod.NEWS_COLLECTOR)
    assert lineage.is_verified_ready is True
    assert lineage.collection_method == "NEWS_COLLECTOR"
    assert lineage.evidence_passages[0].article_id == lineage.article_id
    assert lineage.evidence_passages[0].content_hash == lineage.content_hash


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


def test_fill_from_news_excludes_already_queued_and_newly_picked_urls(queue_db):
    """Repeated collection calls must not just re-pick the same top-scoring
    article - RSS/Tavily results don't meaningfully change within a few
    minutes, so without exclusion, clicking "자동 수집 시작" repeatedly kept
    re-adding the exact same story."""
    existing_meta = metadata()  # source_url="https://example.com/article"
    db.enqueue_v2(existing_meta, CollectionMethod.NEWS_COLLECTOR)

    calls = []

    def fake_collect(exclude_urls=frozenset()):
        calls.append(set(exclude_urls))
        n = len(calls)
        return SimpleNamespace(
            topic=f"주제{n}",
            selected_item=SimpleNamespace(
                title=f"기사{n}", url=f"https://example.com/new-{n}", summary="본문"
            ),
        )

    with patch.object(content_queue, "_collect_news", side_effect=fake_collect), \
         patch("src.agents.trend_analyzer.build_locked_source_report"), \
         patch(
             "src.services.generation_service.build_queue_metadata",
             return_value=metadata(),
         ):
        content_queue._fill_from_news(2)

    # 요청한 개수(2번)만 시도한다 — 개수를 채우려고 더 끌어오지 않는다.
    assert len(calls) == 2
    # 첫 호출: 이미 큐에 있던 기사 URL이 먼저 제외돼야 한다
    assert "https://example.com/article" in calls[0]
    # 두 번째 호출: 첫 호출에서 방금 고른 기사도 추가로 제외돼야 한다
    assert "https://example.com/new-1" in calls[1]


def test_publish_configuration_is_checked_before_dequeue(monkeypatch):
    with patch.object(
        content_queue,
        "_validate_publish_configuration",
        side_effect=RuntimeError("unsafe publish configuration"),
    ) as validate, patch.object(content_queue, "dequeue_next") as dequeue:
        with pytest.raises(RuntimeError, match="unsafe publish configuration"):
            content_queue.publish_next(publish_to_ig=True)

    validate.assert_called_once_with()
    dequeue.assert_not_called()


def test_publish_configuration_includes_remote_account_preflight():
    with patch("src.agents.publisher.validate_publish_config") as local, patch(
        "src.agents.publisher.verify_instagram_account"
    ) as remote:
        content_queue._validate_publish_configuration()

    local.assert_called_once_with()
    remote.assert_called_once_with()


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

    def simulate(topic, context, angle, publish, attempt_id, before_publish, on_remote_id, source_lineage):
        assert source_lineage.is_verified_ready is True
        assert source_lineage.collection_method == "NEWS_COLLECTOR"
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

    def simulate(topic, context, angle, publish, attempt_id, before_publish, on_remote_id, source_lineage):
        assert source_lineage.is_verified_ready is True
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


def test_invalid_front_row_does_not_block_a_valid_row_behind_it(queue_db):
    """A stale/corrupted row (e.g. HASH_MISMATCH from a schema-era mismatch)
    must not permanently block a perfectly valid row queued behind it."""
    bad_id = db.enqueue_v2(metadata(), CollectionMethod.NEWS_COLLECTOR)
    with sqlite3.connect(queue_db) as conn:
        conn.execute(
            "UPDATE queue SET lineage_hash='corrupted' WHERE id=?", (bad_id,)
        )
    good_meta = QueueMetadataV2(
        topic="다른 검증된 뉴스",
        source_title="다른 원문",
        source_url="https://example.com/other-article",
        context="다른 충분한 문맥",
        evidence=[{"title": "다른 원문", "url": "https://example.com/other-article"}],
    )
    good_id = db.enqueue_v2(good_meta, CollectionMethod.NEWS_COLLECTOR)

    def simulate(topic, context, angle, publish, attempt_id, before_publish, on_remote_id, source_lineage):
        before_publish(attempt_id)
        on_remote_id(attempt_id, "ig-good")
        return _result(succeeded=True, post_id="ig-good", state=PublishAttemptState.REMOTE_ID_CONFIRMED)

    with patch.object(content_queue, "_run_full_pipeline", side_effect=simulate):
        result = content_queue.publish_next()

    assert result["id"] == good_id
    with sqlite3.connect(queue_db) as conn:
        conn.row_factory = sqlite3.Row
        bad_row = conn.execute("SELECT * FROM queue WHERE id=?", (bad_id,)).fetchone()
        good_row = conn.execute("SELECT * FROM queue WHERE id=?", (good_id,)).fetchone()
    assert bad_row["publish_error_code"] == "HASH_MISMATCH"
    assert bad_row["status"] == "pending"
    assert good_row["status"] == "published"


def test_recover_stuck_processing_rows_reverts_to_ready_or_pending(queue_db, tmp_path):
    """A row still 'processing' means the thread that claimed it (via a
    now-restarted/crashed process) never reached its `finally` block to
    release the claim - nothing in a fresh process can still be working on
    it, so it must be recovered rather than left stuck forever."""
    rendered_id = db.enqueue_v2(metadata(), CollectionMethod.NEWS_COLLECTOR)
    unrendered_id = db.enqueue_v2(
        QueueMetadataV2(
            topic="다른 뉴스",
            source_title="다른 원문",
            source_url="https://example.com/other",
            context="다른 충분한 문맥",
            evidence=[{"title": "다른 원문", "url": "https://example.com/other"}],
        ),
        CollectionMethod.NEWS_COLLECTOR,
    )
    with sqlite3.connect(queue_db) as conn:
        conn.execute(
            "UPDATE queue SET status='processing', image_dir=?, "
            "publish_attempt_id='stale-attempt', publish_attempt_state='STARTED' WHERE id=?",
            (str(tmp_path), rendered_id),
        )
        conn.execute("UPDATE queue SET status='processing' WHERE id=?", (unrendered_id,))

    recovered = db.recover_stuck_processing_rows()

    assert recovered == 2
    with sqlite3.connect(queue_db) as conn:
        conn.row_factory = sqlite3.Row
        rendered_row = conn.execute(
            "SELECT * FROM queue WHERE id=?", (rendered_id,)
        ).fetchone()
        unrendered_row = conn.execute(
            "SELECT * FROM queue WHERE id=?", (unrendered_id,)
        ).fetchone()
    assert rendered_row["status"] == "ready"
    assert rendered_row["publish_attempt_id"] is None
    assert unrendered_row["status"] == "pending"


def test_claim_queue_row_is_exclusive(queue_db):
    """Two concurrent callers (a scheduler cron and a dashboard click) must
    not both be able to claim the same row - this is the actual mutex
    dequeue_next() itself doesn't provide (it's a plain SELECT)."""
    row_id = db.enqueue_v2(metadata(), CollectionMethod.NEWS_COLLECTOR)
    assert db.claim_queue_row(row_id) is True
    assert db.claim_queue_row(row_id) is False  # already 'processing'
    with sqlite3.connect(queue_db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM queue WHERE id=?", (row_id,)).fetchone()
    assert row["status"] == "processing"


def test_unclaim_reverts_only_if_still_processing(queue_db):
    row_id = db.enqueue_v2(metadata(), CollectionMethod.NEWS_COLLECTOR)
    db.claim_queue_row(row_id)
    db.unclaim_queue_row(row_id, "ready")
    with sqlite3.connect(queue_db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM queue WHERE id=?", (row_id,)).fetchone()
    assert row["status"] == "ready"

    # Something else already resolved the status (e.g. published) in the
    # meantime - a stray unclaim call must not stomp that.
    db.mark_queue_status(row_id, "published")
    db.unclaim_queue_row(row_id, "pending")
    with sqlite3.connect(queue_db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM queue WHERE id=?", (row_id,)).fetchone()
    assert row["status"] == "published"


def test_publish_next_releases_its_claim_on_a_generation_only_run(queue_db, tmp_path):
    """A concurrent second caller must be able to see this row again (in its
    new resolved status) once publish_next() returns - the row must not be
    left stuck at 'processing'."""
    row_id = db.enqueue_v2(metadata(), CollectionMethod.NEWS_COLLECTOR)
    out_dir = tmp_path / "20260922_rendered"
    out_dir.mkdir()
    (out_dir / "card_01_cover.png").write_bytes(b"")
    generated = PipelineResult(
        image_paths=[out_dir / "card_01_cover.png"],
        generation_succeeded=True,
        publish_requested=False,
        publish_succeeded=False,
        ig_post_id=None,
        permalink=None,
        failure_stage=None,
        error_code=None,
    )
    with patch.object(content_queue, "_run_full_pipeline", return_value=generated):
        content_queue.publish_next(publish_to_ig=False)

    with sqlite3.connect(queue_db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM queue WHERE id=?", (row_id,)).fetchone()
    assert row["status"] == "ready"  # not stuck at 'processing'


def test_clear_queue_error_reverts_to_ready_when_a_render_is_cached(queue_db, tmp_path):
    row_id = db.enqueue_v2(metadata(), CollectionMethod.NEWS_COLLECTOR)
    out_dir = tmp_path / "20260922_rendered"
    with sqlite3.connect(queue_db) as conn:
        conn.execute(
            """UPDATE queue SET status='ready', image_dir=?,
                   publish_error_code='REMOTE_PUBLISH_PERSISTENCE_UNCERTAIN',
                   publish_attempt_id='attempt-1', publish_attempt_state='STARTED'
               WHERE id=?""",
            (str(out_dir), row_id),
        )

    assert db.clear_queue_error(row_id) is True

    with sqlite3.connect(queue_db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM queue WHERE id=?", (row_id,)).fetchone()
    assert row["status"] == "ready"
    assert row["publish_error_code"] is None
    assert row["publish_attempt_id"] is None
    assert row["publish_attempt_state"] == "NOT_ATTEMPTED"


def test_clear_queue_error_reverts_to_pending_without_a_cached_render(queue_db):
    row_id = db.enqueue_v2(metadata(), CollectionMethod.NEWS_COLLECTOR)
    with sqlite3.connect(queue_db) as conn:
        conn.execute(
            "UPDATE queue SET publish_error_code='UNKNOWN_PIPELINE_EXCEPTION' WHERE id=?",
            (row_id,),
        )

    assert db.clear_queue_error(row_id) is True

    with sqlite3.connect(queue_db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM queue WHERE id=?", (row_id,)).fetchone()
    assert row["status"] == "pending"
    assert row["publish_error_code"] is None


@pytest.mark.parametrize("blocked_status", ["published", "skipped", "processing"])
def test_clear_queue_error_refuses_published_skipped_or_in_flight_rows(queue_db, blocked_status):
    row_id = db.enqueue_v2(metadata(), CollectionMethod.NEWS_COLLECTOR)
    with sqlite3.connect(queue_db) as conn:
        conn.execute(
            "UPDATE queue SET status=?, publish_error_code='SOME_ERROR' WHERE id=?",
            (blocked_status, row_id),
        )

    assert db.clear_queue_error(row_id) is False

    with sqlite3.connect(queue_db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM queue WHERE id=?", (row_id,)).fetchone()
    assert row["status"] == blocked_status
    assert row["publish_error_code"] == "SOME_ERROR"


def test_publish_specific_targets_the_exact_row_even_when_queue_order_differs(
    queue_db, tmp_path
):
    """Reproduces a real bug: a human previews and approves one specific
    'ready' row, but publish_next()'s queue-order pick silently processes a
    completely different (unreviewed) row instead - e.g. because the
    previewed row has a stuck non-retryable error from an earlier failed
    attempt and gets skipped by dequeue_next(). publish_specific() must
    never substitute a different row."""
    blocked_id = db.enqueue_v2(metadata(), CollectionMethod.NEWS_COLLECTOR)
    with sqlite3.connect(queue_db) as conn:
        conn.execute(
            "UPDATE queue SET publish_error_code='REMOTE_PUBLISH_PERSISTENCE_UNCERTAIN' "
            "WHERE id=?",
            (blocked_id,),
        )

    reviewed_meta = QueueMetadataV2(
        topic="검토된 기사",
        source_title="검토된 원문",
        source_url="https://example.com/reviewed",
        context="검토된 충분한 문맥",
        evidence=[{"title": "검토된 원문", "url": "https://example.com/reviewed"}],
    )
    reviewed_id = db.enqueue_v2(reviewed_meta, CollectionMethod.NEWS_COLLECTOR)
    out_dir = tmp_path / "reviewed_render"
    out_dir.mkdir()
    (out_dir / "card_01_cover.png").write_bytes(b"fake-png")
    (out_dir / "script.json").write_text(
        '{"hook": "hook text", "hashtags": ["#test"]}', encoding="utf-8"
    )
    with sqlite3.connect(queue_db) as conn:
        conn.execute(
            "UPDATE queue SET status='ready', image_dir=? WHERE id=?",
            (str(out_dir), reviewed_id),
        )

    other_meta = QueueMetadataV2(
        topic="다른 미검토 기사",
        source_title="다른 원문",
        source_url="https://example.com/unreviewed",
        context="다른 충분한 문맥",
        evidence=[{"title": "다른 원문", "url": "https://example.com/unreviewed"}],
    )
    db.enqueue_v2(other_meta, CollectionMethod.NEWS_COLLECTOR)

    # 대기열 순서로 보면 blocked_id가 막혀 있으니 dequeue_next()는 세 번째
    # (미검토) 기사로 넘어갈 것이다 - publish_specific()은 그러면 안 된다.
    with patch.object(content_queue, "_run_full_pipeline") as pipeline, patch(
        "src.agents.publisher.publish", return_value="ig-reviewed"
    ) as publish:
        result = content_queue.publish_specific(reviewed_id, publish_to_ig=True)
        pipeline.assert_not_called()
        publish.assert_called_once()

    assert result["id"] == reviewed_id
    with sqlite3.connect(queue_db) as conn:
        conn.row_factory = sqlite3.Row
        reviewed_row = conn.execute(
            "SELECT * FROM queue WHERE id=?", (reviewed_id,)
        ).fetchone()
    assert reviewed_row["status"] == "published"
    assert reviewed_row["ig_post_id"] == "ig-reviewed"


def test_publish_specific_refuses_an_already_published_row(queue_db):
    row_id = db.enqueue_v2(metadata(), CollectionMethod.NEWS_COLLECTOR)
    with sqlite3.connect(queue_db) as conn:
        conn.execute(
            "UPDATE queue SET status='published', ig_post_id='ig-1' WHERE id=?",
            (row_id,),
        )

    with patch.object(content_queue, "_run_full_pipeline") as pipeline:
        result = content_queue.publish_specific(row_id, publish_to_ig=True)
        pipeline.assert_not_called()

    assert result is None


def test_publish_specific_refuses_a_row_already_claimed_by_another_process(queue_db):
    row_id = db.enqueue_v2(metadata(), CollectionMethod.NEWS_COLLECTOR)
    assert db.claim_queue_row(row_id) is True  # simulate a concurrent claim

    with patch.object(content_queue, "_run_full_pipeline") as pipeline:
        result = content_queue.publish_specific(row_id, publish_to_ig=True)
        pipeline.assert_not_called()

    assert result is None
    with sqlite3.connect(queue_db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM queue WHERE id=?", (row_id,)).fetchone()
    assert row["status"] == "processing"  # untouched - still the other claim's


def test_two_concurrent_publish_next_calls_never_both_take_the_same_row(queue_db):
    """Simulates a scheduler cron and a dashboard click racing to process
    the same front-of-queue row at the same moment. Only one may proceed;
    the other must see an empty queue rather than also running the
    pipeline for that row."""
    row_id = db.enqueue_v2(metadata(), CollectionMethod.NEWS_COLLECTOR)
    calls = []

    def simulate(topic, context, angle, publish, attempt_id, before_publish, on_remote_id, source_lineage):
        calls.append(1)
        before_publish(attempt_id)
        on_remote_id(attempt_id, "ig-1")
        return _result(succeeded=True, post_id="ig-1", state=PublishAttemptState.REMOTE_ID_CONFIRMED)

    # 두 번째 호출자가 먼저 도착한 것처럼: 첫 호출 전에 이미 claim된 상태를
    # 흉내 낸다 (실제로는 서로 다른 프로세스가 거의 동시에 dequeue_next를
    # 부르는 상황).
    assert db.claim_queue_row(row_id) is True

    with patch.object(content_queue, "_run_full_pipeline", side_effect=simulate):
        result = content_queue.publish_next()

    assert result is None  # claimed by "someone else" -> no other row to try
    assert calls == []


def test_generation_only_run_persists_image_dir_and_marks_ready(queue_db, tmp_path):
    row_id = db.enqueue_v2(metadata(), CollectionMethod.NEWS_COLLECTOR)
    out_dir = tmp_path / "20260922_rendered"
    out_dir.mkdir()
    (out_dir / "card_01_cover.png").write_bytes(b"")
    generated = PipelineResult(
        image_paths=[out_dir / "card_01_cover.png"],
        generation_succeeded=True,
        publish_requested=False,
        publish_succeeded=False,
        ig_post_id=None,
        permalink=None,
        failure_stage=None,
        error_code=None,
    )

    with patch.object(content_queue, "_run_full_pipeline", return_value=generated):
        result = content_queue.publish_next(publish_to_ig=False)

    assert result["id"] == row_id
    with sqlite3.connect(queue_db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM queue WHERE id=?", (row_id,)).fetchone()
    assert row["status"] == "ready"
    assert row["image_dir"] == str(out_dir)


def test_ready_row_is_dequeued_and_published_from_cached_render_without_regenerating(
    queue_db, tmp_path
):
    """A row a human already approved (status='ready', image_dir set) must be
    published from that exact render on the next publish_to_ig=True call, not
    regenerated - regenerating could produce different cards than the ones
    that were reviewed."""
    row_id = db.enqueue_v2(metadata(), CollectionMethod.NEWS_COLLECTOR)
    out_dir = tmp_path / "20260922_rendered"
    out_dir.mkdir()
    (out_dir / "card_01_cover.png").write_bytes(b"fake-png")
    (out_dir / "script.json").write_text(
        '{"hook": "hook text", "hashtags": ["#test"]}', encoding="utf-8"
    )
    with sqlite3.connect(queue_db) as conn:
        conn.execute(
            "UPDATE queue SET status='ready', image_dir=? WHERE id=?",
            (str(out_dir), row_id),
        )

    with patch.object(content_queue, "_run_full_pipeline") as pipeline, patch(
        "src.agents.publisher.publish", return_value="ig-cached-1"
    ) as publish:
        result = content_queue.publish_next(publish_to_ig=True)
        pipeline.assert_not_called()
        publish.assert_called_once()

    assert result["id"] == row_id
    with sqlite3.connect(queue_db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM queue WHERE id=?", (row_id,)).fetchone()
    assert row["status"] == "published"
    assert row["ig_post_id"] == "ig-cached-1"


def test_cached_publish_uploads_video_not_static_fallback_when_both_exist(
    queue_db, tmp_path
):
    """A slide with a composited video renders both card_02_content.mp4 (the
    video) and card_02_content.png (a static fallback with the same stem).
    The human-review /preview page already prefers the .mp4 per slide - the
    actual publish must select the same file, or the video silently never
    makes it to Instagram even though it was reviewed and approved."""
    row_id = db.enqueue_v2(metadata(), CollectionMethod.NEWS_COLLECTOR)
    out_dir = tmp_path / "20260922_rendered"
    out_dir.mkdir()
    (out_dir / "card_01_cover.png").write_bytes(b"fake-png")
    (out_dir / "card_02_content.png").write_bytes(b"fake-png-fallback")
    (out_dir / "card_02_content.mp4").write_bytes(b"fake-mp4")
    (out_dir / "script.json").write_text(
        '{"hook": "hook text", "hashtags": ["#test"]}', encoding="utf-8"
    )
    with sqlite3.connect(queue_db) as conn:
        conn.execute(
            "UPDATE queue SET status='ready', image_dir=? WHERE id=?",
            (str(out_dir), row_id),
        )

    with patch.object(content_queue, "_run_full_pipeline") as pipeline, patch(
        "src.agents.publisher.publish", return_value="ig-video-1"
    ) as publish:
        content_queue.publish_next(publish_to_ig=True)
        pipeline.assert_not_called()

    uploaded = publish.call_args.kwargs["image_paths"]
    assert out_dir / "card_02_content.mp4" in uploaded
    assert out_dir / "card_02_content.png" not in uploaded


def test_publish_cached_render_with_no_pngs_becomes_retryable_pending(queue_db, tmp_path):
    """If the cached render's PNGs are gone by the time this actually runs
    (deleted/moved after the row went 'ready'), the row must become
    retryable (back to 'pending'), not permanently stuck - the row itself
    was never invalid, only its cached files are gone."""
    row_id = db.enqueue_v2(metadata(), CollectionMethod.NEWS_COLLECTOR)
    out_dir = tmp_path / "20260922_missing"
    out_dir.mkdir()  # exists, but has no PNGs
    with sqlite3.connect(queue_db) as conn:
        conn.execute("UPDATE queue SET status='ready' WHERE id=?", (row_id,))

    with patch("src.agents.publisher.publish") as publish:
        result = content_queue._publish_cached_render(
            row_id, str(out_dir), "topic", None, None, None
        )
        publish.assert_not_called()

    assert result is None
    with sqlite3.connect(queue_db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM queue WHERE id=?", (row_id,)).fetchone()
    assert row["status"] == "pending"
    assert row["image_dir"] == ""
    assert row["publish_error_code"] is None


def test_publish_cached_render_with_corrupted_script_json_becomes_retryable_pending(
    queue_db, tmp_path
):
    row_id = db.enqueue_v2(metadata(), CollectionMethod.NEWS_COLLECTOR)
    out_dir = tmp_path / "20260922_badscript"
    out_dir.mkdir()
    (out_dir / "card_01_cover.png").write_bytes(b"fake-png")
    (out_dir / "script.json").write_text("{not valid json", encoding="utf-8")
    with sqlite3.connect(queue_db) as conn:
        conn.execute("UPDATE queue SET status='ready' WHERE id=?", (row_id,))

    with patch("src.agents.publisher.publish") as publish:
        result = content_queue._publish_cached_render(
            row_id, str(out_dir), "topic", None, None, None
        )
        publish.assert_not_called()

    assert result is None
    with sqlite3.connect(queue_db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM queue WHERE id=?", (row_id,)).fetchone()
    assert row["status"] == "pending"
    assert row["image_dir"] == ""


def test_cached_publish_normalizes_non_list_hashtags(queue_db, tmp_path):
    """script.json's hashtags can be malformed (null, or not a list) if it
    was hand-edited or written by an older schema - this must not reach
    ig_publisher.publish() as a non-list and blow up with a TypeError."""
    row_id = db.enqueue_v2(metadata(), CollectionMethod.NEWS_COLLECTOR)
    out_dir = tmp_path / "20260922_badtags"
    out_dir.mkdir()
    (out_dir / "card_01_cover.png").write_bytes(b"fake-png")
    (out_dir / "script.json").write_text(
        '{"hook": "hook text", "hashtags": null}', encoding="utf-8"
    )
    with sqlite3.connect(queue_db) as conn:
        conn.execute(
            "UPDATE queue SET status='ready', image_dir=? WHERE id=?",
            (str(out_dir), row_id),
        )

    with patch("src.agents.publisher.publish", return_value="ig-1") as publish:
        result = content_queue.publish_next(publish_to_ig=True)

    assert result["id"] == row_id
    publish.assert_called_once_with(image_paths=[out_dir / "card_01_cover.png"], hook="hook text", hashtags=[])


def dated_metadata(name: str, published_at: str = "") -> QueueMetadataV2:
    return QueueMetadataV2(
        topic=f"검증된 AI 뉴스 {name}",
        source_title=f"원문 기사 {name}",
        source_url=f"https://example.com/{name}",
        context=f"검증에 사용할 충분한 기사 문맥 {name}",
        evidence=[
            {
                "title": f"원문 기사 {name}",
                "url": f"https://example.com/{name}",
                "published_at": published_at,
            }
        ],
    )


def _ready_with_cards(queue_db, tmp_path, row_id, name):
    out_dir = tmp_path / f"20261009_{name}"
    out_dir.mkdir()
    (out_dir / "card_01_cover.png").write_bytes(b"fake-png")
    (out_dir / "script.json").write_text('{"hook": "h", "hashtags": ["#a"]}', encoding="utf-8")
    with sqlite3.connect(queue_db) as conn:
        conn.execute(
            "UPDATE queue SET status='ready', image_dir=? WHERE id=?", (str(out_dir), row_id)
        )
    return out_dir


def _row(queue_db, row_id):
    with sqlite3.connect(queue_db) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute("SELECT * FROM queue WHERE id=?", (row_id,)).fetchone()


def test_the_queue_list_names_each_candidates_outlet_and_date(queue_db):
    from src.dashboard import app as dashboard

    dated = db.enqueue_v2(
        dated_metadata("a", "2026-10-07"), CollectionMethod.NEWS_COLLECTOR
    )
    legacy = db.enqueue_v2(dated_metadata("b"), CollectionMethod.NEWS_COLLECTOR)

    assert "Example · 2026.10.07" in dashboard._candidate_source_label(db.get_queue_row(dated))
    assert "Example" in dashboard._candidate_source_label(db.get_queue_row(legacy))
    assert "2026" not in dashboard._candidate_source_label(db.get_queue_row(legacy))


def test_a_row_with_unreadable_metadata_shows_no_label():
    from src.dashboard import app as dashboard

    assert dashboard._candidate_source_label({"metadata_json": "{not json"}) == ""
    assert dashboard._candidate_source_label({"metadata_json": None}) == ""


def test_a_card_approved_days_later_is_not_posted_if_its_article_went_stale(queue_db, tmp_path):
    from datetime import date

    row_id = db.enqueue_v2(dated_metadata("a", "2025-07-07"), CollectionMethod.NEWS_COLLECTOR)
    _ready_with_cards(queue_db, tmp_path, row_id, "a")

    with patch("src.agents.publisher.publish") as publish:
        result = content_queue.publish_next(publish_to_ig=True)

    assert result is None
    publish.assert_not_called()
    row = _row(queue_db, row_id)
    assert row["publish_error_code"] == "SOURCE_UNSUITABLE_SOURCE_TOO_OLD"
    assert row["status"] == "ready"  # still reviewable, just not postable


def test_a_fresh_card_is_posted_normally(queue_db, tmp_path):
    from datetime import date

    row_id = db.enqueue_v2(
        dated_metadata("a", date.today().isoformat()), CollectionMethod.NEWS_COLLECTOR
    )
    _ready_with_cards(queue_db, tmp_path, row_id, "a")

    with patch("src.agents.publisher.publish", return_value="ig-1") as publish:
        result = content_queue.publish_next(publish_to_ig=True)

    assert result["id"] == row_id
    publish.assert_called_once()
    assert _row(queue_db, row_id)["status"] == "published"


def test_a_legacy_row_without_a_recorded_date_is_checked_against_its_page(
    queue_db, tmp_path, monkeypatch
):
    from datetime import date

    row_id = db.enqueue_v2(dated_metadata("legacy"), CollectionMethod.NEWS_COLLECTOR)
    _ready_with_cards(queue_db, tmp_path, row_id, "legacy")

    monkeypatch.setattr(content_queue, "fetch_published_date", lambda url, **kw: date(2025, 7, 7))
    with patch("src.agents.publisher.publish") as publish:
        assert content_queue.publish_next(publish_to_ig=True) is None
        publish.assert_not_called()
    assert _row(queue_db, row_id)["publish_error_code"] == "SOURCE_UNSUITABLE_SOURCE_TOO_OLD"


def test_a_legacy_row_whose_page_date_cannot_be_read_is_not_blocked(queue_db, tmp_path):
    row_id = db.enqueue_v2(dated_metadata("legacy"), CollectionMethod.NEWS_COLLECTOR)
    _ready_with_cards(queue_db, tmp_path, row_id, "legacy")

    with patch("src.agents.publisher.publish", return_value="ig-1") as publish:
        assert content_queue.publish_next(publish_to_ig=True)["id"] == row_id
        publish.assert_called_once()


def _three_candidates(queue_db, tmp_path):
    from datetime import date

    today = date.today().isoformat()
    ids = [
        db.enqueue_v2(dated_metadata(n, today), CollectionMethod.NEWS_COLLECTOR)
        for n in ("a", "b", "c")
    ]
    _ready_with_cards(queue_db, tmp_path, ids[0], "a")          # the one that gets posted
    _ready_with_cards(queue_db, tmp_path, ids[2], "c")          # a candidate with a preview
    return ids


def test_posting_one_candidate_clears_the_others(queue_db, tmp_path):
    a, b, c = _three_candidates(queue_db, tmp_path)

    with patch("src.agents.publisher.publish", return_value="ig-1"):
        assert content_queue.publish_next(publish_to_ig=True)["id"] == a

    assert [_row(queue_db, i)["status"] for i in (a, b, c)] == ["published", "skipped", "skipped"]


def test_clearing_can_be_turned_off(queue_db, tmp_path, monkeypatch):
    monkeypatch.setenv("QUEUE_CLEAR_AFTER_PUBLISH", "false")
    a, b, c = _three_candidates(queue_db, tmp_path)

    with patch("src.agents.publisher.publish", return_value="ig-1"):
        content_queue.publish_next(publish_to_ig=True)

    assert [_row(queue_db, i)["status"] for i in (a, b, c)] == ["published", "pending", "ready"]


def test_a_candidate_being_generated_right_now_is_left_alone(queue_db, tmp_path):
    a, b, c = _three_candidates(queue_db, tmp_path)
    assert db.claim_queue_row(b)  # b is mid-generation

    with patch("src.agents.publisher.publish", return_value="ig-1"):
        content_queue.publish_next(publish_to_ig=True)

    assert [_row(queue_db, i)["status"] for i in (a, b, c)] == ["published", "processing", "skipped"]


def test_a_blocked_row_is_kept_for_a_person_to_see_not_swept_away(queue_db, tmp_path):
    a, b, c = _three_candidates(queue_db, tmp_path)
    db.mark_queue_error(b, "SOURCE_UNSUITABLE_SOURCE_TOO_OLD")

    with patch("src.agents.publisher.publish", return_value="ig-1"):
        content_queue.publish_next(publish_to_ig=True)

    assert _row(queue_db, b)["status"] == "pending"
    assert _row(queue_db, b)["publish_error_code"] == "SOURCE_UNSUITABLE_SOURCE_TOO_OLD"
    assert _row(queue_db, c)["status"] == "skipped"


def test_a_failed_post_does_not_clear_anything(queue_db, tmp_path):
    a, b, c = _three_candidates(queue_db, tmp_path)

    with patch("src.agents.publisher.publish", side_effect=RuntimeError("instagram down")):
        content_queue.publish_next(publish_to_ig=True)

    assert _row(queue_db, b)["status"] == "pending"
    assert _row(queue_db, c)["status"] == "ready"


def test_cached_publish_posts_the_caption_the_reviewer_saw(queue_db, tmp_path):
    """The dashboard previews caption.txt; the post must carry that same text
    (source attribution included), not a caption rebuilt from hook + hashtags."""
    row_id = db.enqueue_v2(metadata(), CollectionMethod.NEWS_COLLECTOR)
    out_dir = tmp_path / "20260922_reviewed"
    out_dir.mkdir()
    (out_dir / "card_01_cover.png").write_bytes(b"fake-png")
    (out_dir / "caption.txt").write_text("검토한 캡션\n\n출처: TechCrunch\n\n#알고", encoding="utf-8")
    (out_dir / "script.json").write_text(
        '{"hook": "hook text", "hashtags": ["#알고"]}', encoding="utf-8"
    )
    with sqlite3.connect(queue_db) as conn:
        conn.execute(
            "UPDATE queue SET status='ready', image_dir=? WHERE id=?",
            (str(out_dir), row_id),
        )

    with patch("src.agents.publisher.publish", return_value="ig-1") as publish:
        content_queue.publish_next(publish_to_ig=True)

    publish.assert_called_once_with(
        image_paths=[out_dir / "card_01_cover.png"],
        hook="hook text",
        hashtags=["#알고"],
        caption="검토한 캡션\n\n출처: TechCrunch\n\n#알고",
    )


def test_human_rejection_marks_queue_skipped_without_remote_attempt(queue_db):
    row_id = db.enqueue_v2(metadata(), CollectionMethod.NEWS_COLLECTOR)
    rejected = PipelineResult(
        image_paths=[Path("card.png")],
        generation_succeeded=True,
        publish_requested=True,
        publish_succeeded=False,
        ig_post_id=None,
        permalink=None,
        failure_stage="approval",
        error_code="HUMAN_REJECTED",
        approval_decision="REJECTED",
    )

    with patch.object(content_queue, "_run_full_pipeline", return_value=rejected):
        assert content_queue.publish_next() is None

    with sqlite3.connect(queue_db) as conn:
        status = conn.execute(
            "SELECT status FROM queue WHERE id=?", (row_id,)
        ).fetchone()[0]
    assert status == "skipped"


def test_collection_reports_the_real_count_and_does_not_pad_with_extra_picks(queue_db):
    """Regression: the screen said "3개 추가 완료" while only 2 were queued. The report must say what
    really happened, and a rejected pick must not be replaced by pulling in more articles."""
    from datetime import datetime

    calls = []

    def fake_collect(exclude_urls=frozenset()):
        calls.append(1)
        n = len(calls)
        return SimpleNamespace(
            topic=f"주제{n}",
            selected_item=SimpleNamespace(
                title=f"기사{n}", url=f"https://example.com/new-{n}", summary="본문",
                published=datetime.now(),
            ),
        )

    def fake_freshness(published, **kw):
        stale = len(calls) == 2          # the 2nd pick is judged stale; the others are fresh
        return SimpleNamespace(ok=not stale, code="SOURCE_TOO_OLD", detail="3일 전 기사")

    with patch.object(content_queue, "_collect_news", side_effect=fake_collect),          patch.object(content_queue, "check_freshness", side_effect=fake_freshness),          patch.object(content_queue, "check_source_suitability", return_value=SimpleNamespace(ok=True)),          patch("src.qa.topic_source_guard.topic_matches_source", return_value=True),          patch("src.agents.trend_analyzer.build_locked_source_report"),          patch("src.services.generation_service.build_queue_metadata", side_effect=lambda *a, **k: metadata()),          patch.object(content_queue, "enqueue_v2", side_effect=iter([101, 102, 103])):
        ids = content_queue._fill_from_news(3)

    assert ids == [101, 102]               # 3 requested, 2 acceptable -> exactly 2, no padding
    assert len(calls) == 3
    report = content_queue.LAST_COLLECTION_REPORT
    assert report["requested"] == 3 and report["added"] == 2
    assert any("최신 기사가 아님" in reason for reason in report["skipped"])
