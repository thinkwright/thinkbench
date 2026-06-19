"""formvalidate — describe what valid data looks like, get back every problem at once."""

from .schema import (
    Schema,
    optional,
    required,
    validate,
)
from .errors import ValidationError, ErrorList
from .validators import (
    any_,
    all_,
    none,
    string,
    number,
    integer,
    boolean,
    list_of,
    dict_of,
    one_of,
    email,
    regex,
    length,
    range_,
    predicate,
)

__all__ = [
    "Schema",
    "optional",
    "required",
    "validate",
    "ValidationError",
    "ErrorList",
    "any_",
    "all_",
    "none",
    "string",
    "number",
    "integer",
    "boolean",
    "list_of",
    "dict_of",
    "one_of",
    "email",
    "regex",
    "length",
    "range_",
    "predicate",
]

__version__ = "0.1.0"
