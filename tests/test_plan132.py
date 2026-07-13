"""Plan 132 — reasoning by default across surfaces: shared infra (gate, wall-clock
bound, global budget), model-capability gate, and the reasoning PLANNING pass.
(The live multi-turn loop + measured matrix are validated on the VM.)"""

from __future__ import annotations

import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from duckln import recovery as rec


class ReasoningGate(unittest.TestCase):
    def test_default_off_under_unittest_but_on_via_flag(self):
        # We ARE under unittest → default disabled (keeps the suite fast/safe).
        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("DUCKLN_AGENT_RECOVERY", None)
            self.assertFalse(rec.reasoning_enabled())
        with patch.dict("os.environ", {"DUCKLN_AGENT_RECOVERY": "1"}):
            self.assertTrue(rec.reasoning_enabled())
        with patch.dict("os.environ", {"DUCKLN_AGENT_RECOVERY": "0"}):
            self.assertFalse(rec.reasoning_enabled())


class BoundedAgent(unittest.TestCase):
    def test_returns_result(self):
        self.assertEqual(rec.run_bounded_agent(lambda: "ok"), "ok")

    def test_abandons_slow_runner(self):
        self.assertIsNone(rec.run_bounded_agent(lambda: time.sleep(5) or "late", timeout_seconds=0.2))

    def test_swallows_exception(self):
        def boom():
            raise RuntimeError("x")
        self.assertIsNone(rec.run_bounded_agent(boom))


class Budget(unittest.TestCase):
    def test_caps_total_passes(self):
        b = rec.ReasoningBudget(max_passes=2)
        self.assertTrue(b.available()); b.consume()
        self.assertTrue(b.available()); b.consume()
        self.assertFalse(b.available())


class ModelCapabilityGate(unittest.TestCase):
    def test_capable_models(self):
        for m in ("gpt-5", "gpt-4o-mini", "claude-3-5-sonnet", "nvidia/nemotron-3-super-120b", "qwen2.5-coder:32b", "llama3.1:70b"):
            self.assertTrue(rec.model_is_reasoning_capable(m), m)

    def test_too_small_models(self):
        for m in ("gemma2:2b", "qwen2.5:1.5b", "llama3.2:3b", "tinyllama:1.1b"):
            self.assertFalse(rec.model_is_reasoning_capable(m), m)


class ReasoningPlanningPass(unittest.TestCase):
    def test_returns_brief_from_injected_runner(self):
        def fake_runner(**kw):
            return SimpleNamespace(answer="## Brief\nStack: Tauri+Python. Run: npm run tauri dev. Build sidecar first.")
        with TemporaryDirectory() as d:
            brief = rec.reason_about_plan(
                config_dir=Path(d), repo_name="x", project_dir=Path(d),
                execution_target="vm", vm_name="box", objective="run it",
                mode=None, approve=None, llm_client=lambda **k: "", agent_runner=fake_runner,
            )
        self.assertIn("Tauri+Python", brief)

    def test_budget_exhausted_returns_none(self):
        b = rec.ReasoningBudget(max_passes=0)
        out = rec.reason_about_plan(
            config_dir=Path("/tmp"), repo_name="x", project_dir=None,
            execution_target="local", vm_name=None, objective="o",
            mode=None, approve=None, llm_client=lambda **k: "",
            agent_runner=lambda **k: SimpleNamespace(answer="brief"), budget=b,
        )
        self.assertIsNone(out)

    def test_no_llm_returns_none(self):
        self.assertIsNone(rec.reason_about_plan(
            config_dir=Path("/tmp"), repo_name="x", project_dir=None, execution_target="local",
            vm_name=None, objective="o", mode=None, approve=None, llm_client=None))


if __name__ == "__main__":
    unittest.main()
