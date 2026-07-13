"""Plan 72 Phase 10 — consolidated PRD invariant gate + per-family backbone.

Cross-cutting invariants are individually covered by the per-phase test files;
this module asserts the headline PRD guarantees in one place and exercises the
deterministic clone→install→run backbone across repo families.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from duckln.modes import ControlMode
from duckln.plan_mode import (
    PlanStep,
    plan_first_required,
    plan_skill_signature,
)
from duckln.plan_lifecycle import TERMINAL_STATES, PlanLifecycle, PlanLifecycleState
from duckln.repo_bringup import (
    RepoBringUpPlan,
    RepoBringUpStep,
    _assemble_plansteps_with_clone_and_run,
    _looks_like_run_command,
    _step_allowed_in_mode,
)
from state.repo_catalog import RepoCatalogRecord


class _Knowledge:
    def __init__(self, entrypoints):
        self.entrypoints = tuple(entrypoints)


class _Inspection:
    def __init__(self, entrypoints):
        self.repo_knowledge = _Knowledge(entrypoints)


def _repo(name="App", url="https://github.com/o/App"):
    return RepoCatalogRecord(name=name, repo_url=url, stars=0, description="",
                             category="", framework="", last_updated="")


def _plan(steps, entrypoints):
    return RepoBringUpPlan(repo=_repo(), project_dir=Path("/tmp/x"),
                           detected_files=(), steps=tuple(steps), summary="")


class PerFamilyBackboneTest(unittest.TestCase):
    """Every family's plan must clone first and run last."""

    CASES = {
        "node": (["npm ci"], "npm run dev"),
        "python": (["python3 -m venv .venv", "pip install -r requirements.txt"], "python main.py"),
        "go": (["go mod download", "go build ./..."], "go run ."),
        "rust": (["cargo build"], "cargo run"),
        "docker": (["docker build -t app ."], "docker compose up"),
    }

    def test_clone_first_and_run_last_for_each_family(self) -> None:
        for family, (install, run) in self.CASES.items():
            bringup = _plan([RepoBringUpStep(purpose=f"step {c}", command=c) for c in install], [run])
            steps = _assemble_plansteps_with_clone_and_run(
                PlanStep=PlanStep, repo=_repo(), runtime_dir="/home/u/.duckln/projects/App",
                bringup_plan=bringup, inspection=_Inspection([run]),
            )
            commands = [s.command for s in steps]
            # Plan 85: clone step is idempotent (`if [ -d .git ]; … git clone …`), so it
            # CONTAINS `git clone` and must still be first.
            self.assertIn("git clone", commands[0], f"{family}: {commands}")
            self.assertTrue(_looks_like_run_command(commands[-1]), f"{family}: last={commands[-1]}")


class HeadlineInvariantsTest(unittest.TestCase):
    def test_plan_first_blocks_mutation_when_on(self) -> None:
        self.assertTrue(plan_first_required(plan_mode_enabled=True, safety_class="S2"))
        self.assertFalse(plan_first_required(plan_mode_enabled=True, safety_class="S0"))

    def test_hootlwo_no_auto_s2_and_s4_user_decided(self) -> None:
        self.assertFalse(_step_allowed_in_mode("S2", ControlMode.HOOTLWO, None))
        # Plan 160 Phase A: S4 destructive is never auto-run unattended, but the USER decides
        # when asked (no silent auto-block).
        self.assertFalse(_step_allowed_in_mode("S4", ControlMode.HOOTLWO, None))
        self.assertTrue(_step_allowed_in_mode("S4", ControlMode.HOOTLWO, lambda _m: True))

    def test_lifecycle_has_four_terminal_states(self) -> None:
        self.assertEqual(
            {s.value for s in TERMINAL_STATES},
            {"complete", "waiting_on_user", "blocked_external", "duckln_internal_bug_reported"},
        )

    def test_lifecycle_never_left_spinning(self) -> None:
        cleared = []
        with PlanLifecycle(on_clear_activity=lambda: cleared.append(1), persist=lambda s: None) as lc:
            lc.advance(PlanLifecycleState.CONTEXT_COLLECTED)
        self.assertTrue(lc.is_terminal)
        self.assertTrue(cleared)

    def test_skill_key_includes_family_os_target(self) -> None:
        sig = plan_skill_signature(repo_family="rust", os_name="Linux", execution_target="aws")
        self.assertEqual(sig, "setup-rust-linux-aws")


if __name__ == "__main__":
    unittest.main()
