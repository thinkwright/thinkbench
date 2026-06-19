"""Core search engine: TF-IDF weighting with cosine-similarity ranking.

The model is deliberately simple and standard:

* Documents are tokenized into lowercase terms. A small, conservative set of
  English stop words is dropped so that "the", "a", "of" and friends don't
  drown out the words that actually carry meaning.
* Each document is represented as a TF-IDF vector, where term frequency is
  log-scaled (``1 + log(tf)``) and inverse document frequency follows the
  textbook formula ``log((N + 1) / (df + 1)) + 1`` (smoothed so a term present
  in every document still gets a small, non-zero weight).
* Queries are turned into TF-IDF vectors in the same space and compared to
  every document with cosine similarity. Cosine similarity naturally rewards
  documents whose content is concentrated on the query terms, which is what
  makes the ranking "feel" right: a short document fully about the topic beats
  a long document that mentions it in passing.

The index is incrementally updatable: add documents as they arrive and the
model adjusts. For very large collections you can call ``rebuild()`` to
recompute the cached IDF weights from scratch.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

# A short, conservative stop-word list. Kept small on purpose: we only want to
# drop words that are so common they carry almost no signal. Anything domain
# specific should stay in the index.
_STOP_WORDS = frozenset(
    """
    a an and are as at be by for from has have in is it its of on that the
    to was were will with he she they them his her their this these those
    i you your our we us but or not no so if then than there here which who
    whom whose what when where why how all any both each few more most other
    some such only own same too very can just now into through during before
    after above below up down out off over under again further once
    """.split()
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> List[str]:
    """Split text into lowercase term tokens, dropping stop words.

    Tokens are runs of letters and digits. Anything else is a separator, so
    "don't" becomes ["don", "t"] and "v1.2" becomes ["v1", "2"]. That's a
    reasonable trade-off for a general-purpose index and keeps tokenization
    fast and dependency-free.
    """
    tokens = _TOKEN_RE.findall(text.lower())
    return [t for t in tokens if t not in _STOP_WORDS and len(t) > 1]


class SearchResult:
    """A single ranked search hit.

    Attributes
    ----------
    doc_id : str
        The identifier passed when the document was added.
    score : float
        Cosine similarity to the query, in the range [0, 1]. Higher is better.
    """

    __slots__ = ("doc_id", "score")

    def __init__(self, doc_id: str, score: float) -> None:
        self.doc_id = doc_id
        self.score = score

    def __repr__(self) -> str:
        return f"SearchResult(doc_id={self.doc_id!r}, score={self.score:.4f})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SearchResult):
            return NotImplemented
        return self.doc_id == other.doc_id and self.score == other.score

    def __hash__(self) -> int:
        return hash((self.doc_id, round(self.score, 9)))


class Document:
    """An indexed document: its id and the text it was built from."""

    __slots__ = ("doc_id", "text")

    def __init__(self, doc_id: str, text: str) -> None:
        self.doc_id = doc_id
        self.text = text

    def __repr__(self) -> str:
        return f"Document(doc_id={self.doc_id!r})"


class SearchIndex:
    """A TF-IDF search index over a collection of text documents.

    Parameters
    ----------
    documents : iterable of (id, text), optional
        Initial documents to add. Each id must be unique.

    Example
    -------
    >>> idx = SearchIndex([("a", "red apple"), ("b", "green apple")])
    >>> idx.search("apple")[0].doc_id in ("a", "b")
    True
    """

    def __init__(self, documents: Optional[Iterable[Tuple[str, str]]] = None) -> None:
        # id -> raw text
        self._texts: Dict[str, str] = {}
        # id -> {term: term_frequency} (raw counts)
        self._term_freqs: Dict[str, Dict[str, int]] = {}
        # term -> number of documents containing it
        self._doc_freq: Dict[str, int] = defaultdict(int)
        # term -> idf weight (cached; rebuilt on demand)
        self._idf: Dict[str, float] = {}
        # id -> precomputed L2 norm of its tf-idf vector (cached)
        self._norms: Dict[str, float] = {}
        # True when idf/norms need recomputing before a search
        self._dirty: bool = False

        if documents is not None:
            for doc_id, text in documents:
                self.add(doc_id, text)

    # ------------------------------------------------------------------ #
    # Building the index
    # ------------------------------------------------------------------ #

    def add(self, doc_id: str, text: str) -> None:
        """Add or replace a document.

        If ``doc_id`` already exists, its text is replaced and the document
        frequency counts are updated accordingly.
        """
        if not isinstance(doc_id, str) or not doc_id:
            raise ValueError("doc_id must be a non-empty string")
        if not isinstance(text, str):
            raise TypeError("text must be a string")

        # If replacing an existing document, undo its contribution to df first.
        if doc_id in self._term_freqs:
            self._remove_from_df(doc_id)

        tokens = _tokenize(text)
        tf = dict(Counter(tokens))
        self._texts[doc_id] = text
        self._term_freqs[doc_id] = tf
        for term in tf:
            self._doc_freq[term] += 1
        self._dirty = True

    def remove(self, doc_id: str) -> bool:
        """Remove a document. Returns True if it was present, False otherwise."""
        if doc_id not in self._term_freqs:
            return False
        self._remove_from_df(doc_id)
        del self._texts[doc_id]
        del self._term_freqs[doc_id]
        self._norms.pop(doc_id, None)
        self._dirty = True
        return True

    def _remove_from_df(self, doc_id: str) -> None:
        for term in self._term_freqs.get(doc_id, {}):
            self._doc_freq[term] -= 1
            if self._doc_freq[term] <= 0:
                del self._doc_freq[term]

    def rebuild(self) -> None:
        """Recompute cached IDF weights and document norms from scratch.

        Calling this is optional — searches always see a consistent index.
        It's useful as an explicit hint after a large batch of additions.
        """
        self._recompute_idf()
        self._recompute_norms()
        self._dirty = False

    def _recompute_idf(self) -> None:
        n = len(self._term_freqs)
        # Smoothed IDF: a term in every document still gets a small weight,
        # and a term in no document (shouldn't happen, but safe) is handled.
        self._idf = {
            term: math.log((n + 1) / (df + 1)) + 1.0
            for term, df in self._doc_freq.items()
        }

    def _idf_weight(self, term: str) -> float:
        if self._dirty:
            self._recompute_idf()
        return self._idf.get(term, 0.0)

    def _tfidf(self, doc_id: str) -> Dict[str, float]:
        """Return the TF-IDF vector for a document (uncached)."""
        idf = self._idf if not self._dirty else None
        if idf is None:
            self._recompute_idf()
            idf = self._idf
        vec: Dict[str, float] = {}
        for term, count in self._term_freqs[doc_id].items():
            w = (1.0 + math.log(count)) * idf.get(term, 0.0)
            if w != 0.0:
                vec[term] = w
        return vec

    def _recompute_norms(self) -> None:
        if self._dirty:
            self._recompute_idf()
        self._norms = {}
        for doc_id in self._term_freqs:
            vec = self._tfidf(doc_id)
            self._norms[doc_id] = math.sqrt(sum(w * w for w in vec.values()))

    def _norm(self, doc_id: str) -> float:
        if self._dirty:
            self.rebuild()
        return self._norms.get(doc_id, 0.0)

    # ------------------------------------------------------------------ #
    # Querying
    # ------------------------------------------------------------------ #

    def search(
        self, query: str, limit: Optional[int] = None, min_score: float = 0.0
    ) -> List[SearchResult]:
        """Return documents matching ``query``, best matches first.

        Parameters
        ----------
        query : str
            Free text. Tokenized the same way documents are.
        limit : int, optional
            Return at most this many results. ``None`` means all matches.
        min_score : float
            Drop results scoring below this. Default 0.0 keeps any document
            that shares at least one query term.

        Returns
        -------
        list of SearchResult
            Sorted by descending score, ties broken by document id for
            deterministic output.

        A document appears only if it shares at least one (non-stop-word)
        term with the query; documents with no overlap score 0 and are
        excluded.
        """
        query_terms = _tokenize(query)
        if not query_terms or not self._term_freqs:
            return []

        if self._dirty:
            self.rebuild()

        # Build the query TF-IDF vector in the document space.
        q_counts = Counter(query_terms)
        q_vec: Dict[str, float] = {}
        for term, count in q_counts.items():
            idf = self._idf.get(term, 0.0)
            if idf == 0.0:
                continue
            q_vec[term] = (1.0 + math.log(count)) * idf
        if not q_vec:
            return []

        q_norm = math.sqrt(sum(w * w for w in q_vec.values()))
        if q_norm == 0.0:
            return []

        # Only consider documents that contain at least one query term.
        candidates: set = set()
        for term in q_vec:
            # We don't keep a posting list, but df keys tell us which terms
            # exist; we scan documents for those terms. For larger collections
            # a posting list would help — see _postings below.
            candidates.update(self._postings.get(term, ()))

        results: List[SearchResult] = []
        for doc_id in candidates:
            doc_norm = self._norms.get(doc_id, 0.0)
            if doc_norm == 0.0:
                continue
            # Dot product over the smaller of the two vectors.
            doc_tf = self._term_freqs[doc_id]
            if len(q_vec) <= len(doc_tf):
                small, large = q_vec, doc_tf
            else:
                small, large = doc_tf, q_vec
            idf = self._idf
            dot = 0.0
            for term, qw in q_vec.items():
                count = doc_tf.get(term)
                if count is None:
                    continue
                dw = (1.0 + math.log(count)) * idf.get(term, 0.0)
                dot += qw * dw
            if dot <= 0.0:
                continue
            score = dot / (q_norm * doc_norm)
            if score >= min_score:
                results.append(SearchResult(doc_id, score))

        results.sort(key=lambda r: (-r.score, r.doc_id))
        if limit is not None:
            results = results[:limit]
        return results

    # ------------------------------------------------------------------ #
    # Introspection
    # ------------------------------------------------------------------ #

    @property
    def size(self) -> int:
        """Number of documents in the index."""
        return len(self._term_freqs)

    def __len__(self) -> int:
        return len(self._term_freqs)

    def __contains__(self, doc_id: object) -> bool:
        return doc_id in self._term_freqs

    def get(self, doc_id: str) -> Optional[Document]:
        """Return the stored Document, or None if not present."""
        if doc_id in self._texts:
            return Document(doc_id, self._texts[doc_id])
        return None

    def documents(self) -> Iterator[Document]:
        """Iterate over all stored documents."""
        for doc_id, text in self._texts.items():
            yield Document(doc_id, text)

    def terms(self) -> Sequence[str]:
        """All distinct indexed terms (sorted)."""
        return sorted(self._doc_freq)

    # ------------------------------------------------------------------ #
    # Posting list support
    # ------------------------------------------------------------------ #

    @property
    def _postings(self) -> Dict[str, List[str]]:
        """term -> list of doc_ids containing it.

        Built lazily and cached. Invalidated whenever the index is marked
        dirty. This is what makes ``search`` scale: instead of scoring every
        document, we only touch documents that share a term with the query.
        """
        cached = self.__dict__.get("_postings_cache")
        if cached is not None and not self._dirty:
            return cached
        postings: Dict[str, List[str]] = defaultdict(list)
        for doc_id, tf in self._term_freqs.items():
            for term in tf:
                postings[term].append(doc_id)
        cache = {term: ids for term, ids in postings.items()}
        self.__dict__["_postings_cache"] = cache
        return cache