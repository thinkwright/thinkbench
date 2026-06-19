"""Leaf validators — the building blocks you compose into schemas."""

from __future__ import annotations

import re
from typing import Any, Callable, Pattern, Sequence

from .errors import Path, ValidationError, make
from .schema import Schema


# --- type checks ------------------------------------------------------------

def string(*, allow_empty: bool = False, strip: bool = False) -> Schema:
    """A string. By default empty strings are rejected."""
    def fn(value: Any, path: Path) -> Sequence[ValidationError]:
        if not isinstance(value, str):
            return [make(path, f"must be a string, got {type(value).__name__}")]
        if not allow_empty and (value.strip() if strip else value) == "":
            return [make(path, "must not be empty")]
        return ()
    return Schema(fn, name="string")


def number(*, coerce: bool = False) -> Schema:
    """An int or float (but not bool). With coerce=True, numeric strings are accepted."""
    def fn(value: Any, path: Path) -> Sequence[ValidationError]:
        # bool is a subclass of int — exclude it explicitly
        if isinstance(value, bool):
            return [make(path, "must be a number, got bool")]
        if isinstance(value, (int, float)):
            return ()
        if coerce and isinstance(value, str):
            try:
                float(value)
                return ()
            except ValueError:
                pass
        return [make(path, f"must be a number, got {type(value).__name__}")]
    return Schema(fn, name="number")


def integer(*, coerce: bool = False) -> Schema:
    """An int (not bool). With coerce=True, integer-shaped strings are accepted."""
    def fn(value: Any, path: Path) -> Sequence[ValidationError]:
        if isinstance(value, bool):
            return [make(path, "must be an integer, got bool")]
        if isinstance(value, int):
            return ()
        if coerce and isinstance(value, str):
            try:
                int(value)
                return ()
            except ValueError:
                pass
        return [make(path, f"must be an integer, got {type(value).__name__}")]
    return Schema(fn, name="integer")


def boolean() -> Schema:
    def fn(value: Any, path: Path) -> Sequence[ValidationError]:
        if not isinstance(value, bool):
            return [make(path, f"must be a boolean, got {type(value).__name__}")]
        return ()
    return Schema(fn, name="boolean")


def none() -> Schema:
    """Only None is accepted."""
    def fn(value: Any, path: Path) -> Sequence[ValidationError]:
        if value is not None:
            return [make(path, f"must be null, got {type(value).__name__}")]
        return ()
    return Schema(fn, name="none")


# --- structural -------------------------------------------------------------

def list_of(item: Schema, *, min_length: int = 0, max_length: int | None = None) -> Schema:
    """A list where every element matches `item`."""
    from .schema import list_schema
    return list_schema(item, min_length=min_length, max_length=max_length)


def dict_of(value: Schema, *, allow_extra: bool = True) -> Schema:
    """A dict whose values all match `value`. Keys are not constrained."""
    def fn(data: Any, path: Path) -> Sequence[ValidationError]:
        if not isinstance(data, dict):
            return [make(path, f"must be an object, got {type(data).__name__}")]
        errs: list = []
        for k, v in data.items():
            errs.extend(value._fn(v, path + (str(k),)))
        return errs
    return Schema(fn, name="dict_of")


# --- value constraints ------------------------------------------------------

def one_of(*choices: Any) -> Schema:
    """Value must equal one of `choices`."""
    def fn(value: Any, path: Path) -> Sequence[ValidationError]:
        if value in choices:
            return ()
        shown = ", ".join(repr(c) for c in choices)
        return [make(path, f"must be one of {shown}, got {value!r}")]
    return Schema(fn, name="one_of")


def length(min: int | None = None, max: int | None = None) -> Schema:
    """String length constraint."""
    def fn(value: Any, path: Path) -> Sequence[ValidationError]:
        if not isinstance(value, str):
            return [make(path, f"must be a string to check length, got {type(value).__name__}")]
        errs: list = []
        if min is not None and len(value) < min:
            errs.append(make(path, f"must be at least {min} characters, has {len(value)}"))
        if max is not None and len(value) > max:
            errs.append(make(path, f"must be at most {max} characters, has {len(value)}"))
        return errs
    return Schema(fn, name="length")


def range_(min: float | None = None, max: float | None = None) -> Schema:
    """Numeric range constraint."""
    def fn(value: Any, path: Path) -> Sequence[ValidationError]:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return [make(path, f"must be a number to check range, got {type(value).__name__}")]
        errs: list = []
        if min is not None and value < min:
            errs.append(make(path, f"must be >= {min}, got {value}"))
        if max is not None and value > max:
            errs.append(make(path, f"must be <= {max}, got {value}"))
        return errs
    return Schema(fn, name="range")


# --- format checks ----------------------------------------------------------

# A pragmatic email regex — not RFC 5322, but rejects the obviously wrong
# shapes without being a maintenance burden. Good enough for form input.
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def email() -> Schema:
    def fn(value: Any, path: Path) -> Sequence[ValidationError]:
        if not isinstance(value, str):
            return [make(path, f"must be a string, got {type(value).__name__}")]
        if not _EMAIL_RE.match(value):
            return [make(path, f"must be a valid email address, got {value!r}")]
        return ()
    return Schema(fn, name="email")


def regex(pattern: str | Pattern[str], *, message: str | None = None) -> Schema:
    """Value must match `pattern` (a regex string or compiled Pattern)."""
    pat = re.compile(pattern) if isinstance(pattern, str) else pattern
    msg = message or f"must match pattern {pat.pattern!r}"
    def fn(value: Any, path: Path) -> Sequence[ValidationError]:
        if not isinstance(value, str):
            return [make(path, f"must be a string, got {type(value).__name__}")]
        if not pat.search(value):
            return [make(path, msg)]
        return ()
    return Schema(fn, name="regex")


# --- escape hatches ---------------------------------------------------------

def predicate(fn: Callable[[Any], bool], message: str) -> Schema:
    """Run an arbitrary check. Use sparingly — prefer the named validators."""
    def validator(value: Any, path: Path) -> Sequence[ValidationError]:
        try:
            ok = fn(value)
        except Exception as exc:
            return [make(path, f"{message} (check raised {type(exc).__name__})")]
        if not ok:
            return [make(path, message)]
        return ()
    return Schema(validator, name="predicate")


# --- re-export the schema-level combinators under friendlier names ----------

def any_(*schemas: "Schema") -> "Schema":
    from .schema import any_of
    return any_of(*schemas)


def all_(*schemas: "Schema") -> "Schema":
    from .schema import all_of
    return all_of(*schemas)
