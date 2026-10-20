"""Days left on the Instagram long-lived token, from the refresh date recorded in .env."""
from __future__ import annotations

import os
from datetime import date

TOKEN_LIFETIME_DAYS = 60
WARN_DAYS = 10
_STYLE = "background:#fff3cd;color:#7a5b00;padding:10px 14px;border-radius:8px;margin-bottom:12px"


def days_left(today: date | None = None, var: str = "IG_TOKEN_REFRESHED") -> int | None:
    """Days until the token expires, or None when no refresh date is recorded."""
    raw = os.getenv(var, "").strip()
    if not raw:
        return None
    try:
        refreshed = date.fromisoformat(raw)
    except ValueError:
        return None
    return TOKEN_LIFETIME_DAYS - ((today or date.today()) - refreshed).days


def banner_html(today: date | None = None) -> str:
    """A red/amber notice for the dashboard; empty when the token is comfortably valid."""
    left = days_left(today)
    if left is None:
        return (f'<div style="{_STYLE}">Instagram 토큰 갱신일이 기록되어 있지 않습니다. '
                '<code>python scripts/refresh_ig_token.py</code> 실행 시 자동으로 기록됩니다.</div>')
    if left <= 0:
        return f'<div style="{_STYLE}">Instagram 토큰이 만료되었습니다. 토큰을 새로 발급해야 합니다.</div>'
    if left <= WARN_DAYS:
        return (f'<div style="{_STYLE}">Instagram 토큰 만료까지 {left}일 남았습니다. '
                '<code>python scripts/refresh_ig_token.py</code>로 갱신하세요.</div>')
    return ""


def threads_banner_html(today: date | None = None) -> str:
    """Same notice for the Threads token; silent when Threads is not configured."""
    if not os.getenv("THREADS_ACCESS_TOKEN", "").strip():
        return ""
    left = days_left(today, "THREADS_TOKEN_REFRESHED")
    if left is None:
        return f'<div style="{_STYLE}">Threads 토큰 갱신일이 기록되어 있지 않습니다. <code>python scripts/threads_token.py --refresh</code></div>'
    if left <= 0:
        return f'<div style="{_STYLE}">Threads 토큰이 만료되었습니다. 새로 발급해야 합니다.</div>'
    if left <= WARN_DAYS:
        return f'<div style="{_STYLE}">Threads 토큰 만료까지 {left}일 남았습니다. <code>python scripts/threads_token.py --refresh</code></div>'
    return ""
