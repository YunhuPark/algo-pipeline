"""Transparent provider-aware fallback for every ``ChatOpenAI`` call under ``src``.

OpenAI remains the primary provider. If a provider-level failure occurs and a
fallback key is configured, the exact same request is retried once against an
OpenAI-compatible fallback endpoint (Gemini by default). Application/schema/
Quality-Gate errors are never swallowed by this layer.
"""
from __future__ import annotations

import os

import langchain_openai
from langchain_openai import ChatOpenAI as _OriginalChatOpenAI
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
)


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
        except PROVIDER_FALLBACK_EXCEPTIONS as primary_error:
            fallback = self._fallback_model()
            if fallback is None:
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
        except PROVIDER_FALLBACK_EXCEPTIONS as primary_error:
            fallback = self._fallback_model()
            if fallback is None:
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
