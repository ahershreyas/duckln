"""Bounded Duckln uninstall flow."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import platform
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

from duckln.config import ConfigPaths, SessionExitRequested, _default_select_prompt
from duckln.modes import ControlMode, evaluate_mode_action
from duckln.safety import assess_command


class InstallMethod(str, Enum):
    """Best-effort package install methods."""

    PIPX = "pipx"
    PIP = "pip"
    UNKNOWN = "unknown"


class UninstallTier(str, Enum):
    """Supported removal scopes."""

    APP_ONLY = "application_only"
    APP_AND_MEMORY = "application_and_memory"
    EVERYTHING = "everything"


@dataclass(frozen=True)
class UninstallResult:
    """Outcome of a bounded uninstall run."""

    completed: bool
    tier: UninstallTier | None
    install_method: InstallMethod
    package_command: str | None
    removed_paths: tuple[Path, ...]
    message: str


UNINSTALL_CHOICES: tuple[str, ...] = (
    "Duckln application only (keep memory and sessions)",
    "Duckln + all memory and session history",
    "Everything including config and stored credentials",
    "Cancel",
    "Exit",
)


def run_uninstall_flow(
    paths: ConfigPaths,
    *,
    mode: ControlMode = ControlMode.HITL,
    select: Callable[[str, tuple[str, ...]], str | None] | None = None,
    approve: Callable[[str], bool] | None = None,
    display: Callable[[str], None] = print,
) -> UninstallResult:
    """Run a bounded uninstall flow with explicit confirmation."""

    select_prompt = select or _default_select_prompt
    display("⚠ This will remove Duckln and associated data.")
    selected = select_prompt("What would you like to remove?", UNINSTALL_CHOICES)
    if selected is None or selected == "Cancel":
        display("Uninstall cancelled.")
        return UninstallResult(False, None, detect_install_method(), None, (), "Uninstall cancelled.")
    if selected == "Exit":
        raise SessionExitRequested("Exit requested from uninstall flow.")

    tier = _tier_for_choice(selected)
    install_method = detect_install_method()
    package_command = build_package_uninstall_command(install_method)
    _display_uninstall_plan(
        tier=tier,
        install_method=install_method,
        package_command=package_command,
        display=display,
    )

    confirmed = select_prompt("Continue?", ("Yes", "No", "Cancel", "Exit"))
    if confirmed is None or confirmed in {"No", "Cancel"}:
        display("Uninstall cancelled.")
        return UninstallResult(False, tier, install_method, package_command, (), "Uninstall cancelled.")
    if confirmed == "Exit":
        raise SessionExitRequested("Exit requested from uninstall confirmation.")

    removed_paths: list[Path] = []
    if package_command is not None:
        decision = evaluate_mode_action(
            mode,
            assess_command(package_command),
            is_ai_suggested=True,
            user_approved=True if approve is None else bool(approve(f"Uninstall Duckln package: {package_command}")),
        )
        if mode is ControlMode.HITL:
            display(f"Run this uninstall command manually: {package_command}")
        elif decision.allowed:
            display("Removing Duckln application package...")
            process = subprocess.run(package_command, shell=True, capture_output=True, text=True)
            if process.returncode != 0:
                message = (
                    "Duckln package removal failed, but data cleanup may still continue. "
                    f"Run: {package_command}"
                )
                display(message)
        else:
            display(f"Package uninstall not executed: {decision.reason}")
            display(f"Run this uninstall command manually: {package_command}")
    else:
        display("Install method is unknown. Remove the Duckln package manually, then rerun cleanup if needed.")

    for path in _paths_for_tier(paths, tier):
        if not path.exists():
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        removed_paths.append(path)

    message = _success_message(tier, package_command)
    display(message)
    return UninstallResult(True, tier, install_method, package_command, tuple(removed_paths), message)


def detect_install_method() -> InstallMethod:
    """Best-effort detect pipx vs pip installation."""

    if shutil.which("pipx") is not None:
        try:
            process = subprocess.run(("pipx", "list"), capture_output=True, text=True, check=False)
        except OSError:
            process = None
        if process is not None and "package duckln" in process.stdout.lower():
            return InstallMethod.PIPX
    try:
        import importlib.metadata

        importlib.metadata.version("duckln")
    except importlib.metadata.PackageNotFoundError:
        return InstallMethod.UNKNOWN
    return InstallMethod.PIP


def build_package_uninstall_command(install_method: InstallMethod) -> str | None:
    """Return the package uninstall command for this OS/install method."""

    if install_method is InstallMethod.PIPX:
        return "pipx uninstall duckln"
    if install_method is InstallMethod.PIP:
        if platform.system() == "Windows":
            return "py -m pip uninstall -y duckln"
        return f"{shlex.quote(sys.executable)} -m pip uninstall -y duckln"
    return None


def _tier_for_choice(choice: str) -> UninstallTier:
    if choice == UNINSTALL_CHOICES[0]:
        return UninstallTier.APP_ONLY
    if choice == UNINSTALL_CHOICES[1]:
        return UninstallTier.APP_AND_MEMORY
    return UninstallTier.EVERYTHING


def _paths_for_tier(paths: ConfigPaths, tier: UninstallTier) -> tuple[Path, ...]:
    if tier is UninstallTier.APP_ONLY:
        return ()
    if tier is UninstallTier.APP_AND_MEMORY:
        return (
            paths.config_dir / "memory",
            paths.config_dir / "state",
        )
    return (paths.config_dir,)


def _display_uninstall_plan(
    *,
    tier: UninstallTier,
    install_method: InstallMethod,
    package_command: str | None,
    display: Callable[[str], None],
) -> None:
    selected_label = {
        UninstallTier.APP_ONLY: "Duckln application only",
        UninstallTier.APP_AND_MEMORY: "Duckln + all memory and session history",
        UninstallTier.EVERYTHING: "Everything including config and stored credentials",
    }[tier]
    remove_lines = ["- Duckln application/package"]
    keep_lines: list[str] = []
    if tier is UninstallTier.APP_ONLY:
        keep_lines.extend(["- config", "- memory", "- sessions", "- stored provider credentials"])
    elif tier is UninstallTier.APP_AND_MEMORY:
        remove_lines.extend(["- session history", "- memory files", "- SQLite state"])
        keep_lines.extend(["- config", "- stored provider credentials"])
    else:
        remove_lines.extend(["- config", "- memory", "- sessions", "- SQLite state", "- stored provider credentials"])

    if package_command is None:
        remove_lines[0] = "- Duckln application/package (manual removal required; install method unknown)"
    display(f"You selected: {selected_label}")
    display("This will remove:")
    for line in remove_lines:
        display(line)
    display("This will keep:")
    for line in keep_lines or ["- nothing in this tier"]:
        display(line)
    if package_command is not None:
        display(f"Package command: {package_command}")
    else:
        display(f"Install method detection: {install_method.value}")


def _success_message(tier: UninstallTier, package_command: str | None) -> str:
    prefix = "Duckln package command was suggested." if package_command is not None else "Duckln package needs manual removal."
    if tier is UninstallTier.APP_ONLY:
        return f"{prefix} Memory and config were kept."
    if tier is UninstallTier.APP_AND_MEMORY:
        return f"{prefix} Local memory and session state were removed."
    return f"{prefix} Config, memory, session state, and stored credentials were removed."
