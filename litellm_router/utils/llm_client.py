"""A small wrapper around the LiteLLM library.

Keeping every LiteLLM call in this one file means the rest of the code does not
need to import or know about LiteLLM. If a model call fails, we raise our own
simple ``LLMError`` so the router can catch it and try a different model.
"""

import logging
from typing import Any

import litellm
from litellm.exceptions import APIConnectionError, AuthenticationError, BadRequestError, RateLimitError, Timeout

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """Base class for model call failures."""

class LLMRateLimitError(LLMError):
    """Rate limit exceeded."""

class LLMAPIConnectionError(LLMError):
    """Connection error to the API."""

class LLMTimeoutError(LLMError):
    """Timeout error."""

class LLMClient:
    """A few simple helpers that call LiteLLM."""

    @staticmethod
    def completion(model: str, messages: list[dict], **kwargs: Any) -> Any:
        """Call the model and return its response.

        If the model fails we raise our own specific error instead of LiteLLM's
        errors, so the rest of the code only deals with our error types.
        """
        try:
            return litellm.completion(model=model, messages=messages, **kwargs)
        except RateLimitError as error:
            raise LLMRateLimitError(str(error)) from error
        except APIConnectionError as error:
            raise LLMAPIConnectionError(str(error)) from error
        except Timeout as error:
            raise LLMTimeoutError(str(error)) from error
        except (AuthenticationError, BadRequestError) as error:
            raise LLMError(str(error)) from error

    @staticmethod
    def get_cost(completion_response: Any) -> float:
        """Return how much the call cost, in US dollars."""
        return litellm.completion_cost(completion_response=completion_response)

    @staticmethod
    def set_success_callback(callback) -> None:
        """Tell LiteLLM to call ``callback`` after every successful request."""
        litellm.success_callback = [callback]

    @staticmethod
    def set_verbose(is_verbose: bool) -> None:
        """Turn LiteLLM's noisy debug messages on or off."""
        litellm.suppress_debug_info = not is_verbose
