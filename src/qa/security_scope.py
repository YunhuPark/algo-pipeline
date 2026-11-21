"""Scope rule for security/hacking articles: they are AI news only when AI is part of the story.

Background: an AI-security story ("a ransomware group used AI", "Claude found Chrome vulnerabilities",
"an AI coding tool has a critical flaw") is AI news, while an ordinary breach ("an exchange lost 100M won
from a hot wallet") is not. A judgement made from the headline alone got this wrong once; the decision
must come from what the article actually says, so the same rule is exposed for the headline/summary
(collection time) and for the full body (when the article text is in hand).
"""
from __future__ import annotations

import re

SECURITY_RE = re.compile(
    r"해킹|해커|랜섬|침해|취약점|악성\s*코드|악성코드|유출|탈취|피싱|사이버\s*공격|제로데이|zero-?day|"
    r"CVE-\d|DDoS|백도어|침투|보안\s*사고|(?<![A-Za-z])hack|ransomware|breach|exploit|malware",
    re.IGNORECASE,
)

# AI terms that make a security story an AI story. "AI" alone must stand alone (not "MAIN", "RAIL").
AI_TERM_RE = re.compile(
    r"(?<![A-Za-z])AI(?![A-Za-z])|인공지능|생성형|LLM|머신러닝|딥러닝|딥페이크|"
    r"Claude|클로드|ChatGPT|챗GPT|GPT|Codex|코덱스|Gemini|제미나이|Llama|llama\.cpp|Mistral|미스트랄|"
    r"오픈AI|OpenAI|앤스로픽|앤트로픽|Anthropic|AI\s*에이전트|에이전틱",
    re.IGNORECASE,
)

NOT_SECURITY = "not_security"
AI_SECURITY = "ai_security"          # a security story with AI in it: in scope
PLAIN_SECURITY = "plain_security"    # a security story without AI: out of scope


def is_security_text(text: str) -> bool:
    return bool(SECURITY_RE.search(text or ""))


def ai_terms(text: str) -> set[str]:
    """The distinct AI terms found in the text, case-folded."""
    return {m.group(0).casefold() for m in AI_TERM_RE.finditer(text or "")}


def mentions_ai(text: str, min_distinct: int = 1) -> bool:
    return len(ai_terms(text)) >= min_distinct


def classify(title: str, summary: str = "", body: str = "") -> str:
    """Scope of an article that may be about security.

    The headline and summary decide when they already show AI. Otherwise the body decides, and it takes
    two different AI terms there (one stray mention, e.g. in a related-article link, does not make a
    breach story an AI story)."""
    head = f"{title} {summary}"
    if not is_security_text(head) and not is_security_text(body[:400]):
        return NOT_SECURITY
    if mentions_ai(head):
        return AI_SECURITY
    if body and mentions_ai(body, min_distinct=2):
        return AI_SECURITY
    return PLAIN_SECURITY
