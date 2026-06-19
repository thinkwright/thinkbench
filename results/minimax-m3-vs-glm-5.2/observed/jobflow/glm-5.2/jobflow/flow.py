"""A :class:`Flow` holds jobs, validates the dependency graph, and runs them."""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .job import Job
from .status import Status


class FlowError(Exception):
    """Raised when a flow is structurally invalid (cycles, missing deps, ...)."""


@dataclass
class JobResult:
    """The outcome of a single job within a run."""

    name: str
    status: Status
    return_value: Any = None
    error: Optional[BaseException] = None
    traceback: str = ""
    started_at: Optional[float] = None
    finished_at: Optional[float] = None

    @property
    def duration(self) -> Optional[float]:
        if self.started_at is not None and self.finished_at is not None:
            return self.finished_at - self.started_at
        return None

    @property
    def succeeded(self) -> bool:
        return self.status is Status.SUCCESS


@dataclass
class RunResult:
    """The outcome of a whole run: per-job results plus an overall status."""

    results: Dict[str, JobResult] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        """True iff every job that was *supposed* to run succeeded.

        Jobs that were skipped because an upstream dependency failed do not
        count against this -- they were correctly not run.
        """
        return all(
            r.status is Status.SUCCESS for r in self.results.values()
        )

    def __getitem__(self, name: str) -> JobResult:
        return self.results[name]

    def __contains__(self, name: str) -> bool:
        return name in self.results

    def __iter__(self):
        return iter(self.results.values())

    @property
    def failed(self) -> List[JobResult]:
        return [r for r in self.results.values() if r.status is Status.FAILED]

    @property
    def skipped(self) -> List[JobResult]:
        return [r for r in self.results.values() if r.status is Status.SKIPPED]


class Flow:
    """A directed acyclic graph of jobs.

    Construct it from any iterable of :class:`Job` objects. The flow validates
    that dependency names refer to real jobs and that there are no cycles, then
    exposes :meth:`run` to execute everything in dependency order.

    Example::

        Flow([
            Job("build", func=build),
            Job("test", func=test, needs=["build"]),
        ]).run()
    """

    def __init__(self, jobs: Iterable[Job]) -> None:
        self.jobs: Dict[str, Job] = {}
        for job in jobs:
            if not isinstance(job, Job):
                raise TypeError(
                    f"Flow expects Job objects, got {type(job).__name__}"
                )
            if job.name in self.jobs:
                raise ValueError(f"duplicate job name {job.name!r}")
            self.jobs[job.name] = job
        self._validate()

    # -- introspection -------------------------------------------------

    def __len__(self) -> int:
        return len(self.jobs)

    def __contains__(self, name: object) -> bool:
        return name in self.jobs

    def __getitem__(self, name: str) -> Job:
        return self.jobs[name]

    def __iter__(self):
        return iter(self.jobs.values())

    @property
    def names(self) -> List[str]:
        return list(self.jobs)

    # -- validation ----------------------------------------------------

    def _validate(self) -> None:
        # Every dependency must name a known job.
        for job in self.jobs.values():
            for dep in job.needs:
                if dep not in self.jobs:
                    raise FlowError(
                        f"job {job.name!r} needs unknown job {dep!r}"
                    )
                if dep == job.name:
                    raise FlowError(
                        f"job {job.name!r} cannot depend on itself"
                    )
        # No cycles.
        order = self._topological_order()
        # (order is also used by run; we just want the cycle check here.)

    def _topological_order(self) -> List[str]:
        """Return job names in a valid execution order (deps before dependents).

        Uses Kahn's algorithm. Raises :class:`FlowError` on a cycle.
        """
        # in-degree: number of unsatisfied dependencies per job
        indegree = {name: 0 for name in self.jobs}
        dependents: Dict[str, List[str]] = {
            name: [] for name in self.jobs
        }
        for job in self.jobs.values():
            for dep in job.needs:
                indegree[job.name] += 1
                dependents[dep].append(job.name)

        # Process in sorted order for deterministic output.
        ready = sorted(n for n, d in indegree.items() if d == 0)
        order: List[str] = []
        while ready:
            name = ready.pop(0)
            order.append(name)
            nexts = []
            for dep in dependents[name]:
                indegree[dep] -= 1
                if indegree[dep] == 0:
                    nexts.append(dep)
            ready = sorted(ready + nexts)

        if len(order) != len(self.jobs):
            remaining = sorted(
                n for n, d in indegree.items() if d > 0
            )
            raise FlowError(
                f"cycle detected among jobs: {', '.join(remaining)}"
            )
        return order

    # -- execution -----------------------------------------------------

    def run(
        self,
        *,
        only: Optional[Sequence[str]] = None,
        dry_run: bool = False,
        on_event: Optional[Any] = None,
    ) -> RunResult:
        """Execute the flow in dependency order and return a :class:`RunResult`.

        Parameters
        ----------
        only:
            If given, run only the named job(s) and the dependencies they
            transitively need. Other jobs are left ``PENDING``.
        dry_run:
            If true, compute the order and mark every targeted job ``SKIPPED``
            without executing anything. Useful for "what would happen?" checks.
        on_event:
            Optional callable ``on_event(job_result)`` invoked after each job
            settles (success, failure, or skip). Handy for logging/UIs.
        """
        if only is not None:
            targets = self._resolve_targets(only)
        else:
            targets = set(self.jobs)

        order = [n for n in self._topological_order() if n in targets]

        results: Dict[str, JobResult] = RunResult().results
        # Seed every targeted job as pending so callers see the full set.
        for name in order:
            results[name] = JobResult(name=name, status=Status.PENDING)

        for name in order:
            job = self.jobs[name]
            current = results[name]

            # If any dependency did not succeed, skip this job.
            blocked_by = [
                dep for dep in job.needs
                if dep in results
                and results[dep].status is not Status.SUCCESS
            ]
            if blocked_by:
                current.status = Status.SKIPPED
                current.error = FlowError(
                    f"skipped: dependency {blocked_by[0]!r} did not succeed"
                )
                if on_event is not None:
                    on_event(current)
                continue

            if dry_run:
                current.status = Status.SKIPPED
                if on_event is not None:
                    on_event(current)
                continue

            current.status = Status.RUNNING
            current.started_at = time.time()
            try:
                current.return_value = job.execute()
                current.status = Status.SUCCESS
            except BaseException as exc:  # noqa: BLE001 - record anything
                current.status = Status.FAILED
                current.error = exc
                current.traceback = traceback.format_exc()
            finally:
                current.finished_at = time.time()

            if on_event is not None:
                on_event(current)

        return RunResult(results=results)

    def _resolve_targets(self, only: Sequence[str]) -> set:
        names: set = set()
        stack: List[str] = []
        for n in only:
            if n not in self.jobs:
                raise FlowError(f"unknown job {n!r} in 'only'")
            stack.append(n)
        while stack:
            name = stack.pop()
            if name in names:
                continue
            names.add(name)
            for dep in self.jobs[name].needs:
                if dep not in names:
                    stack.append(dep)
        return names

    def __repr__(self) -> str:
        return f"Flow({list(self.jobs)!r})"