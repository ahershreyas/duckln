from __future__ import annotations

import unittest

from duckln.textual_ui import (
    _ACTIVITY_HARD_CEILING_SECONDS,
    _PROCESSING_VERBS,
    _activity_bar_segments,
    _processing_verb,
)


class TestProcessingVerbs(unittest.TestCase):
    def test_verb_cycles_through_pool(self):
        seen = {_processing_verb(i) for i in range(len(_PROCESSING_VERBS))}
        self.assertEqual(seen, set(_PROCESSING_VERBS))
        self.assertEqual(_processing_verb(len(_PROCESSING_VERBS)), _PROCESSING_VERBS[0])

    def test_spinning_appends_rotating_verb(self):
        _, body, on = _activity_bar_segments(
            activity_text="Preparing the plan",
            spinner_on=True,
            elapsed_seconds=12,
            spinner_frame="⠹",
            spinner_index=0,
        )
        self.assertTrue(on)
        self.assertIn(f"{_PROCESSING_VERBS[0]}…", body)
        self.assertIn("12s", body)

    def test_verb_changes_with_spinner_index(self):
        _, body0, _ = _activity_bar_segments(
            activity_text="Working", spinner_on=True, elapsed_seconds=1,
            spinner_frame="⠹", spinner_index=0,
        )
        _, body1, _ = _activity_bar_segments(
            activity_text="Working", spinner_on=True, elapsed_seconds=1,
            spinner_frame="⠹", spinner_index=1,
        )
        self.assertNotEqual(body0, body1)

    def test_no_verb_when_not_spinning(self):
        _, body, on = _activity_bar_segments(
            activity_text="Idle", spinner_on=False, elapsed_seconds=3,
            spinner_frame="", spinner_index=0,
        )
        self.assertFalse(on)
        for verb in _PROCESSING_VERBS:
            self.assertNotIn(f"{verb}…", body)

    def test_hard_ceiling_forces_spinner_off_no_verb(self):
        prefix, body, on = _activity_bar_segments(
            activity_text="Working",
            spinner_on=True,
            elapsed_seconds=_ACTIVITY_HARD_CEILING_SECONDS + 1,
            spinner_frame="⠹",
            spinner_index=2,
        )
        self.assertFalse(on)
        self.assertEqual(prefix, "• ")
        for verb in _PROCESSING_VERBS:
            self.assertNotIn(f"{verb}…", body)


if __name__ == "__main__":
    unittest.main()
