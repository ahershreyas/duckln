"""Tests for the generic observe-decide-act loop."""

from __future__ import annotations

import unittest

from agent.agent_loop import (
    ActionResult,
    IssueClassification,
    LoopChoice,
    LoopStep,
    Remediation,
    run_agent_loop,
)


def _ok(outcome: str = "ok") -> ActionResult:
    return ActionResult(ok=True, outcome=outcome)


def _fail(stderr: str = "boom") -> ActionResult:
    return ActionResult(ok=False, outcome="failed", raw_stderr=stderr)


def _classify_static(issue: IssueClassification | None):
    return lambda _result: issue


def _resolve_static(remediation: Remediation | None):
    return lambda _issue: remediation


class AgentLoopTests(unittest.TestCase):
    def test_happy_path_all_steps_complete(self) -> None:
        steps = (
            LoopStep(
                name="step_one",
                run=_ok,
                classify=_classify_static(None),
                resolve_remediation=_resolve_static(None),
            ),
            LoopStep(
                name="step_two",
                run=_ok,
                classify=_classify_static(None),
                resolve_remediation=_resolve_static(None),
            ),
        )
        outcome = run_agent_loop(
            steps=steps,
            surface_choice=lambda _i, _r: LoopChoice.IGNORE,
        )
        self.assertTrue(outcome.completed)
        self.assertEqual(outcome.last_step, "step_two")
        self.assertEqual(outcome.stop_reason, "all_steps_completed")

    def test_fix_now_runs_remediation_and_retries(self) -> None:
        attempts = {"count": 0}

        def step_run() -> ActionResult:
            attempts["count"] += 1
            return _ok() if attempts["count"] >= 2 else _fail("API not enabled")

        remediation_runs = {"count": 0}

        def remediation_run() -> ActionResult:
            remediation_runs["count"] += 1
            return _ok("enabled")

        issue = IssueClassification(kind="needs_enable", message="api off")
        remediation = Remediation(kind="needs_enable", label="enable api", run=remediation_run)
        step = LoopStep(
            name="discover",
            run=step_run,
            classify=_classify_static(issue),
            resolve_remediation=_resolve_static(remediation),
        )

        outcome = run_agent_loop(
            steps=(step,),
            surface_choice=lambda _i, _r: LoopChoice.FIX_NOW,
        )
        self.assertTrue(outcome.completed)
        self.assertEqual(attempts["count"], 2)
        self.assertEqual(remediation_runs["count"], 1)

    def test_fix_later_persists_via_on_defer_and_stops(self) -> None:
        deferred_calls: list[IssueClassification] = []
        issue = IssueClassification(kind="auth_expired", message="please reauth")
        step = LoopStep(
            name="discover",
            run=_fail,
            classify=_classify_static(issue),
            resolve_remediation=_resolve_static(None),
        )

        outcome = run_agent_loop(
            steps=(step, step),
            surface_choice=lambda _i, _r: LoopChoice.FIX_LATER,
            on_defer=deferred_calls.append,
        )
        self.assertFalse(outcome.completed)
        self.assertEqual(outcome.stop_reason, "deferred_by_user")
        self.assertEqual(outcome.deferred, (issue,))
        self.assertEqual(deferred_calls, [issue])

    def test_ignore_skips_step_and_continues(self) -> None:
        issue = IssueClassification(kind="non_fatal", message="meh")
        bad_step = LoopStep(
            name="bad",
            run=_fail,
            classify=_classify_static(issue),
            resolve_remediation=_resolve_static(None),
        )
        good_step = LoopStep(
            name="good",
            run=_ok,
            classify=_classify_static(None),
            resolve_remediation=_resolve_static(None),
        )

        outcome = run_agent_loop(
            steps=(bad_step, good_step),
            surface_choice=lambda _i, _r: LoopChoice.IGNORE,
        )
        self.assertTrue(outcome.completed)
        self.assertEqual(outcome.last_step, "good")
        self.assertEqual(outcome.ignored, (issue,))

    def test_max_attempts_exhausted(self) -> None:
        issue = IssueClassification(kind="needs_enable", message="api off")

        def always_fail() -> ActionResult:
            return _fail("API not enabled")

        remediation = Remediation(kind="needs_enable", label="enable", run=lambda: _ok())
        step = LoopStep(
            name="discover",
            run=always_fail,
            classify=_classify_static(issue),
            resolve_remediation=_resolve_static(remediation),
        )

        outcome = run_agent_loop(
            steps=(step,),
            surface_choice=lambda _i, _r: LoopChoice.FIX_NOW,
            max_attempts_per_step=2,
        )
        self.assertFalse(outcome.completed)
        self.assertEqual(outcome.stop_reason, "max_attempts_exhausted")

    def test_unclassified_failure_stops_loop(self) -> None:
        step = LoopStep(
            name="unknown",
            run=lambda: _fail("nothing recognised"),
            classify=_classify_static(None),
            resolve_remediation=_resolve_static(None),
        )

        outcome = run_agent_loop(
            steps=(step,),
            surface_choice=lambda _i, _r: LoopChoice.IGNORE,
        )
        self.assertFalse(outcome.completed)
        self.assertEqual(outcome.stop_reason, "unclassified_failure")

    def test_fix_now_with_no_remediation_falls_to_fix_later(self) -> None:
        issue = IssueClassification(kind="manual_only", message="needs console")
        step = LoopStep(
            name="discover",
            run=_fail,
            classify=_classify_static(issue),
            resolve_remediation=_resolve_static(None),
        )

        outcome = run_agent_loop(
            steps=(step,),
            surface_choice=lambda _i, _r: LoopChoice.FIX_NOW,
        )
        self.assertFalse(outcome.completed)
        self.assertEqual(outcome.stop_reason, "deferred_by_user")

    def test_remediation_failure_stops_loop(self) -> None:
        issue = IssueClassification(kind="needs_enable", message="api off")
        remediation = Remediation(
            kind="needs_enable",
            label="enable",
            run=lambda: _fail("enable rejected"),
        )
        step = LoopStep(
            name="discover",
            run=_fail,
            classify=_classify_static(issue),
            resolve_remediation=_resolve_static(remediation),
        )

        outcome = run_agent_loop(
            steps=(step,),
            surface_choice=lambda _i, _r: LoopChoice.FIX_NOW,
        )
        self.assertFalse(outcome.completed)
        self.assertEqual(outcome.stop_reason, "remediation_failed")


if __name__ == "__main__":
    unittest.main()
