from __future__ import annotations

from types import SimpleNamespace

import pytest

import src.agents.design_renderer as renderer
from src.qa.caption_guard import figures, unsupported_figures, unsupported_hype
from src.schemas.card_news import CardNewsScript, Slide

CARDS = [
    "15억 달러 가치로 투자 유치 Nous Research는 $1.5 billion 기업가치를 인정받아 "
    "$90 million 규모의 Series B 투자를 유치했습니다.",
    "오픈소스 Hermes Agent는 24 million회 이상 복제되었으며 전 세계 AI 토큰 "
    "사용량의 약 2.5%를 차지하고 있습니다.",
    "연간 환산 매출은 약 $36 million 2026년 말 이전에 $100 million 돌파",
]


# ── reading figures ───────────────────────────────────────


@pytest.mark.parametrize(
    "text, kind, value",
    [
        ("15억", "num", 1.5e9),
        ("1.5억", "num", 1.5e8),
        ("2,400만 회", "num", 2.4e7),
        ("$1.5 billion", "num", 1.5e9),
        ("$1.5B", "num", 1.5e9),
        ("24 million", "num", 2.4e7),
        ("24M", "num", 2.4e7),
        ("38K", "num", 3.8e4),
        ("2.5%", "pct", 2.5),
        ("40퍼센트", "pct", 40.0),
        ("3조 원", "num", 3e12),
        ("천만 명", None, None),  # no digits: nothing to check
    ],
)
def test_figures_are_read_by_value(text, kind, value):
    found = figures(text)

    if kind is None:
        assert found == []
    else:
        assert [(f.kind, f.value) for f in found] == [(kind, value)]


def test_the_same_amount_written_differently_is_equal():
    pairs = [("15억", "$1.5 billion"), ("2400만", "24 million"), ("$90M", "90 million")]
    for korean_or_short, long_form in pairs:
        assert figures(korean_or_short)[0].matches(figures(long_form)[0])


def test_a_tenfold_difference_is_not_equal():
    assert not figures("1.5억")[0].matches(figures("15억")[0])


def test_percent_never_matches_a_plain_number():
    assert not figures("40%")[0].matches(figures("40만")[0])


@pytest.mark.parametrize("text", ["2026년 10월", "3가지 이유", "5개 기업", "Series B", "#2026"])
def test_years_small_counts_letters_and_hashtags_are_not_figures(text):
    assert figures(text) == []


# ── checking a caption against the cards ──────────────────


def test_the_tenfold_error_that_was_nearly_posted_is_caught():
    caption = (
        "15억 달러 가치, 누스의 비상 🚀\n"
        "· Nous Research가 1.5억 달러 기업가치를 인정받았어요!\n"
        "· 오픈소스 Hermes Agent, 2400만 회 복제!\n"
        "#알고 #카드뉴스"
    )

    assert unsupported_figures(caption, CARDS) == ["1.5억"]


def test_a_correct_caption_passes():
    caption = "15억 달러 가치\n· 2400만 회 복제\n· 매출 약 3,600만 달러\n#알고"

    assert unsupported_figures(caption, CARDS) == []


def test_a_caption_with_no_numbers_always_passes():
    assert unsupported_figures("누스, 기업용 에이전트를 내놨어요 🚀", CARDS) == []


def test_each_bad_figure_is_reported_once():
    assert unsupported_figures("99억 달러, 99억 달러, 7%", CARDS) == ["99억", "7%"]


# ── overstated wording ────────────────────────────────────


def test_a_strong_word_the_cards_never_use_is_flagged():
    # Real #12 caption: the card said "데이터 보안을 유지하며".
    caption = "· 기업 고객을 위한 서비스 출시, 데이터 보안도 완벽!\n#알고"

    assert unsupported_hype(caption, ["데이터 보안을 유지하며 업무를 수행하는 서비스"]) == ["완벽"]


def test_a_strong_word_the_cards_themselves_use_is_allowed():
    assert unsupported_hype("압도적 1위", ["시장 점유율 압도적 1위를 기록했습니다"]) == []


def test_hashtags_are_not_scanned_for_strong_words():
    assert unsupported_hype("좋은 소식 #최고", ["카드 본문"]) == []


# ── caption generation ────────────────────────────────────


def _script() -> CardNewsScript:
    return CardNewsScript(
        topic="Nous 15억 달러",
        hook="비즈니스 AI의 새로운 시대가 열렸다",
        hashtags=["#알고", "#카드뉴스"],
        slides=[
            Slide(slide_number=1, slide_type="cover", title="누스, 15억 달러 기업가치 달성", body="요약"),
            Slide(
                slide_number=2,
                slide_type="content",
                title="15억 달러 가치로 투자 유치",
                body="Nous Research는 $1.5 billion 기업가치를 인정받았습니다.",
                accent="$1.5 billion",
            ),
            Slide(
                slide_number=3,
                slide_type="content",
                title="오픈소스 에이전트의 높은 사용량",
                body="Hermes Agent는 24 million회 이상 복제되었습니다.",
            ),
            Slide(slide_number=4, slide_type="cta", title="어떻게 생각하나요", body="의견을 남겨주세요."),
        ],
    )


def _fake_llm(monkeypatch, replies: list[str]):
    prompts: list[str] = []

    class _LLM:
        def __init__(self, *a, **k):
            pass

        def invoke(self, prompt):
            prompts.append(prompt)
            return SimpleNamespace(content=replies.pop(0))

    monkeypatch.setattr("langchain_openai.ChatOpenAI", _LLM)
    return prompts


def test_a_correct_caption_is_used_as_written(monkeypatch):
    prompts = _fake_llm(monkeypatch, ["15억 달러 가치, 2400만 회 복제! 🚀\n\n@algo__kr\n#알고 #카드뉴스"])

    caption = renderer._generate_caption(_script(), "@algo__kr")

    assert caption.startswith("15억 달러 가치")
    assert len(prompts) == 1


def test_a_wrong_figure_triggers_one_rewrite_with_the_bad_figure_named(monkeypatch):
    prompts = _fake_llm(
        monkeypatch,
        [
            "1.5억 달러 가치! 🚀\n\n@algo__kr\n#알고 #카드뉴스",
            "15억 달러 가치! 🚀\n\n@algo__kr\n#알고 #카드뉴스",
        ],
    )

    caption = renderer._generate_caption(_script(), "@algo__kr")

    assert caption.startswith("15억 달러 가치")
    assert len(prompts) == 2
    assert "1.5억" in prompts[1] and "수정 지시" in prompts[1]


def test_an_overstated_caption_is_rewritten_too(monkeypatch):
    prompts = _fake_llm(
        monkeypatch,
        [
            "15억 달러 가치, 성능은 압도적! 🚀\n#알고 #카드뉴스",
            "15억 달러 가치를 인정받았어요 🚀\n#알고 #카드뉴스",
        ],
    )

    caption = renderer._generate_caption(_script(), "@algo__kr")

    assert "압도적" not in caption
    assert len(prompts) == 2 and "압도적" in prompts[1]


def test_a_caption_that_stays_wrong_is_replaced_by_the_safe_one(monkeypatch):
    _fake_llm(
        monkeypatch,
        ["1.5억 달러! 🚀\n#알고 #카드뉴스", "여전히 1.5억 달러! 🚀\n#알고 #카드뉴스"],
    )

    caption = renderer._generate_caption(_script(), "@algo__kr")

    assert "1.5억" not in caption
    assert "· 15억 달러 가치로 투자 유치" in caption
    assert "· 오픈소스 에이전트의 높은 사용량" in caption
    assert caption.rstrip().endswith("#알고 #카드뉴스")
    assert unsupported_figures(
        caption, [_script().hook] + [f"{s.title} {s.body}" for s in _script().slides]
    ) == []


def test_the_safe_caption_uses_only_verified_text():
    caption = renderer._safe_caption(_script(), "@algo__kr", "#알고 #카드뉴스")

    lines = caption.splitlines()
    assert lines[0] == "⚡ 비즈니스 AI의 새로운 시대가 열렸다"
    assert "@algo__kr" in lines
    assert caption.rstrip().endswith("#알고 #카드뉴스")
