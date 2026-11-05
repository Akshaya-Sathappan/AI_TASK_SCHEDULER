"""Which LLM provider the planner, the workers and the benchmarks call (D60).

    ATS_LLM_PROVIDER   groq (default; needs GROQ_API_KEY) | hf (Hugging Face Inference Providers; needs HF_TOKEN)
    ATS_HF_PROVIDER    hf only: the backend Hugging Face routes to, appended to the model id as
                       "<model>:<backend>" (cerebras, fireworks-ai, groq, ...). Empty (default): Hugging
                       Face picks one, in the order set at huggingface.co/settings/inference-providers.

Hugging Face's router speaks the OpenAI chat-completions API at HF_BASE_URL, so the hf clients are the
OpenAI SDK's. Both SDKs have the same `chat.completions.create` call and the same exception names
(RateLimitError, APITimeoutError, ...), so the callers and the worker's error classification (8.6) do
not change. The model ids are the same on both (openai/gpt-oss-120b, openai/gpt-oss-20b).
"""

from __future__ import annotations

import os
from typing import Any

PROVIDER_ENV = "ATS_LLM_PROVIDER"
HF_ROUTE_ENV = "ATS_HF_PROVIDER"
HF_TOKEN_ENV = "HF_TOKEN"
HF_BASE_URL = "https://router.huggingface.co/v1"
HF_BASE_URL_ENV = "ATS_HF_BASE_URL"  # override for tests or a dedicated Inference Endpoint
PROVIDERS = ("groq", "hf")


def provider(env: dict[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    name = (env.get(PROVIDER_ENV) or "groq").strip().lower()
    if name not in PROVIDERS:
        raise ValueError(f"{PROVIDER_ENV} must be one of {', '.join(PROVIDERS)} (got {name!r})")
    return name


def model_name(model: str, env: dict[str, str] | None = None) -> str:
    """The model id to send: on hf with ATS_HF_PROVIDER set, "<model>:<backend>" (unless it has one)."""
    env = os.environ if env is None else env
    if provider(env) != "hf":
        return model
    route = (env.get(HF_ROUTE_ENV) or "").strip()
    return f"{model}:{route}" if route and ":" not in model else model


def _hf_kwargs() -> dict[str, Any]:
    token = os.environ.get(HF_TOKEN_ENV)
    if not token:
        raise RuntimeError(f"{PROVIDER_ENV}=hf needs {HF_TOKEN_ENV} (huggingface.co/settings/tokens) in .env")
    return {"base_url": os.environ.get(HF_BASE_URL_ENV) or HF_BASE_URL, "api_key": token}


def sync_client(max_retries: int = 2, **kw: Any) -> Any:
    """The planner's client."""
    if provider() == "hf":
        from openai import OpenAI

        return OpenAI(max_retries=max_retries, **_hf_kwargs(), **kw)
    from groq import Groq  # reads GROQ_API_KEY

    return Groq(max_retries=max_retries)


def async_client(max_retries: int = 0, **kw: Any) -> Any:
    """The workers' and benchmarks' client. No SDK retries by default: atsd owns retries (8.6, D12).
    `kw` goes to the hf client only (tests pass an http_client)."""
    if provider() == "hf":
        from openai import AsyncOpenAI

        return AsyncOpenAI(max_retries=max_retries, **_hf_kwargs(), **kw)
    from groq import AsyncGroq  # reads GROQ_API_KEY

    return AsyncGroq(max_retries=max_retries)


def provider_errors() -> tuple[type[BaseException], ...]:
    """Exceptions that mean "the provider failed" (missing key, auth, rate limit, network), any provider."""
    errors: list[type[BaseException]] = []
    try:
        from groq import GroqError

        errors.append(GroqError)
    except ImportError:
        pass
    try:
        from openai import OpenAIError

        errors.append(OpenAIError)
    except ImportError:
        pass
    return tuple(errors)


__all__ = ["HF_BASE_URL", "PROVIDER_ENV", "async_client", "model_name", "provider", "provider_errors", "sync_client"]
