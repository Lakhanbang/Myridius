"""Unit tests for :class:`litellm_router.protections.rate_limiting.RateLimiter`."""

import pytest

from litellm_router.protections.rate_limiting import RateLimitExceededError, RateLimiter


def test_record_usage_then_check_counts_against_limits(fake_redis) -> None:
    limiter = RateLimiter()
    limiter.record_usage(tokens_used=100)
    # One request recorded; still well under the default 60 RPM ceiling.
    limiter.check_rate_limit(estimated_tokens=0)


def test_check_rate_limit_raises_when_rpm_exceeded(
    fake_redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAX_RPM", "1")
    limiter = RateLimiter()
    limiter.record_usage(tokens_used=0)
    with pytest.raises(RateLimitExceededError):
        limiter.check_rate_limit()


def test_check_rate_limit_raises_when_tpm_exceeded(
    fake_redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAX_TPM", "500")
    limiter = RateLimiter()
    with pytest.raises(RateLimitExceededError):
        limiter.check_rate_limit(estimated_tokens=1000)
