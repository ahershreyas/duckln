"""Plan 186 — production-grade GUIDANCE + clarity batch:
F1 one clean Ollama pull progress bar + a model UNINSTALL path; F2 plain-English connectivity +
smart startup reconnect + situation-aware next steps; F3 a Yes/No/Other/Cancel popup primitive;
F4 the header Plan label shows "off" (never disappears)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch


# --- F4: header Plan: off (never disappears) --------------------------------

class F4PlanHeaderLabel(unittest.TestCase):
    def _segment(self, *, enabled, pending):
        from duckln import textual_ui

        with patch("duckln.config.resolve_config_paths", return_value=object()), \
             patch("duckln.config.load_app_config", return_value=SimpleNamespace(plan_mode_enabled=enabled)), \
             patch("state.access.read_pending_plan", return_value=pending):
            return textual_ui._plan_mode_header_segment("/tmp/cfg")

    # Plan 188: Plan Mode is always on — there is no "Plan: off" state anymore.

    def test_on_no_plan_is_green_on(self):
        seg = self._segment(enabled=True, pending=None)
        _glyph, style, label = seg
        self.assertEqual(label, "Plan: on")
        self.assertEqual(style, "green")

    def test_on_pending_is_orange_status(self):
        seg = self._segment(enabled=True, pending={"status": "pending"})
        _glyph, style, label = seg
        self.assertEqual(label, "Plan: pending")
        self.assertEqual(style, "orange1")


# --- F3: Yes/No/Other/Cancel popup primitive --------------------------------

class F3ProposeAndConfirm(unittest.TestCase):
    def _run(self, *, select_returns, text_prompt_returns="", allow_cancel=True):
        from duckln.interaction import propose_and_confirm

        return propose_and_confirm(
            situation="The VM connection dropped.",
            recommendation="restart it",
            display=lambda _m: None,
            select=lambda _q, _opts: select_returns,
            text_prompt=lambda _q, _d: text_prompt_returns,
            allow_cancel=allow_cancel,
        )

    def test_cancel(self):
        d = self._run(select_returns="Cancel")
        self.assertTrue(d.cancelled)
        self.assertEqual(d.outcome, "cancel")

    def test_accept(self):
        self.assertTrue(self._run(select_returns="Yes — go ahead").accepted)

    def test_reject(self):
        self.assertTrue(self._run(select_returns="No — skip this").rejected)

    def test_custom(self):
        d = self._run(select_returns="Let me tell you what to do instead", text_prompt_returns="run foo")
        self.assertTrue(d.is_custom)
        self.assertEqual(d.custom_text, "run foo")

    def test_no_cancel_option_when_not_allowed(self):
        # allow_cancel=False → Cancel isn't offered; an unrecognized label is REJECT (safe).
        d = self._run(select_returns="Cancel", allow_cancel=False)
        self.assertTrue(d.rejected)

    def test_no_select_falls_back_to_approve(self):
        from duckln.interaction import propose_and_confirm

        d = propose_and_confirm(
            situation="x", recommendation="y", display=lambda _m: None,
            approve=lambda _q: True,
        )
        self.assertTrue(d.accepted)


# --- F1a: one clean Ollama pull progress bar --------------------------------

class _FakeProc:
    def __init__(self, lines, code=0):
        self.stdout = iter(lines)
        self._code = code

    def wait(self):
        return self._code


class F1aOllamaPullProgress(unittest.TestCase):
    def test_only_progress_and_confirm_reach_display(self):
        from duckln import config

        lines = [
            "pulling manifest \n",
            "pulling\n",
            "pulling abc123... 45% ▕██▗ 1.2 GB/2.5 GB\n",
            "verifying sha256\n",
            "writing manifest\n",
            "success\n",
        ]
        seen: list[str] = []
        with patch.object(config.subprocess, "Popen", return_value=_FakeProc(lines)):
            config._run_ollama_pull_subprocess("gemma2:9b", display=seen.append)
        # The download-progress line (has a %) is forwarded; phase-only noise is swallowed.
        self.assertTrue(any("45%" in m for m in seen))
        for noise in ("pulling", "verifying sha256", "writing manifest", "success", "pulling manifest"):
            self.assertNotIn(noise, seen)
        # Exactly one confirmation line on success.
        self.assertTrue(any(m == "✓ gemma2:9b downloaded and ready." for m in seen))

    def test_line_has_percent(self):
        from duckln.config import _ollama_line_has_percent

        self.assertTrue(_ollama_line_has_percent("pulling abc... 45% xyz"))
        self.assertFalse(_ollama_line_has_percent("pulling"))
        self.assertFalse(_ollama_line_has_percent("100 percent done"))

    def test_bare_pulling_collapses_into_activity(self):
        from duckln.textual_ui import _transient_activity_update

        self.assertIsNotNone(_transient_activity_update("pulling"))
        self.assertIsNotNone(_transient_activity_update("pulling manifest"))

    def test_nonzero_exit_raises(self):
        from duckln import config
        from duckln.config import OnboardingError

        with patch.object(config.subprocess, "Popen", return_value=_FakeProc(["success\n"], code=1)):
            with self.assertRaises(OnboardingError):
                config._run_ollama_pull_subprocess("gemma2:9b", display=lambda _m: None)


# --- F1b: uninstall / remove an installed Ollama model ----------------------

class F1bRemoveOllamaModel(unittest.TestCase):
    def _models(self):
        from duckln.ai_client import ProviderModel

        return (ProviderModel("gemma2:9b", "gemma2:9b"), ProviderModel("llama3.2:1b", "llama3.2:1b"))

    def test_remove_confirmed(self):
        from duckln import config

        answers = iter(["gemma2:9b", "Yes, remove it"])
        adapter = SimpleNamespace(
            validate_api_key=lambda _k, client=None: SimpleNamespace(
                ok=True, models=(config.ProviderModel("llama3.2:1b", "llama3.2:1b"),)
            )
        )
        run_calls: list[list[str]] = []

        def _fake_run(cmd, **_kw):
            run_calls.append(cmd)
            return SimpleNamespace(returncode=0, stdout="")

        with patch.object(config.subprocess, "run", side_effect=_fake_run), \
             patch.object(config, "_sorted_models", side_effect=lambda ms: tuple(ms)):
            refreshed = config._remove_ollama_model(
                lambda _q, _opts: next(answers),
                self._models(),
                adapter=adapter, api_key=None, display=lambda _m: None, client=None,
            )
        self.assertEqual(run_calls, [["ollama", "rm", "gemma2:9b"]])
        self.assertNotIn("gemma2:9b", tuple(m.id for m in refreshed))

    def test_remove_cancelled_removes_nothing(self):
        from duckln import config

        run_calls: list[list[str]] = []
        with patch.object(config.subprocess, "run", side_effect=lambda c, **k: run_calls.append(c)):
            models = self._models()
            refreshed = config._remove_ollama_model(
                lambda _q, _opts: "Cancel",
                models,
                adapter=None, api_key=None, display=lambda _m: None, client=None,
            )
        self.assertEqual(run_calls, [])
        self.assertEqual(refreshed, models)


# --- F2a: plain-English connectivity notice ---------------------------------

class F2aOfflineNotice(unittest.TestCase):
    def test_ollama_friendly_no_jargon(self):
        from duckln import connection_status as cs

        cfg = SimpleNamespace(provider=SimpleNamespace(value="ollama"), model="gemma2:9b", base_url="http://localhost:11434")
        with patch.object(cs, "probe_provider_status", return_value="red"):
            msg = cs.startup_offline_notice(config=cfg)
        self.assertIn("Ollama isn't running yet", msg)
        self.assertNotIn("Replies that need the model will fail", msg)

    def test_cloud_provider_wording(self):
        from duckln import connection_status as cs

        cfg = SimpleNamespace(provider=SimpleNamespace(value="openai"), model="gpt-4o", base_url="")
        with patch.object(cs, "probe_provider_status", return_value="red"):
            msg = cs.startup_offline_notice(config=cfg)
        self.assertIn("openai", msg)
        self.assertNotIn("ollama", msg.lower())

    def test_green_no_notice(self):
        from duckln import connection_status as cs

        cfg = SimpleNamespace(provider=SimpleNamespace(value="ollama"), model="gemma2:9b", base_url="")
        with patch.object(cs, "probe_provider_status", return_value="green"):
            self.assertIsNone(cs.startup_offline_notice(config=cfg))


# Plan 188: the Plan-186 F2c `/plan off` resume-hint was removed — there is no `/plan off`
# anymore (Plan Mode is always on), so its tests are gone with it.


if __name__ == "__main__":
    unittest.main()
