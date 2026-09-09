"""
Phase 2: Content Creator — 2단계 방식 (Evidence-bound Claim 기반 개편)
Step 1: 기사에서 구체적 사실(Claim) 추출
Step 2: 추출된 사실 결정적/의미적 검증 (Quality Gate)
Step 3: 통과된 사실만으로 카드뉴스 생성
→ GPT가 기사 내용을 무시하고 임의로 만드는 것을 완전 차단
"""
from __future__ import annotations

from typing import Optional

from src.schemas.card_news import CardNewsScript, TrendReport, SourceLineage
from src.persona import load_persona, Persona
from src.qa.claim_generator import ClaimGenerator
from src.qa.deterministic_verifier import DeterministicVerifier, QualityGateError
from src.qa.editorial_quality_gate import validate_claim_editorial_quality
from src.qa.semantic_critic import run_semantic_critic
from src.qa.script_assembler import ScriptAssembler
from src.schemas.fact_check import FactCheckReport


MAX_CLAIM_QUALITY_ATTEMPTS = 3

_RETRYABLE_CLAIM_QUALITY_ERRORS = {
    "EVIDENCE_MISSING",
    "EVIDENCE_ID_UNKNOWN",
    "SOURCE_URL_MISMATCH",
    "ENTITY_UNSUPPORTED",
    "NUMBERS_MISSING",
    "NUMBER_UNSUPPORTED",
    "DATE_UNSUPPORTED",
    "CTA_COUNT_INVALID",
    "CTA_ORDER_INVALID",
    "CTA_POLICY_VIOLATION",
    "CLAIM_CONTRADICTED",
    "CLAIM_INSUFFICIENT_EVIDENCE",
    "EDITORIAL_COVERAGE_INSUFFICIENT",
    "EDITORIAL_HEADLINE_MISSING",
    "EDITORIAL_HEADLINE_INVALID",
    "EDITORIAL_COPY_LENGTH_INVALID",
    "EDITORIAL_ROLE_DIVERSITY_INSUFFICIENT",
    "EDITORIAL_CLAIM_REDUNDANT",
    "EDITORIAL_TOPIC_MISMATCH",
    "EDITORIAL_QUALITY_FAILED",
}


def _is_listicle_topic(topic: str) -> bool:
    import re
    return bool(re.search(r'\d+가지|\d+대|TOP\s*\d+|탑\s*\d+|\d+선|이유\s*\d+', topic, re.IGNORECASE))


class ContentCreator:
    """Evidence-bound content creator used by the production Pipeline."""

    def __init__(
        self,
        brand_persona: Persona | None = None,
        claim_generator: ClaimGenerator | None = None,
        semantic_llm=None,
        editorial_evaluator=None,
    ):
        self.persona = brand_persona or load_persona()
        self.claim_generator = claim_generator or ClaimGenerator()
        self.semantic_llm = semantic_llm
        self.editorial_evaluator = editorial_evaluator
        self.last_fact_check_report: FactCheckReport | None = None

    def _evaluate_editorial_quality(self, script: CardNewsScript, expected_count: int):
        evaluator = self.editorial_evaluator
        if evaluator is None:
            from src.agents.verifier import verify

            evaluator = verify
        return evaluator(script, self.persona, expected_count=expected_count)

    def run(
        self,
        topic: str,
        trend_report: TrendReport,
        num_cards: Optional[int] = None,
        handle: str = "algo__kr",
        persona: Optional[Persona] = None,
        video_infos: Optional[list] = None,
        feedback: str = "",
        raw_article_body: str = "",
        disputed_notes: str = "",
        source_lineage: Optional[SourceLineage] = None,
        editorial_angle: str = "",
    ) -> CardNewsScript:
        """
        새로운 증거 기반 Claim 생성 및 검증을 수행한 뒤 ScriptAssembler로 넘깁니다.
        """
        # A reused creator must never expose a report from an earlier successful run
        # after the current run fails before a new report is produced.
        self.last_fact_check_report = None

        # 1. Lineage 확인 (신규 생성 시 V2 필수)
        if not source_lineage or not source_lineage.is_verified_ready:
            raise QualityGateError("LEGACY_LINEAGE_UNVERIFIED", "Cannot generate new content with unverified legacy source lineage.")

        # 2~4. Claim 생성 + Quality Gate. 근거 불일치가 발생하면 검증
        # 피드백을 누적해 최대 두 번 재생성하고, 세 번째 실패는 차단한다.
        requested_cards = num_cards or 6
        target_content_slides = max(1, requested_cards - 2)
        validation_feedback = "\n".join(
            item.strip() for item in (feedback, disputed_notes) if item.strip()
        )
        script: CardNewsScript | None = None
        for attempt in range(1, MAX_CLAIM_QUALITY_ATTEMPTS + 1):
            if validation_feedback:
                claims = self.claim_generator.generate_claims(
                    source_lineage,
                    validation_feedback=validation_feedback,
                )
            else:
                # Keep the first call compatible with injected legacy test doubles.
                claims = self.claim_generator.generate_claims(source_lineage)

            try:
                DeterministicVerifier.verify_claims(claims, source_lineage)
                run_semantic_critic(claims, source_lineage, llm=self.semantic_llm)
                validate_claim_editorial_quality(
                    claims,
                    topic=source_lineage.topic,
                    target_content_slides=target_content_slides,
                )
                script = ScriptAssembler.assemble(
                    topic=source_lineage.topic,
                    claims=claims,
                    num_cards=requested_cards,
                    editorial_angle=editorial_angle,
                )
                editorial_result = self._evaluate_editorial_quality(
                    script,
                    requested_cards,
                )
                if not editorial_result.passed:
                    raise QualityGateError(
                        "EDITORIAL_QUALITY_FAILED",
                        editorial_result.feedback or editorial_result.summary(),
                    )
                print(f"[EditorialGate] {editorial_result.summary()}")
                break
            except QualityGateError as exc:
                if (
                    attempt >= MAX_CLAIM_QUALITY_ATTEMPTS
                    or exc.error_code not in _RETRYABLE_CLAIM_QUALITY_ERRORS
                ):
                    raise

                claim_label = f" (claim_id={exc.claim_id})" if exc.claim_id else ""
                print(
                    "[QualityGate] Claim 또는 편집 품질 검증에 실패하여 "
                    f"자동 재생성합니다 ({attempt}/{MAX_CLAIM_QUALITY_ATTEMPTS - 1}, "
                    f"{exc.error_code}{claim_label})."
                )
                current_failure_feedback = (
                    "이전 Claim 세트가 사실 또는 편집 품질 검증에 실패했습니다. "
                    "문제가 된 주장을 삭제하거나 인용한 evidence 범위 안에서 정확히 다시 작성하세요. "
                    "숫자는 원문 표기를 유지하거나 수학적으로 동일한 값으로만 환산하세요. "
                    "원문에 명시되지 않은 단체, 행사, 평가, 원인 또는 전망을 추가하지 마세요. "
                    "CTA Claim은 만들지 말고 모든 Claim에 정확한 evidence_ids를 넣으세요. "
                    "각 카드는 서로 다른 역할과 정보를 가져야 하며 제목은 말줄임표 없이 완결하세요. "
                    f"검증 오류: {exc.error_code}{claim_label} - {exc}"
                )
                validation_feedback = "\n".join(
                    item
                    for item in (validation_feedback, current_failure_feedback)
                    if item
                )

        if script is None:
            raise AssertionError("editorial pipeline completed without a script")

        self.last_fact_check_report = FactCheckReport(
            confirmed_claim_ids=[claim.claim_id for claim in claims],
            confirmed=len(claims),
            disputed=0,
            unverifiable=0,
            flagged_items=[],
        )

        return script

def run(
    topic: str,
    trend_report: Optional[TrendReport] = None,
    num_cards: int = 5,
    handle: str = "algo__kr",
    persona: Optional[Persona] = None,
    video_infos: Optional[list] = None,
    feedback: str = "",
    raw_article_body: str = "",
    disputed_notes: str = ""
) -> CardNewsScript:
    import warnings
    warnings.warn("content_creator.run function is deprecated. Instantiate ContentCreator and call run() instead.", DeprecationWarning, stacklevel=2)
    return ContentCreator().run(
        topic=topic,
        trend_report=trend_report,
        num_cards=num_cards,
        handle=handle,
        persona=persona,
        video_infos=video_infos,
        feedback=feedback,
        raw_article_body=raw_article_body,
        disputed_notes=disputed_notes,
        source_lineage=None
    )
