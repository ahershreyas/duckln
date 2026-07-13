from __future__ import annotations

import unittest

from duckln.plan_mode import (
    PlanRecord,
    PlanStep,
    RepoUnderstanding,
    attribute_failure,
)


def _understanding():
    return RepoUnderstanding(
        repo_slug="a/b", repo_path=None, objective="o", os_name="linux", arch="arm64",
        detected_runtimes=("node",), detected_files=("package.json",),
        repo_family="node_typescript", readme_excerpt="", config_excerpts={},
        recent_failures=(), recent_skills=(), execution_target="vm",
        control_mode="hootlwo", needs_clarification=False, clarification_seed=None,
    )


def _failed_step():
    return PlanStep(
        index=3, title="Run", description="", command="npm run dev",
        safety_class="S2", verification="port", rationale="", estimated_seconds=10,
        target="vm", cwd="/home/ubuntu/.duckln/projects/demo",
    )


def _plan():
    return PlanRecord(
        plan_id="p1", objective="o", context_summary="family=node_typescript",
        steps=(_failed_step(),), risks=(), rollback="", estimated_seconds=10,
        created_at="2026-05-25T00:00:00Z", status="approved", repo_slug="a/b",
        mode_at_creation="hootlwo",
    )


class TestAmendmentEvidence(unittest.TestCase):
    def test_fix_step_carries_target_cwd_and_verification(self):
        def client(*, system_prompt, user_message):
            return ('{"cause": "node too old", "fix": {"command": "sudo apt-get install -y nodejs", '
                    '"safety_class": "S3", "title": "Install Node 20"}}')

        result = attribute_failure(
            plan=_plan(), failed_step=_failed_step(), stderr="Vite requires Node 20",
            stdout="", exit_code=1, understanding=_understanding(), llm_client=client,
        )
        self.assertIsNotNone(result.fix_step)
        self.assertEqual(result.fix_step.target, "vm")
        self.assertEqual(result.fix_step.cwd, "/home/ubuntu/.duckln/projects/demo")
        # A mutating fix with no LLM-provided verification gets a default one,
        # so it survives the supervisor re-review (Plan 78 Fix C/E).
        self.assertTrue(result.fix_step.verification)
        self.assertEqual(result.fix_step.origin, "amendment")

    def test_failed_step_cwd_is_in_payload(self):
        captured = {}

        def client(*, system_prompt, user_message):
            captured["msg"] = user_message
            return '{"cause": "x", "fix": null}'

        attribute_failure(
            plan=_plan(), failed_step=_failed_step(), stderr="err",
            stdout="", exit_code=1, understanding=_understanding(), llm_client=client,
        )
        self.assertIn("/home/ubuntu/.duckln/projects/demo", captured["msg"])


if __name__ == "__main__":
    unittest.main()
