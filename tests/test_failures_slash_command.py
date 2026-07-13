"""Tests: /failures slash command (Plan 61 Fix C)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from duckln.config import AppConfig, ConfigPaths, Provider, save_app_config
from duckln.main import _handle_failures_command
from duckln.modes import ControlMode
from state.access import write_failure_memory_state


def _make_config(failure_window_hours: float = 24.0) -> AppConfig:
    return AppConfig(
        provider=Provider.OPENAI,
        model="gpt-4o",
        mode=ControlMode.HOTL,
        api_key="x",
        base_url="https://api.openai.com/v1",
        failure_window_hours=failure_window_hours,
    )


def _make_paths(config_dir: Path) -> ConfigPaths:
    (config_dir / "config.json").write_text("{}", encoding="utf-8")
    return ConfigPaths(config_dir=config_dir, config_file=config_dir / "config.json")


class FailuresSlashCommandTest(unittest.TestCase):
    def test_show_subcommand_displays_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            paths = _make_paths(config_dir)
            write_failure_memory_state(
                config_dir,
                repo_slug="repo_a",
                command="brew install node",
                execution_target="vm",
                exit_code=127,
                stderr_fingerprint="brew: command not found",
            )
            captured: list[str] = []
            _handle_failures_command(
                command="/failures show",
                current=_make_config(),
                paths=paths,
                display=captured.append,
                approve=lambda _: True,
            )
            joined = "\n".join(captured)
            self.assertIn("brew install node", joined)
            self.assertIn("vm", joined)

    def test_show_when_empty_reports_so(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            paths = _make_paths(config_dir)
            captured: list[str] = []
            _handle_failures_command(
                command="/failures show",
                current=_make_config(),
                paths=paths,
                display=captured.append,
                approve=lambda _: True,
            )
            self.assertTrue(any("no" in m.lower() and "failure" in m.lower() for m in captured))

    def test_clear_subcommand_removes_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            paths = _make_paths(config_dir)
            write_failure_memory_state(
                config_dir,
                repo_slug="repo_a",
                command="brew install node",
                execution_target="vm",
                exit_code=127,
                stderr_fingerprint="brew: command not found",
            )
            captured: list[str] = []
            _handle_failures_command(
                command="/failures clear",
                current=_make_config(),
                paths=paths,
                display=captured.append,
                approve=lambda _: True,  # auto-approve
            )
            from agent.memory import AGENT_MEMORY_DIR_NAME, FAILURES_DIR_NAME
            fdir = config_dir / AGENT_MEMORY_DIR_NAME / FAILURES_DIR_NAME
            md_files = [fp for fp in fdir.iterdir() if fp.suffix == ".md"] if fdir.exists() else []
            self.assertEqual(md_files, [])
            self.assertTrue(any("cleared" in m.lower() for m in captured))

    def test_clear_respects_user_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            paths = _make_paths(config_dir)
            write_failure_memory_state(
                config_dir,
                repo_slug="repo_a",
                command="brew install node",
                execution_target="vm",
                exit_code=127,
                stderr_fingerprint="brew: command not found",
            )
            captured: list[str] = []
            _handle_failures_command(
                command="/failures clear",
                current=_make_config(),
                paths=paths,
                display=captured.append,
                approve=lambda _: False,  # user rejects
            )
            from agent.memory import AGENT_MEMORY_DIR_NAME, FAILURES_DIR_NAME
            fdir = config_dir / AGENT_MEMORY_DIR_NAME / FAILURES_DIR_NAME
            md_files = [fp for fp in fdir.iterdir() if fp.suffix == ".md"]
            self.assertGreater(len(md_files), 0)
            self.assertTrue(any("cancel" in m.lower() for m in captured))

    def test_window_subcommand_updates_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            paths = _make_paths(config_dir)
            save_app_config(_make_config(), paths)
            captured: list[str] = []
            updated = _handle_failures_command(
                command="/failures window 6",
                current=_make_config(),
                paths=paths,
                display=captured.append,
                approve=lambda _: True,
            )
            self.assertAlmostEqual(updated.failure_window_hours, 6.0)
            self.assertTrue(any("6.0" in m for m in captured))

    def test_window_rejects_out_of_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            paths = _make_paths(config_dir)
            captured: list[str] = []
            updated = _handle_failures_command(
                command="/failures window 999",
                current=_make_config(),
                paths=paths,
                display=captured.append,
                approve=lambda _: True,
            )
            # Config NOT updated.
            self.assertEqual(updated.failure_window_hours, 24.0)
            self.assertTrue(any("between" in m.lower() for m in captured))


if __name__ == "__main__":
    unittest.main()
