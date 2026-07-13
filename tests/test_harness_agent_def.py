"""Plan 65 Phase 2 — AgentDefinition + YAML-frontmatter loader tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from duckln.harness.agent_def import (
    AgentDefinition,
    AgentRegistry,
    REQUIRED_AGENTS,
    SUPPORTED_SPEC_VERSION,
    builtin_agents_directory,
    load_agent_definition_from_text,
    parse_agent_spec_markdown,
)
from duckln.harness.tools import build_default_registry


SAMPLE_SPEC = """---
name: test_agent
role: Does test things
tools:
  - shell.probe
  - fs.read_file
max_turns: 5
budget_seconds: 120.0
budget_llm_calls: 4
is_coordinator: false
---

You are a test agent. Be helpful.
"""


SAMPLE_INLINE_LIST_SPEC = """---
name: inline_list_agent
role: Uses inline list syntax
tools: [shell.probe, web.search, user.approve]
can_spawn: []
---

System prompt.
"""


class YamlParserTests(unittest.TestCase):
    """Plan 157 P1-1: frontmatter is parsed with PyYAML — folded scalars, nested
    maps, inline + block lists all parse."""

    def test_inline_list_parse(self) -> None:
        parsed = parse_agent_spec_markdown("---\ntools: [a, b, c]\nname: n\nrole: r\n---\nbody")
        self.assertEqual(parsed["tools"], ["a", "b", "c"])

    def test_block_list_parse(self) -> None:
        parsed = parse_agent_spec_markdown("---\ntools:\n  - one\n  - two\nname: n\nrole: r\n---\nbody")
        self.assertEqual(parsed["tools"], ["one", "two"])

    def test_folded_scalar_role(self) -> None:
        # The reference format uses a folded `>` scalar for role — must parse.
        spec = "---\nname: n\nrole: >\n  line one\n  line two\ntools: []\n---\nbody"
        parsed = parse_agent_spec_markdown(spec)
        self.assertIn("line one line two", parsed["role"])

    def test_nested_contracts_parse(self) -> None:
        spec = (
            "---\nname: n\nrole: r\ntools: []\n"
            "input_contract:\n  required_state_keys:\n    - repo.detected_files\n"
            "output_contract:\n  type: json_array\n  item_schema:\n    title: {type: string}\n"
            "---\nbody"
        )
        parsed = parse_agent_spec_markdown(spec)
        self.assertEqual(parsed["input_contract"]["required_state_keys"], ["repo.detected_files"])
        self.assertEqual(parsed["output_contract"]["type"], "json_array")

    def test_mixed_scalars(self) -> None:
        parsed = parse_agent_spec_markdown(
            "---\nname: foo\nrole: r\ntools: []\nmax_turns: 8\nbudget_seconds: 1.5\n---\nbody"
        )
        self.assertEqual(parsed["name"], "foo")
        self.assertEqual(parsed["max_turns"], 8)
        self.assertEqual(parsed["budget_seconds"], 1.5)


class FrontmatterParseTests(unittest.TestCase):
    def test_missing_frontmatter_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_agent_spec_markdown("just markdown, no frontmatter")

    def test_parses_body_after_frontmatter(self) -> None:
        parsed = parse_agent_spec_markdown(SAMPLE_SPEC)
        self.assertEqual(parsed["name"], "test_agent")
        self.assertIn("test agent", parsed["_body"].lower())


class LoadDefinitionTests(unittest.TestCase):
    def test_load_full_spec(self) -> None:
        ad = load_agent_definition_from_text(SAMPLE_SPEC)
        self.assertEqual(ad.name, "test_agent")
        self.assertEqual(ad.role, "Does test things")
        self.assertEqual(ad.tools, ("shell.probe", "fs.read_file"))
        self.assertEqual(ad.max_turns, 5)
        self.assertEqual(ad.budget_seconds, 120.0)
        self.assertEqual(ad.budget_llm_calls, 4)
        self.assertFalse(ad.is_coordinator)

    def test_load_inline_list_spec(self) -> None:
        ad = load_agent_definition_from_text(SAMPLE_INLINE_LIST_SPEC)
        self.assertEqual(ad.tools, ("shell.probe", "web.search", "user.approve"))
        self.assertEqual(ad.can_spawn, ())

    def test_defaults_applied_when_fields_missing(self) -> None:
        minimal = """---
name: minimal_agent
role: minimal
tools: []
---

Body.
"""
        ad = load_agent_definition_from_text(minimal)
        self.assertEqual(ad.max_turns, 8)  # default
        self.assertEqual(ad.budget_seconds, 300.0)  # default
        self.assertEqual(ad.budget_llm_calls, 8)  # default
        self.assertFalse(ad.is_coordinator)

    def test_missing_name_raises(self) -> None:
        bad = """---
role: orphan
tools: []
---
body
"""
        with self.assertRaises(ValueError):
            load_agent_definition_from_text(bad)

    def test_invalid_tools_type_raises(self) -> None:
        bad = """---
name: x
role: y
tools: not_a_list
---
body
"""
        with self.assertRaises(ValueError):
            load_agent_definition_from_text(bad)


class AgentDefinitionValidationTests(unittest.TestCase):
    def _make(self, **overrides) -> AgentDefinition:
        defaults = dict(
            name="a", role="r", system_prompt="p", tools=(),
        )
        defaults.update(overrides)
        return AgentDefinition(**defaults)

    def test_empty_name_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self._make(name="")

    def test_empty_role_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self._make(role="")

    def test_empty_prompt_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self._make(system_prompt="   ")

    def test_zero_max_turns_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self._make(max_turns=0)

    def test_negative_budget_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self._make(budget_seconds=-1.0)


class AgentRegistryTests(unittest.TestCase):
    def test_register_and_lookup(self) -> None:
        reg = AgentRegistry()
        ad = load_agent_definition_from_text(SAMPLE_SPEC)
        reg.register(ad)
        self.assertIs(reg.lookup("test_agent"), ad)
        self.assertIsNone(reg.lookup("nope"))

    def test_duplicate_registration_raises(self) -> None:
        reg = AgentRegistry()
        ad = load_agent_definition_from_text(SAMPLE_SPEC)
        reg.register(ad)
        with self.assertRaises(ValueError):
            reg.register(ad)

    def test_validate_against_tool_registry_catches_unknown_tools(self) -> None:
        bogus = """---
name: bogus
role: refers to nonexistent tools
tools: [does.not.exist, shell.run]
---
prompt
"""
        reg = AgentRegistry()
        reg.register(load_agent_definition_from_text(bogus))
        errors = reg.validate_against_tool_registry({"shell.run"})
        self.assertEqual(len(errors), 1)
        self.assertIn("does.not.exist", errors[0])

    def test_validate_catches_unknown_can_spawn(self) -> None:
        spec = """---
name: parent
role: spawns a missing child
tools: []
can_spawn: [nonexistent_child]
---
prompt
"""
        reg = AgentRegistry()
        reg.register(load_agent_definition_from_text(spec))
        errors = reg.validate_against_tool_registry(set())
        self.assertTrue(any("nonexistent_child" in e for e in errors))

    def test_from_directory_loads_all_md_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "a.md").write_text(
                SAMPLE_SPEC.replace("test_agent", "a_agent"), encoding="utf-8"
            )
            (d / "b.md").write_text(
                SAMPLE_INLINE_LIST_SPEC.replace("inline_list_agent", "b_agent"),
                encoding="utf-8",
            )
            (d / "not_a_spec.txt").write_text("ignored", encoding="utf-8")
            reg = AgentRegistry.from_directory(d, required=frozenset())
            self.assertEqual(set(reg.names()), {"a_agent", "b_agent"})


class BuiltinAgentsTests(unittest.TestCase):
    """Validate the pilot specs ship with valid configurations."""

    def test_supervisor_spec_loads(self) -> None:
        reg = AgentRegistry.from_directory(builtin_agents_directory())
        sup = reg.lookup("supervisor")
        self.assertIsNotNone(sup)
        self.assertTrue(sup.is_coordinator)

    def test_node_specialist_spec_loads(self) -> None:
        reg = AgentRegistry.from_directory(builtin_agents_directory())
        spec = reg.lookup("node_typescript_specialist")
        self.assertIsNotNone(spec)
        self.assertIn("shell.run", spec.tools)
        self.assertIn("user.approve", spec.tools)

    def test_builtin_agents_pass_tool_validation(self) -> None:
        """Every shipped spec must only reference tools that exist in the
        default tool registry (and only spawn agents that exist)."""
        agent_reg = AgentRegistry.from_directory(builtin_agents_directory())
        tool_reg = build_default_registry(include_handlers=False)
        errors = agent_reg.validate_against_tool_registry(set(tool_reg.names()))
        self.assertEqual(errors, ())

    def test_all_required_agents_present(self) -> None:
        reg = AgentRegistry.from_directory(builtin_agents_directory())
        for name in REQUIRED_AGENTS:
            self.assertIsNotNone(reg.lookup(name), f"required agent missing: {name}")


class Plan157SpecDrivenTests(unittest.TestCase):
    """Plan 157: version gate, contract fields, robust loader."""

    def test_version_too_high_raises(self) -> None:
        spec = '---\nname: future\nrole: r\ntools: []\nversion: "99.0"\n---\nbody'
        with self.assertRaises(ValueError):
            load_agent_definition_from_text(spec)

    def test_supported_version_loads(self) -> None:
        spec = f'---\nname: ok\nrole: r\ntools: []\nversion: "{SUPPORTED_SPEC_VERSION}"\n---\nbody'
        ad = load_agent_definition_from_text(spec)
        self.assertEqual(ad.version, SUPPORTED_SPEC_VERSION)

    def test_contract_fields_and_retries_parsed(self) -> None:
        spec = (
            "---\nname: c\nrole: r\ntools: []\nmax_contract_retries: 1\n"
            "input_contract:\n  required_state_keys:\n    - a.b\n"
            "output_contract:\n  type: json_array\n---\nbody"
        )
        ad = load_agent_definition_from_text(spec)
        self.assertEqual(ad.max_contract_retries, 1)
        self.assertEqual(ad.input_contract["required_state_keys"], ["a.b"])
        self.assertEqual(ad.output_contract["type"], "json_array")
        # default retries when unspecified = 2
        ad2 = load_agent_definition_from_text("---\nname: d\nrole: r\ntools: []\n---\nbody")
        self.assertEqual(ad2.max_contract_retries, 2)

    def test_from_directory_skips_malformed_optional_but_loads_rest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "good.md").write_text(SAMPLE_SPEC.replace("test_agent", "good"), encoding="utf-8")
            (d / "bad.md").write_text("no frontmatter here", encoding="utf-8")
            reg = AgentRegistry.from_directory(d, required=frozenset())
            self.assertIn("good", reg.names())
            self.assertNotIn("bad", reg.names())

    def test_from_directory_raises_when_required_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "good.md").write_text(SAMPLE_SPEC.replace("test_agent", "good"), encoding="utf-8")
            with self.assertRaises(ValueError):
                AgentRegistry.from_directory(d, required=frozenset({"planner"}))


if __name__ == "__main__":
    unittest.main()
