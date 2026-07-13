"""Plan 160 — the LLM is the decision-maker (never deterministic); the USER owns destructive steps."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import httpx

import duckln.repo_bringup as rb
from duckln import recovery as r
from duckln.harness.agent_def import load_agent_definition_from_text
from duckln.modes import ControlMode
from duckln.plan_mode import (
    MODEL_UNREACHABLE_BLOCKER,
    MODEL_UNRESPONSIVE_BLOCKER,
    PlanStep,
    critic_review,
    finalize_plan_from_steps,
)


def _plan(cmd="npm ci", sclass="S2"):
    step = PlanStep(index=1, title="t", description="", command=cmd, safety_class=sclass,
                    verification="x", rationale="x", estimated_seconds=5, confidence=1.0, origin="planner")
    return finalize_plan_from_steps(objective="o", repo_slug=None, context_summary="c", steps=(step,), mode="hitl")


class PhaseB_SupervisorIsLLMDecided(unittest.TestCase):
    def test_connect_error_is_unreachable(self):
        def down(*, system_prompt, user_message):
            raise httpx.ConnectError("Connection refused")
        v = critic_review(plan=_plan(), understanding=None, llm_client=down)
        self.assertEqual(v.external_blocker, MODEL_UNREACHABLE_BLOCKER)

    def test_read_timeout_retries_then_honest_stops(self):
        calls = {"n": 0}
        def slow(*, system_prompt, user_message):
            calls["n"] += 1
            raise httpx.ReadTimeout("timed out")
        v = critic_review(plan=_plan(), understanding=None, llm_client=slow)
        self.assertEqual(v.external_blocker, MODEL_UNRESPONSIVE_BLOCKER)  # honest-stop, not deterministic
        self.assertGreaterEqual(calls["n"], 2)  # retried before honest-stop

    def test_garbage_after_retries_honest_stops_not_deterministic(self):
        v = critic_review(plan=_plan(), understanding=None, llm_client=lambda **k: "not json")
        self.assertEqual(v.verdict, "block")
        self.assertEqual(v.external_blocker, MODEL_UNRESPONSIVE_BLOCKER)

    def test_valid_llm_verdict_is_used(self):
        import json
        good = lambda **k: json.dumps({"verdict": "approve", "reason": "ok"})
        v = critic_review(plan=_plan(), understanding=None, llm_client=good)
        self.assertEqual(v.verdict, "approve")

    def test_no_llm_returns_structural_floor(self):
        # Pure function: llm_client=None → deterministic structural verdict (the bring-up caller
        # gates honest-stop on reasoning_enabled). Not a model_unreachable block here.
        v = critic_review(plan=_plan(cmd="npm run dev"), understanding=None, llm_client=None)
        self.assertNotEqual(v.external_blocker, MODEL_UNREACHABLE_BLOCKER)


class PhaseA_DestructiveIsUserDecided(unittest.TestCase):
    def test_no_approver_blocks_unattended(self):
        self.assertFalse(rb._step_allowed_in_mode("S4", ControlMode.HOOTLWO, None))

    def test_user_yes_runs_user_no_skips(self):
        self.assertTrue(rb._step_allowed_in_mode("S4", ControlMode.HOOTLWO, lambda _m: True))
        self.assertFalse(rb._step_allowed_in_mode("S4", ControlMode.HOOTLWO, lambda _m: False))

    def test_decider_choices(self):
        self.assertTrue(rb._step_allowed_in_mode("S4", "hootlwo", None, destructive_decider=lambda _m: "yes"))
        self.assertTrue(rb._step_allowed_in_mode("S4", "hootlwo", None, destructive_decider=lambda _m: "all"))
        self.assertFalse(rb._step_allowed_in_mode("S4", "hootlwo", None, destructive_decider=lambda _m: "no"))

    def test_approve_all_remembers_session(self):
        calls = {"n": 0}
        def sel(msg, opts):
            calls["n"] += 1
            return "Approve all this session"
        decide = rb.make_destructive_decider(select=sel)
        self.assertEqual(decide("step1"), "all")
        self.assertEqual(decide("step2"), "all")   # remembered
        self.assertEqual(calls["n"], 1)            # only asked once

    def test_decider_binary_fallback_no_all(self):
        decide = rb.make_destructive_decider(approve=lambda m: True)
        self.assertEqual(decide("x"), "yes")
        decide_no = rb.make_destructive_decider(approve=lambda m: False)
        self.assertEqual(decide_no("x"), "no")


class D1_ProbeSetsRetryBudget(unittest.TestCase):
    def _def(self):
        return load_agent_definition_from_text("---\nname: t\nrole: x\ntools: []\nmax_contract_retries: 2\n---\nbody")

    def test_weak_gets_reduced_budget(self):
        with tempfile.TemporaryDirectory() as td:
            cd = Path(td)
            r.write_cached_capability(cd, "weakm", "weak")
            r.write_cached_capability(cd, "strongm", "capable")
            self.assertEqual(r.effective_contract_retries(self._def(), model_id="strongm", config_dir=cd), 2)
            self.assertEqual(r.effective_contract_retries(self._def(), model_id="weakm", config_dir=cd), 1)
            self.assertEqual(r.effective_contract_retries(self._def(), model_id="unprobed", config_dir=cd), 2)


class D2_NoSingleSuccessPromotion(unittest.TestCase):
    def test_weak_not_promoted_on_one_pass(self):
        with tempfile.TemporaryDirectory() as td:
            cd = Path(td)
            r.write_cached_capability(cd, "wm", "weak")
            r.record_capability_observation(cd, "wm", delivered_valid=True)
            self.assertEqual(r.read_cached_capability(cd, "wm"), "weak")  # one pass != trust

    def test_promotes_after_consecutive_passes(self):
        with tempfile.TemporaryDirectory() as td:
            cd = Path(td)
            r.write_cached_capability(cd, "wm", "weak")
            for _ in range(r._CAPABILITY_PROMOTE_THRESHOLD):
                r.record_capability_observation(cd, "wm", delivered_valid=True)
            self.assertEqual(r.read_cached_capability(cd, "wm"), "capable")

    def test_failure_resets_streak(self):
        with tempfile.TemporaryDirectory() as td:
            cd = Path(td)
            r.write_cached_capability(cd, "wm", "weak")
            r.record_capability_observation(cd, "wm", delivered_valid=True)
            r.record_capability_observation(cd, "wm", delivered_valid=False)
            r.record_capability_observation(cd, "wm", delivered_valid=True)
            self.assertEqual(r.read_cached_capability(cd, "wm"), "weak")  # not yet N consecutive


if __name__ == "__main__":
    unittest.main()
