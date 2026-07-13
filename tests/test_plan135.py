"""Plan 135 — active agent, not a passive 30s timer:
F1 route run/build steps correctly + build-tier timeout + progress-aware readiness.
F2 a heavy build that times out = "needs more time," not a code bug.
F4 always write + surface the maintained logical-thinking.md reasoning file.
F5 terminal-busy: shell-prompt detection drains the queued command.
F6 disk-full auto-heal recognizes the resource crunch.
F7 the "Working…" activity spinner clears on a pause/halt/done message.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import duckln.repo_bringup as rb
import duckln.shell as sh


class RouteRunBuild(unittest.TestCase):
    def test_npm_run_tauri_dev_is_a_run_command(self):
        # the bug: `npm run tauri dev` was NOT recognized → 30s synchronous path.
        self.assertTrue(rb._looks_like_run_command("npm run tauri dev"))
        self.assertTrue(rb._looks_like_run_command("npm run dev"))
        self.assertTrue(rb._looks_like_run_command("pnpm run web serve"))
        # a prebuild build is NOT a run command (it must complete synchronously).
        self.assertFalse(rb._looks_like_run_command("npm run --if-present build:all"))
        self.assertFalse(rb._looks_like_run_command("echo hi"))

    def test_build_scripts_get_build_tier_timeout(self):
        for c in ("npm run --if-present build:all", "npm run --if-present build:sidecar",
                  "cargo tauri build", "pnpm run build", "npm run tauri dev"):
            self.assertGreaterEqual(sh._recommended_timeout_seconds(c, 30), 1800.0, c)
        # trivial / non-build stays small; clone stays at the install tier.
        self.assertEqual(sh._recommended_timeout_seconds("echo hi", 30), 30.0)
        self.assertEqual(sh._recommended_timeout_seconds("npm run test", 30), 30.0)
        self.assertGreaterEqual(sh._recommended_timeout_seconds("git clone x", 30), 240.0)

    def test_is_build_tier_command(self):
        self.assertTrue(rb._is_build_tier_command("npm run --if-present build:all"))
        self.assertTrue(rb._is_build_tier_command("cargo tauri build"))
        self.assertFalse(rb._is_build_tier_command("npm run dev"))
        self.assertFalse(rb._is_build_tier_command("ls"))

    def test_build_progress_detection(self):
        self.assertTrue(rb._looks_build_progressing("   Compiling serde v1.0.219"))
        self.assertTrue(rb._looks_build_progressing("Building [=====>      ] 415/561"))
        self.assertFalse(rb._looks_build_progressing("waiting for input"))


class ProgressAwareReadiness(unittest.TestCase):
    def test_keeps_waiting_while_log_grows_then_ready(self):
        # A fake runner whose poll log GROWS for a few polls (build progressing), then
        # serves — the wait must NOT abandon it even though the per-poll budget is tiny.
        import duckln.repo_bringup as rb_
        from duckln.shell import CommandResult

        class _Runner:
            def __init__(self):
                self.n = 0

            def run(self, command, *a, **k):
                # first call = the nohup launch (returns the PID line)
                if "DUCKLN_RUN_PID" in command or self.n == 0 and "nohup" in command:
                    return CommandResult(command=command, exit_code=0,
                                         stdout="DUCKLN_RUN_PID:123", stderr="",
                                         timed_out=False, duration_seconds=0.0)
                self.n += 1
                if self.n < 4:
                    log = "Compiling crate %d\n" % self.n + "DUCKLN_ALIVE"
                else:
                    log = "Local:   http://localhost:1420/\nDUCKLN_ALIVE"
                return CommandResult(command=command, exit_code=0, stdout=log, stderr="",
                                     timed_out=False, duration_seconds=0.0)

        import time as _t
        orig_sleep = _t.sleep
        _t.sleep = lambda *_a, **_k: None
        try:
            status, served, _log = rb_._launch_and_await_server(
                runner=_Runner(), raw_command="npm run tauri dev", project_cwd="/x",
                execution_target="local", vm_name=None, config_dir=Path("/tmp/x"),
                repo=type("R", (), {"name": "demo"})(), display=lambda *_: None,
                think=lambda *_: None, ready_window_seconds=2.0,
            )
        finally:
            _t.sleep = orig_sleep
        self.assertEqual(status, "ready")
        self.assertIn("1420", served or "")


class ReasoningFileAlwaysSurfaced(unittest.TestCase):
    def test_empty_brief_still_writes_and_surfaces(self):
        from duckln.recovery import reason_about_plan, thinking_log_path
        from duckln.modes import ControlMode

        class _Outcome:
            answer = ""  # weak model → empty brief

        def _runner(*, emit_thought, **kw):
            emit_thought("read package.json")
            emit_thought("detected tauri + python backend")
            return _Outcome()

        with tempfile.TemporaryDirectory() as t:
            cfg = Path(t)
            out: list[str] = []
            brief = reason_about_plan(
                config_dir=cfg, repo_name="demo", project_dir=cfg,
                execution_target="vm", vm_name="duckln-vm", objective="set up",
                mode=ControlMode.HOOTLWO, approve=None, llm_client=object(),
                emit_thought=None, display=out.append, agent_runner=_runner,
            )
            self.assertIsNone(brief)  # no brief, but…
            # the clickable link is surfaced anyway (was gated on a non-empty brief)
            self.assertTrue(any("logical-thinking.md" in s for s in out), out)
            # and the live thinking is persisted (mirrors the on-screen panel)
            txt = Path(thinking_log_path(cfg)).read_text()
            self.assertIn("detected tauri", txt)


class DiskFullAutoHeal(unittest.TestCase):
    def test_disk_full_is_recognized_as_a_crunch(self):
        from duckln import resource_manager as rm
        crunch = rm.crunch_from_error("rustc-LLVM ERROR: No space left on device (os error 28)")
        self.assertIsNotNone(crunch)
        self.assertEqual(crunch.resource, "disk")

    def test_non_resource_error_is_not_a_crunch(self):
        out: list[str] = []
        res = rb._handle_resource_crunch(
            stderr="ModuleNotFoundError: No module named 'x'", stdout="",
            paths=type("P", (), {"config_dir": Path("/tmp/x")})(), plan=None,
            understanding=type("U", (), {"execution_target": "vm"})(),
            display=out.append, approve=None, executed=[], fake_repo=type("R", (), {"name": "d"})(),
            project_dir=Path("/tmp/x"), current=type("C", (), {"mode": None})(), step=None,
        )
        self.assertIsNone(res)


class TerminalBusyAndActivity(unittest.TestCase):
    def test_shell_prompt_detection(self):
        from duckln.textual_ui import _looks_like_shell_prompt
        self.assertTrue(_looks_like_shell_prompt("ubuntu@duckln-vm:~/p$ "))
        self.assertTrue(_looks_like_shell_prompt("(base) shreyas@Air % "))
        self.assertTrue(_looks_like_shell_prompt("some output\nDUCKLN-DONE-abc:0"))
        self.assertFalse(_looks_like_shell_prompt("   Compiling serde v1.0"))

    def test_activity_halting_messages(self):
        from duckln.textual_ui import _is_activity_halting_message
        self.assertTrue(_is_activity_halting_message("Plan paused for an amendment — run /plan continue"))
        self.assertTrue(_is_activity_halting_message("✅ Plan executed — your repo is ready! 🎉"))
        self.assertTrue(_is_activity_halting_message("Plan halted: needs a token"))
        self.assertFalse(_is_activity_halting_message("Installing dependencies — this can take a moment…"))


if __name__ == "__main__":
    unittest.main()
