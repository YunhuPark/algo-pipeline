"""Post a devlog card folder to Threads (the algo account only; the account guard runs first).

Usage: python scripts/post_devlog_threads.py <card folder> --yes
Without --yes it only shows what would be posted.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env", override=True)

from src import threads_credentials  # noqa: E402
from src.threads_cross_post import _caption_for, cross_post_folder  # noqa: E402


def main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--yes"]
    if len(args) != 1 or not Path(args[0]).is_dir():
        print("Usage: python scripts/post_devlog_threads.py <card folder> [--yes]", file=sys.stderr)
        return 2
    folder = Path(args[0])
    topic = json.loads((folder / "script.json").read_text(encoding="utf-8"))["topic"]
    token, user_id = threads_credentials.load()
    account = threads_credentials.assert_expected_account(token, user_id)      # raises when it is the wrong account
    print(f"대상 계정: @{account}\n--- 올라갈 글 ---\n{_caption_for(folder)}\n-----------------")
    if "--yes" not in argv:
        print("미리 보기만 했습니다. 올리려면 --yes를 붙이세요.")
        return 0
    ok, message = cross_post_folder(str(folder), topic)
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
