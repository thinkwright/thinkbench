"""CLI smoke tests."""

from notifyhub.__main__ import main


def test_cli_sends_email(capsys):
    rc = main(["hello", "--to", "alice@example.com", "--subject", "hi"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "delivered via email" in out
    assert "alice@example.com" in out


def test_cli_picks_kind_from_prefix(capsys):
    rc = main(["ping", "--to", "https://hooks.example.com/x"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "delivered via webhook" in out


def test_cli_requires_a_recipient(capsys):
    rc = main(["hello"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--to" in err


def test_cli_reports_failure(capsys, monkeypatch):
    # Force the default Notifier's log backend to fail — but log backend doesn't
    # fail; instead, point at an SMS-only recipient with no SMS backend.
    rc = main(["hello", "--to", "sms:+15555550100"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "no reachable channel" in err
