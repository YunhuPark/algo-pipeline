from __future__ import annotations

import importlib
import os
from pathlib import Path
from unittest.mock import patch

import pytest


def _publisher(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    module = importlib.import_module("src.agents.publisher")
    monkeypatch.setattr(module, "IG_ACCESS_TOKEN", "ig-test-token")
    monkeypatch.setattr(module, "IG_USER_ID", "ig-test-user")
    return module


def test_empty_public_url_fails_closed(monkeypatch):
    publisher = _publisher(monkeypatch)
    monkeypatch.setattr(publisher, "IG_IMAGE_BASE_URL", "")

    with pytest.raises(publisher.PublishConfigurationError, match="IG_IMAGE_BASE_URL"):
        publisher.validate_publish_config()


@pytest.mark.parametrize("configured", ["catbox", "catbox://"])
def test_catbox_requires_explicit_configuration(monkeypatch, configured):
    publisher = _publisher(monkeypatch)
    monkeypatch.setattr(publisher, "IG_IMAGE_BASE_URL", configured)

    assert publisher.validate_publish_config() == ("", True)


def test_https_base_url_is_normalized(monkeypatch):
    publisher = _publisher(monkeypatch)
    monkeypatch.setattr(publisher, "IG_IMAGE_BASE_URL", "https://images.example.test/root/")

    assert publisher.validate_publish_config() == (
        "https://images.example.test/root",
        False,
    )


@pytest.mark.parametrize(
    "configured",
    ["https://", "http://insecure.test", "file:///tmp/images", "ftp://example.test"],
)
def test_non_https_public_url_is_rejected(monkeypatch, configured):
    publisher = _publisher(monkeypatch)
    monkeypatch.setattr(publisher, "IG_IMAGE_BASE_URL", configured)

    with pytest.raises(publisher.PublishConfigurationError, match="https://"):
        publisher.validate_publish_config()


def test_publish_rejects_empty_url_before_network(monkeypatch, tmp_path: Path):
    publisher = _publisher(monkeypatch)
    monkeypatch.setattr(publisher, "IG_IMAGE_BASE_URL", "")
    image = tmp_path / "card.png"

    with patch.object(publisher, "_upload_to_catbox") as catbox, patch.object(
        publisher.httpx, "post"
    ) as http_post, pytest.raises(
        publisher.PublishConfigurationError, match="IG_IMAGE_BASE_URL"
    ):
        publisher.publish([image], "hook", ["#tag"])

    catbox.assert_not_called()
    http_post.assert_not_called()


def test_remote_account_preflight_uses_instagram_login_and_matches_id(monkeypatch):
    publisher = _publisher(monkeypatch)
    calls = []

    def client(url, params):
        calls.append((url, params))
        return {
            "id": "ig-test-user",
            "username": "verified_account",
            "account_type": "BUSINESS",
        }

    result = publisher.verify_instagram_account(http_client=client)

    assert result["status"] == "ok"
    assert calls[0][0] == "https://graph.instagram.com/v21.0/me"
    assert calls[0][1]["fields"] == "id,username,account_type"


def test_remote_account_mismatch_fails_closed_without_token_leak(monkeypatch):
    publisher = _publisher(monkeypatch)
    secret = "IGAA-this-must-never-appear"
    monkeypatch.setattr(publisher, "IG_ACCESS_TOKEN", secret)

    with pytest.raises(publisher.PublishConfigurationError) as exc:
        publisher.verify_instagram_account(
            http_client=lambda _url, _params: {
                "id": "different-user",
                "account_type": "BUSINESS",
            }
        )

    assert "ACCOUNT_MISMATCH" in str(exc.value)
    assert secret not in str(exc.value)


@pytest.mark.parametrize(
    "payload,error_code",
    [
        ({"error": {"code": 190, "error_subcode": 460}}, "TOKEN_EXPIRED"),
        ({"error": {"code": 190}}, "TOKEN_INVALID"),
        ({"error": {"code": 10}}, "PERMISSION_DENIED"),
    ],
)
def test_remote_account_errors_are_sanitized(monkeypatch, payload, error_code):
    publisher = _publisher(monkeypatch)
    secret = "IGAA-this-must-never-appear"
    monkeypatch.setattr(publisher, "IG_ACCESS_TOKEN", secret)

    with pytest.raises(publisher.PublishConfigurationError) as exc:
        publisher.verify_instagram_account(
            http_client=lambda _url, _params: payload
        )

    assert error_code in str(exc.value)
    assert secret not in str(exc.value)
