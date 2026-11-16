"""LiteLLM-based control gateway component.

Use it from anywhere, including future sibling components like ``agents``:

    from litellm_router import LLMControlGateway

    gateway = LLMControlGateway()
    result = gateway.generate_response("Hello")
"""

from litellm_router.gateway import GenerationResult, LLMControlGateway

__all__ = ["LLMControlGateway", "GenerationResult"]
