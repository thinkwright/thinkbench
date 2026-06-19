"""Loading configuration sources from files or accepting them as dicts."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping


def load_source(source: Any, name: str | None = None) -> tuple[str, dict]:
    """Load a configuration source into a (name, dict) pair.

    ``source`` may be:
      * a Mapping -> used directly
      * a path to a JSON file (``.json``)
      * a path to a YAML file (``.yaml``/``.yolo``/``.yml``), if PyYAML is installed

    ``name`` labels the source for provenance. If omitted it is inferred from
    the path (basename) or, for a Mapping, defaults to the provided/``dict``
    label.
    """
    if isinstance(source, Mapping):
        label = name if name is not None else "dict"
        return label, dict(source)

    if isinstance(source, (str, os.PathLike)):
        path = Path(source)
        suffix = path.suffix.lower()
        text = path.read_text(encoding="utf-8")
        if suffix == ".json":
            data = json.loads(text)
        elif suffix in (".yaml", ".yml"):
            data = _load_yaml(text)
        else:
            # Best effort: try JSON, then YAML. Lets operators point at a file
            # without worrying about the extension.
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = _load_yaml(text)
        if not isinstance(data, Mapping):
            raise ValueError(
                f"source {path!s} did not contain a mapping at the top level "
                f"(got {type(data).__name__})"
            )
        label = name if name is not None else path.name
        return label, dict(data)

    raise TypeError(
        f"unsupported source type {type(source).__name__}; expected a Mapping "
        f"or a path to a JSON/YAML file"
    )


def _load_yaml(text: str) -> Any:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - exercised only without yaml
        raise ImportError(
            "PyYAML is required to load YAML sources; install it or use JSON"
        ) from exc
    return yaml.safe_load(text)