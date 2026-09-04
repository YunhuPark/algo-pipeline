"""Read-only Instagram credential and public-image configuration preflight."""
from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env", override=True)


def main() -> int:
    from src.agents.publisher import (
        PublishConfigurationError,
        validate_publish_config,
        verify_instagram_account,
    )

    try:
        _, use_catbox = validate_publish_config()
        account = verify_instagram_account()
    except PublishConfigurationError as exc:
        print(f"Instagram preflight blocked: {exc}", file=sys.stderr)
        return 2

    delivery = "explicit third-party upload" if use_catbox else "configured HTTPS base URL"
    username = account.get("username") or "verified account"
    account_type = account.get("account_type") or "professional"
    print(f"Instagram preflight passed: @{username} ({account_type})")
    print(f"Image delivery: {delivery}")
    print("No database write, media upload, container creation, or publish occurred.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
