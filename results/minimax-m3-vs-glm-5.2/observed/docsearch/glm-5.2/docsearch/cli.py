"""Command-line interface for docsearch.

Usage
-----
    # Index every .txt file in a directory (recursively) and search it:
    python -m docsearch "quick brown fox" ./notes

    # Index specific files:
    python -m docsearch "quick brown fox" a.txt b.txt

    # Limit / show snippets:
    python -m docsearch "apple" ./docs --limit 5 --snippets

Documents are keyed by their path relative to the current directory.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List

from .search import SearchIndex


def _iter_files(paths: List[str]):
    """Yield (doc_id, text) for each readable text file under the given paths.

    Directories are walked recursively. Files that can't be decoded as UTF-8
    are skipped with a warning on stderr.
    """
    for path in paths:
        if os.path.isdir(path):
            for root, _dirs, files in os.walk(path):
                for name in sorted(files):
                    yield from _yield_file(os.path.join(root, name))
        else:
            yield from _yield_file(path)


def _yield_file(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        print(f"warning: skipping {path}: {exc}", file=sys.stderr)
        return
    yield path, text


def _snippet(text: str, query_terms: List[str], width: int = 80) -> str:
    """Return a short snippet of text around the first query-term hit."""
    lower = text.lower()
    pos = -1
    for term in query_terms:
        idx = lower.find(term)
        if idx != -1 and (pos == -1 or idx < pos):
            pos = idx
    if pos == -1:
        pos = 0
    start = max(0, pos - width // 2)
    end = min(len(text), start + width)
    snippet = text[start:end].replace("\n", " ").strip()
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{snippet}{suffix}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m docsearch",
        description="Search a collection of text documents by relevance.",
    )
    parser.add_argument("query", help="the search query")
    parser.add_argument(
        "paths", nargs="+", help="files or directories to index (dirs are recursive)"
    )
    parser.add_argument(
        "--limit", "-n", type=int, default=10, help="max results to show (default 10)"
    )
    parser.add_argument(
        "--snippets", "-s", action="store_true", help="show a snippet of each match"
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=0.0,
        help="drop results below this score (default 0)",
    )
    args = parser.parse_args(argv)

    idx = SearchIndex()
    count = 0
    for doc_id, text in _iter_files(args.paths):
        idx.add(doc_id, text)
        count += 1

    if count == 0:
        print("no readable documents found", file=sys.stderr)
        return 1

    results = idx.search(args.query, limit=args.limit, min_score=args.min_score)
    if not results:
        print("no matches")
        return 0

    query_terms = [t for t in args.query.lower().split() if len(t) > 1]
    for r in results:
        print(f"{r.score:.4f}\t{r.doc_id}")
        if args.snippets:
            doc = idx.get(r.doc_id)
            if doc is not None:
                print(f"\t{_snippet(doc.text, query_terms)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())