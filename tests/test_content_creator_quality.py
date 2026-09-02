import json
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from src.agents.content_creator import ContentCreator
from src.qa.deterministic_verifier import QualityGateError
from src.schemas.card_news import Claim, EvidencePassage, SourceLineage, TrendReport


@pytest.fixture
def lineage():
    return SourceLineage(
        schema_version="2.0",
        topic="OpenAI 발표",
        source_title="원문 기사",
        source_url="https://example.com/article",
        context="OpenAI는 새로운 모델을 발표했다.",
        article_id="article-1",
        content_hash="hash-1",
        evidence_passages=[
            EvidencePassage(
                evidence_id="evidence-1",
                article_id="article-1",
                text="OpenAI는 새로운 모델을 발표했다.",
                source_url="https://example.com/article",
                content_hash="hash-1",
            )
        ],
    )


class StubClaimGenerator:
    def __init__(self, claims):
        self.claims = claims
        self.calls = 0

    def generate_claims(self, source_lineage):
        self.calls += 1
        return self.claims


def supported_critic():
    return RunnableLambda(
        lambda _: AIMessage(
            content=json.dumps(
                {
                    "verdict": "supported",
                    "reason": "원문에 명시되어 있습니다.",
                    "confidence": 1.0,
                },
                ensure_ascii=False,
            )
        )
    )


def test_content_creator_runs_both_gates_and_records_report(lineage):
    claim = Claim(
        claim_id="claim-1",
        claim_text="OpenAI는 새로운 모델을 발표했다.",
        claim_type="factual",
        entities=["OpenAI"],
        evidence_ids=["evidence-1"],
        source_url=lineage.source_url,
    )
    generator = StubClaimGenerator([claim])
    creator = ContentCreator(
        brand_persona=MagicMock(),
        claim_generator=generator,
        semantic_llm=supported_critic(),
    )

    script = creator.run(
        topic=lineage.topic,
        trend_report=TrendReport(query=lineage.topic, results=[]),
        source_lineage=lineage,
    )

    assert script.topic == lineage.topic
    assert claim.verification_status == "verified"
    assert creator.last_fact_check_report.confirmed == 1
    assert creator.last_fact_check_report.disputed == 0
    assert generator.calls == 1


def test_content_creator_rejects_empty_claims_before_semantic_call(lineage):
    semantic = MagicMock()
    creator = ContentCreator(
        brand_persona=MagicMock(),
        claim_generator=StubClaimGenerator([]),
        semantic_llm=semantic,
    )

    with pytest.raises(QualityGateError) as exc:
        creator.run(
            topic=lineage.topic,
            trend_report=TrendReport(query=lineage.topic, results=[]),
            source_lineage=lineage,
        )

    assert exc.value.error_code == "CLAIMS_EMPTY"
    semantic.invoke.assert_not_called()
    assert creator.last_fact_check_report is None


def test_content_creator_rejects_legacy_lineage_before_claim_generation(lineage):
    generator = StubClaimGenerator([])
    creator = ContentCreator(
        brand_persona=MagicMock(),
        claim_generator=generator,
        semantic_llm=MagicMock(),
    )

    with pytest.raises(QualityGateError) as exc:
        creator.run(
            topic=lineage.topic,
            trend_report=TrendReport(query=lineage.topic, results=[]),
            source_lineage=lineage.model_copy(update={"schema_version": "1.0"}),
        )

    assert exc.value.error_code == "LEGACY_LINEAGE_UNVERIFIED"
    assert generator.calls == 0


def test_content_creator_clears_stale_report_before_next_run(lineage):
    claim = Claim(
        claim_id="claim-1",
        claim_text="OpenAI는 새로운 모델을 발표했다.",
        claim_type="factual",
        entities=["OpenAI"],
        evidence_ids=["evidence-1"],
        source_url=lineage.source_url,
    )
    generator = StubClaimGenerator([claim])
    creator = ContentCreator(
        brand_persona=MagicMock(),
        claim_generator=generator,
        semantic_llm=supported_critic(),
    )

    creator.run(
        topic=lineage.topic,
        trend_report=TrendReport(query=lineage.topic, results=[]),
        source_lineage=lineage,
    )
    assert creator.last_fact_check_report is not None

    generator.claims = []
    with pytest.raises(QualityGateError) as exc:
        creator.run(
            topic=lineage.topic,
            trend_report=TrendReport(query=lineage.topic, results=[]),
            source_lineage=lineage,
        )

    assert exc.value.error_code == "CLAIMS_EMPTY"
    assert creator.last_fact_check_report is None
