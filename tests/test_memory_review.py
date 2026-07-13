"""Tests for approval-gated core memory review helpers."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from duckln.memory_review import review_managed_memory_update, validate_memory_manifest


class MemoryReviewTest(unittest.TestCase):
    def test_validate_memory_manifest_checks_tools_json(self) -> None:
        errors = validate_memory_manifest("tools.json", '{"tools":[{"id":"x"}]}')

        self.assertTrue(errors)

    def test_review_managed_memory_update_rejects_unapproved_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = review_managed_memory_update(
                config_dir=Path(temp_dir),
                relative_path="skills/example.md",
                title="Example",
                content="Keep setup fixes concise.",
                approve=lambda prompt: False,
            )

            self.assertFalse(result.accepted)
            self.assertIn("unchanged", result.summary)


if __name__ == "__main__":
    unittest.main()
