"""Secure one-time OAuth setup for Instagram API with Instagram Login.

Run from the repository root with the dashboard and Scheduler stopped:

    python scripts/get_ig_token.py

App credentials are read only from ``.env``. Tokens, authorization codes, and
provider responses are never printed.
"""
from __future__ import annotations

import getpass
import os
import secrets
import sys
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
AUTH_ENDPOINT = "https://www.instagram.com/oauth/authorize"
TOKEN_ENDPOINT = "https://api.instagram.com/oauth/access_token"
LONG_LIVED_ENDPOINT = "https://graph.instagram.com/access_token"
ACCOUNT_ENDPOINT = "https://graph.instagram.com/v21.0/me"
SCOPES = ("instagram_business_basic", "instagram_business_content_publish")


class OAuthSetupError(RuntimeError):
    """Sanitized OAuth setup failure."""


def _required_setting(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value or value.lower() in {"placeholder", "changeme", "example"}:
        raise OAuthSetupError(f"{name} is not configured in .env.")
    return value


def build_authorization_url(app_id: str, redirect_uri: str, state: str) -> str:
    query = urllib.parse.urlencode(
        {
            "client_id": app_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": ",".join(SCOPES),
            "state": state,
            "enable_fb_login": "0",
            "force_authentication": "1",
        }
    )
    return f"{AUTH_ENDPOINT}?{query}"


def parse_callback_url(callback_url: str, redirect_uri: str, state: str) -> str:
    callback = urllib.parse.urlparse(callback_url.strip())
    expected = urllib.parse.urlparse(redirect_uri)
    if (callback.scheme, callback.netloc, callback.path) != (
        expected.scheme,
        expected.netloc,
        expected.path,
    ):
        raise OAuthSetupError("Callback URL does not match IG_REDIRECT_URI.")

    values = urllib.parse.parse_qs(callback.query)
    if values.get("state", [""])[0] != state:
        raise OAuthSetupError("OAuth state mismatch; restart setup.")
    if "error" in values:
        raise OAuthSetupError("Instagram authorization was denied.")
    code = values.get("code", [""])[0].removesuffix("#_").strip()
    if not code:
        raise OAuthSetupError("Callback URL does not contain an authorization code.")
    return code


def _response_json(response: httpx.Response, stage: str) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise OAuthSetupError(f"{stage} returned an invalid response.") from exc
    if not isinstance(data, dict) or "error" in data:
        raise OAuthSetupError(f"{stage} was rejected; provider response is hidden.")
    return data


def exchange_authorization_code(
    code: str,
    app_id: str,
    app_secret: str,
    redirect_uri: str,
) -> tuple[str, str]:
    try:
        response = httpx.post(
            TOKEN_ENDPOINT,
            data={
                "client_id": app_id,
                "client_secret": app_secret,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
                "code": code,
            },
            timeout=15,
        )
    except httpx.HTTPError as exc:
        raise OAuthSetupError("Short-lived token exchange could not be completed.") from exc
    data = _response_json(response, "Short-lived token exchange")
    token = str(data.get("access_token") or "")
    user_id = str(data.get("user_id") or "")
    if not token or not user_id:
        raise OAuthSetupError("Token exchange did not return the required account data.")
    return token, user_id


def exchange_long_lived_token(short_token: str, app_secret: str) -> str:
    try:
        response = httpx.get(
            LONG_LIVED_ENDPOINT,
            params={
                "grant_type": "ig_exchange_token",
                "client_secret": app_secret,
                "access_token": short_token,
            },
            timeout=15,
        )
    except httpx.HTTPError as exc:
        raise OAuthSetupError("Long-lived token exchange could not be completed.") from exc
    data = _response_json(response, "Long-lived token exchange")
    token = str(data.get("access_token") or "")
    if not token:
        raise OAuthSetupError("Long-lived token exchange returned no token.")
    return token


def verify_account(token: str, expected_user_id: str) -> dict[str, str]:
    try:
        response = httpx.get(
            ACCOUNT_ENDPOINT,
            params={
                "fields": "id,username,account_type",
                "access_token": token,
            },
            timeout=10,
        )
    except httpx.HTTPError as exc:
        raise OAuthSetupError("Instagram account verification could not be completed.") from exc
    data = _response_json(response, "Instagram account verification")
    actual_user_id = str(data.get("id") or "")
    if not actual_user_id or actual_user_id != expected_user_id:
        raise OAuthSetupError("Token account does not match the returned Instagram user ID.")
    account_type = str(data.get("account_type") or "").upper()
    if account_type not in {"BUSINESS", "CREATOR", "MEDIA_CREATOR"}:
        raise OAuthSetupError("Instagram account is not a supported professional account.")
    return {
        "username": str(data.get("username") or ""),
        "account_type": account_type,
    }


def update_env(values: dict[str, str]) -> None:
    current = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""
    remaining = dict(values)
    output: list[str] = []
    for line in current.splitlines():
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in remaining:
            output.append(f"{key}={remaining.pop(key)}")
        else:
            output.append(line)
    if output and output[-1] != "":
        output.append("")
    output.extend(f"{key}={value}" for key, value in remaining.items())
    ENV_PATH.write_text("\n".join(output).rstrip("\n") + "\n", encoding="utf-8")


def main() -> int:
    load_dotenv(ENV_PATH, override=True)
    try:
        app_id = _required_setting("META_APP_ID")
        app_secret = _required_setting("META_APP_SECRET")
        redirect_uri = _required_setting("IG_REDIRECT_URI")

        state = secrets.token_urlsafe(32)
        auth_url = build_authorization_url(app_id, redirect_uri, state)
        print("Instagram Login authorization page를 엽니다.")
        print("승인 후 브라우저 주소 표시줄의 전체 callback URL을 복사하세요.")
        if not webbrowser.open(auth_url):
            print(f"브라우저가 열리지 않으면 이 주소를 직접 여세요: {auth_url}")
        callback_url = getpass.getpass("Callback URL (입력 숨김): ")
        code = parse_callback_url(callback_url, redirect_uri, state)

        short_token, user_id = exchange_authorization_code(
            code, app_id, app_secret, redirect_uri
        )
        long_token = exchange_long_lived_token(short_token, app_secret)
        account = verify_account(long_token, user_id)
        update_env({"IG_ACCESS_TOKEN": long_token, "IG_USER_ID": user_id})
    except OAuthSetupError as exc:
        print(f"OAuth setup blocked: {exc}", file=sys.stderr)
        return 2

    username = account["username"] or "verified account"
    account_type = account["account_type"] or "professional"
    print(f"OAuth setup complete: @{username} ({account_type})")
    print("Secrets were saved to .env and were not printed.")
    print("Next: python scripts/ig_preflight.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
