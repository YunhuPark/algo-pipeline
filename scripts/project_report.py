"""Write docs/retrospective/metrics.md: the project's numbers, read from git and the local databases.

Run it whenever the retrospective should be refreshed (it only reads; nothing is posted or paid):
    python scripts/project_report.py
The numbers come from data, never from memory: commits and code size from git, posts/cost/retries
from data/algo.db and data/tracking.db, regression runs from data/regression/results.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "retrospective" / "metrics.md"


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8").stdout.strip()


def _lines(pattern: str) -> int:
    files = [f for f in _git("ls-files", pattern).splitlines() if f.endswith(".py")]
    total = 0
    for name in files:
        try:
            total += len((ROOT / name).read_text(encoding="utf-8").splitlines())
        except OSError:
            pass
    return total


def _rows(db: Path, sql: str) -> list[tuple]:
    if not db.exists():
        return []
    try:
        with sqlite3.connect(db) as conn:
            return conn.execute(sql).fetchall()
    except sqlite3.Error:
        return []


def collect() -> dict:
    commits = int(_git("rev-list", "--count", "HEAD") or 0)
    first = _git("log", "--reverse", "--format=%ad", "--date=short").splitlines()[:1]
    algo, tracking = ROOT / "data" / "algo.db", ROOT / "data" / "tracking.db"
    usage_days = _rows(tracking, "select date(created_at), count(*), round(sum(cost_usd),2) from api_usage group by 1 order by 1")
    runs = _rows(
        tracking,
        "select count(*), sum(status='SUCCESS' or status='success'), round(avg(cost_usd),3) from content_runs",
    )
    regression = []
    for path in sorted((ROOT / "data" / "regression" / "results").glob("*.json")):
        try:
            summary = json.loads(path.read_text(encoding="utf-8"))["summary"]
        except (OSError, ValueError, KeyError):
            continue
        regression.append((path.stem, summary.get("articles"), summary.get("success_rate"),
                           summary.get("total_cost"), summary.get("total_retries")))
    growth = _rows(
        ROOT / "data" / "growth.db",
        "select day, platform, followers from account_snapshots where followers is not null order by day",
    )
    return {
        "growth": growth,
        "commits": commits,
        "first_commit": first[0] if first else "",
        "src_lines": _lines("src/*.py"),
        "test_lines": _lines("tests/*.py"),
        "test_files": len([f for f in _git("ls-files", "tests/test_*.py").splitlines() if f]),
        "posts": _rows(algo, "select platform, count(*), min(posted_at), max(posted_at) from posts group by platform"),
        "queue": _rows(algo, "select status, count(*) from queue group by status"),
        "usage_days": usage_days,
        "runs": runs[0] if runs else (0, 0, 0),
        "retries": _rows(tracking, "select error_code, count(*) from retry_events group by 1 order by 2 desc limit 8"),
        "regression": regression,
    }


def render(stats: dict) -> str:
    total_cost = sum(day[2] or 0 for day in stats["usage_days"])
    lines = [
        "# 프로젝트 수치 (자동 생성)",
        "",
        f"`scripts/project_report.py`가 {date.today().isoformat()}에 git과 로컬 DB에서 읽은 값입니다. 손으로 고치지 마세요.",
        "",
        "## 코드와 개발 기록",
        f"- 커밋 {stats['commits']}개, 첫 커밋 {stats['first_commit']}",
        f"- 소스 {stats['src_lines']:,}줄, 테스트 {stats['test_lines']:,}줄 (테스트 파일 {stats['test_files']}개)",
        "",
        "## 게시",
    ]
    for platform, count, first, last in stats["posts"]:
        lines.append(f"- {platform}: {count}건 (기록 {str(first)[:10]} ~ {str(last)[:10]})")
    if not stats["posts"]:
        lines.append("- 기록 없음")
    lines += ["", "## 큐 상태", *[f"- {status}: {count}건" for status, count in stats["queue"]]]
    lines += ["", "## AI 비용 (기록이 시작된 날부터)"]
    lines.append(f"- 합계 ${total_cost:.2f}, 기록한 날 {len(stats['usage_days'])}일")
    for day, calls, cost in stats["usage_days"]:
        lines.append(f"  - {day}: 호출 {calls}회, ${cost}")
    runs_total, runs_ok, runs_avg = stats["runs"]
    lines += ["", "## 생성 실행", f"- 기록된 실행 {runs_total or 0}회(취소·회귀 측정·재시도 포함), 그중 성공 상태 {runs_ok or 0}회, 평균 비용 ${runs_avg or 0}. 성공률로 읽지 마세요"]
    lines += ["", "## 재시도 원인 (많은 순)"]
    lines += [f"- {code}: {count}회" for code, count in stats["retries"]] or ["- 기록 없음"]
    lines += ["", "## 회귀 측정 (골든 기사 11개)"]
    for stem, articles, rate, cost, retries in stats["regression"]:
        shown = f"{rate * 100:.0f}%" if isinstance(rate, (int, float)) else "-"
        lines.append(f"- {stem}: 기사 {articles}개, 성공률 {shown}, 비용 ${cost}, 재시도 {retries}회")
    lines += ["", "## 성장 (data/growth.db, python scripts/growth_snapshot.py)"]
    by_platform: dict[str, list[tuple[str, int]]] = {}
    for day, platform, followers in stats.get("growth", []):
        by_platform.setdefault(platform, []).append((day, followers))
    for platform, rows in by_platform.items():
        first, last = rows[0], rows[-1]
        lines.append(f"- 팔로워 {platform}: {first[1]} ({first[0]}) → {last[1]} ({last[0]}), 기록 {len(rows)}일")
    if not by_platform:
        lines.append("- 기록 없음")
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(collect()), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
