from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from duckln.repo_bringup import (
    _node_npm_prerequisite_check,
    _node_package_manager_prerequisite_check,
    _wrap_command_for_execution_target,
)


class TestNodePrerequisiteCheck(unittest.TestCase):
    def test_npm_check_is_simple_version_check(self):
        cmd = _node_npm_prerequisite_check(execution_target="local")
        self.assertEqual(cmd, "node --version && npm --version")
        self.assertNotIn("node -e", cmd)
        self.assertNotIn("require(", cmd)

    def test_pnpm_and_yarn_checks(self):
        pnpm = _node_package_manager_prerequisite_check({"pnpm"}, execution_target="local")
        yarn = _node_package_manager_prerequisite_check({"yarn"}, execution_target="local")
        self.assertEqual(pnpm, "node --version && pnpm --version")
        self.assertEqual(yarn, "node --version && yarn --version")
        for c in (pnpm, yarn):
            self.assertNotIn("node -e", c)

    def test_survives_vm_wrapping_no_quote_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            cmd = _node_npm_prerequisite_check(execution_target="vm")
            wrapped, meta = _wrap_command_for_execution_target(
                config_dir=Path(tmp),
                execution_target="vm",
                command=cmd,
                cwd=None,
                preferred_vm_name="testvm",
            )
            self.assertIsNotNone(wrapped)
            self.assertIn("multipass exec", wrapped)
            self.assertIn("bash -lc", wrapped)
            # The inner command carries no nested single-quoted node -e payload,
            # so it does not collide with the bash -lc '...' wrapper quotes.
            self.assertNotIn("node -e", wrapped)
            self.assertIn("node --version", wrapped)


if __name__ == "__main__":
    unittest.main()
