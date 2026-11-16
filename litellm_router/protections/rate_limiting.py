"""Limits how many requests and tokens we use each minute (counts in Redis).

RPM = requests per minute, TPM = tokens per minute. We keep a counter for the
current minute in Redis and let it expire so the next minute starts fresh.
"""

import logging

from litellm_router.config import AppConfig
from litellm_router.utils.cache_client import CacheClient
from litellm_router.utils.env_loader import EnvLoader
from litellm_router.utils.time_utils import TimeTracker

logger = logging.getLogger(__name__)

DEFAULT_MAX_RPM = "60"
DEFAULT_MAX_TPM = "100000"
WINDOW_TTL_SECONDS = 120


class RateLimitExceededError(Exception):
    """This request would go over the per-minute request or token limit."""


class RateLimiter:
    """Counts requests and tokens per minute and blocks anything over the limit."""

    def __init__(self) -> None:
        self.redis = CacheClient.get_client(**AppConfig.get_redis_config())
        self.max_rpm = int(EnvLoader.get_var("MAX_RPM", DEFAULT_MAX_RPM))
        self.max_tpm = int(EnvLoader.get_var("MAX_TPM", DEFAULT_MAX_TPM))

    def _get_current_minute_key(self, metric: str) -> str:
        """Build a Redis key for this metric and the current minute."""
        current_minute = int(TimeTracker.get_current_time() / 60)
        return f"global_{metric}:{current_minute}"

    def check_rate_limit(self, estimated_tokens: int = 0) -> None:
        """Raise RateLimitExceededError if this request would go over a limit."""
        rpm_key = self._get_current_minute_key("rpm")
        tpm_key = self._get_current_minute_key("tpm")

        # Read both counters at once.
        rpm_val, tpm_val = self.redis.mget(rpm_key, tpm_key)
        current_rpm = int(rpm_val or 0)
        current_tpm = int(tpm_val or 0)

        if current_rpm >= self.max_rpm:
            raise RateLimitExceededError(
                f"Too many requests this minute (limit {self.max_rpm})."
            )
        if (current_tpm + estimated_tokens) > self.max_tpm:
            raise RateLimitExceededError(
                f"Too many tokens this minute (limit {self.max_tpm})."
            )

    def record_usage(self, tokens_used: int) -> None:
        """Add one request and its tokens to this minute's counters."""
        rpm_key = self._get_current_minute_key("rpm")
        tpm_key = self._get_current_minute_key("tpm")

        # A pipeline sends all of these Redis commands together (faster).
        pipeline = self.redis.pipeline()
        pipeline.incr(rpm_key, 1)
        if tokens_used > 0:
            pipeline.incr(tpm_key, tokens_used)
        pipeline.expire(rpm_key, WINDOW_TTL_SECONDS)
        pipeline.expire(tpm_key, WINDOW_TTL_SECONDS)
        pipeline.execute()

        logger.debug("Usage recorded: +1 request, +%d tokens", tokens_used)
