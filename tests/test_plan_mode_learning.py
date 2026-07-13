"""Plan 72 Phase 8 — learning loop: verified-success skill extraction."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from duckln.plan_mode import plan_skill_signature, record_plan_skill


class LearningLoopTest(unittest.TestCase):
    def test_signature_is_stable_and_keyed(self) -> None:
        sig = plan_skill_signature(repo_family="node_typescript", os_name="Darwin arm64", execution_target="vm")
        self.assertEqual(sig, "setup-node_typescript-darwin-vm")

    def test_record_writes_redacted_skill(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            config_dir = Path(td)
            slug = record_plan_skill(
                config_dir=config_dir, repo_family="node_typescript", os_name="Darwin",
                execution_target="vm",
                commands=("git clone --depth 1 https://x .", "npm ci", "token=ghp_abcdefghijklmnopqrstuvwxyz0123456789 npm run dev"),
            )
            self.assertIsNotNone(slug)
            skills_dir = config_dir / "memory" / "skills"
            # The skill file should exist and NOT contain the raw token.
            blobs = list(skills_dir.glob("*.md")) if skills_dir.exists() else []
            text = "\n".join(p.read_text() for p in blobs)
            self.assertIn("npm ci", text)
            self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz", text)

    def test_no_commands_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            slug = record_plan_skill(
                config_dir=Path(td), repo_family="python", os_name="Linux",
                execution_target="local", commands=(),
            )
            self.assertIsNone(slug)


if __name__ == "__main__":
    unittest.main()
