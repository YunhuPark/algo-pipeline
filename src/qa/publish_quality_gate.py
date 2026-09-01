from typing import Any

from src.qa.deterministic_verifier import QualityGateError
from src.schemas.card_news import CardNewsScript
from src.schemas.fact_check import FactCheckReport


def validate_publish_quality(report: Any, script: CardNewsScript) -> None:
    """Require an auditable V2 fact-check report immediately before publication."""

    if not isinstance(report, FactCheckReport):
        raise QualityGateError(
            "FACT_CHECK_REPORT_INVALID",
            "Publication requires a validated FactCheckReport V2 instance.",
        )
    if report.schema_version != "2.0":
        raise QualityGateError(
            "FACT_CHECK_REPORT_VERSION_INVALID",
            "Publication requires FactCheckReport schema version 2.0.",
        )
    if report.confirmed < 1 or not report.confirmed_claim_ids:
        raise QualityGateError(
            "VERIFIED_CLAIMS_MISSING",
            "Publication requires at least one verified claim.",
        )
    if report.disputed > 0:
        raise QualityGateError(
            "CLAIM_CONTRADICTED",
            "Publication is blocked because the report contains disputed claims.",
        )
    if report.unverifiable > 0:
        raise QualityGateError(
            "CLAIM_INSUFFICIENT_EVIDENCE",
            "Publication is blocked because the report contains unverifiable claims.",
        )
    if not isinstance(script, CardNewsScript) or not script.content_slides:
        raise QualityGateError(
            "SCRIPT_CONTENT_MISSING",
            "Publication requires a validated script with at least one content slide.",
        )
