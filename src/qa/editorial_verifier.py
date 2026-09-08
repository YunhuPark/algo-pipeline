"""Fail-closed validation for a human-requested slide revision."""
from __future__ import annotations

import re
from typing import Any

from src.qa.deterministic_verifier import (
    DeterministicVerifier,
    QualityGateError,
    parse_number_with_qualifier,
)
from src.qa.semantic_critic import run_semantic_critic
from src.schemas.card_news import Claim, NormalizedNumber, SourceLineage


_NUMBER_TOKEN = re.compile(
    r"\d[\d,]*(?:\.\d+)?\s*(?:%|퍼센트|개|명|곳|원|달러|만|억)?"
)


def _numbers(text: str) -> list[NormalizedNumber]:
    values: list[NormalizedNumber] = []
    for match in _NUMBER_TOKEN.finditer(text):
        raw = match.group(0).strip()
        value, unit = parse_number_with_qualifier(raw)
        if value is not None:
            values.append(
                NormalizedNumber(
                    raw_text=raw,
                    normalized_value=value,
                    unit=unit,
                )
            )
    return values


def validate_edited_slide(
    *,
    title: str,
    body: str,
    slide_type: str,
    source_lineage: SourceLineage,
    semantic_llm: Any = None,
) -> None:
    """Verify revised copy against the original evidence before it is saved.

    Numeric tokens are checked deterministically and the complete revised text
    is then checked by the semantic critic.  Any unavailable or inconclusive
    verifier blocks the edit.
    """

    text = f"{title.strip()}\n{body.strip()}".strip()
    if not text:
        raise QualityGateError("EDIT_TEXT_EMPTY", "Edited slide text is empty.")
    if not source_lineage.is_verified_ready:
        raise QualityGateError(
            "EDIT_LINEAGE_UNVERIFIED",
            "A verified source lineage is required to validate an edit.",
        )

    numbers = _numbers(text)
    claim = Claim(
        claim_id="editorial-revision",
        claim_text=text,
        claim_type=(
            "cta"
            if slide_type == "cta"
            else "numerical" if numbers else "factual"
        ),
        numbers=numbers,
        evidence_ids=[item.evidence_id for item in source_lineage.evidence_passages],
        source_url=source_lineage.source_url,
    )
    DeterministicVerifier.verify_claims([claim], source_lineage)
    run_semantic_critic([claim], source_lineage, llm=semantic_llm)
