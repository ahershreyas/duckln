"""Tests: runtime prerequisite plan honors the OS-volunteered install hint (Plan 55)."""

from __future__ import annotations

import unittest

from duckln.main import _build_runtime_prerequisite_plan
from duckln.repair_intake import summarize_failure_incident


class RuntimeOsHintTest(unittest.TestCase):
    def test_apt_hint_overrides_hardcoded_nodesource_pipeline(self) -> None:
        stderr = "Command 'node' not found, but can be installed with:\n  sudo apt install nodejs\n"
        incident = summarize_failure_incident(command="node -v", stderr=stderr)
        plan = _build_runtime_prerequisite_plan(incident=incident, execution_target="local")
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.dependency, "node")
        self.assertIn("apt install -y nodejs", plan.install_command)
        self.assertNotIn("nodesource", plan.install_command.lower())
        self.assertEqual(plan.installer, "os-hint:apt")

    def test_no_hint_falls_back_to_catalog_command(self) -> None:
        stderr = "node: command not found\nbash: line 1: node: command not found\n"
        incident = summarize_failure_incident(command="node -v", stderr=stderr)
        plan = _build_runtime_prerequisite_plan(incident=incident, execution_target="local")
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.dependency, "node")
        self.assertNotEqual(plan.installer, "os-hint:apt")

    def test_hint_strips_shell_metachar_injection(self) -> None:
        stderr = "Command 'node' not found, but can be installed with:\n  sudo apt install nodejs; rm -rf /\n"
        incident = summarize_failure_incident(command="node -v", stderr=stderr)
        plan = _build_runtime_prerequisite_plan(incident=incident, execution_target="local")
        self.assertIsNotNone(plan)
        assert plan is not None
        # The hint parser must stop at the shell metacharacter; the resulting
        # plan command must NOT contain the injected `rm -rf` payload.
        self.assertNotIn("rm -rf", plan.install_command)
        self.assertNotIn(";", plan.install_command)


if __name__ == "__main__":
    unittest.main()
