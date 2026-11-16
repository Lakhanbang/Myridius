"""Integration tests wiring the gateway end-to-end with fakes."""

from types import SimpleNamespace

import pytest

from litellm_router.gateway import LLMControlGateway


def _make_response(model: str = "gpt-4o-mini"):
    message = SimpleNamespace(content="ok")
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(
        choices=[choice], model=model, usage=SimpleNamespace(total_tokens=10)
    )


@pytest.fixture
def patched_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "litellm_router.routing.router.LLMClient.completion",
        lambda **kwargs: _make_response(model=kwargs["model"]),
    )
    monkeypatch.setattr(
        "litellm_router.gateway.LLMClient.get_cost", lambda completion_response: 0.001
    )


def test_generate_response_success(fake_redis, patched_llm) -> None:
    gateway = LLMControlGateway()
    result = gateway.generate_response("Write a python function")
    assert result.succeeded
    assert result.response == "ok"
    assert result.tokens_used == 10
    assert result.cost == pytest.approx(0.001)


def test_generate_response_blocked_by_budget(
    fake_redis, patched_llm, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAX_MONTHLY_BUDGET_USD", "0.00")
    gateway = LLMControlGateway()
    result = gateway.generate_response("Write a python function")
    assert not result.succeeded
    assert "Protection Triggered" in result.error
