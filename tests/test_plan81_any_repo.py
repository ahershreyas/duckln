from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from duckln.diagnostics import ErrorCategory, match_deterministic_fix
from duckln.plan_mode import PlanStep
from duckln.repo_bringup import (
    RepoBringUpPlan,
    RepoBringUpStep,
    _assemble_plansteps_with_clone_and_run,
    _codegen_steps,
    _compose_services,
    _detect_project_subdir,
    _port_from_url,
    _required_env_vars,
    _vm_port_reachable,
    resolve_runtime_project_dir,
)
from state.repo_catalog import RepoCatalogRecord


def _repo(url="https://github.com/acme/demo"):
    return RepoCatalogRecord("demo", url, 1, "d", "Custom", "x", "2026-04-01")


def _write_local(cfg, repo, name, content):
    proj = Path(resolve_runtime_project_dir(cfg, repo, execution_target="local"))
    (proj / Path(name).parent).mkdir(parents=True, exist_ok=True)
    (proj / name).write_text(content, encoding="utf-8")


class TestEnvRequirements(unittest.TestCase):
    def test_required_flags_secrets_with_placeholders(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, repo = Path(tmp), _repo()
            _write_local(cfg, repo, ".env.example", "PORT=3000\nDATABASE_URL=\nOPENAI_API_KEY=your_key_here\nDEBUG=true")
            req = _required_env_vars(config_dir=cfg, repo=repo, detected_files=(".env.example",))
            self.assertIn("DATABASE_URL", req)
            self.assertIn("OPENAI_API_KEY", req)
            self.assertNotIn("PORT", req)
            self.assertNotIn("DEBUG", req)


class TestMonorepo(unittest.TestCase):
    def test_detects_apps_web(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, repo = Path(tmp), _repo()
            _write_local(cfg, repo, "package.json", json.dumps({"workspaces": ["apps/*"]}))
            _write_local(cfg, repo, "apps/web/package.json", json.dumps({"scripts": {"dev": "vite"}}))
            sub = _detect_project_subdir(config_dir=cfg, repo=repo, detected_files=("package.json", "pnpm-workspace.yaml"))
            self.assertEqual(sub, "apps/web")

    def test_single_package_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, repo = Path(tmp), _repo()
            _write_local(cfg, repo, "package.json", json.dumps({"scripts": {"dev": "vite"}}))
            self.assertEqual(_detect_project_subdir(config_dir=cfg, repo=repo, detected_files=("package.json",)), "")

    def test_assembled_steps_use_subdir_cwd(self):
        plan = RepoBringUpPlan(
            repo=_repo(), project_dir=Path("/tmp/demo"), detected_files=("package.json",),
            steps=(RepoBringUpStep(purpose="Install deps", command="npm install"),), summary="node",
        )
        steps = _assemble_plansteps_with_clone_and_run(
            PlanStep=PlanStep, repo=_repo(), runtime_dir="/tmp/demo", bringup_plan=plan,
            inspection=SimpleNamespace(repo_knowledge=SimpleNamespace(entrypoints=("npm run dev",)), detected_files=("package.json",), readme_excerpt=""),
            execution_target="vm", project_subdir="apps/web",
        )
        run = next(s for s in steps if "npm run dev" in (s.command or ""))
        self.assertEqual(run.cwd, "/tmp/demo/apps/web")
        clone = next(s for s in steps if "git clone" in (s.command or ""))
        self.assertEqual(clone.cwd, "")  # clone stays at root


class TestComposeServices(unittest.TestCase):
    def test_extracts_services(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, repo = Path(tmp), _repo()
            _write_local(cfg, repo, "docker-compose.yml",
                         "version: '3'\nservices:\n  db:\n    image: postgres:16\n  cache:\n    image: redis:7\n")
            svc = _compose_services(config_dir=cfg, repo=repo, detected_files=("docker-compose.yml",))
            self.assertIn("db", svc)
            self.assertIn("cache", svc)

    def test_compose_step_added_when_docker_present(self):
        plan = RepoBringUpPlan(
            repo=_repo(), project_dir=Path("/tmp/demo"), detected_files=("package.json",),
            steps=(RepoBringUpStep(purpose="Install deps", command="npm install"),), summary="node",
        )
        steps = _assemble_plansteps_with_clone_and_run(
            PlanStep=PlanStep, repo=_repo(), runtime_dir="/tmp/demo", bringup_plan=plan,
            inspection=SimpleNamespace(repo_knowledge=SimpleNamespace(entrypoints=("npm run dev",)), detected_files=("package.json",), readme_excerpt=""),
            execution_target="vm", compose_services=("db",), docker_available=True,
        )
        self.assertTrue(any("docker compose up -d" in (s.command or "") for s in steps))

    def test_compose_step_skipped_without_docker(self):
        plan = RepoBringUpPlan(
            repo=_repo(), project_dir=Path("/tmp/demo"), detected_files=("package.json",),
            steps=(RepoBringUpStep(purpose="Install deps", command="npm install"),), summary="node",
        )
        steps = _assemble_plansteps_with_clone_and_run(
            PlanStep=PlanStep, repo=_repo(), runtime_dir="/tmp/demo", bringup_plan=plan,
            inspection=SimpleNamespace(repo_knowledge=SimpleNamespace(entrypoints=("npm run dev",)), detected_files=("package.json",), readme_excerpt=""),
            execution_target="vm", compose_services=("db",), docker_available=False,
        )
        self.assertFalse(any("docker compose up" in (s.command or "") for s in steps))


class TestCodegen(unittest.TestCase):
    def test_prisma_and_build(self):
        out = _codegen_steps(
            payload={"dependencies": {"prisma": "5"}, "scripts": {"build": "tsc", "start": "node ."}},
            package_manager="npm", run_command="npm run start",
        )
        titles = [t for t, _, _ in out]
        self.assertIn("Generate Prisma client", titles)
        self.assertIn("Build the app", titles)

    def test_no_codegen_for_plain_repo(self):
        self.assertEqual(_codegen_steps(payload={"scripts": {"dev": "vite"}}, package_manager="npm", run_command="npm run dev"), ())


class TestDeterministicFixesPlan81(unittest.TestCase):
    def test_prisma(self):
        self.assertEqual(match_deterministic_fix(stderr="@prisma/client did not initialize yet").fix_command, "npx prisma generate")

    def test_native_lib(self):
        self.assertIn("libpq-dev", match_deterministic_fix(stderr="pg_config executable not found", execution_target="vm").fix_command)

    def test_playwright(self):
        self.assertIn("playwright install", match_deterministic_fix(stderr="Host system is missing dependencies to run browsers").fix_command)

    def test_service_unreachable(self):
        self.assertEqual(match_deterministic_fix(stderr="connect ECONNREFUSED 127.0.0.1:5432").category, ErrorCategory.SERVICE_UNREACHABLE)

    def test_private_repo_blocks(self):
        f = match_deterministic_fix(stderr="fatal: could not read Username for 'https://github.com'", command="git clone --depth 1 x", exit_code=128)
        self.assertTrue(f.block)
        self.assertEqual(f.fix_command, "")
        self.assertIn("private", f.block_question.lower())


class TestVmPort(unittest.TestCase):
    def test_port_from_url(self):
        self.assertEqual(_port_from_url("http://10.0.0.2:5173/"), "5173")

    def test_reachable_false_on_failure(self):
        class _Bad:
            def run(self, *a, **k):
                return SimpleNamespace(exit_code=7, timed_out=False)
        self.assertFalse(_vm_port_reachable("http://10.0.0.2:5173/", runner=_Bad()))

    def test_reachable_true(self):
        class _Good:
            def run(self, *a, **k):
                return SimpleNamespace(exit_code=0, timed_out=False)
        self.assertTrue(_vm_port_reachable("http://10.0.0.2:5173/", runner=_Good()))


if __name__ == "__main__":
    unittest.main()
