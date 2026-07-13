"""Plan 173 — a missing NODE dependency during a build must have a deterministic, model-free
fix (install the deps incl. devDependencies); node installs always include devDeps; and a WEAK
model must not loop the recovery 3× — it does a single pass (or stops on no-new-evidence).
"""

from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path

from duckln.diagnostics import ErrorCategory, match_deterministic_fix
from duckln.recovery import recover_failed_step_with_agent, recovery_autonomy, write_cached_capability
from duckln.repo_bringup import _node_install_command
from state.access import write_config_snapshot

SN = types.SimpleNamespace


class NodeDepMissingDeterministicFix(unittest.TestCase):
    """F1: the node 'missing package' family maps to a model-free, auto-applied node install."""

    def test_vite_unresolved_and_err_module(self):
        for stderr in (
            "[ERR_MODULE_NOT_FOUND]: Cannot find package 'vite' imported from /x/vite.config.ts",
            "[UNRESOLVED_IMPORT] Could not resolve '@vitejs/plugin-react' in vite.config.ts",
            "Error: Cannot find module 'react' from /x/src/index.ts",
            "This is not the tsc command you are looking for",
        ):
            fx = match_deterministic_fix(stderr=stderr, command="npm run --if-present build:all", execution_target="vm")
            self.assertIsNotNone(fx, stderr[:40])
            self.assertEqual(fx.category, ErrorCategory.NODE_DEP_MISSING)
            self.assertIn("npm install", fx.fix_command)
            self.assertIn("--include=dev", fx.fix_command)
            # repo-scoped → auto-applies (no user prompt needed).
            self.assertEqual(recovery_autonomy(fx.fix_command, execution_target="vm"), "auto")

    def test_package_manager_from_command(self):
        fx = match_deterministic_fix(stderr="ERR_MODULE_NOT_FOUND: Cannot find package 'vite'",
                                     command="pnpm run build", execution_target="vm")
        self.assertTrue(fx.fix_command.startswith("pnpm install"))
        fx = match_deterministic_fix(stderr="ERR_MODULE_NOT_FOUND: Cannot find package 'vite'",
                                     command="yarn build", execution_target="vm")
        self.assertTrue(fx.fix_command.startswith("yarn install"))

    def test_python_missing_module_still_pip(self):
        fx = match_deterministic_fix(stderr="ModuleNotFoundError: No module named 'flask'", command="python app.py")
        self.assertEqual(fx.category, ErrorCategory.MISSING_MODULE)
        self.assertIn("pip install", fx.fix_command)

    def test_dns_resolve_host_is_not_a_node_dep(self):
        fx = match_deterministic_fix(stderr="curl: (6) Could not resolve host: example.com", command="curl x")
        self.assertTrue(fx is None or fx.category != ErrorCategory.NODE_DEP_MISSING)


class NodeInstallIncludesDevDeps(unittest.TestCase):
    """F2: the node install command always installs devDependencies (build tools)."""

    def test_includes_dev(self):
        self.assertEqual(_node_install_command(("package.json", "package-lock.json")), ("npm ci --include=dev", "package-lock.json"))
        self.assertIn("--prod=false", _node_install_command(("package.json", "pnpm-lock.yaml"))[0])
        self.assertIn("--production=false", _node_install_command(("package.json", "yarn.lock"))[0])
        self.assertEqual(_node_install_command(("package.json",)), ("npm install --include=dev", None))


def _obs(n):
    return (SN(tool="fs.read_file", args={"path": f"file{n}"}, ok=True, payload={"summary": f"data{n}"}),)


class WeakModelStopsRecoveryEarly(unittest.TestCase):
    """F3: a weak model does ONE pass (no 3-round loop); a capable model keeps the full rounds
    when new evidence is gathered, and stops early when a round adds no new evidence."""

    def _run(self, *, verdict, observations_per_round):
        calls = {"n": 0}

        def runner(**kw):
            calls["n"] += 1
            obs = observations_per_round(calls["n"])
            return SN(answer="", observations=obs)  # answer="" → report None → always challenged

        with tempfile.TemporaryDirectory() as td:
            cd = Path(td)
            write_config_snapshot(cd, {"model": "test-model"})
            write_cached_capability(cd, "test-model", verdict)
            recover_failed_step_with_agent(
                config_dir=cd, repo_name="repo", project_dir=cd, execution_target="vm", vm_name="vm",
                failed_command="npm run build:all", stderr="boom", stdout="", mode=None,
                approve=(lambda _m: True), llm_client=(lambda **k: ""), display=(lambda _m: None),
                emit_thought=(lambda _m: None), agent_runner=runner,
            )
        return calls["n"]

    def test_weak_model_single_pass(self):
        # weak → _MAX_SUPERVISOR_ROUNDS=0 → exactly one investigation, not three.
        self.assertEqual(self._run(verdict="weak", observations_per_round=_obs), 1)

    def test_capable_model_full_rounds_with_new_evidence(self):
        # capable + NEW evidence each round → all 3 rounds (1 + 2 supervisor rounds).
        self.assertEqual(self._run(verdict="capable", observations_per_round=_obs), 3)

    def test_capable_model_stops_on_no_new_evidence(self):
        # capable but the SAME evidence each round → break after round 2 (no point looping).
        self.assertEqual(self._run(verdict="capable", observations_per_round=lambda _n: _obs(0)), 2)


if __name__ == "__main__":
    unittest.main()
