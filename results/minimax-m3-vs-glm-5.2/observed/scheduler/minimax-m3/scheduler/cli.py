"""Command-line entry point.

Loads a Python file that defines a `SCHEDULER` (a `scheduler.Scheduler`
instance, or a list of `Task`s) and runs it until interrupted.

    python -m scheduler path/to/jobs.py
"""

from __future__ import annotations

import argparse
import importlib.util
import logging
import sys
from pathlib import Path

from .core import Scheduler, Task


def _load(path: Path) -> Scheduler:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    obj = getattr(module, "SCHEDULER", None)
    if isinstance(obj, Scheduler):
        return obj
    if isinstance(obj, list) and all(isinstance(t, Task) for t in obj):
        return Scheduler(obj)
    raise SystemExit(
        f"{path} must define SCHEDULER = scheduler.Scheduler(...) "
        "or SCHEDULER = [Task(...), ...]"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scheduler",
        description="Run registered tasks on their schedules.",
    )
    parser.add_argument("jobs", type=Path, help="Python file defining SCHEDULER")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="enable debug logging"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    sched = _load(args.jobs)
    names = ", ".join(t.name for t in sched.tasks()) or "(none)"
    logging.getLogger("scheduler").info("starting with tasks: %s", names)
    try:
        sched.run_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
