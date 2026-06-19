"""Core TF-IDF index for docsearch.

Design notes
------------
- Documents are stored by integer id; the caller supplies an id (or we mint
  one). The id is what gets returned in results, so callers can map back to
  whatever they like.
- The index is incremental: adding a document updates IDF and the document
  vector in place. No full rebuild needed.
- Scoring is cosine similarity between the query vector and each document
  vector. Documents with no overlap get a score of 0 and are dropped.
- All state is plain Python (dicts, lists, sets) so the whole index can be
  pickled or serialized to JSON trivially.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from .tokenize import tokenize


@dataclass(frozen=True)
class SearchResult:
    doc_id: int
    score: float

    def __repr__(self) -> str:
        return f"SearchResult(doc_id={self.doc_id}, score={self.score:.4f})"


@dataclass
class _Doc:
    """Internal record for an indexed document."""
    doc_id: int
    text: str = ""
    tokens: list[str] = field(default_factory=list)
    # term -> tf (raw count). Sparse — only terms that appear are stored.
    tf: Counter = field(default_factory=Counter)
    # L2 norm of the TF-IDF vector, cached for cosine similarity.
    norm: float = 0.0
    length: int = 0  # number of tokens


class DocSearch:
    """A small incremental TF-IDF search index.

    Usage::

        ds = DocSearch()
        ds.add("doc about python typing", doc_id=1)
        ds.add("doc about cooking pasta", doc_id=2)
        ds.search("python type hints", top_k=5)
    """

    def __init__(self) -> None:
        self._docs: dict[int, _Doc] = {}
        self._df: Counter = Counter()  # document frequency per term
        self._next_id: int = 1

    # ------------------------------------------------------------------ #
    # Mutation
    # ------------------------------------------------------------------ #

    def add(self, text: str, doc_id: int | None = None) -> int:
        """Index a document and return its id.

        If `doc_id` is None, a fresh integer id is assigned.
        """
        if doc_id is None:
            doc_id = self._next_id
            self._next_id += 1
        elif doc_id >= self._next_id:
            self._next_id = doc_id + 1

        if doc_id in self._docs:
            raise ValueError(f"doc_id {doc_id} already indexed; remove it first")

        tokens = tokenize(text)
        tf: Counter = Counter(tokens)
        doc = _Doc(
            doc_id=doc_id,
            text=text,
            tokens=tokens,
            tf=tf,
            length=len(tokens),
        )

        # Update document frequencies and the document's own TF-IDF vector.
        for term in tf:
            self._df[term] += 1
        doc.norm = self._compute_norm(tf)

        self._docs[doc_id] = doc
        return doc_id

    def remove(self, doc_id: int) -> None:
        """Remove a document and update IDF accordingly."""
        doc = self._docs.pop(doc_id)
        for term in doc.tf:
            self._df[term] -= 1
            if self._df[term] <= 0:
                del self._df[term]

    def clear(self) -> None:
        """Drop all documents."""
        self._docs.clear()
        self._df.clear()
        self._next_id = 1

    # ------------------------------------------------------------------ #
    # Inspection
    # ------------------------------------------------------------------ #

    def __len__(self) -> int:
        return len(self._docs)

    def __contains__(self, doc_id: int) -> bool:
        return doc_id in self._docs

    @property
    def doc_ids(self) -> list[int]:
        return sorted(self._docs)

    def get_text(self, doc_id: int) -> str:
        """Return the original text of a previously indexed document."""
        return self._docs[doc_id].text

    # ------------------------------------------------------------------ #
    # Search
    # ------------------------------------------------------------------ #

    def search(self, query: str, top_k: int = 10) -> list[SearchResult]:
        """Return up to `top_k` documents ranked by cosine similarity.

        Documents with zero overlap are omitted. Results are sorted by score
        descending; ties are broken by shorter document length (more focused
        docs win), then by doc_id for stability.
        """
        q_tokens = tokenize(query)
        if not q_tokens or not self._docs:
            return []

        q_tf = Counter(q_tokens)
        q_norm = self._compute_norm(q_tf)
        if q_norm == 0.0:
            return []

        n_docs = len(self._docs)
        results: list[SearchResult] = []

        for doc in self._docs.values():
            # Dot product over terms present in both query and document.
            score = 0.0
            for term, q_weight in q_tf.items():
                df = self._df.get(term, 0)
                if df == 0:
                    continue
                idf = math.log((1 + n_docs) / (1 + df)) + 1.0
                d_tf = doc.tf.get(term, 0)
                if d_tf == 0:
                    continue
                score += (q_weight * idf) * (d_tf * idf)

            if score <= 0.0 or doc.norm == 0.0:
                continue
            score /= q_norm * doc.norm
            results.append(SearchResult(doc_id=doc.doc_id, score=score))

        results.sort(key=lambda r: (-r.score, self._docs[r.doc_id].length, r.doc_id))
        return results[: max(0, top_k)]

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _compute_norm(self, tf: Counter) -> float:
        """L2 norm of the TF-IDF vector for the given term frequencies."""
        if not tf:
            return 0.0
        n_docs = len(self._docs) or 1
        s = 0.0
        for term, freq in tf.items():
            df = self._df.get(term, 0)
            # Smoothed IDF, same formula used at query time.
            idf = math.log((1 + n_docs) / (1 + df)) + 1.0
            w = freq * idf
            s += w * w
        return math.sqrt(s)
