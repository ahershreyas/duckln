from __future__ import annotations

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
    _plan_precheck_probe,
)
from state.repo_catalog import RepoCatalogRecord


def _repo():
    return RepoCatalogRecord("JustHireMe", "https://github.com/vasu-devs/JustHireMe", 1, "d", "Custom", "TS", "2026-04-01")


def _plan(repo):
    return RepoBringUpPlan(
        repo=repo, project_dir=Path("/tmp/jhm"), detected_files=("package.json",),
        steps=(RepoBringUpStep(purpose="Install deps", command="npm ci"),), summary="node",
    )


def _assemble(skip_clone):
    return _assemble_plansteps_with_clone_and_run(
        PlanStep=PlanStep, repo=_repo(), runtime_dir="~/.duckln/projects/JustHireMe",
        bringup_plan=_plan(_repo()),
        inspection=SimpleNamespace(repo_knowledge=SimpleNamespace(entrypoints=("npm run dev",)),
                                   detected_files=("package.json",), readme_excerpt=""),
        execution_target="vm", skip_clone=skip_clone,
    )


class TestCloneSkipAndIdempotency(unittest.TestCase):
    def test_already_cloned_drops_clone_step(self):
        steps = _assemble(skip_clone=True)
        self.assertFalse(any("git clone" in (s.command or "") for s in steps),
                         "no clone step when the repo is already cloned")

    def test_clone_step_is_idempotent_and_quote_safe(self):
        steps = _assemble(skip_clone=False)
        clone = next(s for s in steps if "git clone" in (s.command or ""))
        self.assertIn("if [ -d", clone.command)            # guarded
        self.assertIn("duckln-already-cloned", clone.command)
        self.assertNotIn("'", clone.command)                # survives bash -lc '...'


class TestDiagnosisNotPrivate(unittest.TestCase):
    def test_already_exists_is_not_private(self):
        f = match_deterministic_fix(
            stderr="fatal: destination path '/home/ubuntu/.duckln/projects/JustHireMe' already exists and is not an empty directory.",
            command="git clone --depth 1 https://github.com/vasu-devs/JustHireMe ~/.duckln/projects/JustHireMe",
            exit_code=128,
        )
        self.assertEqual(f.category, ErrorCategory.ALREADY_PRESENT)
        self.assertFalse(f.block)

    def test_real_auth_failures_are_private(self):
        for stderr in (
            "fatal: could not read Username for 'https://github.com'",
            "remote: Repository not found.",
            "fatal: could not resolve host: github.com",
            "git@github.com: Permission denied (publickey).",
        ):
            f = match_deterministic_fix(stderr=stderr, command="git clone x", exit_code=128)
            self.assertIsNotNone(f, stderr)
            self.assertEqual(f.category, ErrorCategory.PRIVATE_REPO, stderr)
            self.assertTrue(f.block, stderr)

    def test_bare_128_without_signal_is_not_private(self):
        self.assertIsNone(match_deterministic_fix(stderr="fatal: something unusual", command="git clone x", exit_code=128))


class _SeqRunner:
    """Returns a precheck block WITHOUT CLONED on the first call, then confirms via fallback."""
    def __init__(self):
        self.n = 0

    def run(self, command, **kw):
        self.n += 1
        if self.n == 1:
            block = "DUCKLN_PRECHECK_BEGIN\nTOOL:node\nNODEV:v18.19.1\nDUCKLN_PRECHECK_END"
            return SimpleNamespace(stdout=block, exit_code=0, timed_out=False)
        return SimpleNamespace(stdout="DUCKLN_CLONED_CONFIRMED\n", exit_code=0, timed_out=False)


class TestPrecheckClonedFallback(unittest.TestCase):
    def test_fallback_confirms_already_cloned(self):
        import duckln.repo_bringup as rb
        runner = _SeqRunner()
        orig = rb.ControlledCommandRunner
        rb.ControlledCommandRunner = lambda **kw: runner
        try:
            with tempfile.TemporaryDirectory() as tmp:
                present, cloned, vers = _plan_precheck_probe(
                    repo=_repo(), execution_target="vm", config_dir=Path(tmp),
                    vm_name="duckln-vm", pane_executor=object(), display=lambda _m: None,
                )
        finally:
            rb.ControlledCommandRunner = orig
        # Block had no CLONED line, but the explicit fallback confirmed it.
        self.assertTrue(cloned)
        self.assertEqual(runner.n, 2)  # block probe + fallback


if __name__ == "__main__":
    unittest.main()
