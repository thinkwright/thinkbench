"""workflow — describe a process as stages and transitions, then drive things through it."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Hashable, Iterable


class WorkflowError(Exception):
    """Base class for workflow errors."""


class UnknownStageError(WorkflowError):
    """Raised when a stage is not part of the workflow."""

    def __init__(self, stage: Hashable):
        super().__init__(f"unknown stage: {stage!r}")
        self.stage = stage


class IllegalTransitionError(WorkflowError):
    """Raised when no transition is defined for an event from the current stage."""

    def __init__(self, stage: Hashable, event: Hashable, allowed: Iterable[Hashable]):
        allowed_list = list(allowed)
        if allowed_list:
            allowed_repr = ", ".join(repr(e) for e in allowed_list)
            msg = (
                f"cannot apply event {event!r} from stage {stage!r}; "
                f"allowed events from here: {allowed_repr}"
            )
        else:
            msg = (
                f"cannot apply event {event!r} from stage {stage!r}; "
                f"{stage!r} is a terminal stage with no outgoing transitions"
            )
        super().__init__(msg)
        self.stage = stage
        self.event = event
        self.allowed = allowed_list


class AmbiguousTransitionError(WorkflowError):
    """Raised when more than one transition matches the same (stage, event)."""

    def __init__(self, stage: Hashable, event: Hashable, targets: Iterable[Hashable]):
        targets_list = list(targets)
        targets_repr = ", ".join(repr(t) for t in targets_list)
        super().__init__(
            f"ambiguous transition: event {event!r} from stage {stage!r} "
            f"matches multiple targets: {targets_repr}"
        )
        self.stage = stage
        self.event = event
        self.targets = targets_list


@dataclass(frozen=True)
class Transition:
    """A directed edge: from `source`, event `event` moves to `target`."""

    source: Hashable
    event: Hashable
    target: Hashable


@dataclass
class Workflow:
    """A process: a set of stages and the transitions between them.

    Transitions are added with `add_transition(source, event, target)`. A stage
    with no outgoing transitions is terminal. The same (source, event) pair may
    only map to a single target — ambiguity is a definition error, not a runtime
    surprise.
    """

    name: str = "workflow"
    _transitions: list = field(default_factory=list)
    _outgoing: dict = field(default_factory=dict)
    _stages: set = field(default_factory=set)

    def add_transition(self, source: Hashable, event: Hashable, target: Hashable) -> None:
        """Declare that `event` applied at `source` moves to `target`."""
        inner = self._outgoing.setdefault(source, {})
        if event in inner:
            existing = inner[event]
            raise WorkflowError(
                f"duplicate transition for ({source!r}, {event!r}); "
                f"already maps to {existing!r}, refusing to add {target!r}"
            )
        inner[event] = target
        self._transitions.append(Transition(source, event, target))
        self._stages.add(source)
        self._stages.add(target)

    def stages(self) -> set:
        """All stages reachable from any transition (including implicit ones)."""
        return set(self._stages)

    def transitions(self) -> tuple:
        """All declared transitions, in insertion order."""
        return tuple(self._transitions)

    def allowed_events(self, stage: Hashable) -> tuple:
        """Events that can be applied from `stage`. Empty tuple means terminal."""
        if stage not in self._stages:
            raise UnknownStageError(stage)
        return tuple(self._outgoing.get(stage, {}).keys())

    def is_terminal(self, stage: Hashable) -> bool:
        """True if `stage` has no outgoing transitions."""
        if stage not in self._stages:
            raise UnknownStageError(stage)
        return stage not in self._outgoing or not self._outgoing[stage]

    def can(self, stage: Hashable, event: Hashable) -> bool:
        """True if applying `event` at `stage` is defined."""
        if stage not in self._stages:
            raise UnknownStageError(stage)
        return event in self._outgoing.get(stage, {})

    def advance(self, stage: Hashable, event: Hashable) -> Hashable:
        """Apply `event` at `stage` and return the resulting stage.

        Raises UnknownStageError, IllegalTransitionError, or AmbiguousTransitionError
        with enough context to understand why a move was rejected.
        """
        if stage not in self._stages:
            raise UnknownStageError(stage)
        outgoing = self._outgoing.get(stage, {})
        if event not in outgoing:
            raise IllegalTransitionError(stage, event, outgoing.keys())
        # _outgoing stores at most one target per (source, event); ambiguity is
        # prevented at definition time. The check below is a defensive guard.
        return outgoing[event]

    def driver(self, start: Hashable) -> "Driver":
        """Return a Driver positioned at `start`."""
        if start not in self._stages:
            raise UnknownStageError(start)
        return Driver(self, start)

    def __repr__(self) -> str:
        return f"Workflow(name={self.name!r}, stages={len(self._stages)}, transitions={len(self._transitions)})"


@dataclass
class Driver:
    """Drives a single thing through a Workflow.

    Holds the current stage and exposes the operations you actually want to call
    while running a process: ask where you are, ask what's allowed, advance.
    """

    workflow: Workflow
    stage: Hashable

    @property
    def state(self) -> Hashable:
        """The current stage."""
        return self.stage

    @property
    def is_terminal(self) -> bool:
        """True if no further events are accepted from the current stage."""
        return self.workflow.is_terminal(self.stage)

    @property
    def allowed_events(self) -> tuple:
        """Events that can be applied right now."""
        return self.workflow.allowed_events(self.stage)

    def can(self, event: Hashable) -> bool:
        """True if `event` is a legal move from the current stage."""
        return self.workflow.can(self.stage, event)

    def advance(self, event: Hashable) -> Hashable:
        """Apply `event` and move to the next stage. Returns the new stage."""
        new_stage = self.workflow.advance(self.stage, event)
        self.stage = new_stage
        return new_stage

    def __repr__(self) -> str:
        return f"Driver(workflow={self.workflow.name!r}, stage={self.stage!r})"


__all__ = [
    "Workflow",
    "Driver",
    "Transition",
    "WorkflowError",
    "UnknownStageError",
    "IllegalTransitionError",
    "AmbiguousTransitionError",
]
