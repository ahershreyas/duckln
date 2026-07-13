"""Plan 67: failure attribution + amendment tests."""

from __future__ import annotations

import json
import unittest

from duckln.plan_mode import (
    MAX_AMENDMENTS,
    PLAN_STATUS_AMENDED,
    PlanRecord,
    PlanStep,
    RepoUnderstanding,
    amend_plan,
    attribute_failure,
    mark_status,
)


def _u() -> RepoUnderstanding:
    return RepoUnderstanding(
        repo_slug="owner/x", repo_path=None, objective="Set up x",
        os_name="Darwin", arch="arm64", detected_runtimes=("node",),
        detected_files=("package.json",), repo_family="node_typescript",
        readme_excerpt="", config_excerpts={}, recent_failures=(),
        recent_skills=(), execution_target="local", control_mode="hotl",
        needs_clarification=False, clarification_seed=None,
    )


def _plan() -> PlanRecord:
    return PlanRecord(
        plan_id="p1", objective="o", context_summary="",
        steps=(
            PlanStep(index=1, title="A", description="", command="echo a",
                     safety_class="S0", verification="exit 0", rationale="",
                     estimated_seconds=1, confidence=0.9),
            PlanStep(index=2, title="B", description="", command="false",
                     safety_class="S1", verification="exit 0", rationale="",
                     estimated_seconds=1, confidence=0.9),
        ),
        risks=(), rollback="", estimated_seconds=2,
        created_at="2026-05-19T00:00:00Z", status="approved",
        repo_slug="owner/x", mode_at_creation="hotl",
    )


class ScriptedLLM:
    def __init__(self, responses):
        self._responses = list(responses)

    def __call__(self, *, system_prompt, user_message):
        return self._responses.pop(0)


class AttributionTest(unittest.TestCase):
    def test_attribute_returns_cause_and_fix(self) -> None:
        plan = _plan()
        failed = plan.steps[1]
        llm = ScriptedLLM([
            json.dumps({
                "cause": "Step B failed because the binary is missing.",
                "fix": {
                    "title": "Install missing binary",
                    "description": "Use brew.",
                    "command": "brew install missing",
                    "safety_class": "S3",
                    "rationale": "It is required.",
                    "verification": "exit 0",
                    "estimated_seconds": 30,
                },
            }),
        ])
        result = attribute_failure(
            plan=plan, failed_step=failed, stderr="not found",
            stdout="", exit_code=127, understanding=_u(), llm_client=llm,
        )
        self.assertIn("binary is missing", result.cause)
        self.assertIsNotNone(result.fix_step)
        self.assertEqual(result.fix_step.command, "brew install missing")
        self.assertEqual(result.fix_step.origin, "amendment")

    def test_attribute_rejects_destructive_fix(self) -> None:
        plan = _plan()
        failed = plan.steps[1]
        llm = ScriptedLLM([
            json.dumps({
                "cause": "x",
                "fix": {"title": "wipe", "command": "rm -rf /", "safety_class": "S3"},
            }),
        ])
        result = attribute_failure(
            plan=plan, failed_step=failed, stderr="x", stdout="", exit_code=1,
            understanding=_u(), llm_client=llm,
        )
        self.assertIsNone(result.fix_step)

    def test_attribute_handles_malformed_response(self) -> None:
        plan = _plan()
        failed = plan.steps[1]
        llm = ScriptedLLM(["not json"])
        result = attribute_failure(
            plan=plan, failed_step=failed, stderr="x", stdout="", exit_code=1,
            understanding=_u(), llm_client=llm,
        )
        self.assertIsNone(result.fix_step)

    def test_amend_plan_inserts_before_index(self) -> None:
        plan = _plan()
        fix = PlanStep(
            index=0, title="fix", description="", command="echo fix",
            safety_class="S1", verification="exit 0", rationale="",
            estimated_seconds=1, confidence=0.9, origin="amendment",
        )
        amended = amend_plan(plan=plan, fix_step=fix, before_index=2, cause="missing dep")
        self.assertEqual(amended.status, PLAN_STATUS_AMENDED)
        self.assertEqual(amended.amendment_count, 1)
        # The fix should appear at the position previously held by step 2.
        self.assertEqual(amended.steps[1].command, "echo fix")
        self.assertEqual(amended.steps[2].command, "false")
        self.assertEqual(amended.steps[0].command, "echo a")

    def test_max_amendments_constant_is_three(self) -> None:
        self.assertEqual(MAX_AMENDMENTS, 3)


if __name__ == "__main__":
    unittest.main()
