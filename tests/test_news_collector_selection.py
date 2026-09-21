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


def test_auto_selection_falls_back_to_anchor_matching_alt_when_primary_mismatches():
    """LLM이 총정리 기사 안의 세부 사례로 topic을 지었지만 그 기사 자체에는
    앵커가 없는 경우, 다운스트림(assert_source_lineage_matches_topic)에서
    값비싼 원문 추출 이후에야 실패하는 대신, 앵커가 맞는 2순위 대안으로
    미리 바꿔야 한다.
    """
    roundup_item = NewsItem(
        title="2025년 IT 산업 총정리",
        summary="여러 기업의 다양한 소식을 한데 모아 정리했다.",
        source="테스트",
        url="https://example.com/roundup",
    )
    apple_item = NewsItem(
        title="Apple restructures its car project Titan team",
        summary="Apple reorganized the team behind the car project.",
        source="테스트",
        url="https://example.com/apple-titan",
    )
    selection = _SelectedTopic(
        selected_index=1,
        topic="애플 프로젝트 타이탄의 10년 여정",
        reason="흥미로운 세부 사례라서 골랐다.",
        context="총정리 기사에서 스쳐간 사례를 설명한다.",
        alt_selected_index=2,
        alt_topic="애플 프로젝트 타이탄 조직 개편",
        alt_reason="원문이 직접 다루는 사건이다.",
        alt_context="Apple이 타이탄 팀을 개편했다.",
    )

    with patch(
        "src.agents.news_collector._parse_rss_feeds",
        return_value=[roundup_item, apple_item],
    ), patch(
        "src.agents.news_collector._fetch_tavily_trends",
        return_value=[],
    ), patch(
        "src.agents.news_collector._select_topic_with_gpt",
        return_value=selection,
    ), patch(
        "src.agents.news_collector._quick_video_coverage",
        return_value=-1,
    ):
        result = collect_and_select()

    assert result.selected_item is apple_item
    assert "애플" in result.topic
    assert "타이탄" in result.topic
