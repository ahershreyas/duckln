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

_TAURI_PKG = {
    "name": "justhireme",
    "dependencies": {"@tauri-apps/api": "^1.5.0", "react": "18", "react-dom": "18"},
    "devDependencies": {"vite": "^5", "@tauri-apps/cli": "^1.5"},
    "scripts": {"dev": "vite", "build": "vite build", "tauri": "tauri"},
}


def _repo():
    return RepoCatalogRecord("JustHireMe", "https://github.com/vasu-devs/JustHireMe", 1, "d", "Custom", "TypeScript", "2026-04-01")


def _fake_inspection():
    return SimpleNamespace(
        detected_files=("package.json", "tauri.conf.json", ".env.example"),
        readme_excerpt="JustHireMe is a desktop app built with Tauri. Requires Node 20.",
        repo_knowledge=SimpleNamespace(entrypoints=("npm run dev",)),
    )


def _fake_bringup_plan(repo):
    steps = (
        RepoBringUpStep(purpose="Run README install command", command="npm ci", verification_command="test -d node_modules", source="readme"),
    )
    return RepoBringUpPlan(
        repo=repo, project_dir=Path("/tmp/jhm"), detected_files=("package.json", "tauri.conf.json"),
        steps=steps, summary="tauri setup", repo_family=rb.RepoFamily.NODE_TYPESCRIPT,
    )


class TestAgentThoughtsAndDesktop(unittest.TestCase):
    def _run_draft(self, execution_target, *, vm_name="duckln-vm", versions=None):
        repo = _repo()
        thoughts: list[str] = []
        saved = {}
        probe_versions = {"node": "20"} if versions is None else versions

        def fake_grounded(*, repo, execution_target, config_dir, system_probe):
            return _fake_inspection(), rb.RepoFamily.NODE_TYPESCRIPT, _fake_bringup_plan(repo)

        def fake_probe(*, repo, execution_target, config_dir, vm_name, pane_executor, display, emit_thought=None):
            return ({"node", "npm", "git"}, True, dict(probe_versions))

        for name, fn in (
            ("_grounded_setup_plan", fake_grounded),
            ("_plan_precheck_probe", fake_probe),
            ("_read_repo_package_json", lambda *a, **k: dict(_TAURI_PKG)),
            ("_detect_required_node_version", lambda **kw: "20"),
            ("_required_env_vars", lambda **kw: ("OPENAI_API_KEY",)),
        ):
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
                    emit_thought=thoughts.append,
                )
                pending = read_pending_plan(paths.config_dir)
                self.assertIsNotNone(pending, "a pending plan should be written")
                return PlanRecord.from_dict(pending), thoughts
        finally:
            for name, fn in saved.items():
                setattr(rb, name, fn)

    def test_emits_agent_handoff_thoughts(self):
        _plan, thoughts = self._run_draft("local")
        joined = " | ".join(thoughts)
        self.assertIn("Inspector →", joined)
        self.assertIn("Understood:", joined)
        self.assertIn("desktop (tauri) app", joined.lower())
        self.assertIn("Planner →", joined)

    def test_desktop_run_command_chosen_not_frontend(self):
        plan, _thoughts = self._run_draft("local")
        cmds = " ".join(s.command or "" for s in plan.steps)
        # The native shell, not the frontend-only `dev`.
        self.assertIn("tauri", cmds)
        self.assertNotIn("npm run dev", cmds)

    def test_vm_target_injects_streaming_stack(self):
        plan, _thoughts = self._run_draft("vm")
        titles = " | ".join(s.title for s in plan.steps)
        cmds = " ".join(s.command or "" for s in plan.steps)
        self.assertIn("streaming stack", titles)
        self.assertIn("xvfb", cmds.lower())

    def test_vm_run_step_is_tauri_not_frontend(self):
        plan, _thoughts = self._run_draft("vm")
        run_steps = [s for s in plan.steps if "tauri" in (s.command or "")]
        self.assertTrue(run_steps, "the run step must invoke tauri, not the frontend dev")
        all_cmds = " ".join(s.command or "" for s in plan.steps)
        self.assertNotIn("npm run dev", all_cmds)

    def test_low_ram_vm_injects_swap_step(self):
        # Plan 89: a Tauri build on a small VM with no swap gets a swap + single-job step.
        plan, _thoughts = self._run_draft("vm", versions={"node": "20", "mem_kb": "2048000", "swap": "no"})
        cmds = " ".join(s.command or "" for s in plan.steps)
        self.assertIn("swapon", cmds)
        self.assertIn("jobs = 1", cmds)

    def test_ample_ram_vm_no_swap_step(self):
        plan, _thoughts = self._run_draft("vm", versions={"node": "20", "mem_kb": "16000000", "swap": "no"})
        cmds = " ".join(s.command or "" for s in plan.steps)
        self.assertNotIn("swapon", cmds)

    def test_idempotent_guards_present(self):
        plan, _thoughts = self._run_draft("vm")
        cmds = "\n".join(s.command or "" for s in plan.steps)
        # Plan 127: npm install is completion-aware — a re-run skips only when the
        # install COMPLETED (marker), else cleans + reinstalls (heals half-installs).
        self.assertTrue(
            any("node_modules/.package-lock.json" in (s.command or "") for s in plan.steps),
            f"expected a completion-aware npm-install guard in: {cmds}",
        )


if __name__ == "__main__":
    unittest.main()
