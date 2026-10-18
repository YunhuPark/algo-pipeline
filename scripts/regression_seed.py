"""Copy the golden articles' source data out of the queue DB into data/regression/ (git-ignored).

    python scripts/regression_seed.py

The article texts are third-party content, so they are never committed: the manifest in
tests/golden/manifest.json lists only ids and labels. Seed once; the runner then works offline
from these files, so every run sees exactly the same input.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "data" / "regression" / "articles"


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    from src.db import resolve_algo_db_path

    manifest = json.loads((ROOT / "tests" / "golden" / "manifest.json").read_text(encoding="utf-8"))
    conn = sqlite3.connect(resolve_algo_db_path())
    conn.row_factory = sqlite3.Row
    OUT.mkdir(parents=True, exist_ok=True)
    seeded = 0
    for entry in manifest["articles"]:
        row = conn.execute("SELECT * FROM queue WHERE id = ?", (entry["id"],)).fetchone()
        if row is None or not row["metadata_json"]:
            print(f"  skip id={entry['id']}: row or metadata missing")
            continue
        payload = {
            "id": entry["id"],
            "expect": entry["expect"],
            "why": entry["why"],
            "topic": row["topic"],
            "collection_method": row["collection_method"],
            "metadata_json": json.loads(row["metadata_json"]),
        }
        (OUT / f"{entry['id']:03d}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        seeded += 1
        print(f"  seeded id={entry['id']:>3} [{entry['expect']}] {row['topic'][:40]}")
    print(f"seeded {seeded}/{len(manifest['articles'])} articles into {OUT}")
    return 0 if seeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
