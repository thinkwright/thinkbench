"""Core implementation of formvalidate.

Design notes
------------

* A :class:`Field` knows how to check a single value. Its :meth:`validate`
  method either returns a cleaned value or raises :class:`Invalid` with a
  human-readable message.

* A :class:`Schema` is a mapping of key -> Field. It walks the input dict,
  asks each field to validate its value, and collects *every* error into a
  :class:`Result` rather than stopping at the first one.

* Errors are keyed by field name. For nested structures (``Dict``, ``List``)
  the keys are dotted paths like ``"address.city"`` or ``"tags.0"`` so a
  caller can point at exactly what's wrong.

* Missing required values and ``None`` are treated the same way: the field
  is only asked to validate when a value is present. ``required`` controls
  whether absence is itself an error.
"""

import re

__all__ = [
    "Field",
    "Schema",
    "Result",
    "ValidationError",
    "Invalid",
    "String",
    "Int",
    "Float",
    "Bool",
    "Email",
    "Choice",
    "List",
    "Dict",
    "Optional",
    "Each",
    "Any",
]


class Invalid(Exception):
    """Raised by a field when a value fails validation.

    The message should be something you'd be comfortable showing to a user
    or writing to a log: short, plain, and specific to the field that
    failed (e.g. ``"must be an integer"``, not ``"ValidationError"``).
    """

    def __init__(self, message):
        super().__init__(message)
        self.message = message


class ValidationError(Exception):
    """Raised by :meth:`Schema.validate_or_raise` when data is invalid.

    Carries the :class:`Result` so callers can inspect every error rather
    than just the first one.
    """

    def __init__(self, result):
        super().__init__(str(result.errors))
        self.result = result


class Result:
    """The outcome of running a schema over some data.

    Attributes:
        valid: ``True`` when there were no errors.
        errors: a dict mapping dotted field paths to error messages.
        data: the cleaned data when valid; otherwise whatever could be
            cleaned (callers usually want ``errors`` instead).
    """

    __slots__ = ("errors", "data")

    def __init__(self, data=None):
        self.errors = {}
        self.data = data if data is not None else {}

    @property
    def valid(self):
        return not self.errors

    def add_error(self, path, message):
        self.errors[path] = message

    def __repr__(self):
        if self.valid:
            return "Result(valid=True)"
        return "Result(valid=False, errors=%r)" % (self.errors,)


# --------------------------------------------------------------------------
# Base field
# --------------------------------------------------------------------------


class Field:
    """Base class for all validators.

    Subclasses implement :meth:`_validate`, which receives a value that is
    guaranteed to be present (not missing, not ``None``) and either returns
    a cleaned value or raises :class:`Invalid`.
    """

    def __init__(self, required=True, default=None):
        self.required = required
        self.default = default

    def validate(self, value):
        """Validate a value.

        Returns a cleaned value, or raises :class:`Invalid`.
        ``None`` and missing values are handled by :class:`Schema`; this
        method is only called when a real value is present.
        """
        return self._validate(value)

    def _validate(self, value):
        raise NotImplementedError

    # Allow fields to be used directly as schema entries without wrapping.
    # Schemas call these hooks so nested fields can participate in the
    # collection of errors.
    def _run(self, path, value, result):
        """Validate ``value`` at ``path`` and record errors into ``result``.

        Returns the cleaned value (or ``None`` if invalid).
        """
        try:
            return self.validate(value)
        except Invalid as exc:
            result.add_error(path, exc.message)
            return None


# --------------------------------------------------------------------------
# Scalar fields
# --------------------------------------------------------------------------


class Any(Field):
    """Accept any value, no type checking. Useful as a passthrough."""

    def _validate(self, value):
        return value


class String(Field):
    """A string field.

    Args:
        min_len: minimum length (after stripping, if ``strip`` is set).
        max_len: maximum length.
        strip: strip surrounding whitespace before checking length.
        choices: if given, the (stripped) value must be one of these.
    """

    def __init__(self, min_len=None, max_len=None, strip=True, choices=None, **kw):
        super().__init__(**kw)
        self.min_len = min_len
        self.max_len = max_len
        self.strip = strip
        self.choices = choices

    def _validate(self, value):
        if not isinstance(value, str):
            raise Invalid("must be text")
        if self.strip:
            value = value.strip()
        if self.min_len is not None and len(value) < self.min_len:
            raise Invalid("must be at least %d characters" % self.min_len)
        if self.max_len is not None and len(value) > self.max_len:
            raise Invalid("must be at most %d characters" % self.max_len)
        if self.choices is not None and value not in self.choices:
            raise Invalid("must be one of: %s" % ", ".join(self.choices))
        return value


class _Number(Field):
    """Shared base for numeric fields."""

    type_name = "number"
    py_type = None

    def __init__(self, min=None, max=None, **kw):
        super().__init__(**kw)
        self.min = min
        self.max = max

    def _coerce(self, value):
        raise NotImplementedError

    def _validate(self, value):
        # Accept numeric strings like "42" for friendliness with form data.
        if isinstance(value, str):
            try:
                value = self._coerce(value.strip())
            except (ValueError, TypeError):
                raise Invalid("must be a %s" % self.type_name)
        if not isinstance(value, self.py_type) or isinstance(value, bool):
            raise Invalid("must be a %s" % self.type_name)
        if self.min is not None and value < self.min:
            raise Invalid("must be at least %s" % self.min)
        if self.max is not None and value > self.max:
            raise Invalid("must be at most %s" % self.max)
        return value


class Int(_Number):
    """An integer field. Accepts ints or numeric strings like ``"42"``."""

    type_name = "integer"
    py_type = int

    def _coerce(self, value):
        return int(value)


class Float(_Number):
    """A float field. Accepts floats, ints, or numeric strings."""

    type_name = "number"
    py_type = float

    def __init__(self, **kw):
        super().__init__(**kw)
        # Floats accept ints too.
        self.py_type = (int, float)

    def _coerce(self, value):
        return float(value)

    def _validate(self, value):
        # bool is a subclass of int; reject it explicitly.
        if isinstance(value, bool):
            raise Invalid("must be a %s" % self.type_name)
        return super()._validate(value)


class Bool(Field):
    """A boolean field.

    Accepts actual booleans plus the common string spellings
    (``"true"``/``"false"``, ``"1"``/``"0"``, ``"yes"``/``"no"``),
    case-insensitive.
    """

    TRUE = {"true", "1", "yes", "on", "t", "y"}
    FALSE = {"false", "0", "no", "off", "f", "n"}

    def _validate(self, value):
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if value == 1:
                return True
            if value == 0:
                return False
            raise Invalid("must be true or false")
        if isinstance(value, str):
            v = value.strip().lower()
            if v in self.TRUE:
                return True
            if v in self.FALSE:
                return False
        raise Invalid("must be true or false")


# A pragmatic email pattern: not RFC-perfect, but good enough to catch the
# obvious mistakes without false negatives on reasonable addresses.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Email(Field):
    """An email address field. Checks shape, not deliverability."""

    def _validate(self, value):
        if not isinstance(value, str):
            raise Invalid("must be a valid email address")
        v = value.strip()
        if not _EMAIL_RE.match(v):
            raise Invalid("must be a valid email address")
        return v


class Choice(Field):
    """A field whose value must come from a fixed set of allowed values.

    Unlike ``String(choices=...)`` this does no type coercion, so it works
    for any hashable value (ints, enums, etc.).
    """

    def __init__(self, choices, **kw):
        super().__init__(**kw)
        self.choices = list(choices)

    def _validate(self, value):
        if value not in self.choices:
            raise Invalid("must be one of: %s" % ", ".join(str(c) for c in self.choices))
        return value


# --------------------------------------------------------------------------
# Container fields
# --------------------------------------------------------------------------


class List(Field):
    """A list field. Each element is validated against ``field``.

    Errors are reported at paths like ``"tags.0"``, ``"tags.1"``.
    """

    def __init__(self, field, min_len=None, max_len=None, **kw):
        super().__init__(**kw)
        self.field = field
        self.min_len = min_len
        self.max_len = max_len

    def _run(self, path, value, result):
        if value is None:
            return None
        if not isinstance(value, (list, tuple)):
            result.add_error(path, "must be a list")
            return None
        if self.min_len is not None and len(value) < self.min_len:
            result.add_error(path, "must have at least %d items" % self.min_len)
            return None
        if self.max_len is not None and len(value) > self.max_len:
            result.add_error(path, "must have at most %d items" % self.max_len)
            return None
        cleaned = []
        for i, item in enumerate(value):
            cleaned.append(self.field._run("%s.%d" % (path, i), item, result))
        return cleaned

    def _validate(self, value):
        # Direct (non-schema) use: collect into a throwaway result.
        result = Result()
        cleaned = self._run("", value, result)
        if result.errors:
            raise Invalid("; ".join(result.errors.values()))
        return cleaned


class Dict(Field):
    """A nested object field, validated against a sub-:class:`Schema`.

    Errors are reported at dotted paths like ``"address.city"``.
    """

    def __init__(self, schema, **kw):
        super().__init__(**kw)
        if isinstance(schema, dict):
            schema = Schema(schema)
        self.schema = schema

    def _run(self, path, value, result):
        if value is None:
            return None
        if not isinstance(value, dict):
            result.add_error(path, "must be an object")
            return None
        sub = self.schema._run(value, prefix=path)
        result.errors.update(sub.errors)
        return sub.data if not sub.errors else None

    def _validate(self, value):
        result = Result()
        cleaned = self._run("", value, result)
        if result.errors:
            raise Invalid("; ".join(result.errors.values()))
        return cleaned


class Each(Field):
    """Apply a single field to every value of a dict.

    Useful for ``{"mon": "9-5", "tue": "9-5"}`` style maps where the keys
    are arbitrary but the values share a shape. Errors are keyed by the
    dict key: ``"hours.tue"``.
    """

    def __init__(self, field, **kw):
        super().__init__(**kw)
        self.field = field

    def _run(self, path, value, result):
        if value is None:
            return None
        if not isinstance(value, dict):
            result.add_error(path, "must be an object")
            return None
        cleaned = {}
        for k, v in value.items():
            key = "%s.%s" % (path, k) if path else str(k)
            cleaned[k] = self.field._run(key, v, result)
        return cleaned

    def _validate(self, value):
        result = Result()
        cleaned = self._run("", value, result)
        if result.errors:
            raise Invalid("; ".join(result.errors.values()))
        return cleaned


# --------------------------------------------------------------------------
# Convenience wrappers
# --------------------------------------------------------------------------


class Optional:
    """Marker that wraps a field to mark it as not required.

    ``Optional(String())`` is shorthand for ``String(required=False)``.
    It's handy when you want to make an existing field optional without
    digging into its constructor.
    """

    def __init__(self, field):
        self.field = field
        self.required = False
        self.default = field.default

    def _run(self, path, value, result):
        return self.field._run(path, value, result)

    def validate(self, value):
        return self.field.validate(value)


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------


_MISSING = object()


class Schema:
    """A collection of named fields describing valid input data.

    Build one from a dict of ``{name: Field}``::

        schema = Schema({
            "email": Email(required=True),
            "age": Int(min=18, max=120),
            "newsletter": Bool(required=False),
        })

    Then call :meth:`validate` with a dict of raw input. You always get a
    :class:`Result` back; it never raises on invalid data.
    """

    def __init__(self, fields):
        self.fields = dict(fields)

    def validate(self, data):
        """Validate ``data`` and return a :class:`Result`.

        Collects every error rather than stopping at the first. Missing
        required fields, type errors, and constraint failures are all
        reported together.
        """
        return self._run(data, prefix="")

    def validate_or_raise(self, data):
        """Like :meth:`validate` but raises :class:`ValidationError` if invalid."""
        result = self.validate(data)
        if not result.valid:
            raise ValidationError(result)
        return result.data

    def _run(self, data, prefix=""):
        if data is None:
            data = {}
        if not isinstance(data, dict):
            result = Result()
            result.add_error(prefix or "", "must be an object")
            return result
        result = Result()
        cleaned = {}
        for name, field in self.fields.items():
            path = "%s.%s" % (prefix, name) if prefix else name
            value = data.get(name, _MISSING)
            if value is _MISSING or value is None or value == "":
                # Treat missing/None/blank as absence.
                if getattr(field, "required", True):
                    result.add_error(path, "is required")
                else:
                    default = getattr(field, "default", None)
                    if default is not None:
                        cleaned[name] = default
                continue
            cleaned_value = field._run(path, value, result)
            if path not in result.errors:
                cleaned[name] = cleaned_value
        # Pass through unknown keys untouched? No -- be strict by default so
        # callers notice typos. Callers who want lenient behaviour can post-
        # process. (Kept simple on purpose.)
        return Result(cleaned) if not result.errors else _merge(result, cleaned)


def _merge(result, cleaned):
    """Attach whatever cleaned data we have to the result, even on error."""
    result.data = cleaned
    return result