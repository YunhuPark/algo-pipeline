import warnings
from typing import Any, Literal
from pydantic import BaseModel
from src.qa.deterministic_verifier import QualityGateError

class CriticReport(BaseModel):
    passed: bool
    score: int
    feedback: str
    blocking_issue: bool = False

def validate_deterministic(script: Any, target_locale: str = "ko-KR") -> CriticReport | None:
    warnings.warn("validate_deterministic is deprecated", DeprecationWarning, stacklevel=2)
    return None

def run_critic(script: Any, claims: list[Any], target_locale: str = "ko-KR") -> CriticReport:
    warnings.warn("run_critic is deprecated", DeprecationWarning, stacklevel=2)
    return CriticReport(passed=True, score=100, feedback="Legacy adapter")

def validate_content_quality(meta: dict, script: Any) -> None:
    """
    [DEPRECATED] Use the new DeterministicVerifier and SemanticCritic directly.
    Exists for legacy pipeline compatibility.
    """
    warnings.warn("validate_content_quality is deprecated", DeprecationWarning, stacklevel=2)
    
    # If legacy code calls this with undisputed counts, let it pass.
    # Otherwise if it has disputed counts, fail it.
    if meta.get("fact_disputed", 0) > 0 or meta.get("fact_unverifiable", 0) > 0:
        raise QualityGateError("LEGACY_DISPUTED", "Legacy fact checker found disputed or unverifiable claims.")
