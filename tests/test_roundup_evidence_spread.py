import pytest

from src.qa.deterministic_verifier import QualityGateError
from src.qa.editorial_quality_gate import validate_claim_editorial_quality
from src.schemas.card_news import Claim


def _claim(index: int, role: str, evidence_id: str, title: str, body: str) -> Claim:
    return Claim(
        claim_id=f"c{index}",
        display_title=title,
        editorial_role=role,
        claim_text=body,
        claim_type="factual",
        evidence_ids=[evidence_id],
        verification_status="verified",
    )


def _roundup_claims(evidence_ids: list[str]) -> list[Claim]:
    rows = [
        (
            "context",
            "행사 전반의 발표 배경",
            "Apple은 WWDC에서 여러 플랫폼에 걸친 소프트웨어 업데이트와 개발자 대상 변화를 함께 소개했다.",
        ),
        (
            "change",
            "새 인터페이스 변화 공개",
            "Apple은 운영체제 전반의 인터페이스와 상호작용 방식을 바꾸는 새로운 디자인 변화를 발표했다.",
        ),
        (
            "limitation",
            "일부 기능은 제한 제공",
            "Apple은 새 기능 가운데 일부가 기기와 지역에 따라 제한되거나 단계적으로 제공될 수 있다고 설명했다.",
        ),
        (
            "impact",
            "개발자 경험도 함께 변화",
            "Apple은 개발 도구와 플랫폼 기능을 업데이트해 앱을 만드는 개발자의 작업 흐름에도 변화를 제시했다.",
        ),
    ]
    return [
        _claim(i + 1, role, evidence_ids[i], title, body)
        for i, (role, title, body) in enumerate(rows)
    ]


def test_roundup_rejects_four_cards_from_one_primary_evidence():
    claims = _roundup_claims(["e1", "e1", "e1", "e1"])

    with pytest.raises(QualityGateError) as exc:
        validate_claim_editorial_quality(
            claims,
            topic="애플 WWDC 2026 핵심 요약",
            source_title="Apple WWDC 2026",
            target_content_slides=4,
        )

    assert exc.value.error_code == "EDITORIAL_COVERAGE_INSUFFICIENT"
    assert "one evidence passage" in str(exc.value)


def test_roundup_accepts_primary_evidence_spread_across_sources():
    claims = _roundup_claims(["e1", "e1", "e2", "e2"])

    validate_claim_editorial_quality(
        claims,
        topic="애플 WWDC 2026 핵심 요약",
        source_title="Apple WWDC 2026",
        target_content_slides=4,
    )


def test_non_roundup_can_use_one_primary_evidence():
    claims = _roundup_claims(["e1", "e1", "e1", "e1"])

    validate_claim_editorial_quality(
        claims,
        topic="Apple 오디오 기능 발표",
        source_title="Apple audio feature update",
        target_content_slides=4,
    )
