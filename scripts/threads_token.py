"""Threads token helper: --check (read-only), --exchange (short -> 60-day), --refresh (extend).

Token values and provider responses are never printed. Reads/writes THREADS_ACCESS_TOKEN,
THREADS_USER_ID and THREADS_TOKEN_REFRESHED in .env.
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
BASE = "https://graph.threads.net"


class ThreadsTokenError(RuntimeError):
    """Sanitized failure."""


def _json(response: httpx.Response, stage: str) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise ThreadsTokenError(f"{stage} returned an invalid response.") from exc
    if not isinstance(data, dict) or "error" in data:
        raise ThreadsTokenError(f"{stage} was rejected; provider response is hidden.")
    return data


def _get(path: str, params: dict[str, str], stage: str) -> dict[str, Any]:
    try:
        return _json(httpx.get(f"{BASE}{path}", params=params, timeout=15), stage)
    except httpx.HTTPError as exc:
        raise ThreadsTokenError(f"{stage} could not be completed.") from exc


def check(token: str, user_id: str) -> str:
    data = _get("/v1.0/me", {"fields": "id,username", "access_token": token}, "Account check")
    if user_id and str(data.get("id") or "") != user_id:
        raise ThreadsTokenError("Token belongs to a different Threads account.")
    return str(data.get("username") or "?")


def exchange(short_token: str, app_secret: str) -> tuple[str, int]:
    data = _get(
        "/access_token",
        {"grant_type": "th_exchange_token", "client_secret": app_secret, "access_token": short_token},
        "Token exchange",
    )
    return str(data.get("access_token") or ""), int(data.get("expires_in") or 0)


def refresh(token: str) -> tuple[str, int]:
    data = _get(
        "/refresh_access_token",
        {"grant_type": "th_refresh_token", "access_token": token},
        "Token refresh",
    )
    return str(data.get("access_token") or ""), int(data.get("expires_in") or 0)


def update_env(values: dict[str, str]) -> None:
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    for key, value in values.items():
        out, done = [], False
        for line in lines:
            if line.startswith(f"{key}="):
                out.append(f"{key}={value}")
                done = True
            else:
                out.append(line)
        if not done:
            out.append(f"{key}={value}")
        lines = out
    ENV_PATH.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    load_dotenv(ENV_PATH, override=True)
    mode = argv[0] if argv else "--check"
    token = os.getenv("THREADS_ACCESS_TOKEN", "").strip()
    user_id = os.getenv("THREADS_USER_ID", "").strip()
    if not token:
        print("THREADS_ACCESS_TOKEN is missing in .env.", file=sys.stderr)
        return 2
    try:
        if mode == "--check":
            print(f"Threads account OK: @{check(token, user_id)}")
            return 0
        if mode == "--exchange":
            secret = os.getenv("THREADS_APP_SECRET", "").strip()
            if not secret:
                print("THREADS_APP_SECRET is missing in .env (Threads use case -> settings).", file=sys.stderr)
                return 2
            new, expires = exchange(token, secret)
        elif mode == "--refresh":
            new, expires = refresh(token)
        else:
            print("Usage: threads_token.py [--check|--exchange|--refresh]", file=sys.stderr)
            return 2
        if not new:
            raise ThreadsTokenError("No token returned.")
        check(new, user_id)
        update_env({"THREADS_ACCESS_TOKEN": new, "THREADS_TOKEN_REFRESHED": date.today().isoformat()})
    except ThreadsTokenError as exc:
        print(f"Threads token step blocked: {exc}", file=sys.stderr)
        return 2
    print(f"Done; validity days: {expires // 86400 if expires else 'unknown'}")
    print("The token value and provider responses were not printed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
