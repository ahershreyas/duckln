"""Plan 72 Phase 6 — tightened HOOTLWO + executor mode enforcement."""

from __future__ import annotations

import unittest

from duckln.modes import ControlMode
from duckln.repo_bringup import _step_allowed_in_mode


class StepModePolicyTest(unittest.TestCase):
    def test_hootlwo_does_not_auto_run_s2_s3(self) -> None:
        # No approve callback → S2/S3 must NOT run automatically in HOOTLWO.
        self.assertFalse(_step_allowed_in_mode("S2", ControlMode.HOOTLWO, None))
        self.assertFalse(_step_allowed_in_mode("S3", ControlMode.HOOTLWO, None))

    def test_hootlwo_auto_runs_s0_s1(self) -> None:
        self.assertTrue(_step_allowed_in_mode("S0", ControlMode.HOOTLWO, None))
        self.assertTrue(_step_allowed_in_mode("S1", ControlMode.HOOTLWO, None))

    def test_s4_destructive_is_user_decided(self) -> None:
        # Plan 160 Phase A: a destructive (S4) step is NOT auto-blocked — the USER decides.
        for mode in (ControlMode.HITL, ControlMode.HOTL, ControlMode.HOOTLWO):
            # Unattended (no way to ask) → never runs destructive.
            self.assertFalse(_step_allowed_in_mode("S4", mode, None))
            # User says yes → runs; user says no → doesn't.
            self.assertTrue(_step_allowed_in_mode("S4", mode, lambda _m: True))
            self.assertFalse(_step_allowed_in_mode("S4", mode, lambda _m: False))
            # 3-way decider: "all" (approve-all-this-session) → runs.
            self.assertTrue(_step_allowed_in_mode("S4", mode, None, destructive_decider=lambda _m: "all"))

    def test_hotl_s1_auto_s2_needs_approval(self) -> None:
        self.assertTrue(_step_allowed_in_mode("S1", ControlMode.HOTL, None))
        self.assertFalse(_step_allowed_in_mode("S2", ControlMode.HOTL, None))
        self.assertTrue(_step_allowed_in_mode("S2", ControlMode.HOTL, lambda _m: True))

    def test_hitl_only_s0_auto(self) -> None:
        self.assertTrue(_step_allowed_in_mode("S0", ControlMode.HITL, None))
        self.assertFalse(_step_allowed_in_mode("S1", ControlMode.HITL, None))

    def test_s2s3_approved_runs_when_user_says_yes(self) -> None:
        self.assertTrue(_step_allowed_in_mode("S3", ControlMode.HOOTLWO, lambda _m: True))
        self.assertFalse(_step_allowed_in_mode("S3", ControlMode.HOOTLWO, lambda _m: False))


if __name__ == "__main__":
    unittest.main()
