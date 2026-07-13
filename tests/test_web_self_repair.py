from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from duckln.plan_mode import (
    PlanRecord,
    PlanStep,
    RepoUnderstanding,
    attribute_failure,
)
from duckln.repo_bringup import _web_evidence_for_failed_step
from state.repo_catalog import RepoCatalogRecord


def _understanding():
    return RepoUnderstanding(
        repo_slug="acme/demo", repo_path=None, objective="Run demo",
        os_name="linux", arch="arm64", detected_runtimes=("node",),
        detected_files=("package.json",), repo_family="node_typescript",
        readme_excerpt="", config_excerpts={}, recent_failures=(),
        recent_skills=(), execution_target="vm", control_mode="hootlwo",
        needs_clarification=False, clarification_seed=None,
    )


def _plan():
    step = PlanStep(
        index=1, title="Run", description="", command="npm run dev",
        safety_class="S2", verification=None, rationale="",
        estimated_seconds=10, confidence=1.0,
    )
    return PlanRecord(
        plan_id="p1", objective="Run demo", context_summary="family=node_typescript",
        steps=(step,), risks=(), rollback="", estimated_seconds=10,
        created_at="2026-05-25T00:00:00Z", status="approved", repo_slug="acme/demo",
        mode_at_creation="hootlwo",
    )


class TestWebEvidenceSecondPass(unittest.TestCase):
    def test_evidence_unlocks_a_fix(self):
        plan, u = _plan(), _understanding()
        step = plan.steps[0]

        def client(*, system_prompt, user_message):
            if "web_evidence" in user_message:
                return '{"cause": "Node too old", "fix": {"command": "install node 20", "safety_class": "S3", "title": "Install Node 20"}}'
            return '{"cause": "Node too old", "fix": null}'

        first = attribute_failure(
            plan=plan, failed_step=step, stderr="Vite requires Node 20",
            stdout="", exit_code=1, understanding=u, llm_client=client,
        )
        self.assertIsNone(first.fix_step)

        second = attribute_failure(
            plan=plan, failed_step=step, stderr="Vite requires Node 20",
            stdout="", exit_code=1, understanding=u, llm_client=client,
            web_evidence="StackOverflow: upgrade Node to 20 via NodeSource",
        )
        self.assertIsNotNone(second.fix_step)
        self.assertIn("install node 20", second.fix_step.command)


class TestWebEvidenceGatedByInternet(unittest.TestCase):
    def test_returns_none_when_internet_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            # No config written → internet skill defaults to off.
            evidence = _web_evidence_for_failed_step(
                config_dir=Path(tmp),
                command="npm run dev",
                error_text="Vite requires Node 20",
                execution_target="vm",
                display=lambda _m: None,
            )
            self.assertIsNone(evidence)


if __name__ == "__main__":
    unittest.main()
