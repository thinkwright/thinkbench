"""mergeconf — layered configuration with provenance.

Merge configuration from several sources into a single effective view,
and record where each leaf came from so the result is auditable.

Typical layering (lowest to highest priority):
    defaults  ->  per-environment file  ->  CLI/runtime overrides

Public API:
    merge(sources) -> (effective: dict, provenance: dict)
    load(path)     -> dict   (JSON or YAML, by extension)

CLI:
    mergeconf [files...] [--override k=v ...] [--explain]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Iterable

try:
    import yaml  # type: ignore
except ImportError:  # YAML is optional; only needed for .yaml/.yml files.
    yaml = None


# Sentinel meaning "no override at this key". We can't use None because
# None is a legitimate config value (e.g. {"logging": null} to disable).
_NO_OVERRIDE = object()


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load(path: str) -> dict:
    """Load a config file as a dict.

    Format is chosen by extension: .yaml/.yml -> YAML, anything else -> JSON.
    Raises a clear error if the file is unreadable or not a mapping.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"config file not found: {path}")

    _, ext = os.path.splitext(path)
    ext = ext.lower()

    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()

    if ext in (".yaml", ".yml"):
        if yaml is None:
            raise RuntimeError(
                f"{path} looks like YAML but PyYAML is not installed; "
                "pip install pyyaml"
            )
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(
            f"{path}: top-level config must be a mapping, got {type(data).__name__}"
        )
    return data


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------

# Lists are values, not maps: they are replaced wholesale, not element-wise
# merged. This is the only defensible default — list merging has no
# universally correct answer (concat? union? dedupe? by index?), and
# guessing wrong is worse than being explicit. Callers who want list
# merging should do it themselves before handing config to mergeconf.


def _join(path: str, key: str) -> str:
    """Build a dotted provenance path. Avoids the leading-dot artifact
    that comes from concatenating '' + '.' + 'foo'."""
    return f"{path}.{key}" if path else key


def _merge_value(
    base: Any,
    override: Any,
    path: str,
    source: str,
    provenance: dict,
) -> Any:
    """Merge two values, recording provenance for the resulting leaf/subtree.

    `override` may be the _NO_OVERRIDE sentinel, meaning "this key was not
    present in the higher-priority source — keep base as-is". This is how
    we distinguish "absent" from "present with value None".
    """
    # If both sides are dicts, recurse — this is what makes nested config
    # behave sensibly. Operators can override one nested key without
    # restating the whole subtree.
    if isinstance(base, dict) and isinstance(override, dict):
        merged: dict = {}
        # Start with base keys so untouched values keep their provenance.
        for k, v in base.items():
            child_override = override.get(k, _NO_OVERRIDE)
            merged[k] = _merge_value(v, child_override, _join(path, k), source, provenance)
        # Then add keys only present in override.
        for k, v in override.items():
            if k not in base:
                merged[k] = _merge_value(_NO_OVERRIDE, v, _join(path, k), source, provenance)
        return merged

    # No override at this key -> keep base, but still record provenance
    # so the trail is complete (every leaf has a source).
    if override is _NO_OVERRIDE:
        provenance[path] = {"source": source, "value": base}
        return base

    # Otherwise override wins. This covers:
    #   - scalar vs scalar (last writer wins)
    #   - list vs list (replaced wholesale)
    #   - type mismatches (override's type wins; we record the conflict
    #     so the trail shows what happened)
    if base is _NO_OVERRIDE or type(base) is not type(override) or base != override:
        entry: dict = {"source": source, "value": override}
        if base is not _NO_OVERRIDE:
            entry["replaced"] = {"source": "(lower priority)", "value": base}
        provenance[path] = entry
    else:
        # Same value, same type — no conflict, but still record source.
        provenance[path] = {"source": source, "value": override}
    return override


def merge(sources: Iterable[tuple[str, dict]]) -> tuple[dict, dict]:
    """Merge a sequence of (name, config) pairs into one effective config.

    Earlier sources have lower priority; later sources override them.
    Returns (effective, provenance) where provenance is a dict keyed by
    dotted path, e.g. {"server.port": {"source": "env.json", "value": 8080}}.
    """
    effective: dict = {}
    provenance: dict = {}

    for name, cfg in sources:
        if not isinstance(cfg, dict):
            raise ValueError(
                f"source {name!r} must be a mapping, got {type(cfg).__name__}"
            )
        effective = _merge_value(effective, cfg, "", name, provenance)

    return effective, provenance


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_override(spec: str) -> tuple[str, Any]:
    """Parse a 'key.path=value' override.

    Value is parsed as JSON if it looks like JSON (number, bool, null,
    object, array, quoted string); otherwise treated as a raw string.
    This lets operators write --override port=8080 or --override debug=true
    without quoting, while still allowing --override name='"prod"'.
    """
    if "=" not in spec:
        raise argparse.ArgumentTypeError(
            f"override {spec!r} must be of the form key.path=value"
        )
    key, raw = spec.split("=", 1)
    if not key:
        raise argparse.ArgumentTypeError(f"override {spec!r} has empty key")

    # Try JSON first; fall back to string. json.loads("hello") fails,
    # which is exactly the signal we want.
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = raw
    return key, value


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mergeconf",
        description="Merge layered config files with CLI overrides.",
    )
    p.add_argument(
        "files",
        nargs="*",
        help="Config files in increasing priority order (e.g. defaults.json env.json).",
    )
    p.add_argument(
        "--override", "-o",
        action="append",
        default=[],
        type=_parse_override,
        metavar="KEY=VALUE",
        help="Override a dotted key. May be repeated. Value parsed as JSON if possible.",
    )
    p.add_argument(
        "--explain", "-e",
        action="store_true",
        help="Also print provenance (where each leaf came from) as a second JSON object.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    sources: list[tuple[str, dict]] = []
    for path in args.files:
        sources.append((path, load(path)))

    if args.override:
        # Build a synthetic source from --override flags. Using a dict
        # with dotted keys lets us reuse the same merge logic.
        override_cfg: dict = {}
        for key, value in args.override:
            _set_dotted(override_cfg, key, value)
        sources.append(("cli", override_cfg))

    effective, provenance = merge(sources)

    if args.explain:
        out = {"config": effective, "provenance": provenance}
        json.dump(out, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        json.dump(effective, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    return 0


def _set_dotted(d: dict, dotted_key: str, value: Any) -> None:
    """Set d['a']['b']['c'] = value from the dotted key 'a.b.c'."""
    parts = dotted_key.split(".")
    cur = d
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
        if not isinstance(cur, dict):
            raise ValueError(
                f"override key {dotted_key!r} conflicts with a non-mapping value"
            )
    cur[parts[-1]] = value


if __name__ == "__main__":
    sys.exit(main())
