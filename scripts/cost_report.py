"""Where do the LLM tokens (and money) go?  Read-only report from data/tracking.db.

    python scripts/cost_report.py            # last 7 days
    python scripts/cost_report.py --days 30

Costs are estimates from list prices (see src/usage_tracker.py; override with
LLM_PRICES_JSON). A call to a model with no known price shows its tokens but no
dollar figure, and is counted separately instead of being treated as free.
Only LLM calls are tracked; Tavily/YouTube/Pexels and image generation are not.
"""
from __future__ import annotations

import argparse
import io
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
else:  # pragma: no cover
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def _money(value: float | None) -> str:
    return "n/a" if value is None else f"${value:,.4f}"


def _table(title: str, header: list[str], rows: list[list[str]]) -> None:
    print(f"\n== {title}")
    if not rows:
        print("  (no data)")
        return
    widths = [max(len(str(x)) for x in col) for col in zip(header, *rows)]
    print("  " + "  ".join(h.ljust(w) for h, w in zip(header, widths)))
    for row in rows:
        print("  " + "  ".join(str(c).ljust(w) for c, w in zip(row, widths)))


def build_report(conn: sqlite3.Connection, days: int) -> None:
    conn.row_factory = sqlite3.Row
    since = f"-{int(days)} days"
    where = "created_at >= datetime('now','localtime', ?)"

    total = conn.execute(
        f"""SELECT COUNT(*) calls, COALESCE(SUM(ok=0),0) failed,
                   COALESCE(SUM(input_tokens),0) tin, COALESCE(SUM(output_tokens),0) tout,
                   COALESCE(SUM(cost_usd),0.0) priced,
                   COALESCE(SUM(ok=1 AND cost_usd IS NULL),0) unpriced
            FROM api_usage WHERE {where}""",
        (since,),
    ).fetchone()
    print(f"LLM usage, last {days} day(s)")
    if not total["calls"]:
        print("  No calls recorded in this period. Recording starts with the first LLM call")
        print("  after this feature was installed; earlier runs have no usage data.")
        return
    print(f"  calls: {total['calls']}  (failed: {total['failed']})")
    print(f"  tokens: {total['tin']:,} in / {total['tout']:,} out")
    print(f"  estimated cost (priced calls only): {_money(total['priced'])}")
    if total["unpriced"]:
        unpriced_tokens = conn.execute(
            f"""SELECT COALESCE(SUM(input_tokens),0)+COALESCE(SUM(output_tokens),0) t
                FROM api_usage WHERE {where} AND ok=1 AND cost_usd IS NULL""",
            (since,),
        ).fetchone()["t"]
        print(
            f"  NOT PRICED: {total['unpriced']} calls / {unpriced_tokens:,} tokens "
            "- the real total is higher than shown"
        )

    _table(
        "by scope",
        ["scope", "calls", "failed", "tokens", "cost"],
        [
            [
                r["scope"] or "(none)",
                r["calls"],
                r["failed"],
                f"{r['tokens']:,}",
                _money(r["priced"]),
            ]
            for r in conn.execute(
                f"""SELECT scope, COUNT(*) calls, SUM(ok=0) failed,
                           COALESCE(SUM(input_tokens),0)+COALESCE(SUM(output_tokens),0) tokens,
                           COALESCE(SUM(cost_usd),0.0) priced
                    FROM api_usage WHERE {where} GROUP BY scope ORDER BY calls DESC""",
                (since,),
            )
        ],
    )
    _table(
        "by model",
        ["provider", "model", "calls", "tokens", "cost", "priced?"],
        [
            [
                r["provider"],
                r["model"],
                r["calls"],
                f"{r['tokens']:,}",
                _money(r["priced"]) if r["has_price"] else "n/a",
                "yes" if r["has_price"] else "NO",
            ]
            for r in conn.execute(
                f"""SELECT provider, model, COUNT(*) calls,
                           COALESCE(SUM(input_tokens),0)+COALESCE(SUM(output_tokens),0) tokens,
                           COALESCE(SUM(cost_usd),0.0) priced,
                           MAX(cost_usd IS NOT NULL) has_price
                    FROM api_usage WHERE {where} GROUP BY provider, model ORDER BY calls DESC""",
                (since,),
            )
        ],
    )
    _table(
        "top stages (where the calls happen)",
        ["stage", "calls", "failed", "tokens", "cost"],
        [
            [r["stage"], r["calls"], r["failed"], f"{r['tokens']:,}", _money(r["priced"])]
            for r in conn.execute(
                f"""SELECT stage, COUNT(*) calls, SUM(ok=0) failed,
                           COALESCE(SUM(input_tokens),0)+COALESCE(SUM(output_tokens),0) tokens,
                           COALESCE(SUM(cost_usd),0.0) priced
                    FROM api_usage WHERE {where} GROUP BY stage ORDER BY calls DESC LIMIT 15""",
                (since,),
            )
        ],
    )
    _table(
        "generation runs: successful vs failed (is failure where the money goes?)",
        ["status", "runs", "calls", "tokens", "cost"],
        [
            [
                r["status"],
                r["runs"],
                r["calls"],
                f"{r['tokens']:,}",
                _money(r["priced"]),
            ]
            for r in conn.execute(
                f"""SELECT r.status, COUNT(DISTINCT r.run_id) runs, COUNT(u.id) calls,
                           COALESCE(SUM(u.input_tokens),0)+COALESCE(SUM(u.output_tokens),0) tokens,
                           COALESCE(SUM(u.cost_usd),0.0) priced
                    FROM content_runs r JOIN api_usage u ON u.run_id = r.run_id
                    WHERE u.{where} GROUP BY r.status""",
                (since,),
            )
        ],
    )
    _table(
        "latest runs",
        ["topic", "status", "calls", "tokens", "cost"],
        [
            [
                (r["topic"] or "")[:34],
                r["status"],
                r["calls"],
                f"{r['tokens']:,}",
                _money(r["priced"]) if not r["unpriced"] else f"{_money(r['priced'])}+",
            ]
            for r in conn.execute(
                """SELECT r.topic, r.status, COUNT(u.id) calls,
                          COALESCE(SUM(u.input_tokens),0)+COALESCE(SUM(u.output_tokens),0) tokens,
                          COALESCE(SUM(u.cost_usd),0.0) priced,
                          SUM(u.ok=1 AND u.cost_usd IS NULL) unpriced
                   FROM content_runs r JOIN api_usage u ON u.run_id = r.run_id
                   GROUP BY r.run_id ORDER BY MAX(u.id) DESC LIMIT 10"""
            )
        ],
    )
    print("\n('+' = some calls in that run have no known price, so the cost is a lower bound)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    from src.db_tracking import resolve_tracking_db_path

    path = resolve_tracking_db_path()
    if not path.exists():
        print(f"No tracking database at {path}")
        return 1
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='api_usage'"
        ).fetchone()
        if not has_table:
            print("No usage has been recorded yet (the api_usage table does not exist).")
            return 0
        build_report(conn, args.days)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
