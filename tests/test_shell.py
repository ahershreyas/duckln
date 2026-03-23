"""Phase 7 alignment for controlled shell execution.

Req: R4
Plan: 5
Tasks:
- Unit tests for safety rules
"""

from __future__ import annotations

import json
import logging
import unittest

from duckln.shell import ControlledCommandRunner


class ControlledCommandRunnerReqR4Plan5Test(unittest.TestCase):
    """Covers stdout/stderr capture and timeout handling."""

    def _parse_log_payload(self, entry: str) -> dict:
        return json.loads(entry[entry.find("{"):])

    def test_r4_plan5_runner_captures_stdout_and_stderr_separately(self) -> None:
        runner = ControlledCommandRunner()

        with self.assertLogs("duckln", level=logging.INFO) as captured:
            result = runner.run("printf 'hello'; printf 'oops' >&2", timeout_seconds=1)

        self.assertEqual("hello", result.stdout)
        self.assertEqual("oops", result.stderr)
        self.assertEqual(0, result.exit_code)
        self.assertFalse(result.timed_out)
        payload = self._parse_log_payload(captured.output[0])
        self.assertEqual("execution_result", payload["event"])
        self.assertEqual("True", payload["metadata"]["success"])

    def test_r4_plan5_runner_marks_timeout_and_kills_process(self) -> None:
        runner = ControlledCommandRunner()

        with self.assertLogs("duckln", level=logging.ERROR) as captured:
            result = runner.run("sleep 1", timeout_seconds=0.01)

        self.assertTrue(result.timed_out)
        self.assertIsNone(result.exit_code)
        self.assertIn("timed out", result.stderr)
        payload = self._parse_log_payload(captured.output[0])
        self.assertEqual("execution_result", payload["event"])
        self.assertEqual("True", payload["metadata"]["timed_out"])


if __name__ == "__main__":
    unittest.main()
