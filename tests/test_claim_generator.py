import pytest
import os
from src.qa.claim_generator import ClaimGenerationError, ClaimGenerator
from src.schemas.card_news import SourceLineage, EvidencePassage
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

os.environ["OPENAI_API_KEY"] = "test-key"


def generator_with_response(content):
    return ClaimGenerator(llm=RunnableLambda(lambda _: AIMessage(content=content)))

@pytest.fixture
def lineage():
    return SourceLineage(
        schema_version="2.0",
        topic="Test",
        source_title="Test",
        source_url="http://test.com",
        context="Test",
        article_id="art_1",
        content_hash="hash",
        evidence_passages=[
            EvidencePassage(evidence_id="ev_1", article_id="art_1", source_url="http://test.com", text="Test passage", content_hash="hash")
        ]
    )

def test_claim_generator_normal(lineage):
    generator = generator_with_response('{"claims": [{"claim_text": "text", "claim_type": "factual", "claim_id": "c1"}]}')
    claims = generator.generate_claims(lineage)
    assert len(claims) == 1
    assert claims[0].claim_text == "text"
    assert claims[0].claim_type == "factual"
    assert claims[0].source_url == lineage.source_url

def test_claim_generator_markdown_fence(lineage):
    generator = generator_with_response('```json\n{"claims": [{"claim_text": "text", "claim_type": "factual", "claim_id": "c1"}]}\n```')
    claims = generator.generate_claims(lineage)
    assert len(claims) == 1
    assert claims[0].claim_text == "text"

def test_claim_generator_missing_required_fields(lineage):
    generator = generator_with_response('{"claims": [{"claim_id": "c1", "claim_type": "factual"}]}')
    with pytest.raises(ClaimGenerationError) as exc:
        generator.generate_claims(lineage)
    assert exc.value.error_code == "CLAIM_SCHEMA_INVALID"

def test_claim_generator_invalid_claim_type(lineage):
    generator = generator_with_response('{"claims": [{"claim_text": "t", "claim_type": "invalid_type", "claim_id": "c1"}]}')
    with pytest.raises(ClaimGenerationError) as exc:
        generator.generate_claims(lineage)
    assert exc.value.error_code == "CLAIM_SCHEMA_INVALID"

def test_claim_generator_empty_claims(lineage):
    generator = generator_with_response('{"claims": []}')
    with pytest.raises(ClaimGenerationError) as exc:
        generator.generate_claims(lineage)
    assert exc.value.error_code == "CLAIMS_EMPTY"

def test_claim_generator_array_instead_of_object(lineage):
    generator = generator_with_response('[{"claim_text": "text"}]')
    with pytest.raises(ClaimGenerationError, match="Expected JSON root to be an object"):
        generator.generate_claims(lineage)

def test_claim_generator_partial_json(lineage):
    generator = generator_with_response('{"claims": [{"claim_text": "text", "claim_type": "fact')
    with pytest.raises(ClaimGenerationError, match="Failed to parse claim JSON"):
        generator.generate_claims(lineage)

def test_claim_generator_unknown_field_policy(lineage):
    generator = generator_with_response('{"claims": [{"claim_text": "text", "claim_type": "factual", "claim_id": "c1", "unknown_field": "test"}]}')
    claims = generator.generate_claims(lineage)
    assert len(claims) == 1
    assert claims[0].claim_text == "text"
    assert not hasattr(claims[0], "unknown_field")

def test_claim_generator_double_encoding(lineage):
    generator = generator_with_response('{\\"claims\\": [{\\"claim_text\\": \\"text\\", \\"claim_type\\": \\"factual\\", \\"claim_id\\": \\"c1\\"}]}')
    with pytest.raises(ClaimGenerationError, match="Failed to parse claim JSON"):
        generator.generate_claims(lineage)


def test_claim_generator_is_lazy_until_generation():
    calls = []
    generator = ClaimGenerator(llm_factory=lambda: calls.append("built"))
    assert calls == []
    assert generator._llm is None


def test_claim_generator_requires_verified_evidence(lineage):
    no_evidence = lineage.model_copy(update={"evidence_passages": []})
    generator = ClaimGenerator(llm_factory=lambda: pytest.fail("LLM must not be created"))
    with pytest.raises(ClaimGenerationError) as exc:
        generator.generate_claims(no_evidence)
    assert exc.value.error_code == "CLAIM_EVIDENCE_MISSING"


def test_claim_generator_rejects_duplicate_ids(lineage):
    generator = generator_with_response(
        '{"claims": ['
        '{"claim_text": "one", "claim_type": "factual", "claim_id": "c1"},'
        '{"claim_text": "two", "claim_type": "factual", "claim_id": "c1"}'
        ']}'
    )
    with pytest.raises(ClaimGenerationError) as exc:
        generator.generate_claims(lineage)
    assert exc.value.error_code == "CLAIM_ID_DUPLICATE"


def test_claim_generator_factory_failure_is_fail_closed(lineage):
    def fail_factory():
        raise RuntimeError("provider unavailable")

    generator = ClaimGenerator(llm_factory=fail_factory)

    with pytest.raises(ClaimGenerationError) as exc:
        generator.generate_claims(lineage)

    assert exc.value.error_code == "CLAIM_GENERATION_FAILED"
    assert "provider unavailable" not in str(exc.value)
