"""
tool_helpers.py
───────────────────────────────────────────────────────────────────────────
Provides a decorator for consistent error handling across all agent tools.

Instead of writing try/except in every tool's _run() method, tools can
use @safe_tool_run("Tool Name") to automatically catch exceptions, log
them, and return a clean error string back to the agent.
───────────────────────────────────────────────────────────────────────────
"""

import logging
from functools import wraps

logger = logging.getLogger(__name__)


def safe_tool_run(tool_name: str):
    """
    Decorator that wraps a tool's _run() method with standardized
    error handling and logging.

    Usage:
        class MyTool(BaseTool):
            @safe_tool_run("My Tool")
            def _run(self, query: str) -> str:
                ...  # no try/except needed

    On error, returns a consistent message:
        "[My Tool Error] FileNotFoundError: file.csv not found"
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.error(
                    f"{tool_name} failed: {e}",
                    exc_info=True,
                )
                return f"[{tool_name} Error] {type(e).__name__}: {e}"
        return wrapper
    return decorator
