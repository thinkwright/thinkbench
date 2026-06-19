"""Error types returned by formvalidate.

A ValidationError carries a path (a tuple of keys leading to the offending
value) and a human-readable message. ErrorList is a thin list subclass that
formats itself nicely when printed or logged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator, List, Sequence, Tuple

Path = Tuple[str, ...]


@dataclass(frozen=True)
class ValidationError:
    path: Path
    message: str

    def __str__(self) -> str:
        if not self.path:
            return self.message
        return f"{'.'.join(self.path)}: {self.message}"


class ErrorList(List[ValidationError]):
    """A list of ValidationError that formats itself readably."""

    def __init__(self, errors: Iterable[ValidationError] = ()) -> None:
        super().__init__(errors)

    def __str__(self) -> str:
        if not self:
            return "(no errors)"
        return "\n".join(str(e) for e in self)

    def by_path(self) -> dict:
        """Group errors by their dotted path."""
        out: dict = {}
        for err in self:
            key = ".".join(err.path) if err.path else "<root>"
            out.setdefault(key, []).append(err)
        return out

    def __bool__(self) -> bool:  # an ErrorList is "truthy" iff it has errors
        return len(self) > 0


def make(path: Path, message: str) -> ValidationError:
    return ValidationError(path=tuple(path), message=message)
