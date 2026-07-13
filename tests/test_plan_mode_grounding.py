"""Plan 72 Phase 1: grounded backbone (clone→install→build→run) + rendering."""

from __future__ import annotations

import re
import unittest

from duckln.modes import ControlMode
from duckln.plan_mode import (
    PLAN_STATUS_PENDING,
    PlanRecord,
    PlanStep,
    RepoUnderstanding,
    finalize_plan_from_steps,
)
from duckln.repo_bringup import (
    RepoBringUpPlan,
    RepoBringUpStep,
    _assemble_plansteps_with_clone_and_run,
    _infer_run_command,
    _looks_like_run_command,
)
from state.repo_catalog import RepoCatalogRecord


class _FakeKnowledge:
    def __init__(self, entrypoints):
        self.entrypoints = tuple(entrypoints)


class _FakeInspection:
    def __init__(self, *, detected_files=(), readme="", entrypoints=()):
        self.detected_files = tuple(detected_files)
        self.readme_excerpt = readme
        self.repo_knowledge = _FakeKnowledge(entrypoints)


def _repo():
    return RepoCatalogRecord(
        name="JustHireMe", repo_url="https://github.com/o/JustHireMe", stars=0,
        description="Node app", category="Web", framework="React", last_updated="",
    )


def _bringup_plan(steps):
    return RepoBringUpPlan(
        repo=_repo(),
        project_dir=__import__("pathlib").Path("/tmp/x"),
        detected_files=("package.json",),
        steps=tuple(steps),
        summary="",
    )


class AssembleBackboneTest(unittest.TestCase):
    def test_clone_prepended_and_run_appended(self) -> None:
        # Node install steps with no clone and no run command.
        bringup = _bringup_plan([
            RepoBringUpStep(purpose="Install deps", command="npm ci", verification_command="exit 0"),
        ])
        inspection = _FakeInspection(detected_files=("package.json",), entrypoints=("npm run dev",))
        steps = _assemble_plansteps_with_clone_and_run(
            PlanStep=PlanStep, repo=_repo(),
            runtime_dir="/home/ubuntu/.duckln/projects/JustHireMe",
            bringup_plan=bringup, inspection=inspection,
        )
        commands = [s.command for s in steps]
        # Plan 85: the clone step is idempotent (guarded with `if [ -d .git ]`), so it
        # CONTAINS `git clone` rather than starting with it; it must still be first.
        self.assertIn("git clone", commands[0], commands)
        self.assertIn("npm ci", commands)
        self.assertTrue(_looks_like_run_command(commands[-1]), commands[-1])

    def test_existing_clone_and_run_not_duplicated(self) -> None:
        bringup = _bringup_plan([
            RepoBringUpStep(purpose="Clone", command="git clone --depth 1 https://github.com/o/JustHireMe ."),
            RepoBringUpStep(purpose="Install", command="npm install"),
            RepoBringUpStep(purpose="Run", command="npm run dev"),
        ])
        inspection = _FakeInspection(entrypoints=("npm run dev",))
        steps = _assemble_plansteps_with_clone_and_run(
            PlanStep=PlanStep, repo=_repo(), runtime_dir="/tmp/x",
            bringup_plan=bringup, inspection=inspection,
        )
        clone_count = sum(1 for s in steps if "git clone" in (s.command or ""))
        run_count = sum(1 for s in steps if _looks_like_run_command(s.command or ""))
        self.assertEqual(clone_count, 1)
        self.assertEqual(run_count, 1)

    def test_run_detection_patterns(self) -> None:
        self.assertTrue(_looks_like_run_command("npm run dev"))
        self.assertTrue(_looks_like_run_command("uvicorn app:app"))
        self.assertTrue(_looks_like_run_command("cargo run"))
        self.assertTrue(_looks_like_run_command("docker compose up"))
        self.assertFalse(_looks_like_run_command("npm ci"))
        self.assertFalse(_looks_like_run_command("pip install -r requirements.txt"))

    def test_infer_run_command_from_entrypoints(self) -> None:
        self.assertEqual(_infer_run_command(_FakeInspection(entrypoints=("npm run dev",))), "npm run dev")
        self.assertIsNone(_infer_run_command(_FakeInspection(entrypoints=())))


class FinalizeFromStepsTest(unittest.TestCase):
    def test_numbers_and_classifies(self) -> None:
        steps = (
            PlanStep(index=1, title="Clone", description="", command="git clone --depth 1 https://x .",
                     safety_class="S0", verification=None, rationale="", estimated_seconds=20, confidence=1.0),
            PlanStep(index=2, title="Install", description="", command="npm ci",
                     safety_class="S0", verification=None, rationale="", estimated_seconds=90, confidence=1.0),
        )
        u = RepoUnderstanding(
            repo_slug="o/x", repo_path=None, objective="Set up x", os_name="Darwin", arch="arm64",
            detected_runtimes=("node",), detected_files=("package.json",), repo_family="node_typescript",
            readme_excerpt="", config_excerpts={}, recent_failures=(), recent_skills=(),
            execution_target="vm", control_mode="hootlwo", needs_clarification=False, clarification_seed=None,
        )
        plan = finalize_plan_from_steps(
            objective="Set up x", repo_slug="o/x", context_summary="family=node_typescript",
            steps=steps, mode=ControlMode.HOOTLWO, understanding=u,
        )
        self.assertEqual(plan.status, PLAN_STATUS_PENDING)
        self.assertEqual([s.index for s in plan.steps], [1, 2])
        # Real safety classes assigned (not the placeholder S0 for npm ci which is S2).
        self.assertIn(plan.steps[1].safety_class, ("S1", "S2", "S3"))


class PanelWrappingTest(unittest.TestCase):
    def test_long_command_wraps_without_overflow(self) -> None:
        from duckln.ui import render_plan_panel

        long_cmd = "git clone --depth 1 https://github.com/some-org/a-really-long-repository-name-that-exceeds " \
                   "/home/ubuntu/.duckln/projects/a-really-long-repository-name-that-exceeds"
        plan = PlanRecord(
            plan_id="abcd1234", objective="Set up X on vm", context_summary="family=node_typescript",
            steps=(PlanStep(index=1, title="Clone", description="", command=long_cmd,
                            safety_class="S1", verification="test -d .git", rationale="needed first",
                            estimated_seconds=20, confidence=1.0),),
            risks=(), rollback="rm -rf x", estimated_seconds=20,
            created_at="2026-05-22T00:00:00Z", status="pending", repo_slug="https://github.com/o/x",
            mode_at_creation="hootlwo",
        )
        out = render_plan_panel(plan, width=80)
        inner = 80 - 2
        for raw in out.splitlines():
            # Strip border + ANSI, ensure no visual line overflows the panel.
            no_ansi = re.sub(r"\x1b\[[0-9;]*m", "", raw)
            content = no_ansi[2:] if no_ansi.startswith("│ ") else no_ansi.lstrip("│").lstrip("╭╰─ ")
            self.assertLessEqual(len(content), inner + 4, f"line too long: {content!r}")
        self.assertIn("Step 1", re.sub(r"\x1b\[[0-9;]*m", "", out))


if __name__ == "__main__":
    unittest.main()
