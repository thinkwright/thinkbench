"""Command-line interface for docsearch.

The CLI works against a JSON file on disk so you can keep a persistent
collection across sessions. The file format is::

    {
      "docs": [
        {"id": 1, "text": "..."},
        {"id": 2, "text": "..."}
      ]
    }

Usage::

    python -m docsearch add path/to/index.json path/to/doc.txt
    python -m docsearch search path/to/index.json "my query" --top 5
    python -m docsearch count path/to/index.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core import DocSearch


def _load_index(path: Path) -> DocSearch:
    ds = DocSearch()
    if not path.exists():
        return ds
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"error: {path} is not valid JSON: {e}", file=sys.stderr)
        sys.exit(2)
    for entry in data.get("docs", []):
        ds.add(entry["text"], doc_id=entry["id"])
    return ds


def _save_index(ds: DocSearch, path: Path) -> None:
    payload = {
        "docs": [
            {"id": doc.doc_id, "text": doc.text}
            for doc in sorted(ds._docs.values(), key=lambda d: d.doc_id)
        ]
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _cmd_add(args: argparse.Namespace) -> int:
    ds = _load_index(args.index)
    for src in args.files:
        text = Path(src).read_text(encoding="utf-8", errors="replace")
        doc_id = ds.add(text)
        print(f"added {src} as doc {doc_id}")
    _save_index(ds, args.index)
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    ds = _load_index(args.index)
    if len(ds) == 0:
        print("no documents indexed", file=sys.stderr)
        return 1
    results = ds.search(args.query, top_k=args.top)
    if not results:
        print("no matches")
        return 0
    for r in results:
        print(f"{r.score:.4f}\tdoc {r.doc_id}")
    return 0


def _cmd_count(args: argparse.Namespace) -> int:
    ds = _load_index(args.index)
    print(len(ds))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docsearch",
        description="Small TF-IDF document search.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="add documents to an index")
    p_add.add_argument("index", type=Path, help="path to the index JSON file")
    p_add.add_argument("files", nargs="+", type=Path, help="text files to add")
    p_add.set_defaults(func=_cmd_add)

    p_search = sub.add_parser("search", help="search the index")
    p_search.add_argument("index", type=Path, help="path to the index JSON file")
    p_search.add_argument("query", help="query string")
    p_search.add_argument("--top", type=int, default=10, help="number of results")
    p_search.set_defaults(func=_cmd_search)

    p_count = sub.add_parser("count", help="print number of indexed documents")
    p_count.add_argument("index", type=Path, help="path to the index JSON file")
    p_count.set_defaults(func=_cmd_count)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
