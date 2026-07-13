"""Tests: stale active_runtime_execution_target is overridden by the live session target."""

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
    active_runtime_execution_target: str | None = None
    active_runtime_cwd: str | None = None
    active_runtime_command: str | None = None
    active_runtime_command_kind: str | None = None


def _repo() -> RepoCatalogRecord:
    return RepoCatalogRecord(
        name="JustHireMe",
        repo_url="https://example.com/JustHireMe",
        stars=10,
        description="Node app",
        category="web",
        framework="TypeScript",
        last_updated="2026-05-15",
    )


def _config() -> AppConfig:
    return AppConfig(
        provider=Provider.OPENAI,
        model="gpt-4o",
        mode=ControlMode.HOTL,
        api_key="x",
        base_url="https://api.openai.com/v1",
    )


class StaleRuntimeTargetTest(unittest.TestCase):
    def test_stale_gcp_target_overridden_by_local_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            (config_dir / "config.json").write_text("{}", encoding="utf-8")
            paths = ConfigPaths(config_dir=config_dir, config_file=config_dir / "config.json")

            displayed: list[str] = []
            # Workflow state says gcp; session has EXPLICITLY persisted "local".
            # The override notice must mention both, and the live target wins.
            with patch("duckln.main.read_config_snapshot", return_value={"execution_target": "local"}):
                # Force budget exceeded quickly so the workflow returns after the
                # target reconciliation; we only need to inspect the displayed
                # messages here.
                time_values = iter([0.0, 9999.0, 9999.0, 9999.0, 9999.0])
                with patch("duckln.main.time.monotonic", side_effect=lambda: next(time_values, 9999.0)):
                    _run_runtime_repair_workflow(
                        repo=_repo(),
                        current=_config(),
                        paths=paths,
                        display_output=displayed.append,
                        approve_prompt=lambda _: True,
                        terminal_interface=None,
                        run_summary="something broke",
                        runtime_command_override="npm run dev",
                        workflow=_FakeWorkflow(active_runtime_execution_target="gcp"),
                    )
            joined = " ".join(displayed)
            self.assertIn("stale runtime target (gcp)", joined)
            self.assertIn("current session target (local)", joined)
            # The "Google Cloud Linux VM" os_hint must NOT have leaked into any
            # search query (no displayed line should contain it).
            for line in displayed:
                self.assertNotIn("Google Cloud Linux VM", line)


if __name__ == "__main__":
    unittest.main()
