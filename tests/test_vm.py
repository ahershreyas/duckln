"""Tests for Multipass Ubuntu VM provisioning."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest

from agent.probe import GpuProbeState, SystemProbe
from duckln.config import resolve_config_paths
from duckln.shell import CommandResult
from duckln.vm import (
    DEFAULT_VM_NAME,
    VmConfig,
    build_multipass_launch_command,
    build_vm_runtime_install_command,
    configure_existing_multipass_vm,
    create_multipass_vm,
    installation_guidance,
    next_available_vm_name,
)
from state.store import resolve_state_store_paths


class FakeRunner:
    def __init__(self, responses: dict[str, CommandResult]) -> None:
        self.responses = responses
        self.commands: list[str] = []

    def run(self, command: str, *, timeout_seconds: float = 30.0, cwd: str | None = None, env=None) -> CommandResult:
        self.commands.append(command)
        return self.responses.get(
            command,
            CommandResult(
                command=command,
                exit_code=0,
                stdout="",
                stderr="",
                timed_out=False,
                duration_seconds=0.01,
            ),
        )


def _ok(command: str, *, stdout: str = "") -> CommandResult:
    return CommandResult(
        command=command,
        exit_code=0,
        stdout=stdout,
        stderr="",
        timed_out=False,
        duration_seconds=0.01,
    )


class VmProvisionTest(unittest.TestCase):
    def test_next_available_vm_name_auto_increments(self) -> None:
        self.assertEqual(
            "duckln-vm-2",
            next_available_vm_name(("duckln-vm", "duckln-vm-1"), DEFAULT_VM_NAME),
        )

    def test_build_multipass_launch_command_uses_selected_resources(self) -> None:
        command = build_multipass_launch_command(VmConfig(name="duckln-vm-1", cpu_count=4, memory_gb=8))

        self.assertEqual("multipass launch 24.04 --name duckln-vm-1 --cpus 4 --memory 8G", command)

    def test_create_multipass_vm_guides_install_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            displayed: list[str] = []

            from unittest.mock import patch

            with patch("duckln.vm.is_multipass_installed", return_value=False):
                result = create_multipass_vm(
                    paths,
                    display=displayed.append,
                    system_probe=SystemProbe(
                        operating_system="Darwin",
                        architecture="arm64",
                        cpu_logical_cores=8,
                        ram_bytes=16 * 1024**3,
                        disk_free_bytes=100 * 1024**3,
                        python_version="3.11.8",
                        gpu=GpuProbeState(
                            backend="mps",
                            summary="Apple Silicon detected; MPS available.",
                            cuda_capable=False,
                            cuda_available=False,
                            mps_capable=True,
                            mps_available=True,
                        ),
                    ),
                )

            self.assertFalse(result.ok)
            self.assertTrue(any("Local is usually the best first choice." in message for message in displayed))
            self.assertTrue(any("CUDA is unsupported here." in message for message in displayed))
            self.assertIn("brew install --cask multipass", result.message)

    def test_create_multipass_vm_shows_host_cuda_without_vm_gpu_access_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            displayed: list[str] = []

            from unittest.mock import patch

            with patch("duckln.vm.is_multipass_installed", return_value=False):
                create_multipass_vm(
                    paths,
                    display=displayed.append,
                    system_probe=SystemProbe(
                        operating_system="Linux",
                        architecture="x86_64",
                        cpu_logical_cores=8,
                        ram_bytes=32 * 1024**3,
                        disk_free_bytes=200 * 1024**3,
                        python_version="3.11.8",
                        gpu=GpuProbeState(
                            backend="cuda",
                            summary="CUDA available in Python (RTX 4090).",
                            cuda_capable=True,
                            cuda_available=True,
                            mps_capable=False,
                            mps_available=False,
                        ),
                    ),
                )

            self.assertTrue(any("Host CUDA is available" in message for message in displayed))
            self.assertTrue(any("no GPU access" in message for message in displayed))

    def test_create_multipass_vm_creates_vm_and_records_linkage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            displayed: list[str] = []
            list_command = "multipass list --format json"
            launch_command = "multipass launch 24.04 --name duckln-vm-1 --cpus 2 --memory 4G"
            runner = FakeRunner(
                {
                    list_command: _ok(list_command, stdout='{"list": {"duckln-vm": {}}}'),
                    launch_command: _ok(launch_command),
                }
            )
            prompts = iter(("2", "4", ""))

            from unittest.mock import patch

            with patch("duckln.vm.is_multipass_installed", return_value=True):
                result = create_multipass_vm(
                    paths,
                    text_prompt=lambda prompt, default: next(prompts),
                    display=displayed.append,
                    runner=runner,
                    system_probe=SystemProbe(
                        operating_system="Linux",
                        architecture="x86_64",
                        cpu_logical_cores=8,
                        ram_bytes=16 * 1024**3,
                        disk_free_bytes=100 * 1024**3,
                        python_version="3.11.8",
                        gpu=GpuProbeState(
                            backend="cpu",
                            summary="No CUDA-capable GPU detected.",
                            cuda_capable=False,
                            cuda_available=False,
                            mps_capable=False,
                            mps_available=False,
                        ),
                    ),
                )

            self.assertTrue(result.ok)
            self.assertEqual("duckln-vm-1", result.vm_name)
            self.assertEqual(
                (
                    "multipass shell duckln-vm-1",
                    "multipass stop duckln-vm-1",
                    "multipass list",
                ),
                result.connection_commands,
            )
            self.assertIn(launch_command, runner.commands)
            self.assertTrue(any("Ubuntu VM ready: duckln-vm-1" == message for message in displayed))

            database_file = resolve_state_store_paths(paths.config_dir).database_file
            with sqlite3.connect(database_file) as connection:
                row = connection.execute(
                    "SELECT vm_name, status, summary FROM vm_linkage WHERE vm_name = ?",
                    ("duckln-vm-1",),
                ).fetchone()
            self.assertEqual(("duckln-vm-1", "ready", "Ubuntu VM created and ready."), row)

    def test_build_vm_runtime_install_command_is_bounded(self) -> None:
        command = build_vm_runtime_install_command("duckln-vm")

        self.assertIn("multipass exec duckln-vm -- bash -lc", command)
        self.assertIn("python3-venv", command)
        self.assertIn("~/.duckln/memory/sessions", command)

    def test_configure_existing_vm_leaves_vm_ready_when_duckln_install_is_declined(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            list_command = "multipass list --format json"
            runner = FakeRunner(
                {
                    list_command: _ok(list_command, stdout='{"list": {"duckln-vm": {}}}'),
                }
            )
            displayed: list[str] = []

            result = configure_existing_multipass_vm(
                "duckln-vm",
                paths,
                select=lambda prompt, choices: "No",
                display=displayed.append,
                runner=runner,
            )

            self.assertTrue(result.ok)
            self.assertEqual("VM is ready without Duckln installed inside it.", result.message)
            self.assertEqual(
                (
                    "multipass shell duckln-vm",
                    "multipass stop duckln-vm",
                    "multipass list",
                ),
                result.connection_commands,
            )
            self.assertEqual([list_command], runner.commands)
            self.assertIn("multipass shell duckln-vm", displayed)

            database_file = resolve_state_store_paths(paths.config_dir).database_file
            with sqlite3.connect(database_file) as connection:
                row = connection.execute(
                    "SELECT status, summary FROM vm_linkage WHERE vm_name = ?",
                    ("duckln-vm",),
                ).fetchone()
            self.assertEqual(("ready_without_duckln", "VM is ready without Duckln installed inside it."), row)

    def test_configure_existing_vm_installs_duckln_after_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            list_command = "multipass list --format json"
            install_command = build_vm_runtime_install_command("duckln-vm")
            runner = FakeRunner(
                {
                    list_command: _ok(list_command, stdout='{"list": {"duckln-vm": {}}}'),
                    install_command: _ok(install_command),
                }
            )
            displayed: list[str] = []

            result = configure_existing_multipass_vm(
                "duckln-vm",
                paths,
                select=lambda prompt, choices: "Yes",
                display=displayed.append,
                runner=runner,
            )

            self.assertTrue(result.ok)
            self.assertEqual("Duckln is installed inside the VM.", result.message)
            self.assertEqual(
                (
                    "multipass shell duckln-vm",
                    "duckln",
                    "/config",
                    "exit",
                ),
                result.connection_commands,
            )
            self.assertEqual([list_command, install_command], runner.commands)
            self.assertTrue(any("Configure provider/model inside the VM" in message for message in displayed))
            self.assertTrue(any("API-dependent steps should wait" in message for message in displayed))

            database_file = resolve_state_store_paths(paths.config_dir).database_file
            with sqlite3.connect(database_file) as connection:
                row = connection.execute(
                    "SELECT status, summary FROM vm_linkage WHERE vm_name = ?",
                    ("duckln-vm",),
                ).fetchone()
            self.assertEqual(("duckln_installed", "Duckln is installed inside the VM."), row)


if __name__ == "__main__":
    unittest.main()
