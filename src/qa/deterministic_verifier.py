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

# Entity verification is reserved for named entities. The Claim schema already
# filters these exact common nouns, but model_copy/model_construct and legacy
# deserialization paths can bypass Pydantic field validators. Keep the same
# narrow fail-safe at the verification boundary so generic metadata such as
# "시장" cannot cause a false ENTITY_UNSUPPORTED failure. Unsupported proper
# nouns remain subject to the normal fail-closed entity gate.
_GENERIC_ENTITY_TERMS = frozenset({
    "시장",
    "업계",
    "산업",
    "기술",
    "서비스",
    "기능",
    "제품",
    "사용자",
    "소비자",
    "고객",
    "기업",
    "회사",
    "분야",
    "발표",
    "뉴스",
    "데이터",
    "플랫폼",
    "기기",
    "개발자",
    "모델",
    "보안",
    "프라이버시",
    "매출",
    "가격",
    "성장",
    "전망",
    "경쟁",
    "변화",
    "영향",
    "제한",
})


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
_RANGE_VALUE_PATTERN = (
    rf"(?:[$₩]\s*)?{_NUMBER_PATTERN}\s*"
    rf"(?:{_KOREAN_SCALE_PATTERN}|{_ENGLISH_SCALE_PATTERN})?\s*"
    rf"(?:{_UNIT_PATTERN})?"
)
_RANGE_ENDPOINT_RE = re.compile(
    rf"^\s*(?P<currency>[$₩])?\s*(?P<number>{_NUMBER_PATTERN})\s*"
    rf"(?P<scale>{_KOREAN_SCALE_PATTERN}|{_ENGLISH_SCALE_PATTERN})?\s*"
    rf"(?P<unit>{_UNIT_PATTERN})?\s*$",
    re.IGNORECASE,
)
_SIMPLE_NUMERIC_RANGE_RE = re.compile(
    rf"(?P<left>{_RANGE_VALUE_PATTERN})\s*"
    rf"(?:에서|부터|[~～]|\bto\b)\s*"
    rf"(?P<right>{_RANGE_VALUE_PATTERN})",
    re.IGNORECASE,
)
_BETWEEN_NUMERIC_RANGE_RE = re.compile(
    rf"\bbetween\s+(?P<left>{_RANGE_VALUE_PATTERN})\s+and\s+"
    rf"(?P<right>{_RANGE_VALUE_PATTERN})",
    re.IGNORECASE,
)
_KOREAN_EOK_USD_RE = re.compile(
    rf"(?<![\w.])(?P<number>{_NUMBER_PATTERN})\s*억\s*(?P<unit>달러|usd)",
    re.IGNORECASE,
)
_ENGLISH_BILLION_USD_RE = re.compile(
    rf"(?<![\w.])(?P<currency>\$)?\s*(?P<number>{_NUMBER_PATTERN})\s*"
    rf"(?:billion|b(?![A-Za-z]))\s*(?P<unit>dollars?|usd)?",
    re.IGNORECASE,
)

# Approximate newsworthy magnitudes must not be coerced into one exact value.
# Compare their scale band instead: ``hundreds of millions`` and ``수억``
# both describe band 8, while still requiring compatible semantic units.
_ENGLISH_APPROXIMATE_NUMBER_RE = re.compile(
    rf"(?<![A-Za-z0-9])"
    rf"(?:(?P<prefix>tens|hundreds)\s+of\s+)?"
    rf"(?P<scale>thousands?|millions?|billions?|trillions?)"
    rf"(?:\s+of)?\s*(?P<unit>{_UNIT_PATTERN})?"
    rf"(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_KOREAN_APPROXIMATE_NUMBER_RE = re.compile(
    rf"(?<![가-힣A-Za-z0-9])수\s*"
    rf"(?P<scale>천억|백억|십억|천만|백만|십만|조|억|만|천)"
    rf"\s*(?P<unit>{_UNIT_PATTERN})?",
    re.IGNORECASE,
)
_ENGLISH_APPROXIMATE_EXPONENTS = {
    "thousand": 3,
    "million": 6,
    "billion": 9,
    "trillion": 12,
}
_KOREAN_APPROXIMATE_EXPONENTS = {
    "천": 3,
    "만": 4,
    "십만": 5,
    "백만": 6,
    "천만": 7,
    "억": 8,
    "십억": 9,
    "백억": 10,
    "천억": 11,
    "조": 12,
}

_ENGLISH_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
_ENGLISH_MONTH_PATTERN = "|".join(
    month.title() for month in _ENGLISH_MONTHS
)
_ISO_DATE_RE = re.compile(
    r"(?<!\d)(?P<year>\d{4})[-/.](?P<month>\d{1,2})"
    r"(?:[-/.](?P<day>\d{1,2}))?(?!\d)"
)
_KOREAN_DATE_RE = re.compile(
    r"(?:(?P<year>\d{4})\s*년\s*)?"
    r"(?P<month>\d{1,2})\s*월"
    r"(?:\s*(?P<day>\d{1,2})\s*일)?"
)
_ENGLISH_MONTH_DAY_RE = re.compile(
    rf"\b(?P<month>{_ENGLISH_MONTH_PATTERN})\s+"
    r"(?P<day>\d{1,2})(?:st|nd|rd|th)?"
    r"(?:,?\s+(?P<year>\d{4}))?\b"
)
_ENGLISH_DAY_MONTH_RE = re.compile(
    rf"\b(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+"
    rf"(?P<month>{_ENGLISH_MONTH_PATTERN})"
    r"(?:\s+(?P<year>\d{4}))?\b"
)
_ENGLISH_MONTH_YEAR_RE = re.compile(
    rf"\b(?P<month>{_ENGLISH_MONTH_PATTERN})\s+(?P<year>\d{{4}})\b"
)
_ENGLISH_MONTH_RE = re.compile(rf"\b(?P<month>{_ENGLISH_MONTH_PATTERN})\b")


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


def _range_endpoint_parts(raw_text: str) -> tuple[Decimal, str, str] | None:
    """Parse one range endpoint without guessing a scale or semantic unit."""

    match = _RANGE_ENDPOINT_RE.fullmatch(
        unicodedata.normalize("NFKC", raw_text or "").strip()
    )
    if not match:
        return None
    value = _to_decimal(match.group("number"))
    if value is None:
        return None
    scale = (match.group("scale") or "").lower()
    unit = _canonical_unit(
        match.group("unit") or "",
        match.group("currency") or "",
    )
    return value, scale, unit


def _canonical_numeric_range(
    left_raw: str,
    right_raw: str,
    declared_unit: str = "",
) -> tuple[tuple[Decimal, str], tuple[Decimal, str]] | None:
    """Canonicalize a two-endpoint range using only explicit/shared syntax.

    A missing scale or unit may inherit from the opposite endpoint because
    forms such as ``7.2~7.45 billion`` and ``72억~74.5억 달러`` conventionally
    share their trailing qualifier. No currency exchange or inferred unit
    conversion is performed.
    """

    left = _range_endpoint_parts(left_raw)
    right = _range_endpoint_parts(right_raw)
    if left is None or right is None:
        return None

    left_value, left_scale, left_unit = left
    right_value, right_scale, right_unit = right

    if not left_scale and right_scale:
        left_scale = right_scale
    elif not right_scale and left_scale:
        right_scale = left_scale

    left_value *= _SCALE_FACTORS.get(left_scale, Decimal("1"))
    right_value *= _SCALE_FACTORS.get(right_scale, Decimal("1"))

    canonical_declared_unit = _canonical_unit(declared_unit)
    explicit_units = {unit for unit in (left_unit, right_unit) if unit}
    if len(explicit_units) > 1:
        return None
    shared_unit = next(iter(explicit_units), canonical_declared_unit)
    if canonical_declared_unit and shared_unit and canonical_declared_unit != shared_unit:
        return None

    left_unit = left_unit or shared_unit
    right_unit = right_unit or shared_unit
    return (left_value, left_unit), (right_value, right_unit)


def _extract_numeric_ranges(
    text: str,
    declared_unit: str = "",
) -> Iterable[tuple[tuple[Decimal, str], tuple[Decimal, str]]]:
    """Yield simple two-endpoint ranges with canonical values and units."""

    normalized = unicodedata.normalize("NFKC", text or "")
    for pattern in (_BETWEEN_NUMERIC_RANGE_RE, _SIMPLE_NUMERIC_RANGE_RE):
        for match in pattern.finditer(normalized):
            canonical = _canonical_numeric_range(
                match.group("left"),
                match.group("right"),
                declared_unit,
            )
            if canonical is not None:
                yield canonical


def _extract_approximate_number_mentions(text: str) -> Iterable[Tuple[int, str]]:
    """Yield magnitude bands without inventing precision for vague amounts."""

    normalized = unicodedata.normalize("NFKC", text or "")
    for match in _ENGLISH_APPROXIMATE_NUMBER_RE.finditer(normalized):
        preceding = normalized[: match.start()].rstrip()
        if preceding and (preceding[-1].isdigit() or preceding[-1] in "$₩."):
            # ``48 billion`` is an exact amount handled by the Decimal parser,
            # not an approximate ``billions`` magnitude.
            continue
        scale = match.group("scale").lower().rstrip("s")
        exponent = _ENGLISH_APPROXIMATE_EXPONENTS[scale]
        prefix = (match.group("prefix") or "").lower()
        if prefix == "tens":
            exponent += 1
        elif prefix == "hundreds":
            exponent += 2
        yield exponent, _canonical_unit(match.group("unit") or "")

    for match in _KOREAN_APPROXIMATE_NUMBER_RE.finditer(normalized):
        yield (
            _KOREAN_APPROXIMATE_EXPONENTS[match.group("scale")],
            _canonical_unit(match.group("unit") or ""),
        )


def _number_supported_by_evidence(num_obj, evidence_text: str) -> bool:
    """Return whether one declared number is supported by its cited evidence."""

    declared_unit = _canonical_unit(num_obj.unit)

    # Range claims must match both endpoints of one evidence-backed range.
    # This prevents the old behavior from validating only the first number in
    # a multi-number raw_text while still allowing exact scale normalization.
    claim_ranges = list(_extract_numeric_ranges(num_obj.raw_text, declared_unit))
    if claim_ranges:
        evidence_ranges = list(_extract_numeric_ranges(evidence_text))
        return any(
            claim_range == evidence_range
            for claim_range in claim_ranges
            for evidence_range in evidence_ranges
        )

    approximate_claim = list(
        _extract_approximate_number_mentions(num_obj.raw_text)
    )
    if approximate_claim:
        magnitude, qual = approximate_claim[0]
        if not qual and declared_unit:
            qual = declared_unit
        return any(
            magnitude == ev_magnitude and qual == ev_qual
            for ev_magnitude, ev_qual in _extract_approximate_number_mentions(
                evidence_text
            )
        )

    parsed_claim = list(_extract_number_mentions(num_obj.raw_text))
    if len(parsed_claim) > 1:
        # Multiple exact values without a recognized range connector are
        # ambiguous in a single NormalizedNumber, so fail closed.
        return False
    if parsed_claim:
        value, qual = parsed_claim[0]
        if not qual and declared_unit:
            qual = declared_unit
    else:
        value = num_obj.normalized_value
        qual = declared_unit

    return any(
        value == evidence_value and qual == evidence_unit
        for evidence_value, evidence_unit in _extract_number_mentions(evidence_text)
    )


def _plain_decimal(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _billion_to_eok_replacement(raw_text: str, evidence_text: str) -> tuple[str, Decimal] | None:
    """Repair one unambiguous ``$N billion`` -> ``N0억 달러`` scale slip.

    This intentionally covers only a narrow mechanical localization error. It
    does not guess among evidence amounts or relax numeric verification.
    """

    claim_match = _KOREAN_EOK_USD_RE.fullmatch(
        unicodedata.normalize("NFKC", raw_text or "").strip()
    )
    if not claim_match:
        return None
    claim_coefficient = _to_decimal(claim_match.group("number"))
    if claim_coefficient is None:
        return None

    candidates: set[Decimal] = set()
    for match in _ENGLISH_BILLION_USD_RE.finditer(
        unicodedata.normalize("NFKC", evidence_text or "")
    ):
        if not match.group("currency") and not match.group("unit"):
            continue
        coefficient = _to_decimal(match.group("number"))
        if coefficient == claim_coefficient:
            candidates.add(coefficient * _SCALE_FACTORS["billion"])

    if len(candidates) != 1:
        return None
    canonical_value = candidates.pop()
    eok_value = canonical_value / _SCALE_FACTORS["억"]
    replacement = f"{_plain_decimal(eok_value)}억 달러"
    if replacement == claim_match.group(0):
        return None
    return replacement, canonical_value


def repair_source_backed_numeric_localizations(
    claims: List[Claim],
    lineage: SourceLineage,
) -> tuple[List[Claim], list[tuple[str, str, str]]]:
    """Repair only exact, source-backed billion-to-eok translation slips.

    The original list is left untouched. Every repair records
    ``(claim_id, old_text, new_text)`` for observable pipeline logging.
    """

    evidence_map = {ev.evidence_id: ev for ev in lineage.evidence_passages}
    repaired_claims: list[Claim] = []
    repairs: list[tuple[str, str, str]] = []

    for claim in claims:
        combined_evidence_text = " ".join(
            evidence_map[evidence_id].text
            for evidence_id in claim.evidence_ids
            if evidence_id in evidence_map
        )
        replacements: dict[str, tuple[str, Decimal]] = {}
        repaired_numbers = []
        for number in claim.numbers:
            repair = None
            if not _number_supported_by_evidence(number, combined_evidence_text):
                repair = _billion_to_eok_replacement(
                    number.raw_text,
                    combined_evidence_text,
                )
            if repair is None:
                repaired_numbers.append(number)
                continue

            replacement, canonical_value = repair
            replacements[number.raw_text] = (replacement, canonical_value)
            repaired_numbers.append(
                number.model_copy(
                    update={
                        "raw_text": replacement,
                        "normalized_value": canonical_value,
                        "unit": "달러",
                    }
                )
            )
            repairs.append((claim.claim_id, number.raw_text, replacement))

        if not replacements:
            repaired_claims.append(claim)
            continue

        display_title = claim.display_title
        claim_text = claim.claim_text
        for old_text, (new_text, _) in replacements.items():
            display_title = display_title.replace(old_text, new_text)
            claim_text = claim_text.replace(old_text, new_text)
        repaired_claims.append(
            claim.model_copy(
                update={
                    "display_title": display_title,
                    "claim_text": claim_text,
                    "numbers": repaired_numbers,
                    "verification_status": "pending",
                    "verification_reason": "",
                }
            )
        )

    return repaired_claims, repairs


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


def _valid_date_key(
    year: str | None,
    month: str | int,
    day: str | int | None,
) -> tuple[int | None, int, int | None] | None:
    """Return a comparable date key without inventing missing precision."""
    month_value = int(month)
    day_value = int(day) if day else None
    if not 1 <= month_value <= 12:
        return None
    if day_value is not None and not 1 <= day_value <= 31:
        return None
    return int(year) if year else None, month_value, day_value


def _extract_date_mentions(text: str) -> set[tuple[int | None, int, int | None]]:
    """Extract Korean, ISO, and unambiguous English month expressions.

    English month matching is deliberately case-sensitive.  This recognizes
    the calendar month ``May`` without treating the modal verb ``may`` as a
    date.
    """
    mentions: set[tuple[int | None, int, int | None]] = set()
    normalized = unicodedata.normalize("NFKC", text or "")

    for pattern in (_ISO_DATE_RE, _KOREAN_DATE_RE):
        for match in pattern.finditer(normalized):
            key = _valid_date_key(
                match.group("year"),
                match.group("month"),
                match.group("day"),
            )
            if key:
                mentions.add(key)

    for pattern in (_ENGLISH_MONTH_DAY_RE, _ENGLISH_DAY_MONTH_RE):
        for match in pattern.finditer(normalized):
            month = _ENGLISH_MONTHS[match.group("month").lower()]
            key = _valid_date_key(match.group("year"), month, match.group("day"))
            if key:
                mentions.add(key)

    for match in _ENGLISH_MONTH_YEAR_RE.finditer(normalized):
        month = _ENGLISH_MONTHS[match.group("month").lower()]
        key = _valid_date_key(match.group("year"), month, None)
        if key:
            mentions.add(key)

    for match in _ENGLISH_MONTH_RE.finditer(normalized):
        month = _ENGLISH_MONTHS[match.group("month").lower()]
        mentions.add((None, month, None))

    return mentions


def _date_keys_compatible(
    claim_date: tuple[int | None, int, int | None],
    evidence_date: tuple[int | None, int, int | None],
) -> bool:
    """Match only the precision asserted by the claim's raw date text."""
    claim_year, claim_month, claim_day = claim_date
    evidence_year, evidence_month, evidence_day = evidence_date
    if claim_month != evidence_month:
        return False
    if claim_year is not None and claim_year != evidence_year:
        return False
    if claim_day is not None and claim_day != evidence_day:
        return False
    return True


def _date_supported_by_evidence(
    raw_text: str,
    normalized_date: str,
    combined_evidence_text: str,
) -> bool:
    norm_evidence_text = normalize_text(combined_evidence_text)
    if normalize_text(raw_text) in norm_evidence_text:
        return True
    if normalized_date and normalized_date in combined_evidence_text:
        return True

    claim_dates = _extract_date_mentions(raw_text)
    evidence_dates = _extract_date_mentions(combined_evidence_text)
    return bool(claim_dates) and any(
        _date_keys_compatible(claim_date, evidence_date)
        for claim_date in claim_dates
        for evidence_date in evidence_dates
    )


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
                if not norm_ent or norm_ent in _GENERIC_ENTITY_TERMS:
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
                if not _number_supported_by_evidence(num_obj, combined_evidence_text):
                    raise QualityGateError("NUMBER_UNSUPPORTED", f"Number '{num_obj.raw_text}' not supported by evidence.", claim.claim_id)

            # 4. Check Dates
            for date_obj in claim.dates:
                if not _date_supported_by_evidence(
                    date_obj.raw_text,
                    date_obj.normalized_date,
                    combined_evidence_text,
                ):
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