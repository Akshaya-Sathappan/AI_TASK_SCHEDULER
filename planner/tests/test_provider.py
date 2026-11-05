"""ATS_LLM_PROVIDER (D60): Groq by default, Hugging Face's OpenAI-compatible router with hf."""

import pytest

from planner import provider


def test_default_is_groq_and_model_ids_unchanged():
    assert provider.provider({}) == "groq"
    assert provider.model_name("openai/gpt-oss-120b", {"ATS_HF_PROVIDER": "cerebras"}) == "openai/gpt-oss-120b"


def test_unknown_provider_is_an_error():
    with pytest.raises(ValueError, match="ATS_LLM_PROVIDER"):
        provider.provider({"ATS_LLM_PROVIDER": "openrouter"})


def test_hf_appends_the_backend_once():
    env = {"ATS_LLM_PROVIDER": "HF", "ATS_HF_PROVIDER": "cerebras"}
    assert provider.model_name("openai/gpt-oss-120b", env) == "openai/gpt-oss-120b:cerebras"
    assert provider.model_name("openai/gpt-oss-20b:groq", env) == "openai/gpt-oss-20b:groq"
    assert provider.model_name("openai/gpt-oss-120b", {"ATS_LLM_PROVIDER": "hf"}) == "openai/gpt-oss-120b"


def test_hf_needs_a_token(monkeypatch):
    monkeypatch.setenv("ATS_LLM_PROVIDER", "hf")
    monkeypatch.delenv("HF_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="HF_TOKEN"):
        provider.async_client()


def test_hf_clients_point_at_the_router(monkeypatch):
    monkeypatch.setenv("ATS_LLM_PROVIDER", "hf")
    monkeypatch.setenv("HF_TOKEN", "hf_test")
    monkeypatch.delenv("ATS_HF_BASE_URL", raising=False)
    for client in (provider.sync_client(max_retries=0), provider.async_client()):
        assert str(client.base_url).rstrip("/") == provider.HF_BASE_URL
        assert client.max_retries == 0
    assert str(provider.sync_client().base_url).rstrip("/") == provider.HF_BASE_URL


def test_provider_errors_cover_both_sdks():
    import groq
    import openai

    errors = provider.provider_errors()
    assert issubclass(groq.RateLimitError, errors) and issubclass(openai.RateLimitError, errors)
