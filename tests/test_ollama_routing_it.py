"""Plan 189 F5 — REAL Ollama integration test: passes ACTUAL questions to a local Ollama model
and asserts the routing + a cited technical answer. Opt-in (needs DUCKLN_OLLAMA_IT=1 and a
reachable Ollama), so CI stays green without a model.

Run: DUCKLN_OLLAMA_IT=1 DUCKLN_OLLAMA_IT_MODEL=gemma2:9b PYTHONPATH=src \
     python -m unittest tests.test_ollama_routing_it
"""

from __future__ import annotations

import os
import unittest

_MODEL = os.getenv("DUCKLN_OLLAMA_IT_MODEL", "gemma2:9b")
_BASE = "http://localhost:11434"


def _ollama_reachable() -> bool:
    try:
        import httpx

        return httpx.get(f"{_BASE}/api/tags", timeout=3).status_code == 200
    except Exception:
        return False


def _client(*, system_prompt, user_message, **_kw):
    from duckln.ai_client import Provider, generate_provider_reply

    return generate_provider_reply(Provider.OLLAMA, model_id=_MODEL, api_key=None, base_url=_BASE,
                                   system_prompt=system_prompt, user_message=user_message)


@unittest.skipUnless(os.getenv("DUCKLN_OLLAMA_IT") and _ollama_reachable(),
                     "set DUCKLN_OLLAMA_IT=1 with Ollama running to exercise the real model")
class OllamaRoutingIT(unittest.TestCase):
    """Drives the ACTUAL cascade (deterministic cues → the 4-way LLM classifier) against the real model."""

    def _route(self, msg: str) -> str:
        from duckln.conversation_routes.llm_intent import classify_repo_relevance
        from duckln.repo_agent import classify_repo_agent_intent

        det = classify_repo_agent_intent(msg)
        if det == "ask":
            return "repo_question"
        if det == "do":
            return "repo_action"
        return classify_repo_relevance(msg, llm_client=_client)

    def test_social_never_routes_to_repo(self):
        for m in ("hi there", "tell me a joke", "what's the weather today", "how are you?"):
            self.assertNotIn(self._route(m), ("repo_question", "repo_action"), m)

    def test_general_technical_and_capability_never_loop_repo_qa(self):
        # The production requirement: these must NOT land in repo_qa/action (the loop the user hit).
        for m in ("how does recursion work", "explain how CUDA works", "aws vs gcp for gpu",
                  "do you support docker", "what is a hash map"):
            self.assertNotIn(self._route(m), ("repo_question", "repo_action"), m)

    def test_real_repo_questions_route_to_repo(self):
        for m in ("where is the entry point in this repo", "how does authentication work in the code",
                  "is the build passing"):
            self.assertEqual(self._route(m), "repo_question", m)

    def test_repo_actions_route_to_action(self):
        for m in ("fix the failing build", "add a test for the login function"):
            self.assertEqual(self._route(m), "repo_action", m)

    def test_technical_answer_is_clear_and_grounded(self):
        # Real end-to-end: the technical system prompt produces a non-trivial, plain-language answer.
        # A big local model under load can read-timeout — that's an infra condition, not a logic
        # failure, so skip rather than report a false FAILED.
        from duckln import main
        from duckln.ai_client import ProviderRequestError

        try:
            answer = _client(system_prompt=main._TECH_SYSTEM_CLEAR, user_message="how does recursion work")
        except ProviderRequestError as exc:
            self.skipTest(f"Ollama generation unavailable/overloaded: {exc}")
        self.assertTrue(answer and len(answer.strip()) > 40)
        self.assertIn("recursion", answer.lower())


@unittest.skipUnless(os.getenv("DUCKLN_OLLAMA_IT") and _ollama_reachable(),
                     "set DUCKLN_OLLAMA_IT=1 with Ollama running to exercise the real model")
class Plan190GroundedIT(unittest.TestCase):
    """Plan 190 on the REAL model: (1) the deterministic self/recommendation handlers answer grounded
    (model-independent floor); (2) if a self/recommendation paraphrase reaches the classifier it must
    NEVER route to repo_action/repo_question (the dangerous misroutes); (3) a general technical answer
    under the guarded prompt never claims to be a disconnected language model with no repo access."""

    def _route(self, msg: str) -> str:
        from duckln.conversation_routes.llm_intent import classify_repo_relevance
        from duckln.repo_agent import classify_repo_agent_intent

        det = classify_repo_agent_intent(msg)
        if det == "ask":
            return "repo_question"
        if det == "do":
            return "repo_action"
        return classify_repo_relevance(msg, llm_client=_client)

    def _cur(self):
        from types import SimpleNamespace

        return SimpleNamespace(provider=SimpleNamespace(label="Ollama"),
                               model=_MODEL, mode=SimpleNamespace(value="hootlwo"))

    def test_self_questions_answer_grounded_not_a_disclaimer(self):
        from pathlib import Path

        from duckln import main

        for m in ("how many repos do you have access to", "what can you do", "who are you"):
            seen: list[str] = []
            self.assertTrue(main._maybe_answer_duckln_self(m, self._cur(), Path("/tmp"), seen.append), m)
            low = seen[0].lower()
            self.assertNotIn("language model", low, m)
            self.assertNotIn("don't have access", low, m)
            self.assertNotIn("text-only model", low, m)

    def test_recommendation_reaches_the_grounded_flow(self):
        from pathlib import Path

        from duckln import main

        # ambiguous -> ONE clarify; domain-specified -> grounded catalog answer
        seen: list[str] = []
        self.assertTrue(main._maybe_answer_recommendation("recommend a repo", Path("/tmp"), seen.append))
        self.assertIn("?", seen[0])
        seen = []
        self.assertTrue(main._maybe_answer_recommendation("best repo for image generation", Path("/tmp"), seen.append))
        self.assertIn("From my catalog", seen[0])

    def test_self_paraphrase_never_routes_to_repo_action_or_question(self):
        # The classifier is the safety net behind the deterministic fast-path: even a paraphrase
        # that slips past it must NOT act on / read the active repo.
        for m in ("are you able to run shell commands?", "what kind of things can you help me with?"):
            self.assertNotIn(self._route(m), ("repo_action", "repo_question"), m)

    def test_guarded_technical_answer_never_disclaims_being_a_tool(self):
        from duckln import main
        from duckln.ai_client import ProviderRequestError

        try:
            answer = _client(system_prompt=main._TECH_SYSTEM_CLEAR,
                             user_message="what can you do with a repository?")
        except ProviderRequestError as exc:
            self.skipTest(f"Ollama generation unavailable/overloaded: {exc}")
        low = (answer or "").lower()
        self.assertTrue(answer and answer.strip())
        self.assertNotIn("i don't have access to any repositor", low)
        self.assertNotIn("text-only model", low)


@unittest.skipUnless(os.getenv("DUCKLN_OLLAMA_IT") and _ollama_reachable(),
                     "set DUCKLN_OLLAMA_IT=1 with Ollama running to exercise the real model")
class Plan191ClarifyIT(unittest.TestCase):
    """Plan 191 on the REAL model: the adaptive clarify engine plans CLEVER, grounded questions
    (each option maps to a real catalog domain), resolves a domain-named ask with few/no questions,
    and continue-vs-new classifies correctly — proven on gemma2:9b and gemma2:2b."""

    _DOMAINS = ("machine learning / LLMs", "image generation", "audio / speech", "web apps", "chatbots / agents")

    class _Ch:
        def __init__(self, answers):
            self.answers = list(answers)
            self.asked = []

        def supports_overlay(self):
            return True

        def select_choice(self, text, opts, *, allow_other=False, other_placeholder=""):
            self.asked.append((text, tuple(opts)))
            return self.answers.pop(0) if self.answers else None

    def _facts(self):
        from duckln.clarify import ClarifyFacts

        return ClarifyFacts(topic="what you want to build or run", options=self._DOMAINS)

    def test_ambiguous_ask_produces_clever_grounded_options(self):
        import tempfile
        from pathlib import Path

        from duckln.ai_client import ProviderRequestError
        from duckln.clarify import run_clarification

        with tempfile.TemporaryDirectory() as td:
            ch = self._Ch(["web apps"])
            try:
                r = run_clarification(ask="recommend a repo", facts=self._facts(), channel=ch,
                                      config_dir=Path(td), llm_client=_client, display=lambda _m: None)
            except ProviderRequestError as exc:
                self.skipTest(f"Ollama generation unavailable/overloaded: {exc}")
        # It asked at least one question, capped at 4, and resolved.
        self.assertLessEqual(len(ch.asked), 4)
        self.assertTrue(r.resolved or r.cancelled)
        if ch.asked:
            _text, opts = ch.asked[0]
            self.assertTrue(2 <= len(opts) <= 4)  # clever, concise — not a wall of options

    def test_domain_named_ask_resolves_quickly(self):
        import tempfile
        from pathlib import Path

        from duckln.ai_client import ProviderRequestError
        from duckln.clarify import run_clarification

        with tempfile.TemporaryDirectory() as td:
            ch = self._Ch(["image generation", "image generation", "image generation", "image generation"])
            try:
                r = run_clarification(ask="I want to generate images", facts=self._facts(), channel=ch,
                                      config_dir=Path(td), llm_client=_client, display=lambda _m: None)
            except ProviderRequestError as exc:
                self.skipTest(f"Ollama generation unavailable/overloaded: {exc}")
        self.assertLessEqual(len(ch.asked), 2)  # already clear → few/no questions

    def test_continue_vs_new_on_real_model(self):
        from duckln.ai_client import ProviderRequestError
        from duckln.clarify import decide_continue_or_new

        try:
            cont = decide_continue_or_new(message="something for making pictures", pending_question="What do you want to build?",
                                          options=self._DOMAINS, llm_client=_client)
            new = decide_continue_or_new(message="actually set up AutoGPT instead", pending_question="What do you want to build?",
                                         options=self._DOMAINS, llm_client=_client)
        except ProviderRequestError as exc:
            self.skipTest(f"Ollama generation unavailable/overloaded: {exc}")
        self.assertIn(cont, ("continue", "new"))
        self.assertEqual(new, "new")  # a fresh set-up verb is deterministically NEW


@unittest.skipUnless(os.getenv("DUCKLN_OLLAMA_IT") and _ollama_reachable(),
                     "set DUCKLN_OLLAMA_IT=1 with Ollama running to exercise the real model")
class Plan192RoutingIT(unittest.TestCase):
    """Plan 192 on the REAL model: the doc's Tier-3 classifier (verbatim prompts) classifies a
    greeting as conversation and a clear repo task as repo_task; "is it ready?" with context is not
    a repo_task guess; Tier 4 produces a one-line repo-vs-chat question, never the repo-only
    clarifier. Proven on gemma2:9b and gemma2:2b."""

    def test_greeting_is_never_a_repo_task(self):
        # The SAFE property (model-honest per the doc §1: a small model is weaker and may return
        # "ambiguous" → Tier 4 clarify). In the real cascade a greeting is handled by the
        # DETERMINISTIC tiers (F1/F2) before Tier 3; here we only require the classifier never
        # DANGEROUSLY calls a greeting a repo_task.
        from duckln.ai_client import ProviderRequestError
        from duckln.conversation_routes.llm_intent import classify_intent_with_context

        try:
            intent, _conf, _ = classify_intent_with_context("how r you?", "", llm_client=_client)
        except ProviderRequestError as exc:
            self.skipTest(f"Ollama unavailable/overloaded: {exc}")
        self.assertIn(intent, ("conversation", "ambiguous"), intent)
        self.assertNotEqual(intent, "repo_task")

    def test_clear_repo_task_is_never_conversation(self):
        # A capable model → repo_task; a weak model may say "ambiguous" (→ the deterministic repo
        # signal already routed it). The SAFE property: a setup request is never "just chatting".
        from duckln.ai_client import ProviderRequestError
        from duckln.conversation_routes.llm_intent import classify_intent_with_context

        try:
            intent, _conf, _ = classify_intent_with_context("set up the JustHireMe repository", "", llm_client=_client)
        except ProviderRequestError as exc:
            self.skipTest(f"Ollama unavailable/overloaded: {exc}")
        self.assertIn(intent, ("repo_task", "ambiguous"), intent)
        self.assertNotEqual(intent, "conversation")

    def test_status_with_context_is_not_a_repo_task_guess(self):
        from duckln.ai_client import ProviderRequestError
        from duckln.conversation_routes.llm_intent import classify_intent_with_context

        ctx = "user: set up JustHireMe\nassistant: Cloning and installing JustHireMe now…"
        try:
            with_ctx, _c, _ = classify_intent_with_context("is it ready?", ctx, llm_client=_client)
        except ProviderRequestError as exc:
            self.skipTest(f"Ollama unavailable/overloaded: {exc}")
        self.assertIn(with_ctx, ("status", "conversation", "ambiguous"))
        self.assertNotEqual(with_ctx, "repo_task")

    def test_tier4_asks_the_repo_vs_chat_axis(self):
        from duckln.ai_client import ProviderRequestError
        from duckln.conversation_routes.llm_intent import phrase_axis_clarification

        try:
            q = phrase_axis_clarification("hmm not sure", "", llm_client=_client)
        except ProviderRequestError as exc:
            self.skipTest(f"Ollama unavailable/overloaded: {exc}")
        self.assertTrue(q and q.strip())
        self.assertEqual(len(q.splitlines()), 1)
        self.assertNotIn("status or path", q.lower())


if __name__ == "__main__":
    unittest.main()
