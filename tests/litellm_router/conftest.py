"""Shared fixtures and test doubles for the gateway test suite."""

from typing import Any, Dict, List, Optional

import pytest

from litellm_router.utils.cache_client import CacheClient


class FakePipeline:
    """Minimal stand-in for a redis pipeline used by the rate limiter."""

    def __init__(self, store: "FakeRedis") -> None:
        self._store = store
        self._ops: List[tuple] = []

    def incr(self, key: str, amount: int = 1) -> "FakePipeline":
        self._ops.append(("incr", key, amount))
        return self

    def expire(self, key: str, ttl: int) -> "FakePipeline":
        self._ops.append(("expire", key, ttl))
        return self

    def execute(self) -> List[Any]:
        results = []
        for op, key, value in self._ops:
            if op == "incr":
                results.append(self._store.incr(key, value))
            else:
                results.append(self._store.expire(key, value))
        self._ops.clear()
        return results


class FakeRedis:
    """In-memory subset of the redis client used across the gateway."""

    def __init__(self) -> None:
        self.data: Dict[str, str] = {}

    def get(self, key: str) -> Optional[str]:
        return self.data.get(key)

    def mget(self, *keys: str) -> List[Optional[str]]:
        return [self.data.get(key) for key in keys]

    def incr(self, key: str, amount: int = 1) -> int:
        new_value = int(self.data.get(key, 0)) + amount
        self.data[key] = str(new_value)
        return new_value

    def incrbyfloat(self, key: str, amount: float) -> float:
        new_value = float(self.data.get(key, 0.0)) + amount
        self.data[key] = str(new_value)
        return new_value

    def expire(self, key: str, ttl: int) -> bool:
        return key in self.data

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self)

    def ping(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def set_dummy_api_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set dummy API keys so get_available_models() works without real credentials."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    """Patch :class:`CacheClient` to hand out a shared in-memory fake."""
    fake = FakeRedis()
    monkeypatch.setattr(CacheClient, "get_client", classmethod(lambda cls, **_: fake))
    return fake


@pytest.fixture
def user_messages() -> List[Dict[str, str]]:
    """A simple single-turn user message list."""
    return [{"role": "user", "content": "Write a python function to add numbers"}]
