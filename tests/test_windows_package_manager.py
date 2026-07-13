"""Tests: Windows winget package manager support (Plan 61 Fix A)."""

from __future__ import annotations

import unittest

from duckln.readme_skill import (
    ReadmePrerequisite,
    detect_package_manager,
    _KNOWN_PREREQUISITES,
)
from duckln.safety import assess_command


def _prereq_for(name: str) -> ReadmePrerequisite:
    spec = _KNOWN_PREREQUISITES[name]
    return ReadmePrerequisite(
        name=name,
        version_constraint=None,
        probe_command=spec[0],
        install_apt=spec[1],
        install_brew=spec[2],
        install_fallback=spec[3],
        install_winget=spec[4] if len(spec) > 4 else None,
    )


class WindowsDetectPackageManagerTest(unittest.TestCase):
    def test_windows_returns_winget(self) -> None:
        self.assertEqual(detect_package_manager("Windows"), "winget")

    def test_linux_still_returns_apt(self) -> None:
        self.assertEqual(detect_package_manager("Linux"), "apt")

    def test_darwin_still_returns_brew(self) -> None:
        self.assertEqual(detect_package_manager("Darwin"), "brew")


class WingetInstallCommandTest(unittest.TestCase):
    def test_node_winget_install_command(self) -> None:
        p = _prereq_for("node")
        cmd = p.install_command_for(package_manager="winget")
        self.assertIsNotNone(cmd)
        self.assertIn("winget install", cmd)
        self.assertIn("OpenJS.NodeJS.LTS", cmd)
        self.assertIn("--silent", cmd)

    def test_python_winget_install_command(self) -> None:
        p = _prereq_for("python")
        cmd = p.install_command_for(package_manager="winget")
        self.assertIn("Python.Python.3.12", cmd)

    def test_uv_winget_install_command(self) -> None:
        p = _prereq_for("uv")
        cmd = p.install_command_for(package_manager="winget")
        self.assertIn("astral-sh.uv", cmd)

    def test_rust_winget_install_command(self) -> None:
        p = _prereq_for("rust")
        cmd = p.install_command_for(package_manager="winget")
        self.assertIn("Rustlang.Rustup", cmd)

    def test_pnpm_falls_back_to_npm_g_on_winget(self) -> None:
        """pnpm has no winget id; install_command_for("winget") returns the
        existing install_fallback (npm install -g pnpm)."""
        p = _prereq_for("pnpm")
        cmd = p.install_command_for(package_manager="winget")
        # No winget id → falls back to install_fallback.
        self.assertIn("npm install -g pnpm", cmd or "")


class WindowsSafetyGuardsTest(unittest.TestCase):
    def test_winget_blocked_on_vm_target(self) -> None:
        assessment = assess_command(
            "winget install --id OpenJS.NodeJS.LTS --silent",
            execution_target="vm",
        )
        self.assertTrue(assessment.blocked)
        self.assertIn("winget", assessment.reason.lower())

    def test_winget_allowed_on_local_target(self) -> None:
        assessment = assess_command(
            "winget install --id Python.Python.3.12 --silent",
            execution_target="local",
        )
        self.assertFalse(assessment.blocked)

    def test_apt_blocked_on_windows_local(self) -> None:
        """When local execution_target is Windows, apt commands must be blocked."""
        assessment = assess_command(
            "sudo apt install -y nodejs",
            execution_target="windows_local",
        )
        self.assertTrue(assessment.blocked)
        self.assertIn("apt", assessment.reason.lower())

    def test_choco_also_blocked_on_vm(self) -> None:
        assessment = assess_command(
            "choco install nodejs -y",
            execution_target="vm",
        )
        self.assertTrue(assessment.blocked)


if __name__ == "__main__":
    unittest.main()
