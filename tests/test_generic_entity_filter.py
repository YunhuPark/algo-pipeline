from src.qa.deterministic_verifier import DeterministicVerifier, QualityGateError
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


def test_apple_watch_series_korean_surface_is_canonicalized_and_verified():
    claim = Claim(
        claim_id="c-watch",
        claim_text="애플워치 시리즈 12에 새 기능이 추가됩니다.",
        claim_type="factual",
        entities=["애플워치 시리즈 12"],
        evidence_ids=["ev_1"],
    )

    assert claim.entities == ["Apple Watch Series 12"]
    DeterministicVerifier.verify_claims(
        [claim],
        _lineage("Apple Watch Series 12 adds a new feature."),
    )
    assert claim.verification_status == "verified"


def test_apple_watch_series_canonicalization_still_fails_without_source_support():
    claim = Claim(
        claim_id="c-watch",
        claim_text="애플워치 시리즈 12에 새 기능이 추가됩니다.",
        claim_type="factual",
        entities=["애플워치 시리즈 12"],
        evidence_ids=["ev_1"],
    )

    assert claim.entities == ["Apple Watch Series 12"]
    try:
        DeterministicVerifier.verify_claims(
            [claim],
            _lineage("Apple Watch Series 11 remains available."),
        )
    except QualityGateError as exc:
        assert exc.error_code == "ENTITY_UNSUPPORTED"
    else:
        raise AssertionError("unsupported canonical entity must fail closed")
