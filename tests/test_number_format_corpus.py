"""A corpus of real-world number notations the claim verifier must read correctly.

Why this exists: 66% of all paid claim retries came from the number check rejecting a number that
IS in the source ("1.5 million ... human workers", "a 10-minute conversation"). Each time one
notation was fixed after it cost money. This table lists many notations at once, in both
directions (English evidence -> Korean claim and back), plus the cases that must stay rejected, so
a parser change is judged against all of them. Add a row whenever a new notation shows up.

Each row: (evidence sentence, claim raw_text, declared unit, must_be_supported)
"""
from __future__ import annotations

from types import SimpleNamespace as N

import pytest

from src.qa.claim_generator import parse_number_with_qualifier
from src.qa.deterministic_verifier import _number_supported_by_evidence as supported

SUPPORTED = [
    # ── scale words, plain ─────────────────────────────
    ("Amazon runs more than 1 million robots.", "100만 대", "count"),
    ("The company has 24 million users.", "2,400만 명", "people"),
    ("It raised $1.5 billion in funding.", "15억 달러", "usd"),
    ("The deal is worth $7.2 billion.", "72억 달러", "usd"),
    ("The market reached $48 billion.", "480억 달러", "usd"),
    ("Shipments hit 1.05 million units.", "105만 대", "count"),
    ("It cost $2 trillion over a decade.", "2조 달러", "usd"),
    ("Sales topped 3.2 billion yuan.", "32억 위안", ""),
    ("The model has 70 billion parameters.", "700억 개", "count"),
    ("The fund holds 450 thousand shares.", "45만 주", ""),
    # ── abbreviations ──────────────────────────────────
    ("Revenue was $2.4B in the quarter.", "24억 달러", "usd"),
    ("It signed 3M customers.", "300만 명", "people"),
    ("A $500M round led by Sequoia.", "5억 달러", "usd"),
    ("About 250K developers joined.", "25만 명", "people"),
    ("Raised $2.4bn this year.", "24억 달러", "usd"),
    # ── hyphenated compound modifiers ──────────────────
    ("Candidates have a 10-minute conversation with an AI client.", "10분", "minutes"),
    ("A 5-year contract was signed.", "5년", "years"),
    ("The 3-day event drew crowds.", "3일", "days"),
    ("A $5-million grant funds the lab.", "500만 달러", "usd"),
    ("The 100-page report was released.", "100페이지", ""),
    ("A 30-second ad ran during the game.", "30초", "seconds"),
    # ── people nouns, with modifiers in between ────────
    ("Amazon employs more than 1.5 million full-time and part-time human workers.", "150만 명", "people"),
    ("The firm has 12,000 employees worldwide.", "1만 2,000명", "people"),
    ("It serves 200 million active users.", "2억 명", "people"),
    ("About 40 million customers use the app weekly.", "4,000만 명", "people"),
    ("It hired 300 new engineers.", "300명", "people"),
    # ── percent forms ──────────────────────────────────
    ("Revenue grew 12 percent.", "12%", "percent"),
    ("Prices rose 3.5%.", "3.5%", "percent"),
    ("Usage is up 40 per cent year over year.", "40%", "percent"),
    ("The scenarios cover 80% of cases.", "80%", "percent"),
    ("Margins fell by 2.5 percentage points.", "2.5%p", ""),
    # ── multiples ──────────────────────────────────────
    ("The chip is 3x faster than its predecessor.", "3배", "times"),
    ("Output grew 1.5 times.", "1.5배", "times"),
    ("Valuation tripled to $35 billion.", "350억 달러", "usd"),
    # ── time units ─────────────────────────────────────
    ("The call lasted 10 minutes.", "10분", "minutes"),
    ("Training took 2 hours.", "2시간", "hours"),
    ("The pilot ran for 6 months.", "6개월", "months"),
    ("The program started 3 years ago.", "3년", "years"),
    ("Delivery takes 2 days.", "2일", "days"),
    # ── qualifiers ─────────────────────────────────────
    ("Nearly 1 million robots work in its warehouses.", "100만 대", "count"),
    ("Over $2 billion has been invested.", "20억 달러", "usd"),
    ("At least 5 offices will test the tool.", "5곳", ""),
    ("Roughly 20% of staff were affected.", "20%", "percent"),
    ("The company employs about 1.5 million people.", "150만 명", "people"),
    # ── Korean evidence -> Korean claim ────────────────
    ("아마존은 150만 명 이상의 직원을 고용하고 있다.", "150만 명", "people"),
    ("매출은 3조 2,000억 원을 기록했다.", "3조 2,000억 원", "krw"),
    ("점유율이 30% 늘었다.", "30%", "percent"),
    ("전년 대비 1.5배 증가했다.", "1.5배", "times"),
    ("서비스 이용자가 2,400만 명에 달한다.", "2,400만 명", "people"),
    ("프로그램은 10분간 진행된다.", "10분", "minutes"),
    # ── Korean evidence -> English-looking claim ───────
    ("이용자가 2,400만 명이다.", "24 million", ""),
    ("투자금은 15억 달러다.", "$1.5 billion", ""),
]

# must stay rejected: wrong value, wrong scale, or a different thing than the claim says
REJECTED = [
    ("Amazon operates 1 million robots.", "150만 명", "people"),           # different number
    ("Amazon has deployed more than 1.5 million robots.", "150만 명", "people"),  # robots, not people
    ("It raised $1.5 billion.", "1.5억 달러", "usd"),                       # scale slip: 1.5억 != 15억
    ("It raised $1.5 billion.", "150억 달러", "usd"),
    ("Revenue grew 12 percent.", "21%", "percent"),
    ("The call lasted 10 minutes.", "10시간", "hours"),                      # wrong unit
    ("The pilot ran for 6 months.", "6년", "years"),
    ("Prices rose 3.5%.", "35%", "percent"),                                # moved decimal
    ("The firm has 12,000 employees.", "120,000명", "people"),
    ("It signed 3M customers.", "30만 명", "people"),
]


def _num(raw: str, unit: str) -> N:
    value, _ = parse_number_with_qualifier(raw)
    return N(raw_text=raw, unit=unit, normalized_value=value)


@pytest.mark.parametrize("evidence,raw,unit", SUPPORTED, ids=[f"{r[1]}<-{r[0][:38]}" for r in SUPPORTED])
def test_number_present_in_the_source_is_supported(evidence, raw, unit):
    assert supported(_num(raw, unit), evidence), f"{raw!r} should be supported by: {evidence}"


@pytest.mark.parametrize("evidence,raw,unit", REJECTED, ids=[f"{r[1]}<-{r[0][:38]}" for r in REJECTED])
def test_number_that_is_not_in_the_source_stays_rejected(evidence, raw, unit):
    assert not supported(_num(raw, unit), evidence), f"{raw!r} must NOT be supported by: {evidence}"
