import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Iterable, List, Dict, Tuple, Optional
from src.schemas.card_news import Claim, SourceLineage

class QualityGateError(Exception):
    def __init__(self, error_code: str, message: str, claim_id: Optional[str] = None):
        super().__init__(message)
        self.error_code = error_code
        self.claim_id = claim_id
        self.failure_stage = "QUALITY_GATE"
        self.publish_succeeded = False
        self.ig_post_id = None


# Alias mapping for entities
ALLOWED_ALIASES = {
    "openai": ["open ai"],
    "meta": ["facebook", "fb"],
    "google": ["alphabet"],
}


_SCALE_FACTORS = {
    "천": Decimal("1000"),
    "만": Decimal("10000"),
    "십만": Decimal("100000"),
    "백만": Decimal("1000000"),
    "천만": Decimal("10000000"),
    "억": Decimal("100000000"),
    "십억": Decimal("1000000000"),
    "백억": Decimal("10000000000"),
    "천억": Decimal("100000000000"),
    "조": Decimal("1000000000000"),
    "k": Decimal("1000"),
    "thousand": Decimal("1000"),
    "m": Decimal("1000000"),
    "million": Decimal("1000000"),
    "b": Decimal("1000000000"),
    "billion": Decimal("1000000000"),
    "t": Decimal("1000000000000"),
    "trillion": Decimal("1000000000000"),
}

_KOREAN_SCALE_PATTERN = r"(?:천억|백억|십억|천만|백만|십만|조|억|만|천)"
_ENGLISH_SCALE_PATTERN = r"(?:trillion|billion|million|thousand|[kmbt](?![A-Za-z]))"
_UNIT_PATTERN = (
    r"(?:퍼센트|percentage|percent|%|달러|dollars?|usd|원|krw|won|"
    r"명|people|persons?|users?|customers?|employees?|개|items?|parameters?|"
    r"tokens?|companies?|models?|곳|places?|locations?|개월|months?|년|years?|"
    r"일|days?|시간|hours?|분|minutes?|초|seconds?|배|times?|x(?![A-Za-z]))"
)
_NUMBER_PATTERN = r"\d[\d,]*(?:\.\d+)?"

_KOREAN_SCALED_PART_RE = re.compile(
    rf"(?P<number>{_NUMBER_PATTERN})\s*(?P<scale>{_KOREAN_SCALE_PATTERN})",
    re.IGNORECASE,
)
_KOREAN_COMPOUND_NUMBER_RE = re.compile(
    rf"(?<![\w.])(?P<expression>"
    rf"{_NUMBER_PATTERN}\s*{_KOREAN_SCALE_PATTERN}"
    rf"(?:\s*{_NUMBER_PATTERN}\s*{_KOREAN_SCALE_PATTERN})+)"
    rf"\s*(?P<unit>{_UNIT_PATTERN})?",
    re.IGNORECASE,
)
_NUMBER_MENTION_RE = re.compile(
    rf"(?<![\w.])(?P<currency>[$₩])?\s*"
    rf"(?P<number>{_NUMBER_PATTERN})\s*"
    rf"(?P<scale>{_KOREAN_SCALE_PATTERN}|{_ENGLISH_SCALE_PATTERN})?\s*"
    rf"(?P<unit>{_UNIT_PATTERN})?",
    re.IGNORECASE,
)


def _to_decimal(raw: str) -> Optional[Decimal]:
    try:
        return Decimal(raw.replace(",", ""))
    except InvalidOperation:
        return None


def _canonical_unit(raw_unit: str = "", currency: str = "") -> str:
    unit = unicodedata.normalize("NFKC", raw_unit or "").strip().lower()
    if currency == "$" or unit in {"달러", "dollar", "dollars", "usd"}:
        return "usd"
    if currency == "₩" or unit in {"원", "krw", "won"}:
        return "krw"
    if unit in {"%", "퍼센트", "percent", "percentage"}:
        return "percent"
    if unit in {"명", "people", "person", "persons", "user", "users", "customer", "customers", "employee", "employees"}:
        return "people"
    if unit in {"개", "item", "items", "parameter", "parameters", "token", "tokens", "company", "companies", "model", "models"}:
        return "count"
    if unit in {"곳", "place", "places", "location", "locations"}:
        return "place"
    if unit in {"일", "day", "days"}:
        return "day"
    if unit in {"개월", "month", "months"}:
        return "month"
    if unit in {"년", "year", "years"}:
        return "year"
    if unit in {"시간", "hour", "hours"}:
        return "hour"
    if unit in {"분", "minute", "minutes"}:
        return "minute"
    if unit in {"초", "second", "seconds"}:
        return "second"
    if unit in {"배", "time", "times", "x"}:
        return "multiplier"
    return ""


def _extract_number_mentions(text: str) -> Iterable[Tuple[Decimal, str]]:
    """Yield canonical numeric values and semantic units from Korean/English text."""
    normalized = unicodedata.normalize("NFKC", text or "")
    compound_spans: list[tuple[int, int]] = []

    # Korean large numbers are often additive (for example, 1억 500만 = 105M).
    for compound in _KOREAN_COMPOUND_NUMBER_RE.finditer(normalized):
        total = Decimal("0")
        valid = True
        for part in _KOREAN_SCALED_PART_RE.finditer(compound.group("expression")):
            value = _to_decimal(part.group("number"))
            factor = _SCALE_FACTORS.get(part.group("scale").lower())
            if value is None or factor is None:
                valid = False
                break
            total += value * factor
        if valid:
            compound_spans.append(compound.span("expression"))
            yield total, _canonical_unit(compound.group("unit") or "")

    for match in _NUMBER_MENTION_RE.finditer(normalized):
        if any(start <= match.start() < end for start, end in compound_spans):
            continue
        value = _to_decimal(match.group("number"))
        if value is None:
            continue
        scale = (match.group("scale") or "").lower()
        value *= _SCALE_FACTORS.get(scale, Decimal("1"))
        yield value, _canonical_unit(
            match.group("unit") or "",
            match.group("currency") or "",
        )


def normalize_text(text: str) -> str:
    """Normalize text using NFKC, lowercase, remove extra spaces and punctuation."""
    if not text:
        return ""
    text = unicodedata.normalize('NFKC', text)
    text = text.lower()
    # Remove basic punctuation but keep alphanumerics and hangul
    text = re.sub(r'[^\w\s가-힣]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def parse_number_with_qualifier(text: str) -> Tuple[Optional[Decimal], str]:
    """Return the first canonical value/unit, including Korean/English scales."""
    return next(iter(_extract_number_mentions(text)), (None, ""))


class DeterministicVerifier:
    """순수 함수 기반 결정적 검증기"""

    @staticmethod
    def verify_claims(claims: List[Claim], lineage: SourceLineage) -> None:
        """
        Verify all claims deterministically against the source lineage.
        Raises QualityGateError on failure.
        """
        if not lineage.is_verified_ready:
            raise QualityGateError("LEGACY_LINEAGE_UNVERIFIED", "Cannot verify legacy lineage for new generation.")
        if not lineage.evidence_passages:
            raise QualityGateError("EVIDENCE_MISSING", "Verified lineage contains no evidence passages.")
        if not claims:
            raise QualityGateError("CLAIMS_EMPTY", "At least one claim is required for verification.")

        claim_ids = [claim.claim_id for claim in claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise QualityGateError("CLAIM_ID_DUPLICATE", "Claim IDs must be unique.")

        cta_indexes = [index for index, claim in enumerate(claims) if claim.claim_type == "cta"]
        if len(cta_indexes) > 1:
            raise QualityGateError("CTA_COUNT_INVALID", "At most one CTA claim is allowed.")
        if cta_indexes and cta_indexes[0] != len(claims) - 1:
            raise QualityGateError("CTA_ORDER_INVALID", "CTA claim must be the final claim.")

        # Map evidence by ID
        evidence_map = {ev.evidence_id: ev for ev in lineage.evidence_passages}

        for claim in claims:
            # 1. Verification of evidence constraints
            if not claim.evidence_ids:
                raise QualityGateError("EVIDENCE_MISSING", f"Claim {claim.claim_id} of type {claim.claim_type} requires evidence_ids.", claim.claim_id)

            if claim.claim_type == "numerical" and not claim.numbers:
                raise QualityGateError(
                    "NUMBERS_MISSING",
                    f"Numerical claim {claim.claim_id} must declare normalized numbers.",
                    claim.claim_id,
                )

            for ev_id in claim.evidence_ids:
                if ev_id not in evidence_map:
                    raise QualityGateError("EVIDENCE_ID_UNKNOWN", f"Evidence ID {ev_id} not found in lineage.", claim.claim_id)
                ev = evidence_map[ev_id]

                # Multi-source lineage is allowed.  The evidence ID is the
                # authoritative link; a claim-level URL, when present, must
                # identify at least one of its cited passages.

            cited_urls = {evidence_map[ev_id].source_url for ev_id in claim.evidence_ids}
            if claim.source_url and claim.source_url not in cited_urls:
                raise QualityGateError(
                    "SOURCE_URL_MISMATCH",
                    "Claim source URL doesn't match any cited evidence URL.",
                    claim.claim_id,
                )

            # Extract combined evidence text for this claim
            combined_evidence_text = " ".join([evidence_map[ev_id].text for ev_id in claim.evidence_ids])
            norm_evidence_text = normalize_text(combined_evidence_text)

            # 2. Check Entities (Token/Boundary based)
            for entity in claim.entities:
                norm_ent = normalize_text(entity)
                if not norm_ent:
                    continue

                # Expand aliases
                allowed_forms = [norm_ent] + ALLOWED_ALIASES.get(norm_ent, [])
                found = False
                for form in allowed_forms:
                    # ASCII boundaries block partial matches ("Meta" in "metadata")
                    # while allowing Korean particles attached to an English entity
                    # ("OpenAI는", "Google이").
                    pattern = r'(?<![a-z0-9])' + re.escape(form) + r'(?![a-z0-9])'
                    if re.search(r'[a-zA-Z]', form):
                        if re.search(pattern, norm_evidence_text):
                            found = True
                            break
                    else:
                        if form in norm_evidence_text:
                            found = True
                            break
                if not found:
                    raise QualityGateError("ENTITY_UNSUPPORTED", f"Entity '{entity}' not found in evidence.", claim.claim_id)

            # 3. Check Numbers
            for num_obj in claim.numbers:
                parsed_claim = list(_extract_number_mentions(num_obj.raw_text))
                if parsed_claim:
                    val, qual = parsed_claim[0]
                    declared_unit = _canonical_unit(num_obj.unit)
                    if not qual and declared_unit:
                        qual = declared_unit
                else:
                    val = num_obj.normalized_value
                    qual = _canonical_unit(num_obj.unit)

                # Compare canonical values after magnitude conversion. Units remain
                # fail-closed, so 30% cannot validate 30 people and 3 days cannot
                # validate 3 items.
                found = any(
                    val == ev_val and qual == ev_qual
                    for ev_val, ev_qual in _extract_number_mentions(combined_evidence_text)
                )
                if not found:
                    raise QualityGateError("NUMBER_UNSUPPORTED", f"Number '{num_obj.raw_text}' not supported by evidence.", claim.claim_id)

            # 4. Check Dates
            for date_obj in claim.dates:
                # Basic check to see if the date text appears in the evidence
                if normalize_text(date_obj.raw_text) not in norm_evidence_text and date_obj.normalized_date not in combined_evidence_text:
                    raise QualityGateError("DATE_UNSUPPORTED", f"Date '{date_obj.raw_text}' not supported by evidence.", claim.claim_id)
                # Fail if relative date converted to arbitrary absolute date
                if date_obj.is_relative and not date_obj.raw_text:
                    raise QualityGateError("DATE_UNSUPPORTED", "Relative date must have raw_text reference.", claim.claim_id)

            # 5. CTA Policy Violation (Heuristic check)
            if claim.claim_type == "cta":
                bad_patterns = ["당장 써볼 것", "지금 당장", "빨리 다운로드", "위험합니다!"]
                for bad in bad_patterns:
                    if bad in claim.claim_text:
                        raise QualityGateError("CTA_POLICY_VIOLATION", f"CTA text violates policy: {bad}", claim.claim_id)

            # If all passed
            claim.verification_status = "verified"
