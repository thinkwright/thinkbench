"""Tests for the workflow library, focused on the interesting transition cases."""

import pytest

from workflow import Process, Transition, Instance, Rejected


# A representative order process used across several tests.
def order_process():
    return Process(
        stages=["received", "paid", "packed", "shipped", "cancelled", "refunded"],
        start="received",
        transitions=[
            ("pay", "received", "paid"),
            ("decline", "received", "cancelled"),
            ("pack", "paid", "packed"),
            ("ship", "packed", "shipped"),
            ("refund", "paid", "refunded"),
        ],
    )


# --- definition / shape ------------------------------------------------


def test_start_must_be_a_known_stage():
    with pytest.raises(ValueError, match="start stage"):
        Process(stages=["a", "b"], start="c", transitions=[])


def test_unknown_transition_stages_rejected():
    with pytest.raises(ValueError, match="unknown stage"):
        Process(
            stages=["a", "b"],
            start="a",
            transitions=[("go", "a", "z")],
        )


def test_duplicate_transition_names_rejected():
    with pytest.raises(ValueError, match="duplicate transition name"):
        Process(
            stages=["a", "b", "c"],
            start="a",
            transitions=[("go", "a", "b"), ("go", "b", "c")],
        )


def test_transitions_can_be_given_as_objects_or_dicts():
    p = Process(
        stages=["a", "b", "c"],
        start="a",
        transitions=[
            Transition("go", "a", "b"),
            {"name": "next", "from": "b", "to": "c"},
        ],
    )
    assert set(p.transitions) == {"go", "next"}


# --- the happy path -----------------------------------------------------


def test_instance_starts_at_start_stage():
    p = order_process()
    inst = p.start()
    assert inst.stage == "received"
    assert inst.history == ()
    assert not inst.is_terminal


def test_advance_follows_a_path():
    p = order_process()
    inst = p.start()
    inst.advance("pay").advance("pack").advance("ship")
    assert inst.stage == "shipped"
    assert inst.history == ("pay", "pack", "ship")
    assert inst.is_terminal


def test_advance_returns_self_for_chaining():
    p = order_process()
    assert p.start().advance("pay") is not None
    inst = p.start()
    assert inst.advance("pay") is inst


# --- branching: one stage can go more than one way ----------------------


def test_branching_from_one_stage():
    p = order_process()
    paid = p.start().advance("pay")
    assert set(paid.available()) == {"pack", "refund"}

    packed = p.start().advance("pay").advance("pack")
    assert packed.stage == "packed"
    assert set(packed.available()) == {"ship"}


def test_two_different_orders_take_different_paths():
    p = order_process()
    cleared = p.start().advance("pay").advance("pack").advance("ship")
    declined = p.start().advance("decline")
    assert cleared.stage == "shipped"
    assert declined.stage == "cancelled"
    assert declined.is_terminal


# --- refusing moves that don't make sense -------------------------------


def test_skip_a_stage_is_refused():
    p = order_process()
    inst = p.start()
    with pytest.raises(Rejected) as exc:
        inst.advance("ship")  # can't ship from received
    assert exc.value.reason == Rejected.NOT_FROM_HERE
    assert exc.value.details["expected_from"] == "packed"
    # instance is untouched
    assert inst.stage == "received"
    assert inst.history == ()


def test_unknown_transition_refused():
    p = order_process()
    inst = p.start()
    with pytest.raises(Rejected) as exc:
        inst.advance("teleport")
    assert exc.value.reason == Rejected.NO_SUCH_TRANSITION


def test_cannot_advance_from_terminal_stage():
    p = order_process()
    inst = p.start().advance("decline")  # cancelled is terminal
    with pytest.raises(Rejected) as exc:
        inst.advance("pay")
    assert exc.value.reason == Rejected.ALREADY_TERMINAL


def test_rejection_message_is_readable():
    p = order_process()
    inst = p.start()
    with pytest.raises(Rejected) as exc:
        inst.advance("ship")
    msg = str(exc.value)
    assert "ship" in msg
    assert "received" in msg
    assert "packed" in msg


# --- explicitly ruling out a path --------------------------------------


def test_forbid_rules_out_a_source():
    # "refund" is only meaningful before packing; rule it out from packed.
    p = Process(
        stages=["received", "paid", "packed", "shipped", "refunded"],
        start="received",
        transitions=[
            ("pay", "received", "paid"),
            ("pack", "paid", "packed"),
            ("ship", "packed", "shipped"),
            Transition("refund", "paid", "refunded", forbid=("packed",)),
        ],
    )
    assert p.can("paid", "refund") is True
    assert p.can("packed", "refund") is False  # ruled out

    inst = p.start().advance("pay").advance("pack")
    with pytest.raises(Rejected) as exc:
        inst.advance("refund")
    # forbid is checked before the from_stage mismatch, so we get FORBIDDEN
    assert exc.value.reason == Rejected.FORBIDDEN


def test_forbid_unknown_stage_rejected_at_definition():
    with pytest.raises(ValueError, match="forbids unknown stage"):
        Process(
            stages=["a", "b"],
            start="a",
            transitions=[Transition("go", "a", "b", forbid=("z",))],
        )


# --- guards: instance-level conditions ---------------------------------


def test_guard_can_refuse_a_move():
    # "pay" only succeeds if the instance data says payment cleared.
    def payment_cleared(inst):
        return inst.data.get("cleared", False)

    p = Process(
        stages=["received", "paid", "cancelled"],
        start="received",
        transitions=[
            Transition("pay", "received", "paid", guard=payment_cleared),
            ("decline", "received", "cancelled"),
        ],
    )

    declined = p.start(data={"cleared": False})
    with pytest.raises(Rejected) as exc:
        declined.advance("pay")
    assert exc.value.reason == Rejected.GUARD_REFUSED
    assert declined.stage == "received"
    # the same transition succeeds for a different instance
    cleared = p.start(data={"cleared": True})
    cleared.advance("pay")
    assert cleared.stage == "paid"


def test_can_evaluates_guards():
    p = Process(
        stages=["received", "paid"],
        start="received",
        transitions=[
            Transition("pay", "received", "paid", guard=lambda i: i.data["ok"]),
        ],
    )
    assert p.start(data={"ok": True}).can("pay") is True
    assert p.start(data={"ok": False}).can("pay") is False


# --- introspection ------------------------------------------------------


def test_transitions_from_and_terminal():
    p = order_process()
    assert {t.name for t in p.transitions_from("received")} == {"pay", "decline"}
    assert p.transitions_from("shipped") == ()
    assert p.is_terminal("shipped") is True
    assert p.is_terminal("received") is False


def test_describe_lists_every_stage():
    p = order_process()
    text = p.describe()
    assert "start='received'" in text
    assert "received: pay->paid, decline->cancelled" in text
    assert "shipped: (terminal)" in text


# --- resume: rehydrating an instance -----------------------------------


def test_resume_rebuilds_at_a_stage_with_history():
    p = order_process()
    inst = p.resume("packed", history=["pay", "pack"])
    assert inst.stage == "packed"
    assert inst.history == ("pay", "pack")
    # it can continue from there
    inst.advance("ship")
    assert inst.stage == "shipped"


def test_resume_unknown_stage_rejected():
    p = order_process()
    with pytest.raises(ValueError):
        p.resume("nowhere")


# --- a process that is not a straight line, end to end -----------------


def test_nonlinear_process_round_trip():
    # Claims: can be reopened after resolution, can escalate at multiple
    # points, and some moves are forbidden once you've escalated.
    p = Process(
        stages=["new", "open", "escalated", "resolved", "closed"],
        start="new",
        transitions=[
            ("open", "new", "open"),
            # escalation is allowed from new OR open (two transitions, one name
            # each, sharing a target):
            ("escalate", "new", "escalated"),
            ("escalate", "open", "escalated"),
            # once escalated you cannot just resolve; you must de-escalate.
            # We rule out resolving directly from escalated by simply not
            # defining that transition -- but also forbid backsliding:
            ("de_escalate", "escalated", "open"),
            ("resolve", "open", "resolved"),
            ("close", "resolved", "closed"),
            ("reopen", "closed", "open"),
            # closing straight from resolved is fine, but never from open:
            Transition("close", "resolved", "closed", forbid=("open",)),
        ],
    )

    # escalate right away from new
    a = p.start().advance("escalate")
    assert a.stage == "escalated"
    # cannot resolve from escalated (no such transition from here)
    with pytest.raises(Rejected):
        a.advance("resolve")
    a.advance("de_escalate").advance("resolve").advance("close")
    assert a.stage == "closed"
    assert a.is_terminal is False  # reopen is possible
    a.advance("reopen")
    assert a.stage == "open"

    # the forbid on "close" from open:
    b = p.start().advance("open")
    assert b.can("close") is False
    with pytest.raises(Rejected) as exc:
        b.advance("close")
    assert exc.value.reason == Rejected.NO_SUCH_TRANSITION