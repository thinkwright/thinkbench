"""A JobFlow is a collection of Jobs that gets executed in dependency order."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from .job import Job, JobResult, JobStatus, _execute


class FlowError(Exception):
    """Raised when a JobFlow cannot be executed (cycle, missing dep, etc.)."""


@dataclass
class FlowReport:
    """Summary of a flow run."""

    results: list[JobResult] = field(default_factory=list)

    @property
    def succeeded(self) -> list[JobResult]:
        return [r for r in self.results if r.status == JobStatus.SUCCEEDED]

    @property
    def failed(self) -> list[JobResult]:
        return [r for r in self.results if r.status == JobStatus.FAILED]

    @property
    def skipped(self) -> list[JobResult]:
        return [r for r in self.results if r.status == JobStatus.SKIPPED]

    @property
    def ok(self) -> bool:
        return not self.failed

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        lines = [f"FlowReport: {len(self.results)} jobs"]
        for r in self.results:
            extra = ""
            if r.status == JobStatus.FAILED and r.error is not None:
                extra = f" — {type(r.error).__name__}: {r.error}"
            elif r.duration is not None:
                extra = f" ({r.duration:.3f}s)"
            lines.append(f"  [{r.status.value}] {r.job.name}{extra}")
        return "\n".join(lines)


class JobFlow:
    """A named collection of Jobs to be executed in dependency order.

    The flow owns its Jobs. Adding the same Job (or a Job that transitively
    depends on it) more than once is fine — it will only run once.
    """

    def __init__(self, jobs: Iterable[Job], *, name: str = "flow") -> None:
        self.name = name
        # Preserve insertion order; dedupe by identity.
        seen: set[int] = set()
        self.jobs: list[Job] = []
        for job in jobs:
            if id(job) in seen:
                continue
            seen.add(id(job))
            self.jobs.append(job)

    def __iter__(self):
        return iter(self.jobs)

    def __len__(self) -> int:
        return len(self.jobs)

    # ------------------------------------------------------------------ #
    # Validation / ordering
    # ------------------------------------------------------------------ #

    def _all_jobs(self) -> list[Job]:
        """All jobs reachable from the declared ones, in stable order."""
        seen: set[int] = set()
        ordered: list[Job] = []

        def visit(job: Job) -> None:
            if id(job) in seen:
                return
            seen.add(id(job))
            for dep in job.depends_on:
                visit(dep)
            ordered.append(job)

        for job in self.jobs:
            visit(job)
        return ordered

    def _validate(self) -> list[Job]:
        """Return the full job list, raising FlowError on cycles or bad deps."""
        all_jobs = self._all_jobs()
        job_set = {id(j): j for j in all_jobs}

        # Every dependency must be a Job we know about. (We don't currently
        # support cross-flow references, so this also catches typos.)
        for job in all_jobs:
            for dep in job.depends_on:
                if id(dep) not in job_set:
                    raise FlowError(
                        f"Job {job.name!r} depends on {dep.name!r}, "
                        "which is not part of this flow"
                    )

        # Cycle detection via DFS coloring.
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[int, int] = {id(j): WHITE for j in all_jobs}

        def dfs(job: Job, stack: list[Job]) -> None:
            color[id(job)] = GRAY
            for dep in job.depends_on:
                c = color[id(dep)]
                if c == GRAY:
                    cycle = " -> ".join(j.name for j in stack[stack.index(dep):])
                    cycle += f" -> {dep.name}"
                    raise FlowError(f"Dependency cycle detected: {cycle}")
                if c == WHITE:
                    dfs(dep, stack + [dep])
            color[id(job)] = BLACK

        for job in all_jobs:
            if color[id(job)] == WHITE:
                dfs(job, [job])

        return all_jobs

    def order(self) -> list[Job]:
        """Return the jobs in a valid execution order (topological)."""
        return self._validate()

    # ------------------------------------------------------------------ #
    # Execution
    # ------------------------------------------------------------------ #

    def run(
        self,
        *,
        on_job_start=None,
        on_job_finish=None,
        stop_on_failure: bool = True,
    ) -> FlowReport:
        """Execute the flow and return a FlowReport.

        - ``on_job_start(job)`` is called just before each Job runs.
        - ``on_job_finish(result)`` is called just after each Job finishes
          (succeeded, failed, or skipped).
        - If ``stop_on_failure`` is True (the default), a failed Job causes
          any Job that transitively depends on it to be marked SKIPPED, and
          no further Jobs are started. Set it to False to keep going and
          record every failure.
        """
        all_jobs = self._validate()

        # Reset state in case the flow is being re-run.
        for job in all_jobs:
            job.status = JobStatus.PENDING
            job.result = None

        report = FlowReport()
        failed: set[int] = set()
        aborted = False

        for job in all_jobs:
            if aborted:
                break

            # If any dependency failed, skip this job.
            blocking = [d for d in job.depends_on if id(d) in failed]
            if blocking:
                names = ", ".join(d.name for d in blocking)
                result = JobResult(
                    job=job,
                    status=JobStatus.SKIPPED,
                    error=FlowError(
                        f"skipped because dependency failed: {names}"
                    ),
                )
                job.result = result
                job.status = JobStatus.SKIPPED
                report.results.append(result)
                if on_job_finish is not None:
                    on_job_finish(result)
                continue

            job.status = JobStatus.READY
            if on_job_start is not None:
                on_job_start(job)

            result = _execute(job)
            report.results.append(result)
            if on_job_finish is not None:
                on_job_finish(result)

            if result.status == JobStatus.FAILED:
                failed.add(id(job))
                if stop_on_failure:
                    aborted = True

        return report


def run_flow(
    flow: JobFlow,
    *,
    on_job_start=None,
    on_job_finish=None,
    stop_on_failure: bool = True,
) -> FlowReport:
    """Convenience wrapper around ``JobFlow.run``."""
    return flow.run(
        on_job_start=on_job_start,
        on_job_finish=on_job_finish,
        stop_on_failure=stop_on_failure,
    )
