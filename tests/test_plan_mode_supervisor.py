"""Plan 67: supervisor wiring tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from duckln.harness.agent_def import AgentRegistry, builtin_agents_directory
from duckln.harness.plan_supervisor import REQUIRED_AGENT_NAMES, run_plan_supervisor
from duckln.modes import ControlMode
from duckln.plan_mode import RepoUnderstanding


class ScriptedLLM:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    def __call__(self, *, system_prompt, user_message) -> str:
        if not self._responses:
            raise RuntimeError("no more scripted responses")
        return self._responses.pop(0)


def _understanding() -> RepoUnderstanding:
    return RepoUnderstanding(
        repo_slug="owner/x",
        repo_path="/tmp/x",
        objective="Set up x",
        os_name="Darwin",
        arch="arm64",
        detected_runtimes=("node",),
        detected_files=("package.json",),
        repo_family="node_typescript",
        readme_excerpt="npm install",
        config_excerpts={},
        recent_failures=(),
        recent_skills=(),
        execution_target="local",
        control_mode="hotl",
        needs_clarification=False,
        clarification_seed=None,
    )


class SupervisorTest(unittest.TestCase):
    def test_required_agent_names_registered(self) -> None:
        registry = AgentRegistry.from_directory(builtin_agents_directory())
        for name in REQUIRED_AGENT_NAMES:
            self.assertIsNotNone(
                registry.lookup(name),
                f"agent spec '{name}' is missing from {builtin_agents_directory()}",
            )

    def test_run_plan_supervisor_uses_harness_when_specs_present(self) -> None:
        llm = ScriptedLLM([
            json.dumps([
                {"title": "install", "command": "npm install", "confidence": 0.9, "estimated_seconds": 60},
            ]),
            json.dumps({
                "ordered_steps": [
                    {"index": 1, "title": "install", "description": "d",
                     "command": "npm install", "rationale": "r",
                     "verification": "exit 0", "depends_on": [], "confidence": 0.9,
                     "estimated_seconds": 60}
                ],
                "dropped": [], "reasoning": "ok"
            }),
        ])
        with tempfile.TemporaryDirectory() as td:
            result = run_plan_supervisor(
                objective="Set up x",
                understanding=_understanding(),
                llm_client=llm,
                config_dir=Path(td),
                mode=ControlMode.HOTL,
            )
        self.assertTrue(result.used_harness)
        self.assertEqual(result.missing_agents, ())
        self.assertEqual(result.plan.status, "pending")
        self.assertEqual(len(result.plan.steps), 1)
        self.assertTrue(result.session_id)

    def test_fallback_when_supervisor_spec_missing(self) -> None:
        # Make a registry missing supervisor.
        registry = AgentRegistry()
        llm = ScriptedLLM([
            json.dumps([{"title": "install", "command": "npm install", "confidence": 0.9, "estimated_seconds": 1}]),
            json.dumps({
                "ordered_steps": [
                    {"index": 1, "title": "install", "description": "d",
                     "command": "npm install", "rationale": "r",
                     "verification": "exit 0", "depends_on": [], "confidence": 0.9,
                     "estimated_seconds": 1}
                ],
                "dropped": [], "reasoning": ""
            }),
        ])
        with tempfile.TemporaryDirectory() as td:
            result = run_plan_supervisor(
                objective="Set up x",
                understanding=_understanding(),
                llm_client=llm,
                config_dir=Path(td),
                mode=ControlMode.HOTL,
                agent_registry=registry,
            )
        self.assertFalse(result.used_harness)
        self.assertEqual(result.missing_agents, REQUIRED_AGENT_NAMES)
        self.assertEqual(result.plan.status, "pending")
        self.assertEqual(len(result.plan.steps), 1)


if __name__ == "__main__":
    unittest.main()
