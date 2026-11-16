"""Interactive command-line front-end for the gateway."""

import logging

from litellm_router.config import AppConfig
from litellm_router.gateway import GenerationResult, LLMControlGateway

logger = logging.getLogger(__name__)

# Short display names for the CLI output.
_MODEL_NAMES = {
    AppConfig.OPENAI_MODEL: "OpenAI",
    AppConfig.GEMINI_MODEL: "Gemini",
    AppConfig.GROQ_MODEL: "Groq",
}


def _friendly_name(model: str) -> str:
    """Turn a model id into a short provider name (e.g. 'OpenAI').

    The model we get back can be a versioned name like
    'gpt-4o-mini-2024-07-18', so we match on the base name, not an exact key.
    """
    if not model:
        return ""
    for key, name in _MODEL_NAMES.items():
        base = key.split("/")[-1]   # 'groq/llama-...' -> 'llama-...'
        if base in model:
            return name
    return ""


class GatewayCLI:
    """A simple text loop that reads a prompt, runs it, and prints the answer."""

    def __init__(self) -> None:
        print("Starting up the LLM Gateway...")
        self.gateway = LLMControlGateway()

    @staticmethod
    def _render(result: GenerationResult) -> None:
        """Print the answer, then a short friendly summary line."""
        if not result.succeeded:
            print(f"\nSorry, that didn't work: {result.error}\n")
            return

        # The answer itself.
        print(f"\n{result.response}\n")

        # Multi-line formatted output
        model_name = _friendly_name(result.primary_model) or result.primary_model
        if model_name:
            print(f"Model Selected: {model_name}")
        
        if result.was_fallback:
            fallback_name = _friendly_name(result.model_used) or result.model_used
            print(f"Fallback Selected: {fallback_name}")
            
        print(f"Time: {result.time_needed:.2f}s")
        print(f"Cost: ${result.cost:.6f}")
        print(f"Budget Left: ${result.remaining_budget:.6f}")
        print(f"Tokens Needed: {result.tokens_used}")

        # If we skipped any models, mention it gently
        if result.skipped_models:
            print("  (Skipped models during fallback:)")
            for model, reason in result.skipped_models:
                print(f"    - {_friendly_name(model) or model}: {reason}")
        print()

    def run(self) -> None:
        """Run the interactive prompt loop until the user exits."""
        print("Ready! Ask me anything. Type 'exit' or 'quit' when you're done.\n")
        while True:
            try:
                user_input = input("You: ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nGoodbye!")
                break

            if user_input.lower() in {"exit", "quit"}:
                print("Goodbye!")
                break
            if not user_input:
                continue

            self._render(self.gateway.generate_response(user_input))


def main() -> None:
    """Console entry point for the CLI."""
    logging.basicConfig(level=logging.WARNING)
    try:
        GatewayCLI().run()
    except RuntimeError as error:
        print(f"\nError: {error}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
