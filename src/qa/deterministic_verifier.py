import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import List, Dict, Tuple, Optional
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
    """Parse a string to extract canonical number and its qualifier (unit, %, etc)."""
    # Ex: "30%", "1.5 million", "3개 기업", "13"
    text = unicodedata.normalize('NFKC', text).lower()

    # Try to extract the first decimal-like pattern
    match = re.search(r'([\d]+(?:[\.,]\d+)?)', text)
    if not match:
        return None, ""

    num_str = match.group(1).replace(',', '')
    try:
        val = Decimal(num_str)
    except InvalidOperation:
        return None, ""

    # Extract qualifiers
    qualifier = ""
    if "%" in text or "퍼센트" in text:
        qualifier = "%"
    elif "개" in text:
        qualifier = "개"
    elif "명" in text:
        qualifier = "명"
    elif "곳" in text:
        qualifier = "곳"
    elif "원" in text:
        qualifier = "원"
    elif "달러" in text or "$" in text:
        qualifier = "달러"
    elif "만" in text:
        qualifier = "만"
    elif "억" in text:
        qualifier = "억"

    return val, qualifier


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

                # Check article ID / source URL mismatch
                if ev.article_id != lineage.article_id:
                    raise QualityGateError("EVIDENCE_ARTICLE_MISMATCH", f"Evidence {ev_id} is from a different article.", claim.claim_id)
                if claim.source_url and ev.source_url and claim.source_url != ev.source_url:
                    raise QualityGateError("SOURCE_URL_MISMATCH", f"Claim source URL doesn't match evidence URL.", claim.claim_id)

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
                val = num_obj.normalized_value
                qual = num_obj.unit

                found = False
                # Simple extraction of all numbers in evidence
                words = combined_evidence_text.split()
                for word in words:
                    ev_val, ev_qual = parse_number_with_qualifier(word)
                    if ev_val is not None:
                        # 3 != 13, 1.5 != 15 etc since Decimal(3) != Decimal(13)
                        if val == ev_val and qual == ev_qual:
                            found = True
                            break
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
