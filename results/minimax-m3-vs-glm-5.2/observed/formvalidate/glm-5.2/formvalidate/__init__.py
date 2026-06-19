"""formvalidate - validate user-submitted data and report every problem.

The public surface is intentionally small:

    >>> import formvalidate as fv
    >>> schema = fv.Schema({
    ...     "email": fv.Email(required=True),
    ...     "age": fv.Int(min=18, max=120),
    ... })
    >>> result = schema.validate({"email": "x@y.com", "age": 30})
    >>> result.valid
    True

When data is invalid you get back a :class:`Result` that collects *every*
error, not just the first one, so the caller can surface them all at once::

    >>> result = schema.validate({"email": "nope", "age": "old"})
    >>> result.valid
    False
    >>> result.errors["email"]
    'must be a valid email address'
    >>> result.errors["age"]
    'must be an integer'
"""

from .core import (
    Field,
    Schema,
    Result,
    ValidationError,
    Invalid,
    String,
    Int,
    Float,
    Bool,
    Email,
    Choice,
    List,
    Dict,
    Optional,
    Each,
    Any as AnyField,
)

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

__version__ = "0.1.0"