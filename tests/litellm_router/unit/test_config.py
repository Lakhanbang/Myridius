"""Unit tests for :class:`litellm_router.config.AppConfig`."""

import pytest

from litellm_router.config import AppConfig


def test_get_redis_config_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_HOST", "cache.internal")
    monkeypatch.setenv("REDIS_PORT", "6380")
    monkeypatch.setenv("REDIS_PASSWORD", "secret")
    config = AppConfig.get_redis_config()
    assert config == {
        "host": "cache.internal",
        "port": 6380,
        "password": "secret",
    }


def test_get_redis_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("REDIS_HOST", "REDIS_PORT", "REDIS_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    config = AppConfig.get_redis_config()
    assert config["host"] == "localhost"
    assert config["port"] == 6379
    assert config["password"] is None


def test_normalize_model_for_cost_prefixes_bare_llama() -> None:
    assert AppConfig.normalize_model_for_cost("llama-3.3-70b") == AppConfig.GROQ_MODEL


def test_normalize_model_for_cost_leaves_prefixed_groq_untouched() -> None:
    assert AppConfig.normalize_model_for_cost(AppConfig.GROQ_MODEL) == AppConfig.GROQ_MODEL


def test_normalize_model_for_cost_leaves_other_models_untouched() -> None:
    assert AppConfig.normalize_model_for_cost(AppConfig.OPENAI_MODEL) == AppConfig.OPENAI_MODEL
