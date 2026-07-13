from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import duckln.repo_bringup as rb
from duckln.config import ConfigPaths
from duckln.modes import ControlMode
from duckln.repo_bringup import RepoBringUpPlan, RepoBringUpStep
from state.access import read_pending_plan, write_config_snapshot
from duckln.plan_mode import PlanRecord
from state.repo_catalog import RepoCatalogRecord


def _repo():
    return RepoCatalogRecord("JustHireMe", "https://github.com/vasu-devs/JustHireMe", 1, "d", "Custom", "TypeScript", "2026-04-01")


def _fake_inspection():
    return SimpleNamespace(
        detected_files=("package.json", ".env.example"),
        readme_excerpt="This project requires Node 20 or higher.",
        repo_knowledge=SimpleNamespace(entrypoints=("npm run dev",)),
    )


def _fake_bringup_plan(repo):
    # Mirrors what the specialist produces: redundant prereqs the pre-check satisfies.
    steps = (
        RepoBringUpStep(purpose="Ensure node (>=24) is installed before setup runs", command="sudo apt install -y nodejs", source="readme-prereq"),
        RepoBringUpStep(purpose="Ensure git is installed before setup runs", command="sudo apt install -y git", source="readme-prereq"),
        RepoBringUpStep(purpose="Ensure npm is installed before setup runs", command="sudo apt install -y npm", source="readme-prereq"),
        RepoBringUpStep(purpose="Verify README prerequisite: Node.js and npm", command="node --version && npm --version", source="readme-prerequisite"),
        RepoBringUpStep(purpose="Run README install command", command="npm ci", verification_command="test -d node_modules", source="readme"),
    )
    return RepoBringUpPlan(
        repo=repo, project_dir=Path("/tmp/jhm"), detected_files=("package.json", ".env.example"),
        steps=steps, summary="node setup", repo_family=rb.RepoFamily.NODE_TYPESCRIPT,
    )


class TestDraftLeanIntegration(unittest.TestCase):
    def _run_draft(self, execution_target="vm", vm_name="duckln-vm"):
        repo = _repo()
        saved = {}

        def fake_grounded(*, repo, execution_target, config_dir, system_probe):
            return _fake_inspection(), rb.RepoFamily.NODE_TYPESCRIPT, _fake_bringup_plan(repo)

        def fake_probe(*, repo, execution_target, config_dir, vm_name, pane_executor, display, emit_thought=None):
            if emit_thought:
                emit_thought("Pre-check — node v18, git present; repo already cloned")
            return ({"node", "npm", "git", "python", "cargo", "uv"}, True, {"node": "18", "python": "3.12", "rust": "1.95"})

        for name, fn in (("_grounded_setup_plan", fake_grounded), ("_plan_precheck_probe", fake_probe),
                         ("_detect_required_node_version", lambda **kw: "20"),
                         ("_required_env_vars", lambda **kw: ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DATABASE_URL"))):
            saved[name] = getattr(rb, name)
            setattr(rb, name, fn)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                paths = ConfigPaths(config_dir=Path(tmp), config_file=Path(tmp) / "config.json")
                write_config_snapshot(paths.config_dir, {"plan_precheck": "on", "execution_target": execution_target})
                rb._generate_plan_for_bringup_impl(
                    repo=repo, current_mode=ControlMode.HOOTLWO, paths=paths,
                    execution_target=execution_target, display=lambda _m: None,
                    llm_client=None, vm_name=vm_name, approve=lambda _p: True,
                )
                pending = read_pending_plan(paths.config_dir)
                self.assertIsNotNone(pending, "a pending plan should be written")
                return PlanRecord.from_dict(pending)
        finally:
            for name, fn in saved.items():
                setattr(rb, name, fn)

    def test_lean_plan_drops_satisfied_and_fills_panel(self):
        plan = self._run_draft()
        purposes = [s.title for s in plan.steps] + [s.description for s in plan.steps]
        joined = " | ".join(p for p in purposes if p)
        # 1. satisfied prereqs/verifies are gone
        self.assertNotIn("Ensure git", joined)
        self.assertNotIn("Ensure npm", joined)
        self.assertNotIn("Verify README prerequisite", joined)
        # 2. Node-20 install present (target 18 < 20)
        cmds = " ".join(s.command or "" for s in plan.steps)
        self.assertIn("setup_20.x", cmds)
        self.assertIn("npm ci", cmds)
        # 3. pre-check summary populated + names git/node
        self.assertTrue(plan.precheck_summary)
        self.assertIn("git", plan.precheck_summary)
        self.assertIn("18", plan.precheck_summary)
        # 4. target label reflects the VM
        self.assertIn("duckln-vm", plan.target_label)
        # 5. required env keys captured
        self.assertIn("OPENAI_API_KEY", plan.required_env_keys)
        self.assertIn("DATABASE_URL", plan.required_env_keys)


if __name__ == "__main__":
    unittest.main()
