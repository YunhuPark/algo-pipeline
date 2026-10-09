from langchain_core.runnables import RunnableLambda

from src.agents import topic_archive_search, trend_analyzer
from src.qa.topic_source_guard import topic_matches_source
from src.schemas.card_news import TrendReport, TrendResult
from src.services.generation_service import collect_verified_lineage


def test_wwdc_guard_accepts_official_long_form_name():
    assert topic_matches_source(
        "애플 WWDC 2026 핵심 요약",
        "Apple Worldwide Developers Conference highlights",
        "Apple shared developer platform updates during the Worldwide Developers Conference.",
    )


def test_collect_verified_lineage_reselects_archive_when_recent_source_is_unrelated(monkeypatch):
    unrelated = TrendResult(
        title="McKinsey: companies build more software with agents",
        url="https://example.com/agents",
        content="Enterprise teams are changing how they buy software with AI agents.",
        score=1.7,
    )
    official = TrendResult(
        title="Apple WWDC 2026 developer highlights",
        url="https://developer.apple.com/news/?id=wwdc-2026",
        content=(
            "Apple WWDC 2026 brought new developer platform updates. "
            "The Worldwide Developers Conference covered changes across Apple's platforms. "
        ) * 12,
        score=3.0,
    )

    monkeypatch.setattr(
        trend_analyzer,
        "run",
        lambda topic, max_results=5: TrendReport(query=topic, results=[unrelated]),
    )
    monkeypatch.setattr(
        topic_archive_search,
        "collect_archive_matches",
        lambda topic, max_results=10: [official],
    )
    monkeypatch.setattr(
        trend_analyzer,
        "_enrich_article",
        lambda item, min_length=1000: item,
    )

    lineage = collect_verified_lineage("애플 WWDC 2026 핵심 요약")

    assert lineage.is_verified_ready
    assert lineage.source_title == official.title
    assert lineage.source_url == official.url
    assert all(
        "McKinsey" not in passage.text
        for passage in lineage.evidence_passages
    )


def test_pick_best_article_falls_back_when_gpt_pick_has_no_topic_anchor(monkeypatch):
    """GPT가 값비싼 크롤링 전에 주제와 무관한 기사를 고르면, 앵커가 맞는
    다른 후보로 미리 교체해야 한다 — news_collector.py에서 재현됐던 것과
    같은 종류의 topic-원문 드리프트를 이 검색 경로에서도 막는다."""
    mismatched = TrendResult(
        title="2025 IT 기술 총정리",
        url="https://example.com/it-roundup",
        content="여러 기업의 다양한 소식을 모아 정리했다.",
        score=1.0,
    )
    matching = TrendResult(
        title="Apple WWDC 2026 developer highlights",
        url="https://developer.apple.com/news/?id=wwdc-2026",
        content="Apple WWDC 2026 brought new developer platform updates.",
        score=0.9,
    )

    class _FakeLLM:
        def __init__(self, **_kwargs):
            pass

        def with_structured_output(self, _cls):
            return RunnableLambda(
                lambda _payload: trend_analyzer._BestArticle(
                    index=1, reason="R", related_indices=[]
                )
            )

    monkeypatch.setattr(trend_analyzer, "ChatOpenAI", _FakeLLM)
    monkeypatch.setattr(
        trend_analyzer, "_enrich_article", lambda item, min_length=1000: item
    )

    result = trend_analyzer._pick_best_article(
        [mismatched, matching], "애플 WWDC 2026 핵심 요약"
    )

    assert result.url == matching.url
