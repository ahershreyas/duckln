"""Plan 73 — gating helper, spinner ceiling, runtime-repair gate."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from duckln.ai_client import Provider
from duckln.config import AppConfig, resolve_config_paths, save_app_config
from duckln.modes import ControlMode
from duckln.repo_bringup import gate_mutation_or_draft
from duckln.textual_ui import _ACTIVITY_HARD_CEILING_SECONDS, _activity_bar_segments
from state.access import read_pending_plan


def _cfg(plan_mode: bool):
    return AppConfig(provider=Provider.OLLAMA, model="gemma2:2b", api_key=None,
                     mode=ControlMode.HOOTLWO, plan_mode_enabled=plan_mode,
                     base_url="http://localhost:11434")


class GateMutationTest(unittest.TestCase):
    def test_gated_when_on_drafts_pending_and_runs_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": td})
            save_app_config(_cfg(True), paths)
            out: list[str] = []
            # Hermetic: force an APPROVE critic verdict so the draft is deterministic and not
            # dependent on live-model reachability (which varies by test ordering).
            approve = SimpleNamespace(verdict="approve", external_blocker=None,
                                      missing_question=None, reason="ok")
            with patch("duckln.plan_mode.critic_review", return_value=approve):
                gated = gate_mutation_or_draft(
                    intended_steps=(("Launch VM", "multipass launch --name duckln-vm", "running"),),
                    objective="Provision VM", repo_slug=None, paths=paths,
                    current=_cfg(True), display=out.append,
                )
            self.assertTrue(gated)
            self.assertIsNotNone(read_pending_plan(paths.config_dir))
            self.assertIn("not run", "\n".join(out).lower())

    def test_not_gated_when_off(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": td})
            save_app_config(_cfg(False), paths)
            gated = gate_mutation_or_draft(
                intended_steps=(("x", "multipass launch", "y"),),
                objective="x", repo_slug=None, paths=paths,
                current=_cfg(False), display=lambda _m: None,
            )
            self.assertFalse(gated)


class RuntimeRepairGateTest(unittest.TestCase):
    def test_runtime_repair_offers_consent_not_plan_off(self) -> None:
        # Plan 188 F2c: Plan Mode is always on, so the repair is OFFERED (Yes/No), not blocked
        # behind a removed "/plan off". With no interactive channel the offer defaults to REJECT,
        # leaving the blocker paused; the message references neither "/plan off" nor "/plan approve".
        from duckln.main import _run_runtime_repair_workflow
        from state.repo_catalog import RepoCatalogRecord

        with tempfile.TemporaryDirectory() as td:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": td})
            repo = RepoCatalogRecord(name="x", repo_url="o/x", stars=0, description="",
                                     category="", framework="", last_updated="")
            out: list[str] = []
            _run_runtime_repair_workflow(
                repo=repo, current=_cfg(True), paths=paths,
                display_output=out.append, approve_prompt=None,
                terminal_interface=None, run_summary="boom",
            )
            joined = "\n".join(out).lower()
            self.assertNotIn("plan approve", joined)
            self.assertNotIn("/plan off", joined)
            self.assertIn("paused", joined)


class SpinnerCeilingTest(unittest.TestCase):
    def test_fresh_spins(self) -> None:
        _p, _b, on = _activity_bar_segments(activity_text="Working...", spinner_on=True,
                                            elapsed_seconds=5, spinner_frame="X")
        self.assertTrue(on)

    def test_slow_notice_still_spins(self) -> None:
        _p, body, on = _activity_bar_segments(activity_text="Working...", spinner_on=True,
                                              elapsed_seconds=200, spinner_frame="X")
        self.assertTrue(on)
        self.assertIn("taking longer than expected", body)

    def test_ceiling_stops_spinner_and_shows_notice(self) -> None:
        _p, body, on = _activity_bar_segments(
            activity_text="Working...", spinner_on=True,
            elapsed_seconds=_ACTIVITY_HARD_CEILING_SECONDS + 1, spinner_frame="X")
        self.assertFalse(on)
        self.assertIn("blocked", body.lower())


if __name__ == "__main__":
    unittest.main()
