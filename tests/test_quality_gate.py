import pytest
from decimal import Decimal
from src.qa.deterministic_verifier import DeterministicVerifier, QualityGateError
from src.qa.semantic_critic import SemanticCritic, SemanticCriticResult, run_semantic_critic
from src.schemas.card_news import SourceLineage, EvidencePassage, Claim, NormalizedNumber, NormalizedDate
from src.qa.script_assembler import ScriptAssembler
from langchain_core.runnables import RunnableLambda

@pytest.fixture
def mock_evidence():
    return [
        EvidencePassage(
            evidence_id="e1",
            article_id="a1",
            text="OpenAI는 최근 새로운 모델을 발표했다.",
            source_url="http://test.com",
            content_hash="hash1"
        ),
        EvidencePassage(
            evidence_id="e2",
            article_id="a1",
            text="이 기술은 메타데이터(metadata)를 활용하며, 1.5백만 개의 파라미터를 갖추고 있다.",
            source_url="http://test.com",
            content_hash="hash2"
        ),
        EvidencePassage(
            evidence_id="e3",
            article_id="a1",
            text="3개 기업이 30%의 점유율을 차지하고 13일 동안 테스트를 진행했다.",
            source_url="http://test.com",
            content_hash="hash3"
        ),
        EvidencePassage(
            evidence_id="e4",
            article_id="a1",
            text="내일 발표가 있을 예정이다. 오늘 매출은 30명 이상의 고객으로부터 발생했다.",
            source_url="http://test.com",
            content_hash="hash4"
        )
    ]

@pytest.fixture
def mock_lineage(mock_evidence):
    return SourceLineage(
        schema_version="2.0",
        topic="AI Tech",
        source_title="Tech News",
        source_url="http://test.com",
        context="Summary",
        article_id="a1",
        content_hash="fullhash",
        evidence_passages=mock_evidence
    )

# --- DETERMINISTIC TESTS ---
def test_legacy_lineage_fails(mock_lineage):
    mock_lineage.schema_version = "1.0"
    with pytest.raises(QualityGateError) as exc:
        DeterministicVerifier.verify_claims([], mock_lineage)
    assert exc.value.error_code == "LEGACY_LINEAGE_UNVERIFIED"


def test_empty_claims_fail_closed(mock_lineage):
    with pytest.raises(QualityGateError) as exc:
        DeterministicVerifier.verify_claims([], mock_lineage)
    assert exc.value.error_code == "CLAIMS_EMPTY"


def test_duplicate_claim_ids_fail_closed(mock_lineage):
    claims = [
        Claim(
            claim_id="duplicate",
            claim_text=text,
            claim_type="factual",
            evidence_ids=["e1"],
        )
        for text in ("첫 번째 주장", "두 번째 주장")
    ]
    with pytest.raises(QualityGateError) as exc:
        DeterministicVerifier.verify_claims(claims, mock_lineage)
    assert exc.value.error_code == "CLAIM_ID_DUPLICATE"


def test_numerical_claim_requires_normalized_numbers(mock_lineage):
    claim = Claim(
        claim_id="c-number",
        claim_text="3개 기업이 참여했다.",
        claim_type="numerical",
        evidence_ids=["e3"],
    )
    with pytest.raises(QualityGateError) as exc:
        DeterministicVerifier.verify_claims([claim], mock_lineage)
    assert exc.value.error_code == "NUMBERS_MISSING"


def test_cta_must_be_last_and_evidence_bound(mock_lineage):
    claims = [
        Claim(
            claim_id="cta",
            claim_text="원문을 확인해 보세요.",
            claim_type="cta",
            evidence_ids=["e1"],
        ),
        Claim(
            claim_id="fact",
            claim_text="OpenAI는 새로운 모델을 발표했다.",
            claim_type="factual",
            evidence_ids=["e1"],
        ),
    ]
    with pytest.raises(QualityGateError) as exc:
        DeterministicVerifier.verify_claims(claims, mock_lineage)
    assert exc.value.error_code == "CTA_ORDER_INVALID"

def test_claude_hallucination(mock_lineage):
    claim = Claim(
        claim_id="c1",
        claim_text="Claude가 새로운 모델을 발표했다.",
        claim_type="factual",
        entities=["Claude"],
        evidence_ids=["e1"]
    )
    with pytest.raises(QualityGateError) as exc:
        DeterministicVerifier.verify_claims([claim], mock_lineage)
    assert exc.value.error_code == "ENTITY_UNSUPPORTED"


def test_english_entity_with_korean_particle_is_supported(mock_lineage):
    claim = Claim(
        claim_id="c-openai",
        claim_text="OpenAI는 최근 새로운 모델을 발표했다.",
        claim_type="factual",
        entities=["OpenAI"],
        evidence_ids=["e1"],
    )

    DeterministicVerifier.verify_claims([claim], mock_lineage)

    assert claim.verification_status == "verified"

def test_number_3_vs_13(mock_lineage):
    # 3개 기업 vs 13개 기업
    claim = Claim(
        claim_id="c2",
        claim_text="13개 기업이 참여했다.",
        claim_type="numerical",
        numbers=[NormalizedNumber(raw_text="13개", normalized_value=Decimal("13"), unit="개")],
        evidence_ids=["e3"]
    )
    with pytest.raises(QualityGateError) as exc:
        DeterministicVerifier.verify_claims([claim], mock_lineage)
    assert exc.value.error_code == "NUMBER_UNSUPPORTED"

def test_number_1_5_vs_15(mock_lineage):
    claim = Claim(
        claim_id="c2",
        claim_text="15백만 개의 파라미터.",
        claim_type="numerical",
        numbers=[NormalizedNumber(raw_text="15백만", normalized_value=Decimal("15"), unit="만")],
        evidence_ids=["e2"]
    )
    with pytest.raises(QualityGateError) as exc:
        DeterministicVerifier.verify_claims([claim], mock_lineage)
    assert exc.value.error_code == "NUMBER_UNSUPPORTED"

def test_number_unit_mismatch(mock_lineage):
    # 30% vs 30명
    claim = Claim(
        claim_id="c2",
        claim_text="30명이 참여했다.",
        claim_type="numerical",
        numbers=[NormalizedNumber(raw_text="30명", normalized_value=Decimal("30"), unit="명")],
        evidence_ids=["e3"]
    )
    with pytest.raises(QualityGateError) as exc:
        DeterministicVerifier.verify_claims([claim], mock_lineage)
    assert exc.value.error_code == "NUMBER_UNSUPPORTED"

def test_number_object_mismatch(mock_lineage):
    # 3개 기업 vs 3일
    claim = Claim(
        claim_id="c3",
        claim_text="3일 동안",
        claim_type="numerical",
        numbers=[NormalizedNumber(raw_text="3일", normalized_value=Decimal("3"), unit="일")],
        evidence_ids=["e3"]
    )
    with pytest.raises(QualityGateError) as exc:
        DeterministicVerifier.verify_claims([claim], mock_lineage)
    assert exc.value.error_code == "NUMBER_UNSUPPORTED"

def test_date_relative_absolute_mismatch(mock_lineage):
    # 상대 날짜를 임의의 절대 날짜로 변경한 경우
    claim = Claim(
        claim_id="c4",
        claim_text="2024년 10월 25일에 발표가 있을 예정이다.",
        claim_type="factual",
        dates=[NormalizedDate(raw_text="2024년 10월 25일", normalized_date="2024-10-25", precision="day", is_relative=False)],
        evidence_ids=["e4"]
    )
    with pytest.raises(QualityGateError) as exc:
        DeterministicVerifier.verify_claims([claim], mock_lineage)
    assert exc.value.error_code == "DATE_UNSUPPORTED"

# --- SEMANTIC CRITIC TESTS (Meaning Distortion) ---
def get_mock_llm(verdict="contradicted", reason="reason", confidence=1.0, claim_id="c1", evidence_ids=["e1"]):
    import json
    def invoke(inputs):
        class Resp:
            content = json.dumps({
                "verdict": verdict,
                "reason": reason,
                "confidence": confidence,
                "claim_id": claim_id,
                "evidence_ids": evidence_ids
            })
        return Resp()
    return RunnableLambda(invoke)

def test_semantic_critic_positive_negative(mock_lineage):
    # 긍정 ↔ 부정
    claim = Claim(claim_id="c1", claim_text="OpenAI는 모델 발표를 취소했다.", claim_type="factual", evidence_ids=["e1"], verification_status="verified")
    mock_llm = get_mock_llm(verdict="contradicted")
    with pytest.raises(QualityGateError) as exc:
        run_semantic_critic([claim], mock_lineage, llm=mock_llm)
    assert exc.value.error_code == "CLAIM_CONTRADICTED"
    assert exc.value.failure_stage == "QUALITY_GATE"

def test_semantic_critic_possibility_to_certainty(mock_lineage):
    # 가능성 → 확정
    claim = Claim(claim_id="c1", claim_text="OpenAI가 시장을 독점했다.", claim_type="inference", evidence_ids=["e1"], verification_status="verified")
    mock_llm = get_mock_llm(verdict="contradicted")
    with pytest.raises(QualityGateError) as exc:
        run_semantic_critic([claim], mock_lineage, llm=mock_llm)
    assert exc.value.error_code == "CLAIM_CONTRADICTED"

def test_semantic_critic_correlation_to_causation(mock_lineage):
    # 상관관계 → 인과관계
    claim = Claim(claim_id="c1", claim_text="발표 때문에 주가가 올랐다.", claim_type="inference", evidence_ids=["e1"], verification_status="verified")
    mock_llm = get_mock_llm(verdict="insufficient_evidence")
    with pytest.raises(QualityGateError) as exc:
        run_semantic_critic([claim], mock_lineage, llm=mock_llm)
    assert exc.value.error_code == "CLAIM_INSUFFICIENT_EVIDENCE"

def test_semantic_critic_wrong_speaker(mock_lineage):
    # 발언자 오귀속
    claim = Claim(claim_id="c1", claim_text="Google이 말했다.", claim_type="attributed_statement", evidence_ids=["e1"], verification_status="verified")
    mock_llm = get_mock_llm(verdict="contradicted")
    with pytest.raises(QualityGateError) as exc:
        run_semantic_critic([claim], mock_lineage, llm=mock_llm)
    assert exc.value.error_code == "CLAIM_CONTRADICTED"

def test_semantic_critic_overgeneralization(mock_lineage):
    # 단일 사례 → 산업 전체 일반화
    claim = Claim(claim_id="c1", claim_text="모든 AI 기업이 발표했다.", claim_type="factual", evidence_ids=["e1"], verification_status="verified")
    mock_llm = get_mock_llm(verdict="contradicted")
    with pytest.raises(QualityGateError) as exc:
        run_semantic_critic([claim], mock_lineage, llm=mock_llm)
    assert exc.value.error_code == "CLAIM_CONTRADICTED"

# --- SEMANTIC CRITIC SYSTEM TESTS ---
def test_semantic_critic_claim_id_mismatch(mock_lineage):
    claim = Claim(claim_id="c1", claim_text="...", claim_type="factual", evidence_ids=["e1"], verification_status="verified")
    mock_llm = get_mock_llm(verdict="supported", claim_id="c999")
    with pytest.raises(QualityGateError) as exc:
        run_semantic_critic([claim], mock_lineage, llm=mock_llm)
    assert exc.value.error_code == "CRITIC_RESPONSE_MISMATCH"

def test_semantic_critic_evidence_id_mismatch(mock_lineage):
    claim = Claim(claim_id="c1", claim_text="...", claim_type="factual", evidence_ids=["e1"], verification_status="verified")
    mock_llm = get_mock_llm(verdict="supported", evidence_ids=["e999"])
    with pytest.raises(QualityGateError) as exc:
        run_semantic_critic([claim], mock_lineage, llm=mock_llm)
    assert exc.value.error_code == "CRITIC_RESPONSE_MISMATCH"

def test_semantic_critic_empty_reason(mock_lineage):
    claim = Claim(claim_id="c1", claim_text="...", claim_type="factual", evidence_ids=["e1"], verification_status="verified")
    mock_llm = get_mock_llm(verdict="supported", reason="")
    with pytest.raises(QualityGateError) as exc:
        run_semantic_critic([claim], mock_lineage, llm=mock_llm)
    assert exc.value.error_code == "CRITIC_PARSE_ERROR"

def test_semantic_critic_partial_response(mock_lineage):
    claim = Claim(claim_id="c1", claim_text="...", claim_type="factual", evidence_ids=["e1"], verification_status="verified")
    import json
    def invoke(inputs):
        class Resp:
            content = json.dumps({"verdict": "supported"}) # missing fields
        return Resp()
    with pytest.raises(QualityGateError) as exc:
        run_semantic_critic([claim], mock_lineage, llm=RunnableLambda(invoke))
    assert exc.value.error_code == "CRITIC_PARSE_ERROR"

def test_semantic_critic_invalid_confidence(mock_lineage):
    claim = Claim(claim_id="c1", claim_text="...", claim_type="factual", evidence_ids=["e1"], verification_status="verified")
    with pytest.raises(QualityGateError) as exc:
        run_semantic_critic([claim], mock_lineage, llm=get_mock_llm(confidence=-1.0))
    assert exc.value.error_code == "CRITIC_PARSE_ERROR"
    with pytest.raises(QualityGateError) as exc2:
        run_semantic_critic([claim], mock_lineage, llm=get_mock_llm(confidence=float('inf')))
    assert exc2.value.error_code == "CRITIC_PARSE_ERROR"

def test_semantic_critic_zero_calls_on_deterministic_failure(mock_lineage):
    # deterministic failure 시 critic 0회
    claim = Claim(claim_id="c1", claim_text="...", claim_type="factual", evidence_ids=["e1"])
    # Not verified -> Semantic critic should raise error or skip
    mock_llm = get_mock_llm(verdict="supported")
    with pytest.raises(QualityGateError) as exc:
        run_semantic_critic([claim], mock_lineage, llm=mock_llm)
    assert exc.value.error_code == "UNVERIFIED_CLAIM_PASSED_TO_CRITIC"

def test_semantic_critic_supported_does_not_override(mock_lineage):
    # critic supported가 deterministic failure를 override하지 못함
    claim = Claim(claim_id="c1", claim_text="...", claim_type="factual", evidence_ids=["e1"])
    mock_llm = get_mock_llm(verdict="supported")
    with pytest.raises(QualityGateError):
        run_semantic_critic([claim], mock_lineage, llm=mock_llm)

# --- SCRIPT ASSEMBLER TESTS ---
def test_script_assembler(mock_lineage):
    claim = Claim(
        claim_id="c1",
        claim_text="OpenAI는 최근 새로운 모델을 발표했다.",
        claim_type="factual",
        entities=["OpenAI"],
        evidence_ids=["e1"],
        verification_status="verified"
    )
    script = ScriptAssembler.assemble("AI Tech", [claim])
    assert script.topic == "AI Tech"
    assert len(script.slides) == 3 # 1 cover, 1 content, 1 cta
