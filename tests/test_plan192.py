"""Plan 192 — the 4-tier intent-routing cascade (per docs/knowledge/intent_routing_design.md):
F1 Tier-1 chat-contraction normalization (fixes "how r you"); F2 the flipped fallback — no repo
SIGNAL ⇒ conversation, never the repo clarifier; F3 the Tier-3 context+confidence classifier (the
doc's verbatim prompts); F4 the Tier-4 right-axis clarify (verbatim prompt); plus the doc §10
regression battery. Deterministic tiers 1–2 are proven here; tiers 3–4 are proven on a real model
in test_ollama_routing_it.Plan192RoutingIT."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace


# --- F1: Tier-1 normalization --------------------------------------------------

class F1Normalization(unittest.TestCase):
    def test_how_r_you_normalizes_to_conversation(self):
        from duckln.conversation_routes.normalizer import classify_conversation_intent, normalize_compact_message

        self.assertEqual(normalize_compact_message("how r you ?"), "how are you ?")
        self.assertEqual(classify_conversation_intent("how r you ?"), "rapport")

    def test_common_contractions_expand(self):
        from duckln.conversation_routes.normalizer import normalize_compact_message

        self.assertEqual(normalize_compact_message("u good?"), "you good?")
        self.assertEqual(normalize_compact_message("ur repo"), "your repo")
        self.assertEqual(normalize_compact_message("plz help"), "please help")
        self.assertEqual(normalize_compact_message("hows it going"), "how is it going")

    def test_whole_token_only_never_inside_words(self):
        from duckln.conversation_routes.normalizer import normalize_compact_message

        # "run"/"your"/"install" must be untouched (no substring expansion of r/u).
        self.assertEqual(normalize_compact_message("run install your"), "run install your")


# --- F2: has_repo_signal + the flipped fallback --------------------------------

class F2RepoSignal(unittest.TestCase):
    def test_positive_signals(self):
        from duckln.conversation_routes.normalizer import has_repo_signal, normalize_compact_message

        for m in ("set up JustHireMe", "clone github.com/x/y", "fix the failing test", "run the app",
                  "install requests", "open src/main.py", "build the project", "vasu-devs/JustHireMe"):
            self.assertTrue(has_repo_signal(normalize_compact_message(m)), m)

    def test_no_signal_for_small_talk_and_status(self):
        from duckln.conversation_routes.normalizer import has_repo_signal, normalize_compact_message

        for m in ("how are you", "how r you", "tell me a joke", "thanks!", "wassup", "u good?",
                  "what can you do", "is it ready?", "when will this be done?"):
            self.assertFalse(has_repo_signal(normalize_compact_message(m)), m)

    def test_router_no_signal_routes_to_conversation_not_repo_clarifier(self):
        # End-to-end through the real router (duck-typed context): "how r you?" with an ACTIVE repair
        # objective must NOT produce the repo clarifier — it must be conversation.
        from duckln.conversation_routes.normalizer import normalize_conversation_turn
        from duckln.conversation_routes.router import route_conversation
        from duckln.conversation_routes.recovery import (
            clarification_options_for_candidates as _cand,
            clarification_options_for_message as _msg,
        )

        followup = SimpleNamespace(pending_clarification_options=(), active_topic=None, pending_offer_kind=None,
                                   pending_offer_id=None, pending_offer_thread_id=None, active_thread_id=None,
                                   last_supervisor_decision=None, pending_next_action=None, pending_repo_key=None,
                                   pending_repo_name=None)
        thread = SimpleNamespace(active_repo_name="JustHireMe", active_topic=None)
        workflow = SimpleNamespace(active_issue_kind="runtime_repair", repo_name="JustHireMe")
        ctx = SimpleNamespace(
            records=(), mentioned_repo=None, active_repo=None, platform_hint="", machine_explanation="",
            execution_target="local", active_vm_name=None, requested_target_name=None, vm_inventory_snapshot=(),
            agent_context=SimpleNamespace(), repo_knowledge=None, repo_state_snapshot=None, active_repo_snapshot=None,
            repo_inventory_snapshot=(), live_repo_sessions_snapshot=(), workflow_state=workflow,
            recommendation_memory=None, recent_thread_summary=None, project_summary=None, promoted_heuristics=(),
            followup_state=followup, thread_state=thread,
            live_thread=SimpleNamespace(), pending_offer=SimpleNamespace(kind=None),
            durable_recommendation=SimpleNamespace(primary_repo=None, secondary_repo=None),
            durable_repo_memory=None,
        )
        turn = normalize_conversation_turn("how r you ?")
        decision = route_conversation(
            turn, context=ctx, recent_turns=(), recent_replies=(), config_dir=None,
            resolve_followup_intent=lambda text, fs: None,
            resolve_corrected_intent=lambda text, bi, rt, rr, cd: bi,
            clarification_options_for_message=lambda text, c: _msg(text, context=c, looks_like_repo_pronoun_followup=lambda _t: False),
            clarification_options_for_candidates=lambda cands, c, t, ex: _cand(cands, context=c, normalized_compact=t, existing=ex),
            phrase_matches=lambda text, phrase: phrase in text,
            looks_like_command_surface_request=lambda _t: False,
            looks_like_repo_pronoun_followup=lambda _t: False,
            looks_like_social_or_identity_turn=lambda _t: True,
            looks_like_next_step_question=lambda _t: False,
        )
        self.assertNotEqual(decision.route_family, "clarify", "how r you must NOT hit the repo clarifier")
        self.assertFalse(decision.requires_clarification)


# --- F3: Tier-3 classifier (verbatim prompts, confidence) ----------------------

class F3Tier3Classifier(unittest.TestCase):
    def _client(self, intent, conf):
        return lambda **kw: json.dumps({"intent": intent, "confidence": conf, "rationale": "x"})

    def test_parses_and_thresholds(self):
        from duckln.conversation_routes.llm_intent import TIER3_CONFIDENCE_THRESHOLD, classify_intent_with_context

        self.assertEqual(TIER3_CONFIDENCE_THRESHOLD, 0.6)
        self.assertEqual(classify_intent_with_context("hi", llm_client=self._client("conversation", 0.95))[0], "conversation")
        self.assertEqual(classify_intent_with_context("set up x", llm_client=self._client("repo_task", 0.9))[0], "repo_task")
        i, c, _ = classify_intent_with_context("is it ready?", llm_client=self._client("ambiguous", 0.3))
        self.assertEqual(i, "ambiguous")
        self.assertLess(c, TIER3_CONFIDENCE_THRESHOLD)

    def test_no_model_and_garbage_are_ambiguous(self):
        from duckln.conversation_routes.llm_intent import classify_intent_with_context

        self.assertEqual(classify_intent_with_context("hi", llm_client=None)[0], "ambiguous")
        self.assertEqual(classify_intent_with_context("hi", llm_client=lambda **k: "not json")[0], "ambiguous")

    def test_prompt_is_verbatim_from_the_doc(self):
        from duckln.conversation_routes.llm_intent import _TIER3_SYSTEM_PROMPT

        # Key sentences from docs/knowledge/intent_routing_design.md §5 must be present unchanged.
        self.assertIn("You are Duckln's intent classifier.", _TIER3_SYSTEM_PROMPT)
        self.assertIn('{"intent": "repo_task|status|conversation|ambiguous", "confidence": 0.0-1.0, "rationale": "one short line"}', _TIER3_SYSTEM_PROMPT)
        self.assertIn("most greetings are", _TIER3_SYSTEM_PROMPT)


# --- F4: Tier-4 right-axis clarify (verbatim prompt) ---------------------------

class F4Tier4Clarify(unittest.TestCase):
    def test_returns_one_line_and_floor_when_no_model(self):
        from duckln.conversation_routes.llm_intent import phrase_axis_clarification

        out = phrase_axis_clarification("hmm", llm_client=lambda **k: "Repo job or just chatting?")
        self.assertEqual(out, "Repo job or just chatting?")
        floor = phrase_axis_clarification("hmm", llm_client=None)
        self.assertIn("repo task", floor.lower())
        self.assertIn("chatting", floor.lower())
        # Never the repo-only status/path clarifier.
        self.assertNotIn("status", floor.lower())
        self.assertNotIn("path", floor.lower())

    def test_prompt_is_verbatim_from_the_doc(self):
        from duckln.conversation_routes.llm_intent import _TIER4_SYSTEM_PROMPT

        self.assertIn("Do NOT assume it is about a repository.", _TIER4_SYSTEM_PROMPT)
        self.assertIn('Do NOT list repo-specific options (like "status or path")', _TIER4_SYSTEM_PROMPT)
        self.assertIn("Output ONLY the clarifying question text", _TIER4_SYSTEM_PROMPT)


# --- Doc §10 regression battery (deterministic layer) --------------------------

class DocBattery(unittest.TestCase):
    def test_conversation_messages_carry_no_repo_signal(self):
        from duckln.conversation_routes.normalizer import has_repo_signal, normalize_compact_message

        for m in ("how r you?", "r you ok?", "u good?", "wassup", "tell me a joke", "thanks!"):
            self.assertFalse(has_repo_signal(normalize_compact_message(m)), m)

    def test_repo_tasks_carry_a_repo_signal(self):
        from duckln.conversation_routes.normalizer import has_repo_signal, normalize_compact_message

        for m in ("set up JustHireMe", "clone github.com/x/y", "fix the failing test"):
            self.assertTrue(has_repo_signal(normalize_compact_message(m)), m)

    def test_status_shaped_questions_are_detected(self):
        from duckln.main import _looks_like_status_question
        from duckln.conversation_routes.normalizer import normalize_compact_message

        for m in ("is it ready?", "when will this be done?", "how long?", "are we done?"):
            self.assertTrue(_looks_like_status_question(normalize_compact_message(m)), m)
        self.assertFalse(_looks_like_status_question(normalize_compact_message("tell me a joke")))

    def test_common_chatter_classifies_as_conversation_not_repo(self):
        # doc §10: r you ok? / u good? / wassup / thanks! / tell me a joke → conversation.
        from duckln.conversation_routes.normalizer import classify_conversation_intent, normalize_compact_message

        _CONV = {"rapport", "greeting", "smalltalk"}
        for m in ("how r you?", "r you ok?", "u good?", "wassup", "thanks!", "thank you",
                  "tell me a joke", "how is it going"):
            self.assertIn(classify_conversation_intent(normalize_compact_message(m)), _CONV, m)
        # a real VM/repo recommendation with a "yo" typo must NOT be captured as a greeting.
        self.assertNotIn(classify_conversation_intent(normalize_compact_message("which repo do yo recommend to put in vm?")),
                         _CONV)

    def test_workflow_anchored_repo_clarifier_gated_on_a_repo_signal(self):
        # Plan 192 F2: the dangerous "continue repairing {repo} / status / path" clarifier must NOT be
        # produced for a signal-less message even with an active repair objective; a repo-signal
        # (or lifecycle) turn still gets it.
        from duckln.conversation_routes.recovery import clarification_options_for_message

        workflow = SimpleNamespace(active_issue_kind="runtime_repair", repo_name="JustHireMe")
        thread = SimpleNamespace(active_repo_name="JustHireMe", active_topic=None)
        followup = SimpleNamespace(pending_clarification_options=())
        ctx = SimpleNamespace(mentioned_repo=None, active_repo=None, workflow_state=workflow,
                              thread_state=thread, followup_state=followup)

        no_signal = clarification_options_for_message("how are you", context=ctx,
                                                      looks_like_repo_pronoun_followup=lambda _t: False)
        self.assertFalse(any("continue repairing" in o.label.lower() for o in no_signal))

        with_signal = clarification_options_for_message("fix the build now", context=ctx,
                                                        looks_like_repo_pronoun_followup=lambda _t: False)
        self.assertTrue(any("continue repairing" in o.label.lower() for o in with_signal))


if __name__ == "__main__":
    unittest.main()
