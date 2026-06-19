"""A Job is a unit of work that may depend on other Jobs."""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional


class JobStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class JobResult:
    """The outcome of running a single Job."""

    job: "Job"
    status: JobStatus
    value: Any = None
    error: Optional[BaseException] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None

    @property
    def duration(self) -> Optional[float]:
        if self.started_at is None or self.finished_at is None:
            return None
        return self.finished_at - self.started_at

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"JobResult({self.job.name!r}, {self.status.value})"


class Job:
    """A unit of work that may depend on other Jobs.

    A Job wraps a callable. When the Job runs, the callable is invoked with the
    results of the Jobs it depends on (in declaration order) as positional
    arguments. The callable's return value becomes the Job's result.

    Example::

        def build():
            return "built"

        def test(build_result):
            assert build_result == "built"
            return "tested"

        def deploy(test_result):
            return f"deployed after {test_result}"

        a = Job(build, name="build")
        b = Job(test, name="test", depends_on=[a])
        c = Job(deploy, name="deploy", depends_on=[b])
    """

    def __init__(
        self,
        func: Callable[..., Any],
        *,
        name: Optional[str] = None,
        depends_on: Optional[list["Job"]] = None,
    ) -> None:
        if not callable(func):
            raise TypeError(f"Job func must be callable, got {type(func).__name__}")
        self.func = func
        self.name = name or getattr(func, "__name__", "job")
        self.depends_on: list[Job] = list(depends_on or [])
        self.status: JobStatus = JobStatus.PENDING
        self.result: Optional[JobResult] = None

    def add_dependency(self, other: "Job") -> "Job":
        """Declare that this Job depends on `other`. Returns self for chaining."""
        if other is self:
            raise ValueError(f"Job {self.name!r} cannot depend on itself")
        if not isinstance(other, Job):
            raise TypeError(f"dependency must be a Job, got {type(other).__name__}")
        if other not in self.depends_on:
            self.depends_on.append(other)
        return self

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        deps = ", ".join(d.name for d in self.depends_on) or "-"
        return f"Job({self.name!r}, depends_on=[{deps}])"

    def _invoke(self) -> Any:
        """Call the wrapped function with dependency results as arguments."""
        args = [dep.result.value for dep in self.depends_on]
        return self.func(*args)


def _execute(job: Job) -> JobResult:
    """Run a single Job and record its result. Never raises."""
    job.status = JobStatus.RUNNING
    started = time.monotonic()
    try:
        value = job._invoke()
        status = JobStatus.SUCCEEDED
        error = None
    except BaseException as exc:  # noqa: BLE001 - we want to capture everything
        value = None
        status = JobStatus.FAILED
        error = exc
    finished = time.monotonic()
    result = JobResult(
        job=job,
        status=status,
        value=value,
        error=error,
        started_at=started,
        finished_at=finished,
    )
    job.result = result
    job.status = status
    return result
