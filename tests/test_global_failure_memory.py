"""Tests: cross-repo global failure memory (Plan 59 Fix 1)."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from state.access import (
    write_failure_memory_state,
    read_failure_memory_state,
    lookup_recent_failure,
    _is_environmental_failure,
    _GLOBAL_FAILURE_SLUG,
)


class EnvironmentalClassifierTest(unittest.TestCase):
    def test_command_not_found_is_environmental(self) -> None:
        self.assertTrue(_is_environmental_failure("brew: command not found"))

    def test_unable_to_locate_package_is_environmental(self) -> None:
        self.assertTrue(_is_environmental_failure("E: Unable to locate package nodejs"))

    def test_module_not_found_is_not_environmental(self) -> None:
        """A Python ModuleNotFoundError is repo-specific, not host."""
        self.assertFalse(_is_environmental_failure("ModuleNotFoundError: my_pkg"))

    def test_empty_string_is_not_environmental(self) -> None:
        self.assertFalse(_is_environmental_failure(""))

    def test_permission_denied_alone_is_not_environmental(self) -> None:
        """'permission denied' alone could be a repo-specific file perm issue."""
        self.assertFalse(_is_environmental_failure("permission denied: ./some_local_file"))

    def test_permission_denied_with_sudo_is_environmental(self) -> None:
        self.assertTrue(_is_environmental_failure("sudo: permission denied"))


class GlobalFailureMemoryTest(unittest.TestCase):
    def test_environmental_failure_dual_writes_to_global(self) -> None:
        """Writing an environmental failure for repo A populates the global log too."""
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            write_failure_memory_state(
                config_dir,
                repo_slug="https://example.com/repo_a",
                command="brew install node",
                execution_target="vm",
                exit_code=127,
                stderr_fingerprint="brew: command not found",
            )
            # Per-repo file exists.
            repo_a = read_failure_memory_state(config_dir, repo_slug="https://example.com/repo_a")
            self.assertEqual(len(repo_a), 1)
            # Global file ALSO exists with the same record.
            global_records = read_failure_memory_state(config_dir, repo_slug=_GLOBAL_FAILURE_SLUG)
            self.assertEqual(len(global_records), 1)
            self.assertEqual(global_records[0]["command"], "brew install node")

    def test_non_environmental_failure_only_per_repo(self) -> None:
        """A repo-specific failure does NOT propagate to the global log."""
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            write_failure_memory_state(
                config_dir,
                repo_slug="https://example.com/repo_b",
                command="npm install",
                execution_target="vm",
                exit_code=1,
                stderr_fingerprint="ModuleNotFoundError: somepkg",  # repo-specific
            )
            global_records = read_failure_memory_state(config_dir, repo_slug=_GLOBAL_FAILURE_SLUG)
            self.assertEqual(len(global_records), 0)

    def test_lookup_from_different_repo_finds_global_record(self) -> None:
        """Failure recorded for repo A is found when querying from repo B."""
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            # Repo A hits an environmental failure.
            write_failure_memory_state(
                config_dir,
                repo_slug="https://example.com/repo_a",
                command="brew install node",
                execution_target="vm",
                exit_code=127,
                stderr_fingerprint="brew: command not found",
            )
            # Repo B's planner consults the failure log.
            found = lookup_recent_failure(
                config_dir,
                repo_slug="https://example.com/repo_b",
                command="brew install node",
                execution_target="vm",
            )
            self.assertIsNotNone(found, "Repo B should find the global brew failure")

    def test_per_repo_takes_precedence_over_global(self) -> None:
        """When both per-repo and global have a record, per-repo wins (more specific)."""
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            # Write to repo A — propagates to global (environmental).
            write_failure_memory_state(
                config_dir,
                repo_slug="repo_a",
                command="brew install node",
                execution_target="vm",
                exit_code=127,
                stderr_fingerprint="brew: command not found",
            )
            # Now write a DIFFERENT outcome to repo A only (e.g. retry).
            # Both records share (command, target) key so merged in repo_a.
            # Lookup from repo_a should return the per-repo record.
            found = lookup_recent_failure(
                config_dir,
                repo_slug="repo_a",
                command="brew install node",
                execution_target="vm",
            )
            self.assertIsNotNone(found)
            # retry_count is 1 in per-repo (single write).
            self.assertEqual(int(found.get("retry_count", 0)), 1)

    def test_global_record_respects_24h_window(self) -> None:
        """A stale (>24h) global record should NOT match a lookup."""
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            write_failure_memory_state(
                config_dir,
                repo_slug="repo_a",
                command="brew install node",
                execution_target="vm",
                exit_code=127,
                stderr_fingerprint="brew: command not found",
            )
            # Backdate ALL failure-log files in the failures dir (handles slug normalization).
            from agent.memory import FAILURES_DIR_NAME, AGENT_MEMORY_DIR_NAME
            import re as _re
            old_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - (48 * 3600)))
            failures_dir = config_dir / AGENT_MEMORY_DIR_NAME / FAILURES_DIR_NAME
            for fp in failures_dir.iterdir():
                if fp.suffix == ".md":
                    text = fp.read_text(encoding="utf-8")
                    text = _re.sub(r'"last_seen":\s*"[^"]+"', f'"last_seen": "{old_iso}"', text)
                    fp.write_text(text, encoding="utf-8")
            # Lookup from a different repo (so per-repo is empty) — global stale.
            found = lookup_recent_failure(
                config_dir,
                repo_slug="repo_b",
                command="brew install node",
                execution_target="vm",
                window_hours=24.0,
            )
            self.assertIsNone(found)


if __name__ == "__main__":
    unittest.main()
