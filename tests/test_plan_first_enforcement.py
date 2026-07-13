"""Plan 72 Phase 4 — default-ON + plan-first enforcement chokepoint."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from duckln.ai_client import Provider
from duckln.config import (
    AppConfig,
    deserialize_app_config,
    resolve_config_paths,
    save_app_config,
    load_app_config,
)
from duckln.modes import ControlMode
from duckln.plan_mode import plan_first_required, require_plan_for_mutation


class DefaultOnTest(unittest.TestCase):
    def test_absent_key_defaults_on(self) -> None:
        payload = {"provider": "ollama", "model": "gemma2:2b", "api_key": None, "mode": "hootlwo",
                   "base_url": "http://localhost:11434"}
        cfg = deserialize_app_config(payload)
        self.assertTrue(cfg.plan_mode_enabled)

    def test_explicit_false_is_coerced_on_at_load(self) -> None:
        # Plan 188: there is no OFF — a legacy stored `false` is coerced on at the live LOAD
        # boundary (serialize/deserialize stay faithful).
        payload = {"provider": "ollama", "model": "gemma2:2b", "api_key": None, "mode": "hootlwo",
                   "base_url": "http://localhost:11434", "plan_mode_enabled": False}
        self.assertFalse(deserialize_app_config(payload).plan_mode_enabled)  # faithful
        with tempfile.TemporaryDirectory() as td:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": td})
            save_app_config(deserialize_app_config(payload), paths)
            self.assertTrue(load_app_config(paths).plan_mode_enabled)  # coerced on at load

    def test_explicit_true_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": td})
            save_app_config(
                AppConfig(provider=Provider.OPENAI, model="gpt", api_key="k",
                          mode=ControlMode.HOTL, plan_mode_enabled=True), paths)
            self.assertTrue(load_app_config(paths).plan_mode_enabled)


class ChokepointTest(unittest.TestCase):
    def test_s0_always_passes(self) -> None:
        self.assertFalse(plan_first_required(plan_mode_enabled=True, safety_class="S0"))
        self.assertTrue(require_plan_for_mutation(plan_mode_enabled=True, command="pwd"))

    def test_s2plus_requires_plan_when_on(self) -> None:
        self.assertTrue(plan_first_required(plan_mode_enabled=True, safety_class="S2"))
        out: list[str] = []
        allowed = require_plan_for_mutation(
            plan_mode_enabled=True, command="apt-get install -y nodejs",
            display=out.append, action_label="install Node",
        )
        self.assertFalse(allowed)
        self.assertIn("approved plan", "\n".join(out).lower())

    def test_off_allows_direct(self) -> None:
        self.assertFalse(plan_first_required(plan_mode_enabled=False, safety_class="S3"))
        self.assertTrue(require_plan_for_mutation(plan_mode_enabled=False, command="apt-get install -y x"))


if __name__ == "__main__":
    unittest.main()
