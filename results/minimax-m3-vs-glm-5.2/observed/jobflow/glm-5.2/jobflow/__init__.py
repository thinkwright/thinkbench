"""jobflow: define jobs and their dependencies, then run them in the right order.

A *job* is a unit of work -- typically a function or a shell command -- that may
declare other jobs it *needs* to have finished first. A *flow* is a collection of
jobs. Hand a flow to :func:`jobflow.run` (or call ``Flow.run``) and jobflow works
out a valid order and executes the jobs, stopping dependents of any job that
fails so a run never silently does the wrong thing.

Example::

    from jobflow import Job, Flow

    build = Job("build", func=build_project)
    test = Job("test", func=run_tests, needs=["build"])
    deploy = Job("deploy", func=deploy_project, needs=["test"])

    Flow([build, test, deploy]).run()

The same flow can be run from the command line by pointing jobflow at a Python
file that defines a top-level ``flow`` (or a ``make_flow()`` function)::

    $ python -m jobflow pipeline.py
"""

from .job import Job
from .flow import Flow, FlowError, RunResult, JobResult, Status

__all__ = [
    "Job",
    "Flow",
    "FlowError",
    "RunResult",
    "JobResult",
    "Status",
    "run",
]

__version__ = "0.1.0"


def run(flow, **kwargs):
    """Run a :class:`Flow` and return its :class:`RunResult`.

    This is just a thin convenience wrapper around ``flow.run(**kwargs)`` so
    there's an obvious module-level entry point::

        import jobflow
        result = jobflow.run(flow)
    """
    if not isinstance(flow, Flow):
        raise TypeError(f"expected a Flow, got {type(flow).__name__}")
    return flow.run(**kwargs)