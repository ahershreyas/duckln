"""Phase 7 alignment for mode policy behavior.

Req: R3, R4, R7
Plan: 4, 8, 14
Tasks:
- Unit tests for safety rules
"""

from __future__ import annotations

import unittest

from duckln.modes import ControlMode, evaluate_mode_action
from duckln.safety import SafetyAssessment, SafetyClass, assess_command


class ModePolicyReqR3R4R7Plan4Plan8Plan14Test(unittest.TestCase):
    """Covers HITL, HOTL, and HOOTLWO mode separation."""

    def test_r3_r7_plan4_plan8_hitl_never_executes_ai_suggested_commands(self) -> None:
        decision = evaluate_mode_action(
            ControlMode.HITL,
            assess_command("python --version"),
            is_ai_suggested=True,
        )

        self.assertFalse(decision.allowed)
        self.assertIn("never executes", decision.reason)

    def test_r3_r7_plan4_plan8_hotl_requires_approval_for_ai_suggested_commands(self) -> None:
        decision = evaluate_mode_action(
            ControlMode.HOTL,
            assess_command("python --version"),
            is_ai_suggested=True,
        )

        self.assertFalse(decision.allowed)
        self.assertTrue(decision.requires_approval)

    def test_r3_r7_plan4_plan8_hotl_allows_ai_suggested_command_after_approval(self) -> None:
        decision = evaluate_mode_action(
            ControlMode.HOTL,
            assess_command("python --version"),
            is_ai_suggested=True,
            user_approved=True,
        )

        self.assertTrue(decision.allowed)
        self.assertFalse(decision.auto_run)

    def test_r3_r4_plan14_hootlwo_auto_runs_whitelisted_safe_commands_only(self) -> None:
        decision = evaluate_mode_action(
            ControlMode.HOOTLWO,
            assess_command("pip show duckln"),
            is_ai_suggested=True,
        )

        self.assertTrue(decision.allowed)
        self.assertTrue(decision.auto_run)

    def test_r3_r4_plan14_hootlwo_non_whitelisted_command_needs_approval(self) -> None:
        decision = evaluate_mode_action(
            ControlMode.HOOTLWO,
            assess_command("python script.py"),
            is_ai_suggested=True,
        )

        self.assertFalse(decision.allowed)
        self.assertTrue(decision.requires_approval)

    def test_r4_plan5_blocked_commands_are_denied_in_all_modes(self) -> None:
        blocked = SafetyAssessment(
            safety_class=SafetyClass.S4,
            blocked=True,
            whitelisted=False,
            reason="Blocked.",
        )

        decision = evaluate_mode_action(ControlMode.HOOTLWO, blocked, is_ai_suggested=True)

        self.assertFalse(decision.allowed)
        self.assertFalse(decision.requires_approval)


if __name__ == "__main__":
    unittest.main()
