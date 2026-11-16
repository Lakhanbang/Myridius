"""Settings and the list of models the gateway can use."""

import logging
import warnings

from litellm_router.utils.env_loader import EnvLoader
from litellm_router.utils.llm_client import LLMClient

logger = logging.getLogger(__name__)


class AppConfig:
    """Holds the model names and a few helper settings."""

    # The three models we can route to. The names follow LiteLLM's format.
    OPENAI_MODEL = "gpt-4o-mini"
    GEMINI_MODEL = "gemini/gemini-2.0-flash"
    GROQ_MODEL = "groq/llama-3.3-70b-versatile"
    ALL_MODELS = [OPENAI_MODEL, GEMINI_MODEL, GROQ_MODEL]

    # Maps each model to the env var that holds its API key.
    MODEL_KEY_MAP = {
        OPENAI_MODEL: "OPENAI_API_KEY",
        GEMINI_MODEL: "GEMINI_API_KEY",
        GROQ_MODEL: "GROQ_API_KEY",
    }

    @classmethod
    def get_available_models(cls) -> list:
        """Return only the models whose API key is set in the environment."""
        available = []
        for model in cls.ALL_MODELS:
            key_name = cls.MODEL_KEY_MAP[model]
            if EnvLoader.get_var(key_name, None):
                available.append(model)
        if not available:
            raise RuntimeError("No provider API keys found. Set at least one of: OPENAI_API_KEY, GEMINI_API_KEY, GROQ_API_KEY.")
        logger.info("Available models: %s", available)
        return available

    @classmethod
    def initialize_environment(cls) -> None:
        """Load settings from the .env file and quiet LiteLLM's debug output."""
        warnings.filterwarnings("ignore")
        LLMClient.set_verbose(False)
        EnvLoader.load()

    @classmethod
    def get_redis_config(cls) -> dict:
        """Read the Redis connection settings from the environment."""
        return {
            "host": EnvLoader.get_var("REDIS_HOST", "localhost"),
            "port": int(EnvLoader.get_var("REDIS_PORT", "6379")),
            "password": EnvLoader.get_var("REDIS_PASSWORD", None),
        }

    @classmethod
    def normalize_model_for_cost(cls, model: str) -> str:
        """Fix a model name so LiteLLM can work out its price.

        LiteLLM cannot always price a bare Groq "llama" model name, so we change
        it to the full GROQ_MODEL name. The gateway and the metrics logger both
        use this so they agree on the price.
        """
        if not model.startswith("groq/") and "llama" in model.lower():
            return cls.GROQ_MODEL
        return model
