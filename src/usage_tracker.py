"""Record every LLM call's token usage and estimated cost.

Until now nothing recorded what a run costs: ``content_runs.cost_usd`` was
written as the default 0.0 for all 113 runs, which is indistinguishable from
"free". This module records one ``api_usage`` row per call from the single
choke point every ``ChatOpenAI`` call already goes through (llm_provider),
tagged with the pipeline stage and the run it belongs to.

Two rules keep the numbers honest:

* A model without a known price is stored with ``cost_usd = NULL``, never 0.
  Tokens are always kept, so the cost can be computed later once a price is
  configured. A run's total is only reported as a number when every call in it
  was priced.
* Recording is best-effort. A tracking failure must never break generation.

Prices are USD per 1M tokens, taken from published list prices and **may be
out of date**; override or extend them with the LLM_PRICES_JSON env var, e.g.
``{"gemini-3.8-flash": [0.0, 0.0]}`` for a free tier. Only LLM tokens are
tracked here — search APIs (Tavily, YouTube, Pexels) and image generation are
not.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

# (input, output) USD per 1M tokens.
DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
}

_SRC_ROOT = os.path.normcase(str(Path(__file__).resolve().parent))
_SKIP_FILES = ("llm_provider.py", "usage_tracker.py")

_scope_lock = threading.Lock()
_scope: dict[str, str | None] = {"kind": "", "run_id": None}
_table_ready: set[str] = set()


# ── pricing ───────────────────────────────────────────────


def _price_table() -> dict[str, tuple[float, float]]:
    table = dict(DEFAULT_PRICES)
    raw = os.getenv("LLM_PRICES_JSON", "").strip()
    if raw:
        try:
            for model, pair in json.loads(raw).items():
                table[str(model)] = (float(pair[0]), float(pair[1]))
        except (ValueError, TypeError, IndexError, AttributeError):
            pass  # a malformed override must not disable tracking
    return table


def price_for(model: str) -> tuple[float, float] | None:
    """USD per 1M (input, output) tokens, or None when the price is unknown.

    The longest matching key wins, so a dated snapshot such as
    "gpt-4o-mini-2024-07-18" prices as "gpt-4o-mini", not as "gpt-4o".
    """

    name = (model or "").strip().lower()
    table = _price_table()
    if name in table:
        return table[name]
    matches = [key for key in table if name.startswith(key.lower())]
    return table[max(matches, key=len)] if matches else None


def estimate_cost(
    model: str, input_tokens: int | None, output_tokens: int | None
) -> float | None:
    """Estimated USD for one call; None if the price or the token counts are unknown."""

    price = price_for(model)
    if price is None or input_tokens is None or output_tokens is None:
        return None
    return round((input_tokens * price[0] + output_tokens * price[1]) / 1_000_000, 8)


# ── scope ─────────────────────────────────────────────────


@contextmanager
def usage_scope(kind: str, run_id: str | None = None) -> Iterator[None]:
    """Tag every LLM call made inside the block (including in worker threads).

    The pipeline runs one generation at a time and fans out to worker threads
    (video verification), so a process-wide scope is used rather than a
    thread-local one, which those workers would not see.
    """

    with _scope_lock:
        previous = dict(_scope)
        _scope.update(kind=kind, run_id=run_id)
    try:
        yield
    finally:
        with _scope_lock:
            _scope.update(previous)


def _current_scope() -> tuple[str, str | None]:
    with _scope_lock:
        return str(_scope["kind"] or ""), _scope["run_id"]


def _caller_stage() -> str:
    """The first src/ function above the LLM wrapper, e.g. agents.angle_selector.generate_angles."""

    frame = sys._getframe(1)
    while frame is not None:
        filename = os.path.normcase(frame.f_code.co_filename)
        if filename.startswith(_SRC_ROOT) and os.path.basename(filename) not in _SKIP_FILES:
            relative = os.path.relpath(filename, _SRC_ROOT)
            module = os.path.splitext(relative)[0].replace(os.sep, ".")
            return f"{module}.{frame.f_code.co_name}"
        frame = frame.f_back
    return "unknown"


# ── reading usage out of a response ───────────────────────


def usage_from_result(result: Any) -> tuple[int | None, int | None]:
    """(input_tokens, output_tokens) from a LangChain ChatResult; None if absent."""

    try:
        for generation in getattr(result, "generations", None) or []:
            meta = getattr(getattr(generation, "message", None), "usage_metadata", None)
            if meta and meta.get("input_tokens") is not None:
                return int(meta["input_tokens"]), int(meta.get("output_tokens") or 0)
        usage = (getattr(result, "llm_output", None) or {}).get("token_usage") or {}
        if usage.get("prompt_tokens") is not None:
            return int(usage["prompt_tokens"]), int(usage.get("completion_tokens") or 0)
    except (AttributeError, TypeError, ValueError):
        pass
    return None, None


# ── storage ───────────────────────────────────────────────


def _ensure_table(conn, key: str) -> None:
    if key in _table_ready:
        return
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS api_usage (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            scope         TEXT NOT NULL DEFAULT '',
            run_id        TEXT,
            stage         TEXT NOT NULL DEFAULT '',
            provider      TEXT NOT NULL DEFAULT '',
            model         TEXT NOT NULL DEFAULT '',
            input_tokens  INTEGER,
            output_tokens INTEGER,
            cost_usd      REAL,
            ok            INTEGER NOT NULL DEFAULT 1,
            error         TEXT NOT NULL DEFAULT ''
        );
        """
    )
    _table_ready.add(key)


def tracking_enabled() -> bool:
    """Whether calls are recorded.

    ALGO_USAGE_TRACKING=off|on forces it either way. Otherwise recording is on,
    except while pytest is running: many tests build real LLM wrapper objects
    without pointing at a test database, and their (mocked) calls would be
    written into the production tracking DB. Tests that exercise recording opt
    in with ALGO_USAGE_TRACKING=on and their own database.
    """

    flag = os.getenv("ALGO_USAGE_TRACKING", "").strip().lower()
    if flag in {"off", "0", "false", "no"}:
        return False
    if flag in {"on", "1", "true", "yes"}:
        return True
    return "PYTEST_CURRENT_TEST" not in os.environ


def record_llm_call(
    *,
    provider: str,
    model: str,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    ok: bool = True,
    error: str = "",
    stage: str | None = None,
) -> None:
    """Store one call. Never raises: tracking must not break generation."""

    if not tracking_enabled():
        return
    try:
        from src.db_tracking import _conn, resolve_tracking_db_path

        scope, run_id = _current_scope()
        cost = estimate_cost(model, input_tokens, output_tokens) if ok else None
        with _conn() as conn:
            _ensure_table(conn, str(resolve_tracking_db_path()))
            conn.execute(
                """INSERT INTO api_usage (
                       scope, run_id, stage, provider, model,
                       input_tokens, output_tokens, cost_usd, ok, error
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    scope,
                    run_id,
                    stage if stage is not None else _caller_stage(),
                    provider,
                    model,
                    input_tokens,
                    output_tokens,
                    cost,
                    1 if ok else 0,
                    error[:200],
                ),
            )
    except Exception:
        pass


def run_totals(run_id: str) -> dict[str, Any]:
    """Aggregate one run: priced cost, tokens, and how many calls had no price.

    ``cost_usd`` is a number only when every successful call was priced;
    otherwise it is None, because a partial sum would understate the real cost.
    """

    empty: dict[str, Any] = {
        "calls": 0,
        "failed_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "priced_cost_usd": 0.0,
        "unpriced_calls": 0,
        "cost_usd": None,
    }
    try:
        from src.db_tracking import _conn

        with _conn() as conn:
            row = conn.execute(
                """SELECT COUNT(*) AS calls,
                          SUM(ok = 0) AS failed,
                          COALESCE(SUM(input_tokens), 0) AS tin,
                          COALESCE(SUM(output_tokens), 0) AS tout,
                          COALESCE(SUM(cost_usd), 0.0) AS priced,
                          SUM(ok = 1 AND cost_usd IS NULL) AS unpriced
                   FROM api_usage WHERE run_id = ?""",
                (run_id,),
            ).fetchone()
    except Exception:
        return empty
    if not row or not row["calls"]:
        return empty
    unpriced = int(row["unpriced"] or 0)
    priced = round(float(row["priced"]), 6)
    return {
        "calls": int(row["calls"]),
        "failed_calls": int(row["failed"] or 0),
        "input_tokens": int(row["tin"]),
        "output_tokens": int(row["tout"]),
        "priced_cost_usd": priced,
        "unpriced_calls": unpriced,
        "cost_usd": priced if unpriced == 0 else None,
    }
