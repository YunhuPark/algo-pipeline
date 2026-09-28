from unittest.mock import patch

from src.agents.news_collector import (
    NewsItem,
    _SelectedTopic,
    collect_and_select,
)


def test_auto_selection_keeps_the_exact_selected_article():
    articles = [
        NewsItem(
            title="첫 번째 기사",
            summary="첫 번째 요약",
            source="테스트",
            url="https://example.com/first",
        ),
        NewsItem(
            title="GPT-6 Astra benchmark results",
            summary="The benchmark contains several measured results.",
            source="테스트",
            url="https://example.com/astra",
        ),
    ]
    selection = _SelectedTopic(
        selected_index=2,
        topic="GPT-6 Astra 벤치마크 공개",
        reason="구체적인 수치가 공개됐다.",
        context="선택된 두 번째 기사를 설명한다.",
    )

    with patch(
        "src.agents.news_collector._parse_rss_feeds",
        return_value=articles,
    ), patch(
        "src.agents.news_collector._fetch_tavily_trends",
        return_value=[],
    ), patch(
        "src.agents.news_collector._select_topic_with_gpt",
        return_value=selection,
    ):
        result = collect_and_select()

    assert result.selected_item is articles[1]
    assert result.selected_item.url == "https://example.com/astra"
    assert result.topic == "GPT-6 Astra 벤치마크 공개"


def test_auto_selection_rejects_candidates_without_real_urls():
    invalid = NewsItem(
        title="링크 없는 기사",
        summary="요약",
        source="테스트",
        url="",
    )

    with patch(
        "src.agents.news_collector._parse_rss_feeds",
        return_value=[invalid],
    ), patch(
        "src.agents.news_collector._fetch_tavily_trends",
        return_value=[],
    ):
        try:
            collect_and_select()
        except RuntimeError as exc:
            assert "수집된 뉴스가 없습니다" in str(exc)
        else:
            raise AssertionError("invalid source must be rejected")


def test_auto_selection_restores_source_anchor_to_generic_topic():
    article = NewsItem(
        title="Cognition hits $48B valuation in AI coding market",
        summary="Cognition raised funding at a new valuation.",
        source="테스트",
        url="https://example.com/cognition",
    )
    selection = _SelectedTopic(
        selected_index=1,
        topic="AI 코딩 시장의 새로운 가능성",
        reason="시장 변화가 크다.",
        context="선택된 기사를 설명한다.",
    )

    with patch(
        "src.agents.news_collector._parse_rss_feeds",
        return_value=[article],
    ), patch(
        "src.agents.news_collector._fetch_tavily_trends",
        return_value=[],
    ), patch(
        "src.agents.news_collector._select_topic_with_gpt",
        return_value=selection,
    ):
        result = collect_and_select()

    assert result.topic == "Cognition AI 코딩 시장의 새로운 가능성"
