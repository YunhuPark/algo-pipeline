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
from src.qa.deterministic_verifier import (
    DeterministicVerifier,
    QualityGateError,
    repair_source_backed_numeric_localizations,
)
from src.qa.editorial_intent import is_roundup_topic
from src.qa.editorial_quality_gate import validate_claim_editorial_quality
from src.qa.semantic_critic import run_semantic_critic
from src.qa.script_assembler import ScriptAssembler
from src.schemas.fact_check import FactCheckReport


MAX_CLAIM_QUALITY_ATTEMPTS = 6
MAX_CLAIM_QUALITY_REPAIRS_PER_ERROR = 2

_FACTUAL_CLAIM_QUALITY_ERRORS = {
    "EVIDENCE_MISSING",
    "EVIDENCE_ID_UNKNOWN",
    "SOURCE_URL_MISMATCH",
    "ENTITY_UNSUPPORTED",
    "CLAIM_ENTITY_UNSUPPORTED",
    "NUMBERS_MISSING",
    "NUMBER_UNSUPPORTED",
    "CLAIM_NUMBER_UNSUPPORTED",
    "DATE_UNSUPPORTED",
    "CTA_COUNT_INVALID",
    "CTA_ORDER_INVALID",
    "CTA_POLICY_VIOLATION",
    "CLAIM_CONTRADICTED",
    "CLAIM_INSUFFICIENT_EVIDENCE",
}

_EDITORIAL_CLAIM_QUALITY_ERRORS = {
    "EDITORIAL_COVERAGE_INSUFFICIENT",
    "EDITORIAL_HEADLINE_MISSING",
    "EDITORIAL_HEADLINE_INVALID",
    "EDITORIAL_COPY_LENGTH_INVALID",
    "EDITORIAL_ROLE_DIVERSITY_INSUFFICIENT",
    "EDITORIAL_CLAIM_REDUNDANT",
    "EDITORIAL_TOPIC_MISMATCH",
    "EDITORIAL_QUALITY_FAILED",
}

_RETRYABLE_CLAIM_QUALITY_ERRORS = (
    _FACTUAL_CLAIM_QUALITY_ERRORS | _EDITORIAL_CLAIM_QUALITY_ERRORS
)

_NUMERIC_SUPPORT_ERRORS = {
    "NUMBERS_MISSING",
    "NUMBER_UNSUPPORTED",
    "CLAIM_NUMBER_UNSUPPORTED",
}


def _failure_class(error_code: str) -> str:
    if error_code in _FACTUAL_CLAIM_QUALITY_ERRORS:
        return "factual"
    return "editorial"


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

        # 2~4. Claim 생성 + Quality Gate. 서로 다른 오류가 같은 repair
        # budget을 소진하지 않도록 error_code별로 bounded retry를 관리한다.
        # 검증 기준 자체는 낮추지 않는다.
        requested_cards = num_cards or 6
        target_content_slides = max(1, requested_cards - 2)
        validation_feedback = "\n".join(
            item.strip() for item in (feedback, disputed_notes) if item.strip()
        )
        script: CardNewsScript | None = None
        repair_counts: dict[str, int] = {}
        claims = []
        for attempt in range(1, MAX_CLAIM_QUALITY_ATTEMPTS + 1):
            # Generation-bound support errors are QualityGateError subclasses too.
            # Keep generation inside the same bounded retry loop so an exhausted
            # ClaimGenerator-local retry does not bypass ContentCreator repair.
            claims = []
            try:
                if validation_feedback:
                    claims = self.claim_generator.generate_claims(
                        source_lineage,
                        validation_feedback=validation_feedback,
                    )
                else:
                    # Keep the first call compatible with injected legacy test doubles.
                    claims = self.claim_generator.generate_claims(source_lineage)

                claims, numeric_repairs = repair_source_backed_numeric_localizations(
                    claims,
                    source_lineage,
                )
                for claim_id, old_text, new_text in numeric_repairs:
                    print(
                        "[QualityGate] 인용 근거에 따라 숫자 단위 환산을 교정했습니다 "
                        f"(claim_id={claim_id}, '{old_text}' -> '{new_text}')."
                    )

                DeterministicVerifier.verify_claims(claims, source_lineage)
                run_semantic_critic(claims, source_lineage, llm=self.semantic_llm)
                validate_claim_editorial_quality(
                    claims,
                    topic=source_lineage.topic,
                    target_content_slides=target_content_slides,
                    source_title=source_lineage.source_title,
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
                failure_class = _failure_class(exc.error_code)
                error_repairs = repair_counts.get(exc.error_code, 0)
                if (
                    attempt >= MAX_CLAIM_QUALITY_ATTEMPTS
                    or exc.error_code not in _RETRYABLE_CLAIM_QUALITY_ERRORS
                    or error_repairs >= MAX_CLAIM_QUALITY_REPAIRS_PER_ERROR
                ):
                    raise
                error_repairs += 1
                repair_counts[exc.error_code] = error_repairs

                claim_label = f" (claim_id={exc.claim_id})" if exc.claim_id else ""
                print(
                    "[QualityGate] Claim 또는 편집 품질 검증에 실패하여 "
                    f"자동 재생성합니다 ({failure_class} {error_repairs}/"
                    f"{MAX_CLAIM_QUALITY_REPAIRS_PER_ERROR}, "
                    f"{exc.error_code}{claim_label})."
                )
                failed_claim = next(
                    (claim for claim in claims if claim.claim_id == exc.claim_id),
                    None,
                )
                evidence_by_id = {
                    item.evidence_id: item
                    for item in source_lineage.evidence_passages
                }
                cited_evidence = "\n".join(
                    f"[{evidence_id}] {evidence_by_id[evidence_id].text}"
                    for evidence_id in (
                        failed_claim.evidence_ids if failed_claim else []
                    )
                    if evidence_id in evidence_by_id
                )
                roundup = is_roundup_topic(source_lineage.topic)
                targeted_feedback = ""
                if exc.error_code in _NUMERIC_SUPPORT_ERRORS:
                    if error_repairs >= MAX_CLAIM_QUALITY_REPAIRS_PER_ERROR:
                        targeted_feedback = (
                            " 숫자 검증이 반복 실패했습니다. 이번 재시도에서는 실패한 숫자를 "
                            "다시 표현하거나 환산하지 마세요. 해당 숫자 Claim 전체를 버리고, "
                            "숫자가 필요 없는 다른 Evidence-backed Claim으로 교체하세요. "
                            "교체 Claim의 numbers 배열은 비워 두고 원문에 직접 적힌 비수치 사실만 사용하세요."
                        )
                    else:
                        targeted_feedback = (
                            " 숫자 오류를 고칠 때는 실패한 생성 숫자 문자열을 참고하거나 재사용하지 마세요. "
                            "인용 Evidence를 처음부터 다시 읽고 숫자+통화+단위 표면형을 문자 그대로 복사하세요. "
                            "한국어 억/조 단위로 새로 환산하지 말고 원문 표기를 유지하세요. "
                            "안전하게 복사할 수 없으면 그 숫자 Claim 전체를 버리고 숫자가 필요 없는 "
                            "다른 Evidence-backed Claim으로 교체하세요."
                        )
                elif exc.error_code == "DATE_UNSUPPORTED":
                    targeted_feedback = (
                        " 날짜 오류를 고칠 때는 인용 근거의 날짜를 그대로 사용하고, "
                        "원문에 없는 연도·월·일을 보충하지 마세요."
                    )
                elif exc.error_code in {"ENTITY_UNSUPPORTED", "CLAIM_ENTITY_UNSUPPORTED"}:
                    targeted_feedback = (
                        " entities 배열은 해당 Claim이 인용한 Evidence에 실제로 등장하는 "
                        "고유명사의 원문 철자만 사용하세요. 번역명이나 추정한 조직명은 제거하세요. "
                        "같은 unsupported entity가 반복되면 해당 Claim 전체를 다른 근거 기반 사실로 교체하세요."
                    )
                elif exc.error_code in {
                    "CLAIM_CONTRADICTED",
                    "CLAIM_INSUFFICIENT_EVIDENCE",
                }:
                    targeted_feedback = (
                        " 의미 검증에 실패한 Claim은 추론으로 보완하지 말고, "
                        "인용 근거에 직접 쓰인 사실만 충실하게 번역하거나 요약하세요."
                    )
                elif exc.error_code == "EDITORIAL_TOPIC_MISMATCH":
                    if roundup:
                        targeted_feedback = (
                            " 이 요청은 요약/정리형이므로 하나의 세부 사건으로 범위를 좁히지 마세요. "
                            f"요청 주제 '{source_lineage.topic}'의 범위를 유지하면서 Evidence 전체에서 "
                            "직접 뒷받침되는 서로 다른 발표·기능·제한·영향을 선택하세요. "
                            "각 Claim은 인용한 evidence 범위 안에서만 작성하세요."
                        )
                    else:
                        targeted_feedback = (
                            " 전체 Claim을 다른 일반론으로 바꾸지 마세요. "
                            f"카드뉴스 주제 '{source_lineage.topic}'와 고정 원문 제목 "
                            f"'{source_lineage.source_title}'이 가리키는 한 사건만 설명하세요. "
                            "최소 2개 Claim의 제목 또는 본문에 원문의 핵심 고유명사를 직접 명시하세요."
                        )
                elif exc.error_code == "EDITORIAL_COVERAGE_INSUFFICIENT":
                    if roundup:
                        targeted_feedback = (
                            " 요약형 요청이 한 세부 주제에 편중되었습니다. 같은 세부 기능을 "
                            "배경·보안·제한·영향으로 표현만 바꿔 반복하지 마세요. Evidence 전체에서 "
                            "서로 다른 발표·제품/기능·변화·제한·영향을 골라 4개 카드를 구성하세요. "
                            "서로 다른 출처가 주제의 핵심을 직접 뒷받침한다면 여러 출처를 활용하되, "
                            "관련 없는 내용을 다양성 확보용으로 억지로 넣지 마세요."
                        )
                    else:
                        targeted_feedback = (
                            " 서로 다른 핵심 사실이 충분하지 않습니다. 같은 사실을 풀어 쓰지 말고 "
                            "Evidence 안에서 독립적으로 검증되는 다른 Claim을 선택하세요."
                        )
                elif exc.error_code == "EDITORIAL_COPY_LENGTH_INVALID":
                    targeted_feedback = (
                        " 길이 오류가 난 Claim만 우선 고쳐 주세요. 모든 본문 Claim은 45~80자로 작성하세요. "
                        "한 카드에는 인용 근거가 직접 뒷받침하는 핵심 사실 하나만 남기고, "
                        "길이를 맞추기 위한 새 정보·평가·전망은 추가하지 마세요."
                    )
                elif exc.error_code == "EDITORIAL_QUALITY_FAILED":
                    targeted_feedback = (
                        " 편집 평가 피드백을 그대로 반영하되 사실을 새로 만들지 마세요. "
                        "특히 첫 장은 입력 주제를 단순 복사하지 말고 궁금증·효용 중심으로 더 짧게 구성하고, "
                        "마지막 CTA는 해당 주제에 맞는 질문형 또는 저장 유도형으로 자연스럽게 마무리하세요."
                    )
                if cited_evidence:
                    targeted_feedback += f"\n문제가 된 Claim의 인용 근거:\n{cited_evidence[:1600]}"

                # Numeric support failures are prompt-contaminating if the rejected
                # generated value is echoed back verbatim. Keep the error code but
                # intentionally omit str(exc) for numeric retries; the cited Evidence
                # remains available as the only source of allowed numeric wording.
                error_detail = "" if exc.error_code in _NUMERIC_SUPPORT_ERRORS else f" - {exc}"
                current_failure_feedback = (
                    "이전 Claim 세트가 사실 또는 편집 품질 검증에 실패했습니다. "
                    "문제가 된 주장을 삭제하거나 인용한 evidence 범위 안에서 정확히 다시 작성하세요. "
                    "숫자는 원문 표기를 유지하거나 수학적으로 동일한 값으로만 환산하세요. "
                    "원문에 명시되지 않은 단체, 행사, 평가, 원인 또는 전망을 추가하지 마세요. "
                    "CTA Claim은 만들지 말고 모든 Claim에 정확한 evidence_ids를 넣으세요. "
                    "각 카드는 서로 다른 역할과 정보를 가져야 하며 제목은 말줄임표 없이 완결하세요. "
                    f"검증 오류: {exc.error_code}{claim_label}{error_detail}"
                    f"{targeted_feedback}"
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