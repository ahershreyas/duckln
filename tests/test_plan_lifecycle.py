"""Plan 72 Phase 2 — lifecycle state machine tests."""

from __future__ import annotations

import unittest

from duckln.plan_lifecycle import (
    LifecycleTransitionError,
    PlanLifecycle,
    PlanLifecycleState,
    TERMINAL_STATES,
)


class LifecycleTest(unittest.TestCase):
    def test_happy_path_reaches_complete(self) -> None:
        cleared = []
        lc = PlanLifecycle(on_clear_activity=lambda: cleared.append(True), persist=lambda s: None)
        for state in (
            PlanLifecycleState.CONTEXT_COLLECTED,
            PlanLifecycleState.PLAN_DRAFTED,
            PlanLifecycleState.SUPERVISOR_REVIEW,
            PlanLifecycleState.USER_REVIEW,
            PlanLifecycleState.APPROVED,
            PlanLifecycleState.EXECUTING,
            PlanLifecycleState.VERIFYING,
            PlanLifecycleState.REFLECTING,
        ):
            lc.advance(state)
        lc.terminal(PlanLifecycleState.COMPLETE)
        self.assertTrue(lc.is_terminal)
        self.assertEqual(lc.state, PlanLifecycleState.COMPLETE)
        self.assertEqual(cleared, [True])

    def test_illegal_transition_raises(self) -> None:
        lc = PlanLifecycle(persist=lambda s: None)
        with self.assertRaises(LifecycleTransitionError):
            lc.advance(PlanLifecycleState.EXECUTING)  # can't jump from intent_detected

    def test_cannot_advance_after_terminal(self) -> None:
        lc = PlanLifecycle(persist=lambda s: None)
        lc.terminal(PlanLifecycleState.WAITING_ON_USER)
        with self.assertRaises(LifecycleTransitionError):
            lc.advance(PlanLifecycleState.CONTEXT_COLLECTED)

    def test_context_manager_exception_reports_internal_bug(self) -> None:
        cleared = []
        with self.assertRaises(ValueError):
            with PlanLifecycle(on_clear_activity=lambda: cleared.append(True), persist=lambda s: None) as lc:
                lc.advance(PlanLifecycleState.CONTEXT_COLLECTED)
                raise ValueError("boom")
        self.assertEqual(lc.state, PlanLifecycleState.DUCKLN_INTERNAL_BUG_REPORTED)
        self.assertTrue(lc.is_terminal)
        self.assertTrue(cleared)

    def test_context_manager_clean_exit_defaults_waiting(self) -> None:
        cleared = []
        with PlanLifecycle(on_clear_activity=lambda: cleared.append(True), persist=lambda s: None) as lc:
            lc.advance(PlanLifecycleState.CONTEXT_COLLECTED)
        self.assertEqual(lc.state, PlanLifecycleState.WAITING_ON_USER)
        self.assertTrue(cleared)

    def test_blocker_amend_loop_allowed(self) -> None:
        lc = PlanLifecycle(persist=lambda s: None)
        for state in (
            PlanLifecycleState.CONTEXT_COLLECTED,
            PlanLifecycleState.PLAN_DRAFTED,
            PlanLifecycleState.SUPERVISOR_REVIEW,
            PlanLifecycleState.USER_REVIEW,
            PlanLifecycleState.APPROVED,
            PlanLifecycleState.EXECUTING,
            PlanLifecycleState.BLOCKER_CAPTURED,
            PlanLifecycleState.AMEND_PLAN,
            PlanLifecycleState.SUPERVISOR_REVIEW,
            PlanLifecycleState.USER_REVIEW,
        ):
            lc.advance(state)
        self.assertEqual(lc.state, PlanLifecycleState.USER_REVIEW)

    def test_terminal_set_has_exactly_four(self) -> None:
        self.assertEqual(len(TERMINAL_STATES), 4)

    def test_persists_status_to_config(self) -> None:
        import tempfile
        from pathlib import Path
        from state.access import read_workflow_state

        with tempfile.TemporaryDirectory() as td:
            lc = PlanLifecycle(config_dir=Path(td))
            lc.advance(PlanLifecycleState.CONTEXT_COLLECTED)
            lc.terminal(PlanLifecycleState.COMPLETE)
            status = read_workflow_state(Path(td)).get("active_objective_status")
            self.assertEqual(status, "complete")


if __name__ == "__main__":
    unittest.main()
