"""Tests: persistent cross-session failure memory (Plan 58 Bug D)."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from state.access import (
    write_failure_memory_state,
    read_failure_memory_state,
    lookup_recent_failure,
)


class PersistentFailureMemoryTest(unittest.TestCase):
    def test_write_and_read_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            write_failure_memory_state(
                config_dir,
                repo_slug="https://example.com/test_repo",
                command="brew install node",
                execution_target="vm",
                exit_code=127,
                stderr_fingerprint="brew_not_found_fp",
            )
            records = read_failure_memory_state(
                config_dir, repo_slug="https://example.com/test_repo"
            )
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["command"], "brew install node")
            self.assertEqual(records[0]["execution_target"], "vm")
            self.assertEqual(records[0]["exit_code"], 127)
            self.assertEqual(records[0]["stderr_fingerprint"], "brew_not_found_fp")
            self.assertEqual(records[0]["retry_count"], 1)

    def test_duplicate_command_increments_retry_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            for _ in range(3):
                write_failure_memory_state(
                    config_dir,
                    repo_slug="repo_a",
                    command="brew install node",
                    execution_target="vm",
                    exit_code=127,
                    stderr_fingerprint="fp1",
                )
            records = read_failure_memory_state(config_dir, repo_slug="repo_a")
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["retry_count"], 3)

    def test_different_commands_kept_as_separate_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            write_failure_memory_state(
                config_dir,
                repo_slug="repo_b",
                command="brew install node",
                execution_target="vm",
                exit_code=127,
                stderr_fingerprint="fp1",
            )
            write_failure_memory_state(
                config_dir,
                repo_slug="repo_b",
                command="apt install nodejs",
                execution_target="vm",
                exit_code=100,
                stderr_fingerprint="fp2",
            )
            records = read_failure_memory_state(config_dir, repo_slug="repo_b")
            self.assertEqual(len(records), 2)

    def test_lookup_returns_recent_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            write_failure_memory_state(
                config_dir,
                repo_slug="repo_c",
                command="brew install node",
                execution_target="vm",
                exit_code=127,
                stderr_fingerprint="fp1",
            )
            found = lookup_recent_failure(
                config_dir,
                repo_slug="repo_c",
                command="brew install node",
                execution_target="vm",
            )
            self.assertIsNotNone(found)
            self.assertEqual(found["stderr_fingerprint"], "fp1")

    def test_lookup_returns_none_for_unknown_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            write_failure_memory_state(
                config_dir,
                repo_slug="repo_d",
                command="brew install node",
                execution_target="vm",
                exit_code=127,
                stderr_fingerprint="fp1",
            )
            found = lookup_recent_failure(
                config_dir,
                repo_slug="repo_d",
                command="sudo apt install nodejs",
                execution_target="vm",
            )
            self.assertIsNone(found)

    def test_lookup_ignores_failures_older_than_24h(self) -> None:
        """Failures with last_seen > 24h ago must be ignored by lookup."""
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            # Write a failure record, then manually backdate its timestamp.
            write_failure_memory_state(
                config_dir,
                repo_slug="repo_old",
                command="brew install node",
                execution_target="vm",
                exit_code=127,
                stderr_fingerprint="fp1",
            )
            # Manually rewrite the file to backdate the timestamp.
            from agent.memory import FAILURES_DIR_NAME, AGENT_MEMORY_DIR_NAME
            old_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - (48 * 3600)))
            failure_file = config_dir / AGENT_MEMORY_DIR_NAME / FAILURES_DIR_NAME / "repo_old.md"
            text = failure_file.read_text(encoding="utf-8")
            # Replace the last_seen field.
            import re as _re
            text = _re.sub(r'"last_seen":\s*"[^"]+"', f'"last_seen": "{old_iso}"', text)
            failure_file.write_text(text, encoding="utf-8")

            found = lookup_recent_failure(
                config_dir,
                repo_slug="repo_old",
                command="brew install node",
                execution_target="vm",
                window_hours=24.0,
            )
            self.assertIsNone(found, "Failures older than 24h should be ignored")

    def test_read_returns_empty_when_no_file_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            records = read_failure_memory_state(config_dir, repo_slug="never_existed")
            self.assertEqual(records, ())


if __name__ == "__main__":
    unittest.main()
