from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.agents import topic_refiner


def test_refine_topic_excludes_roundup_titled_candidates_from_the_prompt():
    """news_collector.py의 후보 압축과 같은 이유로, 총정리형 후보는 GPT가
    보기 전에 걸러져야 한다 — 안 그러면 GPT가 총정리 기사를 고르고 그 안의
    세부 사례로 topic을 지어, 정작 고정 원문과 어긋나는 실제 버그가 이 경로
    에서도 반복된다."""
    mock_tavily = MagicMock()
    mock_tavily.search.return_value = {
        "results": [
            {
                "title": "2025년 AI 업계 총정리",
                "content": "여러 기업의 소식을 모아 정리했다.",
                "url": "https://example.com/roundup",
            },
            {
                "title": "Cognition AI 코딩 에이전트 출시",
                "content": "Cognition이 새 AI 코딩 에이전트를 출시했다고 발표했다.",
                "url": "https://example.com/cognition-launch",
            },
        ]
    }
    captured_prompt = {}

    def _fake_invoke(self, prompt):
        captured_prompt["text"] = prompt
        return SimpleNamespace(
            content='{"selected": 1, "refined_topic": "Cognition AI 코딩 에이전트 출시", "reason": "테스트"}'
        )

    with patch("tavily.TavilyClient", return_value=mock_tavily), patch(
        "langchain_openai.ChatOpenAI.invoke", _fake_invoke
    ), patch("tavily.TavilyClient.extract", return_value={"results": []}):
        refined, reason, _content = topic_refiner.refine_topic("AI 에이전트 최신 트렌드")

    assert "총정리" not in captured_prompt["text"]
    assert refined == "Cognition AI 코딩 에이전트 출시"


def test_refine_topic_falls_back_to_original_when_refined_topic_mismatches_selection():
    """GPT가 고른 기사와 정제된 topic이 실제로 다른 사건을 가리키면, 근거
    없는 topic을 그대로 내보내는 대신 원본 주제로 되돌아가야 한다."""
    mock_tavily = MagicMock()
    mock_tavily.search.return_value = {
        "results": [
            {
                "title": "Cognition AI 코딩 에이전트 출시",
                "content": "Cognition이 새 AI 코딩 에이전트를 출시했다고 발표했다.",
                "url": "https://example.com/cognition-launch",
            }
        ]
    }

    def _fake_invoke(self, prompt):
        return SimpleNamespace(
            content='{"selected": 1, "refined_topic": "애플 비전 프로 신제품 공개", "reason": "테스트"}'
        )

    with patch("tavily.TavilyClient", return_value=mock_tavily), patch(
        "langchain_openai.ChatOpenAI.invoke", _fake_invoke
    ):
        refined, reason, content = topic_refiner.refine_topic("AI 에이전트 최신 트렌드")

    assert refined == "AI 에이전트 최신 트렌드"
    assert content == ""
