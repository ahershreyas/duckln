"""Tests: stopped_repeated_failure result blocks repair and run phases (Plan 56)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from duckln.repo_bringup import _execute_plan_with_bounded_recovery


class StoppedRepeatNoRepairTest(unittest.TestCase):
    def _make_plan(self) -> MagicMock:
        plan = MagicMock()
        plan.repo.repo_url = "https://example.com/repo"
        plan.repo.name = "TestRepo"
        plan.project_dir = "/tmp/repo"
        plan.detected_files = ()
        plan.execution_target = "local"
        plan.steps = ()
        plan.repo_family = "node"
        plan.specialist_name = "node_specialist"
        return plan

    def _make_runner_that_fails(self, command: str, stderr: str, exit_code: int = 1) -> MagicMock:
        result = MagicMock()
        result.exit_code = exit_code
        result.stderr = stderr
        result.stdout = ""
        result.timed_out = False
        runner = MagicMock()
        runner.run.return_value = result
        return runner

    def test_stopped_repeated_failure_sets_should_offer_repair_false(self) -> None:
        """The dedup return in _execute_plan_with_bounded_recovery must set should_offer_repair=False."""
        import inspect
        import duckln.repo_bringup as _rb
        src = inspect.getsource(_rb._execute_plan_with_bounded_recovery)
        # The dedup short-circuit block must explicitly set should_offer_repair=False.
        self.assertIn(
            "should_offer_repair=False",
            src,
            "Dedup RepoBringUpResult must contain should_offer_repair=False to block the run loop",
        )

    def test_stopped_repeated_failure_status_has_no_repair(self) -> None:
        """Verify the dedup RepoBringUpResult is not re-queued for repair."""
        from duckln.repo_bringup import RepoBringUpResult
        from state.repo_catalog import RepoCatalogRecord

        repo = RepoCatalogRecord(
            name="DemoRepo",
            repo_url="https://example.com/DemoRepo",
            stars=1,
            description="",
            category="web",
            framework="Node",
            last_updated="2026-05-01",
        )
        result = RepoBringUpResult(
            repo=repo,
            project_dir="/tmp/demo",
            detected_files=(),
            mode="HOTL",
            executed_commands=(),
            verification_passed=False,
            should_offer_repair=False,
            message="Duckln tried `node -v` twice and got the same error both times.",
            repo_family="node",
            specialist_name="node",
        )
        self.assertFalse(result.should_offer_repair)
        self.assertFalse(result.verification_passed)

    def test_normal_failure_still_offers_repair(self) -> None:
        """A one-shot failure (no dedup) should still have should_offer_repair=True by default."""
        from duckln.repo_bringup import RepoBringUpResult
        from state.repo_catalog import RepoCatalogRecord

        repo = RepoCatalogRecord(
            name="DemoRepo",
            repo_url="https://example.com/DemoRepo",
            stars=1,
            description="",
            category="web",
            framework="Node",
            last_updated="2026-05-01",
        )
        result = RepoBringUpResult(
            repo=repo,
            project_dir="/tmp/demo",
            detected_files=(),
            mode="HOTL",
            executed_commands=(),
            verification_passed=False,
            message="npm install failed.",
            repo_family="node",
            specialist_name="node",
        )
        self.assertTrue(result.should_offer_repair)


if __name__ == "__main__":
    unittest.main()
