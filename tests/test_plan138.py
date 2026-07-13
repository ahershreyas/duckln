"""Plan 138 — stop the self-inflicted cascade + read the real error + web search + clickable log:
F1 the detached desktop launch is `env VAR=val …` so `nohup env …` runs (no exit-127).
F2 an apt dependency conflict is diagnosed as such (not "install a compiler").
F3 the `🧠 Reasoning captured` log line renders as a clickable hyperlink.
F4 when stuck, Duckln announces a web search (internet on) or nudges to enable it (off).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import duckln.repo_bringup as rb


class ValidDetachedLaunch(unittest.TestCase):
    def test_env_prefix_survives_nohup(self):
        for headless in (True, False):
            cmd = rb._desktop_launch_command("npm run tauri dev", "tauri", headless=headless)
            self.assertTrue(cmd.startswith("env "), cmd)
            # the first token after `nohup` must be a REAL program (`env`), not `PATH=…`
            self.assertEqual(f"nohup {cmd}".split()[1], "env")
            self.assertIn(".cargo/bin", cmd)
            self.assertNotIn(";", cmd)  # still nohup/$!-safe
        # a non-headless, non-tauri command has no env to apply → unchanged
        self.assertEqual(rb._desktop_launch_command("electron .", "electron", headless=False), "electron .")


class ReadTheRealError(unittest.TestCase):
    APT = ("The following packages have unmet dependencies:\n"
           " nodejs : Conflicts: npm\n"
           " npm : Depends: node-gyp but it is not going to be installed\n"
           "E: Unable to correct problems, you have held broken packages.")

    def test_apt_conflict_is_not_a_missing_compiler(self):
        from duckln.diagnostics import match_deterministic_fix, ErrorCategory
        f = match_deterministic_fix(stderr=self.APT, exit_code=100,
                                    command="sudo apt-get install -y nodejs", execution_target="vm")
        self.assertIsNotNone(f)
        self.assertEqual(f.category, ErrorCategory.APT_CONFLICT)
        self.assertNotIn("build-essential", f.fix_command)  # NOT a compiler install

    def test_real_gyp_build_error_still_gets_compiler(self):
        from duckln.diagnostics import match_deterministic_fix, ErrorCategory
        f = match_deterministic_fix(stderr="gyp ERR! build error\nnode-gyp rebuild failed",
                                    exit_code=1, command="npm install", execution_target="vm")
        self.assertIsNotNone(f)
        self.assertEqual(f.category, ErrorCategory.MISSING_COMPILER)

    def test_implausible_compiler_fix_for_apt_conflict_rejected(self):
        self.assertFalse(rb._amendment_fix_is_plausible(self.APT, "sudo apt-get install -y build-essential"))
        self.assertTrue(rb._amendment_fix_is_plausible(self.APT, "sudo apt-get --fix-broken install -y"))


class ClickableReasoningLog(unittest.TestCase):
    def test_link_line_renders_clickable(self):
        try:
            from duckln.textual_ui import _reasoning_link_renderable
        except Exception:
            self.skipTest("textual not available")
        line = "🧠 Reasoning captured → [logical-thinking.md](file:///Users/x/.duckln/memory/logical-thinking.md) (click to open)"
        t = _reasoning_link_renderable(line)
        self.assertIsNotNone(t)
        import io
        from rich.console import Console
        c = Console(file=io.StringIO(), force_terminal=True)
        c.print(t)
        out = c.file.getvalue()
        self.assertIn("\x1b]8", out)                       # OSC-8 hyperlink emitted
        self.assertIn("file:///Users/x", out)
        self.assertIsNone(_reasoning_link_renderable("Installing deps…"))


class WebSearchWhenStuck(unittest.TestCase):
    def _paths(self, t):
        from duckln.config import resolve_config_paths
        from state.store import initialize_state_store
        paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": t})
        initialize_state_store(paths.config_dir)
        return paths

    def test_internet_off_nudges_to_enable(self):
        with tempfile.TemporaryDirectory() as t:
            paths = self._paths(t)
            out: list[str] = []
            note = rb._web_evidence_for_failed_step(
                config_dir=paths.config_dir, command="x", error_text="boom",
                execution_target="vm", display=out.append,
            )
            self.assertIsNone(note)  # internet off → no evidence
            self.assertTrue(any("/internet on" in s for s in out), out)

    def test_recovery_prompt_directs_web_search(self):
        from duckln.recovery import _recovery_spec_prompt
        low = _recovery_spec_prompt("recovery_agent").lower()
        self.assertIn("web.search", low)
        self.assertIn("check the web", low)


if __name__ == "__main__":
    unittest.main()
