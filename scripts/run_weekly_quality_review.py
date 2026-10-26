"""Run one safe, deterministic weekly quality review."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv


def main() -> int:
    load_dotenv()
    from src.analytics.weekly_review import run_weekly_quality_review

    report = run_weekly_quality_review()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
