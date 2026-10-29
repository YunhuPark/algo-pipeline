"""A card must not state as fact what its source only hedges.

Security reporting is full of qualifiers: "정황" (circumstantial signs), "추정" (presumed), "단정할 수 없다"
(cannot be concluded), "확인되지 않았다" (not confirmed). A card that drops them turns "traffic from a North
Korean IP range was seen, but it cannot be called a North Korean hack" into "North Korea hacked X". This
check compares each claim with the source sentence it is built from and refuses a claim that asserts an
attack/attribution when that sentence hedges it and the claim does not.
"""
from __future__ import annotations

import re

# Hedges in the source that must survive in the claim.
SOURCE_HEDGE_RE = re.compile(r"정황|추정|의혹|의심|단정|확인되지\s*않|확인된\s*바\s*없|밝혀지지\s*않|입증되지\s*않")
# The claim asserts something about an attack, a breach or who is behind it.
ASSERTION_RE = re.compile(r"소행|배후|해킹|공격|침투|유출|탈취|악용|침해|랜섬")
# Anything in the claim that keeps the uncertainty (or states the negation).
CLAIM_SOFTENER_RE = re.compile(
    r"정황|추정|의혹|의심|단정|확인되지|확인된\s*바|밝혀지지|입증되지|가능성|보인|알려|수\s*있|못|않|아직|아니|없"
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。])\s+|\n+")
_WORD_RE = re.compile(r"[가-힣A-Za-z0-9]{2,}")


def _stems(text: str) -> set[str]:
    """Words reduced so that "북한이" and "북한의" meet: Korean words by their first two characters (the
    particle comes after them), Latin words whole."""
    stems: set[str] = set()
    for word in _WORD_RE.findall(text or ""):
        stems.add(word[:2] if re.match(r"[가-힣]", word) else word.casefold())
    return stems


def find_dropped_hedge(claim_text: str, evidence_text: str, min_shared: int = 3) -> str | None:
    """The hedge word the claim dropped, or None. Looks only at the evidence sentence the claim is built from."""
    if not ASSERTION_RE.search(claim_text or "") or CLAIM_SOFTENER_RE.search(claim_text or ""):
        return None
    claim_stems = _stems(claim_text)
    if len(claim_stems) < min_shared:
        return None
    best_sentence, best_shared = "", 0
    for sentence in _SENTENCE_SPLIT.split(evidence_text or ""):
        shared = len(claim_stems & _stems(sentence))
        if shared > best_shared:
            best_sentence, best_shared = sentence, shared
    if best_shared < min_shared or best_shared < 0.4 * len(claim_stems):
        return None
    hedge = SOURCE_HEDGE_RE.search(best_sentence)
    return hedge.group(0) if hedge else None
