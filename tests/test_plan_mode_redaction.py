"""Plan 72 Phase 5 — redaction before LLM payloads."""

from __future__ import annotations

import json
import unittest

from duckln.plan_mode import (
    PlanRecord,
    PlanStep,
    RepoUnderstanding,
    attribute_failure,
    critic_review,
)


SECRETS = "sk-ABCDEFGHIJKLMNOdummy0123456789TESTtoken token=ghp_abcdefghijklmnopqrstuvwxyz0123456789"


class _CapturingLLM:
    def __init__(self):
        self.seen = ""

    def __call__(self, *, system_prompt, user_message):
        self.seen = user_message
        return json.dumps({"verdict": "approve", "reason": "ok"})


def _understanding():
    return RepoUnderstanding(
        repo_slug="o/x", repo_path=None, objective="Set up x", os_name="Darwin", arch="arm64",
        detected_runtimes=("node",), detected_files=("package.json",), repo_family="node_typescript",
        readme_excerpt=f"see {SECRETS}", config_excerpts={"package.json": SECRETS},
        recent_failures=(), recent_skills=(), execution_target="local", control_mode="hotl",
        needs_clarification=False, clarification_seed=None,
    )


def _plan():
    return PlanRecord(
        plan_id="p1", objective="Set up x", context_summary="", steps=(
            PlanStep(index=1, title="install", description="", command="npm ci", safety_class="S2",
                     verification="exit 0", rationale="", estimated_seconds=5, confidence=1.0),
        ), risks=(), rollback="", estimated_seconds=5, created_at="2026-05-22T00:00:00Z",
        status="pending", repo_slug="o/x", mode_at_creation="hotl",
    )


class RedactionTest(unittest.TestCase):
    def test_critic_payload_is_redacted(self) -> None:
        llm = _CapturingLLM()
        critic_review(plan=_plan(), understanding=_understanding(), llm_client=llm)
        self.assertNotIn("sk-ABCDEFGHIJKLMNOdummy", llm.seen)
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz", llm.seen)

    def test_attribution_stderr_is_redacted(self) -> None:
        class _Cap:
            def __init__(self):
                self.seen = ""

            def __call__(self, *, system_prompt, user_message):
                self.seen = user_message
                return json.dumps({"cause": "x", "fix": None})

        llm = _Cap()
        attribute_failure(
            plan=_plan(), failed_step=_plan().steps[0],
            stderr=f"auth failed {SECRETS}", stdout="", exit_code=1,
            understanding=_understanding(), llm_client=llm,
        )
        self.assertNotIn("sk-ABCDEFGHIJKLMNOdummy", llm.seen)
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz", llm.seen)


if __name__ == "__main__":
    unittest.main()
