from unittest.mock import patch

import httpx
import langchain_openai
import pytest
from openai import RateLimitError

from src.llm_provider import (
    GEMINI_OPENAI_BASE_URL,
    ProviderAwareChatOpenAI,
    _clear_primary_outage,
    _OriginalChatOpenAI,
    _primary_likely_down,
    fallback_is_configured,
)


@pytest.fixture(autouse=True)
def _reset_primary_outage_state():
    # 이 모듈 레벨 상태는 프로세스 전역이라, 한 테스트가 남긴 "OpenAI 장애"
    # 표시가 다음 테스트로 새어나가지 않도록 매번 초기화한다.
    _clear_primary_outage()
    yield
    _clear_primary_outage()


def _rate_limit_error() -> RateLimitError:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(429, request=request)
    return RateLimitError("rate limited", response=response, body=None)


def test_src_bootstrap_installs_provider_aware_chat_model():
    assert langchain_openai.ChatOpenAI is ProviderAwareChatOpenAI


def test_fallback_stays_disabled_without_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_FALLBACK_API_KEY", raising=False)
    monkeypatch.setenv("LLM_FALLBACK_ENABLED", "true")

    assert fallback_is_configured() is False


def test_fallback_can_be_explicitly_disabled(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setenv("LLM_FALLBACK_ENABLED", "false")

    assert fallback_is_configured() is False


def test_fallback_model_uses_gemini_compatible_endpoint(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setenv("LLM_FALLBACK_ENABLED", "true")
    monkeypatch.delenv("LLM_FALLBACK_API_KEY", raising=False)
    monkeypatch.delenv("LLM_FALLBACK_BASE_URL", raising=False)
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "gemini-test-model")

    primary = ProviderAwareChatOpenAI(
        model="gpt-4o",
        api_key="test-openai-key",
        temperature=0,
    )
    fallback = primary._fallback_model()

    assert fallback is not None
    assert fallback.model_name == "gemini-test-model"
    assert str(fallback.openai_api_base).rstrip("/") == GEMINI_OPENAI_BASE_URL.rstrip("/")


def test_second_call_skips_primary_after_a_rate_limit(monkeypatch):
    """A whole pipeline run makes dozens of small LLM calls. If OpenAI is out
    of quota, every one of them used to pay a full failing round trip against
    OpenAI before falling back - once we've actually seen it reject a call,
    later calls in the same cooldown window should go straight to the
    fallback instead of repeating a call we already know will fail."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setenv("LLM_FALLBACK_ENABLED", "true")
    monkeypatch.delenv("LLM_FALLBACK_API_KEY", raising=False)

    model = ProviderAwareChatOpenAI(model="gpt-4o", api_key="test-openai-key", temperature=0)
    assert _primary_likely_down() is False

    with patch.object(
        _OriginalChatOpenAI, "_generate", side_effect=_rate_limit_error()
    ) as primary_generate, patch(
        "src.llm_provider.ProviderAwareChatOpenAI._fallback_model"
    ) as get_fallback:
        fallback_stub = get_fallback.return_value
        fallback_stub._generate.return_value = "fallback-result"

        first = model._generate([])
        assert first == "fallback-result"
        assert primary_generate.call_count == 1
        assert _primary_likely_down() is True

        second = model._generate([])
        assert second == "fallback-result"
        # The whole point: the second call must not have retried the primary
        # model that we already know is rejecting requests right now.
        assert primary_generate.call_count == 1
        assert fallback_stub._generate.call_count == 2
