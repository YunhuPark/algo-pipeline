from unittest.mock import patch

import pytest

from src.agents.verifier import _AIScore, _rule_check, verify
from src.persona import Persona
from src.qa.deterministic_verifier import QualityGateError
from src.qa.editorial_quality_gate import validate_claim_editorial_quality
from src.qa.script_assembler import ScriptAssembler
from src.schemas.card_news import Claim, NormalizedNumber


def _claim(index: int, role: str, title: str, body: str) -> Claim:
    return Claim(
        claim_id=f"c{index}",
        display_title=title,
        editorial_role=role,
        claim_text=body,
        claim_type="factual",
        evidence_ids=["e1"],
        verification_status="verified",
    )


def _distinct_claims() -> list[Claim]:
    return [
        _claim(
            1,
            "context",
            "공개 배경부터 확인",
            "연구팀은 이번 발표에서 모델 개발 배경과 검증 대상이 된 작업 범위를 공식 문서로 함께 공개했고 적용 조건도 명시했다.",
        ),
        _claim(
            2,
            "change",
            "이전 방식과 달라진 점",
            "새 버전은 기존 처리 단계 두 개를 하나로 통합해 사용자가 거쳐야 하는 설정 절차와 반복 작업을 줄였다고 설명했다.",
        ),
        _claim(
            3,
            "evidence",
            "측정 결과가 보여준 것",
            "공개된 시험에서는 동일한 입력 조건과 평가 기준을 적용해 이전 버전과 새 버전의 결과와 처리 과정을 비교했다.",
        ),
        _claim(
            4,
            "limitation",
            "해석할 때 남은 한계",
            "발표 자료는 제한된 시험 환경의 결과이므로 실제 서비스와 다양한 입력 조건에서도 같은 성능인지 추가 확인이 필요하다.",
        ),
    ]


def test_editorial_claim_gate_accepts_distinct_story_roles():
    validate_claim_editorial_quality(
        _distinct_claims(),
        topic="모델 개발 발표",
        target_content_slides=4,
    )


def test_editorial_claim_gate_rejects_thin_six_card_story():
    with pytest.raises(QualityGateError) as exc:
        validate_claim_editorial_quality(
            _distinct_claims()[:2],
            topic="모델 개발 발표",
            target_content_slides=4,
        )

    assert exc.value.error_code == "EDITORIAL_COVERAGE_INSUFFICIENT"


def test_editorial_claim_gate_rejects_repeated_card_points():
    claims = _distinct_claims()
    claims[1] = claims[0].model_copy(
        update={"claim_id": "c2", "editorial_role": "change"}
    )

    with pytest.raises(QualityGateError) as exc:
        validate_claim_editorial_quality(
            claims,
            topic="모델 개발 발표",
            target_content_slides=4,
        )

    assert exc.value.error_code == "EDITORIAL_CLAIM_REDUNDANT"


def test_editorial_claim_gate_rejects_generic_topic_drift():
    with pytest.raises(QualityGateError) as exc:
        validate_claim_editorial_quality(
            _distinct_claims(),
            topic="AI 용어 정복: 당신이 알아야 할 필수 용어들",
            target_content_slides=4,
        )

    assert exc.value.error_code == "EDITORIAL_TOPIC_MISMATCH"


def test_editorial_claim_gate_uses_locked_source_title_for_relevance():
    claims = _distinct_claims()
    claims[0] = claims[0].model_copy(
        update={
            "display_title": "Cognition 투자 변화",
            "claim_text": (
                "Cognition은 이번 투자 발표에서 기업가치와 조달 규모를 공개했고, "
                "AI 코딩 사업 확장을 위한 향후 운영 방향도 함께 설명했다."
            ),
        }
    )

    validate_claim_editorial_quality(
        claims,
        topic="AI 코딩 시장의 새로운 가능성",
        source_title="Cognition hits $48B valuation in AI coding market",
        target_content_slides=4,
    )


def test_rule_check_rejects_truncated_headline():
    script = ScriptAssembler.assemble("테스트", _distinct_claims(), num_cards=6)
    script.slides[1].title = "완결되지 않은 제목…"

    errors = _rule_check(script, expected_count=6)

    assert any("말줄임표" in error for error in errors)


def test_long_topic_produces_complete_cover_title_and_enough_hashtags():
    script = ScriptAssembler.assemble(
        "OpenAI 새 추론 모델이 공개한 긴 벤치마크 결과",
        _distinct_claims(),
        num_cards=6,
    )

    assert len(script.slides[0].title) <= 22
    assert not script.slides[0].title.endswith(("…", "..."))
    assert len(script.hashtags) >= 5


def test_script_assembler_makes_headline_number_the_primary_visual():
    claim = Claim(
        claim_id="c1",
        display_title="Cognition의 480억 달러 가치",
        editorial_role="context",
        claim_text=(
            "Cognition은 20억 달러를 조달했고 기업가치는 480억 달러로 "
            "평가됐다고 발표했다."
        ),
        claim_type="numerical",
        entities=["Cognition"],
        numbers=[
            NormalizedNumber(raw_text="20억 달러", normalized_value=2_000_000_000, unit="USD", subject="조달액"),
            NormalizedNumber(raw_text="480억 달러", normalized_value=48_000_000_000, unit="USD", subject="기업가치"),
        ],
        evidence_ids=["e1"],
        verification_status="verified",
    )

    slide = ScriptAssembler.assemble("Cognition 투자", [claim]).content_slides[0]

    assert slide.accent == "480억 달러"
    assert slide.visual_type == "hero_stat"
    assert slide.visual_values[0] == "480억 달러"
    assert slide.visual_labels[0] == "기업가치"


def test_script_assembler_localizes_approximate_english_visual_label():
    claim = Claim(
        claim_id="c1",
        display_title="Cognition의 서버 비용",
        editorial_role="limitation",
        claim_text="Cognition은 서버 클러스터에 매년 수억 달러를 지출하고 있다.",
        claim_type="numerical",
        entities=["Cognition"],
        numbers=[
            NormalizedNumber(
                raw_text="hundreds of millions",
                normalized_value=100_000_000,
                unit="dollars",
                subject="연간 서버 비용",
            )
        ],
        evidence_ids=["e1"],
        verification_status="verified",
    )

    slide = ScriptAssembler.assemble("Cognition 투자", [claim]).content_slides[0]

    assert slide.accent == "수억 달러"
    assert slide.visual_values == ["수억 달러"]


def test_script_assembler_selects_distinct_visuals_by_editorial_role():
    claims = [
        _claim(1, "mechanism", "작동 방식", "입력을 분석하고 검증 단계를 거쳐 결과를 제공하는 구조다."),
        _claim(2, "limitation", "남은 한계", "제한된 환경에서만 확인돼 추가 검증이 필요하다."),
        _claim(3, "impact", "시장 영향", "개발 도구를 선택하는 기업의 판단 기준에 영향을 줄 수 있다."),
    ]

    slides = ScriptAssembler.assemble("검증된 변화", claims, num_cards=5).content_slides

    assert [slide.visual_type for slide in slides] == ["process", "warning", "impact"]


def test_ai_editorial_gate_requires_every_quality_axis_to_pass():
    script = ScriptAssembler.assemble("테스트", _distinct_claims(), num_cards=6)
    low_readability = _AIScore(
        hook_power=9,
        readability=6,
        brand_tone=9,
        info_quality=9,
        naturalness=9,
        completeness=9,
        feedback="슬라이드2의 문장을 더 짧게 정리하세요.",
    )

    with patch("src.agents.verifier._rule_check", return_value=[]), patch(
        "src.agents.verifier._ai_evaluate",
        return_value=low_readability,
    ):
        result = verify(script, Persona(), expected_count=6)

    assert result.score > 7
    assert result.passed is False
    assert "가독성" in result.feedback
