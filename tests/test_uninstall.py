"""Tests for the bounded Duckln uninstall flow."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from duckln.config import resolve_config_paths
from duckln.modes import ControlMode
from duckln.uninstall import InstallMethod, UninstallTier, build_package_uninstall_command, run_uninstall_flow


class UninstallFlowTest(unittest.TestCase):
    def test_build_package_uninstall_command_is_os_and_install_method_aware(self) -> None:
        with patch("duckln.uninstall.platform.system", return_value="Windows"):
            self.assertEqual(
                "py -m pip uninstall -y duckln",
                build_package_uninstall_command(InstallMethod.PIP),
            )
        self.assertEqual("pipx uninstall duckln", build_package_uninstall_command(InstallMethod.PIPX))

    def test_hitl_uninstall_suggests_command_and_preserves_data_for_app_only_tier(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            paths.config_dir.mkdir(parents=True, exist_ok=True)
            (paths.config_dir / "memory").mkdir(parents=True, exist_ok=True)
            (paths.config_dir / "config.json").write_text("{}", encoding="utf-8")
            displayed: list[str] = []
            selections = iter(("Duckln application only (keep memory and sessions)", "Yes"))

            with patch("duckln.uninstall.detect_install_method", return_value=InstallMethod.PIPX):
                result = run_uninstall_flow(
                    paths,
                    mode=ControlMode.HITL,
                    select=lambda prompt, choices: next(selections),
                    display=displayed.append,
                )

            self.assertTrue(result.completed)
            self.assertEqual(UninstallTier.APP_ONLY, result.tier)
            self.assertEqual((), result.removed_paths)
            self.assertTrue((paths.config_dir / "memory").exists())
            self.assertTrue((paths.config_dir / "config.json").exists())
            self.assertIn("Run this uninstall command manually: pipx uninstall duckln", displayed)
            self.assertIn("Duckln package command was suggested. Memory and config were kept.", displayed)

    def test_hotl_uninstall_removes_memory_and_state_after_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            memory_dir = paths.config_dir / "memory"
            state_dir = paths.config_dir / "state"
            memory_dir.mkdir(parents=True, exist_ok=True)
            state_dir.mkdir(parents=True, exist_ok=True)
            (memory_dir / "session.md").write_text("summary", encoding="utf-8")
            (state_dir / "duckln-state.sqlite3").write_text("db", encoding="utf-8")
            selections = iter(("Duckln + all memory and session history", "Yes"))

            with (
                patch("duckln.uninstall.detect_install_method", return_value=InstallMethod.PIPX),
                patch("duckln.uninstall.subprocess.run") as run_command,
            ):
                run_command.return_value.returncode = 0
                result = run_uninstall_flow(
                    paths,
                    mode=ControlMode.HOTL,
                    select=lambda prompt, choices: next(selections),
                    approve=lambda prompt: True,
                    display=lambda message: None,
                )

            self.assertTrue(result.completed)
            self.assertFalse(memory_dir.exists())
            self.assertFalse(state_dir.exists())
            run_command.assert_called_once()

    def test_cancel_returns_without_removing_anything(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            paths.config_dir.mkdir(parents=True, exist_ok=True)
            (paths.config_dir / "config.json").write_text("{}", encoding="utf-8")
            displayed: list[str] = []

            result = run_uninstall_flow(
                paths,
                mode=ControlMode.HITL,
                select=lambda prompt, choices: "Cancel",
                display=displayed.append,
            )

            self.assertFalse(result.completed)
            self.assertTrue((paths.config_dir / "config.json").exists())
            self.assertEqual("Uninstall cancelled.", displayed[-1])


if __name__ == "__main__":
    unittest.main()
