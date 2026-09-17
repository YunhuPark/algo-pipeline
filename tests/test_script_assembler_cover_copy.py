from src.qa import script_assembler
from src.qa.script_assembler import ScriptAssembler
from src.schemas.card_news import Claim


def _verified_claim(claim_id: str) -> Claim:
    return Claim(
        claim_id=claim_id,
        claim_text="새 모델은 공식 문서에 소개된 핵심 기능을 그대로 갖췄다고 설명했다.",
        display_title="새 모델 공개",
        editorial_role="change",
        claim_type="factual",
        entities=["OpenAI"],
        evidence_ids=["evidence-1"],
        source_url="https://example.com/article",
        verification_status="verified",
    )


def test_assemble_falls_back_to_template_when_llm_copy_unavailable():
    """The default (patched by conftest) fallback keeps the deterministic copy."""
    script = ScriptAssembler.assemble(
        topic="OpenAI 발표",
        claims=[_verified_claim("c1")],
        num_cards=3,
    )
    assert script.slides[0].slide_type == "cover"
    assert script.hook == script.slides[0].body


def test_assemble_uses_llm_cover_copy_and_forwards_feedback(monkeypatch):
    """When the LLM helper succeeds, its copy replaces the static template,
    and editorial repair feedback reaches it — the concrete bug this test
    guards against: a fixed template can never respond to retry feedback."""

    captured: dict = {}

    def _fake_cover_copy(topic, claim_headlines, validation_feedback=""):
        captured["topic"] = topic
        captured["claim_headlines"] = claim_headlines
        captured["validation_feedback"] = validation_feedback
        return script_assembler._CoverCopy(
            cover_title="더 자극적인 제목",
            hook="한 줄 후크 문구",
            cta_title="저장하고 확인하세요",
            cta_body="지금 확인한 핵심 내용을 나중에 다시 보고 싶다면 저장해두세요.",
        )

    monkeypatch.setattr(script_assembler, "_generate_cover_copy", _fake_cover_copy)

    feedback = "슬라이드1 커버 제목은 조금 더 자극적이고 호기심을 유발하는 문구로 바꾸세요."
    script = ScriptAssembler.assemble(
        topic="OpenAI 발표",
        claims=[_verified_claim("c1")],
        num_cards=3,
        validation_feedback=feedback,
    )

    assert script.slides[0].title == "더 자극적인 제목"
    assert script.slides[0].body == "한 줄 후크 문구"
    assert script.slides[-1].title == "저장하고 확인하세요"
    assert captured["validation_feedback"] == feedback
    assert captured["claim_headlines"] == ["새 모델 공개"]
