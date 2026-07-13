from __future__ import annotations

import unittest

from duckln.modes import ControlMode
from duckln.repo_bringup import _step_allowed_in_mode


def _raising_approve(_prompt):
    raise AssertionError("approve must NOT be called for an approved planner step")


class TestStepAuthorization(unittest.TestCase):
    def test_approved_planner_steps_skip_prompt(self):
        for sclass in ("S1", "S2", "S3"):
            allowed = _step_allowed_in_mode(
                sclass, ControlMode.HOOTLWO, _raising_approve,
                plan_approved=True, step_origin="planner",
            )
            self.assertTrue(allowed)

    def test_s4_user_decided_even_when_plan_approved(self):
        # Plan 160 Phase A: a destructive (S4) step ALWAYS asks the user, even inside an
        # approved plan (approving the plan does NOT pre-authorize destructive) — the USER
        # decides; it is no longer a silent auto-block.
        self.assertFalse(  # unattended → never runs destructive
            _step_allowed_in_mode("S4", ControlMode.HOOTLWO, None, plan_approved=True, step_origin="planner")
        )
        self.assertTrue(  # user approved when asked → runs
            _step_allowed_in_mode("S4", ControlMode.HOOTLWO, lambda _p: True, plan_approved=True, step_origin="planner")
        )
        self.assertFalse(  # user declined → doesn't run
            _step_allowed_in_mode("S4", ControlMode.HOOTLWO, lambda _p: False, plan_approved=True, step_origin="planner")
        )

    def test_amendment_step_still_prompts(self):
        calls = []

        def approve(prompt):
            calls.append(prompt)
            return True

        allowed = _step_allowed_in_mode(
            "S2", ControlMode.HOOTLWO, approve,
            plan_approved=True, step_origin="amendment",
        )
        self.assertTrue(allowed)
        self.assertEqual(len(calls), 1)  # amendment was prompted

    def test_unapproved_context_unchanged(self):
        calls = []

        def approve(prompt):
            calls.append(prompt)
            return True

        allowed = _step_allowed_in_mode("S2", ControlMode.HOOTLWO, approve)
        self.assertTrue(allowed)
        self.assertEqual(len(calls), 1)  # legacy per-step prompt preserved


if __name__ == "__main__":
    unittest.main()
