from __future__ import annotations

import unittest

from duckln.diagnostics import ErrorCategory, match_deterministic_fix


class TestOomDeterministicFix(unittest.TestCase):
    def test_sigkill_during_cargo_build(self) -> None:
        err = (
            "error: could not compile `gtk` (lib)\n"
            "Caused by: process didn't exit successfully: `rustc ...` (signal: 9, SIGKILL: kill)\n"
            "warning: build failed, waiting for other jobs to finish..."
        )
        fix = match_deterministic_fix(stderr=err, command="npm run tauri dev", execution_target="vm")
        self.assertIsNotNone(fix)
        self.assertEqual(fix.category, ErrorCategory.OUT_OF_MEMORY)
        self.assertIn("swap", fix.fix_command.lower())
        self.assertIn("jobs = 1", fix.fix_command)

    def test_cc1plus_out_of_memory(self) -> None:
        fix = match_deterministic_fix(stderr="cc1plus: out of memory allocating 65536 bytes", execution_target="vm")
        self.assertIsNotNone(fix)
        self.assertEqual(fix.category, ErrorCategory.OUT_OF_MEMORY)

    def test_bare_killed_with_build_context(self) -> None:
        fix = match_deterministic_fix(stderr="Compiling serde\nKilled", command="cargo build", execution_target="vm")
        self.assertIsNotNone(fix)
        self.assertEqual(fix.category, ErrorCategory.OUT_OF_MEMORY)

    def test_benign_memory_mention_not_oom(self) -> None:
        # A doc/log line mentioning "memory" must NOT trigger the OOM fix.
        fix = match_deterministic_fix(stderr="In-memory cache initialized successfully", execution_target="vm")
        self.assertTrue(fix is None or fix.category != ErrorCategory.OUT_OF_MEMORY)

    def test_macos_caps_jobs_without_swap(self) -> None:
        fix = match_deterministic_fix(stderr="(signal: 9, SIGKILL: kill)", command="cargo build", execution_target="local")
        self.assertIsNotNone(fix)
        self.assertEqual(fix.category, ErrorCategory.OUT_OF_MEMORY)
        self.assertNotIn("swapon", fix.fix_command)
        self.assertIn("jobs = 1", fix.fix_command)


if __name__ == "__main__":
    unittest.main()
