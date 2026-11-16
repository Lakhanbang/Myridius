"""Keeps a small record of the cost and time for each model call."""

import logging

from litellm_router.config import AppConfig
from litellm_router.utils.llm_client import LLMClient

logger = logging.getLogger(__name__)


class MetricsLogger:
    """Remembers the cost and time of every successful call."""

    def __init__(self) -> None:
        self.call_logs = []  # one dict per successful call

    def log_metrics(self, kwargs, completion_response, start_time, end_time) -> None:
        """LiteLLM calls this after each successful request; we save the numbers."""
        model = AppConfig.normalize_model_for_cost(completion_response.model)
        completion_response.model = model
        tokens = 0
        usage = getattr(completion_response, "usage", None)
        if usage:
            if hasattr(usage, "total_tokens"):
                tokens = usage.total_tokens
            elif isinstance(usage, dict):
                tokens = usage.get("total_tokens", 0)

        record = {
            "model": model,
            "seconds": round(end_time - start_time, 2),
            "cost": round(LLMClient.get_cost(completion_response=completion_response), 6),
            "tokens": tokens,
        }
        self.call_logs.append(record)
        logger.debug("Logged metrics: %s", record)

    def attach(self) -> None:
        """Tell LiteLLM to call log_metrics after every successful request."""
        LLMClient.set_success_callback(self.log_metrics)
