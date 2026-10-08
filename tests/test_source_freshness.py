from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.agents import content_queue
from src.qa import source_freshness as fresh

TODAY = date(2026, 10, 8)


# ── reading the date from a page ──────────────────────────


def test_reads_json_ld_date_published():
    html = (
        '<script type="application/ld+json">'
        '{"@type":"Article","datePublished":"2025-07-07","dateModified":"2026-09-22"}'
        "</script>"
    )

    assert fresh.extract_published_date(html) == date(2025, 7, 7)


def test_reads_nested_json_ld_graph():
    html = (
        '<script type="application/ld+json">'
        '{"@graph":[{"@type":"WebSite"},{"@type":"NewsArticle","datePublished":"2026-10-07T09:30:00+09:00"}]}'
        "</script>"
    )

    assert fresh.extract_published_date(html) == date(2026, 10, 7)


@pytest.mark.parametrize(
    "tag",
    [
        '<meta property="article:published_time" content="2026-10-07T01:02:03Z">',
        '<meta content="2026-10-07T01:02:03Z" property="article:published_time" />',
        "<meta name='pubdate' content='2026-10-07'>",
    ],
)
def test_reads_meta_tags_in_any_attribute_order(tag):
    assert fresh.extract_published_date(f"<head>{tag}</head>") == date(2026, 10, 7)


def test_falls_back_to_the_first_time_tag():
    html = '<article><time datetime="2026-10-06T08:00:00">Oct 6</time></article>'

    assert fresh.extract_published_date(html) == date(2026, 10, 6)


def test_modification_dates_are_ignored():
    # A page can be re-served yesterday and still describe a year-old report.
    html = (
        '<meta property="article:modified_time" content="2026-10-07">'
        '<meta name="last-modified" content="2026-10-07">'
    )

    assert fresh.extract_published_date(html) is None


def test_json_ld_beats_meta_and_malformed_json_is_survivable():
    html = (
        '<meta property="article:published_time" content="2026-10-01">'
        '<script type="application/ld+json">{"datePublished": "2026-10-07", BROKEN</script>'
    )

    assert fresh.extract_published_date(html) == date(2026, 10, 7)


def test_page_without_a_date_returns_none():
    assert fresh.extract_published_date("<html><body>hello</body></html>") is None
    assert fresh.extract_published_date("") is None


# ── fetching ──────────────────────────────────────────────


def test_fetch_reads_the_date_from_the_response():
    seen = {}

    def getter(url, **kwargs):
        seen["url"] = url
        return SimpleNamespace(
            text='<meta property="article:published_time" content="2026-10-07">'
        )

    assert fresh.fetch_published_date("https://example.com/a", getter=getter) == date(2026, 10, 7)
    assert seen["url"] == "https://example.com/a"


def test_fetch_failure_is_not_an_error_just_unknown():
    def boom(url, **kwargs):
        raise RuntimeError("blocked")

    assert fresh.fetch_published_date("https://example.com/a", getter=boom) is None
    assert fresh.fetch_published_date("ftp://example.com/a") is None


def test_feed_date_is_preferred_and_page_is_read_only_when_missing():
    calls: list[str] = []

    def fetcher(url):
        calls.append(url)
        return date(2026, 10, 1)

    assert fresh.resolve_published(datetime(2026, 10, 7, 5), "u", fetcher=fetcher) == TODAY - timedelta(days=1)
    assert calls == []
    assert fresh.resolve_published(None, "u", fetcher=fetcher) == date(2026, 10, 1)
    assert calls == ["u"]


# ── the rule ──────────────────────────────────────────────


@pytest.mark.parametrize("days_old", [0, 1, 2])
def test_recent_articles_pass(days_old):
    result = fresh.check_freshness(TODAY - timedelta(days=days_old), today=TODAY)

    assert result.ok is True
    assert result.age_days == days_old


def test_a_three_day_old_article_is_rejected():
    result = fresh.check_freshness(TODAY - timedelta(days=3), today=TODAY)

    assert result.ok is False
    assert result.code == "SOURCE_TOO_OLD"
    assert result.error_code == "SOURCE_UNSUITABLE_SOURCE_TOO_OLD"


def test_the_stale_report_that_reached_the_queue_is_rejected():
    # Real queue item #9: Samsung SDS insight report, datePublished 2025-07-07.
    result = fresh.check_freshness(date(2025, 7, 7), today=TODAY)

    assert result.ok is False
    assert result.code == "SOURCE_TOO_OLD"
    assert result.age_days == 458


def test_unknown_date_fails_closed_for_collection():
    result = fresh.check_freshness(None, today=TODAY)

    assert result.ok is False
    assert result.code == "PUBLISHED_DATE_UNKNOWN"


def test_legacy_rows_without_a_date_are_allowed_but_known_old_ones_are_not():
    assert fresh.check_freshness(None, today=TODAY, allow_unknown=True).ok is True
    assert (
        fresh.check_freshness(date(2025, 7, 7), today=TODAY, allow_unknown=True).ok
        is False
    )


def test_window_is_configurable_and_bad_values_fall_back(monkeypatch):
    monkeypatch.setenv("SOURCE_MAX_AGE_DAYS", "0")
    assert fresh.max_age_days() == 0
    assert fresh.check_freshness(TODAY - timedelta(days=1), today=TODAY).ok is False

    monkeypatch.setenv("SOURCE_MAX_AGE_DAYS", "7")
    assert fresh.check_freshness(TODAY - timedelta(days=7), today=TODAY).ok is True

    for bad in ("", "soon", "-3"):
        monkeypatch.setenv("SOURCE_MAX_AGE_DAYS", bad)
        assert fresh.max_age_days() == fresh.DEFAULT_MAX_AGE_DAYS


def test_future_dates_from_timezone_skew_are_fine():
    assert fresh.check_freshness(TODAY + timedelta(days=1), today=TODAY).ok is True


# ── wiring into collection ────────────────────────────────

LONG_BODY = "이 기사는 구체적인 사건과 숫자, 인물, 일정을 충분히 설명하는 본문입니다. " * 30


def _news(topic, published, url="https://example.com/a"):
    item = SimpleNamespace(url=url, title="title", summary=LONG_BODY, published=published)
    return SimpleNamespace(topic=topic, selected_item=item)


def test_collection_skips_stale_and_undated_articles(monkeypatch):
    now = datetime.now()
    sequence = iter(
        [
            _news("Oura의 22억 달러 IPO", now - timedelta(days=458)),      # stale
            _news("Snorkel AI, 35억 달러로 가치 3배 증가", None),            # undated
            _news("Nous 누스 리서치 15억 달러 기업가치", now),               # fresh
        ]
    )
    enqueued = []

    monkeypatch.setattr(content_queue, "_queued_source_urls", lambda: set())
    monkeypatch.setattr(
        content_queue, "_collect_news", lambda exclude_urls=frozenset(): next(sequence)
    )
    # The undated article's page has no readable date either.
    monkeypatch.setattr(
        content_queue,
        "resolve_published",
        lambda feed, url, **kw: feed.date() if feed is not None else None,
    )
    monkeypatch.setattr("src.qa.topic_source_guard.topic_matches_source", lambda *a: True)
    monkeypatch.setattr(
        "src.agents.trend_analyzer.build_locked_source_report",
        lambda topic, **kwargs: SimpleNamespace(topic=topic),
    )
    monkeypatch.setattr(
        "src.services.generation_service.build_queue_metadata",
        lambda topic, report: SimpleNamespace(
            topic=topic, evidence=[SimpleNamespace(published_at="")]
        ),
    )
    monkeypatch.setattr(
        content_queue,
        "enqueue_v2",
        lambda metadata, method: enqueued.append(metadata) or len(enqueued),
    )

    ids = content_queue._fill_from_news(3)

    assert ids == [1]
    assert [m.topic for m in enqueued] == ["Nous 누스 리서치 15억 달러 기업가치"]
    # The publication date is recorded so a later generation can re-check it.
    assert enqueued[0].evidence[0].published_at == now.date().isoformat()


def _queued_row(published_at: str) -> dict:
    import hashlib

    from src.schemas.queue_schemas import CollectionMethod, QueueMetadataV2

    metadata = QueueMetadataV2(
        topic="Snorkel AI 35억 달러 투자 유치",
        source_title="Source",
        source_url="https://example.com/source",
        context="verified context",
        evidence=[
            {
                "title": "Source",
                "url": "https://example.com/source",
                "published_at": published_at,
            }
        ],
    )
    canonical = metadata.canonical_json()
    return {
        "id": 1,
        "topic": metadata.topic,
        "context": metadata.context,
        "angle_hint": "",
        "image_dir": "",
        "status": "pending",
        "collection_method": CollectionMethod.NEWS_COLLECTOR.value,
        "metadata_schema_version": 2,
        "metadata_json": canonical,
        "lineage_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def _run_queued(published_at: str):
    from unittest.mock import patch

    with patch.object(content_queue, "dequeue_next", return_value=_queued_row(published_at)), \
         patch.object(content_queue, "claim_queue_row", return_value=True), \
         patch.object(content_queue, "unclaim_queue_row"), \
         patch.object(content_queue, "_run_full_pipeline", return_value=None) as pipeline, \
         patch.object(content_queue, "mark_queue_error") as mark_error:
        result = content_queue.publish_next(publish_to_ig=False, require_human_approval=False)
    return result, pipeline, mark_error


def test_an_item_that_went_stale_in_the_queue_is_blocked_before_generation():
    result, pipeline, mark_error = _run_queued("2025-07-07")

    assert result is None
    pipeline.assert_not_called()
    assert mark_error.call_args.args[1] == "SOURCE_UNSUITABLE_SOURCE_TOO_OLD"


def test_a_fresh_queued_item_is_generated_and_an_undated_legacy_item_is_not_blocked():
    for published_at in (datetime.now().date().isoformat(), ""):
        _, pipeline, mark_error = _run_queued(published_at)

        # The stubbed pipeline returns nothing, so EMPTY_PIPELINE_RESULT is
        # expected; what matters is that it was reached and not screened out.
        pipeline.assert_called_once()
        assert not any(
            str(call.args[1]).startswith("SOURCE_UNSUITABLE")
            for call in mark_error.call_args_list
        )


def test_stamp_is_harmless_for_metadata_that_cannot_take_a_date():
    content_queue._stamp_published("not-a-model", date(2026, 10, 7))
    content_queue._stamp_published(SimpleNamespace(evidence=[]), date(2026, 10, 7))
