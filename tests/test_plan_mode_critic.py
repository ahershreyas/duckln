"""Plan 72 Phase 3 — mandatory supervisor critic verdict tests."""

from __future__ import annotations

import json
import unittest

from duckln.plan_mode import CriticVerdict, PlanRecord, PlanStep, RepoUnderstanding, critic_review


def _plan(steps):
    return PlanRecord(
        plan_id="p1", objective="Set up x", context_summary="family=node",
        steps=tuple(steps), risks=(), rollback="", estimated_seconds=10,
        created_at="2026-05-22T00:00:00Z", status="pending", repo_slug="o/x",
        mode_at_creation="hootlwo",
    )


def _step(cmd, sclass="S2"):
    return PlanStep(index=1, title="s", description="", command=cmd, safety_class=sclass,
                    verification="exit 0", rationale="", estimated_seconds=5, confidence=1.0)


class _LLM:
    def __init__(self, response):
        self.response = response
        self.called = False

    def __call__(self, *, system_prompt, user_message):
        self.called = True
        return self.response


class CriticReviewTest(unittest.TestCase):
    def test_no_llm_falls_back_to_deterministic_not_skipped(self) -> None:
        # Plan 76 Fix B: the supervisor never returns "skipped" — without an LLM
        # it runs the deterministic checklist review. A run-less plan → "revise".
        v = critic_review(plan=_plan([_step("npm ci")]), understanding=None, llm_client=None)
        self.assertNotEqual(v.verdict, "skipped")
        self.assertEqual(v.verdict, "revise")
        self.assertTrue(v.reason)

    def test_empty_plan_blocked_without_llm(self) -> None:
        v = critic_review(plan=_plan([]), understanding=None, llm_client=None)
        self.assertEqual(v.verdict, "block")

    def test_s4_step_blocked_deterministically(self) -> None:
        v = critic_review(plan=_plan([_step("rm -rf /", sclass="S4")]), understanding=None, llm_client=None)
        self.assertEqual(v.verdict, "block")

    def test_llm_approve(self) -> None:
        llm = _LLM(json.dumps({"verdict": "approve", "reason": "looks good", "missing_question": None, "external_blocker": None}))
        v = critic_review(plan=_plan([_step("npm ci")]), understanding=None, llm_client=llm)
        self.assertEqual(v.verdict, "approve")
        self.assertTrue(llm.called)

    def test_llm_block_with_question(self) -> None:
        llm = _LLM(json.dumps({"verdict": "block", "reason": "needs creds", "missing_question": "Which AWS region?", "external_blocker": None}))
        v = critic_review(plan=_plan([_step("aws ec2 run-instances")]), understanding=None, llm_client=llm)
        self.assertEqual(v.verdict, "block")
        self.assertEqual(v.missing_question, "Which AWS region?")

    def test_malformed_verdict_after_retries_honest_stops_not_deterministic(self) -> None:
        # Plan 160 Phase B: the LLM is the decision-maker. A REACHABLE model that returns
        # garbage even after its retry budget → honest-stop (model_unresponsive), NOT a
        # silent deterministic approve/revise. (Supersedes Plan 76's deterministic fallback.)
        from duckln.plan_mode import MODEL_UNRESPONSIVE_BLOCKER

        llm = _LLM("not json")
        v = critic_review(plan=_plan([_step("npm ci")]), understanding=None, llm_client=llm)
        self.assertNotEqual(v.verdict, "skipped")
        self.assertNotEqual(v.verdict, "approve")
        self.assertEqual(v.verdict, "block")
        self.assertEqual(v.external_blocker, MODEL_UNRESPONSIVE_BLOCKER)


if __name__ == "__main__":
    unittest.main()
