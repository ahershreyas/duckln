from __future__ import annotations

import unittest

from duckln.main import _looks_like_runtime_repair_request


class TestResumePhrases(unittest.TestCase):
    def test_resume_phrases_are_recognized(self):
        for phrase in (
            "retry", "try again", "try now", "check now", "check again",
            "resume", "Retry", "  Try Again  ", "CHECK NOW",
        ):
            self.assertTrue(
                _looks_like_runtime_repair_request(phrase),
                f"expected resume phrase to be recognized: {phrase!r}",
            )

    def test_unrelated_text_is_not_a_resume_request(self):
        for phrase in ("what repos do I have", "show me the plan", "hello"):
            self.assertFalse(_looks_like_runtime_repair_request(phrase), phrase)


if __name__ == "__main__":
    unittest.main()
