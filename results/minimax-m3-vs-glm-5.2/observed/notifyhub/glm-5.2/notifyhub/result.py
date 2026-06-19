"""Outcomes we can see and reason about.

Every channel attempt produces a :class:`SendResult`.  A
:class:`SendReport` gathers the results of one :meth:`Hub.notify` call.
Failures are recorded, never swallowed: a result with a non-OK status
always carries the exception (or a message describing what went wrong),
and the report exposes ``failures`` so callers can react.
"""

from __future__ import annotations

import enum
from typing import Iterator, List, Optional


class Status(enum.Enum):
    OK = "ok"
    FAILED = "failed"
    SKIPPED = "skipped"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class SendResult:
    """The outcome of one channel attempt."""

    __slots__ = ("channel", "status", "error", "detail")

    def __init__(
        self,
        channel: str,
        status: Status,
        error: Optional[BaseException] = None,
        detail: str = "",
    ) -> None:
        self.channel = channel
        self.status = status
        self.error = error
        self.detail = detail

    @property
    def ok(self) -> bool:
        return self.status is Status.OK

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"SendResult(channel={self.channel!r}, status={self.status}, "
            f"detail={self.detail!r})"
        )


class SendReport:
    """The collected results of a single ``notify`` call."""

    __slots__ = ("_results",)

    def __init__(self, results: Optional[List[SendResult]] = None) -> None:
        self._results = list(results) if results else []

    def add(self, result: SendResult) -> None:
        self._results.append(result)

    def __iter__(self) -> Iterator[SendResult]:
        return iter(self._results)

    def __len__(self) -> int:
        return len(self._results)

    @property
    def results(self) -> List[SendResult]:
        return list(self._results)

    @property
    def ok(self) -> bool:
        """True if every attempted send succeeded (skips don't count)."""
        return all(r.ok for r in self._results if r.status is not Status.SKIPPED)

    @property
    def attempted(self) -> List[SendResult]:
        return [r for r in self._results if r.status is not Status.SKIPPED]

    @property
    def failures(self) -> List[SendResult]:
        return [r for r in self._results if r.status is Status.FAILED]

    @property
    def skipped(self) -> List[SendResult]:
        return [r for r in self._results if r.status is Status.SKIPPED]

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"SendReport(ok={self.ok}, attempted={len(self.attempted)}, "
            f"failures={len(self.failures)})"
        )