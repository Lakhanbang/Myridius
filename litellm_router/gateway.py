"""The main gateway: the one object you use to send a prompt and get an answer.

For every request it does four things:
1. Check we are under budget and under the rate limit.
2. Ask the router to pick a model and call it (falling back if one fails).
3. Work out the cost and how much budget is left.
4. Save the usage so the next request sees up-to-date numbers.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from litellm_router.config import AppConfig
from litellm_router.observability import MetricsLogger
from litellm_router.protections.budget_tracking import BudgetExceededError, BudgetTracker
from litellm_router.protections.rate_limiting import RateLimiter, RateLimitExceededError
from litellm_router.routing.router import GatewayRouter
from litellm_router.utils.llm_client import LLMClient, LLMError
from litellm_router.utils.time_utils import TimeTracker

logger = logging.getLogger(__name__)

# We don't know the real token count before the call, so we assume this much
# for the pre-flight rate-limit check (a safe buffer).
ESTIMATED_PROMPT_TOKENS = 500


@dataclass
class GenerationResult:
    """Everything we know about one request after it finishes."""

    response: Optional[str] = None
    model_used: Optional[str] = None
    tokens_used: int = 0
    cost: float = 0.0
    remaining_budget: float = 0.0
    time_needed: float = 0.0
    error: Optional[str] = None
    skipped_models: list = field(default_factory=list)
    task_type: str = ""
    pool: list = field(default_factory=list)
    was_fallback: bool = False
    primary_model: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        """True if the request worked (no error was recorded)."""
        return self.error is None


class LLMControlGateway:
    """Send a prompt with ``generate_response()`` and get a result back."""

    def __init__(self) -> None:
        # Load settings from .env and quiet the noisy LiteLLM library.
        AppConfig.initialize_environment()

        # Records cost/time for each call (observability).
        self.metrics_logger = MetricsLogger()
        self.metrics_logger.attach()

        self.router = GatewayRouter()
        self.budget_tracker = BudgetTracker()
        self.rate_limiter = RateLimiter()
        
        # Track all tokens used through this gateway instance
        self.session_tokens = 0

    def generate_response(self, user_prompt: str) -> GenerationResult:
        """Run one prompt through the gateway and return the result + metrics."""
        result = GenerationResult()
        try:
            # 1. Pre-flight checks. These raise if we are over a limit.
            self.budget_tracker.check_budget()
            self.rate_limiter.check_rate_limit(estimated_tokens=ESTIMATED_PROMPT_TOKENS)

            # 2. Ask the router to pick a model and call it.
            start_time = TimeTracker.get_current_time()
            response = self.router.completion(
                messages=[{"role": "user", "content": user_prompt}],
            )
            result.time_needed = TimeTracker.calculate_duration_sec(start_time)

            # 3. Pull the answer and usage numbers out of the response.
            result.response = response.choices[0].message.content
            result.model_used = response.model
            if getattr(response, "usage", None):
                result.tokens_used = response.usage.total_tokens
                self.session_tokens += result.tokens_used
            result.cost = self._calculate_cost(response)

            # 4. Save usage so budget/rate limits stay accurate next time.
            self.rate_limiter.record_usage(tokens_used=result.tokens_used)
            self.budget_tracker.record_cost(cost=result.cost)
            result.remaining_budget = max(
                0.0,
                self.budget_tracker.max_monthly_budget_usd
                - self.budget_tracker.get_current_spend(),
            )
            result.skipped_models = getattr(self.router, "last_skipped", [])
            result.task_type = getattr(self.router, "last_task_type", "")
            result.pool = getattr(self.router, "last_pool", [])
            result.was_fallback = getattr(self.router, "last_was_fallback", False)
            
            # If it was a fallback, the primary was the first in the pool
            if result.was_fallback and result.pool:
                result.primary_model = result.pool[0]
            else:
                result.primary_model = result.model_used

        except (BudgetExceededError, RateLimitExceededError) as error:
            # We hit a budget or rate limit. Report it instead of crashing.
            logger.warning("Request blocked: %s", error)
            result.error = f"Protection Triggered: {error}"
        except (LLMError, RuntimeError) as error:
            # The model call failed, or no model was available.
            logger.error("Request failed: %s", error)
            result.error = str(error)

        return result

    def generate_response_from_messages(self, messages: list, force_models: Optional[list] = None, **kwargs) -> GenerationResult:
        """Like generate_response(), but accepts the full structured messages list.

        Used by the agent layer so that system/user roles are preserved for
        accurate task classification inside the router. Also accepts and passes
        kwargs (e.g. stop sequences from CrewAI/ReAct) down to the completion call.
        """
        result = GenerationResult()
        try:
            # 1. Pre-flight checks — same as generate_response().
            self.budget_tracker.check_budget()
            self.rate_limiter.check_rate_limit(estimated_tokens=ESTIMATED_PROMPT_TOKENS)

            # 2. Pass the full messages list and kwargs directly to the router.
            start_time = TimeTracker.get_current_time()
            response = self.router.completion(messages=messages, force_models=force_models, **kwargs)
            result.time_needed = TimeTracker.calculate_duration_sec(start_time)

            # 3. Pull the answer and usage numbers out of the response.
            result.response = response.choices[0].message.content
            result.model_used = response.model
            if getattr(response, "usage", None):
                result.tokens_used = response.usage.total_tokens
                self.session_tokens += result.tokens_used
            result.cost = self._calculate_cost(response)

            # 4. Save usage so budget/rate limits stay accurate next time.
            self.rate_limiter.record_usage(tokens_used=result.tokens_used)
            self.budget_tracker.record_cost(cost=result.cost)
            result.remaining_budget = max(
                0.0,
                self.budget_tracker.max_monthly_budget_usd
                - self.budget_tracker.get_current_spend(),
            )
            result.skipped_models = getattr(self.router, "last_skipped", [])
            result.task_type = getattr(self.router, "last_task_type", "")
            result.pool = getattr(self.router, "last_pool", [])
            result.was_fallback = getattr(self.router, "last_was_fallback", False)

            if result.was_fallback and result.pool:
                result.primary_model = result.pool[0]
            else:
                result.primary_model = result.model_used

        except (BudgetExceededError, RateLimitExceededError) as error:
            logger.warning("Request blocked: %s", error)
            result.error = f"Protection Triggered: {error}"
        except (LLMError, RuntimeError) as error:
            logger.error("Request failed: %s", error)
            result.error = str(error)

        return result

    def _calculate_cost(self, response: Any) -> float:
        """Work out the USD cost of a response."""
        # Some Groq model names need fixing before LiteLLM can price them, so we
        # swap in the priceable name, get the cost, then put the real name back.
        real_name = response.model
        response.model = AppConfig.normalize_model_for_cost(real_name)
        cost = LLMClient.get_cost(completion_response=response)
        response.model = real_name
        return round(cost, 6)
