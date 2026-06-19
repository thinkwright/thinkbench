"""Command-line entry point for inspecting a process and driving an instance.

This is the "obvious way to actually run something through" without writing a
full program. A process is supplied as ``module.path:attribute`` -- a Python
object that is a :class:`~workflow.process.Process`.

    # Print the shape of a process:
    python -m workflow myapp.orders:process

    # Drive an instance through it, transition by transition:
    python -m workflow myapp.orders:process --drive pay pack ship

    # Just ask what moves are available from a stage:
    python -m workflow myapp.orders:process --from paid
"""

import argparse
import importlib

from .process import Process


def _load(spec):
    if ":" not in spec:
        raise SystemExit(
            "expected 'module.path:attribute', got %r" % (spec,)
        )
    module_name, attr = spec.split(":", 1)
    module = importlib.import_module(module_name)
    process = getattr(module, attr)
    if not isinstance(process, Process):
        raise SystemExit("%s is not a workflow.Process" % (spec,))
    return process


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m workflow",
        description="Inspect a workflow process or drive an instance through it.",
    )
    parser.add_argument(
        "process",
        help="process location as 'module.path:attribute'",
    )
    parser.add_argument(
        "--from",
        dest="from_stage",
        metavar="STAGE",
        help="list the transitions available from this stage",
    )
    parser.add_argument(
        "--drive",
        nargs="*",
        metavar="TRANSITION",
        help="apply these transitions in order, starting from the start stage, "
        "and print the resulting stage and history",
    )
    args = parser.parse_args(argv)

    process = _load(args.process)

    if args.drive is not None:
        inst = process.start()
        for name in args.drive:
            try:
                inst.advance(name)
            except Exception as exc:  # Rejected, but keep it general for CLI
                print("rejected at %r: %s" % (inst.stage, exc))
                return 1
        print("stage:   %s" % inst.stage)
        print("history: %s" % ", ".join(inst.history) if inst.history else "(none)")
        return 0

    if args.from_stage is not None:
        if args.from_stage not in process.stages:
            print("unknown stage %r" % (args.from_stage,))
            return 1
        moves = process.transitions_from(args.from_stage)
        if not moves:
            print("%r is terminal" % (args.from_stage,))
        else:
            for t in moves:
                print("%s -> %s" % (t.name, t.to_stage))
        return 0

    # Default: describe the whole process.
    print(process.describe())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())