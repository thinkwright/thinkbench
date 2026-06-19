"""Tests for the workflow library."""

import unittest

from workflow import (
    AmbiguousTransitionError,
    Driver,
    IllegalTransitionError,
    UnknownStageError,
    Workflow,
    WorkflowError,
)


def make_order_workflow() -> Workflow:
    """A realistic order process: received -> paid -> packed -> shipped, with a
    payment-declined branch that ends in a cancelled terminal state."""
    wf = Workflow(name="order")
    wf.add_transition("received", "pay", "paid")
    wf.add_transition("received", "cancel", "cancelled")
    wf.add_transition("paid", "pack", "packed")
    wf.add_transition("paid", "refund", "refunded")
    wf.add_transition("packed", "ship", "shipped")
    wf.add_transition("packed", "unpack", "received")  # back to the start of fulfillment
    wf.add_transition("shipped", "deliver", "delivered")
    # 'cancelled', 'refunded', 'delivered' are terminal — no outgoing transitions.
    return wf


class WorkflowDefinitionTests(unittest.TestCase):
    def test_stages_are_tracked(self):
        wf = make_order_workflow()
        self.assertEqual(
            wf.stages(),
            {"received", "paid", "packed", "shipped", "delivered",
             "cancelled", "refunded"},
        )

    def test_duplicate_transition_is_rejected(self):
        wf = Workflow()
        wf.add_transition("a", "go", "b")
        with self.assertRaises(WorkflowError) as ctx:
            wf.add_transition("a", "go", "c")
        self.assertIn("duplicate", str(ctx.exception))

    def test_transitions_preserve_insertion_order(self):
        wf = Workflow()
        wf.add_transition("a", "x", "b")
        wf.add_transition("b", "y", "c")
        self.assertEqual(
            [t.source for t in wf.transitions()],
            ["a", "b"],
        )


class WorkflowAdvanceTests(unittest.TestCase):
    def setUp(self):
        self.wf = make_order_workflow()

    def test_happy_path(self):
        self.assertEqual(self.wf.advance("received", "pay"), "paid")
        self.assertEqual(self.wf.advance("paid", "pack"), "packed")
        self.assertEqual(self.wf.advance("packed", "ship"), "shipped")
        self.assertEqual(self.wf.advance("shipped", "deliver"), "delivered")

    def test_payment_declined_branch(self):
        # An order can be cancelled straight from 'received' if payment never starts.
        self.assertEqual(self.wf.advance("received", "cancel"), "cancelled")
        self.assertTrue(self.wf.is_terminal("cancelled"))

    def test_refund_branch(self):
        self.assertEqual(self.wf.advance("received", "pay"), "paid")
        self.assertEqual(self.wf.advance("paid", "refund"), "refunded")
        self.assertTrue(self.wf.is_terminal("refunded"))

    def test_unpack_returns_to_received(self):
        # Real processes aren't a straight line: an unpacked order goes back to received.
        self.assertEqual(self.wf.advance("received", "pay"), "paid")
        self.assertEqual(self.wf.advance("paid", "pack"), "packed")
        self.assertEqual(self.wf.advance("packed", "unpack"), "received")

    def test_terminal_stage_rejects_any_event(self):
        with self.assertRaises(IllegalTransitionError) as ctx:
            self.wf.advance("delivered", "ship")
        msg = str(ctx.exception)
        self.assertIn("'delivered'", msg)
        self.assertIn("'ship'", msg)
        self.assertIn("terminal", msg)

    def test_illegal_event_lists_what_is_allowed(self):
        # 'ship' is not allowed from 'received'; the error should tell you what is.
        with self.assertRaises(IllegalTransitionError) as ctx:
            self.wf.advance("received", "ship")
        msg = str(ctx.exception)
        self.assertIn("'received'", msg)
        self.assertIn("'ship'", msg)
        # The two events that ARE allowed from 'received':
        self.assertIn("'pay'", msg)
        self.assertIn("'cancel'", msg)

    def test_unknown_stage_raises(self):
        with self.assertRaises(UnknownStageError):
            self.wf.advance("not_a_stage", "pay")

    def test_unknown_stage_on_can_raises(self):
        with self.assertRaises(UnknownStageError):
            self.wf.can("not_a_stage", "pay")

    def test_can_reports_legality(self):
        self.assertTrue(self.wf.can("received", "pay"))
        self.assertFalse(self.wf.can("received", "ship"))
        self.assertFalse(self.wf.can("delivered", "anything"))

    def test_allowed_events_for_terminal_is_empty(self):
        self.assertEqual(self.wf.allowed_events("delivered"), ())

    def test_allowed_events_for_non_terminal(self):
        self.assertEqual(
            set(self.wf.allowed_events("received")),
            {"pay", "cancel"},
        )


class DriverTests(unittest.TestCase):
    def setUp(self):
        self.wf = make_order_workflow()
        self.driver = self.wf.driver("received")

    def test_starts_at_given_stage(self):
        self.assertEqual(self.driver.state, "received")
        self.assertEqual(self.driver.stage, "received")

    def test_advance_moves_and_returns_new_stage(self):
        new = self.driver.advance("pay")
        self.assertEqual(new, "paid")
        self.assertEqual(self.driver.state, "paid")

    def test_can_reflects_current_stage(self):
        self.assertTrue(self.driver.can("pay"))
        self.assertFalse(self.driver.can("ship"))
        self.driver.advance("pay")
        # Now we're at 'paid': 'pay' is no longer legal, 'pack' is.
        self.assertFalse(self.driver.can("pay"))
        self.assertTrue(self.driver.can("pack"))

    def test_allowed_events_reflects_current_stage(self):
        self.assertEqual(set(self.driver.allowed_events), {"pay", "cancel"})
        self.driver.advance("pay")
        self.assertEqual(set(self.driver.allowed_events), {"pack", "refund"})

    def test_is_terminal(self):
        self.assertFalse(self.driver.is_terminal)
        self.driver.advance("cancel")
        self.assertTrue(self.driver.is_terminal)
        self.assertEqual(self.driver.allowed_events, ())

    def test_illegal_advance_does_not_move(self):
        # A rejected move must not change the driver's state.
        with self.assertRaises(IllegalTransitionError):
            self.driver.advance("ship")
        self.assertEqual(self.driver.state, "received")

    def test_full_happy_path_via_driver(self):
        d = self.wf.driver("received")
        d.advance("pay")
        d.advance("pack")
        d.advance("ship")
        d.advance("deliver")
        self.assertEqual(d.state, "delivered")
        self.assertTrue(d.is_terminal)

    def test_branching_via_driver(self):
        d = self.wf.driver("received")
        d.advance("pay")
        d.advance("refund")
        self.assertEqual(d.state, "refunded")
        self.assertTrue(d.is_terminal)

    def test_driver_start_must_be_known_stage(self):
        with self.assertRaises(UnknownStageError):
            self.wf.driver("not_a_stage")


class ReprTests(unittest.TestCase):
    def test_workflow_repr_is_informative(self):
        wf = make_order_workflow()
        r = repr(wf)
        self.assertIn("order", r)
        self.assertIn("7", r)  # 7 stages
        self.assertIn("7", r)  # 7 transitions

    def test_driver_repr_is_informative(self):
        wf = make_order_workflow()
        d = wf.driver("received")
        self.assertIn("received", repr(d))
        self.assertIn("order", repr(d))


if __name__ == "__main__":
    unittest.main()
