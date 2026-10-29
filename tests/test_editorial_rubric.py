from __future__ import annotations

from types import SimpleNamespace

from src.agents import verifier
from src.schemas.card_news import CardNewsScript, Slide


def _script() -> CardNewsScript:
    return CardNewsScript(
        topic="T",
        hook="h",
        hashtags=["#a"],
        slides=[
            Slide(slide_number=1, slide_type="cover", title="100만 대 이상의 로봇", body="후크"),
            Slide(slide_number=2, slide_type="content", title="운영", body="아마존은 100만 대 이상의 로봇을 운영합니다."),
            Slide(slide_number=3, slide_type="cta", title="의견은", body="댓글로 남겨주세요."),
        ],
    )


def test_hook_rubric_rewards_specificity_not_exaggeration_and_forbids_hype_advice():
    rubric = verifier._EVAL_SYSTEM
    assert "구체적인 숫자·주체·변화·이득" in rubric
    assert "과장·낚시·감탄사" in rubric and "가점이 아니다" in rubric
    # the critic must not push the rewriter toward things the fact checks reject
    assert "제안하지 마십시오" in rubric and "원문에 없는 수치·사실·평가의 추가" in rubric
    assert "스크롤을 멈추게" not in rubric


def test_the_judge_runs_at_temperature_zero(monkeypatch):
    captured = {}

    class _FakeLLM:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def with_structured_output(self, _cls):
            from langchain_core.runnables import RunnableLambda

            return RunnableLambda(
                lambda _p: verifier._AIScore(
                    hook_power=8, readability=8, brand_tone=8, info_quality=8,
                    naturalness=8, completeness=8, feedback="ok",
                )
            )

    monkeypatch.setattr(verifier, "ChatOpenAI", _FakeLLM)
    verifier._ai_evaluate(_script())
    assert captured["temperature"] == 0


def test_rubric_matches_the_cover_standard():
    # the judge must not demand a company name or a cover longer than the layout allows
    rubric = verifier._EVAL_SYSTEM
    assert "22자 이내" in rubric
    assert "회사명이 없어도 높게 채점" in rubric


# ── a cover that meets the output standard cannot fail on the hook score alone ─────────

def _scores(hook, others=8.0, feedback="슬라이드1: 표지를 바꾸세요"):
    return SimpleNamespace(
        hook_power=hook, readability=others, brand_tone=others, info_quality=others,
        naturalness=others, completeness=others, feedback=feedback,
    )


def _with_cover(title):
    """A script that passes the rule checks, so only the AI scores decide the outcome."""
    return CardNewsScript(
        topic="T",
        hook="h",
        hashtags=["#알고", "#카드뉴스", "#AI뉴스", "#인공지능", "#테크뉴스"],
        slides=[
            Slide(slide_number=1, slide_type="cover", title=title, body="오픈AI가 광고 서비스를 확장합니다"),
            Slide(slide_number=2, slide_type="content", title="광고 서비스 확대",
                  body="오픈AI가 챗GPT 광고 서비스를 26개국에 추가로 도입해 전 세계 89개국으로 확대했다"),
            Slide(slide_number=3, slide_type="content", title="중남미 14개국 추가",
                  body="이번 확장으로 중남미 14개국에서 광고 서비스를 추가로 제공하게 되었다고 밝혔다"),
            Slide(slide_number=4, slide_type="cta", title="어디까지 확장될까", body="저장해두고 다음 소식도 받아보세요"),
        ],
    )


def test_a_standard_cover_is_not_failed_by_a_noisy_hook_score(monkeypatch):
    monkeypatch.setattr(verifier, "_ai_evaluate", lambda script: _scores(hook=6.0))
    result = verifier.verify(_with_cover("89개국 광고 서비스"), persona=None)
    assert result.passed and result.ai_scores["후킹력"] == verifier.HOOK_FLOOR
    assert verifier.HOOK_FLOOR >= verifier.PASS_THRESHOLD       # the pass line itself did not move


def test_a_vague_or_overlong_cover_still_fails_on_the_hook_score(monkeypatch):
    monkeypatch.setattr(verifier, "_ai_evaluate", lambda script: _scores(hook=6.0))
    # (a title over 22 chars never reaches the AI scores: the rule check rejects it first)
    for title in ("AI 혁신 소식", "80%"):
        result = verifier.verify(_with_cover(title), persona=None)
        assert not result.passed and result.ai_scores["후킹력"] == 6.0, title


def test_a_higher_hook_score_is_never_lowered(monkeypatch):
    monkeypatch.setattr(verifier, "_ai_evaluate", lambda script: _scores(hook=9.0))
    result = verifier.verify(_with_cover("89개국 광고 서비스"), persona=None)
    assert result.ai_scores["후킹력"] == 9.0


def test_other_dimensions_are_still_judged_and_the_rewriter_is_told_to_keep_the_cover(monkeypatch):
    monkeypatch.setattr(verifier, "_ai_evaluate", lambda script: _scores(hook=6.0, others=6.5))
    result = verifier.verify(_with_cover("89개국 광고 서비스"), persona=None)
    assert not result.passed and "가독성" in result.feedback
    assert "표지 제목은 이미 출력 기준" in result.feedback
