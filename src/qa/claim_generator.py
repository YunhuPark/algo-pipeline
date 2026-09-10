import json
import os
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, List

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import ValidationError

from src.qa.deterministic_verifier import QualityGateError, parse_number_with_qualifier
from src.qa.editorial_intent import is_roundup_topic
from src.schemas.card_news import Claim, SourceLineage


MAX_GENERATED_CLAIMS = 12
MAX_CLAIM_RESPONSE_ATTEMPTS = 2

_ALLOWED_EDITORIAL_ROLES = {
    "context",
    "change",
    "mechanism",
    "evidence",
    "limitation",
    "impact",
    "detail",
    "cta",
}

_RANGE_SPLITTERS = (
    re.compile(
        r"^\s*between\s+(?P<left>.+?)\s+and\s+(?P<right>.+?)\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?P<left>.+?)\s*(?:에서|부터|[~～]|\bto\b)\s*(?P<right>.+?)\s*$",
        re.IGNORECASE,
    ),
)

_EVIDENCE_NUMBER_CANDIDATE_RE = re.compile(
    r"(?<![\w.])(?:[$₩]\s*)?\d[\d,]*(?:\.\d+)?\s*"
    r"(?:천억|백억|십억|천만|백만|십만|조|억|만|천|"
    r"trillion|billion|million|thousand|[kmbt](?![A-Za-z]))?\s*"
    r"(?:퍼센트|percentage|percent|%|달러|dollars?|usd|원|krw|won|"
    r"명|people|persons?|users?|customers?|employees?|개|items?|parameters?|"
    r"tokens?|companies?|models?|곳|places?|locations?|개월|months?|년|years?|"
    r"일|days?|시간|hours?|분|minutes?|초|seconds?|배|times?|x(?![A-Za-z]))?",
    re.IGNORECASE,
)

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
      "editorial_role": "context" | "change" | "mechanism" | "evidence" | "limitation" | "impact" | "detail",
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
15. 범위·비교 숫자는 특히 보수적으로 다루십시오. Evidence가 "$7.2 billion to $7.45 billion"처럼 범위를 쓰면 claim_text도 가능한 한 같은 숫자·통화·단위 표기를 유지하십시오. 검증기가 명시적으로 지원하는 것이 확실하지 않다면 "720억에서 745억 달러"처럼 두 끝점을 동시에 환산하지 마십시오.
16. 범위 숫자를 한국어로 자유 변환하지 마십시오. 안전한 선택은 (a) 원문 범위 표기를 그대로 유지하거나, (b) 해당 카드에 꼭 필요한 단일 숫자 한 개만 원문 표기 그대로 사용하는 것입니다. 범위의 두 끝점을 새 단위로 바꿔 조합하거나 추정 단위를 보충하지 마십시오.
17. editorial_role은 반드시 context/change/mechanism/evidence/limitation/impact/detail 중 하나의 문자열만 사용하십시오. feature, privacy, security, announcement 같은 새 역할 이름을 만들지 마십시오.
18. numbers의 normalized_value는 반드시 단일 JSON 숫자 또는 숫자로만 된 문자열이어야 합니다. 배열, 객체, "7.2 billion"처럼 단위가 섞인 문자열을 넣지 마십시오.
19. 범위에 두 끝점이 모두 필요하면 하나의 normalized_value에 배열을 넣지 말고 numbers에 두 객체로 분리하십시오. 예: Evidence가 "$7.2 billion to $7.45 billion"이면 numbers는 [{{"raw_text":"$7.2 billion","normalized_value":7200000000,"unit":"dollars"}}, {{"raw_text":"$7.45 billion","normalized_value":7450000000,"unit":"dollars"}}]처럼 각 끝점을 스칼라 값으로 기록하십시오.
20. numbers.raw_text에는 실제 claim_text에 사용한 수치 표현과 최대한 동일한 문자열을 넣으십시오. 각 숫자는 반드시 인용한 Evidence에서 직접 확인되어야 합니다.
21. normalized_value는 raw_text의 숫자를 단위까지 반영한 실제 값이어야 합니다. 예: "$7.2 billion"의 normalized_value는 7200000000입니다. raw_text를 "720 billion dollars"로 바꾸면 값이 720000000000이 되어 전혀 다른 주장입니다. 소수점을 제거하거나 10배·100배 키우지 마십시오.
22. 숫자 오류 재시도에서는 새 숫자를 만들지 말고 문제가 된 Claim의 인용 Evidence에서 숫자 표현 하나를 문자 그대로 복사해 raw_text와 claim_text에 사용하십시오.
"""


class ClaimGenerationError(QualityGateError, ValueError):
    """Fail-closed claim extraction error compatible with legacy ValueError callers."""


def _split_range_text(raw_text: str) -> tuple[str, str] | None:
    """Split only explicit two-endpoint range syntax."""

    text = str(raw_text or "").strip()
    for pattern in _RANGE_SPLITTERS:
        match = pattern.fullmatch(text)
        if match:
            left = match.group("left").strip()
            right = match.group("right").strip()
            if left and right:
                return left, right
    return None


def _endpoint_with_unit(endpoint: str, unit: str) -> str:
    """Carry an explicitly declared semantic unit onto a bare range endpoint."""

    clean = endpoint.strip()
    clean_unit = str(unit or "").strip()
    if not clean_unit:
        return clean

    lowered = clean.lower()
    unit_lower = clean_unit.lower()
    if unit_lower in lowered or "$" in clean or "₩" in clean:
        return clean

    currency_aliases = {
        "달러": ("dollar", "dollars", "usd"),
        "dollar": ("달러", "dollars", "usd"),
        "dollars": ("달러", "dollar", "usd"),
        "usd": ("달러", "dollar", "dollars"),
        "원": ("krw", "won"),
        "krw": ("원", "won"),
        "won": ("원", "krw"),
    }
    if any(alias in lowered for alias in currency_aliases.get(unit_lower, ())):
        return clean
    return f"{clean} {clean_unit}".strip()


def _normalize_number_payloads(raw_numbers: Any) -> Any:
    """Repair only an unambiguous LLM schema mistake for two-value ranges.

    Some JSON responses put ``[start, end]`` into ``normalized_value`` even
    though the public schema requires one Decimal per number object. When the
    accompanying raw_text contains an explicit two-endpoint range, split it
    into two scalar number objects. The deterministic verifier still checks
    both endpoint values against cited evidence, so this does not relax factual
    validation. Ambiguous payloads are left untouched and fail closed.
    """

    if not isinstance(raw_numbers, list):
        return raw_numbers

    normalized_numbers: list[Any] = []
    for number in raw_numbers:
        if not isinstance(number, dict):
            normalized_numbers.append(number)
            continue

        normalized_value = number.get("normalized_value")
        if not isinstance(normalized_value, (list, tuple)) or len(normalized_value) != 2:
            normalized_numbers.append(number)
            continue

        endpoints = _split_range_text(str(number.get("raw_text", "")))
        if endpoints is None:
            normalized_numbers.append(number)
            continue

        unit = str(number.get("unit", ""))
        for endpoint, value in zip(endpoints, normalized_value):
            split_number = dict(number)
            split_number["raw_text"] = _endpoint_with_unit(endpoint, unit)
            split_number["normalized_value"] = value
            normalized_numbers.append(split_number)

    return normalized_numbers


def _normalize_claim_payload(raw_claim: dict[str, Any]) -> dict[str, Any]:
    """Normalize non-factual metadata without weakening evidence checks."""

    payload = dict(raw_claim)
    role = payload.get("editorial_role")
    if role is not None:
        normalized_role = str(role).strip().lower()
        payload["editorial_role"] = (
            normalized_role if normalized_role in _ALLOWED_EDITORIAL_ROLES else "detail"
        )
    payload["numbers"] = _normalize_number_payloads(payload.get("numbers", []))
    return payload


def _canonical_declared_unit(unit: str) -> str:
    """Map a declared unit through the verifier's canonical parser."""

    _, canonical = parse_number_with_qualifier(f"1 {unit}".strip())
    return canonical


def _evidence_number_candidates(evidence_text: str) -> list[tuple[str, Decimal, str]]:
    """Return exact evidence number phrases with verifier-canonical values."""

    candidates: list[tuple[str, Decimal, str]] = []
    for match in _EVIDENCE_NUMBER_CANDIDATE_RE.finditer(evidence_text or ""):
        raw = match.group(0).strip()
        if not raw:
            continue
        value, unit = parse_number_with_qualifier(raw)
        if value is None:
            continue
        candidates.append((raw, value, unit))
    return candidates


def _align_claim_number_text_to_evidence(claim: Claim, evidence_text: str) -> Claim:
    """Repair only raw-text/normalized-value inconsistencies using cited evidence.

    The normalized numeric value is never changed here. A raw string is replaced
    only when (1) its own parsed value disagrees with normalized_value, and
    (2) exactly one cited evidence phrase has that normalized value and a
    compatible semantic unit. If normalized_value is also wrong, nothing is
    repaired and the deterministic Quality Gate still fails closed.
    """

    evidence_candidates = _evidence_number_candidates(evidence_text)
    if not evidence_candidates or not claim.numbers:
        return claim

    replacements: dict[str, str] = {}
    repaired_numbers = []
    for number in claim.numbers:
        raw_value, raw_unit = parse_number_with_qualifier(number.raw_text)
        declared_unit = _canonical_declared_unit(number.unit)
        effective_raw_unit = raw_unit or declared_unit

        if raw_value == number.normalized_value:
            repaired_numbers.append(number)
            continue

        matching_raws = {
            raw
            for raw, evidence_value, evidence_unit in evidence_candidates
            if evidence_value == number.normalized_value
            and (
                (effective_raw_unit and evidence_unit == effective_raw_unit)
                or (not effective_raw_unit and declared_unit and evidence_unit == declared_unit)
            )
        }
        if len(matching_raws) != 1:
            repaired_numbers.append(number)
            continue

        replacement = next(iter(matching_raws))
        if replacement == number.raw_text:
            repaired_numbers.append(number)
            continue

        replacements[number.raw_text] = replacement
        repaired_numbers.append(number.model_copy(update={"raw_text": replacement}))

    if not replacements:
        return claim

    display_title = claim.display_title
    claim_text = claim.claim_text
    for old_text, new_text in replacements.items():
        display_title = display_title.replace(old_text, new_text)
        claim_text = claim_text.replace(old_text, new_text)

    return claim.model_copy(
        update={
            "display_title": display_title,
            "claim_text": claim_text,
            "numbers": repaired_numbers,
        }
    )


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
        evidence_by_id = {
            item.evidence_id: item for item in lineage.evidence_passages
        }
        for index, raw_claim in enumerate(data["claims"]):
            if not isinstance(raw_claim, dict):
                raise ClaimGenerationError(
                    "CLAIM_SCHEMA_INVALID",
                    f"Claim at index {index} must be an object.",
                )

            if raw_claim.get("claim_type") == "cta":
                continue

            try:
                normalized_claim = _normalize_claim_payload(raw_claim)
                cited_ids = normalized_claim.get("evidence_ids") or []
                cited_source = next(
                    (
                        evidence_by_id[evidence_id].source_url
                        for evidence_id in cited_ids
                        if evidence_id in evidence_by_id
                    ),
                    lineage.source_url,
                )
                claim = Claim.model_validate(
                    {
                        **normalized_claim,
                        "source_url": normalized_claim.get("source_url") or cited_source,
                    }
                )
                cited_evidence_text = " ".join(
                    evidence_by_id[evidence_id].text
                    for evidence_id in cited_ids
                    if evidence_id in evidence_by_id
                )
                claim = _align_claim_number_text_to_evidence(
                    claim,
                    cited_evidence_text,
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
                    "editorial_role은 context/change/mechanism/evidence/limitation/impact/detail 중 "
                    "하나만 사용하세요. numbers[*].normalized_value는 배열이나 객체가 아니라 "
                    "단일 숫자여야 합니다. 범위는 두 number 객체로 분리하고 각 끝점의 "
                    "normalized_value를 스칼라 값으로 기록하세요. "
                    f"검증 오류: {exc}"
                )

        raise AssertionError("unreachable")
