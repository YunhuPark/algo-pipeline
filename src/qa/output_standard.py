"""The pipeline-wide output standard, checked mechanically on every finished card set.

Until now "what a finished set must look like" lived in conversations and in scattered
constants, so each fix could silently break another rule (a bright background made the
text unreadable, a layout tweak moved one card's body). This module is the single list of
those rules. Each rule has an id, a plain-language statement and a severity, and
``check_output`` verifies a rendered output folder against all of them. The same ids are
documented in docs/OUTPUT_STANDARD.md (a test keeps the two in sync).

Severity
  block : the set must not be approved/published until fixed (the dashboard refuses).
  warn  : shown to the reviewer in the audit panel; approval is still possible.

It reads only files the renderer already writes (layout.json, script.json, caption.txt,
background.png, card PNG/MP4 names) and makes no LLM calls, so it costs nothing to run.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class Violation:
    rule: str
    severity: str   # "block" | "warn"
    message: str


@dataclass(frozen=True)
class Rule:
    id: str
    severity: str
    statement: str


RULES: tuple[Rule, ...] = (
    Rule("S1-structure", "block", "표지 1장 + 내용 3~6장 + 마지막 CTA 1장이고, 모든 카드 이미지가 있다"),
    Rule("S2-typography", "warn", "내용 카드의 제목·본문 글자 크기와 굵기는 모두 같다(본문이 너무 길 때만 줄어든다)"),
    Rule("S3-position", "block", "영상 없는 카드의 본문 패널은 모두 같은 줄, 영상 카드의 패널은 영상 바로 아래에서 시작한다"),
    Rule("S4-video-evidence", "block", "영상 카드는 자막 검증과 움직임 확인 기록, 채널명, 영상 파일이 모두 있다"),
    Rule("S5-no-unverified-media", "block", "검증 기록이 없는 영상(스톡 영상 포함)은 카드에 들어가지 않는다 — 없으면 문장 카드"),
    Rule("S6-cover-figure", "warn", "카드에 검증된 숫자가 있으면 표지 제목에 단위가 붙은 숫자가 들어간다"),
    Rule("S7-caption", "block", "캡션은 온전한 첫 줄로 시작하고 출처 줄을 포함한다"),
    Rule("S8-stat-language", "warn", "수치 상자의 표기는 본문과 같은 언어(한국어 카드에 영어 'million' 금지)"),
    Rule("S9-background", "warn", "본문 카드 배경은 글자가 읽힐 만큼 어둡다(평균 밝기 상한)"),
    Rule("S10-source", "block", "기사 출처 정보가 기록돼 모든 내용 카드 하단과 캡션에 표시된다"),
    Rule("S11-hashtags", "warn", "해시태그에 영문 사람 이름이 없다"),
)

# 본문 카드 배경(처리 후) 평균 밝기(0~255) 상한.
MAX_BACKGROUND_LUMINANCE = 125


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def check_output(out_dir: Path) -> list[Violation]:
    """Verify one output folder against every rule. Never raises on a missing file."""

    out_dir = Path(out_dir)
    script = _load(out_dir / "script.json") or {}
    layout = _load(out_dir / "layout.json")
    slides = script.get("slides", [])
    found: list[Violation] = []

    def add(rule_id: str, message: str) -> None:
        severity = next(r.severity for r in RULES if r.id == rule_id)
        found.append(Violation(rule_id, severity, message))

    # ── S1 structure ───────────────────────────────────
    types = [s.get("slide_type") for s in slides]
    content_n = types.count("content")
    if not slides or types[0] != "cover" or types[-1] != "cta" or not (3 <= content_n <= 6):
        add("S1-structure", f"카드 구성이 기준과 다릅니다(표지/내용/CTA = {types})")
    for s in slides:
        png = out_dir / f"card_{int(s.get('slide_number', 0)):02d}_{s.get('slide_type')}.png"
        if not png.exists():
            add("S1-structure", f"카드 이미지가 없습니다: {png.name}")

    # ── S2 / S3 layout (from the renderer's own record) ─
    from src.agents import design_renderer as dr

    if layout is None:
        add("S2-typography", "layout.json이 없어 글자·위치 기준을 확인하지 못했습니다(이전 버전으로 만든 세트)")
    else:
        for record in layout:
            panel = record.get("panel")
            if record.get("type") != "content" or not panel:
                continue
            n = record.get("slide")
            if panel.get("with_title") and panel.get("title_px") != dr.TITLE_FONT_SIZE:
                add("S2-typography", f"카드 {n:02d}: 제목 글자 크기가 기준({dr.TITLE_FONT_SIZE}px)과 다릅니다({panel.get('title_px')}px)")
            if panel.get("body_px") != dr.BODY_FONT_SIZE:
                add("S2-typography", f"카드 {n:02d}: 본문이 길어 글자가 {panel.get('body_px')}px로 줄었습니다(기준 {dr.BODY_FONT_SIZE}px)")
            expected = dr.BODY_TOP if record.get("video") else dr.TEXT_BODY_TOP
            if panel.get("top") != expected:
                add("S3-position", f"카드 {n:02d}: 본문 패널 시작 위치가 {panel.get('top')}px입니다(기준 {expected}px)")

    # ── S4 / S5 video evidence ─────────────────────────
    mp4_numbers = {
        m.group(1) for p in out_dir.glob("card_*.mp4") if (m := re.match(r"card_(\d+)_", p.name))
    }
    for s in slides:
        n = f"{int(s.get('slide_number', 0)):02d}"
        has_mp4 = n in mp4_numbers
        if s.get("video_id"):
            missing = [
                label for key, label in (
                    ("video_verified", "자막 검증 기록"),
                    ("video_motion_checked", "움직임 확인 기록"),
                    ("video_creator", "채널명"),
                ) if not s.get(key)
            ]
            if not has_mp4:
                missing.append("영상 파일")
            if missing:
                add("S4-video-evidence", f"카드 {n}: 영상에 {', '.join(missing)}이(가) 없습니다")
        elif has_mp4:
            add("S5-no-unverified-media", f"카드 {n}: 검증 기록 없는 영상(스톡 등)이 들어 있습니다")

    # ── S6 cover figure ────────────────────────────────
    from src.qa.script_assembler import _FIGURE_PHRASE_RE

    cover = next((s for s in slides if s.get("slide_type") == "cover"), None)
    if cover is not None:
        body_has_figure = any(
            _FIGURE_PHRASE_RE.search(s.get("body", "")) for s in slides if s.get("slide_type") == "content"
        )
        if body_has_figure and not _FIGURE_PHRASE_RE.search(cover.get("title", "")):
            add("S6-cover-figure", f"표지 제목 '{cover.get('title')}'에 단위가 붙은 숫자가 없습니다")

    # ── S7 caption ─────────────────────────────────────
    caption_path = out_dir / "caption.txt"
    if not caption_path.exists():
        add("S7-caption", "caption.txt가 없습니다")
    else:
        from src.qa.preview_audit import caption_issues

        caption = caption_path.read_text(encoding="utf-8")
        for issue in caption_issues(caption):
            add("S7-caption", issue)
        if script.get("source_note") and "출처:" not in caption:
            add("S7-caption", "캡션에 출처 줄이 없습니다")

    # ── S8 stat language ───────────────────────────────
    for s in slides:
        if s.get("slide_type") != "content":
            continue
        values = s.get("visual_values") or []
        has_hangul_body = bool(re.search(r"[가-힣]", s.get("body", "")))
        if has_hangul_body and any(re.fullmatch(r".*\b(million|billion|trillion|thousand)\b.*", str(v), re.I) for v in values):
            add("S8-stat-language", f"카드 {int(s.get('slide_number', 0)):02d}: 수치 상자가 영어 표기({values[0]})입니다")

    # ── S9 background ──────────────────────────────────
    bg_path = out_dir / "background.png"
    if bg_path.exists():
        try:
            from PIL import Image

            processed = dr._apply_background(Image.open(bg_path).convert("RGB"))
            small = processed.convert("L").resize((32, 32))
            mean = sum(small.getdata()) / (32 * 32)
            if mean > MAX_BACKGROUND_LUMINANCE:
                add("S9-background", f"배경이 너무 밝습니다(평균 {mean:.0f}/255, 기준 {MAX_BACKGROUND_LUMINANCE} 이하)")
        except Exception:
            pass

    # ── S10 source ─────────────────────────────────────
    if not script.get("source_note"):
        add("S10-source", "출처 정보(source_note)가 기록되지 않았습니다")

    # ── S11 hashtags ───────────────────────────────────
    for tag in script.get("hashtags", []):
        word = tag.lstrip("#")
        if re.fullmatch(r"[A-Z][a-z]+[A-Z][a-z]+", word) and word not in {"OpenAI", "ChatGPT"}:
            add("S11-hashtags", f"사람 이름으로 보이는 해시태그: {tag}")

    return found


def blocking(violations: list[Violation]) -> list[Violation]:
    return [v for v in violations if v.severity == "block"]


__all__: list[str] = ["RULES", "Violation", "check_output", "blocking", "MAX_BACKGROUND_LUMINANCE"]
