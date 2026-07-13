"""Multipass-based Ubuntu VM provisioning helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import json
import platform
import shlex
import shutil
import threading
import time
from typing import Callable

from agent.probe import SystemProbe, probe_system
from duckln.config import ConfigPaths
from duckln.diagnostics import redact_sensitive_data
from duckln.execution_trace import build_failure_message, render_tool_invocation_trace
from duckln.runtime_governance import DEFAULT_IDLE_SHUTDOWN_MINUTES, default_resource_tags
from duckln.shell import CommandResult, ControlledCommandRunner
from state.access import write_config_snapshot
from state.store import initialize_state_store, MAX_SUMMARY_LENGTH


DEFAULT_VM_NAME = "duckln-vm"
DEFAULT_VM_CPUS = 2
DEFAULT_VM_MEMORY_GB = 4
DEFAULT_VM_DISK_GB = 20
UBUNTU_IMAGE = "24.04"
VM_LAUNCH_TIMEOUT_SECONDS = 600.0   # image download + VM creation can take up to ~10 min
VM_INSTALL_TIMEOUT_SECONDS = 300.0  # apt-get update + install inside VM
_MULTIPASS_REFERENCE_URL = "https://multipass.run/"
_UBUNTU_PACKAGE_REFERENCE_URL = "https://documentation.ubuntu.com/server/how-to/software/package-management/"
_PYTHON_VENV_REFERENCE_URL = "https://docs.python.org/3/library/venv.html"


class VmSetupFailureType(str, Enum):
    """VM setup failure classifications for bounded recovery routing."""

    MULTIPASS_NOT_INSTALLED = "multipass_not_installed"
    USER_CANCELLED = "user_cancelled"
    COMMAND_TIMEOUT = "command_timeout"
    PERMISSION_DENIED = "permission_denied"
    VM_ALREADY_EXISTS = "vm_already_exists"
    NETWORK_ERROR = "network_error"
    VM_NOT_FOUND = "vm_not_found"
    DAEMON_NOT_RUNNING = "daemon_not_running"
    DISK_FULL = "disk_full"
    IMAGE_NOT_FOUND = "image_not_found"
    UNKNOWN = "unknown"


def _classify_multipass_failure(stdout: str, stderr: str, *, timed_out: bool) -> tuple[VmSetupFailureType, str]:
    """Classify a multipass launch failure and return (failure_type, recommended_action)."""

    haystack = f"{stdout or ''}\n{stderr or ''}".lower()
    if timed_out:
        return VmSetupFailureType.COMMAND_TIMEOUT, "retry_with_diagnostics"
    if "already exists" in haystack:
        return VmSetupFailureType.VM_ALREADY_EXISTS, "use_existing_vm"
    if "permission denied" in haystack or "operation not permitted" in haystack:
        return VmSetupFailureType.PERMISSION_DENIED, "check_permissions"
    if "no such file or directory" in haystack and "multipassd" in haystack:
        return VmSetupFailureType.DAEMON_NOT_RUNNING, "restart_daemon"
    if "could not connect to the multipass" in haystack or "connect: connection refused" in haystack:
        return VmSetupFailureType.DAEMON_NOT_RUNNING, "restart_daemon"
    if "no space left" in haystack or "disk full" in haystack or "out of disk" in haystack:
        return VmSetupFailureType.DISK_FULL, "free_disk_space"
    if "unable to find" in haystack and ("image" in haystack or "remote" in haystack):
        return VmSetupFailureType.IMAGE_NOT_FOUND, "check_image_or_network"
    if any(token in haystack for token in ("connection refused", "network is unreachable", "timeout", "could not resolve", "name resolution")):
        return VmSetupFailureType.NETWORK_ERROR, "retry_with_diagnostics"
    return VmSetupFailureType.UNKNOWN, "retry"


def os_specific_recovery_steps(failure_type: VmSetupFailureType, operating_system: str) -> tuple[str, ...]:
    """Concrete OS-aware steps the user can run to unstick a Multipass failure."""

    os_key = (operating_system or "").strip().lower()
    is_macos = os_key in {"darwin", "macos", "mac"}
    is_linux = os_key == "linux"
    is_windows = os_key == "windows"

    if failure_type == VmSetupFailureType.DAEMON_NOT_RUNNING:
        if is_macos:
            return (
                "The Multipass daemon is not running.",
                "Quit and reopen Multipass.app, or run: sudo launchctl kickstart -k system/com.canonical.multipassd",
            )
        if is_linux:
            return (
                "The Multipass daemon is not running.",
                "Restart the snap service: sudo snap restart multipass",
            )
        if is_windows:
            return (
                "The Multipass service is not running.",
                "Restart it from Services.msc or run (Admin PowerShell): Restart-Service Multipass",
            )
        return ("The Multipass daemon is not running. Restart Multipass before retrying.",)

    if failure_type == VmSetupFailureType.PERMISSION_DENIED:
        if is_linux:
            return (
                "Add yourself to the multipass group, then log out and back in:",
                "sudo usermod -aG sudo,multipass $USER",
            )
        if is_macos:
            return (
                "macOS may have blocked Multipass kernel/network access.",
                "Open System Settings → Privacy & Security → allow Multipass, then retry.",
            )
        if is_windows:
            return (
                "Run your terminal as Administrator and retry the VM creation.",
            )
        return ("Re-run with elevated permissions.",)

    if failure_type == VmSetupFailureType.NETWORK_ERROR:
        return (
            "Network looks unreachable from Multipass.",
            "Check internet, VPN/proxy settings, and corporate firewalls. Then run: multipass find",
        )

    if failure_type == VmSetupFailureType.IMAGE_NOT_FOUND:
        return (
            "Multipass could not find the requested Ubuntu image.",
            "Run `multipass find` to list available images, or try a different release.",
        )

    if failure_type == VmSetupFailureType.DISK_FULL:
        return (
            "Host disk is full or near full.",
            "Free at least 25 GB and retry. Existing VMs use ~20 GB each by default.",
        )

    if failure_type == VmSetupFailureType.COMMAND_TIMEOUT:
        return (
            "The multipass launch did not complete in the allowed time.",
            "Likely causes: slow image download, low memory, or stalled daemon.",
            "Try: 1) check internet speed, 2) retry, or 3) reduce VM size (CPU/memory/disk).",
        )

    return ()


@dataclass(frozen=True)
class VmConfig:
    """User-selected VM sizing and name."""

    name: str
    cpu_count: int
    memory_gb: int
    disk_gb: int


@dataclass(frozen=True)
class VmProvisionResult:
    """Outcome of provisioning a Multipass Ubuntu VM."""

    ok: bool
    vm_name: str
    message: str
    connection_commands: tuple[str, ...]
    failure_type: VmSetupFailureType | None = None
    recommended_action: str | None = None
    technical_details: str | None = None


@dataclass(frozen=True)
class VmSyncResult:
    """Outcome of the post-create Duckln VM setup flow."""

    ok: bool
    vm_name: str
    message: str
    connection_commands: tuple[str, ...]
    failure_type: VmSetupFailureType | None = None
    technical_details: str | None = None


def create_multipass_vm(
    paths: ConfigPaths,
    *,
    text_prompt: Callable[[str, str], str | None] | None = None,
    display: Callable[[str], None] = print,
    approve_prompt: Callable[[str], bool] | None = None,
    runner: ControlledCommandRunner | None = None,
    system_probe: SystemProbe | None = None,
    chat: object | None = None,
) -> VmProvisionResult:
    """Guide the user through a minimal Multipass Ubuntu VM creation flow.

    When `chat` is provided and exposes `display_step`, each phase emits an inline
    `◐ → ✓` step entry so the user sees an evolving checklist instead of free-form text.
    """

    prompt = text_prompt or _default_text_prompt_adapter
    runner_instance = runner or ControlledCommandRunner(trace=display, execution_target="local")
    system = system_probe or probe_system()

    def _step(step_id: str, label: str, *, status: str = "running", duration: float | None = None, detail: str | None = None) -> None:
        if chat is not None and hasattr(chat, "display_step"):
            try:
                chat.display_step(step_id, label, status=status, duration_seconds=duration, detail=detail)
            except Exception:
                pass

    for line in _vm_hardware_guidance(system):
        display(line)

    _step("vm.precheck", "Check Multipass is installed")
    if not is_multipass_installed():
        message = installation_guidance(system.operating_system)
        display(message)
        _step("vm.precheck", "Check Multipass is installed", status="failed", detail="not in PATH")
        _record_vm_state(paths, DEFAULT_VM_NAME, status="missing_multipass", summary=message)
        return VmProvisionResult(
            ok=False,
            vm_name=DEFAULT_VM_NAME,
            message=message,
            connection_commands=(),
            failure_type=VmSetupFailureType.MULTIPASS_NOT_INSTALLED,
            recommended_action="install_multipass",
            technical_details="multipass CLI is not in PATH",
        )
    _step("vm.precheck", "Check Multipass is installed", status="done")

    _step("vm.config", "Choose VM size (CPU / memory / disk)")
    # Plan 198 F2: use the FAILURE-AWARE probe. If `multipass list` is ERRORING (None, not just
    # empty), do NOT blindly create a VM — that's how a flaky Multipass cascaded into launching a
    # colliding `duckln-vm` ("instance already exists"). Surface the real problem + how to reset,
    # and stop, instead of guessing a name off an empty list.
    probed_names = list_multipass_vms_or_none(runner=runner_instance)
    if probed_names is None:
        message = (
            "Multipass is installed but `multipass list` is failing, so I can't safely create a VM "
            "(I might collide with one that already exists). It's usually a stuck daemon or a corrupt "
            "instance."
        )
        display(message)
        display(render_remediation_steps("multipass", system.operating_system, reason="daemon_down", next_action="run `/vm`"))
        display("If a previous VM is corrupt, remove it and retry: `multipass delete <name> --purge` (or run `/cleanup`).")
        _step("vm.config", "Choose VM size (CPU / memory / disk)", status="failed", detail="multipass list unavailable")
        return VmProvisionResult(
            ok=False, vm_name=DEFAULT_VM_NAME, message=message, connection_commands=(),
            failure_type=VmSetupFailureType.DAEMON_NOT_RUNNING, recommended_action="restart_daemon",
            technical_details="multipass list returned a non-zero/timeout result",
        )
    existing_names = probed_names
    default_name = next_available_vm_name(existing_names)
    config = prompt_vm_config(
        prompt=prompt,
        default_name=default_name,
        default_cpu_count=DEFAULT_VM_CPUS,
        default_memory_gb=DEFAULT_VM_MEMORY_GB,
        default_disk_gb=DEFAULT_VM_DISK_GB,
    )
    if config is None:
        message = "VM creation cancelled."
        display(message)
        _step("vm.config", "Choose VM size (CPU / memory / disk)", status="failed", detail="cancelled by user")
        return VmProvisionResult(
            ok=False,
            vm_name=default_name,
            message=message,
            connection_commands=(),
            failure_type=VmSetupFailureType.USER_CANCELLED,
            recommended_action="user_cancelled",
            technical_details="User cancelled VM configuration prompts",
        )
    _step("vm.config", "Choose VM size (CPU / memory / disk)", status="done", detail=f"{config.cpu_count} CPU / {config.memory_gb} GB RAM / {config.disk_gb} GB disk")

    launch_command = build_multipass_launch_command(config)
    display(_vm_create_trace(config=config, launch_command=launch_command))
    if approve_prompt is not None and not approve_prompt(_vm_create_approval_prompt(config=config, command=launch_command)):
        message = "VM creation cancelled."
        display(message)
        return VmProvisionResult(
            ok=False,
            vm_name=config.name,
            message=message,
            connection_commands=(),
            failure_type=VmSetupFailureType.USER_CANCELLED,
            recommended_action="user_cancelled",
            technical_details="User did not approve VM creation command",
        )
    # Plan 198 F2: never `multipass launch` a name that ALREADY exists — that just errors with
    # "instance already exists" and (in the pane path) the failure is missed → an infinite poll.
    # Route to use-existing instead so the caller offers "use the existing VM".
    if config.name in existing_names or vm_exists_in_multipass(config.name, runner=runner_instance):
        message = f"VM '{config.name}' already exists — Duckln will use it instead of creating a new one."
        display(message)
        _step("vm.launch", f"Launch Ubuntu {UBUNTU_IMAGE} VM '{config.name}'", status="done", detail="already exists")
        return VmProvisionResult(
            ok=False, vm_name=config.name, message=message, connection_commands=(),
            failure_type=VmSetupFailureType.VM_ALREADY_EXISTS, recommended_action="use_existing_vm",
            technical_details="target VM name already registered in multipass",
        )
    display(f"Downloading Ubuntu {UBUNTU_IMAGE} image and provisioning VM '{config.name}' — this can take several minutes…")
    _step("vm.launch", f"Launch Ubuntu {UBUNTU_IMAGE} VM '{config.name}'")
    launch_started_at = time.monotonic()

    def _stream_line(line: str) -> None:
        text = line.strip()
        if not text:
            return
        # Multipass --verbose emits progress like "Retrieving image: ...%". Surface the gist.
        display(text)

    pane_launch_used = False
    if chat is not None and hasattr(chat, "run_terminal_command"):
        try:
            pane_sent = bool(chat.run_terminal_command(command=launch_command))
        except Exception:
            pane_sent = False
        if pane_sent:
            pane_launch_used = True
            display("Multipass is running in the embedded terminal pane — Duckln will watch for the VM to come up.")
            result = _wait_for_vm_running_via_polling(
                vm_name=config.name,
                runner=runner_instance,
                display=display,
                step_callback=_step,
                step_id="vm.launch",
                step_label=f"Launch Ubuntu {UBUNTU_IMAGE} VM '{config.name}'",
                timeout_seconds=VM_LAUNCH_TIMEOUT_SECONDS,
                command_label=launch_command,
            )

    if not pane_launch_used:
        streaming_runner = getattr(runner_instance, "run_streaming", None)
        if callable(streaming_runner):
            result = streaming_runner(
                launch_command,
                timeout_seconds=VM_LAUNCH_TIMEOUT_SECONDS,
                on_line=_stream_line,
            )
        else:
            result = runner_instance.run(launch_command, timeout_seconds=VM_LAUNCH_TIMEOUT_SECONDS)
    launch_duration = time.monotonic() - launch_started_at
    if result.exit_code != 0 or result.timed_out:
        failure_type, recommended_action = _classify_multipass_failure(
            result.stdout or "",
            result.stderr or "",
            timed_out=bool(result.timed_out),
        )
        _step(
            "vm.launch",
            f"Launch Ubuntu {UBUNTU_IMAGE} VM '{config.name}'",
            status="failed",
            duration=launch_duration,
            detail=failure_type.value.replace("_", " "),
        )
        message = _command_failure_message("VM creation failed", result, command=launch_command, display=display)
        display(message)
        for guidance_line in os_specific_recovery_steps(failure_type, system.operating_system):
            display(guidance_line)
        _record_vm_state(paths, config.name, status="create_failed", summary=message, config=config)
        return VmProvisionResult(
            ok=False,
            vm_name=config.name,
            message=message,
            connection_commands=(),
            failure_type=failure_type,
            recommended_action=recommended_action,
            technical_details=f"Exit code: {result.exit_code}. Timed out: {result.timed_out}. Error: {(result.stderr or result.stdout or '')[:300]}",
        )

    invalidate_multipass_vm_cache()
    # Plan 199 F1/F6: `multipass launch` returned success → the VM is CREATED (multipass's own
    # contract). Do NOT auto-delete/recreate a freshly-launched VM on a flaky `info` (that was the
    # false-failure bug + would destroy a good VM). Best-effort: if it's merely Stopped, start it —
    # but never treat an unconfirmed state as a create FAILURE. The richer ensure-loop
    # (start/recover/daemon-restart/delete+recreate) runs when a VM is later USED/attached (F6).
    try:
        _st = get_multipass_vm_state(config.name, runner=runner_instance)
        if _st in ("Stopped", "Suspended"):
            display(f"VM '{config.name}' is {_st} — starting it…")
            runner_instance.run(f"multipass start {shlex.quote(config.name)}", timeout_seconds=180.0)
            invalidate_multipass_vm_cache()
    except Exception:
        pass
    _step(
        "vm.launch",
        f"Launch Ubuntu {UBUNTU_IMAGE} VM '{config.name}'",
        status="done",
        duration=launch_duration,
    )
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
        failure_type=None,
        recommended_action=None,
        technical_details=None,
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

    runner_instance = runner or ControlledCommandRunner(trace=display, execution_target="local")
    if vm_name not in list_multipass_vm_names(runner=runner_instance):
        message = f"Retryable error: VM {vm_name} was not found."
        display(message)
        _record_vm_state(paths, vm_name, status="missing", summary=message)
        return VmSyncResult(
            ok=False,
            vm_name=vm_name,
            message=message,
            connection_commands=(),
            failure_type=VmSetupFailureType.VM_NOT_FOUND,
            technical_details=f"VM '{vm_name}' not found in multipass list",
        )

    if not _confirm_install_inside_vm(select=select, display=display, vm_name=vm_name):
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
            failure_type=None,
            technical_details=None,
        )

    install_command = build_vm_runtime_install_command(vm_name)
    display("Installing Python runtime inside the VM — this may take a minute...")
    install_result = runner_instance.run(install_command, timeout_seconds=VM_INSTALL_TIMEOUT_SECONDS)
    if install_result.exit_code != 0 or install_result.timed_out:
        failure_type = VmSetupFailureType.UNKNOWN
        if install_result.timed_out:
            failure_type = VmSetupFailureType.COMMAND_TIMEOUT
        elif "permission denied" in (install_result.stderr or "").lower():
            failure_type = VmSetupFailureType.PERMISSION_DENIED
        
        message = _command_failure_message(
            "Duckln VM runtime install failed",
            install_result,
            command=install_command,
            display=display,
        )
        display(message)
        _record_vm_state(paths, vm_name, status="runtime_install_failed", summary=message)
        return VmSyncResult(
            ok=False,
            vm_name=vm_name,
            message=message,
            connection_commands=(),
            failure_type=failure_type,
            technical_details=f"Exit code: {install_result.exit_code}. Error: {install_result.stderr[:200] if install_result.stderr else 'None'}",
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
        failure_type=None,
        technical_details=None,
    )


def is_multipass_installed() -> bool:
    """Return whether the Multipass CLI is available."""

    return shutil.which("multipass") is not None


def installation_guidance(operating_system: str) -> str:
    """Return a concise install hint when Multipass is missing."""

    if operating_system == "Darwin":
        return (
            "Multipass is not installed. Install it with: brew install --cask multipass. "
            f"Official source: {_MULTIPASS_REFERENCE_URL}"
        )
    if operating_system == "Linux":
        return (
            "Multipass is not installed. Install it with: sudo snap install multipass. "
            f"Official source: {_MULTIPASS_REFERENCE_URL}"
        )
    if operating_system == "Windows":
        return f"Multipass is not installed. Install it from {_MULTIPASS_REFERENCE_URL}"
    return f"Multipass is not installed. Install it from {_MULTIPASS_REFERENCE_URL}"


# Plan 197 F1/F7: a required LOCAL service (Multipass / Docker) that Duckln can install itself.
_LOCAL_SERVICE_META: dict[str, tuple[str, str, str]] = {
    # service → (display name, official source URL, verify command)
    "multipass": ("Multipass", "https://multipass.run/install", "multipass version"),
    "docker": ("Docker", "https://docs.docker.com/get-docker/", "docker --version"),
}


@dataclass(frozen=True)
class LocalServiceInstallPlan:
    """One user-approved OFFICIAL install for a local service Duckln needs."""

    service: str
    display_name: str
    command: str  # official install command; "" when there's no clean auto-install (e.g. Windows)
    source_url: str
    verify_command: str
    requires_elevation: bool
    os_type: str


def build_local_service_install_plan(service: str, os_type: str) -> LocalServiceInstallPlan:
    """Plan 197 F1: the OS-appropriate OFFICIAL install command for a local service.
    `command` is "" when Duckln can't cleanly auto-install (Windows / unknown) — the caller
    then shows the F7 detailed installer walkthrough instead."""
    svc = (service or "").strip().lower()
    display_name, url, verify = _LOCAL_SERVICE_META.get(svc, (service or "the service", "", f"{svc} --version"))
    command = ""
    requires_elevation = False
    if svc == "multipass":
        if os_type == "Darwin":
            command = "brew install --cask multipass"
        elif os_type == "Linux":
            command, requires_elevation = "sudo snap install multipass", True
    elif svc == "docker":
        if os_type == "Darwin":
            command = "brew install --cask docker"
        elif os_type == "Linux":
            command, requires_elevation = "sudo snap install docker", True
    return LocalServiceInstallPlan(
        service=svc, display_name=display_name, command=command, source_url=url,
        verify_command=verify, requires_elevation=requires_elevation, os_type=os_type,
    )


def render_remediation_steps(
    service: str, os_type: str, *, reason: str = "not_installed", next_action: str = ""
) -> str:
    """Plan 197 F7: a DETAILED, numbered, copy-pasteable walkthrough (NOT a one-line hint) for
    when Duckln can't auto-complete the fix — declined, install failed, an unsupported OS, or a
    daemon that must be started by hand. Always ends with the exact next action so Duckln resumes."""
    plan = build_local_service_install_plan(service, os_type)
    name = plan.display_name
    resume = next_action or (f"run `/vm`" if plan.service == "multipass" else "ask me again")
    steps: list[str] = []
    n = 1

    if reason == "daemon_down":
        steps.append(f"{name} is installed but not running yet. Here's how to start it:")
        if plan.service == "docker" and os_type == "Darwin":
            steps.append(f"{n}. Open Docker Desktop (from Applications or Spotlight) and wait until the whale icon says \"Docker Desktop is running\".")
            n += 1
        elif plan.service == "multipass" and os_type == "Darwin":
            steps.append(f"{n}. Restart the Multipass service: sudo launchctl kickstart -k system/com.canonical.multipassd  (it may ask for your password).")
            n += 1
        elif os_type == "Linux":
            svc_name = "snap.docker.dockerd" if plan.service == "docker" else "snap.multipass.multipassd"
            steps.append(f"{n}. Start the service: sudo snap start {plan.service}  (or: sudo systemctl start {svc_name}).")
            n += 1
        else:
            steps.append(f"{n}. Start {name} (open its app / start its service), then wait a few seconds.")
            n += 1
        steps.append(f"{n}. Verify it's up: {plan.verify_command}")
        n += 1
        steps.append(f"{n}. Then {resume} and I'll continue from here.")
        return "\n".join(steps)

    steps.append(f"Here's how to get {name} working — follow these steps:")
    if plan.command:
        if os_type == "Darwin" and plan.command.startswith("brew"):
            steps.append(
                f"{n}. If you don't have Homebrew yet, install it first:\n"
                "   /bin/bash -c \"$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
            )
            n += 1
        elev = "  (it may ask for your password)" if plan.requires_elevation else ""
        steps.append(f"{n}. Install {name}:\n   {plan.command}{elev}")
        n += 1
        if plan.service == "docker" and os_type == "Darwin":
            steps.append(f"{n}. Open Docker Desktop once (Applications → Docker) so the engine starts.")
            n += 1
    else:
        steps.append(f"{n}. Download and run the official {name} installer:\n   {plan.source_url}")
        n += 1
    steps.append(f"{n}. Verify it worked (you should see a version number):\n   {plan.verify_command}")
    n += 1
    if plan.source_url:
        steps.append(f"{n}. If you get stuck, the official instructions are here:\n   {plan.source_url}")
        n += 1
    steps.append(f"{n}. Then {resume} and I'll pick it up from here.")
    return "\n".join(steps)


_VM_LIST_CACHE_TTL_SECONDS = 30.0
_VM_LIST_CACHE_LOCK = threading.Lock()
_VM_LIST_CACHE: tuple[float, tuple[str, ...]] | None = None


def invalidate_multipass_vm_cache() -> None:
    """Drop the cached `multipass list` result. Call after launch/restart/delete."""

    global _VM_LIST_CACHE
    with _VM_LIST_CACHE_LOCK:
        _VM_LIST_CACHE = None


def list_multipass_vms_or_none(*, runner: ControlledCommandRunner | None = None) -> tuple[str, ...] | None:
    """Plan 196 F4: list Multipass instance names, DISTINGUISHING a probe FAILURE from an
    empty list. Returns:
      - `None`  → the `multipass list` probe FAILED (multipass missing / daemon down /
                  timeout / unparseable) — the VM set is UNKNOWN, not "zero". Callers must
                  NOT discard a saved VM on this.
      - `()`    → multipass is reachable and there are genuinely ZERO VMs.
      - `(…)`   → the sorted instance names.
    Cached for ~30s on the default path — SUCCESSES only, so a transient failure never
    poisons the cache as "empty"."""

    global _VM_LIST_CACHE
    use_cache = runner is None
    if use_cache:
        with _VM_LIST_CACHE_LOCK:
            cached = _VM_LIST_CACHE
        if cached is not None:
            cached_at, cached_names = cached
            if time.monotonic() - cached_at < _VM_LIST_CACHE_TTL_SECONDS:
                return cached_names

    runner_instance = runner or ControlledCommandRunner()
    result = runner_instance.run("multipass list --format json")
    if result.exit_code != 0 or result.timed_out:
        return None  # probe failed — UNKNOWN, and do NOT cache a failure as empty
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        payload = {}
    info = payload.get("list", {})
    if isinstance(info, dict):
        names: tuple[str, ...] = tuple(sorted(str(name) for name in info))
    elif isinstance(info, list):
        collected: list[str] = []
        for item in info:
            if isinstance(item, dict) and "name" in item:
                collected.append(str(item["name"]))
        names = tuple(sorted(collected))
    else:
        names = ()

    if use_cache:
        with _VM_LIST_CACHE_LOCK:
            _VM_LIST_CACHE = (time.monotonic(), names)
    return names


def list_multipass_vm_names(*, runner: ControlledCommandRunner | None = None) -> tuple[str, ...]:
    """List existing Multipass instance names — `()` on a probe FAILURE OR a genuine empty
    list (backward-compatible). New callers that must distinguish a transient failure from
    'no VMs' (so they don't wipe a saved VM) should use `list_multipass_vms_or_none` (F4)."""
    names = list_multipass_vms_or_none(runner=runner)
    return names if names is not None else ()


def vm_exists_in_multipass(vm_name: str, *, runner: ControlledCommandRunner | None = None) -> bool:
    """Return True only if vm_name is currently registered in Multipass."""

    if not vm_name:
        return False
    if not is_multipass_installed():
        return False
    return vm_name in list_multipass_vm_names(runner=runner)


def get_multipass_vm_state(
    vm_name: str,
    *,
    runner: ControlledCommandRunner | None = None,
) -> str:
    """Return the Multipass state string for vm_name: 'Running', 'Stopped', 'Deleted', or 'Unknown'."""
    runner_instance = runner or ControlledCommandRunner()
    result = runner_instance.run(f"multipass info {vm_name} --format json")
    if result.exit_code != 0 or result.timed_out:
        return "Unknown"
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return "Unknown"
    info_block = payload.get("info", {})
    vm_info = info_block.get(vm_name, {})
    return str(vm_info.get("state", "Unknown"))


def _wait_for_vm_running_via_polling(
    *,
    vm_name: str,
    runner: ControlledCommandRunner,
    display: Callable[[str], None],
    step_callback: Callable[..., None],
    step_id: str,
    step_label: str,
    timeout_seconds: float,
    poll_interval_seconds: float = 3.0,
    command_label: str | None = None,
) -> CommandResult:
    """Poll `multipass info` until vm_name is Running or timeout.

    Used when `multipass launch` is dispatched into the embedded terminal pane and
    Duckln has no direct subprocess to await. Returns a CommandResult-shaped tuple
    so the existing failure-classification path keeps working.
    """

    from duckln.shell import is_global_abort_requested

    started_at = time.monotonic()
    deadline = started_at + timeout_seconds
    last_progress_announce = 0.0
    # Plan 198 F2: a persistently-BROKEN instance (multipass info keeps erroring → "Unknown")
    # must not be polled for the full timeout — bail after this many consecutive Unknowns so
    # Duckln can offer delete+recreate instead of a minutes-long runaway.
    _unknown_streak = 0
    _MAX_UNKNOWN_STREAK = 3
    while time.monotonic() < deadline:
        # Plan 198 F1: honor a user "stop"/cancel immediately — bail promptly, don't run to
        # timeout and don't re-retry. The caller treats a cancelled result as a clean stop.
        if is_global_abort_requested():
            display(f"⏹ Stopped — cancelled watching for VM '{vm_name}'.")
            return CommandResult(
                command=command_label or f"multipass launch (pane) -> {vm_name}",
                exit_code=130,
                stdout="",
                stderr=f"cancelled by user while waiting for VM '{vm_name}'",
                timed_out=False,
                duration_seconds=time.monotonic() - started_at,
            )
        invalidate_multipass_vm_cache()
        state = get_multipass_vm_state(vm_name, runner=runner)
        elapsed = time.monotonic() - started_at
        if state == "Running":
            display(f"VM '{vm_name}' is now Running.")
            return CommandResult(
                command=command_label or f"multipass launch (pane) -> {vm_name}",
                exit_code=0,
                stdout=f"VM '{vm_name}' state: Running",
                stderr="",
                timed_out=False,
                duration_seconds=elapsed,
            )
        if state in ("Stopped", "Suspended"):
            # The launch finished but the VM didn't auto-start — surface and exit.
            return CommandResult(
                command=command_label or f"multipass launch (pane) -> {vm_name}",
                exit_code=1,
                stdout=f"VM '{vm_name}' state: {state}",
                stderr=f"VM '{vm_name}' is {state}; expected Running after launch.",
                timed_out=False,
                duration_seconds=elapsed,
            )
        if state in ("Unknown", "Deleted"):
            _unknown_streak += 1
            if _unknown_streak >= _MAX_UNKNOWN_STREAK:
                # Plan 199 F1: `info` erroring ("Unknown") does NOT mean the VM failed. Confirm via
                # `multipass list` — if the instance EXISTS, the launch worked (a freshly-launched
                # VM can be briefly un-queryable by `info` on a flaky daemon). Return "exists but
                # state unconfirmed" (exit 0) so the caller's ensure-loop can start/verify it,
                # instead of falsely declaring a bad-state failure. Only bail if it's genuinely ABSENT.
                exists = False
                try:
                    exists = vm_name in list_multipass_vm_names(runner=runner)
                except Exception:
                    exists = False
                if exists:
                    display(f"VM '{vm_name}' exists but Multipass couldn't confirm its state yet — continuing.")
                    return CommandResult(
                        command=command_label or f"multipass launch (pane) -> {vm_name}",
                        exit_code=0,
                        stdout=f"VM '{vm_name}' state: present (unconfirmed)",
                        stderr="",
                        timed_out=False,
                        duration_seconds=elapsed,
                    )
                return CommandResult(
                    command=command_label or f"multipass launch (pane) -> {vm_name}",
                    exit_code=1,
                    stdout=f"VM '{vm_name}' state: {state}",
                    stderr=(
                        f"VM '{vm_name}' is not in `multipass list` and Multipass can't report on it. "
                        "It may be a corrupt instance or the daemon is down. Delete + recreate it "
                        f"(`multipass delete {vm_name} --purge` then `/vm`) or run `/cleanup`."
                    ),
                    timed_out=False,
                    duration_seconds=elapsed,
                )
        else:
            _unknown_streak = 0
        if elapsed - last_progress_announce >= 15.0:
            step_callback(step_id, step_label, status="running", duration=elapsed, detail=f"watching pane (state: {state})")
            last_progress_announce = elapsed
        time.sleep(poll_interval_seconds)

    elapsed = time.monotonic() - started_at
    return CommandResult(
        command=command_label or f"multipass launch (pane) -> {vm_name}",
        exit_code=None,
        stdout="",
        stderr=f"Duckln timed out waiting for VM '{vm_name}' to come up after {timeout_seconds:.0f}s.",
        timed_out=True,
        duration_seconds=elapsed,
    )


def ensure_multipass_vm_running(
    vm_name: str,
    *,
    display: Callable[[str], None] = print,
    runner: ControlledCommandRunner | None = None,
) -> bool:
    """Start the VM if it is stopped. Returns True if the VM is Running after this call."""
    runner_instance = runner or ControlledCommandRunner(trace=display, execution_target="local")
    state = get_multipass_vm_state(vm_name, runner=runner_instance)
    if state == "Running":
        return True
    if state in ("Unknown", "Deleted"):
        display(f"VM '{vm_name}' is not accessible (state: {state}). Verify it exists with: multipass list")
        return False
    display(f"VM '{vm_name}' is {state}. Starting it now (this takes a few seconds)...")
    start_result = runner_instance.run(
        f"multipass start {vm_name}",
        timeout_seconds=120.0,
    )
    invalidate_multipass_vm_cache()
    if start_result.exit_code != 0 or start_result.timed_out:
        display(f"Could not start VM '{vm_name}'. Run manually: multipass start {vm_name}")
        return False
    display(f"VM '{vm_name}' is now running.")
    return True


def _restart_multipass_daemon(runner: ControlledCommandRunner, *, os_type: str) -> None:
    """Plan 199 F6: restart the Multipass daemon (automatic — a broken daemon is a fixable
    software problem, not a reason to stop). macOS = launchd kickstart; Linux = snap restart."""
    os_l = (os_type or "").lower()
    if os_l == "darwin":
        runner.run("sudo launchctl kickstart -k system/com.canonical.multipassd", timeout_seconds=60.0)
    elif os_l == "linux":
        runner.run("sudo snap restart multipass", timeout_seconds=120.0)
    else:  # best-effort: try both
        runner.run("sudo snap restart multipass 2>/dev/null || sudo launchctl kickstart -k system/com.canonical.multipassd 2>/dev/null || true", timeout_seconds=120.0)


def ensure_vm_ready(
    vm_name: str,
    *,
    display: Callable[[str], None] = print,
    runner: ControlledCommandRunner | None = None,
    confirm: Callable[[str], bool] | None = None,
    recreate: Callable[[], bool] | None = None,
    os_type: str = "",
    max_cycles: int = 3,
) -> bool:
    """Plan 199 F6: DRIVE the VM to a confirmed-Running state — the auto-fix loop. Detects the
    real state and CORRECTS it automatically (start a stopped VM / recover a deleted one / restart
    a broken daemon), verifying after each step and looping until Running. On exhaustion it may
    (with `confirm`) delete+recreate via the `recreate` callback. Returns True only when Running.
    Everything except delete+recreate is automatic; the loop is bounded and honors 'stop'."""
    from duckln.shell import is_global_abort_requested

    runner_instance = runner or ControlledCommandRunner(trace=display, execution_target="local")
    os_hint = os_type or platform.system()
    daemon_restarts = 0
    for _cycle in range(max(1, max_cycles)):
        if is_global_abort_requested():
            display(f"⏹ Stopped — cancelled getting VM '{vm_name}' ready.")
            return False
        # Daemon-level: if `multipass list` itself is failing, restart the daemon and retry.
        probed = list_multipass_vms_or_none(runner=runner_instance)
        if probed is None:
            if daemon_restarts >= 2:
                display("Multipass still isn't responding after a restart — try reinstalling it "
                        "(`brew reinstall --cask multipass` / `sudo snap install multipass`), or "
                        "check the daemon; then run `/vm`.")
                return False
            daemon_restarts += 1
            display("Multipass isn't responding — restarting its service and retrying…")
            _restart_multipass_daemon(runner_instance, os_type=os_hint)
            invalidate_multipass_vm_cache()
            time.sleep(2.0)
            continue
        state = get_multipass_vm_state(vm_name, runner=runner_instance)
        if state == "Running":
            display(f"✓ VM '{vm_name}' is ready.")
            return True
        present = vm_name in probed
        if present and state in ("Stopped", "Suspended", "Unknown"):
            display(f"VM '{vm_name}' is {state} — starting it…")
            runner_instance.run(f"multipass start {shlex.quote(vm_name)}", timeout_seconds=180.0)
            invalidate_multipass_vm_cache()
            if get_multipass_vm_state(vm_name, runner=runner_instance) == "Running":
                display(f"✓ VM '{vm_name}' is ready.")
                return True
            continue
        if not present:
            display(f"VM '{vm_name}' isn't listed — attempting recovery…")
            rec = runner_instance.run(f"multipass recover {shlex.quote(vm_name)}", timeout_seconds=120.0)
            invalidate_multipass_vm_cache()
            if getattr(rec, "exit_code", 1) == 0:
                runner_instance.run(f"multipass start {shlex.quote(vm_name)}", timeout_seconds=180.0)
                if get_multipass_vm_state(vm_name, runner=runner_instance) == "Running":
                    display(f"✓ VM '{vm_name}' is ready.")
                    return True
            break  # can't recover in place → fall to delete+recreate
    # Exhausted in-place fixes → offer delete+recreate (the real reset for a corrupt instance).
    if recreate is not None:
        do_reset = True
        if confirm is not None:
            do_reset = confirm(
                f"VM '{vm_name}' won't come up (it may be corrupt). Delete it and create a fresh one? "
                "(This removes the current instance — IRREVERSIBLE.)"
            )
        if do_reset:
            display(f"Deleting the corrupt VM '{vm_name}' and recreating it…")
            runner_instance.run(f"multipass delete {shlex.quote(vm_name)} --purge", timeout_seconds=120.0)
            invalidate_multipass_vm_cache()
            if recreate():
                return ensure_vm_ready(vm_name, display=display, runner=runner_instance,
                                       confirm=confirm, recreate=None, os_type=os_hint, max_cycles=2)
    display(f"Couldn't get VM '{vm_name}' to Running. Check `multipass list` / `multipass info {vm_name}`, "
            "or run `/cleanup` to remove it and start fresh.")
    return False


def verify_vm_exec_connectivity(
    vm_name: str,
    *,
    runner: ControlledCommandRunner | None = None,
) -> bool:
    """Return True if multipass exec can reach vm_name right now."""
    runner_instance = runner or ControlledCommandRunner()
    result = runner_instance.run(
        f"multipass exec {vm_name} -- echo ok",
        timeout_seconds=20.0,
    )
    return result.exit_code == 0 and not result.timed_out


def restart_multipass_vm(
    vm_name: str,
    *,
    display: Callable[[str], None] = print,
    runner: ControlledCommandRunner | None = None,
) -> bool:
    """Restart a VM to recover a stale SSH tunnel. Returns True if the VM is reachable after restart."""
    runner_instance = runner or ControlledCommandRunner(trace=display, execution_target="local")
    display(f"Restarting VM '{vm_name}' to recover the SSH connection (this takes ~30 seconds)...")
    restart_result = runner_instance.run(
        f"multipass restart {vm_name}",
        timeout_seconds=120.0,
    )
    invalidate_multipass_vm_cache()
    if restart_result.exit_code != 0 or restart_result.timed_out:
        display(f"Could not restart VM '{vm_name}'. Run manually: multipass restart {vm_name}")
        return False
    display(f"VM '{vm_name}' restarted. Verifying exec connectivity...")
    return verify_vm_exec_connectivity(vm_name, runner=runner_instance)


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
    default_disk_gb: int,
) -> VmConfig | None:
    """Collect the guided VM configuration."""

    cpu_value = prompt("VM CPU count:", str(default_cpu_count))
    if cpu_value is None:
        return None
    memory_value = prompt("VM memory in GB:", str(default_memory_gb))
    if memory_value is None:
        return None
    disk_value = prompt("VM disk in GB:", str(default_disk_gb))
    if disk_value is None:
        return None
    name_value = prompt("VM name (leave blank for default):", default_name)
    if name_value is None:
        return None

    cpu_count = _parse_positive_int(cpu_value, field_name="CPU count")
    memory_gb = _parse_positive_int(memory_value, field_name="Memory")
    disk_gb = _parse_positive_int(disk_value, field_name="Disk")
    vm_name = (name_value.strip() or default_name).strip()
    if not vm_name:
        vm_name = default_name
    return VmConfig(name=vm_name, cpu_count=cpu_count, memory_gb=memory_gb, disk_gb=disk_gb)


def build_multipass_launch_command(config: VmConfig) -> str:
    """Build the bounded Multipass launch command (with --verbose for streaming progress)."""

    return (
        f"multipass launch {UBUNTU_IMAGE} "
        f"--name {config.name} "
        f"--cpus {config.cpu_count} "
        f"--memory {config.memory_gb}G "
        f"--disk {config.disk_gb}G "
        "--verbose"
    )


def build_vm_runtime_install_command(vm_name: str) -> str:
    """Build the bounded command that prepares the Duckln runtime in the VM."""

    return (
        f"multipass exec {vm_name} -- bash -lc "
        "\"sudo apt-get update && "
        "sudo apt-get install -y python3 python3-venv python3-pip && "
        "mkdir -p ~/.duckln ~/.duckln/projects ~/.duckln/memory ~/.duckln/memory/skills ~/.duckln/memory/knowledge ~/.duckln/memory/sessions ~/.duckln/memory/subagents && "
        "touch ~/.duckln/memory/tools.json\""
    )


def _compact_vm_summary(summary: str) -> str:
    """Plan 199 F2: collapse a summary to a COMPACT note that never exceeds MAX_SUMMARY_LENGTH,
    so recording VM state can't raise 'must stay concise' (which masked the real error)."""
    text = " ".join(str(summary or "").split())
    if not text:
        return "VM state updated."
    limit = max(40, MAX_SUMMARY_LENGTH - 1)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _record_vm_state(
    paths: ConfigPaths,
    vm_name: str,
    *,
    status: str,
    summary: str,
    config: VmConfig | None = None,
    metadata: dict | None = None,
) -> None:
    # Plan 199 F2: a state summary is a COMPACT note, never a raw-log dump. Recording a failure
    # used to pass the full multipass error (often > MAX_SUMMARY_LENGTH), which made
    # `_normalize_summary` RAISE "Vm summary must stay concise" — masking the real error AND
    # derailing the recovery loop. Compact it here (first line + a bounded tail) so it NEVER raises.
    summary = _compact_vm_summary(summary)
    payload: dict = {}
    payload.update(
        {
            "duckln_runtime_root": "~/.duckln",
            "duckln_projects_root": "~/.duckln/projects",
            "duckln_memory_root": "~/.duckln/memory",
            "host_control_root": str(paths.config_dir),
        }
    )
    if config is not None:
        payload.update(
            {
                "cpu_count": config.cpu_count,
                "memory_gb": config.memory_gb,
                "disk_gb": config.disk_gb,
            }
        )
    if metadata:
        payload.update(metadata)
    initialize_state_store(paths.config_dir).upsert_vm_linkage(
        vm_name=vm_name,
        mode=None,
        status=status,
        summary=redact_sensitive_data(summary),
        metadata=payload,
    )
    initialize_state_store(paths.config_dir).upsert_managed_resource(
        resource_key=f"vm:{vm_name}",
        resource_kind="vm",
        provider="multipass",
        display_name=vm_name,
        execution_target="vm",
        install_root="~/.duckln",
        status=status,
        idle_timeout_minutes=DEFAULT_IDLE_SHUTDOWN_MINUTES,
        last_activity_at=datetime.now(timezone.utc).isoformat(),
        metadata={
            **default_resource_tags(
                resource_name=vm_name,
                resource_kind="vm",
                execution_target="vm",
            ),
            **payload,
        },
    )
    if status in {"ready", "ready_without_duckln", "duckln_installed"}:
        write_config_snapshot(
            paths.config_dir,
            {
                "execution_target": "vm",
                "execution_vm_name": vm_name,
                "active_vm_name": vm_name,
            },
        )


def _parse_positive_int(value: str, *, field_name: str) -> int:
    try:
        parsed = int(value.strip())
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a whole number.") from exc
    if parsed <= 0:
        raise ValueError(f"{field_name} must be greater than zero.")
    return parsed


def _command_failure_message(
    prefix: str,
    result: CommandResult,
    *,
    command: str | None = None,
    display: Callable[[str], None] | None = None,
) -> str:
    return build_failure_message(prefix, result.stderr, command=command, trace=display, execution_target="local")


def _confirm_install_inside_vm(
    *,
    select: Callable[[str, tuple[str, ...]], str | None] | None,
    display: Callable[[str], None],
    vm_name: str,
) -> bool:
    if select is None:
        from duckln.config import _default_select_prompt

        select_prompt = _default_select_prompt
    else:
        select_prompt = select
    install_command = build_vm_runtime_install_command(vm_name)
    prompt_text = _vm_runtime_install_approval_prompt(vm_name=vm_name, command=install_command)
    display(prompt_text)
    choice_prompt = _vm_runtime_install_choice_prompt(vm_name=vm_name)
    choice = select_prompt(
        choice_prompt,
        (
            "Yes - install Duckln runtime in VM",
            "No - keep VM ready only",
        ),
    )
    normalized = str(choice or "").strip().casefold()
    return normalized.startswith("yes")


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


def _vm_create_trace(*, config: VmConfig, launch_command: str) -> str:
    return render_tool_invocation_trace(
        title="vm create review",
        tool_id="vm.multipass_provision",
        action="Create one bounded Ubuntu VM through Multipass",
        detail_lines=(
            f"Planned VM launch command: {launch_command}",
            f"Requested VM sizing: name={config.name}, cpus={config.cpu_count}, memory={config.memory_gb}G, disk={config.disk_gb}G",
            "Duckln will create one isolated Ubuntu VM and record the linkage in Duckln state.",
        ),
        source_urls=(_MULTIPASS_REFERENCE_URL,),
    )


def _vm_runtime_install_trace(*, vm_name: str, command: str) -> str:
    return render_tool_invocation_trace(
        title="vm runtime install review",
        tool_id="vm.runtime_configure",
        action="Prepare bounded Duckln runtime folders and Python basics inside the VM",
        detail_lines=(
            f"Target VM: {vm_name}",
            f"Planned install command: {command}",
            "VM paths Duckln will prepare: ~/.duckln, ~/.duckln/projects, ~/.duckln/memory",
            "Duckln will wait for explicit approval before it installs packages in the VM.",
        ),
        source_urls=(_MULTIPASS_REFERENCE_URL, _UBUNTU_PACKAGE_REFERENCE_URL, _PYTHON_VENV_REFERENCE_URL),
    )


def _vm_create_approval_prompt(*, config: VmConfig, command: str) -> str:
    return "\n".join(
        (
            "Duckln reviewed the VM plan and is ready to create one bounded Ubuntu VM.",
            f"VM name: {config.name}",
            f"CPU: {config.cpu_count}",
            f"Memory: {config.memory_gb} GB",
            f"Disk: {config.disk_gb} GB",
            f"Launch command: {command}",
            f"Official reference: {_MULTIPASS_REFERENCE_URL}",
            "Approve this VM plan?",
        )
    )


def _vm_runtime_install_approval_prompt(*, vm_name: str, command: str) -> str:
    trace = _vm_runtime_install_trace(vm_name=vm_name, command=command)
    return "\n".join(
        (
            trace,
            f"VM '{vm_name}' is ready.",
            "Duckln can now install Python 3 and set up its runtime folders inside the VM.",
            "This step is required before any repo setup can run inside the VM.",
            "Duckln will not install packages silently — your approval runs the install.",
            "Install Duckln runtime inside the VM?",
        )
    )


def _vm_runtime_install_choice_prompt(*, vm_name: str) -> str:
    return "\n".join(
        (
            f"VM '{vm_name}' is ready for repo setup.",
            "Approve Duckln runtime install in this VM now?",
        )
    )
