from __future__ import annotations

import socket
import unittest

from duckln.plan_mode import (
    MODEL_UNREACHABLE_BLOCKER,
    PlanRecord,
    PlanStep,
    _model_connection_error,
    critic_review,
)


def _sound_plan():
    steps = (
        PlanStep(index=1, title="Clone", description="", command="git clone https://github.com/a/b.git",
                 safety_class="S1", verification="test -d .git", rationale="", estimated_seconds=10),
        PlanStep(index=2, title="Install", description="", command="npm install",
                 safety_class="S2", verification="test -d node_modules", rationale="",
                 estimated_seconds=10, target="vm"),
        PlanStep(index=3, title="Run", description="", command="npm run dev",
                 safety_class="S2", verification="port open", rationale="",
                 estimated_seconds=10, target="vm"),
    )
    return PlanRecord(
        plan_id="p1", objective="o", context_summary="family=node_typescript",
        steps=steps, risks=(), rollback="", estimated_seconds=30,
        created_at="2026-05-25T00:00:00Z", status="pending", repo_slug="a/b",
        mode_at_creation="hootlwo",
    )


class TestModelConnectionDetection(unittest.TestCase):
    def test_detects_connection_errors(self):
        # CONNECT-establishment failures = truly unreachable.
        self.assertTrue(_model_connection_error(ConnectionError("Connection refused")))
        self.assertTrue(_model_connection_error(ConnectionRefusedError()))
        self.assertTrue(_model_connection_error(Exception("HTTPConnectionPool(port=11434): Max retries exceeded")))
        self.assertTrue(_model_connection_error(Exception("could not connect to ollama")))

    def test_reachable_but_failed_is_not_unreachable(self):
        # Plan 170: a READ timeout, or a MID-REQUEST drop (reset/abort/broken-pipe) on a server
        # we reached, is reachable-but-failed — NOT "run ollama serve".
        self.assertFalse(_model_connection_error(TimeoutError()))
        self.assertFalse(_model_connection_error(socket.timeout()))
        self.assertFalse(_model_connection_error(ConnectionResetError("Connection reset by peer")))
        self.assertFalse(_model_connection_error(BrokenPipeError()))
        self.assertFalse(_model_connection_error(Exception("Server disconnected without sending a response")))

    def test_parse_errors_are_not_connection_errors(self):
        self.assertFalse(_model_connection_error(ValueError("invalid json")))
        self.assertFalse(_model_connection_error(KeyError("verdict")))


class TestCriticReviewConnection(unittest.TestCase):
    def test_unreachable_model_blocks_with_signal(self):
        def dead_client(*, system_prompt, user_message):
            raise ConnectionError("Connection refused")

        verdict = critic_review(plan=_sound_plan(), understanding=None, llm_client=dead_client)
        self.assertEqual(verdict.verdict, "block")
        self.assertEqual(verdict.external_blocker, MODEL_UNREACHABLE_BLOCKER)
        self.assertIn("not able to connect", verdict.reason.lower())

    def test_junk_from_reachable_model_honest_stops(self):
        # Plan 160 Phase B: the LLM is the decision-maker. A reachable model that returns junk
        # (no valid verdict) → honest-stop (model_unresponsive), NOT a deterministic approve.
        from duckln.plan_mode import MODEL_UNRESPONSIVE_BLOCKER

        def junk_client(*, system_prompt, user_message):
            return "not json"

        verdict = critic_review(plan=_sound_plan(), understanding=None, llm_client=junk_client)
        self.assertNotEqual(verdict.external_blocker, MODEL_UNREACHABLE_BLOCKER)  # not "can't connect"
        self.assertEqual(verdict.verdict, "block")
        self.assertEqual(verdict.external_blocker, MODEL_UNRESPONSIVE_BLOCKER)

    def test_garbage_is_retried_then_honest_stops(self):
        # Plan 160 Phase B: garbage is RETRIED (within budget) then honest-stops — never a
        # silent deterministic approve (supersedes Plan 79's deterministic fallback).
        from duckln.plan_mode import MODEL_UNRESPONSIVE_BLOCKER

        calls = {"n": 0}

        def junk_client(*, system_prompt, user_message):
            calls["n"] += 1
            return "```not json```"

        verdict = critic_review(plan=_sound_plan(), understanding=None, llm_client=junk_client)
        self.assertGreaterEqual(calls["n"], 2)  # retried
        self.assertEqual(verdict.verdict, "block")
        self.assertEqual(verdict.external_blocker, MODEL_UNRESPONSIVE_BLOCKER)

    def test_clean_verdict_after_one_garbage_is_used(self):
        seq = ['garbage', '{"verdict": "block", "reason": "needs creds", "missing_question": "Which region?"}']

        def client(*, system_prompt, user_message):
            return seq.pop(0)

        verdict = critic_review(plan=_sound_plan(), understanding=None, llm_client=client)
        self.assertEqual(verdict.verdict, "block")
        self.assertEqual(verdict.missing_question, "Which region?")


if __name__ == "__main__":
    unittest.main()
