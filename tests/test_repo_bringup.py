"""Tests for repo bring-up foundation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from duckln.config import resolve_config_paths
from duckln.modes import ControlMode
from duckln.repo_bringup import bring_up_selected_repo, infer_repo_setup_plan, resolve_managed_project_dir
from duckln.shell import CommandResult
from state.repo_catalog import RepoCatalogRecord


class FakeRunner:
    def __init__(self, project_dir: Path, *, create_requirements: bool = True) -> None:
        self.project_dir = project_dir
        self.create_requirements = create_requirements
        self.commands: list[tuple[str, str | None]] = []

    def run(self, command: str, *, timeout_seconds: float = 30.0, cwd: str | None = None, env=None) -> CommandResult:
        self.commands.append((command, cwd))
        if command.startswith("git clone "):
            self.project_dir.mkdir(parents=True, exist_ok=True)
            if self.create_requirements:
                (self.project_dir / "requirements.txt").write_text("requests\n", encoding="utf-8")
        elif command == "python -m venv .venv":
            venv_python = self.project_dir / ".venv" / "bin"
            venv_python.mkdir(parents=True, exist_ok=True)
            (venv_python / "python").write_text("", encoding="utf-8")
        return CommandResult(
            command=command,
            exit_code=0,
            stdout="",
            stderr="",
            timed_out=False,
            duration_seconds=0.01,
        )


class RepoBringUpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = RepoCatalogRecord(
            name="alpha",
            repo_url="https://example.com/alpha",
            stars=50,
            description="Alpha repository",
            category="LLM",
            framework="Python",
            last_updated="2026-03-22",
        )

    def test_infer_repo_setup_plan_prefers_requirements_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "README.md").write_text("# Alpha\n", encoding="utf-8")
            (project_dir / "requirements.txt").write_text("requests\n", encoding="utf-8")

            plan = infer_repo_setup_plan(self.repo, project_dir)

            self.assertEqual(("README.md", "requirements.txt"), plan.detected_files)
            self.assertEqual("python -m venv .venv", plan.steps[0].command)
            self.assertEqual(".venv/bin/python -m pip install -r requirements.txt", plan.steps[1].command)

    def test_bring_up_selected_repo_in_hotl_executes_clone_and_safe_setup_after_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            project_dir = resolve_managed_project_dir(paths.config_dir, self.repo)
            runner = FakeRunner(project_dir)
            displayed: list[str] = []

            result = bring_up_selected_repo(
                self.repo,
                ControlMode.HOTL,
                paths,
                runner=runner,
                approve=lambda prompt: True,
                display=displayed.append,
            )

            self.assertEqual(
                (
                    f"git clone --depth 1 https://example.com/alpha {project_dir}",
                    "python -m venv .venv",
                    ".venv/bin/python -m pip install -r requirements.txt",
                ),
                result.executed_commands,
            )
            self.assertTrue(result.verification_passed)
            self.assertTrue(any("Repo bring-up foundation completed." == message for message in displayed))

    def test_bring_up_selected_repo_in_hootlwo_waits_when_clone_needs_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            project_dir = resolve_managed_project_dir(paths.config_dir, self.repo)
            runner = FakeRunner(project_dir)
            displayed: list[str] = []

            result = bring_up_selected_repo(
                self.repo,
                ControlMode.HOOTLWO,
                paths,
                runner=runner,
                approve=lambda prompt: False,
                display=displayed.append,
            )

            self.assertEqual((), result.executed_commands)
            self.assertFalse(project_dir.exists())
            self.assertTrue(any("suggested only" in message or "Approve" in message for message in displayed))


if __name__ == "__main__":
    unittest.main()
