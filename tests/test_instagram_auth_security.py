from __future__ import annotations

import importlib
import re
import urllib.parse
from pathlib import Path

import pytest

from src.api.preflight import IGPreflightCheck, PreflightError, redact_sensitive


ROOT = Path(__file__).parents[1]
AUTH_FILES = [
    ROOT / "scripts" / "get_ig_token.py",
    ROOT / "scripts" / "refresh_ig_token.py",
    ROOT / "setup_instagram.py",
    ROOT / "src" / "agents" / "publisher.py",
    ROOT / "src" / "api" / "preflight.py",
]


def test_auth_helpers_have_no_embedded_app_secret_or_facebook_login_flow():
    source = "\n".join(path.read_text(encoding="utf-8") for path in AUTH_FILES)
    assert not re.search(r"APP_SECRET\s*=\s*['\"][0-9a-fA-F]{24,}['\"]", source)
    assert "graph.facebook.com" not in source
    assert "fb_exchange_token" not in source
    assert "instagram_basic," not in source
    assert "instagram_content_publish" not in source.replace(
        "instagram_business_content_publish", ""
    )


def test_auth_helpers_do_not_print_tokens_codes_or_provider_payloads():
    oauth = (ROOT / "scripts" / "get_ig_token.py").read_text(encoding="utf-8")
    refresh = (ROOT / "scripts" / "refresh_ig_token.py").read_text(encoding="utf-8")
    forbidden = [
        "print(data",
        "print(response",
        "print(short_token",
        "print(long_token",
        "print(final_token",
        "token[:",
        "code[:",
    ]
    for text in (oauth, refresh):
        assert all(pattern not in text for pattern in forbidden)


def test_env_example_has_blank_instagram_secret_placeholders():
    values = {}
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    for key in (
        "META_APP_ID",
        "META_APP_SECRET",
        "IG_REDIRECT_URI",
        "IG_ACCESS_TOKEN",
        "IG_USER_ID",
        "IG_IMAGE_BASE_URL",
    ):
        assert values[key] == ""


def test_authorization_url_uses_minimal_instagram_login_scopes():
    oauth = importlib.import_module("scripts.get_ig_token")
    url = oauth.build_authorization_url(
        "app-id", "https://callback.example.test/ig", "csrf-state"
    )
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    assert parsed.netloc == "www.instagram.com"
    assert query["scope"] == [
        "instagram_business_basic,instagram_business_content_publish"
    ]
    assert query["state"] == ["csrf-state"]
    assert query["enable_fb_login"] == ["0"]


def test_callback_url_must_match_redirect_and_state():
    oauth = importlib.import_module("scripts.get_ig_token")
    redirect = "https://callback.example.test/ig"
    good = f"{redirect}?code=one-time-code&state=csrf-state"
    assert oauth.parse_callback_url(good, redirect, "csrf-state") == "one-time-code"

    with pytest.raises(oauth.OAuthSetupError, match="state mismatch"):
        oauth.parse_callback_url(good, redirect, "different-state")
    with pytest.raises(oauth.OAuthSetupError, match="does not match"):
        oauth.parse_callback_url(
            "https://attacker.example.test/ig?code=x&state=csrf-state",
            redirect,
            "csrf-state",
        )


def test_preflight_matches_account_and_professional_type():
    calls = []

    def client(url, params):
        calls.append((url, params))
        return {"id": "123", "username": "algo", "account_type": "BUSINESS"}

    result = IGPreflightCheck(client).check_account("IGAA-secret-value", "123")
    assert result == {
        "status": "ok",
        "id": "123",
        "username": "algo",
        "account_type": "BUSINESS",
    }
    assert calls[0][0] == "https://graph.instagram.com/v21.0/me"


def test_preflight_never_surfaces_token_or_provider_message():
    token = "IGAA-super-secret-token"

    def client(_url, _params):
        return {
            "error": {
                "code": 190,
                "error_subcode": 460,
                "message": f"expired {token}",
            }
        }

    with pytest.raises(PreflightError) as exc:
        IGPreflightCheck(client).check_account(token, "123")
    assert exc.value.code == "TOKEN_EXPIRED"
    assert token not in str(exc.value)
    assert f"expired {token}" not in str(exc.value)
    assert token not in redact_sensitive(f"failure {token}", token)


@pytest.mark.parametrize("account_type", ["", "PERSONAL"])
def test_preflight_rejects_non_professional_or_missing_account_type(account_type):
    client = lambda _url, _params: {"id": "123", "account_type": account_type}
    with pytest.raises(PreflightError) as exc:
        IGPreflightCheck(client).check_account("IGAA-secret", "123")
    assert exc.value.code == "ACCOUNT_NOT_PROFESSIONAL"


def test_removed_dashboard_callback_cannot_cache_oauth_codes():
    source = (ROOT / "src" / "dashboard" / "app.py").read_text(encoding="utf-8")
    assert "_ig_oauth_code" not in source
    assert 'route("/callback/code")' not in source


def test_auth_runbook_requires_rotation_and_prohibits_secret_sharing():
    runbook = (ROOT / "docs" / "runbooks" / "instagram-auth-setup.md").read_text(
        encoding="utf-8"
    )
    assert "Rotate the Meta app secret" in runbook
    assert "Revoke old Instagram access tokens" in runbook
    assert "Do not paste the replacement secret or token" in runbook
    assert "python scripts/ig_preflight.py" in runbook
