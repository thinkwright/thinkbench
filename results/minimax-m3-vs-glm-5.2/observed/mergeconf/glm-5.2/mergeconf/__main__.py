"""Run mergeconf directly to inspect a merge without writing a script.

Usage:
    python -m mergeconf baseline.json env.yaml overrides.json
    python -m mergeconf --provenance baseline.json env.yaml overrides.json

Sources are applied in the order given (first = lowest precedence, last =
highest). The effective merged config is printed as JSON to stdout. With
``--provenance``, a ``__provenance__`` key is added alongside the config
showing which source supplied each leaf value.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from . import mergeconf


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mergeconf",
        description="Merge configuration sources into one effective view.",
    )
    parser.add_argument(
        "sources",
        nargs="+",
        help="Paths to JSON/YAML sources, in precedence order (last wins).",
    )
    parser.add_argument(
        "--provenance",
        action="store_true",
        help="Include a __provenance__ tree showing the source of each value.",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indent level (default: 2).",
    )
    args = parser.parse_args(argv)

    result = mergeconf(*args.sources)
    output: dict = {"config": result.config}
    if args.provenance:
        output["__provenance__"] = result.provenance

    json.dump(output, output=sys.stdout, indent=args.indent, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())