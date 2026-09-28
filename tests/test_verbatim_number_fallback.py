from decimal import Decimal

import pytest

from src.qa.deterministic_verifier import DeterministicVerifier, QualityGateError
from src.schemas.card_news import Claim, EvidencePassage, NormalizedNumber, SourceLineage


def _lineage(text: str) -> SourceLineage:
    evidence = EvidencePassage(
        evidence_id="e-verbatim",
        article_id="a-verbatim",
        text=text,
        source_url="https://example.com/verbatim",
        content_hash="verbatim-hash",
    )
    return SourceLineage(
        schema_version="2.0",
        topic="검증되지 않은 숫자 표기",
        source_title="Verbatim fallback source",
        source_url="https://example.com/verbatim",
        context=text,
        article_id="a-verbatim",
        content_hash="verbatim-hash",
        evidence_passages=[evidence],
    )


def _claim(raw_text: str, normalized_value: str = "1200") -> Claim:
    return Claim(
        claim_id="c-verbatim",
        claim_text=f"설문에는 {raw_text}이 참여했다.",
        claim_type="numerical",
        numbers=[
            NormalizedNumber(raw_text=raw_text, normalized_value=Decimal(normalized_value), unit="명"),
        ],
        evidence_ids=["e-verbatim"],
    )


def test_unrecognized_multi_number_idiom_verified_when_copied_verbatim():
    # 아직 전용 규칙이 없는 새로운(가상의) 다중 숫자 표기라도, evidence에
    # 그대로("1200명, 850명") 적혀 있으면 통과해야 한다 — 매번 새 정규식을
    # 추가하지 않아도 되는 안전망.
    lineage = _lineage("설문 응답자는 3차례에 걸쳐 총 1200명, 850명이 참여했다고 밝혔다.")
    claim = _claim("1200명, 850명")

    DeterministicVerifier.verify_claims([claim], lineage)

    assert claim.verification_status == "verified"


def test_fabricated_multi_number_not_present_in_evidence_is_rejected():
    # 안전망이 검증을 아예 무력화하는 건 아니다 — evidence에 없는 숫자는
    # 여전히 거부돼야 한다.
    lineage = _lineage("설문 응답자는 3차례에 걸쳐 총 1200명, 850명이 참여했다고 밝혔다.")
    claim = _claim("9999명, 1명", normalized_value="9999")

    with pytest.raises(QualityGateError) as exc:
        DeterministicVerifier.verify_claims([claim], lineage)

    assert exc.value.error_code == "NUMBER_UNSUPPORTED"
