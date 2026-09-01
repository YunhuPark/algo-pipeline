import warnings
from typing import Any

from src.qa.deterministic_verifier import QualityGateError
from src.schemas.card_news import CardNewsScript
from src.schemas.fact_check import FactCheckItem, FactCheckReport


def _disabled() -> None:
    raise QualityGateError(
        "LEGACY_FACT_CHECKER_DISABLED",
        "Legacy fact-check APIs cannot prove SourceLineage V2 and are disabled.",
    )

def check_script(script: CardNewsScript, source_text: str = "") -> FactCheckReport:
    """
    [DEPRECATED] Use the orchestrated ClaimGenerator and DeterministicVerifier pipeline instead.
    """
    warnings.warn("fact_checker is deprecated, use pipeline orchestration instead", DeprecationWarning, stacklevel=2)
    _disabled()

def extract_claims(script: CardNewsScript) -> list[str]:
    warnings.warn("extract_claims is deprecated.", DeprecationWarning, stacklevel=2)
    _disabled()

def verify_claim(claim: str) -> Any:
    warnings.warn("verify_claim is deprecated.", DeprecationWarning, stacklevel=2)
    _disabled()

def _check_hallucination(script: CardNewsScript, source_text: str) -> list[Any]:
    warnings.warn("_check_hallucination is deprecated.", DeprecationWarning, stacklevel=2)
    _disabled()
