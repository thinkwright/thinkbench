"""Command-line entry point: ``python -m scheduler [config.py]``.

A config module defines a top-level ``build(scheduler)`` function (or simply a
list ``tasks``) that registers work against the scheduler. Example config::

    from scheduler import every, at

    def build(sched):
        sched.add(every(minutes=1), lambda: print("tick"))
        sched.add(at("02:30"), lambda: print("nightly"))
"""

from __future__ import annotations

import argparse
import importlib.util
import runpy
import sys
from pathlib import Path

from .scheduler import Scheduler


def _load_config(path: str) -> dict:
    spec = importlib.util.spec_from_file_location("scheduler_config", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load config from {path!r}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return {k: getattr(module, k) for k in dir(module)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scheduler",
        description="Run a scheduler configured by a Python file.",
    )
    parser.add_argument(
        "config",
        help="Path to a .py file defining build(scheduler) or a tasks list.",
    )
    args = parser.parse_args(argv)

    ns = _load_config(args.config)
    sched = Scheduler()

    build = ns.get("build")
    if callable(build):
        build(sched)
    else:
        for item in ns.get("tasks", []):
            if isinstance(item, dict):
                sched.add(item["schedule"], item["func"], name=item.get("name", ""))
            else:
                schedule, func = item[0], item[1]
                sched.add(schedule, func)

    try:
        sched.run()
    except KeyboardInterrupt:
        sched.stop()
        print("\nstopped.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())