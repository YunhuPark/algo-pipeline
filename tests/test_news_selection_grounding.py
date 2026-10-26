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


def test_on_persona_regex_matches_korean_glued_to_the_abbreviation():
    # 파이썬 정규식의 \b는 한글도 \w로 취급해서, "AI기술"·"IT업계"처럼
    # 영문 약어 바로 뒤에 공백 없이 한글이 붙으면(실제로 흔한 표기) 예전
    # \bAI\b/\bIT\b가 매칭에 실패해 온퍼소나 후보를 놓치고 있었다.
    assert nc._ON_PERSONA_RE.search("AI기술 도입 확산")
    assert nc._ON_PERSONA_RE.search("IT업계 재편 가속")
    assert nc._ON_PERSONA_RE.search("AI 기술 도입")  # 공백 있는 기존 케이스도 유지
    # 영문 단어 내부의 우연한 일치까지 넓어지면 안 된다.
    assert not nc._ON_PERSONA_RE.search("DETAILED 보고서")
    assert not nc._ON_PERSONA_RE.search("MAINSTREAM 뉴스")


def test_falls_back_to_every_candidate_when_few_are_grounded(monkeypatch):
    """수치 있는 기사가 적다고 생성이 막히면 안 된다.

    _MIN_ON_PERSONA_POOL(2) 미만으로 남겨야 카테고리 압축 단계도 건너뛰고
    "전체 후보" 단계까지 떨어진다 — 그래야 이 안전망 자체를 검증한다.
    """
    items = [_opinion(i) for i in range(8)] + [_grounded(i) for i in range(1)]
    _stub_llm(monkeypatch, selected_index=1)

    selected = nc._select_topic_with_gpt(items)

    assert items[selected.selected_index - 1].title == "논평0"


def _roundup(i: int) -> nc.NewsItem:
    """팩트는 많지만 특정 사건이 아니라 여러 소식을 나열한 총정리형 기사."""
    return nc.NewsItem(
        title=f"{2020 + i}년 IT 기술 동향 분석 및 핵심 트렌드 예측",
        summary=f"매출 {i + 1},200억 원으로 전년 대비 {i + 5}% 증가했다.",
        source="TechCrunch",
        url=f"https://example.com/roundup{i}",
    )


def _opinion_statement(i: int) -> nc.NewsItem:
    """발언/의견이 내용의 전부인 기사 — 팩트 기준은 겨우 넘기지만 실제로는
    한 사람의 주장·경고가 카드 대부분을 채운다."""
    return nc.NewsItem(
        title=f"CEO, AI 위험성 경고는 무책임하다고 주장{i}",
        summary=f"그는 AI 산업 규제가 매출 {i + 1}00억 원 손실로 이어질 것이라고 경고했다.",
        source="TechCrunch",
        url=f"https://example.com/opinion{i}",
    )


def test_ai_specific_candidate_outranks_it_business_only_candidates(monkeypatch):
    """AI 자체를 다루는 기사가 있으면, 카테고리는 부합해도 AI가 아닌
    IT/비즈니스 기사보다 우선해야 한다(이 계정의 1순위는 AI)."""
    items = [_grounded(i) for i in range(4)] + [_on_persona(i) for i in range(2)]
    _stub_llm(monkeypatch, selected_index=1)   # 압축된 AI 후보의 첫 번째

    selected = nc._select_topic_with_gpt(items)

    assert items[selected.selected_index - 1].title == "AI 스타트업 투자유치 0"


def test_opinion_statement_titled_article_is_excluded_when_alternative_exists(monkeypatch):
    """발언/주장이 내용의 전부인 기사는, 팩트 기준을 넘겨도 대안이 있으면
    후보 풀에서 빠져야 한다."""
    items = [_opinion_statement(i) for i in range(2)] + [_on_persona(i) for i in range(2)]
    _stub_llm(monkeypatch, selected_index=1)

    selected = nc._select_topic_with_gpt(items)

    assert items[selected.selected_index - 1].title == "AI 스타트업 투자유치 0"


def test_roundup_titled_article_is_excluded_from_the_candidate_pool(monkeypatch):
    """총정리·동향분석형 기사는 팩트가 많아도 후보 풀에서 빠져야 한다.

    안 그러면 GPT가 그 기사를 고른 뒤 topic만 그 안에 스쳐 지나가듯 언급된
    세부 사건으로 지어, 고정 원문(총정리 기사)과 topic이 어긋나는 실제
    불일치가 반복됐다.
    """
    items = [_roundup(i) for i in range(2)] + [_on_persona(i) for i in range(2)]
    _stub_llm(monkeypatch, selected_index=1)   # 압축된 후보의 첫 번째

    selected = nc._select_topic_with_gpt(items)

    assert items[selected.selected_index - 1].title == "AI 스타트업 투자유치 0"
