"""Transparent provider-aware fallback for every ``ChatOpenAI`` call under ``src``.

OpenAI remains the primary provider. If a provider-level failure occurs and a
fallback key is configured, the exact same request is retried once against an
OpenAI-compatible fallback endpoint (Gemini by default). Application/schema/
Quality-Gate errors are never swallowed by this layer.
"""
from __future__ import annotations

import os
from pathlib import Path

import langchain_openai
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI as _OriginalChatOpenAI
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
)


# Load the project-local .env here, before any provider fallback decision is
# made. src.__init__ installs this module before src.config is guaranteed to be
# imported, so relying on config.py to load GEMINI_API_KEY was timing-sensitive.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env", override=False)

GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_FALLBACK_MODEL = "gemini-3.8-flash"

PROVIDER_FALLBACK_EXCEPTIONS = (
    RateLimitError,
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    PermissionDeniedError,
    InternalServerError,
)

_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}
_PATCH_INSTALLED = False


def _fallback_enabled() -> bool:
    configured = os.getenv("LLM_FALLBACK_ENABLED", "true").strip().casefold()
    return configured not in _FALSE_VALUES


def _fallback_api_key() -> str:
    return (
        os.getenv("LLM_FALLBACK_API_KEY", "").strip()
        or os.getenv("GEMINI_API_KEY", "").strip()
    )


def fallback_is_configured() -> bool:
    """Return whether automatic provider fallback can actually run."""

    return _fallback_enabled() and bool(_fallback_api_key())


def _is_provider_failure(exc: Exception) -> bool:
    """Recognize provider failures even when an integration wraps SDK errors.

    Some langchain/openai version combinations expose wrapper class names such
    as ``OpenAIRateLimitError`` rather than ``openai.RateLimitError`` itself.
    Accept only transport/auth/quota/server-like failures; parsing, validation
    and application exceptions remain outside the fallback path.
    """

    if isinstance(exc, PROVIDER_FALLBACK_EXCEPTIONS):
        return True

    status_code = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    if status_code is None and response is not None:
        status_code = getattr(response, "status_code", None)
    if status_code in {401, 403, 408, 429, 500, 502, 503, 504}:
        return True

    name = type(exc).__name__.casefold()
    text = str(exc).casefold()
    markers = (
        "ratelimit",
        "rate limit",
        "too many requests",
        "apiconnection",
        "api connection",
        "apitimeout",
        "api timeout",
        "authentication",
        "permissiondenied",
        "permission denied",
        "internalserver",
        "internal server",
        "serviceunavailable",
        "service unavailable",
        "badgateway",
        "bad gateway",
        "gatewaytimeout",
        "gateway timeout",
    )
    return any(marker in name or marker in text for marker in markers)


class ProviderAwareChatOpenAI(_OriginalChatOpenAI):
    """Drop-in ChatOpenAI that retries provider failures on Gemini.

    Subclassing the original LangChain model preserves existing
    ``with_structured_output()``, prompt piping, ``bind()`` and direct
    ``invoke()`` behavior without changing every existing call site.
    """

    def _fallback_model(self):
        if not fallback_is_configured():
            return None

        return _OriginalChatOpenAI(
            model=(
                os.getenv("LLM_FALLBACK_MODEL", DEFAULT_FALLBACK_MODEL).strip()
                or DEFAULT_FALLBACK_MODEL
            ),
            temperature=self.temperature if self.temperature is not None else 0.0,
            max_retries=0,
            request_timeout=20.0,
            api_key=_fallback_api_key(),
            base_url=(
                os.getenv("LLM_FALLBACK_BASE_URL", GEMINI_OPENAI_BASE_URL).strip()
                or GEMINI_OPENAI_BASE_URL
            ),
            model_kwargs=dict(getattr(self, "model_kwargs", {}) or {}),
        )

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        try:
            return super()._generate(
                messages,
                stop=stop,
                run_manager=run_manager,
                **kwargs,
            )
        except Exception as primary_error:
            if not _is_provider_failure(primary_error):
                raise
            fallback = self._fallback_model()
            if fallback is None:
                print(
                    "[LLMProvider] provider 오류 감지, 그러나 Gemini fallback 키가 로드되지 않았습니다."
                )
                raise
            print(
                "[LLMProvider] OpenAI provider 실패 → "
                f"{fallback.model_name} fallback 실행 ({type(primary_error).__name__})"
            )
            return fallback._generate(
                messages,
                stop=stop,
                run_manager=run_manager,
                **kwargs,
            )

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        try:
            return await super()._agenerate(
                messages,
                stop=stop,
                run_manager=run_manager,
                **kwargs,
            )
        except Exception as primary_error:
            if not _is_provider_failure(primary_error):
                raise
            fallback = self._fallback_model()
            if fallback is None:
                print(
                    "[LLMProvider] provider 오류 감지, 그러나 Gemini fallback 키가 로드되지 않았습니다."
                )
                raise
            print(
                "[LLMProvider] OpenAI provider 실패 → "
                f"{fallback.model_name} fallback 실행 ({type(primary_error).__name__})"
            )
            return await fallback._agenerate(
                messages,
                stop=stop,
                run_manager=run_manager,
                **kwargs,
            )


def install_global_chatopenai_fallback() -> None:
    """Patch ``langchain_openai.ChatOpenAI`` once before src submodules load."""

    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return
    langchain_openai.ChatOpenAI = ProviderAwareChatOpenAI
    _PATCH_INSTALLED = True
