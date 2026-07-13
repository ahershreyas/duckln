"""Plan 191 — the adaptive clarification engine: the LLM decides HOW MANY questions (1, hard cap 4),
asks CLEVER grounded option-questions (radio + always Other + Esc-cancel), reasons CONTINUE-vs-NEW on
a typed reply, and STRONGLY records the reasoning to logical-thinking.md; one reusable module wired
into the recommendation flow; the shared choice overlay is upgraded to numbered options (F6)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


_DOMAINS = ("machine learning / LLMs", "image generation", "audio / speech", "web apps", "chatbots / agents")


class _FakeChannel:
    def __init__(self, answers, *, overlay=True):
        self.answers = list(answers)
        self.overlay = overlay
        self.asked = []

    def supports_overlay(self):
        return self.overlay

    def select_choice(self, text, opts, *, allow_other=False, other_placeholder=""):
        self.asked.append((text, tuple(opts), allow_other))
        return self.answers.pop(0) if self.answers else None


def _facts():
    from duckln.clarify import ClarifyFacts

    return ClarifyFacts(topic="what you want to build or run", options=_DOMAINS)


# --- F1: the adaptive engine ---------------------------------------------------

class F1Engine(unittest.TestCase):
    def test_resolves_in_one_question_and_passes_allow_other(self):
        from duckln.clarify import run_clarification

        def llm(*, system_prompt, user_message):
            if "(none yet)" in user_message:
                return json.dumps({"enough": False, "question": {"text": "What are you building?", "options": list(_DOMAINS[:4])}})
            return json.dumps({"enough": True, "resolution": "image generation"})

        with tempfile.TemporaryDirectory() as td:
            ch = _FakeChannel(["image generation"])
            r = run_clarification(ask="recommend a repo", facts=_facts(), channel=ch,
                                  config_dir=Path(td), llm_client=llm, display=lambda _m: None)
        self.assertTrue(r.resolved)
        self.assertEqual(r.value, "image generation")
        self.assertEqual(r.rounds_asked, 1)
        self.assertTrue(ch.asked[0][2])  # allow_other=True was passed to the overlay

    def test_hard_cap_at_four_even_if_llm_never_stops(self):
        from duckln.clarify import MAX_ROUNDS, run_clarification

        def llm(*, system_prompt, user_message):
            return json.dumps({"enough": False, "question": {"text": "more?", "options": list(_DOMAINS[:4])}})

        with tempfile.TemporaryDirectory() as td:
            ch = _FakeChannel(["a", "b", "c", "d", "e", "f"])
            r = run_clarification(ask="x", facts=_facts(), channel=ch, config_dir=Path(td),
                                  llm_client=llm, display=lambda _m: None)
        self.assertEqual(MAX_ROUNDS, 4)
        self.assertLessEqual(r.rounds_asked, 4)
        self.assertLessEqual(len(ch.asked), 4)
        self.assertTrue(r.resolved)  # resolves best-effort at the cap, never loops forever

    def test_esc_cancels_gracefully(self):
        from duckln.clarify import run_clarification

        def llm(*, system_prompt, user_message):
            return json.dumps({"enough": False, "question": {"text": "q", "options": list(_DOMAINS[:4])}})

        with tempfile.TemporaryDirectory() as td:
            ch = _FakeChannel([None])
            r = run_clarification(ask="x", facts=_facts(), channel=ch, config_dir=Path(td),
                                  llm_client=llm, display=lambda _m: None)
        self.assertTrue(r.cancelled)
        self.assertFalse(r.resolved)

    def test_no_model_floor_asks_one_grounded_question(self):
        from duckln.clarify import run_clarification

        with tempfile.TemporaryDirectory() as td:
            ch = _FakeChannel(["web apps"])
            r = run_clarification(ask="x", facts=_facts(), channel=ch, config_dir=Path(td),
                                  llm_client=None, display=lambda _m: None)
        self.assertTrue(r.resolved)
        self.assertEqual(r.value, "web apps")
        self.assertEqual(len(ch.asked), 1)  # exactly ONE question, never a loop, never dumb
        self.assertEqual(ch.asked[0][1], _DOMAINS[:4])  # grounded options from the facts

    def test_no_overlay_returns_a_pending_question_for_the_text_path(self):
        from duckln.clarify import run_clarification

        with tempfile.TemporaryDirectory() as td:
            ch = _FakeChannel([], overlay=False)
            r = run_clarification(ask="x", facts=_facts(), channel=ch, config_dir=Path(td),
                                  llm_client=None, display=lambda _m: None)
        self.assertIsNotNone(r.pending_question)
        self.assertFalse(r.resolved)
        self.assertFalse(r.cancelled)


# --- F2: continue-vs-new -------------------------------------------------------

class F2ContinueOrNew(unittest.TestCase):
    def test_deterministic_floor(self):
        from duckln.clarify import decide_continue_or_new

        self.assertEqual(decide_continue_or_new(message="the second one", pending_question="q", options=_DOMAINS), "continue")
        self.assertEqual(decide_continue_or_new(message="image generation", pending_question="q", options=_DOMAINS), "continue")
        self.assertEqual(decide_continue_or_new(message="set up AutoGPT", pending_question="q", options=_DOMAINS), "new")
        self.assertEqual(decide_continue_or_new(message="https://github.com/x/y", pending_question="q", options=_DOMAINS), "new")
        self.assertEqual(decide_continue_or_new(message="/repos", pending_question="q", options=_DOMAINS), "new")

    def test_resume_pending_clarify_continue_then_new(self):
        from duckln import main

        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td)
            main._persist_pending_clarify(cfg, question="What are you building?", options=_DOMAINS)
            seen: list[str] = []
            # a bare option-answer CONTINUES → recommends (handled)
            handled = main._maybe_resume_pending_clarify("image generation", cfg, seen.append, current=SimpleNamespace(model=None))
            self.assertTrue(handled)
            self.assertTrue(seen and ("From my catalog" in seen[0] or "catalog" in seen[0].lower()))
            # the pending clarify is a single-shot gate — cleared after use
            handled2 = main._maybe_resume_pending_clarify("image generation", cfg, lambda _m: None, current=SimpleNamespace(model=None))
            self.assertFalse(handled2)

            # a NEW request does NOT continue (falls through)
            main._persist_pending_clarify(cfg, question="What are you building?", options=_DOMAINS)
            handled3 = main._maybe_resume_pending_clarify("set up AutoGPT", cfg, lambda _m: None, current=SimpleNamespace(model=None))
            self.assertFalse(handled3)


# --- F3: reasoning recorded to logical-thinking.md -----------------------------

class F3ThinkingLog(unittest.TestCase):
    def test_clarification_writes_an_episode(self):
        from duckln.clarify import run_clarification

        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td)
            ch = _FakeChannel(["web apps"])
            run_clarification(ask="recommend a repo", facts=_facts(), channel=ch, config_dir=cfg,
                              llm_client=None, display=lambda _m: None)
            log = cfg / "memory" / "logical-thinking.md"
            self.assertTrue(log.exists())
            text = log.read_text(encoding="utf-8")
            self.assertIn("clarify", text.lower())
            self.assertIn("Clarifying: recommend a repo", text)


# --- F4: recommendation wired to the engine ------------------------------------

class F4Recommendation(unittest.TestCase):
    def test_named_domain_recommends_straight_no_question(self):
        from duckln import main

        with tempfile.TemporaryDirectory() as td:
            seen: list[str] = []
            handled = main._maybe_answer_recommendation("best repo for image generation", Path(td), seen.append)
            self.assertTrue(handled)
            self.assertIn("From my catalog", seen[0])

    def test_ambiguous_uses_the_engine_overlay_when_available(self):
        from duckln import main

        with tempfile.TemporaryDirectory() as td:
            seen: list[str] = []
            ch = _FakeChannel(["web apps"])
            handled = main._maybe_answer_recommendation("recommend a repo", Path(td), seen.append,
                                                        channel=ch, current=SimpleNamespace(model=None))
            self.assertTrue(handled)
            self.assertTrue(ch.asked)  # it asked via the radio overlay
            self.assertTrue(ch.asked[0][2])  # with allow_other (the inline Other row)
            self.assertTrue(any("catalog" in s.lower() for s in seen))  # then recommended

    def test_ambiguous_cancel_is_graceful(self):
        from duckln import main

        with tempfile.TemporaryDirectory() as td:
            seen: list[str] = []
            ch = _FakeChannel([None])  # Esc
            handled = main._maybe_answer_recommendation("recommend a repo", Path(td), seen.append,
                                                        channel=ch, current=SimpleNamespace(model=None))
            self.assertTrue(handled)
            self.assertIn("/repos", seen[-1])

    def test_non_recommendation_falls_through(self):
        from duckln import main

        self.assertFalse(main._maybe_answer_recommendation("how does recursion work", Path("/tmp"), lambda _m: None))


# --- F6: the overlay UI (numbered options) -------------------------------------

class F6OverlayLabel(unittest.TestCase):
    def test_options_are_numbered_and_legacy_bullet_kept(self):
        from duckln.textual_ui import _overlay_choice_label

        self.assertEqual(_overlay_choice_label("An LLM / chatbot", index=0), "1  An LLM / chatbot")
        self.assertEqual(_overlay_choice_label("Image generation", index=1), "2  Image generation")
        self.assertEqual(_overlay_choice_label("plain"), "• plain")  # callers without an index keep the bullet


if __name__ == "__main__":
    unittest.main()
