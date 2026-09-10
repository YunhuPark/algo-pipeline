import json
import os
from typing import Any, Callable, List

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import ValidationError

from src.qa.deterministic_verifier import QualityGateError
from src.qa.editorial_intent import is_roundup_topic
from src.schemas.card_news import Claim, SourceLineage


MAX_GENERATED_CLAIMS = 12
MAX_CLAIM_RESPONSE_ATTEMPTS = 2

_RETRYABLE_RESPONSE_ERRORS = {
    "CLAIM_RESPONSE_EMPTY",
    "CLAIM_RESPONSE_INVALID_JSON",
    "CLAIM_RESPONSE_INVALID_ROOT",
    "CLAIM_LIST_MISSING",
    "CLAIM_LIST_INVALID",
    "CLAIMS_EMPTY",
    "CLAIM_LIMIT_EXCEEDED",
    "CLAIM_SCHEMA_INVALID",
    "CLAIM_ID_DUPLICATE",
}

_CLAIM_SYSTEM_PROMPT = """
당신은 사실 관계를 엄밀하게 분리하는 분석기입니다.
주어진 원문(Evidence)을 바탕으로, 카드뉴스로 만들 핵심 사실들을 Claim(주장) 단위로 생성하세요.

반드시 원문에 있는 내용만 사용해야 하며, 다음 스키마의 JSON 리스트를 반환하세요.
{{
  "claims": [
    {{
      "claim_id": "c1",
      "display_title": "독립적으로 읽히는 10~18자 제목",
      "editorial_role": "context" | "change" | "mechanism" | "evidence" | "limitation" | "impact" | "cta",
      "claim_text": "원문에서 추출한 구체적 주장 문장",
      "claim_type": "factual" | "numerical" | "attributed_statement" | "inference" | "opinion",
      "entities": ["언급된 고유명사", "회사명", "인명"],
      "numbers": [{{"raw_text": "3개", "normalized_value": 3.0, "unit": "개", "qualifier": "", "subject": ""}}],
      "dates": [{{"raw_text": "2026년 7월", "normalized_date": "2026-07", "precision": "month", "is_relative": false, "reference_date": ""}}],
      "evidence_ids": ["이 주장을 뒷받침하는 원문 단락의 ID"]
    }}
  ]
}}

규칙:
1. "3가지", "5가지"처럼 임의로 개수를 정하여 숫자를 만들어내지 마십시오.
2. 외부 일반 지식을 결합하지 마십시오.
3. 숫자가 포함된 문장은 반드시 numerical type을 사용하고, numbers 배열에 해당 숫자를 명시하십시오.
4. CTA Claim은 생성하지 마십시오. 마지막 CTA 카드는 검증된 Claim과 분리하여 시스템이 안전한 고정 문구로 생성합니다.
5. 숫자는 원문 표기를 그대로 유지하는 것을 기본으로 하십시오. 한국어 단위로 바꾸면 반드시 값을 정확히 환산하십시오. 예: $48 billion = 480억 달러(48억 달러가 아님), $4.8 billion = 48억 달러, 1.05 million = 105만.
6. 검증 오류 피드백이 있으면 문제가 된 주장을 삭제하거나 원문 표기와 정확히 일치하도록 다시 작성하십시오.
7. 기본 6장 카드뉴스용으로 서로 다른 내용의 non-CTA Claim 4개를 만드십시오. 모든 Claim은 비어 있지 않은 evidence_ids를 가져야 합니다.
8. non-CTA Claim은 context/change/mechanism/evidence/limitation/impact 중 최소 3가지 역할을 사용하고 같은 사실을 표현만 바꿔 반복하지 마십시오.
9. display_title은 10~18자의 자연스러운 한국어 완결형 제목이어야 하며 말줄임표를 쓰지 마십시오. claim_text에 없는 사실을 추가하면 안 됩니다.
10. claim_text는 카드 한 장에서 독립적으로 이해되는 45~80자의 자연스러운 한국어로 작성하십시오. 한 카드에 핵심 사실 하나만 담고, 고유명사·수치의 의미와 비교 기준을 생략하지 마십시오.
11. entities 배열에는 각 Claim이 인용한 Evidence에 실제로 등장하는 고유명사의 원문 철자만 넣으십시오. 근거에 없는 번역명·상위 조직·업계명은 넣지 마십시오.
12. 일반적인 단일 사건 주제라면 모든 Claim이 카드뉴스 주제와 고정 원문 제목이 가리키는 동일한 사건을 설명해야 합니다. 요약·총정리·roundup·recap처럼 여러 핵심 포인트를 요청한 주제라면 고정 원문 한 건으로 범위를 좁히지 말고 제공된 Evidence 전체에서 서로 다른 발표·기능·변화·제한·영향을 선택하십시오. 요약형에서는 4개 카드 중 같은 세부 기능이나 같은 좁은 키워드에 3개 이상 몰리지 않게 하십시오.
13. 원문이 "hundreds of millions"처럼 범위형 수치를 사용하면 임의의 정확한 금액으로 바꾸지 마십시오. claim_text와 numbers.raw_text에는 "수억 달러"처럼 같은 범위의 자연스러운 한국어 표현을 사용하고 근거의 정밀도를 그대로 유지하십시오.
14. 요약형 주제에서 서로 다른 출처의 Evidence가 2개 이상 제공되고 각 출처가 주제의 서로 다른 핵심 포인트를 직접 뒷받침한다면 최소 2개 출처를 활용하십시오. 단, 출처 다양성을 맞추기 위해 약한 근거나 관련 없는 사실을 억지로 사용하지 마십시오.
"""


class ClaimGenerationError(QualityGateError, ValueError):
    """Fail-closed claim extraction error compatible with legacy ValueError callers."""


class ClaimGenerator:
    def __init__(self, llm=None, llm_factory: Callable[[], Any] | None = None):
        self._llm = llm
        self._llm_factory = llm_factory or self._build_default_llm
        self.prompt = ChatPromptTemplate.from_messages([
            ("system", _CLAIM_SYSTEM_PROMPT),
            (
                "human",
                "카드뉴스 주제:\n{topic}\n\n고정 원문 제목:\n{source_title}"
                "\n\n편집 범위 지침:\n{coverage_guidance}"
                "\n\n원문 Evidence:\n{evidence}\n\n{schema_feedback}",
            ),
        ])

    @staticmethod
    def _build_default_llm():
        return ChatOpenAI(
            model=os.getenv("LLM_MODEL", "gpt-4o"),
            temperature=0.0,
            max_retries=1,
            request_timeout=20.0,
            model_kwargs={"response_format": {"type": "json_object"}},
        )

    def _get_llm(self):
        if self._llm is None:
            self._llm = self._llm_factory()
        return self._llm

    @staticmethod
    def _parse_response_content(content: Any) -> dict[str, Any]:
        if not isinstance(content, str) or not content.strip():
            raise ClaimGenerationError(
                "CLAIM_RESPONSE_EMPTY",
                "Claim generator returned empty or non-text content.",
            )

        content = content.strip()
        if content.startswith("```"):
            lines = content.splitlines()
            if len(lines) < 3 or lines[-1].strip() != "```":
                raise ClaimGenerationError(
                    "CLAIM_RESPONSE_INVALID_JSON",
                    "Claim response contains an incomplete Markdown fence.",
                )
            if lines[0].strip().lower() not in {"```", "```json"}:
                raise ClaimGenerationError(
                    "CLAIM_RESPONSE_INVALID_JSON",
                    "Claim response uses an unsupported Markdown fence.",
                )
            content = "\n".join(lines[1:-1]).strip()

        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ClaimGenerationError(
                "CLAIM_RESPONSE_INVALID_JSON",
                f"Failed to parse claim JSON: {exc.msg}",
            ) from exc

        if not isinstance(data, dict):
            raise ClaimGenerationError(
                "CLAIM_RESPONSE_INVALID_ROOT",
                "Expected JSON root to be an object.",
            )
        if "claims" not in data:
            raise ClaimGenerationError(
                "CLAIM_LIST_MISSING",
                "Claim response is missing the claims field.",
            )
        if not isinstance(data["claims"], list):
            raise ClaimGenerationError(
                "CLAIM_LIST_INVALID",
                "Claim response field claims must be a list.",
            )
        if not data["claims"]:
            raise ClaimGenerationError(
                "CLAIMS_EMPTY",
                "Claim generator returned no claims.",
            )
        if len(data["claims"]) > MAX_GENERATED_CLAIMS:
            raise ClaimGenerationError(
                "CLAIM_LIMIT_EXCEEDED",
                f"Claim generator returned more than {MAX_GENERATED_CLAIMS} claims.",
            )
        return data

    @staticmethod
    def _build_claims(data: dict[str, Any], lineage: SourceLineage) -> List[Claim]:
        claims: list[Claim] = []
        seen_ids: set[str] = set()
        for index, raw_claim in enumerate(data["claims"]):
            if not isinstance(raw_claim, dict):
                raise ClaimGenerationError(
                    "CLAIM_SCHEMA_INVALID",
                    f"Claim at index {index} must be an object.",
                )

            # CTA copy is a presentation concern. The assembler supplies a
            # deterministic source-safe CTA, so an LLM-generated CTA must not
            # consume factual verification or retry budget.
            if raw_claim.get("claim_type") == "cta":
                continue

            try:
                cited_ids = raw_claim.get("evidence_ids") or []
                evidence_by_id = {
                    item.evidence_id: item for item in lineage.evidence_passages
                }
                cited_source = next(
                    (
                        evidence_by_id[evidence_id].source_url
                        for evidence_id in cited_ids
                        if evidence_id in evidence_by_id
                    ),
                    lineage.source_url,
                )
                claim = Claim.model_validate(
                    {**raw_claim, "source_url": raw_claim.get("source_url") or cited_source}
                )
            except ValidationError as exc:
                issues = ", ".join(
                    f"{'.'.join(str(part) for part in error['loc'])}:{error['type']}"
                    for error in exc.errors(include_url=False, include_context=False, include_input=False)
                )
                raise ClaimGenerationError(
                    "CLAIM_SCHEMA_INVALID",
                    f"Claim at index {index} failed schema validation ({issues}).",
                ) from exc
            except Exception as exc:
                raise ClaimGenerationError(
                    "CLAIM_SCHEMA_INVALID",
                    f"Claim at index {index} failed schema validation.",
                ) from exc
            if claim.claim_id in seen_ids:
                raise ClaimGenerationError(
                    "CLAIM_ID_DUPLICATE",
                    f"Duplicate claim_id: {claim.claim_id}",
                    claim.claim_id,
                )
            seen_ids.add(claim.claim_id)
            claims.append(claim)

        if not claims:
            raise ClaimGenerationError(
                "CLAIMS_EMPTY",
                "Claim generator returned no factual content claims.",
            )
        return claims

    @staticmethod
    def _evidence_text(lineage: SourceLineage) -> str:
        blocks: list[str] = []
        for evidence in lineage.evidence_passages:
            if (
                evidence.article_id == lineage.article_id
                and evidence.source_url == lineage.source_url
            ):
                source_label = lineage.source_title
            else:
                source_label = evidence.location or evidence.source_url
            blocks.append(
                f"[ID: {evidence.evidence_id}]\n"
                f"[Source: {source_label}]\n"
                f"{evidence.text}"
            )
        return "\n\n".join(blocks)

    @staticmethod
    def _coverage_guidance(lineage: SourceLineage) -> str:
        if is_roundup_topic(lineage.topic):
            source_count = len({
                evidence.source_url for evidence in lineage.evidence_passages
            })
            return (
                "이 요청은 요약/정리형입니다. 고정 원문 한 건의 세부 기능으로 범위를 "
                "좁히지 말고 Evidence 전체에서 서로 다른 발표·제품/기능·정책/제한·영향을 "
                "우선 선택하세요. 4개 카드 중 같은 세부 기능이나 같은 좁은 키워드에 3개 "
                "이상 몰리지 않게 하세요. "
                + (
                    "서로 다른 출처가 2개 이상 제공되었으므로, 각 출처가 주제의 핵심을 직접 "
                    "뒷받침하는 경우 최소 2개 출처를 활용하세요. "
                    if source_count >= 2
                    else ""
                )
                + "각 Claim은 자신이 인용한 evidence에 의해 완전히 지지되어야 합니다."
            )
        return (
            "이 요청은 단일 사건 중심입니다. 카드뉴스 주제와 고정 원문 제목이 가리키는 "
            "사건 범위를 유지하고, 인용한 Evidence 밖의 사실을 추가하지 마세요."
        )

    def generate_claims(
        self,
        lineage: SourceLineage,
        validation_feedback: str = "",
    ) -> List[Claim]:
        if not lineage.is_verified_ready or not lineage.evidence_passages:
            raise ClaimGenerationError(
                "CLAIM_EVIDENCE_MISSING",
                "Verified SourceLineage evidence is required for claim generation.",
            )

        evidence_text = self._evidence_text(lineage)
        coverage_guidance = self._coverage_guidance(lineage)
        schema_feedback = validation_feedback.strip()

        for attempt in range(1, MAX_CLAIM_RESPONSE_ATTEMPTS + 1):
            try:
                chain = self.prompt | self._get_llm()
                response = chain.invoke({
                    "topic": lineage.topic,
                    "source_title": lineage.source_title,
                    "coverage_guidance": coverage_guidance,
                    "evidence": evidence_text,
                    "schema_feedback": schema_feedback,
                })
            except QualityGateError:
                raise
            except Exception as exc:
                raise ClaimGenerationError(
                    "CLAIM_GENERATION_FAILED",
                    f"Claim generator request failed: {type(exc).__name__}",
                ) from exc

            try:
                data = self._parse_response_content(getattr(response, "content", None))
                return self._build_claims(data, lineage)
            except ClaimGenerationError as exc:
                if (
                    attempt >= MAX_CLAIM_RESPONSE_ATTEMPTS
                    or exc.error_code not in _RETRYABLE_RESPONSE_ERRORS
                ):
                    raise
                print(
                    "[QualityGate] Claim JSON 형식이 유효하지 않아 "
                    f"자동 재시도합니다 ({attempt}/{MAX_CLAIM_RESPONSE_ATTEMPTS - 1}, "
                    f"{exc.error_code})."
                )
                schema_feedback = (
                    "이전 응답은 아래 이유로 스키마 검증에 실패했습니다. "
                    "원문의 사실만 사용하여 전체 JSON 객체를 처음부터 다시 생성하세요. "
                    "모든 claim에는 claim_id, claim_text, 허용된 claim_type, entities 배열, "
                    "numbers 배열, dates 배열, evidence_ids 배열이 있어야 합니다. "
                    f"검증 오류: {exc}"
                )

        raise AssertionError("unreachable")
