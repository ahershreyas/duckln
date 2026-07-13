"""Plan 188 — (F1) a ROBUST "which repo did we run last" matcher; (F2) Plan Mode is ALWAYS ON +
auto-decide (no OFF): the config is coerced on, `/plan on`/`/plan off` are no-op explanations, the
runtime-repair path is a consented offer (not a "/plan off" dead-end), and the stale OFF code is gone.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


# --- F1: robust "which repo did we run last" matcher ------------------------

class F1PreviousRepoRobust(unittest.TestCase):
    def _row(self):
        return SimpleNamespace(
            metadata={"repo_name": "JustHireMe"}, repo_path="/x/JustHireMe",
            repo_key="k", repo_url="https://github.com/vasu-devs/JustHireMe",
        )

    def _ask(self, msg, *, row="__default__"):
        from duckln import main

        seen: list[str] = []
        store = SimpleNamespace(get_latest_repo_state=lambda: (self._row() if row == "__default__" else row))
        with patch.object(main, "initialize_state_store", return_value=store):
            handled = main._maybe_answer_previous_repo(msg, Path("/tmp"), seen.append)
        return handled, seen

    def test_matches_paraphrases(self):
        for msg in (
            "which repo we ran last?",
            "the last repo we ran",
            "what repo were we on earlier",
            "which repo we were working on previously",
            "which repository did we run before",
        ):
            handled, seen = self._ask(msg)
            self.assertTrue(handled, msg)
            self.assertIn("JustHireMe", seen[0])
            self.assertNotIn("state.read", seen[0])

    def test_no_active_repo(self):
        handled, seen = self._ask("which repo we ran last?", row=None)
        self.assertTrue(handled)
        self.assertIn("don't have a previous repo", seen[0])

    def test_does_not_fire_on_future_or_unrelated(self):
        for msg in (
            "which repo should I set up next",
            "recommend a repo to work on",
            "how does auth work?",
            "set up a new repo",
            "what is the last commit",  # no first-person session subject
        ):
            handled, _ = self._ask(msg)
            self.assertFalse(handled, msg)


# --- F2a: Plan Mode always on (coerced) + /plan on/off are no-ops -----------

class F2aAlwaysOn(unittest.TestCase):
    def test_stored_false_is_coerced_on_at_load(self):
        from duckln.ai_client import Provider
        from duckln.config import AppConfig, resolve_config_paths, save_app_config, load_app_config
        from duckln.modes import ControlMode

        with tempfile.TemporaryDirectory() as td:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": td})
            save_app_config(
                AppConfig(provider=Provider.OLLAMA, model="gemma2:9b", api_key=None,
                          base_url="http://localhost:11434", mode=ControlMode.HOOTLWO,
                          plan_mode_enabled=False),
                paths,
            )
            self.assertTrue(load_app_config(paths).plan_mode_enabled)  # coerced on at load

    def _cfg(self):
        from duckln.ai_client import Provider
        from duckln.config import AppConfig, resolve_config_paths, save_app_config
        from duckln.modes import ControlMode

        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": td.name})
        cfg = AppConfig(provider=Provider.OPENAI, model="gpt-4o-mini", api_key="k",
                        mode=ControlMode.HOTL, plan_mode_enabled=True)
        save_app_config(cfg, paths)
        return paths, cfg

    def test_plan_off_is_noop_explains(self):
        from duckln.main import _handle_plan_command

        paths, cfg = self._cfg()
        out: list[str] = []
        updated = _handle_plan_command(command="/plan off", current=cfg, paths=paths, display=out.append, approve=None)
        self.assertTrue(updated.plan_mode_enabled)  # NOT disabled
        joined = "\n".join(out)
        self.assertIn("always on", joined)
        self.assertNotIn("disabled", joined.lower())

    def test_plan_on_is_noop_explains(self):
        from duckln.main import _handle_plan_command

        paths, cfg = self._cfg()
        out: list[str] = []
        _handle_plan_command(command="/plan on", current=cfg, paths=paths, display=out.append, approve=None)
        self.assertTrue(any("always on" in line for line in out))


# --- F2c: runtime repair is a consented offer, not a "/plan off" dead-end ----

class F2cConsentedRepair(unittest.TestCase):
    def test_declined_offer_leaves_paused_no_plan_off(self):
        import duckln.main as m
        from duckln.config import AppConfig, ConfigPaths
        from duckln.ai_client import Provider
        from duckln.modes import ControlMode
        from state.repo_catalog import RepoCatalogRecord

        repo = RepoCatalogRecord(name="JustHireMe", repo_url="https://github.com/x/JustHireMe",
                                 stars=0, description="", category="", framework="", last_updated="")
        cfg = AppConfig(provider=Provider.OLLAMA, model="gemma2:9b", api_key=None,
                        mode=ControlMode.HOOTLWO, base_url="http://localhost:11434", plan_mode_enabled=True)
        with tempfile.TemporaryDirectory() as td:
            cd = Path(td)
            paths = ConfigPaths(config_dir=cd, config_file=cd / "config.json")
            out: list[str] = []
            m._run_runtime_repair_workflow(
                repo=repo, current=cfg, paths=paths, display_output=out.append,
                approve_prompt=lambda _m: False, terminal_interface=None, run_summary="boom",
            )
            joined = "\n".join(out)
            self.assertNotIn("/plan off", joined)
            self.assertNotIn("/plan approve", joined)
            self.assertIn("paused", joined.lower())


# --- F2d: the OFF surface is gone -------------------------------------------

class F2dStaleOffRemoved(unittest.TestCase):
    def test_header_has_no_off_state(self):
        from duckln import textual_ui

        with patch("duckln.config.resolve_config_paths", return_value=object()), \
             patch("duckln.config.load_app_config", return_value=SimpleNamespace(plan_mode_enabled=True)), \
             patch("state.access.read_pending_plan", return_value=None):
            seg = textual_ui._plan_mode_header_segment("/tmp/cfg")
        _glyph, _style, label = seg
        self.assertEqual(label, "Plan: on")

    def test_resume_hint_helper_removed(self):
        from duckln import main

        self.assertFalse(hasattr(main, "_paused_objective_resume_hint"))

    def test_plan_off_descriptor_gone(self):
        src = Path("src/duckln/main.py").read_text(encoding="utf-8")
        self.assertNotIn('SlashCommandDescriptor("/plan off"', src)
        self.assertNotIn('SlashCommandDescriptor("/plan on"', src)


if __name__ == "__main__":
    unittest.main()
