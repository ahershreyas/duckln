from __future__ import annotations

import unittest

from duckln.textual_ui import (
    _PROCESSING_VERBS,
    _THOUGHT_MIN_INTERVAL,
    _VERB_DWELL_TICKS,
    _processing_verb,
    _release_due_thoughts,
)


class TestReleaseDueThoughts(unittest.TestCase):
    def test_first_thought_releases_immediately(self) -> None:
        pending, visible, last, changed = _release_due_thoughts(["a"], [], 0.0, 100.0)
        self.assertTrue(changed)
        self.assertEqual(visible, ["a"])
        self.assertEqual(pending, [])
        self.assertEqual(last, 100.0)

    def test_rapid_burst_drains_one_per_interval(self) -> None:
        pending = ["a", "b", "c"]
        visible: list[str] = []
        last = 0.0
        now = 100.0
        # First release.
        pending, visible, last, changed = _release_due_thoughts(pending, visible, last, now)
        self.assertEqual(visible, ["a"])
        # Too soon — nothing released, nothing lost.
        pending, visible, last, changed = _release_due_thoughts(pending, visible, last, now + 0.1)
        self.assertFalse(changed)
        self.assertEqual(visible, ["a"])
        self.assertEqual(pending, ["b", "c"])
        # After the interval, the next one is revealed.
        now2 = now + _THOUGHT_MIN_INTERVAL + 0.01
        pending, visible, last, changed = _release_due_thoughts(pending, visible, last, now2)
        self.assertTrue(changed)
        self.assertEqual(visible, ["a", "b"])
        # And the last one after another interval — no thought ever dropped.
        now3 = now2 + _THOUGHT_MIN_INTERVAL + 0.01
        pending, visible, last, changed = _release_due_thoughts(pending, visible, last, now3)
        self.assertEqual(visible, ["a", "b", "c"])
        self.assertEqual(pending, [])

    def test_cap_keeps_most_recent(self) -> None:
        visible = [f"v{i}" for i in range(40)]
        pending = ["new"]
        pending, visible, last, changed = _release_due_thoughts(
            pending, visible, 0.0, 100.0, max_visible=40
        )
        self.assertTrue(changed)
        self.assertEqual(len(visible), 40)
        self.assertEqual(visible[-1], "new")
        self.assertNotIn("v0", visible)

    def test_empty_pending_no_change(self) -> None:
        pending, visible, last, changed = _release_due_thoughts([], ["a"], 0.0, 100.0)
        self.assertFalse(changed)
        self.assertEqual(visible, ["a"])


class TestVerbDwell(unittest.TestCase):
    def test_verb_dwells_across_ticks(self) -> None:
        # The same verb persists for _VERB_DWELL_TICKS spinner ticks (~2.4s),
        # so it does not flicker every 0.1s.
        v_start = _processing_verb(0 // _VERB_DWELL_TICKS)
        v_mid = _processing_verb((_VERB_DWELL_TICKS - 1) // _VERB_DWELL_TICKS)
        v_next = _processing_verb(_VERB_DWELL_TICKS // _VERB_DWELL_TICKS)
        self.assertEqual(v_start, v_mid)
        self.assertNotEqual(v_start, v_next)

    def test_verb_function_still_one_to_one(self) -> None:
        # The pure helper is unchanged: index maps 1:1 to a verb.
        seen = {_processing_verb(i) for i in range(len(_PROCESSING_VERBS))}
        self.assertEqual(seen, set(_PROCESSING_VERBS))


if __name__ == "__main__":
    unittest.main()
