import pytest
import json
import os
from src.qa.claim_generator import ClaimGenerator
from src.schemas.card_news import SourceLineage, EvidencePassage
from unittest.mock import patch, MagicMock
from langchain_core.messages import AIMessage

os.environ["OPENAI_API_KEY"] = "test-key"

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
    with patch("langchain_core.runnables.base.RunnableSequence.invoke", return_value=AIMessage(content='{"claims": [{"claim_text": "text", "claim_type": "factual", "claim_id": "c1"}]}')):
        generator = ClaimGenerator()
        claims = generator.generate_claims(lineage)
        assert len(claims) == 1
        assert claims[0].claim_text == "text"
        assert claims[0].claim_type == "factual"

def test_claim_generator_markdown_fence(lineage):
    with patch("langchain_core.runnables.base.RunnableSequence.invoke", return_value=AIMessage(content='```json\n{"claims": [{"claim_text": "text", "claim_type": "factual", "claim_id": "c1"}]}\n```')):
        generator = ClaimGenerator()
        claims = generator.generate_claims(lineage)
        assert len(claims) == 1
        assert claims[0].claim_text == "text"

def test_claim_generator_missing_required_fields(lineage):
    with patch("langchain_core.runnables.base.RunnableSequence.invoke", return_value=AIMessage(content='{"claims": [{"claim_id": "c1", "claim_type": "factual"}]}')):
        generator = ClaimGenerator()
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            generator.generate_claims(lineage)

def test_claim_generator_invalid_claim_type(lineage):
    with patch("langchain_core.runnables.base.RunnableSequence.invoke", return_value=AIMessage(content='{"claims": [{"claim_text": "t", "claim_type": "invalid_type", "claim_id": "c1"}]}')):
        generator = ClaimGenerator()
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            generator.generate_claims(lineage)

def test_claim_generator_empty_claims(lineage):
    with patch("langchain_core.runnables.base.RunnableSequence.invoke", return_value=AIMessage(content='{"claims": []}')):
        generator = ClaimGenerator()
        claims = generator.generate_claims(lineage)
        assert len(claims) == 0

def test_claim_generator_array_instead_of_object(lineage):
    with patch("langchain_core.runnables.base.RunnableSequence.invoke", return_value=AIMessage(content='[{"claim_text": "text"}]')):
        generator = ClaimGenerator()
        with pytest.raises(ValueError, match="Expected JSON root to be an object"):
            generator.generate_claims(lineage)

def test_claim_generator_partial_json(lineage):
    with patch("langchain_core.runnables.base.RunnableSequence.invoke", return_value=AIMessage(content='{"claims": [{"claim_text": "text", "claim_type": "fact')):
        generator = ClaimGenerator()
        with pytest.raises(ValueError, match="Failed to parse claim JSON"):
            generator.generate_claims(lineage)

def test_claim_generator_unknown_field_policy(lineage):
    with patch("langchain_core.runnables.base.RunnableSequence.invoke", return_value=AIMessage(content='{"claims": [{"claim_text": "text", "claim_type": "factual", "claim_id": "c1", "unknown_field": "test"}]}')):
        generator = ClaimGenerator()
        claims = generator.generate_claims(lineage)
        assert len(claims) == 1
        assert claims[0].claim_text == "text"
        assert not hasattr(claims[0], "unknown_field")

def test_claim_generator_double_encoding(lineage):
    with patch("langchain_core.runnables.base.RunnableSequence.invoke", return_value=AIMessage(content='{\\"claims\\": [{\\"claim_text\\": \\"text\\", \\"claim_type\\": \\"factual\\", \\"claim_id\\": \\"c1\\"}]}')):
        generator = ClaimGenerator()
        with pytest.raises(ValueError, match="Failed to parse claim JSON"):
            generator.generate_claims(lineage)
