import json

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from src.qa.claim_generator import ClaimGenerator
from src.qa.deterministic_verifier import QualityGateError
from src.qa.editorial_intent import is_roundup_topic
from src.qa.editorial_quality_gate import validate_claim_editorial_quality
from src.schemas.card_news import Claim, EvidencePassage, SourceLineage


def _claim(index: int, role: str, title: str, body: str, entity: str = "Apple") -> Claim:
    return Claim(
        claim_id=f"c{index}",
        display_title=title,
        editorial_role=role,
        claim_text=body,
        claim_type="factual",
        entities=[entity],
        evidence_ids=[f"ev-{index}"],
        source_url=f"https://example.com/{index}",
        verification_status="verified",
    )


def test_roundup_intent_is_explicit_not_generic_trend_language():
    assert is_roundup_topic("애플 WWDC 2026 핵심 요약") is True
    assert is_roundup_topic("WWDC 2026 한눈에 보기") is True
    assert is_roundup_topic("AI latest roundup") is True
    assert is_roundup_topic("AI 최신 트렌드") is False
    assert is_roundup_topic("OpenAI 새 모델 발표") is False


def test_roundup_gate_rejects_four_cards_about_one_narrow_subtopic():
    claims = [
        _claim(
            1,
            "context",
            "오디오 기능 공개 배경",
            "Apple은 WWDC에서 오디오 기능의 공개 배경과 지원 기기 범위를 함께 설명하며 도입 조건을 구체적으로 안내했다.",
        ),
        _claim(
            2,
            "change",
            "오디오 처리 방식 변화",
            "Apple은 오디오 기능의 처리 방식을 바꾸고 기기 안에서 처리되는 데이터 범위를 구체적으로 공개했다고 설명했다.",
        ),
        _claim(
            3,
            "limitation",
            "오디오 기능의 제한점",
            "Apple은 오디오 기능이 일부 환경에서 제한될 수 있으며 지원 조건에 따라 사용할 수 있는 범위가 달라진다고 밝혔다.",
        ),
        _claim(
            4,
            "impact",
            "오디오 변화의 영향",
            "Apple의 오디오 기능 변화는 사용자가 음성 관련 기능을 이용할 때 확인해야 할 프라이버시 조건에도 영향을 준다.",
        ),
    ]

    with pytest.raises(QualityGateError) as exc:
        validate_claim_editorial_quality(
            claims,
            topic="애플 WWDC 2026 핵심 요약",
            target_content_slides=4,
            source_title="Apple WWDC 2026",
        )

    assert exc.value.error_code == "EDITORIAL_COVERAGE_INSUFFICIENT"
    assert "too narrow" in str(exc.value)


def test_roundup_gate_accepts_distinct_event_points():
    claims = [
        _claim(
            1,
            "context",
            "Apple Intelligence 변화",
            "Apple은 WWDC에서 Apple Intelligence의 새 기능과 적용 범위를 공개하고 지원되는 사용 환경을 함께 설명했다.",
        ),
        _claim(
            2,
            "change",
            "iPadOS 작업 방식 변화",
            "Apple은 iPadOS의 멀티태스킹 동작을 조정해 여러 앱을 다루는 작업 흐름이 어떻게 달라지는지 구체적으로 소개했다.",
        ),
        _claim(
            3,
            "limitation",
            "watchOS 지원 조건",
            "Apple은 watchOS에서 제공되는 새 기능의 지원 기기와 이용 조건을 밝혀 모든 환경에서 동일하게 제공되지는 않는다고 설명했다.",
        ),
        _claim(
            4,
            "impact",
            "프라이버시 처리 원칙",
            "Apple은 새 기능의 데이터 처리 범위와 프라이버시 보호 방식을 설명하며 사용자 데이터가 처리되는 경계를 함께 제시했다.",
        ),
    ]

    validate_claim_editorial_quality(
        claims,
        topic="애플 WWDC 2026 핵심 요약",
        target_content_slides=4,
        source_title="Apple WWDC 2026",
    )


def _multi_source_lineage() -> SourceLineage:
    primary_text = (
        "Apple announced several WWDC updates including Apple Intelligence changes and "
        "new iPadOS multitasking behavior for supported devices."
    )
    secondary_text = (
        "Apple also described watchOS updates and privacy protections that define how "
        "supported features process user data."
    )
    return SourceLineage(
        schema_version="2.0",
        topic="애플 WWDC 2026 핵심 요약",
        source_title="Apple WWDC 2026 overview",
        source_url="https://example.com/primary",
        context=primary_text,
        article_id="article-primary",
        content_hash="hash-primary",
        evidence_passages=[
            EvidencePassage(
                evidence_id="ev-primary",
                article_id="article-primary",
                text=primary_text,
                source_url="https://example.com/primary",
                location="primary-context",
                content_hash="hash-primary",
            ),
            EvidencePassage(
                evidence_id="ev-secondary",
                article_id="article-secondary",
                text=secondary_text,
                source_url="https://example.com/secondary",
                location="Secondary WWDC report",
                content_hash="hash-secondary",
            ),
        ],
    )


def test_claim_generator_marks_roundup_scope_and_source_labels_in_prompt():
    calls = []

    def respond(prompt):
        calls.append(str(prompt))
        return AIMessage(
            content=json.dumps(
                {
                    "claims": [
                        {
                            "claim_id": "c1",
                            "display_title": "Apple Intelligence 변화",
                            "editorial_role": "change",
                            "claim_text": "Apple announced Apple Intelligence changes for supported devices.",
                            "claim_type": "factual",
                            "entities": ["Apple"],
                            "numbers": [],
                            "dates": [],
                            "evidence_ids": ["ev-primary"],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )

    generator = ClaimGenerator(llm=RunnableLambda(respond))
    generator.generate_claims(_multi_source_lineage())

    rendered = calls[0]
    assert "요약/정리형" in rendered
    assert "서로 다른 발표" in rendered
    assert "최소 2개 출처" in rendered
    assert "Apple WWDC 2026 overview" in rendered
    assert "Secondary WWDC report" in rendered
