"""docsearch — search a collection of text documents by relevance.

A small, dependency-free library for ranking documents against a query.
It builds a TF-IDF model over a collection of documents and scores each
document against a query using cosine similarity, so documents that are
*about* the query terms rank above ones that merely contain the characters
somewhere.

Example
-------
>>> from docsearch import SearchIndex
>>> idx = SearchIndex()
>>> idx.add("doc1", "the quick brown fox jumps over the lazy dog")
>>> idx.add("doc2", "a quick brown dog outruns the quick fox")
>>> results = idx.search("quick fox")
>>> [r.doc_id for r in results]
['doc1', 'doc2']
"""

from .search import SearchIndex, SearchResult, Document

__all__ = ["SearchIndex", "SearchResult", "Document"]
__version__ = "0.1.0"