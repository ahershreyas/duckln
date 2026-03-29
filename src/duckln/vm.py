"""Multipass-based Ubuntu VM provisioning helpers."""

from __future__ import annotations

from dataclasses import dataclass
import json
import shutil
from typing import Callable

from agent.probe import SystemProbe, probe_system
from duckln.config import ConfigPaths
from duckln.shell import CommandResult, ControlledCommandRunner
from state.store import initialize_state_store


DEFAULT_VM_NAME = "duckln-vm"
DEFAULT_VM_CPUS = 2
DEFAULT_VM_MEMORY_GB = 4
UBUNTU_IMAGE = "24.04"


@dataclass(frozen=True)
class VmConfig:
    """User-selected VM sizing and name."""

    name: str
    cpu_count: int
    memory_gb: int


@dataclass(frozen=True)
class VmProvisionResult:
    """Outcome of provisioning a Multipass Ubuntu VM."""

    ok: bool
    vm_name: str
    message: str
    connection_commands: tuple[str, ...]


@dataclass(frozen=True)
class VmSyncResult:
    """Outcome of the post-create Duckln VM setup flow."""

    ok: bool
    vm_name: str
    message: str
    connection_commands: tuple[str, ...]


def create_multipass_vm(
    paths: ConfigPaths,
    *,
    text_prompt: Callable[[str, str], str | None] | None = None,
    display: Callable[[str], None] = print,
    runner: ControlledCommandRunner | None = None,
    system_probe: SystemProbe | None = None,
) -> VmProvisionResult:
    """Guide the user through a minimal Multipass Ubuntu VM creation flow."""

    prompt = text_prompt or _default_text_prompt_adapter
    runner_instance = runner or ControlledCommandRunner()
    system = system_probe or probe_system()

    for line in _vm_hardware_guidance(system):
        display(line)

    if not is_multipass_installed():
        message = installation_guidance(system.operating_system)
        display(message)
        _record_vm_state(paths, DEFAULT_VM_NAME, status="missing_multipass", summary=message)
        return VmProvisionResult(ok=False, vm_name=DEFAULT_VM_NAME, message=message, connection_commands=())

    existing_names = list_multipass_vm_names(runner=runner_instance)
    default_name = next_available_vm_name(existing_names)
    config = prompt_vm_config(
        prompt=prompt,
        default_name=default_name,
        default_cpu_count=DEFAULT_VM_CPUS,
        default_memory_gb=DEFAULT_VM_MEMORY_GB,
    )
    if config is None:
        message = "VM creation cancelled."
        display(message)
        return VmProvisionResult(ok=False, vm_name=default_name, message=message, connection_commands=())

    launch_command = build_multipass_launch_command(config)
    result = runner_instance.run(launch_command)
    if result.exit_code != 0 or result.timed_out:
        message = _command_failure_message("VM creation failed", result)
        display(message)
        _record_vm_state(paths, config.name, status="create_failed", summary=message, config=config)
        return VmProvisionResult(ok=False, vm_name=config.name, message=message, connection_commands=())

    connection_commands = (
        f"multipass shell {config.name}",
        f"multipass stop {config.name}",
        f"multipass list",
    )
    display(f"Ubuntu VM ready: {config.name}")
    for command in connection_commands:
        display(command)

    _record_vm_state(paths, config.name, status="ready", summary="Ubuntu VM created and ready.", config=config)
    return VmProvisionResult(
        ok=True,
        vm_name=config.name,
        message="Ubuntu VM created and ready.",
        connection_commands=connection_commands,
    )


def configure_existing_multipass_vm(
    vm_name: str,
    paths: ConfigPaths,
    *,
    select: Callable[[str, tuple[str, ...]], str | None] | None = None,
    display: Callable[[str], None] = print,
    runner: ControlledCommandRunner | None = None,
) -> VmSyncResult:
    """Ask whether Duckln should be installed inside an existing VM."""

    runner_instance = runner or ControlledCommandRunner()
    if vm_name not in list_multipass_vm_names(runner=runner_instance):
        message = f"Retryable error: VM {vm_name} was not found."
        display(message)
        _record_vm_state(paths, vm_name, status="missing", summary=message)
        return VmSyncResult(
            ok=False,
            vm_name=vm_name,
            message=message,
            connection_commands=(),
        )

    if not _confirm_install_inside_vm(select):
        message = "VM is ready without Duckln installed inside it."
        connection_commands = (
            f"multipass shell {vm_name}",
            f"multipass stop {vm_name}",
            "multipass list",
        )
        for line in connection_commands:
            display(line)
        _record_vm_state(paths, vm_name, status="ready_without_duckln", summary=message)
        return VmSyncResult(
            ok=True,
            vm_name=vm_name,
            message=message,
            connection_commands=connection_commands,
        )

    install_command = build_vm_runtime_install_command(vm_name)
    install_result = runner_instance.run(install_command)
    if install_result.exit_code != 0 or install_result.timed_out:
        message = _command_failure_message("Duckln VM runtime install failed", install_result)
        display(message)
        _record_vm_state(paths, vm_name, status="runtime_install_failed", summary=message)
        return VmSyncResult(
            ok=False,
            vm_name=vm_name,
            message=message,
            connection_commands=(),
        )

    connection_commands = (
        f"multipass shell {vm_name}",
        "duckln",
        "/config",
        "exit",
    )
    for line in _vm_post_sync_messages(vm_name):
        display(line)
    for line in connection_commands:
        display(line)

    summary = "Duckln is installed inside the VM."
    _record_vm_state(
        paths,
        vm_name,
        status="duckln_installed",
        summary=summary,
    )
    return VmSyncResult(
        ok=True,
        vm_name=vm_name,
        message=summary,
        connection_commands=connection_commands,
    )


def is_multipass_installed() -> bool:
    """Return whether the Multipass CLI is available."""

    return shutil.which("multipass") is not None


def installation_guidance(operating_system: str) -> str:
    """Return a concise install hint when Multipass is missing."""

    if operating_system == "Darwin":
        return "Multipass is not installed. Install it with: brew install --cask multipass"
    if operating_system == "Linux":
        return "Multipass is not installed. Install it with: sudo snap install multipass"
    if operating_system == "Windows":
        return "Multipass is not installed. Install it from https://multipass.run/"
    return "Multipass is not installed. Install it from https://multipass.run/"


def list_multipass_vm_names(*, runner: ControlledCommandRunner | None = None) -> tuple[str, ...]:
    """List existing Multipass instance names."""

    runner_instance = runner or ControlledCommandRunner()
    result = runner_instance.run("multipass list --format json")
    if result.exit_code != 0 or result.timed_out:
        return ()
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return ()

    info = payload.get("list", {})
    if isinstance(info, dict):
        return tuple(sorted(str(name) for name in info))
    if isinstance(info, list):
        names: list[str] = []
        for item in info:
            if isinstance(item, dict) and "name" in item:
                names.append(str(item["name"]))
        return tuple(sorted(names))
    return ()


def next_available_vm_name(existing_names: tuple[str, ...], base_name: str = DEFAULT_VM_NAME) -> str:
    """Return the first available VM name, auto-incrementing when needed."""

    existing = set(existing_names)
    if base_name not in existing:
        return base_name

    suffix = 1
    while f"{base_name}-{suffix}" in existing:
        suffix += 1
    return f"{base_name}-{suffix}"


def prompt_vm_config(
    *,
    prompt: Callable[[str, str], str | None],
    default_name: str,
    default_cpu_count: int,
    default_memory_gb: int,
) -> VmConfig | None:
    """Collect the guided VM configuration."""

    cpu_value = prompt("VM CPU count:", str(default_cpu_count))
    if cpu_value is None:
        return None
    memory_value = prompt("VM memory in GB:", str(default_memory_gb))
    if memory_value is None:
        return None
    name_value = prompt("VM name (leave blank for default):", default_name)
    if name_value is None:
        return None

    cpu_count = _parse_positive_int(cpu_value, field_name="CPU count")
    memory_gb = _parse_positive_int(memory_value, field_name="Memory")
    vm_name = (name_value.strip() or default_name).strip()
    if not vm_name:
        vm_name = default_name
    return VmConfig(name=vm_name, cpu_count=cpu_count, memory_gb=memory_gb)


def build_multipass_launch_command(config: VmConfig) -> str:
    """Build the bounded Multipass launch command."""

    return (
        f"multipass launch {UBUNTU_IMAGE} "
        f"--name {config.name} "
        f"--cpus {config.cpu_count} "
        f"--memory {config.memory_gb}G"
    )


def build_vm_runtime_install_command(vm_name: str) -> str:
    """Build the bounded command that prepares the Duckln runtime in the VM."""

    return (
        f"multipass exec {vm_name} -- bash -lc "
        "\"sudo apt-get update && "
        "sudo apt-get install -y python3 python3-venv python3-pip && "
        "mkdir -p ~/.duckln ~/.duckln/memory ~/.duckln/memory/skills ~/.duckln/memory/knowledge ~/.duckln/memory/sessions\""
    )


def _record_vm_state(
    paths: ConfigPaths,
    vm_name: str,
    *,
    status: str,
    summary: str,
    config: VmConfig | None = None,
    metadata: dict | None = None,
) -> None:
    payload: dict = {}
    if config is not None:
        payload.update(
            {
            "cpu_count": config.cpu_count,
            "memory_gb": config.memory_gb,
            }
        )
    if metadata:
        payload.update(metadata)
    initialize_state_store(paths.config_dir).upsert_vm_linkage(
        vm_name=vm_name,
        mode=None,
        status=status,
        summary=summary,
        metadata=payload,
    )


def _parse_positive_int(value: str, *, field_name: str) -> int:
    try:
        parsed = int(value.strip())
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a whole number.") from exc
    if parsed <= 0:
        raise ValueError(f"{field_name} must be greater than zero.")
    return parsed


def _command_failure_message(prefix: str, result: CommandResult) -> str:
    detail = " ".join(result.stderr.split()) if result.stderr.strip() else "No stderr output."
    return f"{prefix}: {detail}"


def _confirm_install_inside_vm(select: Callable[[str, tuple[str, ...]], str | None] | None) -> bool:
    if select is None:
        from duckln.config import _default_select_prompt

        select_prompt = _default_select_prompt
    else:
        select_prompt = select
    choice = select_prompt(
        "Duckln can work inside the VM to manage dependencies and fix errors there. Do you want to install Duckln in your VM?",
        ("Yes", "No"),
    )
    return choice == "Yes"


def _vm_post_sync_messages(vm_name: str) -> tuple[str, ...]:
    return (
        f"Duckln runtime is ready in {vm_name}.",
        "Configure provider/model inside the VM with Duckln config commands.",
        "Repo setup can continue inside the VM, but API-dependent steps should wait until credentials are configured.",
    )


def _vm_hardware_guidance(system: SystemProbe) -> tuple[str, ...]:
    if system.is_apple_silicon:
        return (
            "Apple Silicon detected. CUDA is unsupported here.",
            "Inside the VM, only CPU is available.",
            "Tip: For best performance, consider running locally with MPS.",
        )

    if system.gpu.cuda_available:
        return (
            "Host CUDA is available, but this Multipass VM has no GPU access.",
            "Expect CPU-only inside the VM unless GPU passthrough is added separately.",
        )

    if system.gpu.cuda_capable:
        return (
            "Host NVIDIA/CUDA hardware was detected, but this Multipass VM has no GPU access.",
            "Expect CPU-only inside the VM.",
        )

    return (
        "No NVIDIA/CUDA host support was detected.",
        "Expect CPU-only inside the VM.",
    )


def _default_text_prompt_adapter(prompt: str, default: str) -> str | None:
    from duckln.config import _default_text_prompt

    return _default_text_prompt(prompt, default)
