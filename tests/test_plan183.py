"""Plan 183 — live-run polish batch (Phase 1 UX + Phase 2 planning-correctness/cost + F6).

Covered here (deterministic units + wiring assertions):
- F1 chat wrap, F2 animated dot, F5 step+error hint, F7 native editor
- F4 precheck re-probe, F8/F9 monorepo install + auto-apply-known-fix-first + F8b weak cap, F6 honest-stop popup
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from duckln.repo_bringup import _first_error_hint, _subdir_node_install_steps


class F5ErrorHint(unittest.TestCase):
    def test_picks_error_line(self):
        hint = _first_error_hint("compiling…\nERR_MODULE_NOT_FOUND: Cannot find package 'vite'\nbye")
        self.assertIn("Cannot find package", hint)

    def test_falls_back_to_last_line(self):
        self.assertEqual(_first_error_hint("all good\nfinished cleanly"), "finished cleanly")

    def test_empty(self):
        self.assertEqual(_first_error_hint(""), "")

    def test_bounded_length(self):
        self.assertLessEqual(len(_first_error_hint("error " + "x" * 500)), 160)


class F8F9MonorepoInstall(unittest.TestCase):
    def test_per_subdir_install_steps(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            for sub in ("frontend", "backend"):
                os.makedirs(base / sub)
                (base / sub / "package.json").write_text("{}")
            steps = _subdir_node_install_steps(base, ("package.json",), "local")
            cmds = [s.command for s in steps]
            self.assertTrue(any("cd frontend &&" in c and "--include=dev" in c for c in cmds))
            self.assertTrue(any("cd backend &&" in c for c in cmds))

    def test_no_subdirs_no_marker_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(_subdir_node_install_steps(Path(d), ("package.json",), "local"), [])

    def test_marker_fallback_when_no_local_clone(self):
        steps = _subdir_node_install_steps(
            Path("/nonexistent-duckln-xyz"), ("package.json", "pnpm-workspace.yaml"), "vm"
        )
        self.assertEqual(len(steps), 1)
        self.assertIn("package.json", steps[0].command)
        self.assertIn("--include=dev", steps[0].command)


class F8F9AutoApplyFirstWired(unittest.TestCase):
    def test_known_fix_auto_applies_before_reasoning(self):
        src = Path("src/duckln/repo_bringup.py").read_text(encoding="utf-8")
        # The deterministic auto-apply runs even when a model is configured, before recovery.
        self.assertIn("Plan 183 F8/F9", src)
        self.assertIn("_cont_known = _auto_apply_recovery_fix(", src)


class F8bWeakModelCap(unittest.TestCase):
    def test_multi_agent_prepass_gated_on_capability(self):
        src = Path("src/duckln/recovery.py").read_text(encoding="utf-8")
        self.assertIn("Plan 183 F8b", src)
        self.assertIn("reasoning_enabled() and _cap_capable", src)


class F4PrecheckReprobe(unittest.TestCase):
    def test_reprobe_when_present_empty(self):
        src = Path("src/duckln/repo_bringup.py").read_text(encoding="utf-8")
        self.assertIn("Plan 183 F4", src)
        self.assertIn("if not present:", src)


class F1F2UI(unittest.TestCase):
    def test_chat_wrap_enabled(self):
        src = Path("src/duckln/textual_ui.py").read_text(encoding="utf-8")
        self.assertIn('RichLog(id="chat-scroll", wrap=True', src)

    def test_provider_dot_pulses_while_busy(self):
        src = Path("src/duckln/textual_ui.py").read_text(encoding="utf-8")
        self.assertIn("Plan 183 F2", src)
        self.assertIn("provider_glyph = _SPINNER_FRAMES[_idx", src)


class F7NativeEditor(unittest.TestCase):
    def test_opens_in_text_editor_per_os(self):
        from duckln.textual_ui import _open_in_text_editor

        calls = []

        class _FakePopen:
            def __init__(self, argv, **kw):
                calls.append(argv)

        with patch("platform.system", return_value="Darwin"), \
             patch("subprocess.Popen", _FakePopen):
            ok = _open_in_text_editor("/tmp/logical-thinking.md")
        self.assertTrue(ok)
        self.assertEqual(calls[0][0], "open")  # macOS opens via `open -t` (default text editor)
        self.assertIn("-t", calls[0])


class F6HonestStopPopup(unittest.TestCase):
    def test_interactive_proposal_wired(self):
        src = Path("src/duckln/repo_bringup.py").read_text(encoding="utf-8")
        self.assertIn("Plan 183 F6", src)
        self.assertIn("from duckln.interaction import propose_and_confirm", src)
        self.assertIn("Continue — run", src)


class F11ModeElevation(unittest.TestCase):
    def _cfg(self, mode):
        from duckln.ai_client import Provider
        from duckln.config import AppConfig
        return AppConfig(provider=Provider.OLLAMA, model="m", api_key=None, base_url=None, mode=mode)

    def test_non_matching_message_passes_through(self):
        from duckln.main import _maybe_offer_mode_elevation
        from duckln.modes import ControlMode

        out = _maybe_offer_mode_elevation(
            "how are you?", current=self._cfg(ControlMode.HITL),
            paths=SimpleNamespace(config_dir=Path("/tmp")), display_output=lambda _t: None, approve_prompt=None,
        )
        self.assertIsNone(out)  # not a mode-elevation message → fall through

    def test_hitl_offers_and_switches_on_consent(self):
        from duckln.main import _maybe_offer_mode_elevation
        from duckln.modes import ControlMode

        with tempfile.TemporaryDirectory() as d:
            msgs: list[str] = []
            out = _maybe_offer_mode_elevation(
                "just do it all, don't ask me each step", current=self._cfg(ControlMode.HITL),
                paths=SimpleNamespace(config_dir=Path(d)), display_output=msgs.append,
                approve_prompt=lambda _m: True,  # user consents
            )
            self.assertIsNotNone(out)
            self.assertEqual(out.mode, ControlMode.HOOTLWO)  # elevated
            self.assertTrue(any("destructive" in m.lower() for m in msgs))  # honest about the floor

    def test_hitl_declined_keeps_mode(self):
        from duckln.main import _maybe_offer_mode_elevation
        from duckln.modes import ControlMode

        out = _maybe_offer_mode_elevation(
            "complete the full operation without asking", current=self._cfg(ControlMode.HITL),
            paths=SimpleNamespace(config_dir=Path("/tmp")), display_output=lambda _t: None,
            approve_prompt=lambda _m: False,  # user declines
        )
        self.assertEqual(out.mode, ControlMode.HITL)  # unchanged

    def test_already_hootlwo_just_acknowledges(self):
        from duckln.main import _maybe_offer_mode_elevation
        from duckln.modes import ControlMode

        msgs: list[str] = []
        out = _maybe_offer_mode_elevation(
            "do it all without asking", current=self._cfg(ControlMode.HOOTLWO),
            paths=SimpleNamespace(config_dir=Path("/tmp")), display_output=msgs.append, approve_prompt=None,
        )
        self.assertEqual(out.mode, ControlMode.HOOTLWO)
        self.assertTrue(any("already in auto" in m.lower() for m in msgs))


if __name__ == "__main__":
    unittest.main()
