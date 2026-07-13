"""Tests for the Duckln tool registry and tools manifest."""

from __future__ import annotations

import json
import unittest

from duckln.tool_registry import (
    ToolVisibilityPolicy,
    active_tool_order,
    default_tool_registry_entries,
    render_tools_manifest,
    validate_tools_manifest,
)


class ToolRegistryTest(unittest.TestCase):
    def test_render_tools_manifest_contains_multipass_and_shell(self) -> None:
        manifest = render_tools_manifest(policy=ToolVisibilityPolicy(execution_target="local"))
        payload = json.loads(manifest.content)

        tool_ids = {item["id"] for item in payload["tools"]}
        self.assertIn("shell.command_runner", tool_ids)
        self.assertIn("filesystem.read", tool_ids)
        self.assertIn("web.documentation_lookup", tool_ids)
        self.assertIn("vm.multipass_provision", tool_ids)
        self.assertIn("docker.local_engine", tool_ids)
        self.assertIn("cloud.resource_registry", tool_ids)
        self.assertEqual("canonical-multipass", payload["approved_vm_tool"])
        self.assertTrue(payload["tool_order"])
        self.assertEqual(payload["tool_order"][0], "shell.command_runner")
        self.assertTrue(payload["usage_rules"]["announce_tool_use_before_action"])
        self.assertTrue(payload["usage_rules"]["show_agent_handoffs_to_user"])
        self.assertTrue(payload["usage_rules"]["show_cited_sources_when_web_is_used"])
        self.assertTrue(payload["trace_contract"]["shared_duckln_trace"])
        self.assertTrue(payload["trace_contract"]["show_web_query_when_broad_search_is_used"])
        self.assertEqual(30, payload["cloud_guardrails"]["idle_shutdown_minutes_default"])
        shell_tool = next(item for item in payload["tools"] if item["id"] == "shell.command_runner")
        self.assertTrue(shell_tool["use_when"])
        self.assertTrue(shell_tool["avoid_when"])
        self.assertTrue(shell_tool["examples"])

    def test_tools_manifest_filters_local_only_tools_for_vm_execution_target(self) -> None:
        manifest = render_tools_manifest(policy=ToolVisibilityPolicy(execution_target="vm"))
        payload = json.loads(manifest.content)
        tool_ids = {item["id"] for item in payload["tools"]}

        self.assertNotIn("system.local_probe", tool_ids)
        self.assertNotIn("vm.multipass_provision", tool_ids)
        self.assertIn("shell.command_runner", tool_ids)
        self.assertIn("runtime.terminal_pane", tool_ids)

    def test_validate_tools_manifest_catches_missing_required_keys(self) -> None:
        errors = validate_tools_manifest('{"tools":[{"id":"x"}]}')

        self.assertTrue(errors)
        self.assertTrue(any("label" in error for error in errors))

    def test_active_tool_order_preserves_registry_priority(self) -> None:
        ordered = active_tool_order(policy=ToolVisibilityPolicy(execution_target="local"))

        self.assertTrue(ordered)
        self.assertEqual("shell.command_runner", ordered[0])
        self.assertIn("web.documentation_lookup", ordered)
        self.assertLess(ordered.index("filesystem.read"), ordered.index("web.documentation_lookup"))


if __name__ == "__main__":
    unittest.main()
