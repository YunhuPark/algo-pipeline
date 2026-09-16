import json
from types import SimpleNamespace
from unittest.mock import patch

from src.agents import news_collector, trend_analyzer
from src.schemas.card_news import TrendResult
from src.utils.rss_content import extract_feed_entry_content


def test_feed_entry_prefers_full_content_over_preview():
    full_text = "Cognition disclosed verified funding and valuation details. " * 40
    entry = {
        "summary": "<p>Short preview.</p>",
        "content": [{"value": f"<article><p>{full_text}</p></article>"}],
    }

    extracted = extract_feed_entry_content(entry)

    assert len(extracted) > 1000
    assert "<article>" not in extracted
    assert "Short preview" not in extracted


def test_news_collector_preserves_rss_embedded_article(monkeypatch):
    full_text = "The feed contains the complete verified article body. " * 45
    entry = SimpleNamespace(
        title="AI company publishes a measured update",
        link="https://example.com/article",
        summary="Short preview",
        content=[{"value": f"<p>{full_text}</p>"}],
    )
    monkeypatch.setattr(
        news_collector.feedparser,
        "parse",
        lambda _url: SimpleNamespace(entries=[entry]),
    )
    monkeypatch.setattr(news_collector, "RSS_FEEDS", [("Test", "https://example.com/feed")])

    items = news_collector._parse_rss_feeds()

    assert len(items) == 1
    assert len(items[0].summary) > 1000


def test_direct_and_feed_content_avoid_tavily_extract(monkeypatch):
    article = TrendResult(
        title="Verified update",
        url="https://example.com/article",
        content="Feed summary with distinct verified context. " * 8,
        score=2.0,
    )
    crawled = "Direct article paragraph with measured source facts. " * 24
    monkeypatch.setattr(trend_analyzer, "_crawl_article", lambda _url: crawled)
    monkeypatch.setattr(trend_analyzer, "TAVILY_API_KEY", "quota-key")

    with patch("tavily.TavilyClient") as tavily_client:
        enriched = trend_analyzer._enrich_article(article, min_length=1000)

    assert len(enriched.content) >= 1000
    tavily_client.assert_not_called()


def test_previous_verified_source_is_reused_for_the_exact_url(monkeypatch, tmp_path):
    cached_dir = tmp_path / "previous-run"
    cached_dir.mkdir()
    cached_content = "Previously verified source evidence. " * 40
    (cached_dir / "source_lineage.json").write_text(
        json.dumps(
            {
                "source_url": "https://example.com/article",
                "context": cached_content,
                "evidence_passages": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(trend_analyzer, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(
        trend_analyzer,
        "_crawl_article",
        lambda _url: (_ for _ in ()).throw(AssertionError("crawl should not run")),
    )

    enriched = trend_analyzer._enrich_article(
        TrendResult(
            title="Verified update",
            url="https://example.com/article",
            content="Short preview",
            score=2.0,
        ),
        min_length=1000,
    )

    assert len(enriched.content) >= 1000
    assert "Previously verified source evidence" in enriched.content


def test_cached_source_never_crosses_to_a_different_url(monkeypatch, tmp_path):
    cached_dir = tmp_path / "previous-run"
    cached_dir.mkdir()
    (cached_dir / "source_lineage.json").write_text(
        json.dumps(
            {
                "source_url": "https://example.com/other",
                "context": "Wrong article evidence. " * 100,
                "evidence_passages": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(trend_analyzer, "OUTPUT_DIR", tmp_path)

    assert trend_analyzer._load_cached_source("https://example.com/article") == ""


def test_direct_crawler_reads_json_ld_article_body(monkeypatch):
    article_body = "Structured article evidence with verified details. " * 30
    payload = json.dumps({"@type": "NewsArticle", "articleBody": article_body})
    response = SimpleNamespace(
        status_code=200,
        text=f'<html><script type="application/ld+json">{payload}</script><body></body></html>',
    )
    monkeypatch.setattr(trend_analyzer.httpx, "get", lambda *_args, **_kwargs: response)

    crawled = trend_analyzer._crawl_article("https://example.com/article")

    assert crawled.startswith("Structured article evidence")
    assert len(crawled) > 1000
