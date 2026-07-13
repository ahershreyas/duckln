"""Tests: failures persist on every failure path, not just dedup (Plan 59 Fix 2)."""

from __future__ import annotations

import inspect as _inspect
import unittest

import duckln.repo_bringup as _rb


class RecoveryExhaustedPersistenceTest(unittest.TestCase):
    def test_first_failure_block_writes_persistent_record(self) -> None:
        """The general failure handler (after every failed command, before dedup)
        must include a write to persistent failure memory so even one-off
        failures are recorded for cross-session learning."""
        src = _inspect.getsource(_rb._execute_plan_with_bounded_recovery)
        # There must be a write_failure_memory_state call that comes BEFORE
        # the `seen_failures[dup_key] = fingerprint` line — that's the
        # Plan 59 Fix 2 first-failure record.
        seen_failures_idx = src.find("seen_failures[dup_key] = fingerprint")
        self.assertGreater(seen_failures_idx, 0)
        write_before = src[:seen_failures_idx].count("write_failure_memory_state")
        self.assertGreater(
            write_before,
            0,
            "First-failure persistence missing — write_failure_memory_state "
            "must be called before seen_failures dedup state mutation.",
        )

    def test_recovery_exhausted_branch_persists(self) -> None:
        """The recovery-exhausted branch (`if recovery_count >= max_recovery_attempts:`)
        must also call write_failure_memory_state so the failure is captured even
        when dedup never fires."""
        src = _inspect.getsource(_rb._execute_plan_with_bounded_recovery)
        # Find the recovery-exhausted block and confirm it persists.
        idx = src.find("recovery_count >= max_recovery_attempts")
        self.assertGreater(idx, 0)
        # Look at the next ~1500 chars to find a write_failure_memory_state call.
        window = src[idx:idx + 2500]
        self.assertIn(
            "write_failure_memory_state",
            window,
            "Recovery-exhausted branch must persist the failure for future sessions.",
        )

    def test_recovery_exhausted_includes_proper_fingerprint(self) -> None:
        """The recovery-exhausted persistence must use the actual stderr fingerprint
        (not an empty string), so cross-session lookup can match."""
        src = _inspect.getsource(_rb._execute_plan_with_bounded_recovery)
        idx = src.find("recovery_count >= max_recovery_attempts")
        window = src[idx:idx + 2500]
        self.assertIn(
            "stderr_fingerprint=fingerprint",
            window,
            "Recovery-exhausted persistence must pass the real fingerprint variable.",
        )


if __name__ == "__main__":
    unittest.main()
