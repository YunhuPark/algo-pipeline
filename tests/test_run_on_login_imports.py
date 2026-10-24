"""`run_on_login.bat` starts the login trigger as `python scripts\\run_on_login.py` from the project folder, with no PYTHONPATH.

Started that way, Python puts only `scripts/` on sys.path, not the repository root, so `import src...` only works after the script itself inserts the
root (`sys.path.insert(0, ROOT)`). A top-level `from src.db_factory import ...` placed BEFORE that line crashes the trigger at startup with
`ModuleNotFoundError: No module named 'src'` (introduced in 18f79fc). The existing guard tests read function source text and never execute the import,
so they could not see it; this one really imports the script the way the .bat does.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_on_login.py"


def _run_header(tmp_path: Path) -> subprocess.CompletedProcess:
    # -I: no cwd, no script dir, no PYTHON* variables on sys.path -> `src` is importable only if the script puts the repository root there itself.
    # run_name != "__main__": executes the module's top-level code (all imports) without starting the login routine.
    code = f"import runpy; runpy.run_path(r'{SCRIPT}', run_name='import_probe')"
    env = {**os.environ, "ALGO_ENV": "test", "OPENAI_API_KEY": "test-key", "TAVILY_API_KEY": "test-key"}
    env.pop("PYTHONPATH", None)
    return subprocess.run([sys.executable, "-I", "-c", code], cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)


def test_the_login_script_imports_cleanly_when_started_the_way_the_bat_file_starts_it(tmp_path):
    proc = _run_header(tmp_path)
    assert proc.returncode == 0, (proc.stderr or proc.stdout)[-800:]


def test_a_failed_import_would_be_reported_not_swallowed(tmp_path):
    """Guards the guard: the probe must fail when `src` really cannot be imported (so a pass above means something)."""
    code = "import runpy, sys; sys.path[:] = [p for p in sys.path if 'algo' not in p.lower()]; import src"  # `src` is not importable from an empty cwd under -I
    proc = subprocess.run([sys.executable, "-I", "-c", code], cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode != 0 and "No module named 'src'" in proc.stderr
