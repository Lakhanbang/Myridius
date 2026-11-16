"""Convenience launcher so ``python main.py`` runs the CLI without installing.

Prefer the packaged entry point after ``pip install -e .``:
    litellm-router     # interactive CLI
"""

import os
import sys

# Add the project root to sys.path so `litellm_router` package is importable.
sys.path.insert(0, os.path.dirname(__file__))

from litellm_router.cli import main

if __name__ == "__main__":
    main()
