"""Plan 123 — fast (mocked, no-network) regression tests for the integration
driver/matrix logic and the Phase-0 cleanup. The REAL repo runs are opt-in via
DUCKLN_INTEGRATION=1 (tests/integration), never in the unit suite."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.integration.driver import run_spec
from tests.integration.matrix import MATRIX, RepoSpec
from duckln.repo_bringup import _secondary_python_setup_steps


def _spec(signal=""):
    return RepoSpec("demo", "https://example.invalid/x", "python", signal, environments=("local",))


class DriverPassFail(unittest.TestCase):
    def test_verification_passed_is_pass(self):
        result = SimpleNamespace(verification_passed=True, failure_type=None, message="", repo_family="python")
        with patch("duckln.repo_bringup.bring_up_selected_repo", return_value=result):
            out = run_spec(_spec(), "local", timeout_seconds=30)
        self.assertTrue(out.passed)
        self.assertEqual(out.failure_class, "")

    def test_failure_reports_real_class(self):
        result = SimpleNamespace(
            verification_passed=False,
            failure_type=SimpleNamespace(value="MISSING_MODULE"),
            message="No module named PyInstaller", repo_family="python",
        )
        with patch("duckln.repo_bringup.bring_up_selected_repo", return_value=result):
            out = run_spec(_spec(), "local", timeout_seconds=30)
        self.assertFalse(out.passed)
        self.assertEqual(out.failure_class, "MISSING_MODULE")
        self.assertIn("PyInstaller", out.detail)

    def test_running_signal_in_output_is_pass(self):
        # verification_passed False, but the served banner appears → PASS.
        def fake_bringup(repo, mode, paths, *, display, **kw):
            display("Running on http://127.0.0.1:5000")
            return SimpleNamespace(verification_passed=False, failure_type=None, message="", repo_family="python")

        with patch("duckln.repo_bringup.bring_up_selected_repo", side_effect=fake_bringup):
            out = run_spec(_spec(signal="Running on"), "local", timeout_seconds=30)
        self.assertTrue(out.passed)

    def test_exception_is_captured_not_raised(self):
        with patch("duckln.repo_bringup.bring_up_selected_repo", side_effect=RuntimeError("clone failed")):
            out = run_spec(_spec(), "local", timeout_seconds=30)
        self.assertFalse(out.passed)
        self.assertEqual(out.failure_class, "exception")
        self.assertIn("clone failed", out.detail)


class MatrixSanity(unittest.TestCase):
    def test_rows_are_well_formed(self):
        self.assertTrue(MATRIX)
        for s in MATRIX:
            self.assertTrue(s.name)
            # Repo rows have a URL; synthetic smoke rows (stack="smoke") have a check instead.
            if s.stack == "smoke":
                self.assertTrue(s.smoke_check)
            else:
                self.assertTrue(s.url.startswith("https://"))
            self.assertTrue(set(s.environments) <= {"local", "vm", "container"})

    def test_local_rows_are_host_safe(self):
        # Safety contract: anything that runs on the HOST must be pure-python or a
        # pip-only synthetic smoke (no node/go/rust toolchain install on the machine).
        for s in MATRIX:
            if "local" in s.environments:
                self.assertIn(s.stack, {"python", "smoke"}, f"{s.name} runs locally but isn't host-safe")


class CleanupRegression(unittest.TestCase):
    def test_secondary_setup_returns_single_step(self):
        # Plan 123: the dead `return tuple(out)` was removed; the function still
        # returns exactly one target-side step.
        steps = _secondary_python_setup_steps(("package.json",), has_prebuild=True)
        self.assertEqual(len(steps), 1)
        self.assertIn("python3 -m venv .venv", steps[0][1])
        self.assertIn(".duckln-deps-ok", steps[0][1])  # Plan 127 completion marker


if __name__ == "__main__":
    unittest.main()
