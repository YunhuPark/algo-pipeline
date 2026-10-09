"""Cheap, deterministic screen for topics that cannot produce a verified card.

Generation spends up to nine claim/editorial attempts (several LLM calls each)
on a queue item before giving up. Two kinds of item can never pass that gate no
matter how often it retries, and both are visible without calling a model:

* the topic is so generic ("2026년 AI 트렌드 전망") that it names no concrete
  subject, so no claim can "directly explain the requested topic"
  (EDITORIAL_TOPIC_MISMATCH);
* the source only *promises* the answer ("in the next episode we'll look at why
  X failed") while the topic asks for that answer, so there is no evidence for
  it (CLAIM_INSUFFICIENT_EVIDENCE).

Screening these before generation saves the retry budget and keeps them out of
the queue. The checks are intentionally conservative: they only block on clear
signals, never loosen the downstream quality gate, and never invent evidence.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from src.qa.topic_source_guard import _topic_anchor_groups

# Below this, there is not enough article text to support several distinct,
# individually cited claims. Real queued articles in production are >= ~1,300.
MIN_SOURCE_CHARS = 500

_TEASER_RE = re.compile(
    r"in the next (?:episode|part|post|video|installment|issue)"
    r"|next (?:episode|week's episode)"
    r"|stay tuned"
    r"|to be continued"
    r"|다음\s*(?:에피소드|편|회|시간|글|호)에서"
    r"|다음\s*(?:에피소드|편|회)\s*(?:예고|에서)",
    re.IGNORECASE,
)

# A teaser only matters when the topic asks for the answer it withholds.
_ANSWER_SEEKING_RE = re.compile(
    r"원인|이유|비결|비밀|방법|어떻게|왜|\bwhy\b|\bhow\b|\breasons?\b|\bcauses?\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SourceSuitability:
    ok: bool
    code: str = ""
    detail: str = ""

    @property
    def error_code(self) -> str:
        """Queue-safe error code, e.g. SOURCE_UNSUITABLE_TOPIC_TOO_GENERIC."""
        return f"SOURCE_UNSUITABLE_{self.code}" if self.code else ""


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def check_source_suitability(
    topic: str,
    source_text: str,
    source_title: str = "",
    *,
    check_length: bool = True,
) -> SourceSuitability:
    """Return ok=False with a reason only when generation is bound to fail.

    check_length=False skips the minimum-length rule. Use it where
    ``source_text`` is not guaranteed to be the whole evidence (a queue row's
    ``context`` column can be shorter than the evidence passages in its
    metadata); the topic and teaser rules do not depend on length.
    """

    topic = _norm(topic).strip()
    body = _norm(source_text).strip()

    if not _topic_anchor_groups(topic):
        return SourceSuitability(
            False,
            "TOPIC_TOO_GENERIC",
            "주제에 구체적인 대상(기업·제품·사건 이름)이 없어 근거 있는 카드를 만들 수 없습니다.",
        )

    if check_length and len(body) < MIN_SOURCE_CHARS:
        return SourceSuitability(
            False,
            "SOURCE_TOO_SHORT",
            f"원문이 {len(body)}자로 너무 짧습니다 (최소 {MIN_SOURCE_CHARS}자).",
        )

    if _ANSWER_SEEKING_RE.search(topic):
        teaser = _TEASER_RE.search(f"{_norm(source_title)}\n{body}")
        if teaser:
            return SourceSuitability(
                False,
                "TEASER_ONLY",
                "원문이 답을 다음 편으로 미루고 있는데('"
                f"{teaser.group(0)}'), 주제는 그 답을 묻고 있습니다.",
            )

    return SourceSuitability(True)
