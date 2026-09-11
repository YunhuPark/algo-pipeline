from src.agents.verifier import PASS_THRESHOLD, _rule_check
from src.qa.script_assembler import ScriptAssembler
from src.schemas.card_news import Claim


def _verified_claims() -> list[Claim]:
    bodies = [
        "애플은 개발자 행사에서 운영체제와 주요 플랫폼의 변경 사항을 공개하고 적용 범위를 함께 설명했다.",
        "이번 발표에는 여러 제품군에 적용되는 기능 변화와 개발자가 확인해야 할 제공 조건이 함께 담겼다.",
        "공개 자료는 기능별 지원 범위와 적용 대상을 구분해 이용자가 실제 변화를 확인할 수 있게 정리했다.",
        "발표 내용 가운데 일부 기능은 제공 시점과 대상이 달라 실제 사용 전 공식 조건을 확인할 필요가 있다.",
    ]
    roles = ["context", "change", "evidence", "limitation"]
    titles = ["발표 범위부터 확인", "제품군별 변화 공개", "공식 자료가 밝힌 조건", "적용 전 확인할 점"]
    return [
        Claim(
            claim_id=f"c{index}",
            claim_text=body,
            display_title=title,
            editorial_role=role,
            claim_type="factual",
            evidence_ids=["e1"],
            verification_status="verified",
        )
        for index, (body, role, title) in enumerate(zip(bodies, roles, titles), start=1)
    ]


def test_generic_summary_topic_gets_non_verbatim_safe_cover():
    topic = "애플 WWDC 2026 핵심 요약"

    script = ScriptAssembler.assemble(topic, _verified_claims(), num_cards=6)

    assert script.cover.title != topic
    assert "요약" not in script.cover.title
    assert len(script.cover.title) <= 22
    assert "?" in script.cover.title or "핵심" in script.cover.title
    assert _rule_check(script, expected_count=6) == []


def test_default_cta_is_contextual_and_within_rule_limits():
    topic = "애플 WWDC 2026 핵심 요약"

    script = ScriptAssembler.assemble(topic, _verified_claims(), num_cards=6)

    assert script.cta.title == "가장 눈에 띈 포인트는?"
    assert "WWDC" in script.cta.body
    assert "저장" in script.cta.body
    assert len(script.cta.title) <= 22
    assert len(script.cta.body) <= 130


def test_editorial_pass_threshold_remains_seven():
    assert PASS_THRESHOLD == 7.0
