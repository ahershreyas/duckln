"""Plan 72 Phase 9 — situation-specific clarification, no repeated/stale questions."""

from __future__ import annotations

import unittest

from duckln.plan_mode import (
    ClarificationQuestion,
    clarification_signature,
    filter_repeat_clarifications,
)


def _q(text):
    return ClarificationQuestion(text=text, options=(), default=None, applies_to_step_indices=())


class ClarificationDedupeTest(unittest.TestCase):
    def test_within_batch_dedupe(self) -> None:
        out = filter_repeat_clarifications((_q("Which Node version?"), _q("which node version?")))
        self.assertEqual(len(out), 1)

    def test_drops_already_asked(self) -> None:
        out = filter_repeat_clarifications(
            (_q("Which AWS region?"),), already_asked=("which aws region?",)
        )
        self.assertEqual(out, ())

    def test_keeps_distinct(self) -> None:
        out = filter_repeat_clarifications((_q("Which region?"), _q("Which instance type?")))
        self.assertEqual(len(out), 2)

    def test_signature_normalizes_whitespace_case(self) -> None:
        self.assertEqual(
            clarification_signature(_q("  Which   Region? ")),
            clarification_signature(_q("which region?")),
        )


if __name__ == "__main__":
    unittest.main()
