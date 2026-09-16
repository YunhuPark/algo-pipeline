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
from dotenv import dotenv_values, load_dotenv
from langchain_openai import ChatOpenAI as _OriginalChatOpenAI
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
)


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PROJECT_ENV_PATH = _PROJECT_ROOT / ".env"

# Load once for normal application behavior.  _fallback_api_key() also reads
# the file directly at call time so a blank/inherited environment variable or
# import-order difference cannot hide a valid project-local Gemini key.
load_dotenv(_PROJECT_ENV_PATH, override=False)

GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_FALLBACK_MODEL = "gemini-3.8-flash"
DEFAULT_FALLBACK_TIMEOUT_SECONDS = 90.0
MIN_FALLBACK_TIMEOUT_SECONDS = 20.0
MAX_FALLBACK_TIMEOUT_SECONDS = 180.0

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


def _project_env_values() -> dict[str, str | None]:
    """Read the repository .env without mutating process environment."""

    if not _PROJECT_ENV_PATH.is_file():
        return {}
    try:
        return dict(dotenv_values(_PROJECT_ENV_PATH))
    except (OSError, UnicodeError):
        return {}


def _setting(name: str, default: str = "") -> str:
    """Prefer a non-empty process value, then the project-local .env value."""

    process_value = os.getenv(name)
    if process_value is not None and process_value.strip():
        return process_value.strip()
    file_value = _project_env_values().get(name)
    if file_value is not None and str(file_value).strip():
        return str(file_value).strip()
    return default


def _fallback_enabled() -> bool:
    configured = _setting("LLM_FALLBACK_ENABLED", "true").casefold()
    return configured not in _FALSE_VALUES


def _fallback_api_key() -> str:
    return _setting("LLM_FALLBACK_API_KEY") or _setting("GEMINI_API_KEY")


def _fallback_timeout_seconds() -> float:
    """Return a bounded timeout appropriate for full production prompts.

    The primary OpenAI path historically used a 20 second timeout.  Gemini
    fallback requests often carry the already-expanded evidence prompt and can
    legitimately take longer.  Keep the value configurable but bounded so a
    provider failure cannot hang the pipeline indefinitely.
    """

    raw = _setting(
        "LLM_FALLBACK_TIMEOUT_SECONDS",
        str(DEFAULT_FALLBACK_TIMEOUT_SECONDS),
    )
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = DEFAULT_FALLBACK_TIMEOUT_SECONDS
    return max(MIN_FALLBACK_TIMEOUT_SECONDS, min(value, MAX_FALLBACK_TIMEOUT_SECONDS))


def fallback_is_configured() -> bool:
    """Return whether automatic provider fallback can actually run."""

    return _fallback_enabled() and bool(_fallback_api_key())


def fallback_diagnostics() -> str:
    """Return secret-safe diagnostics for local startup/error logs."""

    values = _project_env_values()
    process_key = bool((os.getenv("LLM_FALLBACK_API_KEY") or "").strip()) or bool(
        (os.getenv("GEMINI_API_KEY") or "").strip()
    )
    file_key = bool(str(values.get("LLM_FALLBACK_API_KEY") or "").strip()) or bool(
        str(values.get("GEMINI_API_KEY") or "").strip()
    )
    return (
        f"env_file={_PROJECT_ENV_PATH} exists={_PROJECT_ENV_PATH.is_file()} "
        f"process_key={'set' if process_key else 'missing'} "
        f"file_key={'set' if file_key else 'missing'} "
        f"enabled={_fallback_enabled()} timeout={_fallback_timeout_seconds():g}s"
    )


def _is_provider_failure(exc: Exception) -> bool:
    """Recognize provider failures even when an integration wraps SDK errors."""

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


def _safe_status_code(exc: Exception) -> int | None:
    status_code = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    if status_code is None and response is not None:
        status_code = getattr(response, "status_code", None)
    return status_code if isinstance(status_code, int) else None


class ProviderAwareChatOpenAI(_OriginalChatOpenAI):
    """Drop-in ChatOpenAI that retries provider failures on Gemini."""

    def _fallback_model(self):
        if not fallback_is_configured():
            return None

        return _OriginalChatOpenAI(
            model=_setting("LLM_FALLBACK_MODEL", DEFAULT_FALLBACK_MODEL),
            temperature=self.temperature if self.temperature is not None else 0.0,
            # One SDK-level retry is allowed for transient connection/timeout
            # failures.  Quality/schema errors are not retried here.
            max_retries=1,
            request_timeout=_fallback_timeout_seconds(),
            api_key=_fallback_api_key(),
            base_url=_setting("LLM_FALLBACK_BASE_URL", GEMINI_OPENAI_BASE_URL),
            model_kwargs=dict(getattr(self, "model_kwargs", {}) or {}),
        )

    @staticmethod
    def _raise_fallback_error(fallback, fallback_error: Exception):
        status = _safe_status_code(fallback_error)
        status_text = f", status={status}" if status is not None else ""
        print(
            "[LLMProvider] Gemini fallback 실패 "
            f"(model={fallback.model_name}, error={type(fallback_error).__name__}"
            f"{status_text}, timeout={_fallback_timeout_seconds():g}s)"
        )
        raise fallback_error

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
                    "[LLMProvider] provider 오류 감지, 그러나 Gemini fallback 키를 사용할 수 없습니다. "
                    + fallback_diagnostics()
                )
                raise
            print(
                "[LLMProvider] OpenAI provider 실패 → "
                f"{fallback.model_name} fallback 실행 ({type(primary_error).__name__}, "
                f"timeout={_fallback_timeout_seconds():g}s)"
            )
            try:
                return fallback._generate(
                    messages,
                    stop=stop,
                    run_manager=run_manager,
                    **kwargs,
                )
            except Exception as fallback_error:
                self._raise_fallback_error(fallback, fallback_error)

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
                    "[LLMProvider] provider 오류 감지, 그러나 Gemini fallback 키를 사용할 수 없습니다. "
                    + fallback_diagnostics()
                )
                raise
            print(
                "[LLMProvider] OpenAI provider 실패 → "
                f"{fallback.model_name} fallback 실행 ({type(primary_error).__name__}, "
                f"timeout={_fallback_timeout_seconds():g}s)"
            )
            try:
                return await fallback._agenerate(
                    messages,
                    stop=stop,
                    run_manager=run_manager,
                    **kwargs,
                )
            except Exception as fallback_error:
                self._raise_fallback_error(fallback, fallback_error)


def install_global_chatopenai_fallback() -> None:
    """Patch ``langchain_openai.ChatOpenAI`` once before src submodules load."""

    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return
    langchain_openai.ChatOpenAI = ProviderAwareChatOpenAI
    _PATCH_INSTALLED = True
