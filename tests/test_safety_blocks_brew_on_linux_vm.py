"""Tests: assess_command blocks brew on remote Linux targets (Plan 58 Bug C)."""

from __future__ import annotations

import unittest

from duckln.safety import assess_command


class SafetyBrewLinuxVmTest(unittest.TestCase):
    def test_brew_install_on_vm_is_blocked(self) -> None:
        assessment = assess_command("brew install node", execution_target="vm")
        self.assertTrue(assessment.blocked)
        self.assertIn("brew", assessment.reason.lower())
        self.assertIn("macos", assessment.reason.lower())

    def test_brew_install_on_local_is_not_blocked(self) -> None:
        assessment = assess_command("brew install node", execution_target="local")
        self.assertFalse(assessment.blocked)

    def test_brew_install_on_aws_is_blocked(self) -> None:
        assessment = assess_command("brew install python", execution_target="aws")
        self.assertTrue(assessment.blocked)

    def test_brew_install_on_gcp_is_blocked(self) -> None:
        assessment = assess_command("brew install rust", execution_target="gcp")
        self.assertTrue(assessment.blocked)

    def test_apt_install_on_vm_is_not_blocked(self) -> None:
        assessment = assess_command(
            "sudo apt install -y nodejs npm", execution_target="vm"
        )
        self.assertFalse(assessment.blocked)

    def test_sudo_brew_on_vm_is_also_blocked(self) -> None:
        """Defense-in-depth: even with sudo prefix, brew on Linux VM must be blocked."""
        assessment = assess_command("sudo brew install node", execution_target="vm")
        self.assertTrue(assessment.blocked)

    def test_command_without_execution_target_unchanged(self) -> None:
        """Backward-compat: calling assess_command without execution_target should still work."""
        assessment = assess_command("brew install node")
        # Without an explicit target, no Linux-VM block fires.
        self.assertFalse(assessment.blocked)


if __name__ == "__main__":
    unittest.main()
