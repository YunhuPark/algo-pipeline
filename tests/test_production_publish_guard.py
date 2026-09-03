from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).parents[1]


def _function_source(path: Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"missing function {name} in {path}")


def test_scheduler_never_bypasses_queue_publish_state_machine():
    source = _function_source(ROOT / "src" / "scheduler.py", "job_daily_cardnews")
    assert "bulk_generate" in source
    assert "publish_next" in source
    assert "run_pipeline" not in source
    assert "ig_publisher" not in source


def test_daily_and_login_automation_use_queue_cli_only():
    daily = _function_source(ROOT / "scripts" / "run_daily.py", "main")
    login = _function_source(ROOT / "scripts" / "run_on_login.py", "startup")
    for source in (daily, login):
        assert '"--queue", "1"' in source
        assert '"--queue-publish", "--publish"' in source
        assert "run_pipeline" not in source


def test_unattended_automation_checks_explicit_live_mode():
    scheduler = _function_source(ROOT / "src" / "scheduler.py", "job_daily_cardnews")
    daily = _function_source(ROOT / "scripts" / "run_daily.py", "main")
    login = _function_source(ROOT / "scripts" / "run_on_login.py", "startup")
    assert "AUTO_UPLOAD" in scheduler
    assert "automation_mode.live_publish" in daily
    assert "automation_mode.live_publish" in login

    login_main = _function_source(ROOT / "scripts" / "run_on_login.py", "main")
    assert login_main.index("resolve_automation_mode") < login_main.index("_ensure_services")


def test_agent_live_publish_rejects_nonproduction_before_db(tmp_path):
    db_path = tmp_path / "algo.db"
    env = os.environ.copy()
    env["ALGO_ENV"] = "test"
    env["ALGO_DB_PATH"] = str(db_path)
    result = subprocess.run(
        [sys.executable, str(ROOT / "main.py"), "--agent", "--publish"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "ALGO_ENV=production" in result.stderr
    assert not db_path.exists()


def test_manual_cli_publish_is_blocked_before_importing_publisher():
    result = subprocess.run(
        [sys.executable, str(ROOT / "main.py"), "unverified topic", "--publish"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "durable attempt" in result.stdout


def test_cached_output_direct_publish_is_blocked():
    result = subprocess.run(
        [sys.executable, str(ROOT / "main.py"), "--upload-dir", "legacy-output"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "durable publish attempt" in result.stdout
