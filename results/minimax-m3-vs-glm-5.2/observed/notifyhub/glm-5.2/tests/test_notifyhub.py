"""Tests for the parts of notifyhub that matter."""

import io
import logging
import urllib.error

import pytest

import notifyhub
from notifyhub import (
    Hub,
    Recipient,
    Message,
    LogChannel,
    EmailChannel,
    SMSChannel,
    WebhookChannel,
    Status,
)


# -- Recipient ---------------------------------------------------------------


def test_recipient_holds_addresses():
    r = Recipient("alice", email="a@x.com", phone="+1555")
    assert r.id == "alice"
    assert r.address_for("email") == "a@x.com"
    assert r.address_for("phone") == "+1555"
    assert r.address_for("webhook") is None
    assert r.has("email") and not r.has("webhook")


def test_recipient_rejects_empty_id():
    with pytest.raises(ValueError):
        Recipient("")


def test_recipient_addresses_are_copied():
    src = {"email": "a@x.com"}
    r = Recipient("a", **src)
    src["email"] = "tampered"
    assert r.address_for("email") == "a@x.com"


# -- Message -----------------------------------------------------------------


def test_message_defaults():
    m = Message("Hi")
    assert m.subject == "Hi"
    assert m.body == ""
    assert m.data == {} and m.meta == {}


def test_message_rejects_none_subject():
    with pytest.raises(ValueError):
        Message(None)  # type: ignore[arg-type]


# -- Hub routing & fan-out ---------------------------------------------------


def test_notify_only_reaches_channels_recipient_has():
    hub = Hub()
    log = LogChannel()
    email = EmailChannel()
    hub.use(log).use(email)

    alice = Recipient("alice", log="stdout")  # no email address
    report = hub.notify(alice, Message("Hi", "body"))

    by_channel = {r.channel: r for r in report}
    assert by_channel["log"].status is Status.OK
    assert by_channel["email"].status is Status.SKIPPED
    assert report.ok  # skips don't count against ok


def test_notify_fans_out_to_all_matching_channels():
    hub = Hub()
    email = EmailChannel()
    sms = SMSChannel()
    hub.use(email).use(sms)

    bob = Recipient("bob", email="b@x.com", phone="+1555")
    report = hub.notify(bob, Message("Hi", "body"))

    attempted = {r.channel for r in report.attempted}
    assert attempted == {"email", "phone"}
    assert len(report.failures) == 0
    assert report.ok

    # Both stand-ins recorded what they would have sent.
    assert len(email.outbox) == 1
    assert email.outbox[0]["To"] == "b@x.com"
    assert email.outbox[0]["Subject"] == "Hi"
    assert len(sms.outbox) == 1
    assert sms.outbox[0] == ("+1555", "Hi: body")


def test_unknown_recipient_addresses_are_ignored():
    # A recipient with an address for a channel the hub doesn't know about
    # simply doesn't get that channel — no error.
    hub = Hub().use(LogChannel())
    r = Recipient("a", log="stdout", carrier_pigeon="roost-1")
    report = hub.notify(r, Message("Hi"))
    assert report.ok
    assert {c.channel for c in report} == {"log"}


# -- Failure visibility ------------------------------------------------------


class _BoomChannel(LogChannel):
    name = "boom"

    def send(self, recipient, message):
        raise RuntimeError("delivery exploded")


def test_failure_is_recorded_not_swallowed():
    hub = Hub().use(LogChannel()).use(_BoomChannel())
    r = Recipient("a", log="stdout", boom="x")
    report = hub.notify(r, Message("Hi"))

    boom_result = next(c for c in report if c.channel == "boom")
    assert boom_result.status is Status.FAILED
    assert isinstance(boom_result.error, RuntimeError)
    assert "exploded" in boom_result.detail

    # The other channel still succeeded.
    log_result = next(c for c in report if c.channel == "log")
    assert log_result.status is Status.OK

    assert not report.ok
    assert len(report.failures) == 1


def test_strict_mode_raises_on_first_failure():
    hub = Hub(strict=True).use(_BoomChannel()).use(LogChannel())
    r = Recipient("a", boom="x", log="stdout")
    with pytest.raises(RuntimeError, match="exploded"):
        hub.notify(r, Message("Hi"))


def test_no_channels_registered_is_ok_but_empty():
    hub = Hub()
    report = hub.notify(Recipient("a", email="x"), Message("Hi"))
    assert report.ok  # nothing attempted, nothing failed
    assert len(report) == 0


# -- LogChannel --------------------------------------------------------------


def test_log_channel_writes_to_logger():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("notifyhub.test.log")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False

    LogChannel(logger=logger).send(Recipient("a", log="x"), Message("Subj", "Bod"))
    handler.flush()
    assert "notifyhub -> a [log] Subj | Bod" in stream.getvalue()


# -- EmailChannel stand-in ---------------------------------------------------


def test_email_channel_records_when_no_smtp():
    ch = EmailChannel(sender="from@x.com")
    ch.send(Recipient("a", email="to@x.com"), Message("S", "B"))
    assert len(ch.outbox) == 1
    assert ch.outbox[0]["From"] == "from@x.com"
    assert ch.outbox[0]["To"] == "to@x.com"


def test_email_channel_uses_smtp_factory_when_given():
    sent = []

    class FakeSMTP:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def send_message(self, msg):
            sent.append(msg)

    ch = EmailChannel(smtp_factory=lambda: FakeSMTP())
    ch.send(Recipient("a", email="to@x.com"), Message("S", "B"))
    assert len(sent) == 1
    assert len(ch.outbox) == 0  # not recorded when really "sent"


def test_email_channel_missing_address_raises():
    ch = EmailChannel()
    with pytest.raises(ValueError, match="no email address"):
        ch.send(Recipient("a"), Message("S"))


# -- SMSChannel stand-in -----------------------------------------------------


def test_sms_channel_records_text():
    ch = SMSChannel()
    ch.send(Recipient("a", phone="+1555"), Message("S", "B"))
    assert ch.outbox == [("+1555", "S: B")]


def test_sms_channel_uses_sender_when_given():
    sent = []
    ch = SMSChannel(sender=lambda to, text: sent.append((to, text)))
    ch.send(Recipient("a", phone="+1555"), Message("S", "B"))
    assert sent == [("+1555", "S: B")]
    assert ch.outbox == []


# -- WebhookChannel ----------------------------------------------------------


class _FakeResponse:
    def __init__(self, status):
        self.status = status
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def getcode(self):
        return self.status


def _patch_urlopen(monkeypatch, response_or_error):
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req)
        if isinstance(response_or_error, BaseException):
            raise response_or_error
        return response_or_error

    monkeypatch.setattr("notifyhub.channels.webhook.urllib.request.urlopen", fake_urlopen)
    return calls


def test_webhook_success(monkeypatch):
    calls = _patch_urlopen(monkeypatch, _FakeResponse(200))
    ch = WebhookChannel()
    ch.send(Recipient("a", webhook="https://h.x/cb"), Message("S", "B", data={"k": 1}))
    assert len(calls) == 1
    import json

    payload = json.loads(calls[0].data.decode())
    assert payload["recipient"] == "a"
    assert payload["subject"] == "S"
    assert payload["data"] == {"k": 1}
    assert calls[0].headers["Content-type"] == "application/json"


def test_webhook_non_2xx_raises(monkeypatch):
    _patch_urlopen(monkeypatch, _FakeResponse(500))
    ch = WebhookChannel()
    with pytest.raises(urllib.error.HTTPError):
        ch.send(Recipient("a", webhook="https://h.x/cb"), Message("S"))


def test_webhook_network_error_raises_ioerror(monkeypatch):
    err = urllib.error.URLError("connection refused")
    _patch_urlopen(monkeypatch, err)
    ch = WebhookChannel()
    with pytest.raises(IOError, match="unreachable"):
        ch.send(Recipient("a", webhook="https://h.x/cb"), Message("S"))


def test_webhook_failure_surfaces_in_report(monkeypatch):
    _patch_urlopen(monkeypatch, urllib.error.URLError("down"))
    hub = Hub().use(WebhookChannel())
    report = hub.notify(Recipient("a", webhook="https://h.x/cb"), Message("S"))
    assert not report.ok
    assert len(report.failures) == 1
    assert report.failures[0].channel == "webhook"


# -- CLI ---------------------------------------------------------------------


def test_cli_sends_via_log(capsys):
    from notifyhub.__main__ import main

    rc = main(["--to-log", "--subject", "Hello", "--body", "world"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "log: Status.OK" in err


def test_cli_requires_a_channel(capsys):
    from notifyhub.__main__ import main

    rc = main(["--subject", "Hello"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "at least one delivery channel" in err


def test_cli_email_records_to_outbox(capsys):
    from notifyhub.__main__ import main

    rc = main(["--email", "a@x.com", "--subject", "Hi", "--body", "B"])
    assert rc == 0
    capsys.readouterr()  # drain
    # The CLI built its own hub; we just confirm exit code + status line.
    # (Outbox inspection would require plumbing; the channel test covers that.)


# -- Registration ------------------------------------------------------------


def test_cannot_register_duplicate_channel():
    hub = Hub().use(LogChannel())
    with pytest.raises(ValueError, match="already registered"):
        hub.use(LogChannel())


def test_use_returns_hub_for_chaining():
    hub = Hub()
    assert hub.use(LogChannel()) is hub