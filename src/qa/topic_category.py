"""Topic categories for AI stories that readers care about (rule-based, no model call).

The categories mark an AI story's public-interest angle: regulation/policy, money, jobs, environment and
power, security and privacy, education, health. They are shown to the topic selector as a soft hint and
attached to published posts in the growth report, so which angle earns reach is measured, not assumed.
"""
from __future__ import annotations

import re

# (category, pattern) in priority order; a story can have several.
CATEGORIES: list[tuple[str, re.Pattern]] = [
    ("규제·정책", re.compile(r"규제|법안|입법|법원|소송|판결|정책|정부|국회|과징금|가이드라인|표준|인증|"
                           r"regulat|policy|lawsuit|antitrust|\bEU\b|\blaw\b|congress", re.IGNORECASE)),
    ("투자·돈", re.compile(r"투자|기업가치|IPO|상장|인수|합병|유치|매출|수익|가격|요금|비용|달러|억원|조원|"
                          r"valuation|funding|acquir|revenue|price|billion", re.IGNORECASE)),
    ("일자리", re.compile(r"일자리|고용|채용|해고|감원|인력|노동|직원|직무|"
                         r"\bjobs?\b|layoff|hiring|workforce|employ", re.IGNORECASE)),
    ("환경·전력", re.compile(r"전력|에너지|탄소|친환경|ESG|데이터\s*센터|냉각|발전소|원전|"
                           r"energy|power|carbon|climate|data\s*center|emission", re.IGNORECASE)),
    ("보안·개인정보", re.compile(r"해킹|해커|랜섬|취약점|악성|유출|개인정보|프라이버시|딥페이크|사이버|보안|"
                             r"hack|ransomware|vulnerab|breach|privacy|deepfake|security", re.IGNORECASE)),
    ("교육·의료", re.compile(r"교육|학생|대학|학교|의료|병원|신약|진단|환자|임상|"
                           r"education|student|medical|clinical|drug|health", re.IGNORECASE)),
]

GENERAL = "일반"


def categorize(text: str, limit: int = 2) -> list[str]:
    """Up to `limit` categories whose keywords appear in the text, in priority order; ["일반"] when none."""
    found = [name for name, pattern in CATEGORIES if pattern.search(text or "")]
    return found[:limit] or [GENERAL]


def primary(text: str) -> str:
    return categorize(text, limit=1)[0]
