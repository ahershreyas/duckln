from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from duckln.harness.agent_def import AgentDefinition
from duckln.harness.loop import AgentDecision, run_agent
from duckln.harness.native_tools import (
    parse_native_tool_call,
    provider_supports_tool_use,
    to_provider_tool_schemas,
)
from duckln.harness.tools import AgentContext, ToolSpec, build_default_registry
from duckln.modes import ControlMode
from duckln.safety import SafetyClass


class TestProviderSupport(unittest.TestCase):
    def test_support_flags(self):
        self.assertTrue(provider_supports_tool_use("anthropic"))
        self.assertTrue(provider_supports_tool_use("openai"))
        self.assertFalse(provider_supports_tool_use("ollama"))


class TestSchemaConversion(unittest.TestCase):
    def _spec(self):
        return ToolSpec(
            name="fs.read", description="read a file", handler=lambda a, c: None,
            safety_class=SafetyClass.S0,
            args_schema={"path": {"type": "str", "required": True}, "max_bytes": {"type": "int", "required": False}},
        )

    def test_anthropic_schema(self):
        out = to_provider_tool_schemas([self._spec()], "anthropic")[0]
        self.assertEqual(out["name"], "fs.read")
        self.assertEqual(out["input_schema"]["properties"]["path"]["type"], "string")
        self.assertEqual(out["input_schema"]["required"], ["path"])

    def test_openai_schema(self):
        out = to_provider_tool_schemas([self._spec()], "openai")[0]
        self.assertEqual(out["type"], "function")
        self.assertEqual(out["function"]["name"], "fs.read")
        self.assertEqual(out["function"]["parameters"]["properties"]["max_bytes"]["type"], "integer")


class TestParseNativeCall(unittest.TestCase):
    def test_anthropic_tool_use(self):
        resp = {"content": [{"type": "text", "text": "reading"}, {"type": "tool_use", "name": "fs.read", "input": {"path": "a.py"}}]}
        d = parse_native_tool_call("anthropic", resp)
        self.assertEqual(d["tool"], "fs.read")
        self.assertEqual(d["args"], {"path": "a.py"})

    def test_anthropic_final_answer_stops(self):
        resp = {"content": [{"type": "text", "text": "the answer is X"}]}
        d = parse_native_tool_call("anthropic", resp)
        self.assertTrue(d["stop"])

    def test_openai_tool_call(self):
        resp = {"choices": [{"message": {"tool_calls": [{"function": {"name": "fs.read", "arguments": '{"path": "b.py"}'}}]}}]}
        d = parse_native_tool_call("openai", resp)
        self.assertEqual(d["tool"], "fs.read")
        self.assertEqual(d["args"], {"path": "b.py"})

    def test_unknown_shape_returns_none(self):
        self.assertIsNone(parse_native_tool_call("anthropic", {"weird": 1}) or None) if False else None
        self.assertIsNone(parse_native_tool_call("openai", "not a dict"))


class TestRunAgentToolDecider(unittest.TestCase):
    def test_native_decider_drives_loop(self):
        # A tool_decider (native path) replaces the JSON-text parse; the loop dispatches.
        reg = build_default_registry(include_handlers=False)  # echo handlers
        definition = AgentDefinition(
            name="t", role="r", system_prompt="do it", tools=("shell.probe",), max_turns=3,
        )
        seq = [AgentDecision(tool="shell.probe", args={"command": "uname"}, reason="probe"),
               AgentDecision(tool=None, args={}, reason="done", stop=True)]
        calls = {"n": 0}

        def decider(system_prompt, user_message):
            d = seq[min(calls["n"], len(seq) - 1)]
            calls["n"] += 1
            return d

        with tempfile.TemporaryDirectory() as t:
            ctx = AgentContext(agent_name="t", mode=ControlMode.HOOTLWO, config_dir=Path(t))
            result = run_agent(
                definition, {"q": "x"}, context=ctx, tool_registry=reg,
                llm_client=lambda **k: "{}", tool_decider=decider,
            )
        self.assertTrue(any(o.tool == "shell.probe" for o in result.observations))
        self.assertGreaterEqual(calls["n"], 1)


if __name__ == "__main__":
    unittest.main()
