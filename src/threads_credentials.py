"""Threads credentials for this project, read from this project's .env file only.

Why not os.environ: another project (mcp-recipes) keeps its own THREADS_ACCESS_TOKEN / THREADS_USER_ID
as Windows user-level environment variables. A process started normally sees those first (load_dotenv
does not override them), so a post meant for this account went to the other one. The .env file is the
single source of truth here, and every post is guarded by an account check as well.
"""
from __future__ import annotations

from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
DEFAULT_EXPECTED_USERNAME = "algo__kr"


def _values() -> dict:
    return dotenv_values(ENV_PATH) if ENV_PATH.exists() else {}


def load() -> tuple[str, str]:
    """(access token, user id) from .env; empty strings when missing."""
    values = _values()
    return (values.get("THREADS_ACCESS_TOKEN") or "").strip(), (values.get("THREADS_USER_ID") or "").strip()


def expected_username() -> str:
    return (_values().get("THREADS_EXPECTED_USERNAME") or DEFAULT_EXPECTED_USERNAME).strip()


def assert_expected_account(token: str, user_id: str) -> str:
    """Refuse to post unless the token belongs to the expected account. Returns the username."""
    if not token or not user_id:
        raise ValueError("THREADS_ACCESS_TOKEN/THREADS_USER_ID가 .env에 없습니다.")
    try:
        data = httpx.get(
            "https://graph.threads.net/v1.0/me",
            params={"fields": "id,username", "access_token": token},
            timeout=15,
        ).json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ValueError("Threads 계정을 확인하지 못해 게시를 중단합니다.") from exc
    username = str(data.get("username") or "")
    if username != expected_username() or str(data.get("id") or "") != user_id:
        raise ValueError(
            f"토큰의 Threads 계정(@{username or '?'})이 알고 계정(@{expected_username()})과 달라 게시를 중단합니다."
        )
    return username
