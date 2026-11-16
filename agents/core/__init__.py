"""Core module.
Provides the LLM gateway wrapper for subagents.
"""

from agents.core.llm_wrapper import get_crewai_llm, _get_gateway

__all__ = [
    "get_crewai_llm",
    "_get_gateway",
]
