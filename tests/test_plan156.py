"""Plan 156 — reasoning-first production engine.
P1 behavior-based model capability probe (provider-agnostic, cached).
P2 reasoning is required for a non-trivial repo (the honest-stop decision helper).
P3 a generalist sub-agent handles any unmatched stack (Java/Gradle/etc.), not a Python mislabel.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from duckln import recovery as r
import duckln.repo_bringup as rb
from duckln.subagent_runtime import select_subagent_descriptor
from duckln.subagents import default_subagent_runtime_descriptors


def _good(*, system_prompt, user_message):
    return '{"cause": "requests not installed", "fix": "pip install requests"}'


def _fenced(*, system_prompt, user_message):
    return '```json\n{"cause":"x","fix":"y"}\n```'


def _weak(*, system_prompt, user_message):
    return "Sure, you should probably try installing the missing package somehow."


class CapabilityProbe(unittest.TestCase):
    def test_capable_when_structured_json(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(r.probe_model_reasoning_capability(_good, model_id="ollama/x:8b", config_dir=Path(d)), "capable")
            self.assertEqual(r.probe_model_reasoning_capability(_fenced, model_id="or/free", config_dir=Path(d)), "capable")

    def test_weak_when_prose(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(r.probe_model_reasoning_capability(_weak, model_id="tiny:1b", config_dir=Path(d)), "weak")

    def test_unknown_when_no_client(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(r.probe_model_reasoning_capability(None, model_id="x", config_dir=Path(d)), "unknown")

    def test_verdict_is_cached(self):
        with tempfile.TemporaryDirectory() as d:
            cd = Path(d)
            r.probe_model_reasoning_capability(_good, model_id="m:8b", config_dir=cd)
            self.assertEqual(r.read_cached_capability(cd, "m:8b"), "capable")

    def test_capable_prefers_cache_over_name(self):
        with tempfile.TemporaryDirectory() as d:
            cd = Path(d)
            # a name that the heuristic would call capable, but the behavior probe says weak
            r.probe_model_reasoning_capability(_weak, model_id="qwen3-fake", config_dir=cd)
            self.assertFalse(r.model_is_reasoning_capable("qwen3-fake", config_dir=cd))
            self.assertIsNotNone(r.weak_model_reasoning_note("qwen3-fake", config_dir=cd))

    def test_name_heuristic_fallback_when_unprobed(self):
        # no cache → name heuristic: a 1.5b is weak, a 70b is capable
        self.assertFalse(r.model_is_reasoning_capable("local:1.5b"))
        self.assertTrue(r.model_is_reasoning_capable("llama-3.1-70b"))


class PlanningRequiresReasoning(unittest.TestCase):
    def test_nontrivial_signals(self):
        self.assertTrue(rb._planning_is_nontrivial(("pom.xml", "README.md")))        # build surface
        self.assertTrue(rb._planning_is_nontrivial(("build.gradle",)))
        self.assertTrue(rb._planning_is_nontrivial(("a", "b", "c")))                  # >=3 files
        self.assertTrue(rb._planning_is_nontrivial(("README.md",), low_confidence_family=True))

    def test_trivial_repo(self):
        self.assertFalse(rb._planning_is_nontrivial(("README.md",)))
        self.assertFalse(rb._planning_is_nontrivial(()))


class GeneralistRouting(unittest.TestCase):
    def test_unmatched_stack_routes_to_generalist(self):
        # Java/Maven, Gradle → generalist (NOT python)
        self.assertEqual(
            select_subagent_descriptor(repo_family="unknown", execution_target="local",
                                       detected_files=("pom.xml", "README.md")).slug,
            "generalist",
        )
        self.assertEqual(
            select_subagent_descriptor(repo_family="java", execution_target="local",
                                       detected_files=("build.gradle",)).slug,
            "generalist",
        )

    def test_python_signal_still_python(self):
        self.assertEqual(
            select_subagent_descriptor(repo_family="unknown", execution_target="local",
                                       detected_files=("requirements.txt",)).slug,
            "python_setup",
        )

    def test_known_family_direct(self):
        self.assertEqual(
            select_subagent_descriptor(repo_family="node_typescript", execution_target="local",
                                       detected_files=("package.json",)).slug,
            "node_typescript",
        )

    def test_generalist_descriptor_exists(self):
        slugs = {d.slug for d in default_subagent_runtime_descriptors()}
        self.assertIn("generalist", slugs)


class MultiAgentWiring(unittest.TestCase):
    """P4/P5: the dormant multi-agent runners are now reachable from the live path."""

    def test_runners_importable(self):
        from duckln.harness.plan_supervisor import run_plan_supervisor
        from duckln.harness.recovery_flow import run_multi_agent_recovery, RecoveryRequest
        self.assertTrue(callable(run_plan_supervisor))
        self.assertTrue(callable(run_multi_agent_recovery))
        self.assertTrue(RecoveryRequest)

    def test_recovery_coordinator_graceful_without_spec(self):
        # An agent registry with no recovery_coordinator → a CLEAN failure, never a crash
        # (proves the runner is callable + safe without spinning a real model).
        from duckln.harness.recovery_flow import run_multi_agent_recovery, RecoveryRequest
        from duckln.harness.agent_def import AgentRegistry
        from duckln.modes import ControlMode
        with tempfile.TemporaryDirectory() as d:
            empty_registry = AgentRegistry.from_directory(Path(d), required=frozenset())  # no specs
            req = RecoveryRequest(
                failed_command="x", step_purpose="p", stderr="", stdout="", exit_code=1,
                repo_slug="r", project_dir=Path(d), execution_target="local",
                config_dir=Path(d), mode=ControlMode.HOTL,
            )
            out = run_multi_agent_recovery(req, llm_client=lambda **k: "{}", agent_registry=empty_registry)
            self.assertFalse(out.succeeded)
            self.assertIn("recovery_coordinator", out.summary)


if __name__ == "__main__":
    unittest.main()
