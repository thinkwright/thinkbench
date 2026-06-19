"""A :class:`Job` is a single unit of work plus the names of jobs it depends on."""

from __future__ import annotations

import shlex
import subprocess
import sys
from typing import Any, Callable, Iterable, List, Optional, Sequence, Union

from .status import Status

Needs = Union[str, "Job", Sequence[Union[str, "Job"]]]


def _normalize_needs(needs: Optional[Needs]) -> List[str]:
    """Turn a dependency spec into a sorted list of job-name strings."""
    if needs is None:
        return []
    if isinstance(needs, (str, Job)):
        needs = [needs]
    names: List[str] = []
    for item in needs:
        if isinstance(item, Job):
            names.append(item.name)
        elif isinstance(item, str):
            names.append(item)
        else:
            raise TypeError(
                f"dependencies must be Job or str, got {type(item).__name__}"
            )
    if len(set(names)) != len(names):
        raise ValueError(f"duplicate dependencies in {needs!r}")
    return names


class Job:
    """A single unit of work.

    Parameters
    ----------
    name:
        A unique name for this job within a flow.
    func:
        A callable to run. It is called with no arguments by default; pass
        ``args``/``kwargs`` to supply them. If ``func`` returns a value it is
        recorded on the :class:`~jobflow.JobResult`.
    command:
        A shell command to run, as a string or a list of arguments. When given,
        the job runs the command via :mod:`subprocess` and fails if the command
        exits non-zero. ``func`` and ``command`` are mutually exclusive.
    needs:
        Jobs that must finish successfully before this one runs. May be a single
        job/name or a sequence of them. Order is not significant.
    args, kwargs:
        Positional and keyword arguments passed to ``func``.
    description:
        Optional human-readable note; shown in CLI output and results.
    """

    def __init__(
        self,
        name: str,
        *,
        func: Optional[Callable[..., Any]] = None,
        command: Optional[Union[str, Sequence[str]]] = None,
        needs: Optional[Needs] = None,
        args: Sequence[Any] = (),
        kwargs: Optional[dict] = None,
        description: str = "",
    ) -> None:
        if not isinstance(name, str) or not name:
            raise ValueError("job name must be a non-empty string")
        if func is None and command is None:
            raise ValueError(
                f"job {name!r} needs either 'func' or 'command'"
            )
        if func is not None and command is not None:
            raise ValueError(
                f"job {name!r} cannot have both 'func' and 'command'"
            )

        self.name = name
        self.func = func
        self.command = command
        self.needs = _normalize_needs(needs)
        self.args = tuple(args)
        self.kwargs = dict(kwargs) if kwargs else {}
        self.description = description

    @property
    def is_command(self) -> bool:
        return self.command is not None

    def execute(self) -> Any:
        """Run the job's work and return whatever ``func`` returned.

        For command jobs the return value is the completed process's stdout
        (text). Raises on failure -- callers should catch and record the status.
        """
        if self.func is not None:
            return self.func(*self.args, **self.kwargs)
        # command job
        if isinstance(self.command, str):
            argv = shlex.split(self.command)
        else:
            argv = list(self.command)
        if not argv:
            raise ValueError(f"job {self.name!r} has an empty command")
        proc = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(
                proc.returncode, argv, output=proc.stdout, stderr=proc.stderr
            )
        return proc.stdout

    def __repr__(self) -> str:
        return f"Job({self.name!r}, needs={self.needs!r})"

    def __rshift__(self, other: "Job") -> "Job":
        """``a >> b`` means *b needs a*. Returns ``b`` for chaining."""
        if not isinstance(other, Job):
            return NotImplemented
        other.needs = _normalize_needs([*other.needs, self.name])
        return other

    def __lshift__(self, other: "Job") -> "Job":
        """``a << b`` means *a needs b*. Returns ``a`` for chaining."""
        if not isinstance(other, Job):
            return NotImplemented
        self.needs = _normalize_needs([*self.needs, other.name])
        return self

    # Let jobs be used directly as dependency specs by name.
    def __hash__(self) -> int:
        return hash(self.name)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Job):
            return self.name == other.name
        return NotImplemented