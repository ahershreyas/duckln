"""Plan 159 — a read-timeout is NOT 'unreachable'; lean planner prompt + just-in-time stack detail."""

from __future__ import annotations

import socket
import unittest

import httpx

from duckln.ai_client import ProviderRequestError
from duckln.harness.agent_def import builtin_agents_directory, load_agent_definition_from_path
from duckln.plan_mode import (
    MODEL_UNREACHABLE_BLOCKER,
    PlanStep,
    _model_connection_error,
    critic_review,
    finalize_plan_from_steps,
    stack_reference_snippet,
)


def _wrap(public: str, log: str, cause: BaseException) -> ProviderRequestError:
    try:
        raise ProviderRequestError(public, log) from cause
    except ProviderRequestError as e:
        return e


class ConnectionErrorClassifier(unittest.TestCase):
    # F1: a READ/response timeout (reachable, slow) is NOT a connection failure.
    def test_read_timeout_is_reachable(self):
        self.assertFalse(_model_connection_error(httpx.ReadTimeout("read op timed out")))
        self.assertFalse(_model_connection_error(httpx.PoolTimeout("pool")))

    def test_wrapped_read_timeout_is_reachable(self):
        # the real Ollama slow case: adapter wraps ReadTimeout in ProviderRequestError
        exc = _wrap("Failed to get a reply from Ollama.", "error=ReadTimeout: timed out", httpx.ReadTimeout("timed out"))
        self.assertFalse(_model_connection_error(exc))

    def test_connect_error_is_unreachable(self):
        self.assertTrue(_model_connection_error(httpx.ConnectError("Connection refused")))
        self.assertTrue(_model_connection_error(httpx.ConnectTimeout("connect")))

    def test_wrapped_connect_error_is_unreachable(self):
        exc = _wrap("Failed to get a reply.", "error=ConnectError: refused", httpx.ConnectError("Connection refused"))
        self.assertTrue(_model_connection_error(exc))

    def test_generic_timeout_text_is_not_unreachable(self):
        self.assertFalse(_model_connection_error(Exception("the request timed out after 300s")))

    def test_existing_cases_preserved(self):
        self.assertTrue(_model_connection_error(ConnectionError("Connection refused")))
        self.assertTrue(_model_connection_error(Exception("HTTPConnectionPool(port=11434): Max retries exceeded")))
        self.assertFalse(_model_connection_error(ValueError("invalid json")))
        # Plan 170: a bare timeout / mid-request drop on a server we reached is reachable-but-
        # slow/failed, NOT a CONNECT failure (Plan 159's "ambiguous fallback" is now reachable).
        self.assertFalse(_model_connection_error(TimeoutError()))
        self.assertFalse(_model_connection_error(socket.timeout()))


class CriticReviewNoFalseUnreachable(unittest.TestCase):
    # F2: a reachable-but-slow model → deterministic verdict, NOT a model_unreachable block.
    def _plan(self):
        steps = (PlanStep(index=1, title="Install", description="", command="pip install -r requirements.txt",
                          safety_class="S2", verification="pip --version", rationale="x",
                          estimated_seconds=10, confidence=1.0, origin="planner"),)
        return finalize_plan_from_steps(objective="x", repo_slug=None, context_summary="x", steps=steps, mode="hitl")

    def test_read_timeout_falls_back_to_deterministic(self):
        def slow(*, system_prompt, user_message):
            raise httpx.ReadTimeout("timed out")
        v = critic_review(plan=self._plan(), understanding=None, llm_client=slow)
        self.assertNotEqual(v.external_blocker, MODEL_UNREACHABLE_BLOCKER)
        self.assertIn(v.verdict, ("approve", "revise", "block"))

    def test_connect_error_is_model_unreachable(self):
        def down(*, system_prompt, user_message):
            raise httpx.ConnectError("Connection refused")
        v = critic_review(plan=self._plan(), understanding=None, llm_client=down)
        self.assertEqual(v.external_blocker, MODEL_UNREACHABLE_BLOCKER)


class LeanPromptAndJitDetail(unittest.TestCase):
    # F3: planner.md is lean; deep examples load just-in-time by detected stack.
    def test_planner_prompt_is_lean(self):
        ad = load_agent_definition_from_path(builtin_agents_directory() / "planner.md")
        # ~4.7k tokens now (was ~8.3k); assert well under the old size.
        self.assertLess(len(ad.system_prompt), 22000, "planner.md should be slimmed")
        self.assertIn("Node.js / Express", ad.system_prompt)      # anchor example kept
        self.assertIn("AI / ML Training", ad.system_prompt)        # hardest pattern kept
        self.assertNotIn("Django Web App", ad.system_prompt)       # moved to reference
        self.assertNotIn("Package → Command inference table", ad.system_prompt)

    def test_jit_snippet_matches_stack(self):
        self.assertIn("Django Web App", stack_reference_snippet("python", ("manage.py", "requirements.txt")))
        self.assertIn("Monorepo", stack_reference_snippet("node_typescript", ("turbo.json", "package.json")))
        self.assertIn("Docker-First", stack_reference_snippet("python", ("Dockerfile", "compose.yaml")))

    def test_jit_table_for_uncovered_stack_only(self):
        self.assertIn("inference table", stack_reference_snippet("go_native", ("go.mod",)).lower())
        self.assertNotIn("inference table", stack_reference_snippet("node_typescript", ("package.json",)).lower())

    def test_jit_empty_when_no_match(self):
        # a plain python script (core-covered, no special signals) → no extra detail
        self.assertEqual(stack_reference_snippet("python", ("app.py",)), "")

    def test_reference_file_is_not_an_agent_spec(self):
        from duckln.harness.agent_def import AgentRegistry
        reg = AgentRegistry.from_directory(builtin_agents_directory())
        self.assertIsNone(reg.lookup("planner_stack_guides"))


if __name__ == "__main__":
    unittest.main()
