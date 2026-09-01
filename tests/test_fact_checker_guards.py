import pytest
from pydantic import ValidationError

from src.agents import fact_checker
from src.qa import content_quality_gate
from src.qa.deterministic_verifier import QualityGateError
from src.qa.publish_quality_gate import validate_publish_quality
from src.schemas.card_news import CardNewsScript, Slide
from src.schemas.fact_check import FactCheckItem, FactCheckReport


@pytest.fixture
def script():
    return CardNewsScript(
        topic="검증 주제",
        hook="검증된 훅",
        slides=[
            Slide(slide_number=1, slide_type="cover", title="표지", body="표지 본문"),
            Slide(slide_number=2, slide_type="content", title="근거", body="검증된 본문"),
            Slide(slide_number=3, slide_type="cta", title="확인", body="원문을 확인하세요"),
        ],
        hashtags=["#검증"],
    )


@pytest.mark.parametrize(
    "legacy_call",
    [
        lambda script: fact_checker.check_script(script, "원문"),
        lambda script: fact_checker.extract_claims(script),
        lambda script: fact_checker.verify_claim("검증되지 않은 주장"),
        lambda script: fact_checker._check_hallucination(script, "원문"),
    ],
)
def test_legacy_fact_checker_apis_fail_closed(script, legacy_call):
    with pytest.warns(DeprecationWarning), pytest.raises(QualityGateError) as exc:
        legacy_call(script)
    assert exc.value.error_code == "LEGACY_FACT_CHECKER_DISABLED"


@pytest.mark.parametrize(
    "legacy_call",
    [
        lambda script: content_quality_gate.validate_deterministic(script),
        lambda script: content_quality_gate.run_critic(script, []),
        lambda script: content_quality_gate.validate_content_quality({}, script),
    ],
)
def test_legacy_quality_gate_apis_fail_closed(script, legacy_call):
    with pytest.warns(DeprecationWarning), pytest.raises(QualityGateError) as exc:
        legacy_call(script)
    assert exc.value.error_code == "LEGACY_QUALITY_GATE_DISABLED"


def test_fact_check_report_requires_auditable_claim_ids():
    with pytest.raises(ValidationError):
        FactCheckReport(confirmed=1, confirmed_claim_ids=[])

    with pytest.raises(ValidationError):
        FactCheckReport(
            confirmed=2,
            confirmed_claim_ids=["duplicate", "duplicate"],
        )


def test_fact_check_report_requires_auditable_flagged_items():
    with pytest.raises(ValidationError):
        FactCheckReport(
            confirmed=1,
            confirmed_claim_ids=["confirmed-1"],
            disputed=1,
        )

    with pytest.raises(ValidationError):
        FactCheckReport(
            confirmed=1,
            confirmed_claim_ids=["same-claim"],
            disputed=1,
            flagged_items=[
                FactCheckItem(
                    claim_id="same-claim",
                    claim="충돌 주장",
                    verdict="disputed",
                    note="원문과 모순",
                )
            ],
        )


def test_fact_check_report_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        FactCheckReport.model_validate({"schema_version": "2.0", "bypass": True})


def test_publish_gate_requires_v2_report_and_verified_claim(script):
    with pytest.raises(QualityGateError) as missing:
        validate_publish_quality(None, script)
    assert missing.value.error_code == "FACT_CHECK_REPORT_INVALID"

    with pytest.raises(QualityGateError) as empty:
        validate_publish_quality(FactCheckReport(), script)
    assert empty.value.error_code == "VERIFIED_CLAIMS_MISSING"
