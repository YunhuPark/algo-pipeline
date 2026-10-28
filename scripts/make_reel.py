"""Build a Reel (9:16 video) from a finished card set. Nothing is posted.

Usage: python scripts/make_reel.py <queue_id>      (uses the queue row's card folder)
       python scripts/make_reel.py --dir <folder>   (any card folder)
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.agents.reel_builder import build_reel, reel_issues  # noqa: E402


def _folder_for(arg: list[str]) -> Path | None:
    if len(arg) == 2 and arg[0] == "--dir":
        return Path(arg[1])
    if len(arg) == 1 and arg[0].isdigit():
        with sqlite3.connect(ROOT / "data" / "algo.db") as conn:
            row = conn.execute("select image_dir from queue where id=?", (int(arg[0]),)).fetchone()
        return Path(row[0]) if row and row[0] else None
    return None


def main(argv: list[str]) -> int:
    folder = _folder_for(argv)
    if folder is None or not folder.is_dir():
        print("Usage: python scripts/make_reel.py <queue_id> | --dir <folder>", file=sys.stderr)
        return 2
    path = build_reel(folder)
    if path is None:
        print("릴스를 만들지 못했습니다.", file=sys.stderr)
        return 1
    issues = reel_issues(path)
    print(f"{path} ({path.stat().st_size / 1024 / 1024:.1f}MB)", "문제 없음" if not issues else "문제: " + "; ".join(issues))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
