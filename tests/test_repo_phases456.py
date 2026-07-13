from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from duckln.repo_agent import classify_repo_agent_intent
from state.access import read_repo_memory_ranked, write_repo_memory
from state.store import FileMemoryStore, MemoryStore, initialize_state_store, set_state_store_factory


# --- Phase 5: relevance-ranked memory + file backend -------------------------


class TestRankedMemory(unittest.TestCase):
    def test_ranked_recall_by_relevance_not_recency(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_repo_memory(Path(tmp), repo_slug="r", question="how does authentication login work", answer="see auth.py")
            write_repo_memory(Path(tmp), repo_slug="r", question="what is the database schema", answer="see db.py")
            write_repo_memory(Path(tmp), repo_slug="r", question="how to format code", answer="prettier")
            top = read_repo_memory_ranked(Path(tmp), "r", query="explain the authentication flow", top_k=1)
            self.assertEqual(len(top), 1)
            self.assertIn("auth", top[0]["a"])  # relevance beat recency

    def test_file_backend_satisfies_protocol_and_roundtrips(self):
        with tempfile.TemporaryDirectory() as tmp:
            try:
                set_state_store_factory(lambda cfg: FileMemoryStore(cfg))
                store = initialize_state_store(Path(tmp))
                self.assertIsInstance(store, MemoryStore)
                store.upsert_config_values({"k::a": "1", "k::b": "2", "other": "x"})
                self.assertEqual(initialize_state_store(Path(tmp)).read_config_values(prefix="k::"), {"k::a": "1", "k::b": "2"})
            finally:
                set_state_store_factory(None)


# --- Phase 6: auto-route intent classifier -----------------------------------


class TestIntentClassifier(unittest.TestCase):
    def test_action_intent(self):
        for m in ("fix the login bug", "add a docstring to utils.py", "refactor the parser", "run the tests"):
            self.assertEqual(classify_repo_agent_intent(m), "do", m)

    def test_question_intent(self):
        # Plan 189 F3: DEFINITIVE repo cues stay deterministic; "what does"/"explain" defer to the LLM.
        for m in ("where is the entry point?", "which file handles routing", "show me the router", "locate the tests"):
            self.assertEqual(classify_repo_agent_intent(m), "ask", m)

    def test_non_repo_chat_is_empty(self):
        for m in ("hello", "thanks!", "/repos", "good morning"):
            self.assertEqual(classify_repo_agent_intent(m), "")


if __name__ == "__main__":
    unittest.main()
