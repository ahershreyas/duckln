"""Tests: budget guard fires before _attempt_runtime_prerequisite_install (Plan 56)."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from duckln.config import AppConfig, ConfigPaths, Provider
from duckln.main import _run_runtime_repair_workflow
from duckln.modes import ControlMode
from state.repo_catalog import RepoCatalogRecord


@dataclass
class _FakeWorkflow:
    active_runtime_execution_target: str | None = "local"
    active_runtime_cwd: str | None = None
    active_runtime_command: str | None = "npm run dev"
    active_runtime_command_kind: str | None = "run"


def _repo() -> RepoCatalogRecord:
    return RepoCatalogRecord(
        name="BudgetTestRepo",
        repo_url="https://example.com/BudgetTestRepo",
        stars=1,
        description="Test repo",
        category="web",
        framework="Node",
        last_updated="2026-05-01",
    )


def _config() -> AppConfig:
    return AppConfig(
        provider=Provider.OPENAI,
        model="gpt-4o",
        mode=ControlMode.HOTL,
        api_key="x",
        base_url="https://api.openai.com/v1",
    )


class BudgetBeforePrereqInstallTest(unittest.TestCase):
    def test_budget_exceeded_skips_prerequisite_install(self) -> None:
        """When the per-call budget is exhausted, _attempt_runtime_prerequisite_install is not called."""
        install_was_called = []

        def fake_install(**_kwargs: object) -> str:
            install_was_called.append(True)
            return "installed"

        captured: list[str] = []

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            (config_dir / "config.json").write_text("{}", encoding="utf-8")
            paths = ConfigPaths(config_dir=config_dir, config_file=config_dir / "config.json")

            # Force budget to appear exceeded immediately.
            import duckln.main as _main_mod
            big_elapsed = _main_mod._RUNTIME_REPAIR_BUDGET_SECONDS + 1

            with patch("duckln.main.read_config_snapshot", return_value={"execution_target": "local"}), \
                 patch("duckln.main._attempt_runtime_prerequisite_install", side_effect=fake_install), \
                 patch("duckln.main.time") as mock_time:
                # started_at = 0; subsequent calls return budget+1 so _budget_exceeded() fires.
                mock_time.monotonic.side_effect = [0.0, big_elapsed, big_elapsed, big_elapsed]

                _run_runtime_repair_workflow(
                    repo=_repo(),
                    current=_config(),
                    paths=paths,
                    display_output=captured.append,
                    approve_prompt=lambda _: False,
                    terminal_interface=None,
                    run_summary="node: command not found",
                    runtime_command_override="npm run dev",
                    workflow=_FakeWorkflow(),
                )

        self.assertFalse(
            install_was_called,
            "_attempt_runtime_prerequisite_install must not be called when budget is exceeded",
        )

    def test_budget_message_emitted_when_skipping_install(self) -> None:
        """The budget-exhaustion message is emitted when the prereq install is skipped."""
        captured: list[str] = []

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            (config_dir / "config.json").write_text("{}", encoding="utf-8")
            paths = ConfigPaths(config_dir=config_dir, config_file=config_dir / "config.json")

            import duckln.main as _main_mod
            big_elapsed = _main_mod._RUNTIME_REPAIR_BUDGET_SECONDS + 5

            with patch("duckln.main.read_config_snapshot", return_value={"execution_target": "local"}), \
                 patch("duckln.main._attempt_runtime_prerequisite_install", return_value="not_called"), \
                 patch("duckln.main.time") as mock_time:
                mock_time.monotonic.side_effect = [0.0, big_elapsed, big_elapsed, big_elapsed]

                _run_runtime_repair_workflow(
                    repo=_repo(),
                    current=_config(),
                    paths=paths,
                    display_output=captured.append,
                    approve_prompt=lambda _: False,
                    terminal_interface=None,
                    run_summary="node: command not found",
                    runtime_command_override="npm run dev",
                    workflow=_FakeWorkflow(),
                )

        budget_msgs = [m for m in captured if "skipping prerequisite install" in m or "escalating" in m.lower()]
        self.assertTrue(
            budget_msgs,
            f"Expected a budget-skip message; got: {captured!r}",
        )


if __name__ == "__main__":
    unittest.main()
