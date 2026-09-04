"""Read-only Instagram Login credential preflight checks."""
from __future__ import annotations

import re
from typing import Any, Callable

import httpx


GRAPH_BASE = "https://graph.instagram.com/v21.0"
PROFESSIONAL_ACCOUNT_TYPES = {"BUSINESS", "CREATOR", "MEDIA_CREATOR"}


class PreflightError(RuntimeError):
    """A sanitized failure raised before any Instagram publish side effect."""

    def __init__(self, code: str, message: str, meta_subcode: int | None = None):
        self.code = code
        self.message = message
        self.meta_subcode = meta_subcode
        super().__init__(f"[{code}] {message}")


def redact_sensitive(text: str, *secret_values: str) -> str:
    """Redact supplied secrets and common Meta/Instagram token shapes."""
    redacted = text or ""
    for value in secret_values:
        if value:
            redacted = redacted.replace(value, "***(redacted)")
    return re.sub(
        r"\b(?:EAA|IGAA|IGQ)[A-Za-z0-9._-]{8,}\b",
        "***(redacted)",
        redacted,
    )


def _request_json(url: str, params: dict[str, str]) -> dict[str, Any]:
    try:
        response = httpx.get(url, params=params, timeout=10)
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise PreflightError(
            "NETWORK_ERROR",
            "Instagram account verification could not be completed.",
        ) from exc
    if not isinstance(data, dict):
        raise PreflightError("API_ERROR", "Instagram returned an invalid response.")
    return data


class IGPreflightCheck:
    """Verify an Instagram Login token with a read-only ``/me`` request."""

    def __init__(
        self,
        http_client: Callable[[str, dict[str, str]], dict[str, Any]] | None = None,
    ) -> None:
        self.http_client = http_client or _request_json

    def check_token(self, token: str) -> dict[str, Any]:
        """Backward-compatible token check using the Instagram Login API."""
        return self.check_account(token, expected_user_id="")

    def check_account(self, token: str, expected_user_id: str) -> dict[str, Any]:
        if not token:
            raise PreflightError(
                "TOKEN_MISSING", "Instagram access token is not configured."
            )

        try:
            data = self.http_client(
                f"{GRAPH_BASE}/me",
                {
                    "fields": "id,username,account_type",
                    "access_token": token,
                },
            )
        except PreflightError:
            raise
        except Exception as exc:
            raise PreflightError(
                "NETWORK_ERROR",
                "Instagram account verification could not be completed.",
            ) from exc

        if not isinstance(data, dict):
            raise PreflightError("API_ERROR", "Instagram returned an invalid response.")

        error = data.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            subcode = error.get("error_subcode")
            if code == 190 and subcode == 460:
                raise PreflightError("TOKEN_EXPIRED", "Instagram token has expired.", subcode)
            if code == 190:
                raise PreflightError("TOKEN_INVALID", "Instagram token is invalid.", subcode)
            if code in {10, 200, 210}:
                raise PreflightError(
                    "PERMISSION_DENIED",
                    "Instagram token does not have the required permissions.",
                    subcode,
                )
            raise PreflightError("API_ERROR", "Instagram rejected the preflight request.", subcode)

        actual_user_id = str(data.get("id") or "").strip()
        if not actual_user_id:
            raise PreflightError(
                "ACCOUNT_UNAVAILABLE",
                "Instagram account identity was not returned.",
            )
        if expected_user_id and actual_user_id != str(expected_user_id).strip():
            raise PreflightError(
                "ACCOUNT_MISMATCH",
                "Instagram token belongs to a different account.",
            )

        account_type = str(data.get("account_type") or "").upper()
        if account_type not in PROFESSIONAL_ACCOUNT_TYPES:
            raise PreflightError(
                "ACCOUNT_NOT_PROFESSIONAL",
                "Instagram account is not a supported professional account.",
            )

        return {
            "status": "ok",
            "id": actual_user_id,
            "username": str(data.get("username") or ""),
            "account_type": account_type,
        }
