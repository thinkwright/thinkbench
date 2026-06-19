"""Channel protocol / base class."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..recipient import Recipient
from ..message import Message


class Channel(ABC):
    """A way to deliver a message.

    Subclasses set ``name`` (the key a :class:`Recipient` uses to expose
    an address for this channel) and implement :meth:`send`, which should
    raise on failure and return ``None`` on success.
    """

    name: str = ""

    @abstractmethod
    def send(self, recipient: Recipient, message: Message) -> None:
        """Deliver *message* to *recipient*.

        Raise on failure; return ``None`` on success.  The hub wraps any
        raised exception in a failed :class:`SendResult`.
        """
        raise NotImplementedError