from decimal import Decimal

import pytest

from src.qa.deterministic_verifier import (
    DeterministicVerifier,
    QualityGateError,
    _extract_number_mentions,
)
from src.schemas.card_news import Claim, EvidencePassage, NormalizedNumber, SourceLineage


def _lineage(text: str) -> SourceLineage:
    evidence = EvidencePassage(
        evidence_id="e-compound",
        article_id="a-compound",
        text=text,
        source_url="https://example.com/compound",
        content_hash="compound-hash",
    )
    return SourceLineage(
        schema_version="2.0",
        topic="한강버스 누적 탑승객",
        source_title="Compound number source",
        source_url="https://example.com/compound",
        context=text,
        article_id="a-compound",
        content_hash="compound-hash",
        evidence_passages=[evidence],
    )


def _claim(raw_text: str, normalized_value: str = "603293", unit: str = "명") -> Claim:
    return Claim(
        claim_id="c-compound",
        claim_text=f"누적 탑승객이 {raw_text}을 기록했다.",
        claim_type="numerical",
        numbers=[
            NormalizedNumber(
                raw_text=raw_text,
                normalized_value=Decimal(normalized_value),
                unit=unit,
            )
        ],
        evidence_ids=["e-compound"],
    )


def test_scale_plus_bare_residual_is_parsed_as_one_additive_value():
    # "60만 3293명"(만 단위 뒤에 별도 스케일 없이 붙는 나머지 숫자)은 기존에는
    # 두 개의 별개 숫자로 쪼개져 "숫자 2개라 모호함"으로 처리되던 패턴이다.
    assert list(_extract_number_mentions("60만 3293명")) == [
        (Decimal("603293"), "people")
    ]
    # 공백이 없어도(실제 생성물에서 재현된 표기) 동일하게 합쳐져야 한다.
    assert list(_extract_number_mentions("60만3293명")) == [
        (Decimal("603293"), "people")
    ]


def test_unrelated_numbers_separated_by_punctuation_are_not_merged():
    # 쉼표로 분리된 서로 다른 두 수치는 절대 하나로 합쳐지면 안 된다.
    result = list(_extract_number_mentions("60만원, 3293명이 방문했다."))
    assert (Decimal("600000"), "krw") in result
    assert (Decimal("3293"), "people") in result
    assert (Decimal("603293"), "people") not in result


def test_claim_matching_evidence_with_scale_plus_residual_is_supported():
    # 실제 생성에서 재현된 케이스: 근거와 Claim이 똑같이 "60만3293명"을
    # 썼는데도 CLAIM_NUMBER_UNSUPPORTED로 계속 거부되던 버그.
    lineage = _lineage("한강버스 운항 1주년을 맞아 누적 탑승객이 60만3293명을 기록했다고 밝혔다.")
    claim = _claim("60만3293명")

    DeterministicVerifier.verify_claims([claim], lineage)

    assert claim.verification_status == "verified"


def test_claim_with_different_residual_is_still_rejected():
    lineage = _lineage("한강버스 운항 1주년을 맞아 누적 탑승객이 60만3293명을 기록했다고 밝혔다.")
    claim = _claim("60만1000명", normalized_value="601000")

    with pytest.raises(QualityGateError) as exc:
        DeterministicVerifier.verify_claims([claim], lineage)

    assert exc.value.error_code == "NUMBER_UNSUPPORTED"
