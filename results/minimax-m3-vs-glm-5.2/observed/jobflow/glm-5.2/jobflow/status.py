"""The :class:`Status` enum describes the outcome of a single job in a run."""

from enum import Enum


class Status(str, Enum):
    """How a job ended up during a run.

    * ``PENDING``   -- not yet considered (used before a run and for unreachable jobs).
    * ``RUNNING``   -- currently executing.
    * ``SUCCESS``   -- finished without raising.
    * ``FAILED``    -- ran and raised an exception.
    * ``SKIPPED``   -- not run because one of its dependencies did not succeed.
    """

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value