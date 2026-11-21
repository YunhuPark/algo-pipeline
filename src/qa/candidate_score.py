"""Rank queued candidates by how likely they are to turn into a good card set.

No LLM calls: everything is read from what the queue row already stores (article text, publish
date, source) plus one optional search for related videos (a search-API credit, no model cost).
The score only ORDERS candidates; a low score never admits an article that failed the freshness /
suitability checks, and nothing is padded.

Weights (100): freshness 20, article body 15, concrete figures 25, named subjects 15,
headline specificity 5, source 10, video candidates 10.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

_ROOT = Path(__file__).resolve().parents[2]
_VIDEO_CACHE = _ROOT / "data" / "candidate_video_probe.json"

_IMAGE_MD = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK_MD = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_FIGURE = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?\s*(?:%|퍼센트|percent|[A-Za-z가-힣]{1,12})?")
_YEAR = re.compile(r"^(?:19|20)\d{2}(?:년)?$")
_LATIN_NAME = re.compile(r"\b[A-Z][A-Za-z0-9]{2,}\b")
_COMMON_CAPS = {
    "The", "This", "That", "These", "Those", "With", "For", "And", "But", "While", "When", "What",
    "How", "Why", "Here", "There", "TL", "DR", "According", "However", "Also", "After", "Before",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "January", "February", "March", "April", "June", "July", "August", "September", "October",
    "November", "December",
}
_TRUSTED_SOURCES = (
    "aitimes", "thedecoder", "the-decoder", "techcrunch", "theverge", "venturebeat", "wired",
    "arstechnica", "reuters", "bloomberg", "zdnet", "etnews", "zdnet.co.kr", "openai.com",
    "anthropic.com", "blog.google", "deepmind", "nvidia.com", "microsoft.com", "ibm.com",
    "huggingface.co", "mit.edu", "technologyreview",
)


@dataclass
class CandidateScore:
    total: int
    parts: dict[str, int]
    reasons: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return " · ".join(self.reasons)


def clean_text(text: str) -> str:
    text = _IMAGE_MD.sub(" ", text or "")
    text = _LINK_MD.sub(r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _figures(text: str) -> int:
    seen: set[str] = set()
    for m in _FIGURE.finditer(text):
        raw = m.group(0).strip()
        num = re.match(r"\d[\d,]*(?:\.\d+)?", raw)
        if not num or _YEAR.match(raw.replace(" ", "")) or _YEAR.match(num.group(0)):
            continue
        seen.add(raw.lower())
    return len(seen)


def _names(text: str) -> int:
    return len({w for w in _LATIN_NAME.findall(text) if w not in _COMMON_CAPS})


def score_candidate(
    meta: dict,
    *,
    now: datetime | None = None,
    video_count: int | None = None,
) -> CandidateScore:
    """Score one queue row's metadata dict. ``video_count`` None means 'not probed'."""
    now = now or datetime.now(timezone.utc)
    evidence = (meta.get("evidence") or [{}])[0] or {}
    body = clean_text(evidence.get("content") or meta.get("context") or "")
    headline = str(meta.get("topic") or meta.get("source_title") or "")
    source = f"{evidence.get('source') or ''} {meta.get('source_url') or ''}".lower()
    parts: dict[str, int] = {}
    reasons: list[str] = []

    published = _parse_dt(evidence.get("published_at") or "")
    if published is None:
        parts["freshness"] = 0
        reasons.append("발행일 불명")
    else:
        age = (now - published).total_seconds() / 86400
        parts["freshness"] = 20 if age <= 1.5 else 12 if age <= 2.5 else 0
        reasons.append(f"{max(int(age), 0)}일 전 기사")

    parts["body"] = min(15, len(body) // 200)
    reasons.append(f"본문 {len(body):,}자")

    n_fig = _figures(body)
    parts["figures"] = min(25, 5 * n_fig)
    reasons.append(f"수치 {n_fig}개")

    n_names = _names(body)
    parts["subjects"] = min(15, 3 * n_names)
    reasons.append(f"고유명사 {n_names}개")

    parts["headline"] = 5 if re.search(r"\d", headline) else 0

    parts["source"] = 10 if any(s in source for s in _TRUSTED_SOURCES) else 4 if source.strip() else 0
    if parts["source"] == 10:
        reasons.append("신뢰 출처")

    if video_count is None:
        parts["video"] = 0
    else:
        parts["video"] = min(10, 5 * video_count)
        reasons.append(f"영상 후보 {video_count}개(미검증)")

    return CandidateScore(sum(parts.values()), parts, reasons)


# ── optional video probe (search credits only, cached per article URL) ───────────────────────

def _load_cache() -> dict:
    try:
        return json.loads(_VIDEO_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def probe_video_count(url: str, headline: str, search: Callable | None = None) -> int | None:
    """How many related YouTube videos exist for the headline; None when it cannot be probed."""
    cache = _load_cache()
    if url in cache:
        return cache[url]
    try:
        if search is None:
            from src.agents.youtube_fetcher import _search_youtube as search
        from src.qa.topic_source_guard import topic_anchors_present

        found = search(headline, n=5, days=30)
        count = sum(1 for v in found if topic_anchors_present(headline, f"{v.title} {v.snippet or ''}"))
    except Exception:
        return None
    cache[url] = count
    try:
        _VIDEO_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _VIDEO_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return count


def score_row(row, *, probe_video: bool = True) -> CandidateScore | None:
    """Score a queue table row (sqlite Row/dict); None if its metadata cannot be read."""
    try:
        meta = json.loads(row["metadata_json"] or "{}")
    except (TypeError, ValueError, KeyError):
        return None
    if not isinstance(meta, dict) or not meta:
        return None
    videos = None
    if probe_video:
        videos = probe_video_count(str(meta.get("source_url") or ""), str(meta.get("topic") or ""))
    return score_candidate(meta, video_count=videos)


def best_queue_id(rows) -> int | None:
    """Highest-scoring row among selectable ones (pending/ready, no failure code); ties -> lowest id."""
    best: tuple[int, int] | None = None
    for row in rows:
        if row["status"] not in ("pending", "ready") or row["publish_error_code"]:
            continue
        score = score_row(row)
        if score is None:
            continue
        key = (score.total, -int(row["id"]))
        if best is None or key > best:
            best = key
    return -best[1] if best else None
