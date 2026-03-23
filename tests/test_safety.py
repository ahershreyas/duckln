"""Phase 7 alignment for safety rules.

Req: R3, R4
Plan: 5, 14
Tasks:
- Unit tests for safety rules
"""

from __future__ import annotations

import json
import logging
import unittest

from duckln.safety import SafetyClass, assess_command, match_blocked_command


class SafetyRulesReqR3R4Plan5Plan14Test(unittest.TestCase):
    """Covers blocked commands plus S0-S4 classification behavior."""

    def _parse_log_payload(self, entry: str) -> dict:
        return json.loads(entry[entry.find("{"):])

    def test_r4_plan5_s4_blocked_command_matcher_blocks_root_delete(self) -> None:
        with self.assertLogs("duckln", level=logging.WARNING) as captured:
            assessment = match_blocked_command("rm -rf /")

        self.assertIsNotNone(assessment)
        assert assessment is not None
        self.assertTrue(assessment.blocked)
        self.assertEqual(SafetyClass.S4, assessment.safety_class)
        payload = self._parse_log_payload(captured.output[0])
        self.assertEqual("blocked_command", payload["event"])

    def test_r3_r4_plan14_s0_diagnostic_command_is_whitelisted(self) -> None:
        assessment = assess_command("python --version")

        self.assertEqual(SafetyClass.S0, assessment.safety_class)
        self.assertTrue(assessment.whitelisted)

    def test_r3_r4_plan14_s1_safe_install_command_is_whitelisted(self) -> None:
        assessment = assess_command("python -m pip install duckdb")

        self.assertEqual(SafetyClass.S1, assessment.safety_class)
        self.assertTrue(assessment.whitelisted)

    def test_r4_plan5_s3_elevated_mutation_command_needs_approval(self) -> None:
        assessment = assess_command("chmod +x script.sh")

        self.assertEqual(SafetyClass.S3, assessment.safety_class)
        self.assertFalse(assessment.whitelisted)

    def test_r4_plan5_s2_unknown_non_blocked_command_is_classified(self) -> None:
        assessment = assess_command("python script.py")

        self.assertEqual(SafetyClass.S2, assessment.safety_class)
        self.assertFalse(assessment.blocked)


if __name__ == "__main__":
    unittest.main()
