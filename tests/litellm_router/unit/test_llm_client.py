"""Unit tests for the LiteLLM wrapper (LLMClient)."""

import pytest
from litellm.exceptions import APIConnectionError, RateLimitError, Timeout

from litellm_router.utils import llm_client as llm_client_module
from litellm_router.utils.llm_client import LLMClient, LLMError


def _build(exc_cls, message):
    """Build a LiteLLM error, allowing for different constructor shapes."""
    try:
        return exc_cls(message=message, llm_provider="groq", model="m")
    except TypeError:
        return exc_cls(message)


def test_completion_returns_litellm_result(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(
        llm_client_module.litellm, "completion", lambda **kwargs: sentinel
    )
    result = LLMClient.completion(model="m", messages=[{"role": "user", "content": "hi"}])
    assert result is sentinel


@pytest.mark.parametrize("provider_error", [RateLimitError, APIConnectionError, Timeout])
def test_completion_turns_provider_errors_into_llm_error(monkeypatch, provider_error):
    def _raise(**kwargs):
        raise _build(provider_error, "boom")

    monkeypatch.setattr(llm_client_module.litellm, "completion", _raise)
    with pytest.raises(LLMError):
        LLMClient.completion(model="m", messages=[])
