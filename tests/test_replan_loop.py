from __future__ import annotations

import unittest

from duckln.plan_mode import CriticVerdict, PlanRecord, PlanStep, _deterministic_supervisor_review
from duckln.repo_bringup import _repair_plan_for_revise


def _plan(steps):
    return PlanRecord(
        plan_id="p1", objective="o", context_summary="family=node_typescript",
        steps=tuple(steps), risks=(), rollback="", estimated_seconds=30,
        created_at="2026-05-25T00:00:00Z", status="pending", repo_slug="a/b",
        mode_at_creation="hootlwo",
    )


def _step(index, command, *, safety="S2", verification=None, target=""):
    return PlanStep(
        index=index, title=f"Step {index}", description="", command=command,
        safety_class=safety, verification=verification, rationale="",
        estimated_seconds=10, target=target,
    )


class TestRepairPlanForRevise(unittest.TestCase):
    def test_fills_missing_target_and_verify_on_mutating_steps(self):
        plan = _plan([
            _step(1, "git clone https://github.com/a/b.git", safety="S1", verification="test -d .git", target="vm"),
            _step(2, "npm install", safety="S2", verification=None, target=""),
            _step(3, "npm run dev", safety="S2", verification="port", target="vm"),
        ])
        verdict = CriticVerdict(verdict="revise", reason="missing verify/target")
        repaired = _repair_plan_for_revise(plan, verdict, execution_target="vm")
        self.assertIsNotNone(repaired)
        self.assertIsNot(repaired, plan)
        install = repaired.steps[1]
        self.assertEqual(install.target, "vm")
        self.assertTrue(install.verification)
        # The repaired plan now passes the deterministic review.
        from tests.test_supervisor_review import _understanding  # reuse builder
        self.assertEqual(_deterministic_supervisor_review(repaired, _understanding()).verdict, "approve")

    def test_returns_same_plan_when_nothing_to_repair(self):
        plan = _plan([
            _step(1, "git clone https://github.com/a/b.git", safety="S1", verification="test -d .git", target="vm"),
            _step(2, "npm install", safety="S2", verification="ok", target="vm"),
            _step(3, "npm run dev", safety="S2", verification="port", target="vm"),
        ])
        verdict = CriticVerdict(verdict="revise", reason="something unfixable")
        repaired = _repair_plan_for_revise(plan, verdict, execution_target="vm")
        self.assertIs(repaired, plan)  # caller stops the loop


if __name__ == "__main__":
    unittest.main()
