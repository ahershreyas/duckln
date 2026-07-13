"""Plan 190 — ground questions about DUCKLN ITSELF (catalog/capabilities/identity/state) and
repo RECOMMENDATION in real facts, and route them without confusion:
F1 the deterministic Duckln-self grounded handler; F2b the recommendation detector (clarify ONCE
when ambiguous, answer from the catalog when a domain is named); F2 the classifier prompt hardening
(self + recommendation -> social, never repo_action/repo_question); F3 the technical-prompt identity
guard + one-shot presentation example."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def _current():
    return SimpleNamespace(
        provider=SimpleNamespace(label="Ollama"),
        model="gemma2:9b",
        mode=SimpleNamespace(value="hootlwo"),
    )


# --- F1: the Duckln-self grounded handler --------------------------------------

class F1DucklnSelf(unittest.TestCase):
    def test_repo_library_is_grounded_in_the_catalog(self):
        from duckln import main

        with tempfile.TemporaryDirectory() as td:
            seen: list[str] = []
            for msg in ("how many repos do you have access to", "list your repos",
                        "do you have a repo library", "what repos can you set up"):
                seen.clear()
                handled = main._maybe_answer_duckln_self(msg, _current(), Path(td), seen.append)
                self.assertTrue(handled, msg)
                self.assertIn("/repos", seen[0], msg)
                self.assertRegex(seen[0], r"library of \d+ repos", msg)
                # NEVER the hallucinated disclaimer.
                self.assertNotIn("language model", seen[0].lower(), msg)
                self.assertNotIn("don't have access", seen[0].lower(), msg)

    def test_capabilities_answer_is_grounded(self):
        from duckln import main

        seen: list[str] = []
        handled = main._maybe_answer_duckln_self("what can you do", _current(), Path("/tmp"), seen.append)
        self.assertTrue(handled)
        self.assertIn("set up", seen[0].lower())
        self.assertIn("S0", seen[0])

    def test_capability_verbs_route_to_self_not_an_action(self):
        from duckln import main

        for msg in ("can you run commands", "do you support docker", "can you use a gpu",
                    "can you clone a repo", "are you connected to github"):
            seen: list[str] = []
            self.assertTrue(main._maybe_answer_duckln_self(msg, _current(), Path("/tmp"), seen.append), msg)
            self.assertTrue(seen and seen[0].strip(), msg)

    def test_identity_names_the_live_model_and_mode(self):
        from duckln import main

        for msg in ("who are you", "what model are you", "what is duckln"):
            seen: list[str] = []
            self.assertTrue(main._maybe_answer_duckln_self(msg, _current(), Path("/tmp"), seen.append), msg)
            self.assertIn("Duckln", seen[0], msg)
        # identity answer includes the live model / provider / mode
        seen = []
        main._maybe_answer_duckln_self("what model are you", _current(), Path("/tmp"), seen.append)
        self.assertIn("gemma2:9b", seen[0])
        self.assertIn("Ollama", seen[0])
        self.assertIn("HOOTLWO", seen[0])

    def test_internet_state_is_grounded_on_the_toggle(self):
        from duckln import main, internet_skill

        with patch.object(internet_skill, "is_internet_enabled", return_value=False):
            seen: list[str] = []
            self.assertTrue(main._maybe_answer_duckln_self("can you access the internet", _current(), Path("/tmp"), seen.append))
            self.assertIn("/internet on", seen[0])
        with patch.object(internet_skill, "is_internet_enabled", return_value=True):
            seen = []
            self.assertTrue(main._maybe_answer_duckln_self("do you have internet", _current(), Path("/tmp"), seen.append))
            self.assertIn("ON", seen[0])

    def test_unrelated_question_falls_through(self):
        from duckln import main

        for msg in ("how does recursion work", "explain how CUDA works", "what is a hash map"):
            self.assertFalse(main._maybe_answer_duckln_self(msg, _current(), Path("/tmp"), lambda _m: None), msg)


# --- F2b: the recommendation detector -----------------------------------------

class F2bRecommendation(unittest.TestCase):
    def test_ambiguous_ask_clarifies_exactly_once(self):
        from duckln import main

        for msg in ("recommend a repo", "which repo should I try", "suggest a repo to try", "any cool repos?"):
            seen: list[str] = []
            self.assertTrue(main._maybe_answer_recommendation(msg, Path("/tmp"), seen.append), msg)
            self.assertEqual(len(seen), 1, msg)  # exactly ONE clarifying message, not a loop
            self.assertIn("?", seen[0], msg)

    def test_domain_specified_answers_from_the_catalog(self):
        from duckln import main

        with tempfile.TemporaryDirectory() as td:
            for msg, needle in (("recommend an ML repo", "machine learning"),
                                ("best repo for image generation", "image generation"),
                                ("find me a good audio repo", "audio"),
                                ("top 5 ml repos", "machine learning")):
                seen: list[str] = []
                self.assertTrue(main._maybe_answer_recommendation(msg, Path(td), seen.append), msg)
                self.assertIn("From my catalog", seen[0], msg)
                self.assertIn(needle, seen[0].lower(), msg)
                # answers, does not clarify
                self.assertNotIn("what are you into", seen[0].lower(), msg)

    def test_non_recommendation_falls_through(self):
        from duckln import main

        for msg in ("how does recursion work", "how many repos do you have", "set up AutoGPT"):
            self.assertFalse(main._maybe_answer_recommendation(msg, Path("/tmp"), lambda _m: None), msg)


# --- F2: the classifier prompt (self + recommendation never act on the repo) --

class F2Classifier(unittest.TestCase):
    def test_prompt_routes_self_and_recommendation_to_social(self):
        from duckln.conversation_routes import llm_intent

        p = llm_intent._SYSTEM_PROMPT.lower()
        self.assertIn("duckln itself", p)
        self.assertIn("recommend", p)
        # explicitly forbids the dangerous routes for these classes
        self.assertIn("never", p)
        self.assertIn("repo_action", p)
        self.assertIn("repo_question", p)

    def test_still_four_routes(self):
        from duckln.conversation_routes import llm_intent

        self.assertEqual(llm_intent._VALID, ("repo_question", "repo_action", "technical", "social"))


# --- F3: technical-prompt identity guard + one-shot example --------------------

class F3TechnicalGuard(unittest.TestCase):
    def test_guard_and_one_shot_example_present(self):
        from duckln import main

        p = main._TECH_SYSTEM_CLEAR
        self.assertIn("NEVER claim to be a disconnected language model", p)
        self.assertIn("/repos", p)
        # a worked one-shot example anchoring the presentation shape
        self.assertIn("Example", p)
        self.assertIn("hash map", p.lower())


if __name__ == "__main__":
    unittest.main()
