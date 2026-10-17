from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from src.agents import content_queue
from src.qa.source_suitability import MIN_SOURCE_CHARS, check_source_suitability

LONG_BODY = ("이 기사는 구체적인 사건과 숫자, 인물, 일정을 충분히 설명하는 본문입니다. " * 30)


def test_generic_topic_is_blocked_before_generation():
    # Real queue item #10: failed EDITORIAL_TOPIC_MISMATCH on all 5 attempts.
    result = check_source_suitability("2026년 AI 트렌드 전망", LONG_BODY)

    assert result.ok is False
    assert result.code == "TOPIC_TOO_GENERIC"
    assert result.error_code == "SOURCE_UNSUITABLE_TOPIC_TOO_GENERIC"


def test_teaser_source_is_blocked_when_topic_asks_for_the_answer():
    # Real queue item #11: the article only says the reason comes next episode.
    body = LONG_BODY + " In the next episode, we'll explore why OLPC failed."

    result = check_source_suitability("OLPC의 $100 노트북 실패 원인", body)

    assert result.ok is False
    assert result.code == "TEASER_ONLY"


def test_teaser_is_ignored_when_topic_does_not_ask_for_the_answer():
    body = LONG_BODY + " Stay tuned for more coverage next week."

    assert check_source_suitability("Snorkel AI, 35억 달러로 가치 3배 증가", body).ok


def test_short_source_is_blocked():
    result = check_source_suitability("Oura의 22억 달러 IPO", "짧은 본문")

    assert result.ok is False
    assert result.code == "SOURCE_TOO_SHORT"
    assert str(MIN_SOURCE_CHARS) in result.detail


def test_length_rule_can_be_skipped_when_text_is_not_the_whole_evidence():
    assert check_source_suitability(
        "Oura의 22억 달러 IPO", "짧은 본문", check_length=False
    ).ok


@pytest.mark.parametrize(
    "topic",
    [
        # Topics of cards that were actually generated and published.
        "iPhone 애플의 2억 5천만 달러 Siri AI 합의",
        "Oura의 22억 달러 IPO",
        "Snorkel AI, 35억 달러로 가치 3배 증가",
        "SDS AI 도입 확산과 투자 집중",
        "Nous 누스 리서치 15억 달러 기업가치 달성 및 비즈니스 AI 에이전트 출시",
    ],
)
def test_topics_with_concrete_subjects_pass(topic):
    assert check_source_suitability(topic, LONG_BODY).ok is True


def _news(topic: str, summary: str):
    item = SimpleNamespace(
        url="https://example.com/a",
        title="title",
        summary=summary,
        published=datetime.now(),  # fresh, so only suitability is under test
    )
    return SimpleNamespace(topic=topic, selected_item=item)


def test_collection_skips_unsuitable_items_and_keeps_good_ones(monkeypatch):
    news_by_call = iter(
        [
            _news("2026년 AI 트렌드 전망", LONG_BODY),
            _news("Oura의 22억 달러 IPO", LONG_BODY),
        ]
    )
    enqueued: list[str] = []

    monkeypatch.setattr(content_queue, "_queued_source_urls", lambda: set())
    monkeypatch.setattr(
        content_queue, "_collect_news", lambda exclude_urls=frozenset(): next(news_by_call)
    )
    monkeypatch.setattr("src.qa.topic_source_guard.topic_matches_source", lambda *a: True)
    monkeypatch.setattr(
        "src.agents.trend_analyzer.build_locked_source_report",
        lambda topic, **kwargs: SimpleNamespace(topic=topic),
    )
    monkeypatch.setattr(
        "src.services.generation_service.build_queue_metadata",
        lambda topic, report: topic,
    )
    monkeypatch.setattr(
        content_queue,
        "enqueue_v2",
        lambda metadata, method: enqueued.append(metadata) or len(enqueued),
    )

    ids = content_queue._fill_from_news(2)

    assert enqueued == ["Oura의 22억 달러 IPO"]
    assert ids == [1]


def test_length_is_judged_on_the_full_article_not_the_short_feed_summary():
    """Regression: AI타임스/The Decoder feeds carry ~300-char summaries, so every article was rejected
    with "원문이 300자로 너무 짧습니다" even though the full article (fetched for generation) is long."""
    from types import SimpleNamespace
    from unittest.mock import patch

    from src.agents import content_queue

    short_summary = "AI 모델 발표 기사 요약입니다. " * 10        # ~170 chars, too short on its own
    full_article = "아마존은 로봇 100만 대를 운영합니다. " * 60    # long enough once fetched

    news = SimpleNamespace(
        topic="Google 구글의 Gemini 4 공개",
        selected_item=SimpleNamespace(
            title="Google's Gemini 4", url="https://example.com/g4", summary=short_summary,
            published=None,
        ),
    )
    report = SimpleNamespace(results=[SimpleNamespace(content=full_article)])

    with patch.object(content_queue, "_queued_source_urls", return_value=set()), \
         patch.object(content_queue, "_collect_news", return_value=news), \
         patch.object(content_queue, "resolve_published", return_value=None), \
         patch.object(content_queue, "check_freshness", return_value=SimpleNamespace(ok=True)), \
         patch("src.qa.topic_source_guard.topic_matches_source", return_value=True), \
         patch("src.agents.trend_analyzer.build_locked_source_report", return_value=report), \
         patch("src.services.generation_service.build_queue_metadata", return_value=SimpleNamespace()), \
         patch.object(content_queue, "_stamp_published"), \
         patch.object(content_queue, "enqueue_v2", return_value=77):
        ids = content_queue._fill_from_news(1)

    assert ids == [77]
