"""Plan 73 Phase D — self-learning loop: inject / reflect / extract / loop."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from duckln.ai_client import Provider
from duckln.config import AppConfig, resolve_config_paths, save_app_config
from duckln.modes import ControlMode
from duckln.plan_mode import (
    PlanRecord,
    PlanStep,
    find_matching_skill,
    load_skill_commands,
    persist_reflection,
    record_failure_skill,
    record_plan_skill,
    reflect_on_run,
)
from duckln.main import handle_session_command
from state.access import read_pending_plan, write_pending_plan


SECRET = "token=ghp_abcdefghijklmnopqrstuvwxyz0123456789"


class InjectTest(unittest.TestCase):
    def test_matching_verified_skill_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cd = Path(td)
            slug = record_plan_skill(config_dir=cd, repo_family="node_typescript",
                                     os_name="Darwin", execution_target="vm",
                                     commands=("git clone --depth 1 https://x .", "npm ci", "npm run dev"))
            self.assertEqual(find_matching_skill(cd, repo_family="node_typescript", os_name="Darwin", execution_target="vm"), slug)
            self.assertEqual(load_skill_commands(cd, slug), ("git clone --depth 1 https://x .", "npm ci", "npm run dev"))

    def test_no_match_for_different_signature(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cd = Path(td)
            record_plan_skill(config_dir=cd, repo_family="node_typescript", os_name="Darwin",
                              execution_target="vm", commands=("npm ci",))
            self.assertIsNone(find_matching_skill(cd, repo_family="python", os_name="Linux", execution_target="local"))


class ReflectTest(unittest.TestCase):
    def test_success_reflection(self) -> None:
        note = reflect_on_run(plan=None, executed=("npm ci", "npm run dev"), succeeded=True)
        self.assertTrue(note.succeeded)
        self.assertIn("npm ci", note.render())

    def test_failure_reflection_is_redacted(self) -> None:
        note = reflect_on_run(plan=None, executed=(f"echo {SECRET}",), succeeded=False,
                              attribution_cause=f"auth failed {SECRET}", failed_command=f"git push {SECRET}")
        rendered = note.render()
        self.assertFalse(note.succeeded)
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz", rendered)

    def test_persist_reflection_no_secret(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cd = Path(td)
            note = reflect_on_run(plan=None, executed=(f"echo {SECRET}",), succeeded=False,
                                  attribution_cause="x", failed_command="y")
            persist_reflection(cd, repo_slug="o/x", note=note)
            sessions = cd / "memory" / "sessions"
            text = "\n".join(p.read_text() for p in sessions.glob("*.md")) if sessions.exists() else ""
            self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz", text)


class ExtractTest(unittest.TestCase):
    def test_failure_skill_keyed_with_avoid(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cd = Path(td)
            slug = record_failure_skill(config_dir=cd, repo_family="python", os_name="Linux",
                                        execution_target="local", failed_command="pip install x", cause="no network")
            self.assertTrue(slug.endswith("-avoid"))
            # A failure skill must NOT satisfy find_matching_skill (verified-only).
            self.assertIsNone(find_matching_skill(cd, repo_family="python", os_name="Linux", execution_target="local"))


class LoopRetryTest(unittest.TestCase):
    def test_plan_retry_clears_pending(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": td})
            cfg = AppConfig(provider=Provider.OLLAMA, model="gemma2:2b", api_key=None,
                            mode=ControlMode.HOOTLWO, plan_mode_enabled=True, base_url="http://localhost:11434")
            save_app_config(cfg, paths)
            write_pending_plan(paths.config_dir, {"plan_id": "p1", "status": "failed", "steps": []})
            out: list[str] = []
            handle_session_command("/plan retry", cfg, paths, display=out.append)
            self.assertIsNone(read_pending_plan(paths.config_dir))
            self.assertIn("learned", "\n".join(out).lower())


if __name__ == "__main__":
    unittest.main()
