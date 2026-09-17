from langchain_core.runnables import RunnableLambda

from src.agents import news_collector as nc


def _item(title: str, summary: str) -> nc.NewsItem:
    # RSS_FEEDS에 실제로 있는 이름을 써야 "카테고리 자동 통과" 대상이 된다
    # (RSS는 소스 자체가 IT 전문 매체라 키워드 없이도 통과) — 임의 문자열
    # "S"는 더 이상 자동 통과되지 않는다.
    return nc.NewsItem(title=title, summary=summary, source="TechCrunch", url="https://example.com/a")


def _opinion(i: int) -> nc.NewsItem:
    return _item(f"논평{i}", "일부 전문가들은 우려를 제기하고 있습니다.")


def _grounded(i: int) -> nc.NewsItem:
    return _item(f"데이터{i}", f"매출 {i + 1},200억 원으로 전년 대비 {i + 5}% 증가했다.")


def _off_persona(i: int) -> nc.NewsItem:
    """Tavily(종합 검색) 출신에 팩트는 많지만 AI·IT·비즈니스와 무관한 기사."""
    return nc.NewsItem(
        title=f"연예인 스캔들{i}",
        summary=f"경찰은 사건 관계자 {i + 3}명을 조사했으며 피해 규모는 {i + 1}00억 원으로 추산된다.",
        source="Tavily",
        url="https://example.com/tavily-offtopic",
    )


def _on_persona(i: int) -> nc.NewsItem:
    """Tavily 출신이면서 AI·IT 키워드가 있는 기사."""
    return nc.NewsItem(
        title=f"AI 스타트업 투자유치 {i}",
        summary=(
            f"이 스타트업은 시리즈 {i + 1}00억 원 규모의 투자를 유치했으며 "
            f"이용자는 {i + 50}만 명으로 늘었다고 밝혔다."
        ),
        source="Tavily",
        url="https://example.com/tavily-it",
    )


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


def test_off_persona_item_does_not_outrank_on_persona_candidates(monkeypatch):
    """카테고리 무관 기사가 팩트를 훨씬 많이 담고 있어도, AI·IT 관련 후보가
    최소 기준(_MIN_ON_PERSONA_POOL)을 채우면 그쪽에 밀리지 않아야 한다 —
    프롬프트 지시만으로는 안 지켜지던 걸 코드로 강제한 부분.
    """
    items = (
        [_off_persona(i) for i in range(6)]      # 팩트 많음, 카테고리 무관
        + [_on_persona(i) for i in range(2)]      # 팩트 있음, 카테고리 부합
    )
    _stub_llm(monkeypatch, selected_index=1)   # 압축된 후보(온퍼소나 2건)의 첫 번째

    selected = nc._select_topic_with_gpt(items)

    assert items[selected.selected_index - 1].title == "AI 스타트업 투자유치 0"


def test_falls_back_to_every_candidate_when_few_are_grounded(monkeypatch):
    """수치 있는 기사가 적다고 생성이 막히면 안 된다.

    _MIN_ON_PERSONA_POOL(2) 미만으로 남겨야 카테고리 압축 단계도 건너뛰고
    "전체 후보" 단계까지 떨어진다 — 그래야 이 안전망 자체를 검증한다.
    """
    items = [_opinion(i) for i in range(8)] + [_grounded(i) for i in range(1)]
    _stub_llm(monkeypatch, selected_index=1)

    selected = nc._select_topic_with_gpt(items)

    assert items[selected.selected_index - 1].title == "논평0"
