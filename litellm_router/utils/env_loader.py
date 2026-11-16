"""Read settings from the environment or a .env file."""

import logging
import os
from typing import Optional

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


class EnvLoader:
    """Reads environment variables (and loads them from a .env file)."""

    @staticmethod
    def load() -> None:
        """Load values from a .env file into the environment."""
        loaded = load_dotenv(override=True)
        logger.debug("Loaded .env file: %s", loaded)

    @staticmethod
    def get_var(key: str, default: Optional[str] = None) -> Optional[str]:
        """Return the value of `key`, or `default` if it isn't set."""
        return os.getenv(key, default)
