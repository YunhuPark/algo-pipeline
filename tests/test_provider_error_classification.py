import pytest
from unittest.mock import MagicMock

from src.agents.content_creator import ContentCreator
from src.qa.claim_generator import ClaimGenerationError
from src.schemas.card_news import EvidencePassage, SourceLineage, TrendReport


class RateLimitedGenerator:
    def generate_claims(self, source_lineage, validation_feedback=""):
        raise ClaimGenerationError(
            "CLAIM_GENERATION_FAILED",
            "Claim generator request failed: OpenAIRateLimitError",
        )


def _lineage():
    text = "Apple announced software updates during WWDC."
    return SourceLineage(
        schema_version="2.0",
        topic="애플 WWDC 2026 핵심 요약",
        source_title="Apple WWDC update",
        source_url="https://example.com/apple",
        context=text,
        article_id="article-1",
        content_hash="hash-1",
        evidence_passages=[
            EvidencePassage(
                evidence_id="ev1",
                article_id="article-1",
                text=text,
                source_url="https://example.com/apple",
                content_hash="hash-1",
            )
        ],
    )


def test_rate_limit_is_reclassified_as_provider_failure_without_retry():
    lineage = _lineage()
    creator = ContentCreator(
        brand_persona=MagicMock(),
        claim_generator=RateLimitedGenerator(),
    )

    with pytest.raises(ClaimGenerationError) as exc:
        creator.run(
            topic=lineage.topic,
            trend_report=TrendReport(query=lineage.topic, results=[]),
            source_lineage=lineage,
        )

    assert exc.value.error_code == "LLM_RATE_LIMITED"
    assert exc.value.failure_stage == "LLM_PROVIDER"
    assert "Usage/Billing/Limits" in str(exc.value)
