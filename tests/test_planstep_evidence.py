from __future__ import annotations

import unittest

from duckln.plan_mode import PlanRecord, PlanStep


def _step(**kw):
    base = dict(
        index=1, title="t", description="", command="npm install",
        safety_class="S2", verification="test -d node_modules", rationale="",
        estimated_seconds=10,
    )
    base.update(kw)
    return PlanStep(**base)


class TestPlanStepEvidence(unittest.TestCase):
    def test_new_fields_default_empty(self):
        s = _step()
        self.assertEqual(s.target, "")
        self.assertEqual(s.source, "")
        self.assertEqual(s.evidence_excerpt, "")
        self.assertEqual(s.cwd, "")

    def test_round_trip_preserves_fields(self):
        s = _step(target="vm", source="package.json", evidence_excerpt="npm ci", cwd="/home/x")
        d = s.to_dict()
        self.assertEqual(d["target"], "vm")
        self.assertEqual(d["source"], "package.json")
        self.assertEqual(d["cwd"], "/home/x")

    def test_old_plan_without_fields_still_loads(self):
        # Simulate a plan persisted before Plan 78 (no evidence keys).
        payload = {
            "plan_id": "p1", "objective": "o", "context_summary": "family=node",
            "steps": [{
                "index": 1, "title": "Install", "command": "npm install",
                "safety_class": "S2", "verification": "x", "rationale": "",
                "estimated_seconds": 10,
            }],
            "risks": [], "rollback": "", "estimated_seconds": 10,
            "created_at": "2026-05-25T00:00:00Z", "status": "pending",
            "repo_slug": "o/x", "mode_at_creation": "hotl",
        }
        plan = PlanRecord.from_dict(payload)
        self.assertEqual(plan.steps[0].target, "")
        self.assertEqual(plan.steps[0].cwd, "")


class TestAssembledStepsCarryEvidence(unittest.TestCase):
    def test_assembled_steps_have_target_and_cwd(self):
        from duckln.repo_bringup import (
            RepoBringUpPlan, RepoBringUpStep, _assemble_plansteps_with_clone_and_run,
        )
        from types import SimpleNamespace
        from pathlib import Path
        from state.repo_catalog import RepoCatalogRecord

        repo = RepoCatalogRecord("demo", "https://github.com/acme/demo", 1, "d", "Custom", "TS", "2026-04-01")
        plan = RepoBringUpPlan(
            repo=repo, project_dir=Path("/tmp/demo"), detected_files=("package.json",),
            steps=(RepoBringUpStep(purpose="Install deps", command="npm install"),),
            summary="node",
        )
        steps = _assemble_plansteps_with_clone_and_run(
            PlanStep=PlanStep, repo=repo, runtime_dir="/tmp/demo",
            bringup_plan=plan, inspection=SimpleNamespace(repo_knowledge=SimpleNamespace(entrypoints=("npm run dev",))),
            execution_target="vm",
        )
        self.assertTrue(all(s.target == "vm" for s in steps))
        clone = next(s for s in steps if "git clone" in (s.command or ""))
        self.assertEqual(clone.cwd, "")  # clone runs from the parent
        install = next(s for s in steps if "npm install" in (s.command or ""))
        self.assertEqual(install.cwd, "/tmp/demo")


if __name__ == "__main__":
    unittest.main()
