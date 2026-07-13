"""Plan 178 — robust `<thought>` capture, uniform reasoning discipline across the deep
reasoning agents, and FINISHED plugin usability (a pulled skill is loaded into the agent
context; a pulled tool/MCP is actually executable, not just visible)."""

from __future__ import annotations

import json
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from duckln.reasoning import (
    MISSING_THOUGHT,
    TRUNCATED_PAYLOAD,
    extract_thinking_and_content,
    has_thought,
)


class F1ThoughtExtraction(unittest.TestCase):
    def test_matched_tags(self):
        t, p = extract_thinking_and_content('<thought>reason here</thought>{"decision":"fixed"}')
        self.assertEqual(t, "reason here")
        self.assertEqual(p, '{"decision":"fixed"}')

    def test_truncated_unclosed(self):
        t, p = extract_thinking_and_content("<thought>ran out of tok")
        self.assertEqual(t, "ran out of tok")
        self.assertEqual(p, TRUNCATED_PAYLOAD)

    def test_missing_tag(self):
        t, p = extract_thinking_and_content('{"decision":"skip"}')
        self.assertEqual(t, MISSING_THOUGHT)
        self.assertEqual(p, '{"decision":"skip"}')

    def test_empty_and_helper(self):
        self.assertEqual(extract_thinking_and_content("")[0], MISSING_THOUGHT)
        self.assertTrue(has_thought("x <thought>y"))
        self.assertFalse(has_thought("no tags"))


class F2ThoughtCapturedToLog(unittest.TestCase):
    def test_monologue_persisted_even_with_injected_runner(self):
        from duckln import recovery

        with TemporaryDirectory() as d:
            cfg = Path(d)

            def fake_runner(**_kw):
                return types.SimpleNamespace(
                    answer=(
                        "<thought>Symptom: build failed. Inspected: package.json. "
                        "Root cause: missing dep.</thought>"
                        '{"decision":"skip","cause":"optional step absent","evidence":"listed dir"}'
                    ),
                    observations=(),
                )

            recovery.recover_failed_step_with_agent(
                config_dir=cfg, repo_name="demo", project_dir=None,
                execution_target="local", vm_name=None,
                failed_command="npm run build", stderr="boom", stdout="",
                mode=None, approve=None, llm_client=lambda **k: "",
                agent_runner=fake_runner,
            )
            log = cfg / "memory" / "logical-thinking.md"
            self.assertTrue(log.exists())
            text = log.read_text(encoding="utf-8")
            self.assertIn("Internal monologue", text)
            self.assertIn("Root cause: missing dep", text)


class F3UniformDiscipline(unittest.TestCase):
    def test_deep_reasoners_carry_thought_directive(self):
        base = Path("src/duckln/harness/agents")
        for spec in ("recovery_agent.md", "recovery_supervisor.md", "planning_investigator.md"):
            self.assertIn("<thought>", (base / spec).read_text(encoding="utf-8"), spec)

    def test_planner_stays_lean(self):
        # Plan 159 leanness is preserved — the planner is NOT forced into a <thought> prefix.
        txt = Path("src/duckln/harness/agents/planner.md").read_text(encoding="utf-8")
        self.assertNotIn("<thought>", txt)

    def test_extract_json_strips_leading_thought(self):
        from duckln.harness.agent_def import _extract_json

        self.assertEqual(_extract_json('<thought>reasoning</thought>[{"a":1}]'), [{"a": 1}])
        self.assertEqual(_extract_json('{"a":1}'), {"a": 1})  # no-thought path unchanged


class F4PulledSkillLoaded(unittest.TestCase):
    def _store(self, d):
        from state.store import initialize_state_store

        return initialize_state_store(Path(d))

    def test_registered_skill_is_loaded_and_matched(self):
        from duckln.subagents import registered_skill_hints

        with TemporaryDirectory() as d:
            self._store(d).upsert_managed_memory(
                memory_key="registered-skill:terraform",
                memory_kind="registered_skill",
                relative_path="skills/terraform.md",
                title="Read Terraform plans safely",
                content="When reviewing terraform, never apply; inspect the plan output.",
            )
            hints = registered_skill_hints(d, task_text="review the terraform plan")
            self.assertTrue(any("Terraform" in h for h in hints))

    def test_no_store_is_noop(self):
        from duckln.subagents import registered_skill_hints

        self.assertEqual(registered_skill_hints(None), ())


class F5PulledToolExecutable(unittest.TestCase):
    def _store(self, d):
        from state.store import initialize_state_store

        return initialize_state_store(Path(d))

    def test_registered_shell_tool_becomes_executable_spec(self):
        from duckln.harness.tools import _registered_extension_specs, build_default_registry

        with TemporaryDirectory() as d:
            self._store(d).upsert_managed_memory(
                memory_key="registered-tool:greet",
                memory_kind="registered_tool",
                relative_path="tools/tool-greet.json",
                title="greet",
                content=json.dumps({
                    "tool_id": "tool.greet", "description": "echo a greeting",
                    "draft_config": {"command": "echo hi", "provider_type": "shell"},
                }),
            )
            specs = _registered_extension_specs(d)
            self.assertTrue(any(s.name == "tool.greet" for s in specs))
            reg = build_default_registry(include_handlers=True, config_dir=d)
            self.assertIsNotNone(reg.lookup("tool.greet"))

    def test_unconfigured_shell_tool_fails_gracefully(self):
        from duckln.harness.tools import _make_registered_shell_handler

        ctx = types.SimpleNamespace(execution_target="local", project_dir=None, agent_name="t")
        res = _make_registered_shell_handler("tool.x", "")({}, ctx)
        self.assertFalse(res.ok)
        self.assertEqual(res.error_code, "not_configured")

    def test_unconfigured_mcp_fails_gracefully(self):
        from duckln.harness.tools import _call_mcp_stdio_tool

        res = _call_mcp_stdio_tool({}, tool_name="x", arguments={})
        self.assertFalse(res.ok)
        self.assertEqual(res.error_code, "not_configured")


if __name__ == "__main__":
    unittest.main()
