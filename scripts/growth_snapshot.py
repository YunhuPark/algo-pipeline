"""Record today's follower and post numbers, then print the growth report. Read-only on the platforms.

Usage: python scripts/growth_snapshot.py            take today's snapshot and print the report
       python scripts/growth_snapshot.py --report   print the report from what is already stored (no network)
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import growth_tracker  # noqa: E402


def main(argv: list[str]) -> int:
    if "--report" not in argv:
        data = growth_tracker.snapshot()
        for note in data["notes"]:
            print(f"참고: {note}")
        print(f"오늘 기록: 계정 {len(data['accounts'])}개, 게시물 {len(data['posts'])}개")
    growth_tracker.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(growth_tracker.DB_PATH) as conn:
        print(growth_tracker.report(conn))
        by_category = growth_tracker.category_report(conn, growth_tracker.topics_by_post())
        if by_category:
            print(by_category)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
