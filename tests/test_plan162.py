"""Plan 162 — connect the built-but-unwired execution features + remove dead code.

Each F wires an already-implemented, already-tested helper into the live path. These tests
exercise the helpers + the seams that are unit-reachable; the live runtime behaviours
(F1/F3/F4/F9 effects, F2 masked prompts) are proven on a real VM/model.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import duckln.repo_bringup as rb
from duckln import recovery as r


class _FakeResult:
    def __init__(self, exit_code=0, stdout="", stderr=""):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = False


class _FakeRunner:
    def __init__(self, exit_code=0):
        self._exit = exit_code
        self.commands: list[str] = []

    def run(self, command, **kwargs):
        self.commands.append(command)
        return _FakeResult(exit_code=self._exit)


# --- F1: outcome verification gates "running" ---------------------------------


class F1OutcomeGate(unittest.TestCase):
    def test_outcome_command_is_curl_for_served_url(self):
        cmd = rb._outcome_check_command(served_url="http://localhost:8000", project_dir=Path("/tmp"))
        self.assertIsNotNone(cmd)
        self.assertTrue(cmd.strip().startswith("curl"))

    def test_probe_ok_when_exit_zero(self):
        ok, _log = rb._probe_served_outcome(
            served_url="http://localhost:8000", runner=_FakeRunner(exit_code=0),
            execution_target="local", vm_name=None, config_dir=Path("/tmp"),
            project_cwd=None, think=lambda _t: None,
        )
        self.assertTrue(ok)

    def test_probe_fails_when_non_2xx(self):
        ok, _log = rb._probe_served_outcome(
            served_url="http://localhost:8000", runner=_FakeRunner(exit_code=22),
            execution_target="local", vm_name=None, config_dir=Path("/tmp"),
            project_cwd=None, think=lambda _t: None,
        )
        self.assertFalse(ok)  # curl -fsS exits non-zero on a 4xx/5xx → real failure


# --- F2: repo secret intake (masked, never logged) ----------------------------


class F2SecretIntake(unittest.TestCase):
    def test_secret_keys_needing_intake(self):
        keys = rb._secret_env_keys_needing_intake(("OPENAI_API_KEY", "SECRET_KEY", "DEBUG"))
        self.assertIn("OPENAI_API_KEY", keys)   # external key, no safe default → masked intake
        self.assertNotIn("SECRET_KEY", keys)     # has a random dev default → not an intake ask

    def test_upsert_command_quotes_value_and_upserts(self):
        cmd = rb._secret_upsert_command({"OPENAI_API_KEY": "sk-abc'123"})
        self.assertIn("OPENAI_API_KEY", cmd)
        self.assertIn(".env", cmd)
        self.assertIn("grep -v", cmd)  # drops an existing line before appending (upsert)

    def test_detected_env_example_files(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / ".env.example").write_text("API_KEY=\n")
            self.assertEqual((".env.example",), rb._detected_env_example_files(Path(td)))


# --- F3: concurrent multi-process launch --------------------------------------


class F3ConcurrentRun(unittest.TestCase):
    def test_subdir_run_command_infers(self):
        with tempfile.TemporaryDirectory() as td:
            sub = Path(td) / "backend"
            sub.mkdir()
            (sub / "manage.py").write_text("# django\n")
            self.assertIn("runserver", rb._subdir_run_command(sub) or "")

    def test_launch_concurrent_wires_backend_into_frontend(self):
        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            for name in ("backend", "frontend"):
                d = project / name
                d.mkdir()
            (project / "backend" / "manage.py").write_text("# django\n")
            (project / "frontend" / "package.json").write_text(json.dumps({"scripts": {"dev": "vite"}}))

            launched: list[str] = []

            def fake_launch(*, runner, raw_command, project_cwd, **kw):
                launched.append(raw_command)
                # backend serves first; frontend second
                url = "http://localhost:8000" if "runserver" in raw_command else "http://localhost:5173"
                return "ready", url, ""

            orig = rb._launch_and_await_server
            rb._launch_and_await_server = fake_launch
            try:
                runner = _FakeRunner()
                served = rb._launch_concurrent_processes(
                    topology=(("backend", "backend"), ("frontend", "frontend")),
                    runner=runner, runtime_cwd=str(project), project_dir=project,
                    execution_target="local", vm_name=None, config_dir=project,
                    repo=type("R", (), {"name": "demo"})(), display=lambda _m: None, think=lambda _t: None,
                )
            finally:
                rb._launch_and_await_server = orig
            self.assertEqual({"backend", "frontend"}, set(served))
            # the frontend got the backend URL wired into its .env before launch
            self.assertTrue(any("API_URL=" in c and "localhost:8000" in c for c in runner.commands))


# --- F4: proactive sizing signal ----------------------------------------------


class F4Sizing(unittest.TestCase):
    def test_repo_is_resource_heavy_by_stack(self):
        self.assertTrue(rb._repo_is_resource_heavy((), ("Cargo.toml",)))
        self.assertTrue(rb._repo_is_resource_heavy((), ("environment.yml",)))
        self.assertFalse(rb._repo_is_resource_heavy((), ("package.json",)))


# --- F5: destructive decider (Approve-all-this-session) -----------------------


class F5DestructiveDecider(unittest.TestCase):
    def test_decider_remembers_approve_all(self):
        calls = {"n": 0}

        def select(_msg, _opts):
            calls["n"] += 1
            return "Approve all this session"

        decide = rb.make_destructive_decider(select=select)
        self.assertEqual("all", decide("first"))
        self.assertEqual("all", decide("second"))  # remembered
        self.assertEqual(1, calls["n"])             # asked only once

    def test_step_gate_uses_decider(self):
        from duckln.modes import ControlMode
        self.assertTrue(rb._step_allowed_in_mode("S4", ControlMode.HOOTLWO, None, destructive_decider=lambda _m: "all"))
        self.assertFalse(rb._step_allowed_in_mode("S4", ControlMode.HOOTLWO, None, destructive_decider=lambda _m: "no"))


# --- F6: capability promotion recorded from the critic path -------------------


class F6CapabilityPromotion(unittest.TestCase):
    def test_valid_verdicts_promote_weak_model_via_critic(self):
        from duckln.plan_mode import PlanStep, critic_review, finalize_plan_from_steps

        good = lambda **k: json.dumps({"verdict": "approve", "reason": "ok"})
        step = PlanStep(index=1, title="t", description="", command="npm ci", safety_class="S2",
                        verification="x", rationale="x", estimated_seconds=5, confidence=1.0, origin="planner")
        plan = finalize_plan_from_steps(objective="o", repo_slug=None, context_summary="c", steps=(step,), mode="hitl")
        with tempfile.TemporaryDirectory() as td:
            cd = Path(td)
            r.write_cached_capability(cd, "weakm", "weak")
            for _ in range(r._CAPABILITY_PROMOTE_THRESHOLD):
                v = critic_review(plan=plan, understanding=None, llm_client=good, config_dir=cd, model_id="weakm")
                self.assertEqual("approve", v.verdict)
            self.assertEqual("capable", r.read_cached_capability(cd, "weakm"))  # N-of-M promotion happened


# --- F7: untrusted-code posture -----------------------------------------------


class F7UntrustedPosture(unittest.TestCase):
    def test_notice_for_fresh_local_only(self):
        self.assertIsNotNone(rb._untrusted_local_notice(execution_target="local", repo_previously_trusted=False))
        self.assertIsNone(rb._untrusted_local_notice(execution_target="local", repo_previously_trusted=True))
        self.assertIsNone(rb._untrusted_local_notice(execution_target="vm", repo_previously_trusted=False))


# --- F8: capability-gap surfacing ---------------------------------------------


class F8CapabilityGap(unittest.TestCase):
    def test_gap_extracted_from_block_reason(self):
        self.assertEqual("github", r.capability_gap_from_text("to do this I need the MCP server `github`"))
        self.assertIsNone(r.capability_gap_from_text("the venv is missing a package"))


# --- F9: cloud disk resize commands -------------------------------------------


class F9CloudResize(unittest.TestCase):
    def test_aws_disk_grow_command(self):
        from duckln.cloud_runtime import build_cloud_resize_commands
        from state.store import ManagedResourceRecord

        rec = ManagedResourceRecord(
            resource_key="aws:i-1", resource_kind="cloud", provider="aws", display_name="i-1",
            execution_target="aws", region="us-east-1", shape="t3.small", install_root="",
            status="running", idle_timeout_minutes=None, last_activity_at=None,
            created_at="", updated_at="", metadata={"instance_id": "i-1", "root_volume_id": "vol-1"},
        )
        cmds = build_cloud_resize_commands(rec, resource="disk", disk_gb=40)
        self.assertTrue(cmds and "modify-volume" in cmds[0] and "vol-1" in cmds[0])


# --- F10: dead code is gone ---------------------------------------------------


class F10DeadCodeRemoved(unittest.TestCase):
    def test_dead_symbols_removed(self):
        for name in ("_drop_present_prereqs", "_parse_env_example_keys", "_existing_manifest_paths",
                     "_parse_llm_readme_extras", "_fresh_install_full_command"):
            self.assertFalse(hasattr(rb, name), f"{name} should be deleted")
        from duckln import repo_agent
        self.assertFalse(hasattr(repo_agent, "run_parallel_explorers"))

    def test_readme_link_follower_module_gone(self):
        with self.assertRaises(ImportError):
            import duckln.readme_link_follower  # noqa: F401


if __name__ == "__main__":
    unittest.main()
