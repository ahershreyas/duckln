"""Tests for scoped subagent runtime profiles."""

from __future__ import annotations

import tempfile
import unittest

from duckln.subagent_runtime import build_subagent_runtime_profile, select_subagent_descriptor
from state.access import initialize_managed_memory_state


class SubagentRuntimeProfileTest(unittest.TestCase):
    def test_build_subagent_runtime_profile_reads_scoped_workspace_sections(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            initialize_managed_memory_state(temp_dir)

            profile = build_subagent_runtime_profile(
                config_dir=temp_dir,
                slug="python_setup",
                execution_target="local",
            )

            self.assertIsNotNone(profile)
            assert profile is not None
            self.assertEqual("python_setup", profile.descriptor.slug)
            self.assertIn("subagents/python_setup/AGENTS.md", profile.workspace_sections)
            self.assertIn("use only tools from tools.json", profile.prompt_template.lower())
            self.assertIn("Repo file inspection", profile.allowed_tool_labels)
            self.assertIn("Web and trusted public source lookup", profile.allowed_tool_labels)

    def test_select_subagent_descriptor_uses_repo_family_and_execution_target(self) -> None:
        descriptor = select_subagent_descriptor(
            repo_family="node_typescript",
            execution_target="local",
            detected_files=("package.json",),
        )

        self.assertIsNotNone(descriptor)
        assert descriptor is not None
        self.assertEqual("node_typescript", descriptor.slug)

    def test_select_subagent_descriptor_uses_signal_files_for_multi_service(self) -> None:
        descriptor = select_subagent_descriptor(
            repo_family="multi_service",
            execution_target="local",
            detected_files=("package.json", "Dockerfile"),
        )

        self.assertIsNotNone(descriptor)
        assert descriptor is not None
        self.assertEqual("node_typescript", descriptor.slug)


class SubagentContractEnrichmentTest(unittest.TestCase):
    def test_cpp_native_contract_lists_specific_signal_files(self) -> None:
        from duckln.subagents import default_subagent_contracts

        contracts = {c.relative_path: c.content for c in default_subagent_contracts()}
        cpp_agents = contracts["subagents/cpp_native/AGENTS.md"]
        self.assertIn("## Signal Files", cpp_agents)
        self.assertIn("CMakeLists.txt", cpp_agents)
        self.assertIn("Cargo.toml", cpp_agents)
        self.assertIn("## Repo Families", cpp_agents)
        self.assertIn("- cpp_native", cpp_agents)

    def test_python_setup_contract_lists_allowed_tool_ids(self) -> None:
        from duckln.subagents import default_subagent_contracts

        contracts = {c.relative_path: c.content for c in default_subagent_contracts()}
        py_agents = contracts["subagents/python_setup/AGENTS.md"]
        self.assertIn("`shell.command_runner`", py_agents)
        self.assertIn("`filesystem.read`", py_agents)
        self.assertIn("`process.session_runtime`", py_agents)

    def test_vm_environment_contract_scopes_execution_targets_to_vm_only(self) -> None:
        from duckln.subagents import default_subagent_contracts

        contracts = {c.relative_path: c.content for c in default_subagent_contracts()}
        vm_agents = contracts["subagents/vm_environment/AGENTS.md"]
        self.assertIn("Active execution target scope: vm.", vm_agents)
        # vm specialist should NOT carry shell.command_runner — it doesn't run repo
        # commands directly, only provisions the VM.
        self.assertNotIn("`shell.command_runner`", vm_agents)


if __name__ == "__main__":
    unittest.main()
