import pytest
from decimal import Decimal
from src.qa.deterministic_verifier import DeterministicVerifier, QualityGateError
from src.qa.semantic_critic import SemanticCritic, SemanticCriticResult, run_semantic_critic
from src.qa.editorial_verifier import validate_edited_slide
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


def test_korean_man_matches_equivalent_english_million(mock_lineage):
    evidence = EvidencePassage(
        evidence_id="e-scale",
        article_id="a1",
        text="The model processes 1.05 million tokens in this benchmark.",
        source_url="http://test.com",
        content_hash="scale-hash",
    )
    lineage = mock_lineage.model_copy(update={"evidence_passages": [evidence]})
    claim = Claim(
        claim_id="c-scale",
        claim_text="이 벤치마크에서 105만 개의 토큰을 처리했다.",
        claim_type="numerical",
        numbers=[
            NormalizedNumber(
                raw_text="105만 개",
                normalized_value=Decimal("105"),
                unit="개",
            )
        ],
        evidence_ids=["e-scale"],
    )

    DeterministicVerifier.verify_claims([claim], lineage)

    assert claim.verification_status == "verified"


def test_48_billion_matches_480_eok_dollars(mock_lineage):
    evidence = EvidencePassage(
        evidence_id="e-valuation",
        article_id="a1",
        text="Cognition raised $2 billion at a $48B valuation.",
        source_url="http://test.com",
        content_hash="valuation-hash",
    )
    lineage = mock_lineage.model_copy(update={"evidence_passages": [evidence]})
    claim = Claim(
        claim_id="c-valuation",
        claim_text="Cognition은 20억 달러를 조달하며 기업가치 480억 달러를 인정받았다.",
        claim_type="numerical",
        entities=["Cognition"],
        numbers=[
            NormalizedNumber(
                raw_text="20억 달러",
                normalized_value=Decimal("2000000000"),
                unit="달러",
            ),
            NormalizedNumber(
                raw_text="480억 달러",
                normalized_value=Decimal("48000000000"),
                unit="달러",
            ),
        ],
        evidence_ids=["e-valuation"],
    )

    DeterministicVerifier.verify_claims([claim], lineage)

    assert claim.verification_status == "verified"


def test_48_billion_does_not_match_48_eok_dollars(mock_lineage):
    evidence = EvidencePassage(
        evidence_id="e-valuation",
        article_id="a1",
        text="Cognition reached a $48 billion valuation.",
        source_url="http://test.com",
        content_hash="valuation-hash",
    )
    lineage = mock_lineage.model_copy(update={"evidence_passages": [evidence]})
    claim = Claim(
        claim_id="c-valuation",
        claim_text="Cognition의 기업가치는 48억 달러다.",
        claim_type="numerical",
        entities=["Cognition"],
        numbers=[
            NormalizedNumber(
                raw_text="48억 달러",
                normalized_value=Decimal("4800000000"),
                unit="달러",
            )
        ],
        evidence_ids=["e-valuation"],
    )

    with pytest.raises(QualityGateError) as exc:
        DeterministicVerifier.verify_claims([claim], lineage)

    assert exc.value.error_code == "NUMBER_UNSUPPORTED"


def test_korean_compound_scale_matches_english_m_suffix(mock_lineage):
    evidence = EvidencePassage(
        evidence_id="e-compound",
        article_id="a1",
        text="The model contains 105M parameters.",
        source_url="http://test.com",
        content_hash="compound-hash",
    )
    lineage = mock_lineage.model_copy(update={"evidence_passages": [evidence]})
    claim = Claim(
        claim_id="c-compound",
        claim_text="이 모델은 1억 500만 개의 파라미터를 포함한다.",
        claim_type="numerical",
        numbers=[
            NormalizedNumber(
                raw_text="1억 500만 개",
                normalized_value=Decimal("105000000"),
                unit="개",
            )
        ],
        evidence_ids=["e-compound"],
    )

    DeterministicVerifier.verify_claims([claim], lineage)

    assert claim.verification_status == "verified"


def test_korean_105_man_does_not_match_english_105_million(mock_lineage):
    evidence = EvidencePassage(
        evidence_id="e-mismatch",
        article_id="a1",
        text="The model contains 105M parameters.",
        source_url="http://test.com",
        content_hash="mismatch-hash",
    )
    lineage = mock_lineage.model_copy(update={"evidence_passages": [evidence]})
    claim = Claim(
        claim_id="c-mismatch",
        claim_text="이 모델은 105만 개의 파라미터를 포함한다.",
        claim_type="numerical",
        numbers=[
            NormalizedNumber(
                raw_text="105만 개",
                normalized_value=Decimal("105"),
                unit="개",
            )
        ],
        evidence_ids=["e-mismatch"],
    )

    with pytest.raises(QualityGateError) as exc:
        DeterministicVerifier.verify_claims([claim], lineage)

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


def test_korean_month_matches_english_month_name(mock_lineage):
    evidence = EvidencePassage(
        evidence_id="e-date",
        article_id="a1",
        text="Cognition introduced the coding product in May.",
        source_url="http://test.com",
        content_hash="date-hash",
    )
    lineage = mock_lineage.model_copy(update={"evidence_passages": [evidence]})
    claim = Claim(
        claim_id="c-date",
        claim_text="Cognition은 5월에 코딩 제품을 공개했다.",
        claim_type="factual",
        entities=["Cognition"],
        dates=[
            NormalizedDate(
                raw_text="5월",
                normalized_date="--05",
                precision="month",
                is_relative=False,
            )
        ],
        evidence_ids=["e-date"],
    )

    DeterministicVerifier.verify_claims([claim], lineage)

    assert claim.verification_status == "verified"


def test_korean_month_does_not_match_different_english_month(mock_lineage):
    evidence = EvidencePassage(
        evidence_id="e-date",
        article_id="a1",
        text="Cognition introduced the coding product in June.",
        source_url="http://test.com",
        content_hash="date-hash",
    )
    lineage = mock_lineage.model_copy(update={"evidence_passages": [evidence]})
    claim = Claim(
        claim_id="c-date",
        claim_text="Cognition은 5월에 코딩 제품을 공개했다.",
        claim_type="factual",
        entities=["Cognition"],
        dates=[
            NormalizedDate(
                raw_text="5월",
                normalized_date="--05",
                precision="month",
                is_relative=False,
            )
        ],
        evidence_ids=["e-date"],
    )

    with pytest.raises(QualityGateError) as exc:
        DeterministicVerifier.verify_claims([claim], lineage)

    assert exc.value.error_code == "DATE_UNSUPPORTED"


def test_lowercase_modal_may_is_not_treated_as_date(mock_lineage):
    evidence = EvidencePassage(
        evidence_id="e-date",
        article_id="a1",
        text="The product may improve coding workflows.",
        source_url="http://test.com",
        content_hash="date-hash",
    )
    lineage = mock_lineage.model_copy(update={"evidence_passages": [evidence]})
    claim = Claim(
        claim_id="c-date",
        claim_text="제품은 5월에 공개됐다.",
        claim_type="factual",
        dates=[
            NormalizedDate(
                raw_text="5월",
                normalized_date="--05",
                precision="month",
                is_relative=False,
            )
        ],
        evidence_ids=["e-date"],
    )

    with pytest.raises(QualityGateError) as exc:
        DeterministicVerifier.verify_claims([claim], lineage)

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


def test_editorial_revision_rejects_number_missing_from_evidence(mock_lineage):
    mock_llm = get_mock_llm(
        verdict="supported",
        claim_id="editorial-revision",
        evidence_ids=["e1", "e2", "e3", "e4"],
    )

    with pytest.raises(QualityGateError) as exc:
        validate_edited_slide(
            title="점유율 급등",
            body="점유율이 99%로 올랐다.",
            slide_type="content",
            source_lineage=mock_lineage,
            semantic_llm=mock_llm,
        )

    assert exc.value.error_code == "NUMBER_UNSUPPORTED"


def test_editorial_revision_requires_semantic_support(mock_lineage):
    mock_llm = get_mock_llm(
        verdict="contradicted",
        claim_id="editorial-revision",
        evidence_ids=["e1", "e2", "e3", "e4"],
    )

    with pytest.raises(QualityGateError) as exc:
        validate_edited_slide(
            title="발표 취소",
            body="OpenAI가 모델 발표를 취소했다.",
            slide_type="content",
            source_lineage=mock_lineage,
            semantic_llm=mock_llm,
        )

    assert exc.value.error_code == "CLAIM_CONTRADICTED"


def test_editorial_revision_accepts_supported_copy(mock_lineage):
    mock_llm = get_mock_llm(
        verdict="supported",
        claim_id="editorial-revision",
        evidence_ids=["e1", "e2", "e3", "e4"],
    )

    validate_edited_slide(
        title="새 모델 발표",
        body="OpenAI는 최근 새로운 모델을 발표했다.",
        slide_type="content",
        source_lineage=mock_lineage,
        semantic_llm=mock_llm,
    )

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


def test_semantic_critic_also_checks_generated_display_title(mock_lineage):
    import json

    prompts = []

    def invoke(prompt):
        prompts.append(str(prompt))

        class Resp:
            content = json.dumps({
                "verdict": "supported",
                "reason": "제목과 본문이 원문에 의해 지지됩니다.",
                "confidence": 1.0,
                "claim_id": "c1",
                "evidence_ids": ["e1"],
            })

        return Resp()

    claim = Claim(
        claim_id="c1",
        display_title="OpenAI 새 모델 공개",
        claim_text="OpenAI는 최근 새로운 모델을 발표했다.",
        claim_type="factual",
        evidence_ids=["e1"],
        verification_status="verified",
    )

    SemanticCritic(llm=RunnableLambda(invoke)).critique_claim(claim, mock_lineage)

    assert "카드 제목: OpenAI 새 모델 공개" in prompts[0]

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


def test_semantic_critic_factory_failure_is_fail_closed(mock_lineage):
    claim = Claim(
        claim_id="c1",
        claim_text="OpenAI는 모델을 발표했다.",
        claim_type="factual",
        evidence_ids=["e1"],
        verification_status="verified",
    )

    def fail_factory():
        raise TimeoutError("provider timeout")

    critic = SemanticCritic(llm_factory=fail_factory)
    with pytest.raises(QualityGateError) as exc:
        critic.critique_claim(claim, mock_lineage)

    assert exc.value.error_code == "CRITIC_PARSE_ERROR"

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
    assert script.slides[1].title.startswith("OpenAI는 최근 새로운 모델")
    assert len(script.slides[1].title) <= 22
    assert "핵심 포인트" not in script.slides[1].title


def test_script_assembler_applies_angle_and_requested_card_count(mock_lineage):
    claims = [
        Claim(
            claim_id=f"c{index}",
            claim_text=f"OpenAI는 검증된 기능 {index}을 발표했다.",
            claim_type="factual",
            entities=["OpenAI"],
            evidence_ids=["e1"],
            verification_status="verified",
        )
        for index in range(1, 7)
    ]

    script = ScriptAssembler.assemble(
        "OpenAI 업데이트",
        claims,
        num_cards=5,
        editorial_angle="공감",
    )

    assert len(script.slides) == 5
    assert script.hook == "복잡한 소식을 쉽게 풀었습니다"
    assert script.cover.body == script.hook
    assert "#OpenAI" in script.hashtags
