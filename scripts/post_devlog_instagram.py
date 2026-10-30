"""Post a devlog card folder to Instagram (the algo account only; the account check runs first).

Usage: python scripts/post_devlog_instagram.py <card folder> --yes
Without --yes it only shows the account and the caption that would be posted.
"""
from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env", override=True)

from src import instagram_folder_post as post  # noqa: E402


def main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--yes"]
    if len(args) != 1 or not Path(args[0]).is_dir():
        print("Usage: python scripts/post_devlog_instagram.py <card folder> [--yes]", file=sys.stderr)
        return 2
    folder = str(Path(args[0]).resolve())
    username, caption = post.preview(folder)
    print(f"대상 계정: @{username}\n--- 올라갈 캡션 ---\n{caption}\n-------------------")
    if "--yes" not in argv:
        print("미리 보기만 했습니다. 올리려면 --yes를 붙이세요.")
        return 0
    ok, message = post.post_folder(folder)
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
