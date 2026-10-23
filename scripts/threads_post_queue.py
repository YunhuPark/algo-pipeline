"""Post an already-published queue item to Threads (safe to re-run: duplicates are refused).

Usage: python scripts/threads_post_queue.py <queue_id>
"""
from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env", override=True)

from src.threads_cross_post import cross_post_queue_item  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 1 or not argv[0].isdigit():
        print("Usage: python scripts/threads_post_queue.py <queue_id>", file=sys.stderr)
        return 2
    ok, message = cross_post_queue_item(int(argv[0]))
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
