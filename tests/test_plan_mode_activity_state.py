"""Plan 72 Phase 7 — activity bar clears on terminal/exception (no stuck spinner)."""

from __future__ import annotations

import unittest


class _FakeChat:
    def __init__(self):
        self.activity = ""
        self.spinner = False

    def set_activity_text(self, message, *, spinner=False):
        self.activity = message
        self.spinner = spinner

    def clear_activity_text(self):
        self.activity = ""
        self.spinner = False


class ActivityScopeTest(unittest.TestCase):
    def test_scope_clears_on_clean_exit(self) -> None:
        from duckln.main import _chat_activity_scope

        chat = _FakeChat()
        with _chat_activity_scope(chat, "Working... x", spinner=True):
            self.assertTrue(chat.spinner)
        self.assertEqual(chat.activity, "")
        self.assertFalse(chat.spinner)

    def test_scope_clears_on_exception(self) -> None:
        from duckln.main import _chat_activity_scope

        chat = _FakeChat()
        with self.assertRaises(RuntimeError):
            with _chat_activity_scope(chat, "Working... x", spinner=True):
                raise RuntimeError("boom")
        self.assertEqual(chat.activity, "")
        self.assertFalse(chat.spinner)

    def test_lifecycle_clears_activity_on_terminal(self) -> None:
        from duckln.plan_lifecycle import PlanLifecycle, PlanLifecycleState

        chat = _FakeChat()
        chat.set_activity_text("Working... planning", spinner=True)
        lc = PlanLifecycle(on_clear_activity=chat.clear_activity_text, persist=lambda s: None)
        lc.advance(PlanLifecycleState.CONTEXT_COLLECTED)
        lc.terminal(PlanLifecycleState.BLOCKED_EXTERNAL)
        self.assertEqual(chat.activity, "")
        self.assertFalse(chat.spinner)


if __name__ == "__main__":
    unittest.main()
