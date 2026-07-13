"""Plan 193 — context understanding as a context-engineering pipeline (per docs/knowledge/context_engg.md):
F1 WRITE a bounded/redacted incident + recent-events scratchpad; F2 SELECT the incident slice and
answer FROM it ("what is the issue?"); F3 the §7 responder slice; F7 the finished Tier-2 flip
(no repo signal ⇒ conversation, never a repo clarifier) + F7b greetings skip the LLM classifier."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


# --- F1: WRITE the incident scratchpad (bounded, redacted) --------------------

class F1Write(unittest.TestCase):
    def test_write_active_incident_and_recent_events(self):
        from state.access import read_workflow_state, write_active_incident

        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td)
            write_active_incident(cfg, summary="The VM 'duckln-vm' isn't accessible, so setup couldn't start.",
                                  category="vm_not_accessible", command="multipass info duckln-vm",
                                  excerpt="VM 'duckln-vm' is not accessible (state Unknown). Check with: multipass list")
            wf = read_workflow_state(cfg)
            self.assertIn("accessible", str(wf.get("active_incident_summary")))
            self.assertEqual(wf.get("active_incident_category"), "vm_not_accessible")
            self.assertIsInstance(wf.get("recent_events"), list)
            self.assertTrue(wf.get("recent_events"))

    def test_recent_events_is_bounded_and_redacted(self):
        from state.access import append_recent_event, read_workflow_state

        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td)
            for i in range(9):
                append_recent_event(cfg, f"event number {i}")
            events = read_workflow_state(cfg).get("recent_events")
            self.assertLessEqual(len(events), 5)  # bounded ring
            # a secret in an event is redacted
            append_recent_event(cfg, "failed with token sk-abcdef1234567890abcdef")
            self.assertNotIn("sk-abcdef1234567890", " ".join(read_workflow_state(cfg).get("recent_events")))


# --- F2: SELECT the incident slice and answer FROM it -------------------------

class F2Select(unittest.TestCase):
    def test_incident_question_answers_from_the_written_incident(self):
        from duckln import main
        from state.access import write_active_incident

        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td)
            write_active_incident(cfg, summary="The VM 'duckln-vm' isn't accessible, so Duckln couldn't start the setup.",
                                  category="vm_not_accessible")
            for q in ("what is the issue?", "why did it fail?", "what happened?", "what's blocking it?", "is it fixed now?"):
                seen: list[str] = []
                self.assertTrue(main._maybe_answer_incident_question(q, cfg, seen.append), q)
                self.assertIn("duckln-vm", seen[0])
                self.assertNotIn("name the repo", seen[0].lower())
            # the VM category surfaces the concrete next step
            seen = []
            main._maybe_answer_incident_question("what is the issue?", cfg, seen.append)
            self.assertIn("/vm", seen[0])

    def test_no_incident_defers_and_non_incident_ignored(self):
        from duckln import main

        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td)
            self.assertFalse(main._maybe_answer_incident_question("what is the issue?", cfg, lambda _m: None))  # nothing written
            self.assertFalse(main._maybe_answer_incident_question("tell me a joke", cfg, lambda _m: None))

    def test_incident_slice_is_bounded_and_only_relevant(self):
        from duckln.main import _incident_context_slice
        from state.access import write_active_incident, resolve_active_incident

        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td)
            self.assertEqual(_incident_context_slice(cfg), "")  # nothing → empty (greeting selects nothing)
            write_active_incident(cfg, summary="VM duckln-vm not accessible", category="vm_not_accessible")
            self.assertIn("Active incident", _incident_context_slice(cfg))
            resolve_active_incident(cfg)
            self.assertNotIn("Active incident", _incident_context_slice(cfg))  # resolved → not surfaced


# --- F7 / F7b: the Tier-2 flip drives selection -------------------------------

class F7Routing(unittest.TestCase):
    def test_all_repo_clarifiers_gated_on_a_message_signal(self):
        from duckln.conversation_routes.recovery import clarification_options_for_message

        repo = SimpleNamespace(name="JustHireMe")
        workflow = SimpleNamespace(active_issue_kind=None, repo_name="JustHireMe")
        ctx = SimpleNamespace(mentioned_repo=None, active_repo=repo, workflow_state=workflow,
                              thread_state=SimpleNamespace(active_repo_name="JustHireMe"),
                              followup_state=SimpleNamespace(pending_clarification_options=()))
        # no signal + an ambient active repo → NO repo-only options ("active repo status" absent)
        no_sig = clarification_options_for_message("how you doin", context=ctx,
                                                   looks_like_repo_pronoun_followup=lambda _t: False)
        self.assertFalse(any("active repo status" in o.label.lower() for o in no_sig))
        self.assertFalse(any("repo path" in o.label.lower() for o in no_sig))
        # a repo-signal turn still gets them
        with_sig = clarification_options_for_message("fix the build", context=ctx,
                                                     looks_like_repo_pronoun_followup=lambda _t: False)
        self.assertTrue(any("active repo status" in o.label.lower() for o in with_sig))

    def test_greeting_with_active_repo_routes_to_conversation_not_clarifier(self):
        from duckln.main import _respond_to_free_text
        from state.access import write_workflow_state

        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td)
            write_workflow_state(cfg, {"active_issue_kind": "runtime_repair", "repo_name": "JustHireMe"})
            # A greeting (even unbucketed like "how you doin?") routes to conversation, not a clarifier.
            for m in ("how you doin?", "how r you ?", "how ya been"):
                r = _respond_to_free_text(m, config_dir=cfg)
                self.assertNotEqual(r.intent, "clarify", m)
                self.assertNotIn("active repo status", r.text.lower(), m)
                self.assertNotIn("continue repairing", r.text.lower(), m)

    def test_ambiguous_turn_gets_generic_clarify_not_the_dangerous_repo_one(self):
        # An ambiguous no-signal turn still ASKS (generic clarify), but NEVER the dangerous
        # repo-ASSUMING clarifier — even with an active repair objective.
        from duckln.main import _respond_to_free_text
        from state.access import write_workflow_state

        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td)
            write_workflow_state(cfg, {"active_issue_kind": "runtime_repair", "repo_name": "JustHireMe"})
            for m in ("the thing is not working", "blargle wobble maybe"):
                r = _respond_to_free_text(m, config_dir=cfg)
                self.assertNotIn("active repo status", r.text.lower(), m)
                self.assertNotIn("continue repairing", r.text.lower(), m)
                self.assertNotIn("repo path", r.text.lower(), m)

    def test_greeting_skips_the_llm_classifier(self):
        # Plan 193 F7b: a no-signal greeting must NOT pay the classifier round-trip.
        from duckln import main

        called = {"n": 0}

        def _fake_classify(message, *, llm_client):
            called["n"] += 1
            return "social"

        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td)
            current = SimpleNamespace(model="gemma2:9b", provider=SimpleNamespace(label="Ollama"),
                                      mode=SimpleNamespace(value="hootlwo"))
            paths = SimpleNamespace(config_dir=cfg)
            with patch("duckln.conversation_routes.llm_intent.classify_repo_relevance", _fake_classify), \
                 patch("duckln.ai_client.build_default_llm_client_or_none", return_value=(lambda **k: "x")):
                main._maybe_autoroute_repo_agent(command="how you doin?", current=current, paths=paths,
                                                 display=lambda _m: None, approve=None, terminal_interface=None)
        self.assertEqual(called["n"], 0)  # never classified a plain greeting


if __name__ == "__main__":
    unittest.main()
