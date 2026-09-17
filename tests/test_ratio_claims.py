from decimal import Decimal

import pytest

from src.qa.deterministic_verifier import DeterministicVerifier, QualityGateError
from src.schemas.card_news import Claim, EvidencePassage, NormalizedNumber, SourceLineage


def _lineage(text: str) -> SourceLineage:
    evidence = EvidencePassage(
        evidence_id="e-ratio",
        article_id="a-ratio",
        text=text,
        source_url="https://example.com/ratio",
        content_hash="ratio-hash",
    )
    return SourceLineage(
        schema_version="2.0",
        topic="세대별 이중 부담 비율",
        source_title="Ratio source",
        source_url="https://example.com/ratio",
        context=text,
        article_id="a-ratio",
        content_hash="ratio-hash",
        evidence_passages=[evidence],
    )


def _claim(raw_text: str, normalized_value: str = "6", unit: str = "명") -> Claim:
    return Claim(
        claim_id="c-ratio",
        claim_text=f"서울 거주 50대 {raw_text}이 이중 부담을 지고 있다.",
        claim_type="numerical",
        numbers=[
            NormalizedNumber(
                raw_text=raw_text,
                normalized_value=Decimal(normalized_value),
                unit=unit,
            )
        ],
        evidence_ids=["e-ratio"],
    )


def test_korean_out_of_ratio_matching_evidence_is_supported():
    # "10명 중 6명"처럼 숫자 두 개가 한 raw_text에 들어가는 비율 표현은
    # 예전에는 범위(range)로도, 단일 숫자로도 인식되지 않아 "모호함"으로
    # 처리돼 evidence에 있는 문구를 그대로 옮겨도 항상 거부됐다(재현된 버그).
    lineage = _lineage("서울에 거주하는 50대 10명 중 6명이 이중 부담을 지고 있다고 조사됐다.")
    claim = _claim("10명 중 6명")

    DeterministicVerifier.verify_claims([claim], lineage)

    assert claim.verification_status == "verified"


def test_korean_out_of_ratio_not_matching_evidence_is_rejected():
    # 분자가 달라지면(6명 → 7명) 여전히 근거 불일치로 거부돼야 한다 —
    # 비율 인식이 검증 자체를 느슨하게 만들지 않았는지 확인.
    lineage = _lineage("서울에 거주하는 50대 10명 중 6명이 이중 부담을 지고 있다고 조사됐다.")
    claim = _claim("10명 중 7명", normalized_value="7")

    with pytest.raises(QualityGateError) as exc:
        DeterministicVerifier.verify_claims([claim], lineage)

    assert exc.value.error_code == "NUMBER_UNSUPPORTED"
