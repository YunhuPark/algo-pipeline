"""Render a 운영일지 episode as a card set for review. Nothing is posted and no AI is called.

Usage: python scripts/make_devlog.py docs/devlog/ep01.json

Before drawing, the numbers that can be checked locally are compared with the live databases and the
script stops if they disagree, so an episode can never carry a stale figure.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.agents.devlog_cards import render_devlog  # noqa: E402
from src.qa.output_standard import check_output  # noqa: E402


def _live_retries() -> tuple[int, int]:
    with sqlite3.connect(ROOT / "data" / "tracking.db") as conn:
        total = conn.execute("select count(*) from retry_events").fetchone()[0]
        numbers = conn.execute("select count(*) from retry_events where error_code='CLAIM_NUMBER_UNSUPPORTED'").fetchone()[0]
    return total, numbers


def _stated_numbers(episode: dict) -> list[str]:
    return [s["body"] + " " + " ".join(s.get("visual_values", [])) for s in episode["slides"]]


def verify(episode: dict) -> list[str]:
    """Problems found when the episode's checkable figures are compared with live data."""
    total, numbers = _live_retries()
    text = " ".join(_stated_numbers(episode)) + " " + " ".join(episode["caption_bullets"])
    problems = []
    if f"{total}회" not in text:
        problems.append(f"재시도 전체 횟수가 원고({'202'})와 다릅니다: 현재 {total}회")
    if f"{numbers}회" not in text:
        problems.append(f"숫자 재시도 횟수가 원고와 다릅니다: 현재 {numbers}회")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("Usage: python scripts/make_devlog.py <episode.json>", file=sys.stderr)
        return 2
    episode = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    problems = verify(episode)
    if problems:
        print("원고의 수치가 최신이 아닙니다:\n- " + "\n- ".join(problems), file=sys.stderr)
        return 1
    folder = render_devlog(episode, ROOT / "output")
    violations = check_output(folder)
    print(folder)
    for v in violations:
        print(f"  [{v.severity}] {v.rule}: {v.message}")
    return 1 if any(v.severity == "block" for v in violations) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
