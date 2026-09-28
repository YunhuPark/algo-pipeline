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
