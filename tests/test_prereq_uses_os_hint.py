"""Tests: _prerequisite_recovery_plan prefers the OS-volunteered install hint."""

from __future__ import annotations

import unittest
from pathlib import Path

from duckln.repo_bringup import (
    DebugRecoveryAssessment,
    FailureType,
    RecoveryDecision,
    RepoBringUpPlan,
    RepoBringUpStep,
    RepoFamily,
    _prerequisite_recovery_plan,
)
from state.repo_catalog import RepoCatalogRecord


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


def _failing_step() -> RepoBringUpStep:
    return RepoBringUpStep(
        purpose="Verify README prerequisite: Node.js and npm",
        command="node --version",
        verification_command=None,
        source="inferred",
    )


def _node_failed_plan() -> RepoBringUpPlan:
    return RepoBringUpPlan(
        repo=_repo(),
        project_dir=Path("/tmp/jhm"),
        detected_files=("package.json",),
        steps=(_failing_step(),),
        summary="Setup plan for JustHireMe.",
        repo_family=RepoFamily.NODE_TYPESCRIPT,
        specialist_name="node_typescript",
        playbook_path=None,
        execution_target="local",
    )


def _node_failure_assessment() -> DebugRecoveryAssessment:
    return DebugRecoveryAssessment(
        failure_type=FailureType.NODE_NPM_MISMATCH,
        decision=RecoveryDecision.REQUEST_MISSING_PREREQUISITE,
        summary="Install Node.js to clear the prerequisite.",
    )


class PrereqUsesOsHintTest(unittest.TestCase):
    def test_apt_hint_replaces_hardcoded_nodesource_script(self) -> None:
        os_error = (
            "Command 'node' not found, but can be installed with:\n"
            "  sudo apt install nodejs"
        )
        new_plan = _prerequisite_recovery_plan(
            plan=_node_failed_plan(),
            failed_step=_failing_step(),
            failed_command="node --version",
            recovery=_node_failure_assessment(),
            raw_error_output=os_error,
            display=lambda _: None,
        )
        self.assertIsNotNone(new_plan)
        first_step = new_plan.steps[0]
        self.assertEqual(first_step.command, "sudo apt install -y nodejs")
        self.assertEqual(first_step.source, "recovery-prerequisite-os-hint")
        # The hardcoded NodeSource curl-pipe-to-bash must NOT appear.
        self.assertNotIn("nodesource.com", first_step.command)
        self.assertNotIn("curl", first_step.command)

    def test_no_hint_falls_back_to_hardcoded_nodesource(self) -> None:
        new_plan = _prerequisite_recovery_plan(
            plan=_node_failed_plan(),
            failed_step=_failing_step(),
            failed_command="node --version",
            recovery=_node_failure_assessment(),
            raw_error_output="something else broke",
            display=lambda _: None,
        )
        self.assertIsNotNone(new_plan)
        first_step = new_plan.steps[0]
        # When no hint is parseable, the hardcoded Linux install script wins —
        # it contains the NodeSource setup script on Linux hosts.
        self.assertEqual(first_step.source, "recovery-prerequisite")

    def test_display_announces_the_chosen_os_hint(self) -> None:
        os_error = (
            "Command 'node' not found, but can be installed with:\n"
            "  sudo apt install nodejs"
        )
        displayed: list[str] = []
        _prerequisite_recovery_plan(
            plan=_node_failed_plan(),
            failed_step=_failing_step(),
            failed_command="node --version",
            recovery=_node_failure_assessment(),
            raw_error_output=os_error,
            display=displayed.append,
        )
        # Surfaced to the user as a one-line trace.
        self.assertTrue(
            any("OS install hint" in line and "sudo apt install -y nodejs" in line for line in displayed),
            f"Expected the OS install hint trace, got: {displayed}",
        )


if __name__ == "__main__":
    unittest.main()
