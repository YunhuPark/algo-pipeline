"""Run the golden article set through the real generation pipeline and report stability.

    python scripts/regression_run.py                # all seeded articles
    python scripts/regression_run.py --ids 15 22    # a subset
    python scripts/regression_run.py --list         # show the set, spend nothing

This spends real API money (about $0.05-0.12 per article that passes, up to the per-run cap for
articles that do not). Nothing is published and the card images go to data/regression/out/.
Video search is skipped by default so the result measures the text pipeline, not YouTube's
day-to-day variance (--with-video turns it back on).

For every article it records: did a card set come out, why not if it did not, cost, number of LLM
calls, retries and their causes, the output-standard violations of the finished set, and the cover
title / caption opening. Results are written to data/regression/results/<time>.json and compared
with the previous run, so a code change is judged by numbers rather than by looking at one card.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SEEDED = ROOT / "data" / "regression" / "articles"
RESULTS = ROOT / "data" / "regression" / "results"
OUT_ROOT = ROOT / "data" / "regression" / "out"


def _load_articles(ids: list[int] | None) -> list[dict]:
    items = []
    for path in sorted(SEEDED.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if ids and data["id"] not in ids:
            continue
        items.append(data)
    return items


def _run_one(article: dict, with_video: bool, out_dir: Path) -> dict:
    from unittest.mock import patch

    from src.agents import design_renderer
    from src.cancellation import GenerationCancelled
    from src.qa.output_standard import blocking, check_output
    from src.schemas.queue_schemas import CollectionMethod, QueueMetadataV2
    from src.services.generation_service import execute_generation

    metadata = QueueMetadataV2.model_validate(article["metadata_json"])
    lineage = metadata.to_source_lineage(CollectionMethod(article["collection_method"]))

    record = {
        "id": article["id"], "expect": article["expect"], "topic": article["topic"],
        "ok": False, "error": "", "cost": None, "calls": 0, "retries": {}, "seconds": 0.0,
        "violations": [], "blocking": 0, "cover": "", "caption_opening": "", "cards": 0,
    }
    started = time.monotonic()
    patches = [patch.object(design_renderer, "OUTPUT_DIR", out_dir)]
    if not with_video:
        patches += [
            patch("src.agents.youtube_fetcher.fetch_video_candidates", return_value=[]),
            patch("src.agents.youtube_fetcher.find_verified_video_for_slide", return_value=(None, 0)),
        ]
    for p in patches:
        p.start()
    result = None
    try:
        result = execute_generation(
            topic=article["topic"], source_lineage=lineage, publish=False,
            human_approval=False, auto=True,
        )
    except GenerationCancelled as stopped:
        record["error"] = type(stopped).__name__
    except Exception as exc:  # a crash is a result too
        record["error"] = f"EXC:{type(exc).__name__}"
    finally:
        for p in patches:
            p.stop()
    record["seconds"] = round(time.monotonic() - started, 1)

    run_id = getattr(result, "run_id", None) if result is not None else None
    if result is not None:
        record["ok"] = bool(result.generation_succeeded and result.image_paths)
        record["error"] = "" if record["ok"] else (result.error_code or "NO_RESULT")
    try:
        from src.db_tracking import _conn
        from src.usage_tracker import run_totals

        with _conn() as conn:
            if run_id is None:   # cancelled/crashed runs: take the newest generation run
                row = conn.execute(
                    "SELECT run_id FROM api_usage WHERE scope='generation' ORDER BY id DESC LIMIT 1"
                ).fetchone()
                run_id = row["run_id"] if row else None
            if run_id:
                totals = run_totals(run_id)
                record["cost"] = totals["priced_cost_usd"]
                record["calls"] = totals["calls"]
                for r in conn.execute(
                    "SELECT error_code, COUNT(*) n FROM retry_events WHERE run_id=? GROUP BY error_code",
                    (run_id,),
                ):
                    record["retries"][r["error_code"]] = r["n"]
    except Exception:
        pass

    if record["ok"]:
        folder = Path(result.image_paths[0]).parent
        record["cards"] = len(list(folder.glob("card_*.png")))
        violations = check_output(folder)
        record["violations"] = [f"{v.severity}:{v.rule}" for v in violations]
        record["blocking"] = len(blocking(violations))
        try:
            script = json.loads((folder / "script.json").read_text(encoding="utf-8"))
            record["cover"] = script["slides"][0]["title"]
            caption = (folder / "caption.txt").read_text(encoding="utf-8").strip().splitlines()
            record["caption_opening"] = caption[0] if caption else ""
        except Exception:
            pass
    return record


def _summary(records: list[dict]) -> dict:
    n = len(records)
    ok = [r for r in records if r["ok"]]
    costs = [r["cost"] for r in records if r["cost"] is not None]
    good = [r for r in records if r["expect"] == "success"]
    ok_costs = sorted(r["cost"] for r in ok if r["cost"] is not None)
    return {
        "articles": n,
        "success_rate": round(len(ok) / n, 3) if n else 0,
        "expected_success_rate": round(sum(r["ok"] for r in good) / len(good), 3) if good else None,
        "total_cost": round(sum(costs), 3),
        "median_cost_of_successes": round(ok_costs[len(ok_costs) // 2], 3) if ok_costs else None,
        "max_cost": round(max(costs), 3) if costs else None,
        "blocking_violations": sum(r["blocking"] for r in records),
        "total_retries": sum(sum(r["retries"].values()) for r in records),
    }


def _previous_results() -> dict | None:
    files = sorted(RESULTS.glob("*.json"))
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ids", type=int, nargs="*", help="only these article ids")
    parser.add_argument("--list", action="store_true", help="list the set and exit (spends nothing)")
    parser.add_argument("--with-video", action="store_true", help="also run YouTube video matching")
    args = parser.parse_args()

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    articles = _load_articles(args.ids)
    if not articles:
        print("No seeded articles. Run: python scripts/regression_seed.py")
        return 1
    if args.list:
        for a in articles:
            print(f"  #{a['id']:>3} [{a['expect']:<7}] {a['topic'][:48]}  - {a['why']}")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = OUT_ROOT / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for a in articles:
        print(f"running #{a['id']} [{a['expect']}] {a['topic'][:40]} ...", flush=True)
        rec = _run_one(a, args.with_video, out_dir)
        records.append(rec)
        cost = f"${rec['cost']:.3f}" if rec["cost"] is not None else "n/a"
        print(f"   -> {'OK  ' if rec['ok'] else 'FAIL'} {rec['error'] or ''} cost={cost} calls={rec['calls']} "
              f"retries={sum(rec['retries'].values())} {rec['seconds']}s violations={rec['violations']}",
              flush=True)

    summary = _summary(records)
    previous = _previous_results()
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{stamp}.json").write_text(
        json.dumps({"time": stamp, "summary": summary, "records": records}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\n=== summary ===")
    for key, value in summary.items():
        before = previous["summary"].get(key) if previous else None
        delta = f"   (previous: {before})" if previous and before is not None else ""
        print(f"  {key:<28} {value}{delta}")
    print(f"\nresults: {RESULTS / (stamp + '.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
