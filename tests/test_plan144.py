"""Plan 144 — reason through a MULTI-STAGE failure (the venv ↔ PyInstaller oscillation):
F1 a fix that CHANGES the error made PROGRESS → chain the next fix, don't declare "did not verify".
F2 a generic fix re-run must NOT delete the venv it's about to use; the MISSING_VENV fix builds a
   COMPLETE venv (stamps .duckln-deps-ok + installs the spec-detected PyInstaller).
F3 a marker-guarded setup step (venv) is re-run on resume, not permanently skipped.
"""

from __future__ import annotations

import unittest

import duckln.repo_bringup as rb
from duckln.diagnostics import match_deterministic_fix
from duckln.recovery import apply_fix_and_verify


class ProgressAwareVerify(unittest.TestCase):
    """F1: an error that changed = progress → chain; an unchanged error = honest stop."""

    def test_chains_next_fix_when_the_error_changes(self):
        # build:sidecar is multi-stage: venv-not-found → (venv fix) → PyInstaller-missing →
        # (pyinstaller fix) → exit 0. The venv fix must NOT be judged "did not verify".
        FAILED, VENV_FIX, PY_FIX = "npm run build:sidecar", "create-venv", "pip install pyinstaller"
        state = {"venv": False, "pyinstaller": False}
        calls: list[str] = []

        def run_cmd(cmd: str):
            calls.append(cmd)
            if cmd == VENV_FIX:
                state["venv"] = True
                return (0, "")
            if cmd == PY_FIX:
                state["pyinstaller"] = True
                return (0, "")
            if cmd == FAILED:
                if not state["venv"]:
                    return (1, "Python virtual environment not found: /x/backend/.venv/bin/python")
                if not state["pyinstaller"]:
                    return (1, "/x/backend/.venv/bin/python: No module named PyInstaller")
                return (0, "")
            return (1, "unexpected")

        def next_fix(out: str):
            return PY_FIX if "No module named PyInstaller" in out else None

        self.assertTrue(apply_fix_and_verify(
            fix_command=VENV_FIX, failed_command=FAILED, run_cmd=run_cmd, next_fix=next_fix,
        ))
        self.assertIn(PY_FIX, calls)  # it actually chained the second-stage fix

    def test_unchanged_error_stops_without_looping(self):
        # The step never improves and next_fix proposes the SAME fix → must return False, not loop.
        def run_cmd(cmd: str):
            return (0, "") if cmd == "fix" else (1, "same error")

        self.assertFalse(apply_fix_and_verify(
            fix_command="fix", failed_command="step", run_cmd=run_cmd, next_fix=lambda _o: "fix",
        ))

    def test_bounded_by_max_stages(self):
        # Each fix changes the error to a brand-new one forever → bounded by max_stages, no hang.
        n = {"i": 0}

        def run_cmd(cmd: str):
            if cmd.startswith("fix"):
                return (0, "")
            n["i"] += 1
            return (1, f"error number {n['i']}")  # always a NEW error

        self.assertFalse(apply_fix_and_verify(
            fix_command="fix0", failed_command="step", run_cmd=run_cmd,
            next_fix=lambda _o: f"fix{n['i']}", max_stages=4,
        ))

    def test_backward_compatible_without_next_fix(self):
        ok_seq = iter([(0, ""), (0, "")])           # fix ok, re-run exit 0 → verified
        self.assertTrue(apply_fix_and_verify(
            fix_command="fix", failed_command="step", run_cmd=lambda _c: next(ok_seq),
        ))
        bad_seq = iter([(0, ""), (1, "err")])        # fix ok, re-run fails, no next_fix → False
        self.assertFalse(apply_fix_and_verify(
            fix_command="fix", failed_command="step", run_cmd=lambda _c: next(bad_seq),
        ))


class CleanupKeepsTheVenvOnAFixReRun(unittest.TestCase):
    """F2: only a resource crunch may delete a marker-less venv/node_modules."""

    def test_generic_fix_rerun_keeps_venv_and_node_modules(self):
        conservative = rb._partial_cleanup_command(aggressive=False)
        self.assertNotIn(".venv", conservative)          # never delete the venv the fix uses
        self.assertNotIn("node_modules", conservative)
        self.assertIn("build_cache", conservative)        # only safe scratch
        self.assertIn(".codex-temp-sidecar", conservative)

    def test_aggressive_still_removes_marker_less_partials(self):
        agg = rb._partial_cleanup_command(aggressive=True)   # default (crunch / precheck cleanup)
        self.assertIn(".duckln-deps-ok", agg)
        self.assertIn("rm -rf node_modules", agg)
        self.assertIn(".venv", agg)
        # default arg is the aggressive form (back-compat with Plan 137 callers)
        self.assertEqual(rb._partial_cleanup_command(), agg)


class MissingVenvFixIsComplete(unittest.TestCase):
    """F2: the recovery venv fix produces a COMPLETE venv (marker + spec→PyInstaller)."""

    def test_fix_stamps_marker_and_installs_spec_pyinstaller(self):
        fix = match_deterministic_fix(
            stderr="Error: Python virtual environment not found: /home/ubuntu/x/backend/.venv/bin/python",
            execution_target="vm",
        )
        self.assertIsNotNone(fix)
        self.assertIn("touch", fix.fix_command)
        self.assertIn(".duckln-deps-ok", fix.fix_command)        # survives the partial-cleanup
        self.assertIn("pyinstaller", fix.fix_command.lower())     # spec-detected build tool
        self.assertIn("import PyInstaller", fix.fix_command)       # idempotent import-check first


class MarkerGuardedSetupRerunsOnResume(unittest.TestCase):
    """F3: a venv setup step (marker-guarded) is re-run on resume, not skipped."""

    def test_marker_guarded_setup_detected(self):
        self.assertTrue(rb._is_marker_guarded_setup(rb._SECONDARY_PY_SETUP_CMD))

    def test_ordinary_steps_are_not_marker_guarded(self):
        self.assertFalse(rb._is_marker_guarded_setup("git clone https://github.com/x/y"))
        self.assertFalse(rb._is_marker_guarded_setup("npm ci"))
        self.assertFalse(rb._is_marker_guarded_setup(""))


if __name__ == "__main__":
    unittest.main()
