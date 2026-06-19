"""Tests for docsearch.core.DocSearch.

These tests focus on the things the user actually cares about:
- ranking feels sensible (relevant docs come first)
- incremental indexing works (add/remove update scores correctly)
- edge cases don't blow up
"""

import math

import pytest

from docsearch.core import DocSearch, SearchResult


# --------------------------------------------------------------------- #
# Fixtures: a small but realistic-looking corpus
# --------------------------------------------------------------------- #

CORPUS = {
    1: "Python is a programming language with dynamic typing and duck typing.",
    2: "How to cook pasta: boil water, add salt, drop the pasta, wait.",
    3: "Type hints in Python let you annotate function arguments and return types.",
    4: "The history of Rome and the Roman Empire in antiquity.",
    5: "Database indexing speeds up queries by building auxiliary data structures.",
    6: "Typing practice: improve your typing speed with daily exercises.",
    7: "A recipe for spaghetti carbonara uses eggs, cheese, and pancetta.",
    8: "Static type checking with mypy catches bugs before runtime.",
    9: "Travel notes from a trip to Rome, including pasta recommendations.",
    10: "Notes on Python's type system, gradual typing, and mypy.",
}


@pytest.fixture
def ds():
    s = DocSearch()
    for did, text in CORPUS.items():
        s.add(text, doc_id=did)
    return s


# --------------------------------------------------------------------- #
# Basic mechanics
# --------------------------------------------------------------------- #

def test_add_returns_id():
    s = DocSearch()
    assert s.add("hello world") == 1
    assert s.add("another doc") == 2
    assert s.add("third", doc_id=42) == 42
    assert s.add("fourth") == 43  # next_id advanced past 42


def test_add_duplicate_id_raises():
    s = DocSearch()
    s.add("first", doc_id=1)
    with pytest.raises(ValueError):
        s.add("second", doc_id=1)


def test_len_and_contains():
    s = DocSearch()
    assert len(s) == 0
    s.add("a")
    s.add("b")
    assert len(s) == 2
    assert 1 in s
    assert 99 not in s


def test_doc_ids_sorted():
    s = DocSearch()
    for did in [5, 1, 3]:
        s.add(f"doc {did}", doc_id=did)
    assert s.doc_ids == [1, 3, 5]


def test_get_text_roundtrip():
    s = DocSearch()
    s.add("hello world", doc_id=7)
    assert s.get_text(7) == "hello world"


# --------------------------------------------------------------------- #
# Ranking quality — the thing the user cares about most
# --------------------------------------------------------------------- #

def test_search_returns_relevant_docs_first(ds):
    results = ds.search("python type hints")
    ids = [r.doc_id for r in results]
    # The two docs that are really about python type hints should be on top.
    assert ids[0] in (3, 10, 8)
    assert ids[1] in (3, 10, 8)
    # Doc 6 is about typing practice, not python typing — should be lower.
    assert 6 not in ids[:2]


def test_search_excludes_irrelevant_docs(ds):
    results = ds.search("python type hints")
    ids = {r.doc_id for r in results}
    # Cooking and Roman history have nothing to do with python type hints.
    assert 2 not in ids
    assert 4 not in ids
    assert 7 not in ids


def test_search_handles_half_remembered_wording(ds):
    # The user half-remembers "type hints" — they search "type hint".
    # Tokenization should still match the docs that contain "hints".
    results = ds.search("type hint")
    ids = [r.doc_id for r in results]
    assert 3 in ids  # "Type hints in Python..."
    assert 10 in ids  # "Notes on Python's type system..."
    # And the python-typing docs should outrank the typing-practice doc.
    assert ids.index(3) < ids.index(6)
    assert ids.index(10) < ids.index(6)


def test_search_scores_are_in_unit_interval(ds):
    # Cosine similarity lives in [0, 1] for non-negative vectors.
    for r in ds.search("python database indexing"):
        assert 0.0 <= r.score <= 1.0


def test_search_results_sorted_descending(ds):
    results = ds.search("python typing")
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


def test_search_top_k_limits_results(ds):
    results = ds.search("python", top_k=3)
    assert len(results) <= 3


def test_search_empty_query_returns_empty(ds):
    assert ds.search("") == []
    assert ds.search("   ") == []
    assert ds.search("the and of") == []  # all stopwords


def test_search_empty_index_returns_empty():
    s = DocSearch()
    assert s.search("anything") == []


def test_search_no_overlap_returns_empty(ds):
    # A query with words that don't appear in any document.
    assert ds.search("quantum entanglement") == []


def test_search_returns_searchresult_instances(ds):
    results = ds.search("python")
    assert all(isinstance(r, SearchResult) for r in results)


# --------------------------------------------------------------------- #
# Incremental indexing
# --------------------------------------------------------------------- #

def test_remove_updates_ranking():
    s = DocSearch()
    s.add("python type hints are great", doc_id=1)
    s.add("cooking recipes for dinner", doc_id=2)
    s.add("more about python typing", doc_id=3)

    # Before removal: doc 1 and 3 should both match.
    before = {r.doc_id for r in s.search("python type")}
    assert before == {1, 3}

    s.remove(1)
    after = {r.doc_id for r in s.search("python type")}
    assert after == {3}
    assert 1 not in s


def test_add_after_remove_works():
    s = DocSearch()
    s.add("python type hints", doc_id=1)
    s.remove(1)
    s.add("python type hints again", doc_id=1)
    assert s.search("python type hints")


def test_clear_resets_state():
    s = DocSearch()
    s.add("a")
    s.add("b")
    s.clear()
    assert len(s) == 0
    assert s.search("a") == []


def test_idf_changes_after_adding_common_term():
    # A term that appears in many docs should get a lower score than a
    # term that appears in only one. We verify by checking that the
    # discriminating term boosts its doc more.
    s = DocSearch()
    s.add("the python language", doc_id=1)
    s.add("the java language", doc_id=2)
    s.add("the ruby language", doc_id=3)
    s.add("unique term appears here", doc_id=4)

    # "language" appears in 3/4 docs; "unique" in 1/4.
    # Searching for "unique language" should rank doc 4 highest because
    # "unique" is rare and discriminative.
    results = s.search("unique language")
    assert results[0].doc_id == 4


# --------------------------------------------------------------------- #
# Tie-breaking
# --------------------------------------------------------------------- #

def test_ties_broken_by_shorter_doc():
    s = DocSearch()
    # Two docs with identical content but different lengths — the shorter
    # one (more focused) should win on ties.
    s.add("python", doc_id=1)
    s.add("python python python python python", doc_id=2)
    results = s.search("python")
    assert results[0].doc_id == 1


# --------------------------------------------------------------------- #
# Performance sanity (not a benchmark, just a smoke test)
# --------------------------------------------------------------------- #

def test_search_quick_on_larger_corpus():
    import random
    import string

    random.seed(0)
    s = DocSearch()
    vocab = ["python", "java", "rust", "typing", "database", "indexing",
             "cooking", "pasta", "recipe", "travel", "rome", "history"]
    for i in range(500):
        words = random.choices(vocab, k=random.randint(20, 80))
        text = " ".join(words)
        s.add(text, doc_id=i)

    # Should complete quickly even on 500 docs.
    results = s.search("python typing")
    assert isinstance(results, list)
    assert len(results) > 0
