from __future__ import annotations

import os
import secrets
from urllib.parse import urlparse

from fastapi import Header, HTTPException


def require_admin_token(x_admin_token: str | None = Header(default=None)) -> str:
    expected = os.getenv("ADMIN_TOKEN", "")
    if not expected or not x_admin_token or not secrets.compare_digest(
        x_admin_token, expected
    ):
        raise HTTPException(status_code=401, detail="Invalid admin token")
    return "test_human" if os.getenv("ALGO_ENV", "").lower() == "test" else "human"


def verify_origin(
    origin: str | None = Header(default=None),
    referer: str | None = Header(default=None),
) -> None:
    source = origin or referer
    if not source:
        raise HTTPException(status_code=403, detail="Invalid Origin: missing")
    parsed = urlparse(source)
    allowed_hosts = {"127.0.0.1", "localhost"}
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in allowed_hosts:
        raise HTTPException(status_code=403, detail="Invalid Origin")
