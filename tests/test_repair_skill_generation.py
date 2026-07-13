"""Tests for _generate_repair_skill_note and build_repair_skill_prompt."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from duckln.prompts import build_repair_skill_prompt
from duckln.repo_bringup import _generate_repair_skill_note


class BuildRepairSkillPromptTest(unittest.TestCase):
    def test_contains_all_key_fields(self) -> None:
        prompt = build_repair_skill_prompt(
            failed_command="npm install",
            error_output="ENOENT: no such file or directory\ncould not load package.json",
            recovery_command="npm cache clean --force && npm install",
            repo_family="node_typescript",
            repo_name="JustHireMe",
            framework="TypeScript",
        )
        self.assertIn("npm install", prompt)
        self.assertIn("node_typescript", prompt)
        self.assertIn("JustHireMe", prompt)
        self.assertIn("TypeScript", prompt)
        self.assertIn('"slug"', prompt)
        self.assertIn('"fix_sequence"', prompt)

    def test_error_output_truncated_at_800_chars(self) -> None:
        long_error = "E" * 2000
        prompt = build_repair_skill_prompt(
            failed_command="cargo build",
            error_output=long_error,
            recovery_command="rustup update",
            repo_family="rust",
            repo_name="myapp",
            framework="Rust",
        )
        # The truncated error should appear but not the full 2000-char version
        self.assertIn("E" * 800, prompt)
        self.assertNotIn("E" * 801, prompt)


class GenerateRepairSkillNoteTest(unittest.TestCase):
    def _make_config_dir(self) -> Path:
        # Returns a temp dir; caller owns the lifecycle via context manager.
        self._tmpdir = tempfile.TemporaryDirectory()
        config_dir = Path(self._tmpdir.name)
        (config_dir / "config.json").write_text(
            json.dumps({
                "provider": "openai",
                "model": "gpt-4o",
                "api_key": "test-key",
                "base_url": "https://api.openai.com/v1",
                "mode": "hotl",
                "setup_complete": True,
            }),
            encoding="utf-8",
        )
        return config_dir

    def tearDown(self) -> None:
        if hasattr(self, "_tmpdir"):
            self._tmpdir.cleanup()

    def test_valid_llm_response_returns_tuple(self) -> None:
        config_dir = self._make_config_dir()
        llm_reply = json.dumps({
            "slug": "repair-node-typescript-enoent",
            "title": "Repair: ENOENT during npm install",
            "trigger_pattern": "ENOENT: no such file or directory, open 'package.json'",
            "fix_sequence": ["npm cache clean --force", "npm install"],
            "verification": "node_modules/.bin/react-scripts --version",
            "notes": "Happens when node_modules is partially initialized.",
        })
        with patch("duckln.repo_bringup.generate_provider_reply", return_value=llm_reply):
            result = _generate_repair_skill_note(
                failed_command="npm install",
                error_output="ENOENT: no such file or directory",
                recovery_command="npm cache clean --force",
                repo_family="node_typescript",
                repo_name="JustHireMe",
                framework="TypeScript",
                config_dir=config_dir,
            )
        self.assertIsNotNone(result)
        slug, title, summary = result
        self.assertEqual(slug, "repair-node-typescript-enoent")
        self.assertIn("Repair:", title)
        self.assertIn("npm cache clean --force", summary)
        self.assertIn("npm install", summary)

    def test_malformed_json_returns_none(self) -> None:
        config_dir = self._make_config_dir()
        with patch("duckln.repo_bringup.generate_provider_reply", return_value="not json at all"):
            result = _generate_repair_skill_note(
                failed_command="cargo build",
                error_output="error[E0463]: can't find crate",
                recovery_command="rustup update stable",
                repo_family="rust",
                repo_name="myapp",
                framework="Rust",
                config_dir=config_dir,
            )
        self.assertIsNone(result)

    def test_missing_required_fields_returns_none(self) -> None:
        config_dir = self._make_config_dir()
        # slug is missing
        llm_reply = json.dumps({
            "title": "Repair: something",
            "trigger_pattern": "error pattern",
            "fix_sequence": ["some command"],
            "verification": "check command",
            "notes": "",
        })
        with patch("duckln.repo_bringup.generate_provider_reply", return_value=llm_reply):
            result = _generate_repair_skill_note(
                failed_command="pip install -r requirements.txt",
                error_output="No module named pip",
                recovery_command="python -m ensurepip --upgrade",
                repo_family="python",
                repo_name="myrepo",
                framework="Python",
                config_dir=config_dir,
            )
        self.assertIsNone(result)

    def test_synthetic_narrative_stripped_before_prompt(self) -> None:
        """Error output containing Duckln narrative should be stripped before LLM call."""
        config_dir = self._make_config_dir()
        narrative_error = (
            "Ducklin classified this blocker as rust dependency failure. "
            "Specialist route: rust. Toolchain: cargo. Fatal line: Cargo.toml"
        )
        captured: list[str] = []

        def fake_llm(*args: object, **kwargs: object) -> str:
            captured.append(str(kwargs.get("user_message", "")))
            return json.dumps({
                "slug": "repair-python-pip",
                "title": "Repair: pip missing",
                "trigger_pattern": "No module named pip",
                "fix_sequence": ["python -m ensurepip --upgrade"],
                "verification": "pip --version",
                "notes": "",
            })

        with patch("duckln.repo_bringup.generate_provider_reply", side_effect=fake_llm):
            _generate_repair_skill_note(
                failed_command="pip install -r requirements.txt",
                error_output=narrative_error,
                recovery_command="python -m ensurepip --upgrade",
                repo_family="python",
                repo_name="myrepo",
                framework="Python",
                config_dir=config_dir,
            )
        # The Duckln narrative keywords must NOT appear verbatim in the prompt
        self.assertTrue(len(captured) > 0)
        sent = captured[0]
        self.assertNotIn("Specialist route:", sent)
        self.assertNotIn("Toolchain:", sent)

    def test_llm_error_returns_none(self) -> None:
        config_dir = self._make_config_dir()
        with patch("duckln.repo_bringup.generate_provider_reply", side_effect=OSError("network unavailable")):
            result = _generate_repair_skill_note(
                failed_command="go build ./...",
                error_output="connection refused",
                recovery_command="go env -w GOPROXY=direct",
                repo_family="go_native",
                repo_name="mygoapp",
                framework="Go",
                config_dir=config_dir,
            )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
