"""Errors raised while driving an instance through a process."""


class Rejected(Exception):
    """Raised when a transition cannot be applied to an instance.

    The string form is meant to be read by a person. The :attr:`reason`
    attribute carries a short machine-readable code and the structured details
    are available on the exception for programmatic handling.
    """

    #: Short, stable, machine-readable reason codes.
    NO_SUCH_TRANSITION = "no_such_transition"
    NOT_FROM_HERE = "not_from_here"
    FORBIDDEN = "forbidden"
    GUARD_REFUSED = "guard_refused"
    ALREADY_TERMINAL = "already_terminal"

    def __init__(self, reason, message, **details):
        self.reason = reason
        self.details = details
        self.message = message
        super().__init__(message)

    def __str__(self):  # pragma: no cover - trivial
        return self.message


# Backwards-friendly alias: a rejection *is* a rejected move.
Rejection = Rejected