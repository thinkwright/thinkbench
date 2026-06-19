"""Message construction rules."""

import pytest

from notifyhub.message import Message


def test_message_carries_subject_body_and_metadata():
    m = Message(subject="s", body="b", metadata={"k": 1})
    assert m.subject == "s"
    assert m.body == "b"
    assert m.metadata == {"k": 1}


def test_message_is_immutable():
    m = Message(subject="s", body="b")
    with pytest.raises(Exception):
        m.subject = "other"  # type: ignore[misc]


def test_message_rejects_empty():
    with pytest.raises(ValueError):
        Message()


def test_message_rejects_non_string_subject():
    with pytest.raises(TypeError):
        Message(subject=123, body="b")  # type: ignore[arg-type]


def test_message_rejects_non_string_body():
    with pytest.raises(TypeError):
        Message(subject="s", body=object())  # type: ignore[arg-type]
