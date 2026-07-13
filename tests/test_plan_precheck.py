from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from duckln.ai_client import Provider
from duckln.config import AppConfig, ConfigPaths, save_app_config, load_app_config
from duckln.main import _handle_plan_command
from duckln.modes import ControlMode
from duckln.plan_mode import PlanStep
from duckln.repo_bringup import (
    RepoBringUpPlan,
    RepoBringUpStep,
    _assemble_plansteps_with_clone_and_run,
    _drop_satisfied_prereqs,
)
from state.repo_catalog import RepoCatalogRecord


def _repo():
    return RepoCatalogRecord(
        "demo",
        "https://github.com/acme/demo",
        10,
        "Demo repo.",
        "Custom",
        "TypeScript",
        "2026-04-01",
    )


def _prereq(name):
    return RepoBringUpStep(
        purpose=f"Ensure {name} is installed before setup runs",
        command=f"{name} --version",
    )


class TestDropPresentPrereqs(unittest.TestCase):
    def test_drops_installed_tools_only(self):
        steps = (
            _prereq("node"),
            _prereq("git"),
            RepoBringUpStep(purpose="Install dependencies", command="npm install"),
        )
        kept = _drop_satisfied_prereqs(steps, {"node", "git"})
        purposes = [s.purpose for s in kept]
        self.assertEqual(purposes, ["Install dependencies"])

    def test_no_present_tools_keeps_all(self):
        steps = (_prereq("node"), _prereq("git"))
        kept = _drop_satisfied_prereqs(steps, set())
        self.assertEqual(len(kept), 2)


class TestAssembleWithPrecheck(unittest.TestCase):
    def _plan(self, steps):
        repo = _repo()
        return RepoBringUpPlan(
            repo=repo,
            project_dir=Path("/tmp/demo"),
            detected_files=("package.json",),
            steps=tuple(steps),
            summary="node setup",
        )

    def _inspection(self):
        return SimpleNamespace(repo_knowledge=SimpleNamespace(entrypoints=("npm run dev",)))

    def test_precheck_on_omits_clone_and_installed_prereq(self):
        steps = [
            _prereq("node"),
            RepoBringUpStep(purpose="Install dependencies", command="npm install"),
        ]
        filtered = _drop_satisfied_prereqs(tuple(steps), {"node"})
        plan = self._plan(filtered)
        out = _assemble_plansteps_with_clone_and_run(
            PlanStep=PlanStep,
            repo=_repo(),
            runtime_dir="/tmp/demo",
            bringup_plan=plan,
            inspection=self._inspection(),
            skip_clone=True,
        )
        commands = [s.command for s in out]
        self.assertFalse(any("git clone" in (c or "") for c in commands))
        self.assertFalse(any("node --version" in (c or "") for c in commands))
        self.assertTrue(any("npm install" in (c or "") for c in commands))
        self.assertTrue(any("npm run dev" in (c or "") for c in commands))

    def test_precheck_off_keeps_clone_and_prereqs(self):
        steps = [
            _prereq("node"),
            RepoBringUpStep(purpose="Install dependencies", command="npm install"),
        ]
        plan = self._plan(steps)
        out = _assemble_plansteps_with_clone_and_run(
            PlanStep=PlanStep,
            repo=_repo(),
            runtime_dir="/tmp/demo",
            bringup_plan=plan,
            inspection=self._inspection(),
            skip_clone=False,
        )
        commands = [s.command for s in out]
        self.assertTrue(any("git clone" in (c or "") for c in commands))
        self.assertTrue(any("node --version" in (c or "") for c in commands))


class TestPlanPrecheckCommand(unittest.TestCase):
    def test_precheck_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = ConfigPaths(config_dir=Path(tmp), config_file=Path(tmp) / "config.json")
            cfg = AppConfig(
                provider=Provider.OPENAI, model="gpt-4o-mini",
                api_key="key", mode=ControlMode.HOTL,
            )
            save_app_config(cfg, paths)
            out: list[str] = []

            updated = _handle_plan_command(
                command="/plan precheck on", current=cfg, paths=paths,
                display=out.append, approve=None,
            )
            self.assertEqual(updated.plan_precheck, "on")
            self.assertEqual(load_app_config(paths).plan_precheck, "on")

            updated = _handle_plan_command(
                command="/plan precheck off", current=updated, paths=paths,
                display=out.append, approve=None,
            )
            self.assertEqual(updated.plan_precheck, "off")
            self.assertEqual(load_app_config(paths).plan_precheck, "off")

    def test_precheck_invalid_value_reports_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = ConfigPaths(config_dir=Path(tmp), config_file=Path(tmp) / "config.json")
            cfg = AppConfig(
                provider=Provider.OPENAI, model="gpt-4o-mini",
                api_key="key", mode=ControlMode.HOTL,
            )
            save_app_config(cfg, paths)
            out: list[str] = []
            updated = _handle_plan_command(
                command="/plan precheck", current=cfg, paths=paths,
                display=out.append, approve=None,
            )
            self.assertEqual(updated.plan_precheck, cfg.plan_precheck)
            self.assertTrue(any("precheck" in line.lower() for line in out))


if __name__ == "__main__":
    unittest.main()
