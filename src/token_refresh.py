"""Refresh the Instagram/Threads long-lived tokens on their own when they near expiry.

Runs only when a token has <= REFRESH_WITHIN_DAYS left and is at least a day old (the providers
require that). A failure never blocks anything: the dashboard banner keeps warning. Nothing secret
is printed.
"""
from __future__ import annotations

import io
import os
import sys
import time
from contextlib import redirect_stderr, redirect_stdout

from src.token_status import TOKEN_LIFETIME_DAYS, days_left

REFRESH_WITHIN_DAYS = 14
_MIN_AGE_DAYS = 1
_CHECK_EVERY_SECONDS = 3600
_last_check = 0.0

_MODULE_TOKENS = {
    "src.agents.publisher": "IG_ACCESS_TOKEN",
    "src.agents.analytics": "IG_ACCESS_TOKEN",
    "src.agents.comment_manager": "IG_ACCESS_TOKEN",
    "src.agents.dm_manager": "IG_ACCESS_TOKEN",
    "src.agents.threads_publisher": "THREADS_ACCESS_TOKEN",
}


def _due(date_var: str, token_var: str) -> bool:
    if not os.getenv(token_var, "").strip():
        return False
    left = days_left(var=date_var)
    if left is None:
        return False   # no recorded date: the banner asks for one manual refresh first
    age = TOKEN_LIFETIME_DAYS - left
    return 0 < left <= REFRESH_WITHIN_DAYS and age >= _MIN_AGE_DAYS


def _push_into_loaded_modules() -> None:
    """Modules read the tokens at import time; hand them the refreshed values."""
    from src import threads_credentials

    for mod_name, env in _MODULE_TOKENS.items():
        mod = sys.modules.get(mod_name)
        if mod is None or not hasattr(mod, env):
            continue
        value = threads_credentials.load()[0] if env == "THREADS_ACCESS_TOKEN" else os.getenv(env, "")
        setattr(mod, env, value)


def ensure_fresh(force: bool = False) -> list[str]:
    """Refresh whichever tokens are due. Returns short notes (never token values)."""
    global _last_check
    if not force and time.time() - _last_check < _CHECK_EVERY_SECONDS:
        return []
    _last_check = time.time()
    notes: list[str] = []
    for name, date_var, token_var in (
        ("Instagram", "IG_TOKEN_REFRESHED", "IG_ACCESS_TOKEN"),
        ("Threads", "THREADS_TOKEN_REFRESHED", "THREADS_ACCESS_TOKEN"),
    ):
        if not _due(date_var, token_var):
            continue
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                if name == "Instagram":
                    from scripts.refresh_ig_token import main as run_ig
                    code = run_ig()
                else:
                    from scripts.threads_token import main as run_th
                    code = run_th(["--refresh"])
        except Exception:
            code = 1
        notes.append(f"{name} 토큰 자동 갱신 {'완료' if code == 0 else '실패(수동 갱신 필요)'}")
        if code == 0:
            _push_into_loaded_modules()
    return notes
