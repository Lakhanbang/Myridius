"""Unit tests for :class:`litellm_router.utils.text_parser.TextParser`."""

from litellm_router.utils.text_parser import TextParser


def test_extract_words_lowercases_and_dedupes() -> None:
    assert TextParser.extract_words("Code CODE code") == {"code"}


def test_extract_words_splits_on_non_word_chars() -> None:
    assert TextParser.extract_words("a, b-c!") == {"a", "b", "c"}


def test_extract_words_empty_string_returns_empty_set() -> None:
    assert TextParser.extract_words("") == set()
