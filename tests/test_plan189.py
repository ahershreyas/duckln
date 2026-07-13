"""Plan 189 — answer ANY question clearly + cited, route correctly:
F1 deterministic session-meta fast-path (status/VM/repo); F2 the 4-way LLM classifier
(repo_question/repo_action/technical/social); F3 tightened deterministic cues (ambiguous
"how does" defers to the LLM); F4 the technical-answer handler (clear format + citations +
a one-time internet offer)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


# --- F3: deterministic cues (definitive vs ambiguous) -----------------------

class F3Cues(unittest.TestCase):
    def test_ambiguous_cues_defer_to_llm(self):
        from duckln.repo_agent import classify_repo_agent_intent

        for msg in ("how does recursion work", "what does big-O mean", "explain hashing", "why is it slow"):
            self.assertEqual(classify_repo_agent_intent(msg), "", msg)

    def test_definitive_repo_cues_stay_ask(self):
        from duckln.repo_agent import classify_repo_agent_intent

        for msg in ("where is the entrypoint", "which file has the router", "show me the config", "locate the tests"):
            self.assertEqual(classify_repo_agent_intent(msg), "ask", msg)

    def test_action_verbs(self):
        from duckln.repo_agent import classify_repo_agent_intent

        self.assertEqual(classify_repo_agent_intent("fix the failing build"), "do")

    def test_explicit_repo_reference_is_ask(self):
        # Plan 189: "in the code/repo/..." is a model-independent repo signal → repo_question.
        from duckln.repo_agent import classify_repo_agent_intent

        for m in ("how does authentication work in the code", "what happens in this repo on startup",
                  "how is caching done in the codebase"):
            self.assertEqual(classify_repo_agent_intent(m), "ask", m)


# --- F2: 4-way classifier prompt --------------------------------------------

class F2Classifier(unittest.TestCase):
    def test_valid_has_four_routes(self):
        from duckln.conversation_routes import llm_intent

        self.assertEqual(llm_intent._VALID, ("repo_question", "repo_action", "technical", "social"))

    def test_prompt_names_technical_and_social(self):
        from duckln.conversation_routes import llm_intent

        p = llm_intent._SYSTEM_PROMPT.lower()
        self.assertIn("technical", p)
        self.assertIn("social", p)
        self.assertIn("do you support", p)  # capability → technical
        self.assertIn("session", p)         # session/state is not repo_question

    def test_parses_technical(self):
        from duckln.conversation_routes.llm_intent import classify_repo_relevance

        self.assertEqual(classify_repo_relevance("x", llm_client=lambda **k: '{"route": "technical"}'), "technical")
        self.assertEqual(classify_repo_relevance("x", llm_client=lambda **k: '{"route": "social"}'), "social")
        self.assertEqual(classify_repo_relevance("x", llm_client=lambda **k: 'garbage'), "")


# --- F1: session-meta fast-path ---------------------------------------------

class F1SessionMeta(unittest.TestCase):
    def test_status_answers_from_state(self):
        from duckln import main

        seen: list[str] = []
        with patch.object(main, "_render_active_deploy_status_summary", return_value="STATUS: JustHireMe deploying"):
            handled = main._maybe_answer_session_meta("what's the status of the setup", Path("/tmp"), seen.append)
        self.assertTrue(handled)
        self.assertIn("STATUS: JustHireMe deploying", seen[0])

    def test_which_vm_answers_target(self):
        from duckln import main

        seen: list[str] = []
        with patch.object(main, "_session_execution_target", return_value="vm"), \
             patch("duckln.repo_bringup._active_vm_name", return_value="duckln-vm"):
            handled = main._maybe_answer_session_meta("which VM am I using", Path("/tmp"), seen.append)
        self.assertTrue(handled)
        self.assertIn("vm", seen[0].lower())
        self.assertIn("duckln-vm", seen[0])

    def test_which_repo_still_works(self):
        from duckln import main

        seen: list[str] = []
        row = SimpleNamespace(metadata={"repo_name": "JustHireMe"}, repo_path="/x", repo_key="k",
                              repo_url="https://github.com/x/JustHireMe")
        store = SimpleNamespace(get_latest_repo_state=lambda: row)
        with patch.object(main, "initialize_state_store", return_value=store):
            handled = main._maybe_answer_session_meta("which repo did we run last", Path("/tmp"), seen.append)
        self.assertTrue(handled)
        self.assertIn("JustHireMe", seen[0])

    def test_unrelated_falls_through(self):
        from duckln import main

        self.assertFalse(main._maybe_answer_session_meta("how does recursion work", Path("/tmp"), lambda _m: None))


# --- F4: technical-answer handler (clear + cited + one-time offer) -----------

class F4TechnicalAnswer(unittest.TestCase):
    def _client(self, text="Recursion is a function that calls itself."):
        return lambda **kw: text

    def test_offline_answer_has_honest_note_and_one_time_offer(self):
        from duckln import main, ai_client, internet_skill

        with tempfile.TemporaryDirectory() as td:
            paths = SimpleNamespace(config_dir=Path(td))
            seen: list[str] = []
            with patch.object(ai_client, "build_default_llm_client_or_none", return_value=self._client()), \
                 patch.object(internet_skill, "is_internet_enabled", return_value=False):
                # No interactive channel → the offer defaults to REJECT (no re-answer), but is shown once.
                ti = SimpleNamespace()
                ok = main._answer_technical_question(command="how does recursion work", current=SimpleNamespace(),
                                                     paths=paths, display=seen.append, terminal_interface=ti)
            joined = "\n".join(seen)
            self.assertTrue(ok)
            self.assertIn("Recursion is a function", joined)
            self.assertIn("general knowledge", joined.lower())
            # Second offline answer must NOT re-show the offer (asked once).
            seen2: list[str] = []
            with patch.object(ai_client, "build_default_llm_client_or_none", return_value=self._client()), \
                 patch.object(internet_skill, "is_internet_enabled", return_value=False):
                main._answer_technical_question(command="explain hashing", current=SimpleNamespace(),
                                                paths=paths, display=seen2.append, terminal_interface=SimpleNamespace())
            # the offer's situation line appears at most once across the session
            self.assertNotIn("Duckln can use the internet", "\n".join(seen2))

    def test_online_answer_cites_sources(self):
        from duckln import main, ai_client, internet_skill

        src = SimpleNamespace(title="Recursion", url="https://docs.example/recursion", snippet="A function calling itself.")
        with tempfile.TemporaryDirectory() as td:
            paths = SimpleNamespace(config_dir=Path(td))
            seen: list[str] = []
            with patch.object(ai_client, "build_default_llm_client_or_none", return_value=self._client()), \
                 patch.object(internet_skill, "is_internet_enabled", return_value=True), \
                 patch.object(internet_skill, "internet_search_summary", return_value=(src,)):
                ok = main._answer_technical_question(command="how does recursion work", current=SimpleNamespace(),
                                                     paths=paths, display=seen.append, terminal_interface=SimpleNamespace())
            joined = "\n".join(seen)
            self.assertTrue(ok)
            self.assertIn("https://docs.example/recursion", joined)

    def test_no_model_returns_false(self):
        from duckln import main, ai_client

        with patch.object(ai_client, "build_default_llm_client_or_none", return_value=None):
            self.assertFalse(main._answer_technical_question(
                command="how does recursion work", current=SimpleNamespace(),
                paths=SimpleNamespace(config_dir=Path("/tmp")), display=lambda _m: None, terminal_interface=None))


if __name__ == "__main__":
    unittest.main()
