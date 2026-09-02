import warnings
from typing import Any

from pydantic import BaseModel

from src.qa.deterministic_verifier import QualityGateError

class CriticReport(BaseModel):
    passed: bool
    score: int
    feedback: str
    blocking_issue: bool = False


def _disabled() -> None:
    raise QualityGateError(
        "LEGACY_QUALITY_GATE_DISABLED",
        "Legacy quality-gate adapters are disabled; use the V2 claim gates.",
    )

def validate_deterministic(script: Any, target_locale: str = "ko-KR") -> CriticReport | None:
    warnings.warn("validate_deterministic is deprecated", DeprecationWarning, stacklevel=2)
    _disabled()

def run_critic(script: Any, claims: list[Any], target_locale: str = "ko-KR") -> CriticReport:
    warnings.warn("run_critic is deprecated", DeprecationWarning, stacklevel=2)
    _disabled()

def validate_content_quality(meta: dict, script: Any) -> None:
    """
    [DEPRECATED] Use the new DeterministicVerifier and SemanticCritic directly.
    Exists for legacy pipeline compatibility.
    """
    warnings.warn("validate_content_quality is deprecated", DeprecationWarning, stacklevel=2)

    _disabled()
