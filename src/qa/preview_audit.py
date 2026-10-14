"""Automatic audit of a finished card set, run before it is shown for approval.

The claim text is verified against the source, but everything around it (the
caption, the videos, the stock clips, the backgrounds) used to reach the
operator unchecked, and problems were only found by eye after the money was
spent. This audit runs on the rendered output folder and does two things:

* fixes what can be fixed deterministically (a caption whose first line is a
  broken fragment is replaced by one built from the verified hook);
* lists what it cannot judge (stock clips, backgrounds) so the operator knows
  exactly what to look at, instead of having to look at everything.

It makes no LLM calls: it must never add cost to a generation.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_EMOJI_AND_SYMBOLS = re.compile(r"^[^\w가-힣]+")
# "300! ..." / "AI? ..." — a short fragment ending in !/? is a broken opener
_FRAGMENT_OPENER = re.compile(r"^[\w.%$]{1,4}\s*[!?]")


def _first_text_line(caption: str) -> str:
    for line in caption.splitlines():
        if line.strip():
            return line.strip()
    return ""


def caption_issues(caption: str) -> list[str]:
    """Why the caption's opening is not fit to post (empty list = fine)."""

    issues: list[str] = []
    first = _EMOJI_AND_SYMBOLS.sub("", _first_text_line(caption))
    if len(first) < 10:
        issues.append(f"첫 줄이 너무 짧음: '{first}'")
    elif _FRAGMENT_OPENER.match(first):
        issues.append(f"첫 줄이 잘린 조각으로 시작함: '{first[:20]}'")
    if caption.count("·") < 2:
        issues.append("핵심 내용(· 항목)이 2개 미만")
    return issues


def _rebuild_opening(caption: str, hook: str) -> str:
    """Replace the first text line with the verified hook."""

    lines = caption.splitlines()
    for i, line in enumerate(lines):
        if line.strip():
            lines[i] = f"⚡ {hook}"
            break
    return "\n".join(lines)


def audit_output(out_dir: Path, script_hook: str = "") -> dict[str, Any]:
    """Audit one output folder, fix what is safe to fix, write audit.json."""

    out_dir = Path(out_dir)
    fixed: list[str] = []
    attention: list[str] = []
    checks: list[dict[str, str]] = []

    # ── caption ─────────────────────────────────────────
    caption_path = out_dir / "caption.txt"
    if caption_path.exists():
        caption = caption_path.read_text(encoding="utf-8")
        issues = caption_issues(caption)
        if issues and script_hook:
            repaired = _rebuild_opening(caption, script_hook)
            remaining = [i for i in caption_issues(repaired) if "첫 줄" in i]
            if not remaining:
                caption_path.write_text(repaired, encoding="utf-8")
                fixed.append("캡션 첫 줄을 검증된 훅으로 교체: " + "; ".join(issues))
                issues = [i for i in issues if "첫 줄" not in i]
        if issues:
            attention.append("캡션 확인 필요: " + "; ".join(issues))
        checks.append({"item": "캡션", "result": "문제 없음" if not issues else "; ".join(issues)})
    else:
        attention.append("caption.txt가 없습니다")

    # ── slides: videos and stock clips ──────────────────
    script: dict[str, Any] = {}
    script_path = out_dir / "script.json"
    if script_path.exists():
        try:
            script = json.loads(script_path.read_text(encoding="utf-8"))
        except ValueError:
            attention.append("script.json을 읽을 수 없습니다")

    mp4_numbers = {
        m.group(1) for p in out_dir.glob("card_*.mp4") if (m := re.match(r"card_(\d+)_", p.name))
    }
    for slide in script.get("slides", []):
        number = f"{int(slide.get('slide_number', 0)):02d}"
        has_mp4 = number in mp4_numbers
        if slide.get("video_id"):
            creator = slide.get("video_creator") or ""
            if has_mp4 and creator and not slide.get("video_verified"):
                attention.append(
                    f"카드 {number}: 영상의 자막 검증 기록이 없습니다 — 내용과 맞는지 직접 확인하세요"
                )
            elif has_mp4 and creator:
                checks.append({
                    "item": f"카드 {number} 영상",
                    "result": f"자막으로 내용 검증됨 · {creator} · '{str(slide.get('video_title', ''))[:40]}'",
                })
            elif has_mp4:
                attention.append(f"카드 {number}: 영상 채널명이 없어 출처 표기가 빠집니다")
            else:
                attention.append(f"카드 {number}: 영상이 지정됐지만 영상 파일이 없습니다")
        elif has_mp4:
            attention.append(
                f"카드 {number}: 스톡 영상(자막 검증 불가) — 주제와 어울리는지 직접 확인하세요"
            )

    # ── source credit ───────────────────────────────────
    note = script.get("source_note")
    if note:
        checks.append({"item": "출처 표기", "result": "카드 하단·캡션에 포함"})
    else:
        attention.append("출처 정보가 없어 카드에 출처가 표기되지 않습니다")

    # ── what cannot be judged automatically ─────────────
    attention.append("배경 이미지는 자동으로 판단할 수 없습니다 — 주제와 어울리는지 눈으로 확인하세요")

    # ── 파이프라인 전체 출력 기준(src/qa/output_standard.py) ──
    from src.qa.output_standard import blocking, check_output

    violations = []
    try:
        violations = check_output(out_dir)
    except Exception as exc:   # 점검 실패가 생성을 막으면 안 된다
        attention.append(f"출력 기준 점검 실패({type(exc).__name__}) — 직접 확인하세요")
    for v in violations:
        label = "승인 차단" if v.severity == "block" else "확인 필요"
        attention.append(f"[기준 {v.rule}] {label}: {v.message}")
    if not violations and not any(a.startswith("출력 기준") for a in attention):
        checks.append({"item": "출력 기준", "result": "모든 기준 통과"})

    report = {
        "ok": not any(not a.startswith("배경 이미지") for a in attention),
        "fixed": fixed,
        "attention": attention,
        "checks": checks,
        "violations": [
            {"rule": v.rule, "severity": v.severity, "message": v.message} for v in violations
        ],
        "blocked": bool(blocking(violations)),
    }
    try:
        (out_dir / "audit.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass
    return report


__all__ = ["audit_output", "caption_issues"]
