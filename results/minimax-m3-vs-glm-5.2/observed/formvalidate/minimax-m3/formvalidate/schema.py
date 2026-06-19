"""Schema and the core validate() entry point.

A Schema is a callable: given a value and a path, it returns a list of
ValidationError. Schemas compose — a dict schema's value is another schema,
so nesting is just nesting.

The two helpers `required` and `optional` mark whether a field may be absent
or null. By default a field is required.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, NamedTuple, Sequence, Tuple

from .errors import ErrorList, Path, ValidationError, make

Validator = Callable[[Any, Path], Sequence[ValidationError]]


class Schema:
    """A composable validator. Call it on a value to get an ErrorList."""

    __slots__ = ("_fn", "_name")

    def __init__(self, fn: Validator, name: str = "schema") -> None:
        self._fn = fn
        self._name = name

    def __call__(self, value: Any, path: Path = ()) -> ErrorList:
        return ErrorList(self._fn(value, path))

    def __repr__(self) -> str:
        return f"Schema({self._name!r})"


# --- field presence ---------------------------------------------------------

class _Marker:
    pass


class _Required(_Marker):
    def __repr__(self) -> str:
        return "required"


class _Optional(_Marker):
    def __repr__(self) -> str:
        return "optional"


REQUIRED = _Required()
OPTIONAL = _Optional()


def required(schema: Schema) -> Schema:
    """Mark a field as required (the default). Provided for readability."""
    return schema


def optional(schema: Schema) -> Schema:
    """Mark a field as optional — None or missing is allowed."""
    def fn(value: Any, path: Path) -> Sequence[ValidationError]:
        if value is None:
            return ()
        return schema._fn(value, path)
    return Schema(fn, name=f"optional({schema._name})")


# --- result -----------------------------------------------------------------

class Result(NamedTuple):
    ok: bool
    errors: ErrorList
    value: Any  # the (possibly coerced) validated value


def validate(schema: Schema, data: Any) -> Result:
    """Run `schema` against `data`. Returns a Result with .ok, .errors, .value."""
    errors = schema(data, ())
    return Result(ok=not errors, errors=errors, value=data)


# --- dict schema ------------------------------------------------------------

def _is_missing(value: Any) -> bool:
    return value is None


def dict_schema(fields: Mapping[str, Schema], *, allow_extra: bool = False) -> Schema:
    """Validate a dict against per-field schemas.

    By default unknown keys are reported as errors. Pass allow_extra=True to
    silently ignore them.
    """
    def fn(value: Any, path: Path) -> Sequence[ValidationError]:
        errs: list = []
        if not isinstance(value, Mapping):
            errs.append(make(path, f"expected an object, got {type(value).__name__}"))
            return errs

        for key, sub in fields.items():
            sub_path = path + (key,)
            if key not in value:
                # required by default; optional() wraps the schema and would
                # have produced no error for None, but a missing key is still
                # missing — we treat missing as required unless the schema is
                # explicitly optional AND the value would have been None.
                if isinstance(sub, Schema) and _is_optional(sub):
                    continue
                errs.append(make(sub_path, "is required"))
                continue
            errs.extend(sub._fn(value[key], sub_path))

        if not allow_extra:
            for key in value:
                if key not in fields:
                    errs.append(make(path + (key,), "is not a known field"))

        return errs

    return Schema(fn, name="dict")


def _is_optional(schema: Schema) -> bool:
    return schema._name.startswith("optional(")


# --- list schema ------------------------------------------------------------

def list_schema(item: Schema, *, min_length: int = 0, max_length: int | None = None) -> Schema:
    """Validate a list where every element matches `item`."""
    def fn(value: Any, path: Path) -> Sequence[ValidationError]:
        errs: list = []
        if not isinstance(value, (list, tuple)):
            errs.append(make(path, f"expected a list, got {type(value).__name__}"))
            return errs
        if len(value) < min_length:
            errs.append(make(path, f"must have at least {min_length} item(s), has {len(value)}"))
        if max_length is not None and len(value) > max_length:
            errs.append(make(path, f"must have at most {max_length} item(s), has {len(value)}"))
        for i, v in enumerate(value):
            errs.extend(item._fn(v, path + (str(i),)))
        return errs
    return Schema(fn, name="list")


# --- conditional ------------------------------------------------------------

def when(condition: Callable[[Mapping], bool], then: Schema, otherwise: Schema | None = None) -> Schema:
    """Apply `then` only when `condition(data)` is true.

    `condition` receives the full dict being validated. If `otherwise` is
    given, it is applied in the other case; otherwise nothing is checked.
    """
    def fn(value: Any, path: Path) -> Sequence[ValidationError]:
        if not isinstance(value, Mapping):
            # condition can't be evaluated; let the dict schema complain
            return ()
        if condition(value):
            return then._fn(value, path)
        if otherwise is not None:
            return otherwise._fn(value, path)
        return ()
    return Schema(fn, name="when")


# --- combinators ------------------------------------------------------------

def any_of(*schemas: Schema) -> Schema:
    """Pass if at least one schema passes. Errors from all are reported."""
    def fn(value: Any, path: Path) -> Sequence[ValidationError]:
        all_errs: list = []
        for s in schemas:
            errs = s._fn(value, path)
            if not errs:
                return ()
            all_errs.extend(errs)
        return [make(path, "did not match any allowed shape")] + all_errs
    return Schema(fn, name="any_of")


def all_of(*schemas: Schema) -> Schema:
    """All schemas must pass."""
    def fn(value: Any, path: Path) -> Sequence[ValidationError]:
        errs: list = []
        for s in schemas:
            errs.extend(s._fn(value, path))
        return errs
    return Schema(fn, name="all_of")
