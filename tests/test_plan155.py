"""Plan 155 — production hardening of verified debt.
F1 canonical execution targets + docker→host fall-through fix.
F2 conda repos run through `conda run -n <env>`.
F3 assess_existing_setup + build_fix_only_plan_steps are reachable.
F7 the supervisor-reviewer swallow is logged, not silent.
F8 a registered tool/MCP becomes visible in the agent manifest.
(F4 free-text delete is exercised against delete_controls; F5 is a git/.gitignore change.)
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import duckln.repo_bringup as rb
from duckln import execution_targets as et


class CanonicalExecutionTargets(unittest.TestCase):
    def test_normalize_docker_alias(self):
        self.assertEqual(et.normalize_execution_target("docker"), "container")
        self.assertEqual(et.normalize_execution_target("Docker"), "container")
        self.assertEqual(et.normalize_execution_target("vm"), "vm")
        self.assertEqual(et.normalize_execution_target(None), "local")
        self.assertEqual(et.normalize_execution_target("  "), "local")

    def test_membership_helpers_normalize(self):
        self.assertTrue(et.is_sandbox_target("docker"))      # docker→container ∈ sandbox
        self.assertTrue(et.is_fs_remote_target("docker"))
        self.assertTrue(et.is_local_hosted_target("docker"))
        self.assertFalse(et.is_remote_target("docker"))      # container is not off-host
        self.assertTrue(et.is_remote_target("vm"))

    def test_wrapper_routes_docker_to_container_not_host(self):
        with tempfile.TemporaryDirectory() as d:
            cd = Path(d)
            # No container provisioned → must return None (can't run there), NOT a host passthrough.
            wrapped, _ = rb._wrap_command_for_execution_target(
                config_dir=cd, execution_target="docker", command="echo hi", cwd=None)
            self.assertIsNone(wrapped)

    def test_wrapper_unknown_nonlocal_target_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            wrapped, _ = rb._wrap_command_for_execution_target(
                config_dir=Path(d), execution_target="weird", command="echo hi", cwd=None)
            self.assertIsNone(wrapped)  # never silently run on the host

    def test_wrapper_local_passthrough(self):
        with tempfile.TemporaryDirectory() as d:
            wrapped, _ = rb._wrap_command_for_execution_target(
                config_dir=Path(d), execution_target="local", command="echo hi", cwd=None)
            self.assertEqual(wrapped, "echo hi")


class CondaRunThrough(unittest.TestCase):
    def test_prefix_for_conda_repo(self):
        class _Repo:
            name = "x"
            repo_url = "https://example.com/x"
        with tempfile.TemporaryDirectory() as d:
            cd = Path(d)
            # monkeypatch the repo-text reader to return an environment.yml with a name.
            orig = rb._read_repo_text_file
            rb._read_repo_text_file = lambda c, r, n: ("name: myenv\ndependencies: []\n" if n.startswith("environment") else "")
            try:
                prefix = rb._conda_run_prefix_for_repo(config_dir=cd, repo=_Repo(), detected_files=("environment.yml",))
            finally:
                rb._read_repo_text_file = orig
            self.assertEqual(prefix, "conda run -n myenv ")

    def test_no_prefix_for_non_conda_repo(self):
        class _Repo:
            name = "x"
            repo_url = "u"
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(
                rb._conda_run_prefix_for_repo(config_dir=Path(d), repo=_Repo(), detected_files=("requirements.txt",)),
                "",
            )


class ReVerifyReachable(unittest.TestCase):
    def test_assess_and_fix_only_are_callable(self):
        healthy, issues = rb.assess_existing_setup({"partial": "venv"}, verification_passed=None)
        self.assertFalse(healthy)
        self.assertTrue(issues)
        steps = rb.build_fix_only_plan_steps(issues, package_manager="npm")
        # the venv issue maps to a venv repair step (title, command, safety)
        self.assertTrue(any("venv" in s[0].lower() for s in steps))

    def test_healthy_setup_has_no_issues(self):
        healthy, issues = rb.assess_existing_setup({}, verification_passed=True)
        self.assertTrue(healthy)
        self.assertEqual(issues, ())


class RegisteredExtensionVisible(unittest.TestCase):
    def test_registered_mcp_appears_in_manifest(self):
        from duckln.tool_registry import (registered_extension_entries, tool_registry_entries,
                                           render_tools_manifest)
        from state.store import initialize_state_store
        with tempfile.TemporaryDirectory() as d:
            cd = Path(d)
            self.assertEqual(registered_extension_entries(cd), ())
            initialize_state_store(cd).upsert_managed_memory(
                memory_key="registered-mcp:demo", memory_kind="registered_mcp",
                relative_path="tools/mcp-demo.json", title="t",
                content=json.dumps({"tool_id": "mcp.demo", "label": "Demo", "description": "d"}),
                metadata={})
            ents = registered_extension_entries(cd)
            self.assertEqual(len(ents), 1)
            self.assertEqual(ents[0].provider_type, "mcp")
            manifest = render_tools_manifest(entries=tool_registry_entries(cd))
            self.assertIn("mcp.demo", json.loads(manifest.content)["tool_order"])


class RecoveryReviewerNotSilent(unittest.TestCase):
    def test_reviewer_crash_is_logged_then_falls_through(self):
        from duckln import recovery
        from duckln.recovery import RecoveryReport, RecoveryDecision

        def _boom(_report):
            raise RuntimeError("reviewer down")

        report = RecoveryReport(decision=RecoveryDecision.FIXED, cause="c", command="echo ok",
                                reason="r", evidence="read app.py:1 — confirms the fix")
        with self.assertLogs("duckln.recovery", level="DEBUG") as cm:
            ok, _msg = recovery.supervise_recovery_conclusion(report, empirically_verified=False, review=_boom)
        self.assertTrue(any("reviewer" in line.lower() for line in cm.output))


if __name__ == "__main__":
    unittest.main()
