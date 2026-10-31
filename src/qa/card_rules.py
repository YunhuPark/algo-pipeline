"""Deterministic card and caption rules found by reviewing real sets (no model calls, no cost).

Each rule exists because a finished set needed a manual fix that the pipeline should have made itself:
  * the cover led with a secondary figure (a plan price) instead of the headline's ("사용량 최대 2배"),
  * the caption's key lines carried no figures even though the cards were built on them,
  * a closing-card chip was labelled with another card's title,
  * the closing card asked a question with no answer ("이 모델의 특징은").
Fixing these before the judge also spares retries, and a retry is what costs money.
"""
from __future__ import annotations

import re

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_YEAR = re.compile(r"^(?:19|20)\d{2}$")
_DEFAULT_CTA_TITLE = "핵심만 다시 보면"
_GENERIC_LABELS = {"핵심 수치", "근거 수치", "수치", "핵심"}


def figure_digits(text: str) -> set[str]:
    """Numbers in the text as plain digit strings; years are left out (a year is not a headline figure)."""
    found = {m.group(0).replace(",", "") for m in _NUM.finditer(text or "")}
    return {n for n in found if not _YEAR.match(n)}


# ── 1. the cover leads with the headline's figure ──────────────────────────────────────

def cover_has_headline_figure(cover_title: str, topic: str) -> bool:
    """True when the headline has no figure, or the cover shows at least one of the headline's figures."""
    wanted = figure_digits(topic)
    return not wanted or bool(wanted & figure_digits(cover_title))


def headline_figure_phrase(topic: str, limit: int = 22) -> str:
    """A short cover phrase cut from the headline around its first figure ("프로·기업용 AI 사용량 최대 2배 확대").
    Empty when the headline has no figure or no short phrase can be cut."""
    wanted = figure_digits(topic)
    if not wanted:
        return ""
    for segment in re.split(r"\s*[,，]\s*|\s+[—–-]\s+|\s*…\s*", topic):
        segment = segment.strip()
        if not (figure_digits(segment) & wanted):
            continue
        if len(segment) <= limit:
            return segment
        words = segment.split()
        idx = next((i for i, w in enumerate(words) if figure_digits(w) & wanted), None)
        if idx is None:
            continue
        lo, hi = max(0, idx - 3), min(len(words), idx + 2)      # up to the word after the figure
        while lo <= idx < hi:
            candidate = " ".join(words[lo:hi])
            if len(candidate) <= limit:
                return _complete(words, lo, hi, limit)
            if idx - lo > hi - idx - 1:
                lo += 1
            else:
                hi -= 1
    return ""


_DANGLING_WORD = re.compile(r"[은는이가을를의도만과와]$")


def _complete(words: list[str], lo: int, hi: int, limit: int) -> str:
    """Grow the phrase to the right while it still fits, so it ends on a whole noun phrase
    ("비용 효율 20% 높인" -> "비용 효율 20% 높인 차세대 보안 모델"), and never ends on a dangling particle."""
    while hi < len(words) and not words[hi].startswith(("'", '"', "‘", "“")):
        if len(" ".join(words[lo:hi + 1])) > limit:
            break
        hi += 1
    while hi - lo > 1 and _DANGLING_WORD.search(words[hi - 1]):
        hi -= 1
    return " ".join(words[lo:hi])


# ── 4. a closing-card title must not be an unanswered question ─────────────────────────

_GENERIC_QUESTION = re.compile(r"어떻게\s*(생각|보|느)|여러분|당신|독자|의견")
_DANGLING_END = re.compile(r"[은는이가을를의도만과와]$")


def clean_cta_title(title: str, fallback: str = _DEFAULT_CTA_TITLE) -> str:
    """Replace a generic opinion question or a title that stops mid-sentence ("이 모델의 특징은")."""
    text = (title or "").strip()
    if not text or _GENERIC_QUESTION.search(text) or _DANGLING_END.search(text):
        return fallback
    return text


# ── 3. chip labels say what the figure is ──────────────────────────────────────────────

def chip_label(title: str, value: str, labels: list[str], used: list[str], limit: int = 10) -> str:
    """Label for a closing-card figure chip: the card's headline minus the figure when it contains it, else the
    card's own stat label (unless generic or already used by another chip), else the headline."""
    if value and value in title:
        rest = re.sub(r"\s+", " ", title.replace(value, " ")).strip()
        if len(rest) >= 2:
            return rest[:limit].rstrip()
    for label in labels or []:
        label = (label or "").strip()
        if len(label) >= 2 and label not in _GENERIC_LABELS and label not in used:
            return label[:limit].rstrip()
    return title[:limit].rstrip()


# ── 2. the caption's key lines carry the cards' figures ────────────────────────────────

_CASUAL_ENDINGS = [
    (re.compile(r"했습니다$"), "했어"), (re.compile(r"됐습니다$"), "됐어"), (re.compile(r"였습니다$"), "였어"),
    (re.compile(r"렸습니다$"), "렸어"), (re.compile(r"습니다$"), "어"), (re.compile(r"입니다$"), "이야"),
    (re.compile(r"됩니다$"), "돼"), (re.compile(r"합니다$"), "해"),
    (re.compile(r"했다$"), "했어"), (re.compile(r"됐다$"), "됐어"), (re.compile(r"렸다$"), "렸어"),
    (re.compile(r"된다$"), "돼"), (re.compile(r"한다$"), "해"), (re.compile(r"이다$"), "이야"),
]


def casual(sentence: str) -> str:
    """The caption's friendly ending for a card sentence ("…늘렸습니다" -> "…늘렸어")."""
    text = re.sub(r"[.\s]+$", "", (sentence or "").strip())
    for pattern, replacement in _CASUAL_ENDINGS:
        if pattern.search(text):
            return pattern.sub(replacement, text)
    return text


def _shorten(text: str, limit: int = 70) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut or text[:limit]


def _bullet_lines(slides: list[dict]) -> list[str]:
    """Key lines from the content cards: one per distinct figure first, then fill up to three or four."""
    lines: list[str] = []
    seen_figures: set[str] = set()
    for s in slides:
        sentence = _shorten(casual(str(s.get("body") or "").replace("\n", " ")))
        if not sentence:
            continue
        values = s.get("visual_values") or []
        figure = values[0] if values and re.search(r"\d", values[0]) else ""
        if figure and figure in seen_figures:
            continue
        if figure or len(lines) < 3:
            lines.append(sentence)
            if figure:
                seen_figures.add(figure)
        if len(lines) == 4:
            break
    return lines


def repair_caption_figures(caption: str, script: dict) -> tuple[str, str]:
    """Make the caption's opening and key lines carry the cards' figures.

    Returns (caption, note); note is empty when nothing had to change. The text comes from the cards'
    own verified sentences, so nothing new is claimed."""
    slides = script.get("slides") or []
    content = [s for s in slides if s.get("slide_type") == "content"]
    cover = next((s for s in slides if s.get("slide_type") == "cover"), {})
    body_part = caption.split("출처:")[0]
    lines = caption.split("\n")
    notes: list[str] = []

    figures = []
    for s in content:
        values = s.get("visual_values") or []
        if values and re.search(r"\d", values[0]) and values[0] not in figures:
            figures.append(values[0])
    bullet_idx = [i for i, line in enumerate(lines) if line.strip().startswith("·")]
    if figures and bullet_idx and any(not (figure_digits(f) & figure_digits(body_part)) for f in figures):
        new = [f"· {line}  " for line in _bullet_lines(content)]
        if new:
            first, last = bullet_idx[0], bullet_idx[-1]
            lines[first:last + 1] = new
            notes.append("캡션 핵심 줄에 카드의 수치를 넣음")

    cover_title = str(cover.get("title") or "")
    first_text = next((i for i, line in enumerate(lines) if line.strip()), None)
    if first_text is not None and figure_digits(cover_title) and not (figure_digits(cover_title) & figure_digits(lines[first_text])):
        lines[first_text] = f"⚡ {cover_title}"
        notes.append("캡션 첫 줄에 표지의 수치를 넣음")
    return "\n".join(lines), "; ".join(notes)
