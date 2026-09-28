import langchain_openai

from src.llm_provider import (
    GEMINI_OPENAI_BASE_URL,
    ProviderAwareChatOpenAI,
    fallback_is_configured,
)


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
