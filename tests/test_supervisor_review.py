from __future__ import annotations

import unittest

from duckln.plan_mode import (
    CriticVerdict,
    PlanRecord,
    PlanStep,
    RepoUnderstanding,
    _deterministic_supervisor_review,
    critic_review,
)


def _understanding(recent_failures=()):
    return RepoUnderstanding(
        repo_slug="acme/demo", repo_path=None, objective="o", os_name="linux",
        arch="arm64", detected_runtimes=("node",), detected_files=("package.json",),
        repo_family="node_typescript", readme_excerpt="", config_excerpts={},
        recent_failures=tuple(recent_failures), recent_skills=(), execution_target="vm",
        control_mode="hootlwo", needs_clarification=False, clarification_seed=None,
    )


def _full_step(index, command, *, safety="S2", verification="ok", target="vm"):
    return PlanStep(
        index=index, title=f"Step {index}", description="", command=command,
        safety_class=safety, verification=verification, rationale="",
        estimated_seconds=30, target=target,
    )


def _step(index, command, *, safety="S1"):
    return PlanStep(
        index=index,
        title=f"Step {index}",
        description="",
        command=command,
        safety_class=safety,
        verification=None,
        rationale="",
        estimated_seconds=30,
    )


def _plan(steps, *, context="family=node_typescript"):
    return PlanRecord(
        plan_id="p1",
        objective="Set up demo",
        context_summary=context,
        steps=tuple(steps),
        risks=(),
        rollback="",
        estimated_seconds=90,
        created_at="2026-01-01T00:00:00Z",
        status="pending",
        repo_slug="acme/demo",
        mode_at_creation="hotl",
    )


class TestDeterministicSupervisorReview(unittest.TestCase):
    def test_approves_sound_clone_install_run_plan(self):
        plan = _plan([
            _step(1, "git clone https://github.com/acme/demo.git"),
            _step(2, "npm install"),
            _step(3, "npm run dev"),
        ])
        verdict = _deterministic_supervisor_review(plan, None)
        self.assertEqual(verdict.verdict, "approve")
        self.assertTrue(verdict.reason)
        self.assertLessEqual(len(verdict.reason.splitlines()), 5)
        self.assertIn("Checked", verdict.reason)

    def test_revise_when_no_run_step(self):
        plan = _plan([
            _step(1, "git clone https://github.com/acme/demo.git"),
            _step(2, "npm install"),
        ])
        verdict = _deterministic_supervisor_review(plan, None)
        self.assertEqual(verdict.verdict, "revise")
        self.assertIn("run", verdict.reason.lower())

    def test_block_on_destructive_step(self):
        plan = _plan([
            _step(1, "git clone https://github.com/acme/demo.git"),
            _step(2, "rm -rf /", safety="S4"),
            _step(3, "npm run dev"),
        ])
        verdict = _deterministic_supervisor_review(plan, None)
        self.assertEqual(verdict.verdict, "block")

    def test_block_on_empty_plan(self):
        plan = _plan([])
        verdict = _deterministic_supervisor_review(plan, None)
        self.assertEqual(verdict.verdict, "block")


class TestCriticReviewNeverSkips(unittest.TestCase):
    def _sound_plan(self):
        return _plan([
            _step(1, "git clone https://github.com/acme/demo.git"),
            _step(2, "npm install"),
            _step(3, "npm run dev"),
        ])

    def test_no_llm_falls_back_to_deterministic_approve(self):
        verdict = critic_review(plan=self._sound_plan(), understanding=None, llm_client=None)
        self.assertEqual(verdict.verdict, "approve")
        self.assertTrue(verdict.reason)
        self.assertNotEqual(verdict.verdict, "skipped")

    def test_malformed_llm_falls_back_to_deterministic(self):
        def junk_client(*, system_prompt, user_message):
            return "not json at all"

        verdict = critic_review(plan=self._sound_plan(), understanding=None, llm_client=junk_client)
        self.assertIn(verdict.verdict, ("approve", "revise", "block"))
        self.assertNotEqual(verdict.verdict, "skipped")
        self.assertTrue(verdict.reason)

    def test_llm_verdict_with_empty_reason_backfills_deterministic(self):
        def thin_client(*, system_prompt, user_message):
            return '{"verdict": "approve", "reason": ""}'

        verdict = critic_review(plan=self._sound_plan(), understanding=None, llm_client=thin_client)
        self.assertEqual(verdict.verdict, "approve")
        self.assertTrue(verdict.reason)


class TestDeeperCriticChecks(unittest.TestCase):
    def _base(self, mutating):
        # clone (S1) + run (S2) + the mutating step under test inserted before run.
        clone = _full_step(1, "git clone https://github.com/a/b.git", safety="S1", verification="test -d .git")
        run = _full_step(3, "npm run dev", safety="S2", verification="port", target="vm")
        return _plan([clone, mutating, run])

    def test_revise_when_mutating_step_missing_verify(self):
        bad = _full_step(2, "npm install", safety="S2", verification=None, target="vm")
        v = _deterministic_supervisor_review(self._base(bad), _understanding())
        self.assertEqual(v.verdict, "revise")
        self.assertIn("verification", v.reason.lower())

    def test_revise_when_mutating_step_missing_target(self):
        bad = _full_step(2, "npm install", safety="S2", verification="ok", target="")
        v = _deterministic_supervisor_review(self._base(bad), _understanding())
        self.assertEqual(v.verdict, "revise")
        self.assertIn("target", v.reason.lower())

    def test_revise_on_wrong_os_command(self):
        bad = _full_step(2, "brew install node", safety="S2", verification="ok", target="vm")
        v = _deterministic_supervisor_review(self._base(bad), _understanding())
        self.assertEqual(v.verdict, "revise")
        self.assertIn("wrong-os", v.reason.lower())

    def test_revise_on_duplicate_failed_command(self):
        bad = _full_step(2, "npm install", safety="S2", verification="ok", target="vm")
        u = _understanding(recent_failures=("npm install failed: EACCES",))
        v = _deterministic_supervisor_review(self._base(bad), u)
        self.assertEqual(v.verdict, "revise")
        self.assertIn("already failed", v.reason.lower())

    def test_approves_well_formed_mutating_plan(self):
        good = _full_step(2, "npm install", safety="S2", verification="test -d node_modules", target="vm")
        v = _deterministic_supervisor_review(self._base(good), _understanding())
        self.assertEqual(v.verdict, "approve")
        self.assertLessEqual(len(v.reason.splitlines()), 5)


if __name__ == "__main__":
    unittest.main()
