"""Tests: duplicate-failure detection stops the bring-up loop after 2 identical failures."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from duckln.config import ConfigPaths
from duckln.modes import ControlMode
from duckln.repo_bringup import (
    RepoBringUpPlan,
    RepoBringUpStep,
    RepoFamily,
    _execute_plan_with_bounded_recovery,
)
from duckln.shell import CommandResult
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


def _plan(project_dir: Path) -> RepoBringUpPlan:
    return RepoBringUpPlan(
        repo=_repo(),
        project_dir=project_dir,
        detected_files=("package.json",),
        steps=(
            RepoBringUpStep(
                purpose="Verify Node.js",
                command="node --version",
                source="inferred",
            ),
        ),
        summary="Verify Node.js for ExampleRepo.",
        repo_family=RepoFamily.NODE_TYPESCRIPT,
        specialist_name="node_typescript",
        execution_target="local",
    )


def _failing_command_result() -> CommandResult:
    return CommandResult(
        command="node --version",
        exit_code=127,
        stdout="",
        stderr="bash: node: command not found",
        duration_seconds=0.05,
        timed_out=False,
    )


class NoRepeatLoopTest(unittest.TestCase):
    def test_second_identical_failure_stops_the_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            paths = ConfigPaths(
                config_dir=project_dir,
                config_file=project_dir / "config.json",
            )
            runner = MagicMock()
            runner.run.return_value = _failing_command_result()

            supervisor = MagicMock()
            # The recovery decision keeps prescribing a retry that produces the
            # same failure — exactly the loop we want to shortcut.
            from duckln.repo_bringup import (
                DebugRecoveryAssessment,
                FailureType,
                RecoveryDecision,
            )

            def _assess(*_args, **_kwargs) -> DebugRecoveryAssessment:
                return DebugRecoveryAssessment(
                    failure_type=FailureType.NODE_NPM_MISMATCH,
                    decision=RecoveryDecision.RETRY_SAME_SPECIALIST,
                    summary="Retry the same probe.",
                )

            supervisor.assess_failed_bringup.side_effect = _assess
            supervisor._context_service = MagicMock()
            supervisor._select_specialist = MagicMock()

            displayed: list[str] = []
            result = _execute_plan_with_bounded_recovery(
                plan=_plan(project_dir),
                current_mode=ControlMode.HOOTLWO,
                paths=paths,
                runner=runner,
                supervisor=supervisor,
                system_probe=None,
                approve=lambda _prompt: True,
                display=displayed.append,
                executed_commands=[],
                verification_passed=False,
                started_at=0.0,
                config_dir=project_dir,
            )
            self.assertIsNotNone(result)
            self.assertFalse(result.verification_passed)
            # Message must say Duckln tried the command twice and got the same error.
            joined = " ".join(displayed) + " " + result.message
            self.assertIn("tried", joined.lower())
            self.assertIn("twice", joined.lower())
            self.assertIn("node --version", joined)
            # Runner ran the same command exactly twice (1 initial + 1 retry) — NOT 5+.
            self.assertEqual(runner.run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
