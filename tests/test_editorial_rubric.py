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
