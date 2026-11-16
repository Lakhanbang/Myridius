"""Unit tests for the routing engine."""

from types import SimpleNamespace

import pytest

from litellm_router.config import AppConfig
from litellm_router.routing.router import GatewayRouter
from litellm_router.utils.llm_client import LLMError


def _make_response(content: str = "ok", model: str = AppConfig.OPENAI_MODEL):
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice], model=model, usage=SimpleNamespace(total_tokens=10))


def test_classify_task_matches_keyword(user_messages) -> None:
    assert GatewayRouter().classify_task(user_messages) == "coding"


def test_classify_task_returns_default_for_unknown() -> None:
    messages = [{"role": "user", "content": "Hello there"}]
    assert GatewayRouter().classify_task(messages) == "default"


def test_completion_routes_to_first_in_pool(
    monkeypatch: pytest.MonkeyPatch, user_messages
) -> None:
    monkeypatch.setattr(
        "litellm_router.routing.router.LLMClient.completion",
        lambda **kwargs: _make_response(model=kwargs["model"]),
    )
    # "Write a python function" → coding → first in pool is OPENAI_MODEL
    response = GatewayRouter().completion(messages=user_messages)
    assert response.model == AppConfig.OPENAI_MODEL


def test_completion_falls_back_when_first_fails(
    monkeypatch: pytest.MonkeyPatch, user_messages
) -> None:
    tried = []

    def fake_llm(**kwargs):
        tried.append(kwargs["model"])
        if kwargs["model"] == AppConfig.OPENAI_MODEL:
            raise LLMError("rate limited")
        return _make_response(model=kwargs["model"])

    monkeypatch.setattr("litellm_router.routing.router.LLMClient.completion", fake_llm)
    response = GatewayRouter().completion(messages=user_messages)
    assert tried[0] == AppConfig.OPENAI_MODEL   # tried first
    assert response.model != AppConfig.OPENAI_MODEL  # used a fallback


def test_completion_uses_single_model_directly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Only OpenAI key set — should call it directly without classifying.
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("GROQ_API_KEY", "")
    called_with = []

    def fake_llm(**kwargs):
        called_with.append(kwargs["model"])
        return _make_response(model=kwargs["model"])

    monkeypatch.setattr("litellm_router.routing.router.LLMClient.completion", fake_llm)
    messages = [{"role": "user", "content": "Hello"}]
    GatewayRouter().completion(messages=messages)
    assert called_with == [AppConfig.OPENAI_MODEL]


def test_completion_raises_when_all_models_fail(
    monkeypatch: pytest.MonkeyPatch, user_messages
) -> None:
    monkeypatch.setattr(
        "litellm_router.routing.router.LLMClient.completion",
        lambda **kwargs: (_ for _ in ()).throw(LLMError("rate limited")),
    )
    with pytest.raises(RuntimeError, match="All models"):
        GatewayRouter().completion(messages=user_messages)
