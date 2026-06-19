"""Defining the shape of a process: stages and the transitions between them."""

from .instance import Instance


class Transition:
    """A single named move in a process.

    Parameters
    ----------
    name:
        What this move is called -- the handle you use to advance an instance.
    from_stage:
        The stage an instance must be in for this transition to apply. A
        transition has exactly one source stage; if a move can originate from
        more than one stage, define one transition per source (they can share
        a target). This keeps "where can this go from here" unambiguous.
    to_stage:
        The stage the instance lands in after the move.
    forbid:
        An optional iterable of stages to *rule out* as sources for this move,
        even if some other rule might otherwise suggest them. This is how you
        make a path explicitly impossible. It is checked before the guard.
    guard:
        An optional callable ``(instance) -> bool``. If given and it returns
        false, the move is refused with a ``guard_refused`` reason. Use it for
        conditions that depend on the specific instance rather than the shape
        of the process (e.g. "payment actually cleared").
    """

    __slots__ = ("name", "from_stage", "to_stage", "forbid", "guard")

    def __init__(self, name, from_stage, to_stage, forbid=(), guard=None):
        self.name = name
        self.from_stage = from_stage
        self.to_stage = to_stage
        self.forbid = frozenset(forbid)
        self.guard = guard

    def __repr__(self):  # pragma: no cover - trivial
        return "Transition(%r, %r -> %r)" % (self.name, self.from_stage, self.to_stage)


class Process:
    """A described process: its stages, a start, and the transitions.

    Parameters
    ----------
    stages:
        Iterable of stage names. Order is preserved for display only; the
        process does not care about the order you list them in.
    start:
        The stage new instances begin in. Must be one of ``stages``.
    transitions:
        Either an iterable of :class:`Transition` objects, or an iterable of
        ``(name, from_stage, to_stage)`` triples (optionally extended with
        ``forbid`` and ``guard`` as a 4th/5th element, or given as a dict).
    """

    def __init__(self, stages, start, transitions):
        self.stages = tuple(stages)
        if start not in self.stages:
            raise ValueError("start stage %r is not in stages" % (start,))
        self.start_stage = start

        self.transitions = {}
        self._by_source = {}  # stage name -> list of transitions from it
        for raw in transitions:
            t = self._coerce_transition(raw)
            if t.name in self.transitions:
                raise ValueError("duplicate transition name %r" % (t.name,))
            if t.from_stage not in self.stages:
                raise ValueError(
                    "transition %r starts from unknown stage %r"
                    % (t.name, t.from_stage)
                )
            if t.to_stage not in self.stages:
                raise ValueError(
                    "transition %r goes to unknown stage %r"
                    % (t.name, t.to_stage)
                )
            for f in t.forbid:
                if f not in self.stages:
                    raise ValueError(
                        "transition %r forbids unknown stage %r" % (t.name, f)
                    )
            self.transitions[t.name] = t
            self._by_source.setdefault(t.from_stage, []).append(t)

    @staticmethod
    def _coerce_transition(raw):
        if isinstance(raw, Transition):
            return raw
        if isinstance(raw, dict):
            return Transition(
                raw["name"],
                raw["from"],
                raw["to"],
                forbid=raw.get("forbid", ()),
                guard=raw.get("guard"),
            )
        # tuple/list: (name, from, to[, forbid[, guard]])
        name, frm, to = raw[0], raw[1], raw[2]
        forbid = raw[3] if len(raw) > 3 else ()
        guard = raw[4] if len(raw) > 4 else None
        return Transition(name, frm, to, forbid=forbid, guard=guard)

    # -- introspection ---------------------------------------------------

    def transitions_from(self, stage):
        """Return the transitions that may originate from ``stage``."""
        return tuple(self._by_source.get(stage, ()))

    def can(self, stage, transition_name):
        """True if ``transition_name`` is a valid next move from ``stage``.

        This checks the *shape* of the process only (source stage and explicit
        forbiddens). It does not evaluate instance-level guards, since there is
        no instance to evaluate them against.
        """
        t = self.transitions.get(transition_name)
        if t is None:
            return False
        if stage in t.forbid:
            return False
        return t.from_stage == stage

    def is_terminal(self, stage):
        """True if nothing can leave ``stage`` -- a dead end."""
        return not self.transitions_from(stage)

    # -- driving ---------------------------------------------------------

    def start(self, data=None):
        """Create a new instance at the process's start stage."""
        return Instance(self, self.start_stage, data=data)

    def resume(self, stage, history=(), data=None):
        """Rebuild an instance from a persisted stage and history.

        ``history`` is an iterable of transition names already applied, in
        order. The instance is placed directly at ``stage`` without replaying
        them; the history is kept for inspection. Use this to rehydrate from
        storage.
        """
        if stage not in self.stages:
            raise ValueError("unknown stage %r" % (stage,))
        return Instance(self, stage, history=tuple(history), data=data)

    # -- display ---------------------------------------------------------

    def describe(self):
        """Return a human-readable outline of the process.

        Useful for inspecting a process directly, e.g. in a REPL or a CLI.
        """
        lines = ["process: start=%r" % (self.start_stage,)]
        for stage in self.stages:
            outs = self.transitions_from(stage)
            if outs:
                moves = ", ".join(
                    "%s->%s" % (t.name, t.to_stage) for t in outs
                )
                lines.append("  %s: %s" % (stage, moves))
            else:
                lines.append("  %s: (terminal)" % (stage,))
        return "\n".join(lines)

    def __repr__(self):  # pragma: no cover - trivial
        return "Process(stages=%r, start=%r, %d transitions)" % (
            self.stages,
            self.start_stage,
            len(self.transitions),
        )