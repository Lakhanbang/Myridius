"""Unit tests for :class:`litellm_router.utils.env_loader.EnvLoader`."""

import pytest

from litellm_router.utils.env_loader import EnvLoader


def test_get_var_returns_environment_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_KEY", "my_value")
    assert EnvLoader.get_var("MY_KEY") == "my_value"


def test_get_var_returns_default_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_KEY", raising=False)
    assert EnvLoader.get_var("MISSING_KEY", "fallback") == "fallback"
