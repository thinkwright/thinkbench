"""The core merge logic for mergeconf."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .load import load_source


@dataclass(frozen=True)
class Source:
    """A named configuration source, in precedence order.

    Sources earlier in the list passed to :func:`mergeconf` have lower
    precedence; later sources win.
    """

    name: str
    config: Mapping[str, Any]


@dataclass
class MergeResult:
    """The outcome of a merge: the effective config plus provenance.

    ``config`` is the resolved, effective configuration tree.
    ``provenance`` is a parallel tree mapping each leaf key to the name of the
    source that supplied its effective value.
    """

    config: dict[str, Any]
    provenance: dict[str, Any] = field(default_factory=dict)

    def provenance_for(self, dotted: str) -> str | None:
        """Return the source name that supplied the value at ``dotted`` path.

        ``dotted`` is a dot-separated path like ``"db.pool.size"``. Returns
        ``None`` if the path does not resolve to a leaf that has provenance.
        """
        cur_cfg = self.config
        cur_prov = self.provenance
        parts = dotted.split(".")
        for i, part in enumerate(parts):
            is_last = i == len(parts) - 1
            if not isinstance(cur_cfg, Mapping) or part not in cur_cfg:
                return None
            if is_last:
                return cur_prov.get(part) if isinstance(cur_prov, Mapping) else None
            cur_cfg = cur_cfg[part]
            cur_prov = cur_prov[part] if isinstance(cur_prov, Mapping) else {}
        return None


def mergeconf(
    *sources: Any,
    names: list[str] | None = None,
) -> MergeResult:
    """Merge configuration sources into a single effective view.

    Each item in ``sources`` is a Mapping or a path to a JSON/YAML file (see
    :func:`mergeconf.load.load_source`). Sources are applied in order: the
    first is the lowest-precedence baseline, the last is the highest-precedence
    override.

    Returns a :class:`MergeResult` with the resolved config and per-leaf
    provenance.

    ``names`` optionally labels sources for provenance when you pass raw
    mappings; it must match the number of sources. When a source is a file
    path its name is inferred automatically and ``names`` is ignored for it.
    """
    if not sources:
        return MergeResult({}, {})

    if names is not None and len(names) != len(sources):
        raise ValueError(
            f"names has {len(names)} entries but {len(sources)} sources were given"
        )

    resolved: list[Source] = []
    for i, src in enumerate(sources):
        label = names[i] if names is not None else None
        name, config = load_source(src, label)
        resolved.append(Source(name=name, config=config))

    config: dict[str, Any] = {}
    provenance: dict[str, Any] = {}
    for src in resolved:
        _merge_into(config, provenance, src.config, src.name)
    return MergeResult(config=config, provenance=provenance)


def _merge_into(
    acc: dict[str, Any],
    prov: dict[str, Any],
    incoming: Mapping[str, Any],
    source_name: str,
) -> None:
    """Deep-merge ``incoming`` (from ``source_name``) into ``acc``/``prov``.

    For each key:
      * if both the accumulated value and the incoming value are mappings,
        recurse (deep merge);
      * otherwise the incoming value replaces the accumulated value, and the
        provenance is stamped with ``source_name``.

    A shape mismatch (mapping vs non-mapping) is resolved by letting the
        higher-precedence incoming value win outright, rather than coercing.
    """
    for key, value in incoming.items():
        if (
            isinstance(value, Mapping)
            and isinstance(acc.get(key), Mapping)
        ):
            child_acc = acc[key]
            child_prov = prov.setdefault(key, {})
            _merge_into(child_acc, child_prov, value, source_name)
        else:
            acc[key] = _clone(value)
            prov[key] = source_name


def _clone(value: Any) -> Any:
    """Return a shallow copy of mappings so sources don't alias into the result."""
    if isinstance(value, Mapping):
        return {k: _clone(v) for k, v in value.items()}
    return value