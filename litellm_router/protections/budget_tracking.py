"""Keeps track of how much money we've spent this month (stored in Redis)."""

import logging

from litellm_router.config import AppConfig
from litellm_router.utils.cache_client import CacheClient
from litellm_router.utils.env_loader import EnvLoader

logger = logging.getLogger(__name__)

BUDGET_KEY = "global_monthly_spend"
DEFAULT_MAX_MONTHLY_BUDGET_USD = "50.00"


class BudgetExceededError(Exception):
    """We've spent the whole monthly budget."""


class BudgetTracker:
    """Adds up spending and stops new requests once the budget runs out."""

    def __init__(self) -> None:
        self.redis = CacheClient.get_client(**AppConfig.get_redis_config())
        self.budget_key = BUDGET_KEY
        self.max_monthly_budget_usd = float(
            EnvLoader.get_var("MAX_MONTHLY_BUDGET_USD", DEFAULT_MAX_MONTHLY_BUDGET_USD)
        )

    def check_budget(self) -> float:
        """Return how much we've spent. Raise BudgetExceededError if over budget."""
        current_spend = self.get_current_spend()
        if current_spend >= self.max_monthly_budget_usd:
            raise BudgetExceededError(
                f"Budget exceeded! Spent ${current_spend:.6f} of "
                f"${self.max_monthly_budget_usd:.6f}."
            )
        return current_spend

    def record_cost(self, cost: float) -> None:
        """Add the cost of one request to the running total in Redis."""
        if cost > 0:
            new_spend = self.redis.incrbyfloat(self.budget_key, cost)
            logger.info(
                "Recorded $%.6f; total spent this month is now $%.4f",
                cost,
                float(new_spend),
            )

    def get_current_spend(self) -> float:
        """Return how much we've spent so far this month, in US dollars."""
        spend = self.redis.get(self.budget_key)
        return float(spend) if spend else 0.0
