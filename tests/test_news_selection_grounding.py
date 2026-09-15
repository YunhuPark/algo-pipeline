from langchain_core.runnables import RunnableLambda

from src.agents import news_collector as nc


def _item(title: str, summary: str) -> nc.NewsItem:
    return nc.NewsItem(title=title, summary=summary, source="S", url="https://example.com/a")


def _opinion(i: int) -> nc.NewsItem:
    return _item(f"논평{i}", "일부 전문가들은 우려를 제기하고 있습니다.")


def _grounded(i: int) -> nc.NewsItem:
    return _item(f"데이터{i}", f"매출 {i + 1},200억 원으로 전년 대비 {i + 5}% 증가했다.")


def _stub_llm(monkeypatch, selected_index: int) -> None:
    """LLM이 pool 기준 selected_index를 고른 것처럼 만든다."""

    class _FakeLLM:
        def __init__(self, **_kwargs):
            pass

        def with_structured_output(self, _cls):
            return RunnableLambda(
                lambda _payload: nc._SelectedTopic(
                    selected_index=selected_index,
                    topic="T",
                    reason="R",
                    context="C",
                )
            )

    monkeypatch.setattr(nc, "ChatOpenAI", _FakeLLM)


def test_fact_count_separates_opinion_from_data():
    opinion = "Is Big Tech's AI slowdown a safety pact or a cartel? Critics question the motives."
    assert nc._fact_count(opinion) == 0
    assert nc._fact_count("매출 1,240억 달러, 전년 대비 12% 증가, 이용자 300만 명") >= 3


def test_selected_index_maps_back_to_the_original_list(monkeypatch):
    """후보를 압축해도 호출부가 쓰는 인덱스는 원본 기준이어야 한다.

    이 매핑이 어긋나면 검증 없이 다른 기사로 카드뉴스가 만들어진다.
    """
    items = [_opinion(i) for i in range(5)] + [_grounded(i) for i in range(5)]
    _stub_llm(monkeypatch, selected_index=1)   # 압축된 후보의 첫 번째

    selected = nc._select_topic_with_gpt(items)

    assert items[selected.selected_index - 1].title == "데이터0"


def test_falls_back_to_every_candidate_when_few_are_grounded(monkeypatch):
    """수치 있는 기사가 적다고 생성이 막히면 안 된다."""
    items = [_opinion(i) for i in range(8)] + [_grounded(i) for i in range(2)]
    _stub_llm(monkeypatch, selected_index=1)

    selected = nc._select_topic_with_gpt(items)

    assert items[selected.selected_index - 1].title == "논평0"
