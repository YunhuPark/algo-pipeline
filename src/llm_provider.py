"""Shared text-LLM provider construction with safe automatic fallback.

The primary provider remains OpenAI.  When a configured primary provider call
fails for transport/auth/rate-limit/server reasons, LangChain retries the same
request once through an OpenAI-compatible fallback endpoint.  Fallback is
opt-in-by-key: it is active only when ``GEMINI_API_KEY`` (or the generic
``LLM_FALLBACK_API_KEY``) is present and ``LLM_FALLBACK_ENABLED`` is not false.

Application/schema/quality errors are deliberately *not* fallback triggers.
Those must keep failing closed in the existing Quality Gate.
"""
from __future__ import annotations

import os
from typing import Any

from langchain_openai import ChatOpenAI
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


def _model_kwargs(json_mode: bool) -> dict[str, Any]:
    if not json_mode:
        return {}
    return {"response_format": {"type": "json_object"}}


def _build_primary(
    *,
    model: str,
    temperature: float,
    request_timeout: float,
    json_mode: bool,
    api_key: str | None,
):
    return ChatOpenAI(
        model=model,
        temperature=temperature,
        max_retries=1,
        request_timeout=request_timeout,
        api_key=api_key or os.getenv("OPENAI_API_KEY"),
        model_kwargs=_model_kwargs(json_mode),
    )


def _build_fallback(
    *,
    temperature: float,
    request_timeout: float,
    json_mode: bool,
):
    if not fallback_is_configured():
        return None

    return ChatOpenAI(
        model=os.getenv("LLM_FALLBACK_MODEL", DEFAULT_FALLBACK_MODEL).strip()
        or DEFAULT_FALLBACK_MODEL,
        temperature=temperature,
        max_retries=0,
        request_timeout=request_timeout,
        api_key=_fallback_api_key(),
        base_url=(
            os.getenv("LLM_FALLBACK_BASE_URL", GEMINI_OPENAI_BASE_URL).strip()
            or GEMINI_OPENAI_BASE_URL
        ),
        model_kwargs=_model_kwargs(json_mode),
    )


def build_chat_model(
    *,
    model: str | None = None,
    temperature: float = 0.0,
    request_timeout: float = 20.0,
    json_mode: bool = False,
    api_key: str | None = None,
):
    """Build the primary text model with provider-only fallback semantics.

    The fallback handles only provider-level failures listed in
    ``PROVIDER_FALLBACK_EXCEPTIONS``.  Bad prompts, parsing failures and Quality
    Gate exceptions are never hidden by switching providers.
    """

    primary = _build_primary(
        model=model or os.getenv("LLM_MODEL", "gpt-4o"),
        temperature=temperature,
        request_timeout=request_timeout,
        json_mode=json_mode,
        api_key=api_key,
    )
    fallback = _build_fallback(
        temperature=temperature,
        request_timeout=request_timeout,
        json_mode=json_mode,
    )
    if fallback is None:
        return primary

    return primary.with_fallbacks(
        [fallback],
        exceptions_to_handle=PROVIDER_FALLBACK_EXCEPTIONS,
    )


def build_structured_chat_model(
    schema,
    *,
    model: str | None = None,
    temperature: float = 0.0,
    request_timeout: float = 20.0,
    api_key: str | None = None,
):
    """Build a Pydantic-structured model with the same provider fallback.

    Structured-output wrapping happens independently per provider before the
    fallback wrapper is attached.  This prevents fallback from weakening the
    caller's response schema.
    """

    primary = _build_primary(
        model=model or os.getenv("LLM_MODEL", "gpt-4o"),
        temperature=temperature,
        request_timeout=request_timeout,
        json_mode=False,
        api_key=api_key,
    ).with_structured_output(schema)

    fallback_model = _build_fallback(
        temperature=temperature,
        request_timeout=request_timeout,
        json_mode=False,
    )
    if fallback_model is None:
        return primary

    fallback = fallback_model.with_structured_output(schema)
    return primary.with_fallbacks(
        [fallback],
        exceptions_to_handle=PROVIDER_FALLBACK_EXCEPTIONS,
    )
