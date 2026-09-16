from types import SimpleNamespace
from unittest.mock import MagicMock

from src.agents.content_creator import ContentCreator
from src.qa.claim_generator import ClaimGenerationError
from src.schemas.card_news import Claim, EvidencePassage, SourceLineage, TrendReport


class BoundaryRetryGenerator:
    def __init__(self):
        self.feedback = []

    def generate_claims(self, source_lineage, validation_feedback=""):
        self.feedback.append(validation_feedback)
        if len(self.feedback) == 1:
            raise ClaimGenerationError(
                "CLAIM_NUMBER_UNSUPPORTED",
                "Claim c2 uses number '720 billion dollars' that is not supported by its cited evidence.",
                "c2",
            )
        return [
            Claim(
                claim_id="c1",
                display_title="Apple 기능 변화 공개",
                claim_text="Apple은 공식 발표에서 새로운 소프트웨어 기능과 제공 범위를 함께 명확하게 설명했다.",
                editorial_role="change",
                claim_type="factual",
                entities=["Apple"],
                evidence_ids=["ev1"],
                source_url=source_lineage.source_url,
            )
        ]


class EntityBoundaryRetryGenerator:
    def __init__(self):
        self.feedback = []

    def generate_claims(self, source_lineage, validation_feedback=""):
        self.feedback.append(validation_feedback)
        if len(self.feedback) == 1:
            raise ClaimGenerationError(
                "CLAIM_ENTITY_UNSUPPORTED",
                "Claim c2 uses entity '아이폰 듀오' that is not present in its cited evidence.",
                "c2",
            )
        return [
            Claim(
                claim_id="c1",
                display_title="Apple 기능 변화 공개",
                claim_text="Apple은 공식 발표에서 새로운 소프트웨어 기능과 제공 범위를 함께 명확하게 설명했다.",
                editorial_role="change",
                claim_type="factual",
                entities=["Apple"],
                evidence_ids=["ev1"],
                source_url=source_lineage.source_url,
            )
        ]


def _passing_editorial(script, persona, expected_count):
    return SimpleNamespace(passed=True, feedback="", summary=lambda: "passed")


def _lineage():
    evidence_text = "Apple announced new software features and explained their availability."
    return SourceLineage(
        schema_version="2.0",
        topic="Apple 소프트웨어 발표",
        source_title="Apple software update",
        source_url="https://example.com/apple",
        context=evidence_text,
        article_id="article-1",
        content_hash="hash-1",
        evidence_passages=[
            EvidencePassage(
                evidence_id="ev1",
                article_id="article-1",
                text=evidence_text,
                source_url="https://example.com/apple",
                content_hash="hash-1",
            )
        ],
    )


def _run_creator(generator):
    lineage = _lineage()
    creator = ContentCreator(
        brand_persona=MagicMock(),
        claim_generator=generator,
        semantic_llm=None,
        editorial_evaluator=_passing_editorial,
    )

    # Avoid exercising the semantic LLM in these focused boundary tests.
    import src.agents.content_creator as content_creator_module
    original = content_creator_module.run_semantic_critic
    content_creator_module.run_semantic_critic = lambda *args, **kwargs: None
    try:
        script = creator.run(
            topic=lineage.topic,
            trend_report=TrendReport(query=lineage.topic, results=[]),
            num_cards=3,
            source_lineage=lineage,
        )
    finally:
        content_creator_module.run_semantic_critic = original
    return lineage, script


def test_content_creator_retries_claim_generator_number_support_error():
    generator = BoundaryRetryGenerator()
    lineage, script = _run_creator(generator)

    assert len(generator.feedback) == 2
    assert "CLAIM_NUMBER_UNSUPPORTED" in generator.feedback[1]
    assert "720 billion dollars" not in generator.feedback[1]
    assert "숫자+통화+단위 표면형을 문자 그대로 복사" in generator.feedback[1]
    assert script.topic == lineage.topic


def test_content_creator_retries_claim_generator_entity_support_error_without_echoing_entity():
    generator = EntityBoundaryRetryGenerator()
    lineage, script = _run_creator(generator)

    assert len(generator.feedback) == 2
    assert "CLAIM_ENTITY_UNSUPPORTED" in generator.feedback[1]
    assert "아이폰 듀오" not in generator.feedback[1]
    assert "원문 표면 문자열을 문자 그대로 복사" in generator.feedback[1]
    assert "세대명, 모델명, 시리즈 번호를 추론하지 마세요" in generator.feedback[1]
    assert script.topic == lineage.topic
