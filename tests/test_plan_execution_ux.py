from __future__ import annotations

import tempfile
import unittest

from duckln.ai_client import Provider
from duckln.config import AppConfig, resolve_config_paths, save_app_config
from duckln.modes import ControlMode
from duckln.plan_mode import PlanRecord, PlanStep
from state.access import write_config_snapshot


def _paths(td):
    paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": td.name})
    save_app_config(AppConfig(provider=Provider.OPENAI, model="gpt", api_key=None,
                              mode=ControlMode.HOOTLWO, plan_mode_enabled=True), paths)
    return paths


class _OK:
    exit_code = 0
    stdout = "ok"
    stderr = ""
    timed_out = False


class _Runner:
    def run(self, command, *a, **k):
        return _OK()


def _plan():
    steps = (
        PlanStep(index=1, title="Install deps", description="", command="npm install",
                 safety_class="S0", verification="exit 0", rationale="", estimated_seconds=1, target="local"),
        PlanStep(index=2, title="Run demo", description="", command="echo run",
                 safety_class="S0", verification="exit 0", rationale="", estimated_seconds=1, target="local"),
    )
    return PlanRecord(
        plan_id="p1", objective="Set up demo", context_summary="family=node_typescript",
        steps=steps, risks=(), rollback="", estimated_seconds=2,
        created_at="2026-05-28T00:00:00Z", status="approved", repo_slug="owner/demo",
        mode_at_creation="hootlwo",
    )


class TestExecutionUX(unittest.TestCase):
    def test_checkboxes_celebration_and_live_thoughts(self):
        from duckln.repo_bringup import resume_with_approved_plan

        with tempfile.TemporaryDirectory() as tdname:
            td = type("T", (), {"name": tdname})()
            paths = _paths(td)
            write_config_snapshot(paths.config_dir, {"execution_target": "local"})
            cfg = AppConfig(provider=Provider.OPENAI, model="gpt", api_key=None,
                            mode=ControlMode.HOOTLWO, plan_mode_enabled=True)
            out: list[str] = []
            thoughts: list[str] = []
            resume_with_approved_plan(
                plan=_plan(), paths=paths, current=cfg,
                approve=lambda _m: True, display=out.append, runner=_Runner(),
                emit_thought=thoughts.append,
            )
            joined = "\n".join(out)
            # Per-step completion checkboxes.
            self.assertTrue(any(line.startswith("☑ Step 1") for line in out))
            self.assertTrue(any(line.startswith("☑ Step 2") for line in out))
            # Final celebration.
            self.assertIn("your repo is ready! 🎉", joined)
            # Live thoughts streamed during execution (≥ one per step + start).
            self.assertGreaterEqual(len(thoughts), 2)


if __name__ == "__main__":
    unittest.main()
