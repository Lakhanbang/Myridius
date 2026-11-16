"""Helper to pull the words out of a piece of text."""

import re

_WORD_RE = re.compile(r"\b\w+\b")


class TextParser:
    """Finds the words in a string."""

    @staticmethod
    def extract_words(text: str) -> set:
        """Return the set of lower-cased words found in `text`."""
        return set(_WORD_RE.findall(text.lower()))
