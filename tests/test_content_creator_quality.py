import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from src.agents.content_creator import ContentCreator
from src.qa.deterministic_verifier import QualityGateError
from src.schemas.card_news import (
    Claim,
    EvidencePassage,
    NormalizedNumber,
    SourceLineage,
    TrendReport,
)


SUPPORTED_TEXT = (
    "OpenAI는 새로운 모델을 발표했으며 공식 문서에서 주요 기능과 "
    "사용 가능한 제공 범위를 함께 공개했다고 설명했다."
)


@pytest.fixture
def lineage():
    return SourceLineage(
        schema_version="2.0",
        topic="OpenAI 발표",
        source_title="원문 기사",
        source_url="https://example.com/article",
        context=SUPPORTED_TEXT,
        article_id="article-1",
        content_hash="hash-1",
        evidence_passages=[
            EvidencePassage(
                evidence_id="evidence-1",
                article_id="article-1",
                text=SUPPORTED_TEXT,
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


class SequenceClaimGenerator:
    def __init__(self, claim_sets):
        self.claim_sets = iter(claim_sets)
        self.feedback = []

    def generate_claims(self, source_lineage, validation_feedback=""):
        self.feedback.append(validation_feedback)
        return next(self.claim_sets)


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


def passing_editorial(script, persona, expected_count):
    assert len(script.slides) == expected_count
    return SimpleNamespace(
        passed=True,
        feedback="",
        summary=lambda: "editorial quality passed",
    )


def test_content_creator_runs_both_gates_and_records_report(lineage):
    claim = Claim(
        claim_id="claim-1",
        claim_text=SUPPORTED_TEXT,
        display_title="OpenAI 새 모델 공개",
        editorial_role="change",
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
        editorial_evaluator=passing_editorial,
    )

    script = creator.run(
        topic=lineage.topic,
        trend_report=TrendReport(query=lineage.topic, results=[]),
        num_cards=3,
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


def test_content_creator_retries_grounding_failure_with_verifier_feedback(lineage):
    unsupported = Claim(
        claim_id="claim-bad-number",
        claim_text="새 모델은 105만 개의 토큰을 처리한다.",
        claim_type="numerical",
        numbers=[
            NormalizedNumber(
                raw_text="105만 개",
                normalized_value=Decimal("105"),
                unit="개",
            )
        ],
        evidence_ids=["evidence-1"],
        source_url=lineage.source_url,
    )
    supported = Claim(
        claim_id="claim-supported",
        claim_text=SUPPORTED_TEXT,
        display_title="OpenAI 새 모델 공개",
        editorial_role="change",
        claim_type="factual",
        entities=["OpenAI"],
        evidence_ids=["evidence-1"],
        source_url=lineage.source_url,
    )
    generator = SequenceClaimGenerator([[unsupported], [supported]])
    creator = ContentCreator(
        brand_persona=MagicMock(),
        claim_generator=generator,
        semantic_llm=supported_critic(),
        editorial_evaluator=passing_editorial,
    )

    script = creator.run(
        topic=lineage.topic,
        trend_report=TrendReport(query=lineage.topic, results=[]),
        num_cards=3,
        source_lineage=lineage,
    )

    assert script.topic == lineage.topic
    assert generator.feedback[0] == ""
    assert "NUMBER_UNSUPPORTED" in generator.feedback[1]
    assert creator.last_fact_check_report.confirmed == 1


def test_content_creator_still_blocks_after_bounded_grounding_retry(lineage):
    def unsupported_claim(claim_id):
        return Claim(
            claim_id=claim_id,
            claim_text="새 모델은 105만 개의 토큰을 처리한다.",
            claim_type="numerical",
            numbers=[
                NormalizedNumber(
                    raw_text="105만 개",
                    normalized_value=Decimal("105"),
                    unit="개",
                )
            ],
            evidence_ids=["evidence-1"],
            source_url=lineage.source_url,
        )

    generator = SequenceClaimGenerator(
        [
            [unsupported_claim("claim-bad-1")],
            [unsupported_claim("claim-bad-2")],
            [unsupported_claim("claim-bad-3")],
        ]
    )
    creator = ContentCreator(
        brand_persona=MagicMock(),
        claim_generator=generator,
        semantic_llm=supported_critic(),
    )

    with pytest.raises(QualityGateError) as exc:
        creator.run(
            topic=lineage.topic,
            trend_report=TrendReport(query=lineage.topic, results=[]),
            source_lineage=lineage,
        )

    assert exc.value.error_code == "NUMBER_UNSUPPORTED"
    assert len(generator.feedback) == 3
    assert "NUMBER_UNSUPPORTED" in generator.feedback[1]
    assert "NUMBER_UNSUPPORTED" in generator.feedback[2]
    assert creator.last_fact_check_report is None


def test_content_creator_accumulates_feedback_across_repair_attempts(lineage):
    unsupported = Claim(
        claim_id="claim-bad-number",
        claim_text="새 모델은 105만 개의 토큰을 처리한다.",
        claim_type="numerical",
        numbers=[
            NormalizedNumber(
                raw_text="105만 개",
                normalized_value=Decimal("105"),
                unit="개",
            )
        ],
        evidence_ids=["evidence-1"],
        source_url=lineage.source_url,
    )
    missing_headline = Claim(
        claim_id="claim-missing-headline",
        claim_text=SUPPORTED_TEXT,
        editorial_role="change",
        claim_type="factual",
        entities=["OpenAI"],
        evidence_ids=["evidence-1"],
        source_url=lineage.source_url,
    )
    corrected = missing_headline.model_copy(
        update={
            "claim_id": "claim-corrected",
            "display_title": "OpenAI 새 모델 공개",
        }
    )
    generator = SequenceClaimGenerator(
        [[unsupported], [missing_headline], [corrected]]
    )
    creator = ContentCreator(
        brand_persona=MagicMock(),
        claim_generator=generator,
        semantic_llm=supported_critic(),
        editorial_evaluator=passing_editorial,
    )

    creator.run(
        topic=lineage.topic,
        trend_report=TrendReport(query=lineage.topic, results=[]),
        num_cards=3,
        source_lineage=lineage,
    )

    assert "NUMBER_UNSUPPORTED" in generator.feedback[2]
    assert "EDITORIAL_HEADLINE_MISSING" in generator.feedback[2]
    assert creator.last_fact_check_report.confirmed == 1


def test_content_creator_retries_missing_editorial_headline(lineage):
    missing_headline = Claim(
        claim_id="claim-missing-headline",
        claim_text=SUPPORTED_TEXT,
        editorial_role="change",
        claim_type="factual",
        entities=["OpenAI"],
        evidence_ids=["evidence-1"],
        source_url=lineage.source_url,
    )
    corrected = missing_headline.model_copy(
        update={
            "claim_id": "claim-corrected",
            "display_title": "OpenAI 새 모델 공개",
        }
    )
    generator = SequenceClaimGenerator([[missing_headline], [corrected]])
    creator = ContentCreator(
        brand_persona=MagicMock(),
        claim_generator=generator,
        semantic_llm=supported_critic(),
        editorial_evaluator=passing_editorial,
    )

    creator.run(
        topic=lineage.topic,
        trend_report=TrendReport(query=lineage.topic, results=[]),
        num_cards=3,
        source_lineage=lineage,
    )

    assert "EDITORIAL_HEADLINE_MISSING" in generator.feedback[1]
    assert creator.last_fact_check_report.confirmed == 1


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
        claim_text=SUPPORTED_TEXT,
        display_title="OpenAI 새 모델 공개",
        editorial_role="change",
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
        editorial_evaluator=passing_editorial,
    )

    creator.run(
        topic=lineage.topic,
        trend_report=TrendReport(query=lineage.topic, results=[]),
        num_cards=3,
        source_lineage=lineage,
    )
    assert creator.last_fact_check_report is not None

    generator.claims = []
    with pytest.raises(QualityGateError) as exc:
        creator.run(
            topic=lineage.topic,
            trend_report=TrendReport(query=lineage.topic, results=[]),
            num_cards=3,
            source_lineage=lineage,
        )

    assert exc.value.error_code == "CLAIMS_EMPTY"
    assert creator.last_fact_check_report is None
