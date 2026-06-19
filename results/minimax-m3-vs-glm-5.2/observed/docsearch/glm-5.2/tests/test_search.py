"""Tests for docsearch searching and ranking."""

import math
import os
import tempfile
import unittest

from docsearch import SearchIndex, SearchResult


# A small, hand-curated corpus. The wording overlaps deliberately so that
# ranking has to do real work: several documents share words, but only some
# are actually "about" the query.
CORPUS = [
    ("fox", "the quick brown fox jumps over the lazy dog"),
    ("dog", "a quick brown dog outruns the quick fox"),
    ("cat", "cats and dogs are common pets but cats are quieter"),
    ("pets", "pets bring joy to many homes dogs and cats especially"),
    ("weather", "the weather today is quick to change but mostly calm"),
    ("cooking", "recipes for quick dinners using fresh vegetables"),
    ("unrelated", "a treatise on the history of maritime navigation"),
]


def make_index():
    return SearchIndex(CORPUS)


class TestTokenization(unittest.TestCase):
    def test_lowercase_and_split(self):
        idx = SearchIndex()
        idx.add("d", "Hello, WORLD!  Foo-bar baz123")
        terms = set(idx.terms())
        self.assertIn("hello", terms)
        self.assertIn("world", terms)
        self.assertIn("foo", terms)
        self.assertIn("bar", terms)
        self.assertIn("baz123", terms)

    def test_stop_words_dropped(self):
        idx = SearchIndex()
        idx.add("d", "the the the a an of to is")
        self.assertEqual(idx.terms(), [])

    def test_single_char_tokens_dropped(self):
        idx = SearchIndex()
        idx.add("d", "a x y z real")
        self.assertEqual(idx.terms(), ["real"])


class TestBasicSearch(unittest.TestCase):
    def test_empty_query_returns_nothing(self):
        self.assertEqual(make_index().search(""), [])

    def test_stop_word_only_query_returns_nothing(self):
        self.assertEqual(make_index().search("the of a"), [])

    def test_no_matches_returns_empty(self):
        self.assertEqual(make_index().search("zzzznotfound"), [])

    def test_empty_index_returns_nothing(self):
        self.assertEqual(SearchIndex().search("anything"), [])

    def test_returns_search_result_objects(self):
        results = make_index().search("fox")
        self.assertTrue(all(isinstance(r, SearchResult) for r in results))
        self.assertTrue(all(r.doc_id for r in results))

    def test_results_sorted_descending(self):
        results = make_index().search("quick fox")
        scores = [r.score for r in results]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_deterministic_order_on_ties(self):
        # Two identical documents should tie; order by doc id.
        idx = SearchIndex([("b", "apple pie"), ("a", "apple pie")])
        results = idx.search("apple")
        self.assertEqual([r.doc_id for r in results], ["a", "b"])
        self.assertEqual(results[0].score, results[1].score)


class TestRanking(unittest.TestCase):
    """The heart of the library: does the ranking feel right?"""

    def test_focused_short_doc_beats_tangential_mention(self):
        # "fox" document is entirely about the fox scene; "weather" only
        # mentions "quick" in passing. A query for "quick fox" should rank
        # the fox document above the weather one.
        results = make_index().search("quick fox")
        ids = [r.doc_id for r in results]
        self.assertIn("fox", ids)
        self.assertIn("weather", ids)
        self.assertLess(ids.index("fox"), ids.index("weather"))

    def test_repeated_term_boosts_relevance(self):
        # "dog" repeats "quick" twice; "fox" has it once. For a "quick" query
        # the document more saturated with the term should win.
        results = make_index().search("quick")
        ids = [r.doc_id for r in results]
        self.assertEqual(ids[0], "dog")

    def test_top_results_are_genuinely_about_the_query(self):
        results = make_index().search("cats dogs pets")
        ids = [r.doc_id for r in results]
        # The two documents actually about cats/dogs/pets should come out on
        # top, ahead of everything else. The "cat" doc repeats "cats" and is
        # shorter, so it wins on term concentration — which is the model
        # behaving sensibly, not a bug.
        self.assertEqual(ids[:2], ["cat", "pets"])
        self.assertNotIn("unrelated", ids)
        self.assertNotIn("weather", ids)

    def test_rare_terms_outrank_common_terms(self):
        # "navigation" appears in exactly one document; "quick" in several.
        # A query for both should still surface the navigation document well.
        results = make_index().search("quick navigation")
        self.assertEqual(results[0].doc_id, "unrelated")

    def test_completely_unrelated_doc_not_in_top(self):
        results = make_index().search("quick brown fox")
        ids = [r.doc_id for r in results]
        # "unrelated" shares no terms, must not appear.
        self.assertNotIn("unrelated", ids)

    def test_scores_in_zero_to_one(self):
        for r in make_index().search("quick fox dog"):
            self.assertGreaterEqual(r.score, 0.0)
            self.assertLessEqual(r.score, 1.0 + 1e-9)

    def test_identical_query_and_doc_scores_one(self):
        idx = SearchIndex([("only", "unique words here alone")])
        results = idx.search("unique words here alone")
        self.assertAlmostEqual(results[0].score, 1.0, places=6)
        self.assertEqual(results[0].doc_id, "only")


class TestLimitAndMinScore(unittest.TestCase):
    def test_limit_truncates(self):
        results = make_index().search("quick", limit=2)
        self.assertEqual(len(results), 2)

    def test_limit_none_returns_all(self):
        results = make_index().search("quick", limit=None)
        # quick appears in fox, dog, weather, cooking
        self.assertEqual(len(results), 4)

    def test_min_score_filters(self):
        all_results = make_index().search("quick fox")
        if len(all_results) < 2:
            self.skipTest("need at least two results")
        threshold = all_results[-1].score + 1e-6
        filtered = make_index().search("quick fox", min_score=threshold)
        self.assertLess(len(filtered), len(all_results))


class TestMutation(unittest.TestCase):
    def test_add_then_search(self):
        idx = SearchIndex()
        idx.add("a", "red apple")
        idx.add("b", "green apple")
        results = idx.search("apple")
        self.assertEqual({r.doc_id for r in results}, {"a", "b"})

    def test_add_increments_size(self):
        idx = SearchIndex()
        idx.add("a", "x")
        self.assertEqual(idx.size, 1)
        self.assertEqual(len(idx), 1)

    def test_replace_document(self):
        idx = SearchIndex([("a", "apple pie")])
        idx.add("a", "completely different content about boats")
        self.assertNotIn("apple", idx.terms())
        results = idx.search("apple")
        self.assertEqual(results, [])
        results = idx.search("boats")
        self.assertEqual([r.doc_id for r in results], ["a"])

    def test_remove_document(self):
        idx = make_index()
        self.assertIn("fox", idx)
        self.assertTrue(idx.remove("fox"))
        self.assertNotIn("fox", idx)
        results = idx.search("fox")
        self.assertNotIn("fox", [r.doc_id for r in results])
        # removing again is a no-op
        self.assertFalse(idx.remove("fox"))

    def test_remove_updates_doc_freq(self):
        idx = SearchIndex([("a", "solo term"), ("b", "solo other")])
        # "solo" is in both docs
        idx.remove("a")
        # Now "solo" is in one doc; search should still find b
        results = idx.search("solo")
        self.assertEqual([r.doc_id for r in results], ["b"])

    def test_incremental_add_keeps_ranking_consistent(self):
        idx = SearchIndex()
        idx.add("a", "the quick brown fox")
        idx.add("b", "quick brown dog")
        # Add more docs that dilute "quick"
        idx.add("c", "quick rain today")
        idx.add("d", "quick lunch break")
        results = idx.search("quick brown fox")
        self.assertEqual(results[0].doc_id, "a")


class TestValidation(unittest.TestCase):
    def test_empty_id_rejected(self):
        idx = SearchIndex()
        with self.assertRaises(ValueError):
            idx.add("", "text")

    def test_non_string_text_rejected(self):
        idx = SearchIndex()
        with self.assertRaises(TypeError):
            idx.add("x", 123)  # type: ignore[arg-type]


class TestIntrospection(unittest.TestCase):
    def test_get_returns_document(self):
        idx = make_index()
        doc = idx.get("fox")
        self.assertIsNotNone(doc)
        self.assertEqual(doc.doc_id, "fox")
        self.assertIn("fox", doc.text)

    def test_get_missing_returns_none(self):
        self.assertIsNone(make_index().get("nope"))

    def test_documents_iterates_all(self):
        idx = make_index()
        ids = sorted(d.doc_id for d in idx.documents())
        self.assertEqual(ids, sorted(d for d, _ in CORPUS))

    def test_terms_sorted(self):
        terms = make_index().terms()
        self.assertEqual(terms, sorted(terms))


class TestCli(unittest.TestCase):
    def _write_corpus(self, tmpdir):
        files = {}
        for doc_id, text in CORPUS:
            path = os.path.join(tmpdir, doc_id + ".txt")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            files[doc_id] = path
        return files

    def test_cli_search_returns_zero_and_prints_results(self):
        from docsearch.cli import main

        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_corpus(tmpdir)
            rc = main(["quick fox", tmpdir, "--limit", "3"])
            self.assertEqual(rc, 0)

    def test_cli_no_matches(self):
        from docsearch.cli import main

        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_corpus(tmpdir)
            rc = main(["zzzznotfound", tmpdir])
            self.assertEqual(rc, 0)

    def test_cli_empty_dir(self):
        from docsearch.cli import main

        with tempfile.TemporaryDirectory() as tmpdir:
            rc = main(["anything", tmpdir])
            self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()