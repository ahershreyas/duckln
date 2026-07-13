"""Tests: _run_runtime_repair_workflow respects a wall-clock budget."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from duckln.config import AppConfig, ConfigPaths, Provider
from duckln.main import _run_runtime_repair_workflow
from duckln.modes import ControlMode
from state.repo_catalog import RepoCatalogRecord


def _repo() -> RepoCatalogRecord:
    return RepoCatalogRecord(
        name="ExampleRepo",
        repo_url="https://example.com/ExampleRepo",
        stars=5,
        description="Test",
        category="web",
        framework="TypeScript",
        last_updated="2026-05-15",
    )


def _config(config_dir: Path) -> AppConfig:
    return AppConfig(
        provider=Provider.OPENAI,
        model="gpt-4o",
        mode=ControlMode.HOTL,
        api_key="x",
        base_url="https://api.openai.com/v1",
    )


class RuntimeRepairBudgetTest(unittest.TestCase):
    def test_budget_exceeded_returns_with_timeout_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            (config_dir / "config.json").write_text("{}", encoding="utf-8")
            paths = ConfigPaths(config_dir=config_dir, config_file=config_dir / "config.json")

            displayed: list[str] = []
            # Patch time.monotonic so the function sees the budget already exceeded
            # immediately after the workflow_started_at sample. This is a clean way
            # to verify the timeout path without sleeping.
            time_values = iter([0.0, 1000.0, 1000.0, 1000.0, 1000.0, 1000.0, 1000.0])
            with patch("duckln.main.time.monotonic", side_effect=lambda: next(time_values, 1000.0)):
                _run_runtime_repair_workflow(
                    repo=_repo(),
                    current=_config(config_dir),
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=lambda _: True,
                    terminal_interface=None,
                    run_summary="some real runtime failure stderr",
                    runtime_command_override="npm run dev",
                    workflow=None,
                )
            joined = " ".join(displayed)
            self.assertTrue(
                "escalating to the user" in joined and "runtime repair" in joined,
                f"Expected timeout escalation message, got: {displayed}",
            )


if __name__ == "__main__":
    unittest.main()
