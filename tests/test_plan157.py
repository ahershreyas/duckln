"""Plan 157 — spec-driven harness: real YAML, enforced contracts, executing specs.

P1 parser/loader/version-gate are covered in test_harness_agent_def.py (Plan157SpecDrivenTests).
This file covers P2 (contract enforcement + run_spec re-ask) and the cross-phase behaviors.
"""

from __future__ import annotations

import unittest

from duckln.harness.agent_def import (
    builtin_agents_directory,
    load_agent_definition_from_text,
    render_output_contract_text,
    validate_agent_output,
    validate_input_contract,
    AgentRegistry,
    REQUIRED_AGENTS,
)
from duckln.harness.loop import run_spec, SpecRunResult


_ARRAY_SPEC = """---
name: arr
role: r
tools: []
max_contract_retries: 2
input_contract:
  required_state_keys:
    - repo.detected_files
output_contract:
  type: json_array
  description: a list of candidate steps
  item_schema:
    title: {type: string}
---
You propose steps. Output a JSON array.
"""

_OBJ_SPEC = """---
name: obj
role: r
tools: []
output_contract:
  type: json_object
  schema:
    cause: {type: string}
    fix: {type: "object | null"}
---
You attribute failures. Output a JSON object.
"""


class InputContract(unittest.TestCase):
    def test_missing_keys_reported(self):
        d = load_agent_definition_from_text(_ARRAY_SPEC)
        self.assertEqual(validate_input_contract(d, {"other": 1}), ("repo.detected_files",))

    def test_satisfied(self):
        d = load_agent_definition_from_text(_ARRAY_SPEC)
        self.assertEqual(validate_input_contract(d, {"repo.detected_files": []}), ())

    def test_accepts_a_set_of_keys(self):
        d = load_agent_definition_from_text(_ARRAY_SPEC)
        self.assertEqual(validate_input_contract(d, {"repo.detected_files"}), ())


class OutputContract(unittest.TestCase):
    def test_array_ok(self):
        d = load_agent_definition_from_text(_ARRAY_SPEC)
        ok, parsed, errs = validate_agent_output(d, '[{"title": "Install"}]')
        self.assertTrue(ok)
        self.assertEqual(parsed, [{"title": "Install"}])

    def test_array_in_fences_ok(self):
        d = load_agent_definition_from_text(_ARRAY_SPEC)
        ok, parsed, errs = validate_agent_output(d, '```json\n[{"title":"x"}]\n```')
        self.assertTrue(ok)

    def test_array_rejects_object(self):
        d = load_agent_definition_from_text(_ARRAY_SPEC)
        ok, _p, errs = validate_agent_output(d, '{"not": "an array"}')
        self.assertFalse(ok)
        self.assertTrue(errs)

    def test_object_missing_key(self):
        d = load_agent_definition_from_text(_OBJ_SPEC)
        ok, _p, errs = validate_agent_output(d, '{"cause": "x"}')  # missing 'fix'
        self.assertFalse(ok)
        self.assertTrue(any("fix" in e for e in errs))

    def test_object_ok(self):
        d = load_agent_definition_from_text(_OBJ_SPEC)
        ok, parsed, errs = validate_agent_output(d, '{"cause": "x", "fix": null}')
        self.assertTrue(ok)

    def test_contract_text_contains_schema(self):
        d = load_agent_definition_from_text(_ARRAY_SPEC)
        text = render_output_contract_text(d)
        self.assertIn("json_array", text)
        self.assertIn("title", text)


class RunSpecReAsk(unittest.TestCase):
    def test_bad_then_good_reasks_with_schema(self):
        d = load_agent_definition_from_text(_ARRAY_SPEC)
        seen_prompts = []

        calls = {"n": 0}

        def fake(*, system_prompt, user_message):
            calls["n"] += 1
            seen_prompts.append(user_message)
            return "I think you should install things" if calls["n"] == 1 else '[{"title": "Install deps"}]'

        res = run_spec(d, "Plan the setup", fake, available_state={"repo.detected_files": []})
        self.assertTrue(res.ok)
        self.assertEqual(res.parsed, [{"title": "Install deps"}])
        self.assertEqual(calls["n"], 2)  # one re-ask fired
        # the re-ask message includes the schema + the violation
        self.assertIn("json_array", seen_prompts[1])
        self.assertIn("contract", seen_prompts[1].lower())

    def test_exhausts_retries_then_honest_fail(self):
        d = load_agent_definition_from_text(_ARRAY_SPEC)  # max_contract_retries: 2 → 3 attempts

        calls = {"n": 0}

        def always_bad(*, system_prompt, user_message):
            calls["n"] += 1
            return "never valid json"

        res = run_spec(d, "Plan", always_bad, available_state={"repo.detected_files": []})
        self.assertFalse(res.ok)
        self.assertEqual(calls["n"], 3)  # 1 + max_contract_retries
        self.assertTrue(res.errors)

    def test_reports_missing_input_keys(self):
        d = load_agent_definition_from_text(_ARRAY_SPEC)
        res = run_spec(d, "Plan", lambda **k: '[]', available_state={})
        self.assertIn("repo.detected_files", res.missing_input_keys)


class RequiredAgentsShip(unittest.TestCase):
    def test_required_agents_present_in_builtin_dir(self):
        reg = AgentRegistry.from_directory(builtin_agents_directory())
        for name in REQUIRED_AGENTS:
            self.assertIsNotNone(reg.lookup(name), f"required agent missing: {name}")


class PlanningAgentsAreSpecDriven(unittest.TestCase):
    """P3: the planning prompts come from .md specs; the constants are retired."""

    def test_plan_prompt_constants_retired(self):
        import duckln.prompts as prompts
        for const in ("SYSTEM_PROMPT_PLAN_PROPOSE", "SYSTEM_PROMPT_PLAN_CRITIQUE",
                      "SYSTEM_PROMPT_PLAN_CLARIFY", "SYSTEM_PROMPT_PLAN_VERDICT",
                      "SYSTEM_PROMPT_PLAN_ATTRIBUTE"):
            self.assertFalse(hasattr(prompts, const), f"{const} should be retired")

    def test_spec_prompt_sources_from_md_body(self):
        import duckln.plan_mode as pm
        # the planner stage's prompt is the planner.md body, not a Python constant
        self.assertIn("propose", pm._spec_prompt("planner").lower())
        self.assertIn("critic", pm._spec_prompt("critic").lower())
        self.assertIn("verdict", pm._spec_prompt("verdict").lower())

    def test_verdict_is_its_own_spec(self):
        reg = AgentRegistry.from_directory(builtin_agents_directory())
        self.assertIsNotNone(reg.lookup("verdict"))


class AllSpecsOnReferenceFormat(unittest.TestCase):
    """P6: every shipped spec is current-schema + tool-valid; recovery prompts are specs."""

    def test_every_spec_declares_supported_version(self):
        from duckln.harness.agent_def import builtin_agents_directory as d, load_agent_definition_from_path
        for spec in d().glob("*.md"):
            if spec.stem.upper() == "README":
                continue
            ad = load_agent_definition_from_path(spec)
            self.assertTrue(ad.version, f"{spec.name} should declare a version")

    def test_recovery_specs_exist(self):
        reg = AgentRegistry.from_directory(builtin_agents_directory())
        for name in ("recovery_agent", "recovery_supervisor", "planning_investigator"):
            self.assertIsNotNone(reg.lookup(name), f"missing recovery spec: {name}")

    def test_conversation_persona_sourced_from_spec(self):
        from duckln.conversation_routes.provider_support import _conversation_persona_base
        # Plan 174 F5: role renamed "mentor" → "repo manager".
        self.assertIn("terminal-first repo manager", _conversation_persona_base())

    def test_recovery_prompt_constants_retired(self):
        import duckln.recovery as rec
        for const in ("_RECOVERY_SYSTEM", "_SUPERVISOR_SYSTEM", "_PLANNING_SYSTEM"):
            self.assertFalse(hasattr(rec, const), f"{const} should be retired")


if __name__ == "__main__":
    unittest.main()
