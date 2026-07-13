"""Tests: runtime repair approval prompt names the exact command (Plan 55)."""

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
        name="DemoRepo",
        repo_url="https://example.com/DemoRepo",
        stars=5,
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


class RuntimeApprovalPromptTest(unittest.TestCase):
    def test_top_level_approval_names_specific_action(self) -> None:
        captured_prompts: list[str] = []

        def fake_approve(prompt: str) -> bool:
            captured_prompts.append(prompt)
            return False

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            (config_dir / "config.json").write_text("{}", encoding="utf-8")
            paths = ConfigPaths(config_dir=config_dir, config_file=config_dir / "config.json")

            with patch("duckln.main.read_config_snapshot", return_value={"execution_target": "local"}):
                _run_runtime_repair_workflow(
                    repo=_repo(),
                    current=_config(),
                    paths=paths,
                    display_output=lambda _msg: None,
                    approve_prompt=fake_approve,
                    terminal_interface=None,
                    run_summary="npm err! missing script: dev",
                    runtime_command_override="npm run dev",
                    workflow=_FakeWorkflow(),
                )

        actionable = [p for p in captured_prompts if "Duckln wants to run" in p]
        self.assertTrue(
            actionable,
            f"Expected an actionable approval prompt; got: {captured_prompts!r}",
        )
        first = actionable[0]
        self.assertIn("Approve repair of DemoRepo", first)
        self.assertIn("Reason:", first)
        for prompt in captured_prompts:
            self.assertNotIn(
                "Do you want Duckln to solve the DemoRepo issue?",
                prompt,
                "Generic prompt must be replaced with the actionable Plan 55 variant.",
            )


if __name__ == "__main__":
    unittest.main()
