"""
run.py
───────────────────────────────────────────────────────────────────────────
Entry point for the RFP Agent System.

USAGE:
    python run.py       ← Interactive mode (queries go to RFPOrchestrator)
───────────────────────────────────────────────────────────────────────────
"""

import sys
import os
import logging

# Note: Universal Windows UTF-8 terminal encoding is handled automatically by agents/__init__.py upon import.

# Ensure absolute imports work from the project root
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from rfp.rfp_orchestrator import RFPOrchestrator

logger = logging.getLogger(__name__)


def run_interactive():
    """
    Main interactive loop.
    All queries are directly routed to the RFPOrchestrator.
    """
    orchestrator = RFPOrchestrator()

    print("\n" + "=" * 60)
    print(" Agentic RFP Workflow")
    print("  [>] Type your query. Type 'exit' or 'quit' to stop.")
    print("=" * 60 + "\n")

    while True:
        try:
            query = input("You: ").strip()

            if query.lower() in ["exit", "quit"]:
                print("\nGoodbye! [WAVE]")
                break

            if not query:
                continue

            response = None
            try:
                print("\n[WAIT] Processing -- please wait...\n")
                result = orchestrator.run(query)
                response = result.response or f"Error: {result.error}"
                print(f"\nFinal Response:\n{response}\n")
            except Exception as inner_e:
                response = f"[ERROR] An error occurred during execution: {inner_e}"
                logger.exception("Error during interactive query execution.")
                print(f"\n{response}\n")


        except KeyboardInterrupt:
            print("\n\nGoodbye!")
            break
        except Exception as e:
            logger.exception("Unexpected top-level error.")
            print(f"\n[ERROR] An error occurred: {e}\n")


def main():
    run_interactive()

if __name__ == "__main__":
    main()
