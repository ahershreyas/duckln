"""Tests: int(result.exit_code) call sites are None-safe (Plan 60 Bug F)."""

from __future__ import annotations

import inspect as _inspect
import unittest

import duckln.repo_bringup as _rb


class IntExitCodeNoneSafeTest(unittest.TestCase):
    def test_no_raw_int_exit_code_calls_in_setup_path(self) -> None:
        """Plan 60 Bug F: every `int(result.exit_code)` in
        _execute_plan_with_bounded_recovery must be guarded with a None check."""
        src = _inspect.getsource(_rb._execute_plan_with_bounded_recovery)
        # Find all occurrences of "int(result.exit_code)" — each one must be
        # followed by " if result.exit_code is not None else" within the next
        # ~80 characters.
        import re as _re
        occurrences = list(_re.finditer(r"int\(result\.exit_code\)", src))
        self.assertGreater(len(occurrences), 0, "Expected at least one int(exit_code) call site")
        for match in occurrences:
            window = src[match.end():match.end() + 80]
            self.assertIn(
                "if result.exit_code is not None else",
                window,
                f"Unsafe int(result.exit_code) at offset {match.start()} — "
                f"context: {src[max(0, match.start()-30):match.end()+80]!r}",
            )

    def test_dup_key_construction_is_none_safe(self) -> None:
        """The dedup key tuple uses exit_code; must handle None."""
        src = _inspect.getsource(_rb._execute_plan_with_bounded_recovery)
        # The dup_key line must include the None guard.
        import re as _re
        dup_key_lines = [
            line for line in src.splitlines()
            if "dup_key = (" in line and "exit_code" in line
        ]
        self.assertTrue(dup_key_lines, "dup_key construction not found")
        for line in dup_key_lines:
            self.assertIn(
                "if result.exit_code is not None else",
                line,
                f"dup_key line not None-safe: {line!r}",
            )

    def test_sentinel_minus_one_used_for_none_exit_code(self) -> None:
        """Convention: -1 sentinel for None exit_code (never collides with real
        Unix exit codes 0-255)."""
        src = _inspect.getsource(_rb._execute_plan_with_bounded_recovery)
        # All four sites use -1 as the sentinel.
        import re as _re
        sentinel_count = len(_re.findall(
            r"int\(result\.exit_code\) if result\.exit_code is not None else -1",
            src,
        ))
        self.assertGreaterEqual(
            sentinel_count,
            4,
            f"Expected ≥4 sites using -1 sentinel; got {sentinel_count}",
        )


if __name__ == "__main__":
    unittest.main()
