"""Tests for docsearch.tokenize."""

from docsearch.tokenize import tokenize, vocabulary


def test_lowercases_and_splits():
    assert tokenize("Hello, World!") == ["hello", "world"]


def test_drops_stopwords():
    out = tokenize("the quick brown fox is a fox")
    assert "the" not in out
    assert "is" not in out
    assert "a" not in out
    assert "quick" in out
    assert "fox" in out


def test_drops_single_char_tokens():
    assert tokenize("a b c d") == []


def test_handles_contractions():
    # "don't" becomes "don't" then is stripped of apostrophes -> "dont"
    # but it's in the stopword list, so it's dropped.
    out = tokenize("I don't like it")
    assert "dont" not in out
    assert "like" in out


def test_handles_punctuation_and_numbers():
    out = tokenize("Python 3.11 released -- type hints!")
    assert "python" in out
    assert "3" not in out  # single digit dropped
    assert "11" in out
    assert "released" in out
    assert "type" in out
    assert "hints" in out


def test_empty_string():
    assert tokenize("") == []
    assert tokenize(None or "") == []  # type: ignore[arg-type]


def test_vocabulary_preserves_order():
    toks = tokenize("alpha beta alpha gamma beta")
    assert vocabulary(toks) == ["alpha", "beta", "gamma"]
