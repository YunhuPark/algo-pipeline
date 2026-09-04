"""Manually refresh an Instagram Login long-lived token without logging it."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
REFRESH_ENDPOINT = "https://graph.instagram.com/refresh_access_token"
ACCOUNT_ENDPOINT = "https://graph.instagram.com/v21.0/me"


class TokenRefreshError(RuntimeError):
    """Sanitized token refresh failure."""


def _response_json(response: httpx.Response, stage: str) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise TokenRefreshError(f"{stage} returned an invalid response.") from exc
    if not isinstance(data, dict) or "error" in data:
        raise TokenRefreshError(f"{stage} was rejected; provider response is hidden.")
    return data


def refresh_token(token: str) -> tuple[str, int]:
    try:
        response = httpx.get(
            REFRESH_ENDPOINT,
            params={"grant_type": "ig_refresh_token", "access_token": token},
            timeout=15,
        )
    except httpx.HTTPError as exc:
        raise TokenRefreshError("Instagram token refresh could not be completed.") from exc
    data = _response_json(response, "Instagram token refresh")
    new_token = str(data.get("access_token") or "")
    if not new_token:
        raise TokenRefreshError("Instagram token refresh returned no token.")
    return new_token, int(data.get("expires_in") or 0)


def verify_account(token: str, expected_user_id: str) -> None:
    try:
        response = httpx.get(
            ACCOUNT_ENDPOINT,
            params={"fields": "id", "access_token": token},
            timeout=10,
        )
    except httpx.HTTPError as exc:
        raise TokenRefreshError("Refreshed token verification could not be completed.") from exc
    data = _response_json(response, "Refreshed token verification")
    if str(data.get("id") or "") != expected_user_id:
        raise TokenRefreshError("Refreshed token belongs to a different account.")


def update_env_token(new_token: str) -> None:
    current = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""
    output: list[str] = []
    replaced = False
    for line in current.splitlines():
        if line.startswith("IG_ACCESS_TOKEN="):
            output.append(f"IG_ACCESS_TOKEN={new_token}")
            replaced = True
        else:
            output.append(line)
    if not replaced:
        output.append(f"IG_ACCESS_TOKEN={new_token}")
    ENV_PATH.write_text("\n".join(output).rstrip("\n") + "\n", encoding="utf-8")


def main() -> int:
    load_dotenv(ENV_PATH, override=True)
    token = os.getenv("IG_ACCESS_TOKEN", "").strip()
    user_id = os.getenv("IG_USER_ID", "").strip()
    if not token or not user_id:
        print("Token refresh blocked: IG_ACCESS_TOKEN/IG_USER_ID is missing.", file=sys.stderr)
        return 2
    try:
        new_token, expires_in = refresh_token(token)
        verify_account(new_token, user_id)
        update_env_token(new_token)
    except TokenRefreshError as exc:
        print(f"Token refresh blocked: {exc}", file=sys.stderr)
        return 2
    days = expires_in // 86400 if expires_in else "unknown"
    print(f"Token refresh complete; validity days: {days}")
    print("The token value and provider responses were not printed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
