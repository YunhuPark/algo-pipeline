import warnings
from pydantic import BaseModel, Field
from typing import List, Any
from src.schemas.card_news import CardNewsScript, SourceLineage
from src.schemas.card_news import CardNewsScript

class FactCheckItem(BaseModel):
    claim: str
    verdict: str
    note: str

class FactCheckReport(BaseModel):
    confirmed: int = 0
    disputed: int = 0
    unverifiable: int = 0
    flagged_items: List[FactCheckItem] = Field(default_factory=list)

def check_script(script: CardNewsScript, source_text: str = "") -> FactCheckReport:
    """
    [DEPRECATED] Use the orchestrated ClaimGenerator and DeterministicVerifier pipeline instead.
    """
    warnings.warn("fact_checker is deprecated, use pipeline orchestration instead", DeprecationWarning, stacklevel=2)
    from src.qa.semantic_critic import run_semantic_critic
    from src.qa.deterministic_verifier import QualityGateError

    lineage = SourceLineage(topic=script.topic, source_text=source_text, url="", raw_metadata={})
    
    # We construct a dummy claim list from script contents to pass to semantic critic
    claims = []
    for slide in script.content_slides:
        claims.append({
            "text": slide.body,
            "evidence": source_text[:100] if source_text else ""
        })

    report = FactCheckReport(confirmed=0, disputed=0, unverifiable=0, flagged_items=[])
    try:
        critic_report = run_semantic_critic(claims, lineage)
        report.confirmed = len(claims)
    except QualityGateError as e:
        report.disputed = 1
        report.flagged_items.append(FactCheckItem(claim="Semantic Check", verdict="disputed", note=str(e)))

    return report

def extract_claims(script: CardNewsScript) -> list[str]:
    warnings.warn("extract_claims is deprecated.", DeprecationWarning, stacklevel=2)
    return [slide.body for slide in script.content_slides]

def verify_claim(claim: str) -> Any:
    warnings.warn("verify_claim is deprecated.", DeprecationWarning, stacklevel=2)
    class DummyClaimResult:
        verdict = "supported"
        note = "Legacy adapter"
    return DummyClaimResult()

def _check_hallucination(script: CardNewsScript, source_text: str) -> list[Any]:
    warnings.warn("_check_hallucination is deprecated.", DeprecationWarning, stacklevel=2)
    return []
