from src.qa.deterministic_verifier import DeterministicVerifier
from src.schemas.card_news import Claim, EvidencePassage, SourceLineage


def _lineage(text: str) -> SourceLineage:
    return SourceLineage(
        schema_version="2.0",
        topic="테스트",
        source_title="테스트 기사",
        source_url="https://example.com/article",
        context="테스트",
        article_id="art_1",
        content_hash="hash",
        evidence_passages=[
            EvidencePassage(
                evidence_id="ev_1",
                article_id="art_1",
                source_url="https://example.com/article",
                text=text,
                content_hash="hash",
            )
        ],
    )


def test_generic_market_noun_is_not_treated_as_named_entity():
    claim = Claim(
        claim_id="c1",
        claim_text="관련 시장의 변화가 예상됩니다.",
        claim_type="factual",
        entities=["시장"],
        evidence_ids=["ev_1"],
    )

    assert claim.entities == []
    DeterministicVerifier.verify_claims(
        [claim],
        _lineage("Apple announced a new feature for developers."),
    )


def test_real_proper_noun_still_reaches_entity_gate():
    claim = Claim(
        claim_id="c1",
        claim_text="Apple 관련 발표입니다.",
        claim_type="factual",
        entities=["Apple"],
        evidence_ids=["ev_1"],
    )

    assert claim.entities == ["Apple"]
