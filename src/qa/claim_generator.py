import json
import os
from typing import Any, Callable, List

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from src.qa.deterministic_verifier import QualityGateError
from src.schemas.card_news import Claim, SourceLineage


MAX_GENERATED_CLAIMS = 12

_CLAIM_SYSTEM_PROMPT = """
당신은 사실 관계를 엄밀하게 분리하는 분석기입니다.
주어진 원문(Evidence)을 바탕으로, 카드뉴스로 만들 핵심 사실들을 Claim(주장) 단위로 생성하세요.

반드시 원문에 있는 내용만 사용해야 하며, 다음 스키마의 JSON 리스트를 반환하세요.
{{
  "claims": [
    {{
      "claim_id": "c1",
      "claim_text": "원문에서 추출한 구체적 주장 문장",
      "claim_type": "factual" | "numerical" | "attributed_statement" | "inference" | "opinion" | "cta",
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
4. CTA 타입은 반드시 마지막에 하나만 넣고, 원문과 관련이 없는 "지금 당장 써보세요", "위험합니다" 식의 과도한 선동을 피하십시오.
"""

class ClaimGenerationError(QualityGateError, ValueError):
    """Fail-closed claim extraction error compatible with legacy ValueError callers."""


class ClaimGenerator:
    def __init__(self, llm=None, llm_factory: Callable[[], Any] | None = None):
        self._llm = llm
        self._llm_factory = llm_factory or self._build_default_llm
        self.prompt = ChatPromptTemplate.from_messages([
            ("system", _CLAIM_SYSTEM_PROMPT),
            ("human", "원문:\n{evidence}"),
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

    def generate_claims(self, lineage: SourceLineage) -> List[Claim]:
        if not lineage.is_verified_ready or not lineage.evidence_passages:
            raise ClaimGenerationError(
                "CLAIM_EVIDENCE_MISSING",
                "Verified SourceLineage evidence is required for claim generation.",
            )

        evidence_text = "\n\n".join(
            f"[ID: {ev.evidence_id}]\n{ev.text}" for ev in lineage.evidence_passages
        )

        try:
            chain = self.prompt | self._get_llm()
            response = chain.invoke({"evidence": evidence_text})
        except QualityGateError:
            raise
        except Exception as exc:
            raise ClaimGenerationError(
                "CLAIM_GENERATION_FAILED",
                f"Claim generator request failed: {type(exc).__name__}",
            ) from exc

        data = self._parse_response_content(getattr(response, "content", None))

        claims: list[Claim] = []
        seen_ids: set[str] = set()
        for index, raw_claim in enumerate(data["claims"]):
            if not isinstance(raw_claim, dict):
                raise ClaimGenerationError(
                    "CLAIM_SCHEMA_INVALID",
                    f"Claim at index {index} must be an object.",
                )
            try:
                claim = Claim.model_validate(
                    {**raw_claim, "source_url": lineage.source_url}
                )
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
        return claims
