from decimal import Decimal

import pytest

from src.qa.claim_generator import _CLAIM_SYSTEM_PROMPT
from src.qa.deterministic_verifier import DeterministicVerifier, QualityGateError
from src.schemas.card_news import Claim, EvidencePassage, NormalizedNumber, SourceLineage


def _lineage(text: str) -> SourceLineage:
    evidence = EvidencePassage(
        evidence_id="e-range",
        article_id="a-range",
        text=text,
        source_url="https://example.com/range",
        content_hash="range-hash",
    )
    return SourceLineage(
        schema_version="2.0",
        topic="기업가치 범위 요약",
        source_title="Range source",
        source_url="https://example.com/range",
        context=text,
        article_id="a-range",
        content_hash="range-hash",
        evidence_passages=[evidence],
    )


def _claim(raw_text: str, normalized_value: str = "7200000000", unit: str = "dollars") -> Claim:
    return Claim(
        claim_id="c-range",
        claim_text=f"기업가치는 {raw_text} 범위로 제시됐다.",
        claim_type="numerical",
        numbers=[
            NormalizedNumber(
                raw_text=raw_text,
                normalized_value=Decimal(normalized_value),
                unit=unit,
            )
        ],
        evidence_ids=["e-range"],
    )


def test_prompt_tells_model_to_preserve_numeric_range_notation():
    assert "$7.2 billion to $7.45 billion" in _CLAIM_SYSTEM_PROMPT
    assert "720억에서 745억 달러" in _CLAIM_SYSTEM_PROMPT
    assert "원문 범위 표기" in _CLAIM_SYSTEM_PROMPT


def test_exact_english_range_is_supported():
    lineage = _lineage("The valuation was $7.2 billion to $7.45 billion.")
    claim = _claim("$7.2 billion to $7.45 billion", "7200000000")

    DeterministicVerifier.verify_claims([claim], lineage)

    assert claim.verification_status == "verified"


def test_simple_range_with_shared_trailing_scale_and_unit_is_supported():
    lineage = _lineage("The valuation was between $7.2 billion and $7.45 billion.")
    claim = _claim("$7.2~$7.45 billion", "7200000000")

    DeterministicVerifier.verify_claims([claim], lineage)

    assert claim.verification_status == "verified"


def test_korean_range_passes_only_when_conversion_is_mathematically_equivalent():
    lineage = _lineage("The valuation was $7.2 billion to $7.45 billion.")
    claim = _claim("72억에서 74.5억 달러", "7200000000", "달러")

    DeterministicVerifier.verify_claims([claim], lineage)

    assert claim.verification_status == "verified"


def test_720_to_745_eok_range_does_not_match_7_2_to_7_45_billion():
    lineage = _lineage("The valuation was $7.2 billion to $7.45 billion.")
    claim = _claim("720억에서 745억 달러", "72000000000", "달러")

    with pytest.raises(QualityGateError) as exc:
        DeterministicVerifier.verify_claims([claim], lineage)

    assert exc.value.error_code == "NUMBER_UNSUPPORTED"
