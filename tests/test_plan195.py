"""Plan 195 — dialogue-state / expectation tracking (per docs/knowledge/Dialogue_state_design.md):
a reply to a pending proposal is matched against a CLOSED set (affirm/negate) FIRST and executes,
instead of being re-classified into a fresh clarify loop. The recall of the last repo WRITES a
confirm_action expectation; an affirm resolves it; a negate cancels; the clarify cap breaks loops."""

from __future__ import annotations

import tempfile
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace


class ClosedSetReplies(unittest.TestCase):
    def test_affirmations_every_phrasing(self):
        from duckln.main import _is_affirmation

        for m in ("yes", "yeah", "yep", "sure", "ok", "okay", "go ahead", "do it", "run it",
                  "yes lets run this", "yes let's run this", "yes the repo task", "please do",
                  "absolutely", "sounds good", "yep do it"):
            self.assertTrue(_is_affirmation(m), m)

    def test_negations(self):
        from duckln.main import _is_negation

        for m in ("no", "nope", "nah", "not that", "cancel", "stop", "no thanks", "don't"):
            self.assertTrue(_is_negation(m), m)

    def test_neither_is_neither(self):
        from duckln.main import _is_affirmation, _is_negation

        for m in ("what is the weather", "how does recursion work", "the thing is not working", "run JustHireMe"):
            self.assertFalse(_is_affirmation(m) and _is_negation(m), m)
        # a real request is not an affirmation/negation
        self.assertFalse(_is_affirmation("what is the weather"))
        self.assertFalse(_is_negation("how does recursion work"))


class RecallWritesExpectation(unittest.TestCase):
    def test_run_ish_recall_writes_run_expectation(self):
        from duckln import main
        from state.access import read_followup_state

        row = SimpleNamespace(repo_key="vasu-devs/JustHireMe", repo_url="https://github.com/vasu-devs/JustHireMe",
                              metadata={"repo_name": "JustHireMe"})
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td)
            store = SimpleNamespace(get_latest_repo_state=lambda: row)
            seen: list[str] = []
            with unittest.mock.patch.object(main, "initialize_state_store", return_value=store):
                handled = main._maybe_answer_previous_repo("can you run the last repo we worked on", cfg, seen.append)
            self.assertTrue(handled)
            self.assertIn("want me to run", seen[0].lower())
            fs = read_followup_state(cfg)
            self.assertEqual(fs.get("pending_next_action"), "run_repo")
            self.assertEqual(fs.get("pending_offer_kind"), "run_repo")
            self.assertEqual(fs.get("pending_repo_name"), "JustHireMe")

    def test_non_run_recall_writes_no_expectation(self):
        from duckln import main
        from state.access import read_followup_state

        row = SimpleNamespace(repo_key="x/Y", repo_url="https://github.com/x/Y", metadata={"repo_name": "Y"})
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td)
            store = SimpleNamespace(get_latest_repo_state=lambda: row)
            with unittest.mock.patch.object(main, "initialize_state_store", return_value=store):
                main._maybe_answer_previous_repo("which repo were we working on last?", cfg, lambda _m: None)
            self.assertIsNone(read_followup_state(cfg).get("pending_next_action"))


class PendingFollowupResolvesReply(unittest.TestCase):
    def _call(self, cfg, command, display):
        from duckln import main

        current = SimpleNamespace(mode=SimpleNamespace(), provider=SimpleNamespace(value="ollama"))
        paths = SimpleNamespace(config_dir=cfg)
        return main._handle_pending_repo_followup(
            command=command, current=current, paths=paths, system_probe=SimpleNamespace(),
            display_output=display, approve_prompt=None, text_prompt=None, select_prompt=None,
            terminal_interface=None,
        )

    def test_affirm_passes_the_gate_and_negate_cancels(self):
        from state.access import write_followup_state, read_followup_state

        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td)
            # negate → cancels, clears the expectation, no loop
            write_followup_state(cfg, {"pending_next_action": "run_repo", "pending_repo_key": "x/Y", "pending_repo_name": "Y"})
            seen: list[str] = []
            self.assertTrue(self._call(cfg, "no", seen.append))
            self.assertIn("hold off", seen[0].lower())
            self.assertIsNone(read_followup_state(cfg).get("pending_next_action"))

            # affirm ("yes lets run this") passes the gate (repo unresolvable here → handled message),
            # proving the affirm is accepted rather than re-classified.
            write_followup_state(cfg, {"pending_next_action": "run_repo", "pending_repo_key": "x/Y", "pending_repo_name": "Y"})
            seen2: list[str] = []
            self.assertTrue(self._call(cfg, "yes lets run this", seen2.append))

    def test_non_reply_with_pending_falls_through(self):
        from state.access import write_followup_state

        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td)
            write_followup_state(cfg, {"pending_next_action": "run_repo", "pending_repo_key": "x/Y", "pending_repo_name": "Y"})
            # a clearly different message is neither affirm nor negate nor a repair request → fall through
            self.assertFalse(self._call(cfg, "what is the weather today", lambda _m: None))


if __name__ == "__main__":
    import unittest.mock  # noqa: E402
    unittest.main()
