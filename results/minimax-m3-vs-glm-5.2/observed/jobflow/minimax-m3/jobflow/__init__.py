"""jobflow — define jobs with dependencies and run them in order."""

from .job import Job, JobStatus, JobResult
from .flow import JobFlow, FlowError, run_flow

__all__ = [
    "Job",
    "JobStatus",
    "JobResult",
    "JobFlow",
    "FlowError",
    "run_flow",
]

__version__ = "0.1.0"
