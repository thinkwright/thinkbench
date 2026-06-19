"""A single thing being driven through a process."""

from .errors import Rejected


class Instance:
    """One thing moving through a :class:`~workflow.process.Process`.

    Created via :meth:`Process.start` or :meth:`Process.resume`. Don't build
    this directly unless you know what you're doing.

    The instance records the transitions applied to it (``history``) and
    optionally carries arbitrary user ``data`` (e.g. an order record) that
    guards can inspect.
    """

    __slots__ = ("process", "_stage", "history", "data")

    def __init__(self, process, stage, history=(), data=None):
        self.process = process
        self._stage = stage
        self.history = tuple(history)
        self.data = data

    @property
    def stage(self):
        """The current stage of this instance."""
        return self._stage

    @property
    def is_terminal(self):
        """True if the instance is in a stage nothing can leave."""
        return self.process.is_terminal(self._stage)

    def available(self):
        """Return the transition names that are valid next moves from here.

        Shape-level only; does not evaluate guards.
        """
        return tuple(t.name for t in self.process.transitions_from(self._stage))

    def can(self, transition_name):
        """True if ``transition_name`` can be applied right now.

        Unlike :meth:`Process.can`, this *does* evaluate guards, since it has
        an instance to evaluate them against.
        """
        t = self.process.transitions.get(transition_name)
        if t is None:
            return False
        if t.from_stage != self._stage:
            return False
        if self._stage in t.forbid:
            return False
        if t.guard is not None and not t.guard(self):
            return False
        return True

    def advance(self, transition_name):
        """Apply ``transition_name`` and move to the next stage.

        Returns ``self`` so calls can chain where convenient. Raises
        :class:`~workflow.errors.Rejected` with a readable message and a
        machine-readable ``reason`` if the move is not allowed.
        """
        t = self.process.transitions.get(transition_name)
        if t is None:
            raise Rejected(
                Rejected.NO_SUCH_TRANSITION,
                "no transition named %r in this process" % (transition_name,),
                transition=transition_name,
                stage=self._stage,
            )

        if self.is_terminal:
            # A terminal stage has no outgoing transitions, so from_stage can
            # never match; surface this distinctly because it's a common
            # confusion ("I'm done, why won't it move?").
            raise Rejected(
                Rejected.ALREADY_TERMINAL,
                "instance is in terminal stage %r; no moves are possible"
                % (self._stage,),
                transition=transition_name,
                stage=self._stage,
            )

        if self._stage in t.forbid:
            raise Rejected(
                Rejected.FORBIDDEN,
                "transition %r is explicitly forbidden from %r"
                % (transition_name, self._stage),
                transition=transition_name,
                stage=self._stage,
            )

        if t.from_stage != self._stage:
            raise Rejected(
                Rejected.NOT_FROM_HERE,
                "transition %r runs from %r, but instance is in %r"
                % (transition_name, t.from_stage, self._stage),
                transition=transition_name,
                stage=self._stage,
                expected_from=t.from_stage,
            )

        if t.guard is not None and not t.guard(self):
            raise Rejected(
                Rejected.GUARD_REFUSED,
                "transition %r from %r was refused by its guard"
                % (transition_name, self._stage),
                transition=transition_name,
                stage=self._stage,
            )

        self._stage = t.to_stage
        self.history = self.history + (transition_name,)
        return self

    def __repr__(self):  # pragma: no cover - trivial
        return "Instance(stage=%r, %d steps)" % (self._stage, len(self.history))