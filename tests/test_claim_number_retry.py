from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from src.qa.claim_generator import ClaimGenerator
from src.schemas.card_news import EvidencePassage, SourceLineage


def test_claim_generator_retries_unsupported_numeric_scale():
    lineage = SourceLineage(
        schema_version="2.0",
        topic="애플 WWDC 2026 핵심 요약",
        source_title="Apple supplier outlook",
        source_url="http://test.com",
        context="Test",
        article_id="art_1",
        content_hash="hash",
        evidence_passages=[
            EvidencePassage(
                evidence_id="ev_1",
                article_id="art_1",
                source_url="http://test.com",
                text="Revenue guidance is $7.2 billion to $7.45 billion.",
                content_hash="hash",
            )
        ],
    )

    responses = iter([
        '{"claims": [{"claim_id": "c1", '
        '"display_title": "매출 가이던스 공개", '
        '"claim_text": "Revenue guidance is 720 billion dollars.", '
        '"claim_type": "numerical", "editorial_role": "evidence", '
        '"numbers": [{"raw_text": "720 billion dollars", '
        '"normalized_value": 720000000000, "unit": "dollars"}], '
        '"evidence_ids": ["ev_1"]}]}',
        '{"claims": [{"claim_id": "c1", '
        '"display_title": "매출 가이던스 공개", '
        '"claim_text": "Revenue guidance is $7.2 billion.", '
        '"claim_type": "numerical", "editorial_role": "evidence", '
        '"numbers": [{"raw_text": "$7.2 billion", '
        '"normalized_value": 7200000000, "unit": "dollars"}], '
        '"evidence_ids": ["ev_1"]}]}',
    ])
    calls = []

    def respond(prompt):
        calls.append(str(prompt))
        return AIMessage(content=next(responses))

    generator = ClaimGenerator(llm=RunnableLambda(respond))
    claims = generator.generate_claims(lineage)

    assert len(calls) == 2
    assert "NUMBER SUPPORT ERROR" in calls[1]
    assert "720 billion dollars" in calls[1]
    assert claims[0].numbers[0].raw_text == "$7.2 billion"
    assert int(claims[0].numbers[0].normalized_value) == 7200000000
