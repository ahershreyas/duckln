from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from duckln.plan_mode import PlanRecord, PlanStep, RepoUnderstanding, _deterministic_supervisor_review
from duckln.repo_bringup import (
    RepoBringUpPlan,
    RepoBringUpStep,
    _assemble_plansteps_with_clone_and_run,
    _env_example_file,
    _evidence_line_for,
    _extract_served_url,
    _package_manager_for,
    _run_command_from_scripts,
    _verification_for_run_command,
)
from state.access import read_repo_facts, write_repo_facts
from state.repo_catalog import RepoCatalogRecord


def _repo(url="https://github.com/acme/demo"):
    return RepoCatalogRecord("demo", url, 1, "d", "Custom", "TS", "2026-04-01")


def _understanding(detected_files):
    return RepoUnderstanding(
        repo_slug="acme/demo", repo_path=None, objective="o", os_name="linux", arch="x",
        detected_runtimes=("node",), detected_files=tuple(detected_files), repo_family="node_typescript",
        readme_excerpt="", config_excerpts={}, recent_failures=(), recent_skills=(),
        execution_target="vm", control_mode="hootlwo", needs_clarification=False, clarification_seed=None,
    )


def _plan(steps):
    return PlanRecord(
        plan_id="p", objective="o", context_summary="family=node_typescript", steps=tuple(steps),
        risks=(), rollback="", estimated_seconds=10, created_at="2026-01-01", status="pending",
        repo_slug="acme/demo", mode_at_creation="hootlwo",
    )


def _step(i, cmd, *, sc="S2", v="ok", t="vm"):
    return PlanStep(index=i, title="t", description="", command=cmd, safety_class=sc,
                    verification=v, rationale="", estimated_seconds=10, target=t)


class TestLockfileMismatch(unittest.TestCase):
    def test_npm_with_pnpm_lock_revises(self):
        plan = _plan([
            _step(1, "git clone https://github.com/acme/demo.git", sc="S1", v="test -d .git"),
            _step(2, "npm install"),
            _step(3, "npm run dev"),
        ])
        v = _deterministic_supervisor_review(plan, _understanding(("package.json", "pnpm-lock.yaml")))
        self.assertEqual(v.verdict, "revise")
        self.assertIn("pnpm", v.reason.lower())

    def test_matching_pm_approves(self):
        plan = _plan([
            _step(1, "git clone https://github.com/acme/demo.git", sc="S1", v="test -d .git"),
            _step(2, "pnpm install"),
            _step(3, "pnpm dev"),
        ])
        v = _deterministic_supervisor_review(plan, _understanding(("package.json", "pnpm-lock.yaml")))
        self.assertEqual(v.verdict, "approve")


class TestRunCommandRanking(unittest.TestCase):
    def test_prefers_dev_over_start(self):
        self.assertEqual(_run_command_from_scripts({"start": "node x", "dev": "vite"}, "npm"), "npm run dev")

    def test_pnpm_yarn_syntax(self):
        self.assertEqual(_run_command_from_scripts({"dev": "vite"}, "pnpm"), "pnpm dev")
        self.assertEqual(_run_command_from_scripts({"serve": "x"}, "yarn"), "yarn serve")

    def test_no_run_script(self):
        self.assertIsNone(_run_command_from_scripts({"build": "tsc"}, "npm"))

    def test_package_manager_for(self):
        self.assertEqual(_package_manager_for(("package.json", "yarn.lock")), "yarn")
        self.assertEqual(_package_manager_for(("package.json",)), "npm")


class TestVerificationAndUrl(unittest.TestCase):
    def test_web_run_verification_mentions_url(self):
        self.assertIn("url", _verification_for_run_command("npm run dev").lower())

    def test_non_server_verification(self):
        self.assertIn("stays alive", _verification_for_run_command("python script.py").lower())

    def test_extract_served_url(self):
        self.assertEqual(_extract_served_url("Local: http://localhost:5173/"), "http://localhost:5173/")
        self.assertIsNone(_extract_served_url("nothing here"))


class TestEnvAndEvidence(unittest.TestCase):
    def test_env_example_detection(self):
        self.assertEqual(_env_example_file(("package.json", ".env.example")), ".env.example")
        self.assertIsNone(_env_example_file(("package.json",)))

    def test_assembler_injects_env_copy(self):
        plan = RepoBringUpPlan(
            repo=_repo(), project_dir=Path("/tmp/demo"), detected_files=("package.json", ".env.example"),
            steps=(RepoBringUpStep(purpose="Install deps", command="npm install"),), summary="node",
        )
        steps = _assemble_plansteps_with_clone_and_run(
            PlanStep=PlanStep, repo=_repo(), runtime_dir="/tmp/demo", bringup_plan=plan,
            inspection=SimpleNamespace(repo_knowledge=SimpleNamespace(entrypoints=("npm run dev",)), detected_files=("package.json", ".env.example")),
            execution_target="vm",
        )
        self.assertTrue(any("cp .env.example .env" in (s.command or "") for s in steps))

    def test_evidence_line_prefers_full_command(self):
        readme = "## Setup\n$ npm install\nRun the dev server:\n    npm run dev\n"
        self.assertEqual(_evidence_line_for("npm run dev", readme), "npm run dev")


class TestPerRepoFacts(unittest.TestCase):
    def test_round_trip_and_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_repo_facts(tmp, "https://github.com/acme/demo", {"run_command": "pnpm dev", "node_version": "20"})
            facts = read_repo_facts(tmp, "https://github.com/acme/demo")
            self.assertEqual(facts.get("run_command"), "pnpm dev")
            self.assertEqual(facts.get("node_version"), "20")
            self.assertEqual(read_repo_facts(tmp, "other/repo"), {})

    def test_only_known_keys_stored(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_repo_facts(tmp, "a/b", {"run_command": "npm run dev", "bogus": "x", "empty": ""})
            facts = read_repo_facts(tmp, "a/b")
            self.assertIn("run_command", facts)
            self.assertNotIn("bogus", facts)
            self.assertNotIn("empty", facts)


if __name__ == "__main__":
    unittest.main()
