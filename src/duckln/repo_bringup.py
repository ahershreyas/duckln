"""Repo bring-up foundation for selected cached repositories."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from enum import Enum
from datetime import datetime, timezone
import json
import math
import os
import platform
from pathlib import Path
import re
import shutil
import shlex
import time
from typing import Callable

from agent.probe import GpuProbeState, SystemProbe
from duckln.agent_context import (
    AgentContextService,
    RepoKnowledgeContext,
    RepoFeasibilityAssessment,
    build_hardware_reasoning,
    build_repo_feasibility_assessment,
)
from duckln.ai_client import ProviderRequestError, generate_provider_reply
from duckln.prompts import build_repair_skill_prompt
from duckln.cloud_runtime import build_cloud_remote_exec_command, build_cloud_tunnel_command, resolve_managed_resource
from duckln.config import ConfigPaths, load_app_config
from duckln.diagnostics import redact_sensitive_data
from duckln.execution_trace import build_failure_message, render_execution_trace, render_tool_invocation_trace
from duckln.modes import ControlMode, evaluate_mode_action
from duckln.modes import ModeDecision
from duckln.planning import build_repo_plan_record, write_repo_plan
from duckln.repair_intake import (
    DependencyApprovalDecision,
    DependencyApprovalRequest,
    build_approved_dependency_command,
    build_dependency_approval_request,
    fingerprint_stderr,
    parse_install_hint,
    render_dependency_approval_lines,
    strip_synthetic_and_prompt_lines,
)
from duckln.render_blocks import file_map_block, join_blocks, paragraph_block, status_block, step_list_block
from duckln.runtime_governance import DEFAULT_IDLE_SHUTDOWN_MINUTES, default_resource_tags, describe_runtime_transport
from duckln.safety import assess_command
from duckln.shell import ControlledCommandRunner
from duckln.subagent_runtime import (
    build_subagent_runtime_profile,
    descriptor_for_specialist_name,
    select_subagent_descriptor,
)
from duckln.ui import render_status_box
from state.access import read_config_snapshot, write_session_summary_state, write_workflow_state
from state.repo_catalog import RepoCatalogRecord
from state.store import initialize_state_store


SETUP_FILE_ORDER = (
    "README.md",
    "requirements.txt",
    "pyproject.toml",
    "setup.py",
    "environment.yml",
    "package.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "package-lock.json",
    "go.mod",
    "Cargo.toml",
    "CMakeLists.txt",
    "configure",
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
    "Dockerfile",
    "Makefile",
    "manage.py",
    "main.go",
    "install.sh",
    # Plan 80 Fix 5: version-pin files so generic requirement detection indexes them.
    "setup.cfg",
    ".tool-versions",
    ".python-version",
    ".nvmrc",
    ".node-version",
    "rust-toolchain",
    "rust-toolchain.toml",
    # Plan 81 Fix 2/3: monorepo workspace markers + compose files for subdir/service detection.
    "pnpm-workspace.yaml",
    "turbo.json",
    "nx.json",
    "lerna.json",
    # Plan 87: desktop-app (Tauri/Electron) markers so archetype detection can see them.
    "tauri.conf.json",
    "src-tauri",
    ".env.example",
    ".env.sample",
)
PLAYBOOK_DIR = Path(__file__).resolve().parents[1] / "agent" / "playbooks"
# Plan 155 F1/F6: the canonical execution-target sets live in one module now.
from duckln.execution_targets import (  # noqa: E402
    REMOTE_TARGETS as _REMOTE_EXECUTION_TARGETS,
    is_remote_target as _is_remote_target,
    normalize_execution_target,
)


def _is_windows_host() -> bool:
    return platform.system() == "Windows"


def venv_create_command(*, execution_target: str, venv_path: str = ".venv") -> str:
    """Build the `create venv` command portably:

    - Remote VM / cloud (Ubuntu): `python3 -m venv` (canonical on Ubuntu where `python` is unset).
    - Local Windows: `py -m venv` (the official launcher).
    - Local macOS / Linux: `python -m venv` (works wherever the user has Python on PATH).
    """

    if _is_remote_target(execution_target):
        return f"python3 -m venv {venv_path}"
    if _is_windows_host():
        return f"py -m venv {venv_path}"
    return f"python -m venv {venv_path}"


def venv_python_path(*, execution_target: str, venv_path: str = ".venv") -> str:
    """Path to the venv's Python interpreter, OS-aware for local; always POSIX for remote."""

    if _is_remote_target(execution_target) or not _is_windows_host():
        return f"{venv_path}/bin/python"
    return f"{venv_path}\\Scripts\\python.exe"


def venv_python_test_command(*, execution_target: str, venv_path: str = ".venv") -> str:
    """Verification that the venv interpreter exists, portable across host shells."""

    python = venv_python_path(execution_target=execution_target, venv_path=venv_path)
    if _is_remote_target(execution_target) or not _is_windows_host():
        # POSIX: test -x is universally supported in bash/sh/zsh.
        return f"test -x {python}"
    # Windows cmd: `if exist <path> (exit 0) else (exit 1)`.
    return f'if exist "{python}" (exit 0) else (exit 1)'


def file_exists_check(path: str, *, execution_target: str) -> str:
    """Cross-OS file/dir existence check. POSIX hosts/VM use `test -f`; Windows uses `if exist`."""

    if _is_remote_target(execution_target) or not _is_windows_host():
        return f"test -f {path}"
    return f'if exist "{path}" (exit 0) else (exit 1)'


def dir_exists_check(path: str, *, execution_target: str) -> str:
    """Cross-OS directory existence check."""

    if _is_remote_target(execution_target) or not _is_windows_host():
        return f"test -d {path}"
    return f'if exist "{path}\\" (exit 0) else (exit 1)'


class RepoFamily(str, Enum):
    """Repo families used by the bring-up supervisor."""

    PYTHON = "python"
    CPP_NATIVE = "cpp_native"
    GO_NATIVE = "go_native"
    NODE_TYPESCRIPT = "node_typescript"
    RUST = "rust"
    AUDIO = "audio"
    DIFFUSION_HEAVY = "diffusion_heavy"
    MULTI_SERVICE = "multi_service"
    VM_ENVIRONMENT = "vm_environment"
    PROVIDER_ROUTING = "provider_routing"


class RecoveryDecision(str, Enum):
    """Bounded recovery actions returned by the Debug / Recovery specialist."""

    RETRY_SAME_SPECIALIST = "retry_same_specialist"
    REROUTE_TO_OTHER_SPECIALIST = "reroute_to_other_specialist"
    REQUEST_MISSING_PREREQUISITE = "request_missing_prerequisite"
    UNSUPPORTED_CASE = "unsupported_case"


class FailureType(str, Enum):
    """Failure classes recognized by the Debug / Recovery specialist."""

    DEPENDENCY_INSTALL_FAILURE = "dependency_install_failure"
    COMMAND_NOT_FOUND = "command_not_found"
    MISSING_COMPILER_BUILD_TOOLS = "missing_compiler_build_tools"
    PIP_VENV_MISMATCH = "pip_venv_mismatch"
    NODE_NPM_MISMATCH = "node_npm_mismatch"
    SERVICE_NOT_RUNNING = "service_not_running"
    PORT_CONFLICT = "port_conflict"
    MODEL_RUNTIME_MISSING = "model_runtime_missing"
    GPU_CUDA_MISMATCH = "gpu_cuda_mismatch"
    UNSUPPORTED_PLATFORM = "unsupported_platform"
    MIXED_STACK_SETUP_CONFLICTS = "mixed_stack_setup_conflicts"
    MISSING_PYTHON_MODULE = "missing_python_module"
    NETWORK_DOWNLOAD_FAILURE = "network_download_failure"
    PERMISSION_DENIED = "permission_denied"
    ENTRYPOINT_FAILURE = "entrypoint_failure"
    FILE_NOT_FOUND = "file_not_found"


@dataclass(frozen=True)
class RepoBringUpStep:
    """A single inferred repo bring-up step."""

    purpose: str
    command: str
    verification_command: str | None = None
    source: str = "inferred"


@dataclass(frozen=True)
class RepoBringUpPlan:
    """Detected files and the bounded inferred bring-up path."""

    repo: RepoCatalogRecord
    project_dir: Path
    detected_files: tuple[str, ...]
    steps: tuple[RepoBringUpStep, ...]
    summary: str
    repo_family: RepoFamily = RepoFamily.PYTHON
    specialist_name: str = "python"
    playbook_path: Path | None = None
    runtime_provider: str | None = None
    execution_target: str = "local"
    subagent_prompt: str | None = None
    subagent_workspace_files: tuple[str, ...] = ()


@dataclass(frozen=True)
class RepoBringUpResult:
    """Outcome of presenting or executing a bring-up plan."""

    repo: RepoCatalogRecord
    project_dir: Path
    detected_files: tuple[str, ...]
    mode: ControlMode
    executed_commands: tuple[str, ...]
    verification_passed: bool
    message: str
    repo_family: RepoFamily = RepoFamily.PYTHON
    specialist_name: str = "python"
    recovery_decision: RecoveryDecision | None = None
    failure_type: FailureType | None = None
    setup_outcome: RepoSetupOutcome | None = None
    should_offer_repair: bool = True
    message_already_displayed: bool = False


@dataclass(frozen=True)
class RepoPreflightAssessment:
    """Supervisor-owned repo fit assessment before setup begins."""

    repo: RepoCatalogRecord
    fit_status: str
    rationale: str
    recommended_action: str
    summary: str
    feasibility: RepoFeasibilityAssessment
    local_vm_recommendation: str | None = None


@dataclass(frozen=True)
class RepoVerificationOutcome:
    """Bounded verification result reported back to the supervisor."""

    verified: bool
    summary: str
    checks_run: tuple[str, ...]
    verification_command: str | None = None
    next_step: str | None = None
    confidence: str = "medium"


@dataclass(frozen=True)
class RepoSetupOutcome:
    """Useful setup result facts persisted for follow-up answers."""

    install_location: str
    environment_path: str | None
    run_command: str | None
    verify_command: str | None
    manual_command: str | None
    runtime_kind: str | None
    stack_family: str | None
    auth_requirements: tuple[dict[str, object], ...]
    missing_auth_variables: tuple[str, ...]
    access_hint: str | None
    verification: RepoVerificationOutcome
    removal_hint: str
    changed_items: tuple[str, ...]


@dataclass(frozen=True)
class RepoRuntimeHints:
    """Derived runtime metadata for an already-present repo path."""

    detected_files: tuple[str, ...]
    run_command: str | None
    verify_command: str | None
    manual_command: str | None
    runtime_kind: str | None
    stack_family: str | None
    auth_requirements: tuple[dict[str, object], ...]
    missing_auth_variables: tuple[str, ...]
    access_hint: str | None
    removal_hint: str


@dataclass(frozen=True)
class RepoBringUpInspection:
    """Repo metadata, setup files, and local environment signals."""

    repo: RepoCatalogRecord
    project_dir: Path
    detected_files: tuple[str, ...]
    readme_excerpt: str
    system_probe: SystemProbe
    repo_knowledge: RepoKnowledgeContext | None = None
    runtime_provider: str | None = None
    execution_target: str = "local"
    prior_skill_hint: str | None = None


@dataclass(frozen=True)
class RequirementsSpec:
    """Plan 87: a structured, grounded understanding of EVERYTHING a repo needs to
    run — built from the whole README + manifests BEFORE any recommendation. The
    single source the rest of the plan consumes (run command, services, env keys)."""

    archetype: str = "unknown"               # web | desktop_gui | cli | service | library
    archetype_flavor: str = ""               # tauri | electron | "" (desktop only)
    runtimes: tuple[tuple[str, str], ...] = ()   # (name, version) e.g. ("node","20")
    services: tuple[tuple[str, str], ...] = ()   # (engine, version) e.g. ("postgres","15")
    env_keys: tuple[str, ...] = ()
    needs_migrations: bool = False
    notes: tuple[str, ...] = ()              # low-confidence / manual items, surfaced not dropped
    source: str = "deterministic"            # "deterministic" | "llm+grounded"

    def summary_line(self) -> str:
        """A one-line 'Understood: …' recap for the plan context + thinking box."""
        parts: list[str] = []
        kind = self.archetype + (f"/{self.archetype_flavor}" if self.archetype_flavor else "")
        parts.append(f"stack {kind}")
        if self.runtimes:
            parts.append("runtimes " + ", ".join(f"{n} {v}".strip() for n, v in self.runtimes))
        if self.services:
            parts.append("services " + ", ".join(f"{e} {v}".strip() for e, v in self.services))
        if self.env_keys:
            parts.append("needs " + ", ".join(self.env_keys))
        return "Understood: " + "; ".join(parts) + "."


@dataclass(frozen=True)
class DebugRecoveryAssessment:
    """A bounded recovery decision with concise generalized memory text."""

    failure_type: FailureType
    decision: RecoveryDecision
    summary: str
    reroute_repo_family: RepoFamily | None = None
    playbook_path: Path = PLAYBOOK_DIR / "debug_recovery.md"


class RepoSetupSpecialist(ABC):
    """Bounded planner for one setup family."""

    specialist_name: str
    playbook_file_name: str

    def build_plan(
        self,
        inspection: RepoBringUpInspection,
        repo_family: RepoFamily,
        *,
        config_dir: Path | None = None,
    ) -> RepoBringUpPlan:
        readme_steps = infer_readme_workflow_steps(inspection, config_dir=config_dir)
        steps = readme_steps or self.infer_steps(inspection, repo_family)
        # Plan 57 Phase 2: prerequisite preflight. Read the README's declared system
        # tools (Node, Python, Rust, uv, etc.) and probe+install each one BEFORE the
        # README-derived install commands run. This prevents the "npm install fails
        # because node isn't there" reactive loop on any repo with a Requirements section.
        preflight_steps = _build_readme_prereq_preflight_steps(
            inspection=inspection, execution_target=inspection.execution_target
        )
        steps = list(preflight_steps) + list(steps)
        playbook_path = PLAYBOOK_DIR / self.playbook_file_name
        _validate_steps_against_playbook(steps, playbook_path)
        return RepoBringUpPlan(
            repo=inspection.repo,
            project_dir=inspection.project_dir,
            detected_files=inspection.detected_files,
            steps=tuple(steps),
            summary=_summarize_specialist_plan(
                detected_files=inspection.detected_files,
                steps=steps,
                repo_family=repo_family,
                specialist_name=self.specialist_name,
            ),
            repo_family=repo_family,
            specialist_name=self.specialist_name,
            playbook_path=playbook_path,
            runtime_provider=inspection.runtime_provider,
            execution_target=inspection.execution_target,
        )

    @abstractmethod
    def infer_steps(self, inspection: RepoBringUpInspection, repo_family: RepoFamily) -> list[RepoBringUpStep]:
        """Infer bounded setup steps."""


class PythonRepoSetupSpecialist(RepoSetupSpecialist):
    """Python specialist with venv-first setup."""

    specialist_name = "python"
    playbook_file_name = "python.md"

    def infer_steps(self, inspection: RepoBringUpInspection, repo_family: RepoFamily) -> list[RepoBringUpStep]:
        detected_files = inspection.detected_files
        target = inspection.execution_target
        venv_create = venv_create_command(execution_target=target)
        venv_python = venv_python_path(execution_target=target)
        venv_check = venv_python_test_command(execution_target=target)
        if "requirements.txt" in detected_files:
            return [
                RepoBringUpStep(
                    purpose="Create project virtualenv",
                    command=venv_create,
                    verification_command=venv_check,
                ),
                RepoBringUpStep(
                    purpose="Install requirements",
                    command=f"{venv_python} -m pip install -r requirements.txt",
                    verification_command=f"{venv_python} -m pip --version",
                ),
            ]
        if "pyproject.toml" in detected_files or "setup.py" in detected_files:
            return [
                RepoBringUpStep(
                    purpose="Create project virtualenv",
                    command=venv_create,
                    verification_command=venv_check,
                ),
                RepoBringUpStep(
                    purpose="Install editable package",
                    command=f"{venv_python} -m pip install -e .",
                    verification_command=f"{venv_python} -m pip --version",
                ),
            ]
        if "environment.yml" in detected_files:
            return [
                RepoBringUpStep(
                    purpose="Create conda environment",
                    command="conda env create -f environment.yml",
                    verification_command="test -f environment.yml",
                )
            ]
        if "Dockerfile" in detected_files and repo_family in {RepoFamily.DIFFUSION_HEAVY, RepoFamily.MULTI_SERVICE}:
            return [
                RepoBringUpStep(
                    purpose="Build container image",
                    command=f"docker build -t {_slugify(inspection.repo.name)} .",
                    verification_command="test -f Dockerfile",
                )
            ]
        return _infer_make_steps(inspection.project_dir, detected_files)


class CppNativeRepoSetupSpecialist(RepoSetupSpecialist):
    """C++/native specialist with configure/build-first setup."""

    specialist_name = "cpp-native"
    playbook_file_name = "cpp_native.md"

    def infer_steps(self, inspection: RepoBringUpInspection, repo_family: RepoFamily) -> list[RepoBringUpStep]:
        detected_files = inspection.detected_files
        target = inspection.execution_target
        build_check = dir_exists_check("build", execution_target=target)
        if "CMakeLists.txt" in detected_files:
            return [
                RepoBringUpStep(
                    purpose="Configure native build",
                    command="cmake -S . -B build",
                    verification_command=build_check,
                ),
                RepoBringUpStep(
                    purpose="Build native target",
                    command="cmake --build build",
                    verification_command=build_check,
                ),
            ]
        if "Cargo.toml" in detected_files:
            return [
                RepoBringUpStep(
                    purpose="Build Rust/native crate",
                    command="cargo build",
                    verification_command=dir_exists_check("target", execution_target=target),
                )
            ]
        if "configure" in detected_files:
            return [
                RepoBringUpStep(
                    purpose="Configure native source tree",
                    command="./configure",
                    verification_command=file_exists_check("Makefile", execution_target=target),
                ),
                *_infer_make_steps(inspection.project_dir, detected_files),
            ]
        return _infer_make_steps(inspection.project_dir, detected_files)


class GoNativeRepoSetupSpecialist(RepoSetupSpecialist):
    """Go specialist for repos that rely on Go modules and binaries."""

    specialist_name = "go-native"
    playbook_file_name = "go_native.md"

    def infer_steps(self, inspection: RepoBringUpInspection, repo_family: RepoFamily) -> list[RepoBringUpStep]:
        detected_files = inspection.detected_files
        target = inspection.execution_target
        if "go.mod" in detected_files or "main.go" in detected_files:
            return [
                RepoBringUpStep(
                    purpose="Resolve Go module dependencies",
                    command="go mod download",
                    verification_command=file_exists_check("go.mod", execution_target=target),
                ),
                RepoBringUpStep(
                    purpose="Verify Go package graph",
                    command="go list ./...",
                    verification_command="go list ./...",
                ),
            ]
        return _infer_make_steps(inspection.project_dir, detected_files)


class NodeTypescriptRepoSetupSpecialist(RepoSetupSpecialist):
    """Node/TypeScript specialist with package-manager-first setup."""

    specialist_name = "node-typescript"
    playbook_file_name = "node_typescript.md"

    def infer_steps(self, inspection: RepoBringUpInspection, repo_family: RepoFamily) -> list[RepoBringUpStep]:
        detected_files = inspection.detected_files
        target = inspection.execution_target
        if "package.json" in detected_files:
            install_command, lockfile = _node_install_command(detected_files)
            verify_path = lockfile or "package.json"
            steps = [
                RepoBringUpStep(
                    purpose="Install Node dependencies",
                    command=install_command,
                    verification_command=file_exists_check(verify_path, execution_target=target),
                )
            ]
            # Plan 183 F8/F9(a): a MONOREPO installs EVERY sub-package, not just the root — the
            # JustHireMe `frontend/` vite devDeps weren't installed because only the root install
            # ran (→ the `ERR_MODULE_NOT_FOUND` build failure). Add a per-subdir devDeps install
            # before any build, so the basics are correct for any monorepo/polyglot repo.
            steps.extend(_subdir_node_install_steps(inspection.project_dir, detected_files, target))
            if repo_family is RepoFamily.MULTI_SERVICE:
                compose_file = "docker-compose.yml" if "docker-compose.yml" in detected_files else "docker-compose.yaml"
                if compose_file in detected_files:
                    compose_check = file_exists_check(compose_file, execution_target=target)
                    steps.append(
                        RepoBringUpStep(
                            purpose="Verify compose definition",
                            command=compose_check,
                            verification_command=compose_check,
                        )
                    )
            return steps
        if "Dockerfile" in detected_files:
            return [
                RepoBringUpStep(
                    purpose="Build container image",
                    command=f"docker build -t {_slugify(inspection.repo.name)} .",
                    verification_command=file_exists_check("Dockerfile", execution_target=target),
                )
            ]
        return _infer_make_steps(inspection.project_dir, detected_files)


class RustRepoSetupSpecialist(RepoSetupSpecialist):
    """Rust specialist for Cargo-based crates and workspaces."""

    specialist_name = "rust"
    playbook_file_name = "rust.md"

    def infer_steps(self, inspection: RepoBringUpInspection, repo_family: RepoFamily) -> list[RepoBringUpStep]:
        detected_files = inspection.detected_files
        target = inspection.execution_target
        if "Cargo.toml" not in detected_files:
            return []
        steps: list[RepoBringUpStep] = [
            RepoBringUpStep(
                purpose="Verify Rust toolchain (cargo)",
                command="cargo --version",
                verification_command="cargo --version",
            ),
        ]
        # `rust-toolchain.toml` overrides the active toolchain — surface it so the
        # user sees the same channel the repo expects.
        if "rust-toolchain.toml" in detected_files or "rust-toolchain" in detected_files:
            steps.append(
                RepoBringUpStep(
                    purpose="Confirm pinned Rust toolchain",
                    command="rustup show active-toolchain",
                    verification_command="rustup show active-toolchain",
                )
            )
        steps.append(
            RepoBringUpStep(
                purpose="Fetch Cargo dependencies",
                command="cargo fetch",
                verification_command=file_exists_check("Cargo.toml", execution_target=target),
            )
        )
        steps.append(
            RepoBringUpStep(
                purpose="Build crate (release)",
                command="cargo build --release",
                verification_command=dir_exists_check("target", execution_target=target),
            )
        )
        return steps


class AudioRepoSetupSpecialist(PythonRepoSetupSpecialist):
    """Audio specialist for Whisper, Bark, SpeechBrain, and Coqui-style repos."""

    specialist_name = "audio"
    playbook_file_name = "audio.md"

    def infer_steps(self, inspection: RepoBringUpInspection, repo_family: RepoFamily) -> list[RepoBringUpStep]:
        steps = super().infer_steps(inspection, repo_family)
        if steps:
            return steps
        if "Dockerfile" in inspection.detected_files:
            return [
                RepoBringUpStep(
                    purpose="Build audio container image",
                    command=f"docker build -t {_slugify(inspection.repo.name)} .",
                    verification_command="test -f Dockerfile",
                )
            ]
        return []


class DiffusionRepoSetupSpecialist(PythonRepoSetupSpecialist):
    """Diffusion-heavy specialist for Stable Diffusion, ComfyUI, Diffusers, and InvokeAI."""

    specialist_name = "diffusion-heavy"
    playbook_file_name = "diffusion.md"

    def infer_steps(self, inspection: RepoBringUpInspection, repo_family: RepoFamily) -> list[RepoBringUpStep]:
        steps = super().infer_steps(inspection, repo_family)
        if steps:
            return steps
        if "Dockerfile" in inspection.detected_files:
            return [
                RepoBringUpStep(
                    purpose="Build diffusion container image",
                    command=f"docker build -t {_slugify(inspection.repo.name)} .",
                    verification_command="test -f Dockerfile",
                )
            ]
        return []


class VmEnvironmentRepoSetupSpecialist(RepoSetupSpecialist):
    """VM/environment specialist for local-vs-VM guidance and VM-safe setup constraints."""

    specialist_name = "vm-environment"
    playbook_file_name = "vm.md"

    def infer_steps(self, inspection: RepoBringUpInspection, repo_family: RepoFamily) -> list[RepoBringUpStep]:
        if inspection.execution_target != "vm":
            return []
        steps: list[RepoBringUpStep] = []
        # When install.sh is present, run it natively — VM is its own sandbox, no Docker needed.
        if "install.sh" in inspection.detected_files:
            bin_name = _npm_global_binary_name(inspection.project_dir) or inspection.repo.name.lower()
            steps.append(
                RepoBringUpStep(
                    purpose="Run install script (native VM install)",
                    command="bash install.sh",
                    verification_command=f"command -v {bin_name} 2>/dev/null && echo installed || echo 'install complete'",
                )
            )
        elif "Dockerfile" in inspection.detected_files:
            steps.append(
                RepoBringUpStep(
                    purpose="Verify VM container recipe",
                    command="test -f Dockerfile",
                    verification_command="test -f Dockerfile",
                )
            )
        return steps


class ProviderRoutingRepoSetupSpecialist(RepoSetupSpecialist):
    """Provider-routing specialist for cloud provider hints and local Ollama detection."""

    specialist_name = "provider-routing"
    playbook_file_name = "provider.md"

    def infer_steps(self, inspection: RepoBringUpInspection, repo_family: RepoFamily) -> list[RepoBringUpStep]:
        provider_text = " ".join(
            value
            for value in (
                inspection.runtime_provider or "",
                inspection.repo.name,
                inspection.repo.description,
                inspection.repo.framework,
                inspection.readme_excerpt,
            )
            if value
        ).lower()
        if "ollama" in provider_text:
            return [
                RepoBringUpStep(
                    purpose="Detect local Ollama runtime",
                    command="which ollama",
                    verification_command="which ollama" if shutil.which("ollama") else None,
                )
            ]
        return []


class DebugRecoverySpecialist(RepoSetupSpecialist):
    """Debug / Recovery specialist for failed setup attempts and low-confidence routing."""

    specialist_name = "debug-recovery"
    playbook_file_name = "debug_recovery.md"

    def infer_steps(self, inspection: RepoBringUpInspection, repo_family: RepoFamily) -> list[RepoBringUpStep]:
        return []

    def assess_failure(
        self,
        *,
        inspection: RepoBringUpInspection,
        repo_family: RepoFamily,
        specialist_name: str,
        attempted_steps: tuple[RepoBringUpStep, ...],
        failed_command: str,
        verification_failure: str,
        error_output: str,
        low_confidence: bool = False,
    ) -> DebugRecoveryAssessment:
        failure_type = self._classify_failure(
            inspection=inspection,
            repo_family=repo_family,
            failed_command=failed_command,
            verification_failure=verification_failure,
            error_output=error_output,
            low_confidence=low_confidence,
        )
        decision, reroute_repo_family, action = self._choose_decision(
            inspection=inspection,
            repo_family=repo_family,
            specialist_name=specialist_name,
            attempted_steps=attempted_steps,
            failure_type=failure_type,
            low_confidence=low_confidence,
        )
        summary = (
            "Debug/Recovery classified "
            f"{failure_type.value} after {specialist_name} on {repo_family.value}; "
            f"decision={decision.value}; {action}"
        )
        return DebugRecoveryAssessment(
            failure_type=failure_type,
            decision=decision,
            summary=summary,
            reroute_repo_family=reroute_repo_family,
        )

    def _classify_failure(
        self,
        *,
        inspection: RepoBringUpInspection,
        repo_family: RepoFamily,
        failed_command: str,
        verification_failure: str,
        error_output: str,
        low_confidence: bool,
    ) -> FailureType:
        # Defense-in-depth: if any caller forgets and passes a formatted Duckln summary
        # as error_output, scrub the narrative lines before substring-matching tokens
        # like "rust"/"cargo"/"go.mod" that would mislead the classifier.
        cleaned_error_output = strip_synthetic_and_prompt_lines(error_output or "")
        error_text = " ".join(
            value
            for value in (
                failed_command,
                verification_failure,
                cleaned_error_output,
            )
            if value
        ).lower()
        repo_text = " ".join(
            value
            for value in (
                inspection.repo.name,
                inspection.repo.description,
                inspection.repo.framework,
                inspection.readme_excerpt,
            )
            if value
        ).lower()
        detected_files = set(inspection.detected_files)

        if low_confidence or (
            repo_family is RepoFamily.PYTHON
            and "package.json" in detected_files
            and detected_files & {"requirements.txt", "pyproject.toml", "setup.py"}
        ):
            return FailureType.MIXED_STACK_SETUP_CONFLICTS
        if any(signal in error_text for signal in ("cuda", "torch not compiled with cuda", "no cuda-capable gpu", "mps")):
            return FailureType.GPU_CUDA_MISMATCH
        if any(signal in error_text for signal in ("unsupported platform", "not supported on this platform", "darwin arm64", "win32")):
            return FailureType.UNSUPPORTED_PLATFORM
        if any(signal in error_text for signal in ("address already in use", "port is already allocated", "eaddrinuse")):
            return FailureType.PORT_CONFLICT
        if any(
            signal in error_text
            for signal in (
                "permission denied",
                "operation not permitted",
                "eacces",
                "errno 13",
                "errno -13",
                "npm error code eacces",
                "try running the command again as root/administrator",
            )
        ):
            return FailureType.PERMISSION_DENIED
        if any(
            signal in error_text
            for signal in (
                "temporary failure in name resolution",
                "network is unreachable",
                "connection timed out",
                "failed to establish a new connection",
                "could not resolve host",
                "certificate verify failed",
                "read timed out",
            )
        ):
            return FailureType.NETWORK_DOWNLOAD_FAILURE
        if any(signal in error_text for signal in ("connection refused", "failed to connect", "server is not running", "service unavailable")):
            return FailureType.SERVICE_NOT_RUNNING
        if any(signal in error_text for signal in ("ollama", "model not found", "no such model", "runtime missing")) or "ollama" in repo_text:
            return FailureType.MODEL_RUNTIME_MISSING
        if any(signal in error_text for signal in ("modulenotfounderror", "no module named")):
            return FailureType.MISSING_PYTHON_MODULE
        if any(
            signal in error_text
            for signal in (
                "npm: command not found",
                "pnpm: command not found",
                "yarn: command not found",
                "node: command not found",
                "ebadengine",
                "unsupported engine",
                "requires node",
                "required: { node",
                "node >= 20",
                "node: '>=20",
            )
        ):
            return FailureType.NODE_NPM_MISMATCH
        if any(
            signal in error_text
            for signal in (
                "python: command not found",
                "pip: command not found",
                "no module named pip",
                "bad interpreter",
                "no such file or directory: '.venv/bin/python'",
                ".venv/bin/python: no such file or directory",
            )
        ):
            return FailureType.PIP_VENV_MISMATCH
        if any(signal in error_text for signal in ("cmake: command not found", "make: command not found", "gcc: command not found", "g++: command not found", "c++: command not found", "cargo: command not found")):
            return FailureType.MISSING_COMPILER_BUILD_TOOLS
        if any(
            signal in error_text
            for signal in (
                "no such file or directory",
                "file not found",
                "cannot find the file",
            )
        ):
            return FailureType.FILE_NOT_FOUND
        if "command not found" in error_text or "not recognized as an internal or external command" in error_text:
            return FailureType.COMMAND_NOT_FOUND
        if any(signal in error_text for signal in ("could not find a version", "no matching distribution", "failed building wheel", "dependency resolution", "npm err!", "npm error", "pnpm err!", "yarn error")):
            return FailureType.DEPENDENCY_INSTALL_FAILURE
        if any(
            signal in error_text
            for signal in (
                "usage:",
                "unrecognized arguments",
                "invalid choice",
                "no such option",
                "failed to start",
                "traceback",
            )
        ):
            return FailureType.ENTRYPOINT_FAILURE
        return FailureType.DEPENDENCY_INSTALL_FAILURE

    def _choose_decision(
        self,
        *,
        inspection: RepoBringUpInspection,
        repo_family: RepoFamily,
        specialist_name: str,
        attempted_steps: tuple[RepoBringUpStep, ...],
        failure_type: FailureType,
        low_confidence: bool,
    ) -> tuple[RecoveryDecision, RepoFamily | None, str]:
        reroute_repo_family = _suggest_alternate_repo_family(inspection, current_family=repo_family)

        if failure_type in {
            FailureType.MISSING_COMPILER_BUILD_TOOLS,
            FailureType.NODE_NPM_MISMATCH,
            FailureType.SERVICE_NOT_RUNNING,
            FailureType.PORT_CONFLICT,
            FailureType.MODEL_RUNTIME_MISSING,
            FailureType.PERMISSION_DENIED,
            FailureType.PIP_VENV_MISMATCH,
        }:
            return (
                RecoveryDecision.REQUEST_MISSING_PREREQUISITE,
                None,
                "ask the user to install/start the missing prerequisite before retrying.",
            )
        if failure_type in {FailureType.GPU_CUDA_MISMATCH, FailureType.UNSUPPORTED_PLATFORM}:
            return (
                RecoveryDecision.UNSUPPORTED_CASE,
                None,
                "stop this bring-up path and explain the platform/runtime blocker.",
            )
        if low_confidence or failure_type is FailureType.MIXED_STACK_SETUP_CONFLICTS:
            if reroute_repo_family is not None and reroute_repo_family is not repo_family:
                return (
                    RecoveryDecision.REROUTE_TO_OTHER_SPECIALIST,
                    reroute_repo_family,
                    f"reroute to {reroute_repo_family.value} specialist and re-plan once.",
                )
            return (
                RecoveryDecision.UNSUPPORTED_CASE,
                None,
                "no strong setup signal was found; ask for a documented entrypoint.",
            )
        if failure_type in {
            FailureType.DEPENDENCY_INSTALL_FAILURE,
            FailureType.PIP_VENV_MISMATCH,
            FailureType.COMMAND_NOT_FOUND,
            FailureType.MISSING_PYTHON_MODULE,
            FailureType.NETWORK_DOWNLOAD_FAILURE,
            FailureType.FILE_NOT_FOUND,
            FailureType.ENTRYPOINT_FAILURE,
        } and attempted_steps:
            return (
                RecoveryDecision.RETRY_SAME_SPECIALIST,
                None,
                f"retry {specialist_name} with the smallest corrected setup step and verify again.",
            )
        return (
            RecoveryDecision.UNSUPPORTED_CASE,
            None,
            "stop and surface the smallest unsupported blocker.",
        )


class RepoBringUpSupervisor:
    """Inspect repo and environment signals, then route to a bounded specialist."""

    def __init__(self, *, context_service: AgentContextService | None = None) -> None:
        self._context_service = context_service or AgentContextService()
        self._python_specialist = PythonRepoSetupSpecialist()
        self._cpp_specialist = CppNativeRepoSetupSpecialist()
        self._go_specialist = GoNativeRepoSetupSpecialist()
        self._node_specialist = NodeTypescriptRepoSetupSpecialist()
        self._rust_specialist = RustRepoSetupSpecialist()
        self._audio_specialist = AudioRepoSetupSpecialist()
        self._diffusion_specialist = DiffusionRepoSetupSpecialist()
        self._vm_specialist = VmEnvironmentRepoSetupSpecialist()
        self._provider_specialist = ProviderRoutingRepoSetupSpecialist()
        self._debug_recovery_specialist = DebugRecoverySpecialist()
        self._specialists_by_name = {
            self._python_specialist.specialist_name: self._python_specialist,
            self._cpp_specialist.specialist_name: self._cpp_specialist,
            self._go_specialist.specialist_name: self._go_specialist,
            self._node_specialist.specialist_name: self._node_specialist,
            self._rust_specialist.specialist_name: self._rust_specialist,
            self._audio_specialist.specialist_name: self._audio_specialist,
            self._diffusion_specialist.specialist_name: self._diffusion_specialist,
            self._vm_specialist.specialist_name: self._vm_specialist,
            self._provider_specialist.specialist_name: self._provider_specialist,
            self._debug_recovery_specialist.specialist_name: self._debug_recovery_specialist,
        }

    def plan_repo_bringup(
        self,
        repo: RepoCatalogRecord,
        project_dir: Path,
        *,
        system_probe: SystemProbe | None = None,
        runtime_provider: str | None = None,
        execution_target: str = "local",
        config_dir: Path | None = None,
        detected_files_override: tuple[str, ...] | None = None,
    ) -> RepoBringUpPlan:
        inspection = inspect_repo_for_bringup(
            repo,
            project_dir,
            system_probe=system_probe,
            runtime_provider=runtime_provider,
            execution_target=execution_target,
            config_dir=config_dir,
            context_service=self._context_service,
            detected_files_override=detected_files_override,
        )
        repo_family = classify_repo_family(inspection)
        if _is_low_confidence_repo_family(inspection, repo_family):
            recovery = self._debug_recovery_specialist.assess_failure(
                inspection=inspection,
                repo_family=repo_family,
                specialist_name="supervisor",
                attempted_steps=(),
                failed_command="",
                verification_failure="Low-confidence repo-family classification.",
                error_output="No strong setup files or stack signals were detected.",
                low_confidence=True,
            )
            if (
                recovery.decision is RecoveryDecision.REROUTE_TO_OTHER_SPECIALIST
                and recovery.reroute_repo_family is not None
            ):
                specialist = self._select_specialist(inspection, recovery.reroute_repo_family)
                rerouted_plan = specialist.build_plan(inspection, recovery.reroute_repo_family, config_dir=config_dir)
                return _attach_subagent_runtime_profile(
                    _attach_recovery_summary(rerouted_plan, recovery),
                    config_dir=config_dir,
                )
            return _attach_subagent_runtime_profile(
                RepoBringUpPlan(
                repo=inspection.repo,
                project_dir=inspection.project_dir,
                detected_files=inspection.detected_files,
                steps=(),
                summary=(
                    "Supervisor escalated low-confidence classification to debug-recovery specialist. "
                    f"{recovery.summary}"
                ),
                repo_family=repo_family,
                specialist_name=self._debug_recovery_specialist.specialist_name,
                playbook_path=PLAYBOOK_DIR / self._debug_recovery_specialist.playbook_file_name,
                runtime_provider=inspection.runtime_provider,
                execution_target=inspection.execution_target,
                ),
                config_dir=config_dir,
            )
        specialist = self._select_specialist(inspection, repo_family)
        return _attach_subagent_runtime_profile(
            specialist.build_plan(inspection, repo_family, config_dir=config_dir),
            config_dir=config_dir,
        )

    def assess_failed_bringup(
        self,
        *,
        plan: RepoBringUpPlan,
        failed_command: str,
        verification_failure: str,
        error_output: str,
        system_probe: SystemProbe | None = None,
        config_dir: Path | None = None,
    ) -> DebugRecoveryAssessment:
        inspection = inspect_repo_for_bringup(
            plan.repo,
            plan.project_dir,
            system_probe=system_probe,
            runtime_provider=plan.runtime_provider,
            execution_target=plan.execution_target,
            config_dir=config_dir,
            context_service=self._context_service,
        )
        return self._debug_recovery_specialist.assess_failure(
            inspection=inspection,
            repo_family=plan.repo_family,
            specialist_name=plan.specialist_name,
            attempted_steps=plan.steps,
            failed_command=failed_command,
            verification_failure=verification_failure,
            error_output=error_output,
        )

    def _select_specialist(
        self,
        inspection: RepoBringUpInspection,
        repo_family: RepoFamily,
    ) -> RepoSetupSpecialist:
        descriptor = select_subagent_descriptor(
            repo_family=repo_family.value,
            execution_target=inspection.execution_target,
            detected_files=inspection.detected_files,
        )
        if descriptor is None:
            return self._python_specialist
        return self._specialists_by_name.get(descriptor.specialist_name, self._python_specialist)


def _attach_subagent_runtime_profile(
    plan: RepoBringUpPlan,
    *,
    config_dir: Path | None,
) -> RepoBringUpPlan:
    descriptor = descriptor_for_specialist_name(plan.specialist_name)
    if descriptor is None:
        return plan
    profile = build_subagent_runtime_profile(
        config_dir=config_dir,
        slug=descriptor.slug,
        execution_target=plan.execution_target,
    )
    if profile is None:
        return plan
    return replace(
        plan,
        subagent_prompt=profile.prompt_template,
        subagent_workspace_files=tuple(sorted(profile.workspace_sections)),
    )


def resolve_managed_project_dir(config_dir: Path, repo: RepoCatalogRecord) -> Path:
    """Return the managed local project path for a selected repository."""

    return config_dir / "projects" / _slugify(repo.name)


def resolve_runtime_project_dir(config_dir: Path, repo: RepoCatalogRecord, *, execution_target: str) -> str:
    """Return the repo install location Duckln should use for the current execution target."""

    managed_name = resolve_managed_project_dir(config_dir, repo).name
    if execution_target in _REMOTE_EXECUTION_TARGETS:
        return f"~/.duckln/projects/{managed_name}"
    return str(resolve_managed_project_dir(config_dir, repo))


def _active_vm_name(config_dir: Path) -> str | None:
    snapshot = read_config_snapshot(config_dir)
    vm_name = str(snapshot.get("active_vm_name") or snapshot.get("execution_vm_name") or "").strip()
    return vm_name or None


def _active_container_name(config_dir: Path) -> str | None:
    """Plan 97: the Docker container Duckln runs commands inside for the `container`
    execution target."""
    snapshot = read_config_snapshot(config_dir)
    name = str(snapshot.get("active_container_name") or snapshot.get("execution_container_name") or "").strip()
    return name or None


def _wrap_command_for_execution_target(
    *,
    config_dir: Path,
    execution_target: str,
    command: str,
    cwd: str | None,
    preferred_vm_name: str | None = None,
    preferred_cloud_resource_key: str | None = None,
    preferred_cloud_vendor: str | None = None,
    preferred_cloud_region: str | None = None,
    preferred_cloud_shape: str | None = None,
) -> tuple[str | None, dict[str, str | None]]:
    """Wrap a bounded repo command so Duckln can run it on the active target."""

    metadata: dict[str, str | None] = {
        "vm_name": None,
        "cloud_resource_key": None,
        "cloud_vendor": None,
        "cloud_region": None,
        "cloud_shape": None,
    }
    # Plan 136 F1: every executed step command flows through here — sanitize an obviously
    # malformed command (e.g. a model-hallucinated `/cd …`) so a broken command never runs.
    from duckln.shell import sanitize_shell_command as _sanitize_cmd
    command = _sanitize_cmd(command)
    # Plan 155 F1: canonicalize the target (maps the `docker` alias → `container`) so a
    # "docker" value routes through `docker exec` instead of silently running on the HOST.
    from duckln.execution_targets import normalize_execution_target as _norm_target
    execution_target = _norm_target(execution_target)
    if execution_target == "vm":
        vm_name = preferred_vm_name or _active_vm_name(config_dir)
        metadata["vm_name"] = vm_name
        if not vm_name:
            return None, metadata
        if cwd:
            # shlex.quote wraps in single quotes, which prevents ~ expansion in bash.
            # Replace leading ~ with "${HOME}" and use double-quote syntax so bash
            # expands the variable inside the -lc script (e.g. ~/foo → "${HOME}/foo").
            if cwd.startswith("~/") or cwd == "~":
                safe_cwd = '"${HOME}' + cwd[1:] + '"'
            else:
                safe_cwd = shlex.quote(cwd)
            shell_body = f"cd {safe_cwd} && {command}"
        else:
            shell_body = command
        wrapped = f"multipass exec {shlex.quote(vm_name)} -- bash -lc {shlex.quote(shell_body)}"
        return wrapped, metadata
    if execution_target == "container":
        # Plan 97: run inside a Docker container via `docker exec`. The container name
        # comes from the preferred override or the active-container config key.
        container = preferred_vm_name or _active_container_name(config_dir)
        metadata["vm_name"] = container
        if not container:
            return None, metadata
        if cwd:
            if cwd.startswith("~/") or cwd == "~":
                safe_cwd = '"${HOME}' + cwd[1:] + '"'
            else:
                safe_cwd = shlex.quote(cwd)
            shell_body = f"cd {safe_cwd} && {command}"
        else:
            shell_body = command
        wrapped = f"docker exec {shlex.quote(container)} bash -lc {shlex.quote(shell_body)}"
        return wrapped, metadata
    if execution_target in {"aws", "gcp"}:
        workflow = AgentContextService().load_workflow_state(config_dir=config_dir)
        resource_key = preferred_cloud_resource_key or (None if workflow is None else workflow.active_runtime_cloud_resource_key)
        if not resource_key:
            return None, metadata
        record = resolve_managed_resource(config_dir, resource_key=resource_key)
        if record is None:
            return None, metadata
        metadata.update(
            {
                "cloud_resource_key": record.resource_key,
                "cloud_vendor": preferred_cloud_vendor or record.provider.upper(),
                "cloud_region": preferred_cloud_region or record.region,
                "cloud_shape": preferred_cloud_shape or record.shape,
            }
        )
        return build_cloud_remote_exec_command(record, remote_command=command, remote_cwd=cwd), metadata
    if execution_target == "local":
        return command, metadata
    # Plan 155 F1: a non-local target we don't recognize must NOT silently run on the host
    # (that was the docker→host bug). Return None so the caller treats it as "can't run there".
    return None, metadata


def inspect_repo_setup_files(project_dir: Path) -> tuple[str, ...]:
    """Return the setup-related files Duckln should consider."""

    return tuple(file_name for file_name in SETUP_FILE_ORDER if (project_dir / file_name).exists())


def inspect_remote_repo_setup_files(
    *,
    runner: ControlledCommandRunner,
    config_dir: Path,
    execution_target: str,
    project_dir: str,
) -> tuple[str, ...]:
    """List setup-related files inside a remote target (VM/cloud) repo directory.

    Runs a single `ls -1A` and filters against SETUP_FILE_ORDER, preserving the
    canonical ordering. Returns () on any failure so callers can fall back to
    the host-side inspector or to repo_knowledge."""
    if str(execution_target or "").strip().lower() == "local":
        return ()
    wrapped, _meta = _wrap_command_for_execution_target(
        config_dir=config_dir,
        execution_target=execution_target,
        command="ls -1A 2>/dev/null",
        cwd=project_dir,
    )
    if wrapped is None:
        return ()
    try:
        result = runner.run(wrapped)
    except Exception:
        return ()
    if result.timed_out or result.exit_code != 0:
        return ()
    present = {line.strip() for line in (result.stdout or "").splitlines() if line.strip()}
    return tuple(name for name in SETUP_FILE_ORDER if name in present)


def inspect_repo_for_bringup(
    repo: RepoCatalogRecord,
    project_dir: Path,
    *,
    system_probe: SystemProbe | None = None,
    runtime_provider: str | None = None,
    execution_target: str = "local",
    config_dir: Path | None = None,
    context_service: AgentContextService | None = None,
    detected_files_override: tuple[str, ...] | None = None,
) -> RepoBringUpInspection:
    """Inspect repo files and environment signals for supervisor routing."""

    service = context_service or AgentContextService()
    repo_knowledge = service.resolve_repo_knowledge(
        repo=repo,
        config_dir=config_dir,
        project_dir=project_dir,
    )
    if detected_files_override is not None:
        detected_files = detected_files_override
    else:
        detected_files = inspect_repo_setup_files(project_dir)
    if not detected_files and repo_knowledge is not None and repo_knowledge.setup_files:
        detected_files = tuple(repo_knowledge.setup_files)
    inspection = RepoBringUpInspection(
        repo=repo,
        project_dir=project_dir,
        detected_files=detected_files,
        readme_excerpt=_read_readme_excerpt(project_dir / "README.md"),
        system_probe=system_probe or _safe_probe_system(),
        repo_knowledge=repo_knowledge,
        runtime_provider=runtime_provider,
        execution_target=execution_target,
    )
    if config_dir is not None:
        prior_hint = _load_prior_skill_hint(inspection, config_dir)
        if prior_hint is not None:
            inspection = replace(inspection, prior_skill_hint=prior_hint)
    return inspection


def classify_repo_family(inspection: RepoBringUpInspection) -> RepoFamily:
    """Classify the repo family before specialist routing."""

    detected_files = set(inspection.detected_files)
    has_app_setup_signal = bool(
        detected_files
        & {
            "package.json",
            "pnpm-lock.yaml",
            "yarn.lock",
            "package-lock.json",
            "pyproject.toml",
            "requirements.txt",
            "setup.py",
            "environment.yml",
            "go.mod",
            "main.go",
            "Cargo.toml",
            "CMakeLists.txt",
            "configure",
            "manage.py",
            "Dockerfile",
            "docker-compose.yml",
            "docker-compose.yaml",
        }
    )
    searchable_text = " ".join(
        (
            inspection.repo.name,
            inspection.repo.description,
            inspection.repo.category,
            inspection.repo.framework,
            inspection.execution_target,
            inspection.readme_excerpt,
            "" if inspection.repo_knowledge is None else inspection.repo_knowledge.summary,
            " ".join(inspection.detected_files),
        )
    ).lower()
    stack_family = (
        inspection.repo_knowledge.stack_family.lower().strip()
        if inspection.repo_knowledge is not None and inspection.repo_knowledge.stack_family
        else ""
    )

    # When the VM target has language-specific build files (npm/python/go/rust/cmake),
    # route to the language specialist so the right install commands run inside the VM.
    # When only VM-native files are present (Dockerfile, install.sh), keep VM_ENVIRONMENT.
    has_language_signal = bool(
        detected_files
        & {
            "package.json",
            "pnpm-lock.yaml",
            "yarn.lock",
            "package-lock.json",
            "pyproject.toml",
            "requirements.txt",
            "setup.py",
            "environment.yml",
            "go.mod",
            "main.go",
            "Cargo.toml",
            "CMakeLists.txt",
            "configure",
            "manage.py",
        }
    )
    if inspection.execution_target == "vm" and not has_language_signal:
        return RepoFamily.VM_ENVIRONMENT
    if inspection.runtime_provider == "ollama" and not has_app_setup_signal:
        return RepoFamily.PROVIDER_ROUTING
    if "ollama" in searchable_text and not has_app_setup_signal:
        return RepoFamily.PROVIDER_ROUTING
    if any(signal in searchable_text for signal in ("openrouter", "openai api", "anthropic", "openai-compatible", "openai compatible")) and not has_app_setup_signal:
        return RepoFamily.PROVIDER_ROUTING
    if detected_files & {"docker-compose.yml", "docker-compose.yaml"}:
        return RepoFamily.MULTI_SERVICE
    if any(signal in searchable_text for signal in ("stable diffusion", "diffusion", "comfyui", "invokeai", "txt2img")):
        return RepoFamily.DIFFUSION_HEAVY
    if any(signal in searchable_text for signal in ("tts", "speech", "whisper", "audio", "voice", "coqui")):
        return RepoFamily.AUDIO
    if detected_files & {"go.mod", "main.go"} or stack_family == "go":
        return RepoFamily.GO_NATIVE
    # Route Cargo.toml repos to the dedicated Rust specialist before NODE_TYPESCRIPT or
    # the generic CPP_NATIVE fallback. Workspaces with `rust-toolchain.toml` also land
    # here.
    if detected_files & {"Cargo.toml", "rust-toolchain.toml", "rust-toolchain"} or stack_family == "rust":
        return RepoFamily.RUST
    if detected_files & {"package.json", "pnpm-lock.yaml", "yarn.lock", "package-lock.json"}:
        return RepoFamily.NODE_TYPESCRIPT
    if detected_files & {"CMakeLists.txt", "configure"}:
        return RepoFamily.CPP_NATIVE
    if any(signal in searchable_text for signal in ("typescript", "node.js", "nodejs", "next.js", "vite")):
        return RepoFamily.NODE_TYPESCRIPT
    if any(signal in searchable_text for signal in ("golang", "go module", "go.mod", "gin-gonic", "cobra cli")):
        return RepoFamily.GO_NATIVE
    if any(signal in searchable_text for signal in ("cargo build", "cargo run", "rustup")):
        return RepoFamily.RUST
    if any(signal in searchable_text for signal in ("c++", "cmake", "native runtime", "llama.cpp", "ggml")):
        return RepoFamily.CPP_NATIVE
    if inspection.system_probe.is_apple_silicon and "metal" in searchable_text:
        return RepoFamily.CPP_NATIVE
    return RepoFamily.PYTHON


def infer_repo_setup_plan(
    repo: RepoCatalogRecord,
    project_dir: Path,
    *,
    supervisor: RepoBringUpSupervisor | None = None,
    system_probe: SystemProbe | None = None,
    runtime_provider: str | None = None,
    execution_target: str = "local",
    config_dir: Path | None = None,
    detected_files_override: tuple[str, ...] | None = None,
) -> RepoBringUpPlan:
    """Infer a bounded safe setup plan from the cloned repository files."""

    supervisor_agent = supervisor or RepoBringUpSupervisor()
    return supervisor_agent.plan_repo_bringup(
        repo,
        project_dir,
        system_probe=system_probe,
        runtime_provider=runtime_provider,
        execution_target=execution_target,
        config_dir=config_dir,
        detected_files_override=detected_files_override,
    )


def derive_repo_runtime_hints(
    repo: RepoCatalogRecord,
    project_dir: Path,
    *,
    system_probe: SystemProbe | None = None,
    runtime_provider: str | None = None,
    execution_target: str = "local",
    config_dir: Path | None = None,
) -> RepoRuntimeHints:
    """Infer the smallest reusable runtime hints for an existing repo path."""

    inspection = inspect_repo_for_bringup(
        repo,
        project_dir,
        system_probe=system_probe,
        runtime_provider=runtime_provider,
        execution_target=execution_target,
        config_dir=config_dir,
    )
    plan = infer_repo_setup_plan(
        repo,
        project_dir,
        system_probe=inspection.system_probe,
        runtime_provider=runtime_provider,
        execution_target=execution_target,
        config_dir=config_dir,
    )
    environment_path = str(project_dir / ".venv") if (project_dir / ".venv").exists() else None
    auth_requirements = _normalize_auth_requirements(
        inspection.repo_knowledge.auth_requirements if inspection.repo_knowledge is not None else (),
        inspection.repo_knowledge.metadata if inspection.repo_knowledge is not None else {},
    )
    missing_auth_variables = _missing_required_auth_variables(auth_requirements)
    return RepoRuntimeHints(
        detected_files=inspection.detected_files,
        run_command=_infer_start_command(plan, project_dir),
        verify_command=_infer_verify_command(plan, project_dir),
        manual_command=_infer_manual_command(plan, project_dir),
        runtime_kind=_infer_runtime_kind(plan, project_dir),
        stack_family=inspection.repo_knowledge.stack_family if inspection.repo_knowledge is not None else None,
        auth_requirements=auth_requirements,
        missing_auth_variables=missing_auth_variables,
        access_hint=_infer_access_hint(repo, plan, project_dir),
        removal_hint=_build_removal_hint(project_dir, environment_path),
    )


def bring_up_selected_repo(
    repo: RepoCatalogRecord,
    current_mode: ControlMode,
    paths: ConfigPaths,
    *,
    runner: ControlledCommandRunner | None = None,
    approve: Callable[[str], bool] | None = None,
    display: Callable[[str], None] = print,
    supervisor: RepoBringUpSupervisor | None = None,
    runtime_provider: str | None = None,
    execution_target: str = "local",
    vm_name: str | None = None,
    system_probe: SystemProbe | None = None,
    pane_executor: object | None = None,
    on_repair_success: Callable[[str, str, str, str, str, str], None] | None = None,
    plan_mode_enabled: bool = False,
    llm_client_for_plan: object | None = None,
    emit_thought: Callable[[str], None] | None = None,
) -> RepoBringUpResult:
    """Clone, inspect, and present or execute the minimal bring-up path.

    When `plan_mode_enabled=True`, Duckln short-circuits BEFORE any cloning
    or installation: it generates a structured plan via the harness
    supervisor, writes it to pending state, renders it, and returns. The
    user reviews and runs `/plan approve` to begin strict execution via
    `resume_with_approved_plan`.
    """

    if plan_mode_enabled:
        return _generate_plan_for_bringup(
            repo=repo,
            current_mode=current_mode,
            paths=paths,
            execution_target=execution_target,
            display=display,
            llm_client=llm_client_for_plan,
            vm_name=vm_name,
            pane_executor=pane_executor,
            system_probe=system_probe,
            approve=approve,
            emit_thought=emit_thought,
        )

    local_project_dir = resolve_managed_project_dir(paths.config_dir, repo)
    runtime_project_dir = resolve_runtime_project_dir(paths.config_dir, repo, execution_target=execution_target)
    project_dir = Path(runtime_project_dir)
    executed_commands: list[str] = []
    verification_passed = False
    started_at = time.monotonic()
    runner_instance = runner or ControlledCommandRunner(
        trace=display,
        execution_target=execution_target,
        vm_name=vm_name,
        pane_executor=pane_executor,
    )
    supervisor_agent = supervisor or RepoBringUpSupervisor()
    initial_plan = build_repo_plan_record(
        config_dir=paths.config_dir,
        repo=repo,
        execution_target=execution_target,
        understanding=f"Duckln is preparing a bounded bring-up plan for {repo.name}.",
        inspect_items=("README and setup files", "repo family and execution target fit", "smallest verifiable setup path"),
        change_items=("Clone into Duckln-managed workspace if needed", "prepare the minimal environment", "install only the dependencies the chosen specialist needs"),
        completion_items=("Repo files are present", "setup path is verified", "a bounded next run or access hint is available"),
        project_dir=local_project_dir if local_project_dir.exists() else None,
    )
    plan_path = write_repo_plan(initial_plan)
    _write_repo_objective_state(
        paths,
        repo=repo,
        kind="repo_setup",
        status="active",
        goal=f"Set up {repo.name} and verify the smallest documented path.",
        execution_target=execution_target,
        requires_user_decision=False,
        resume_hint=f"Last time Duckln was setting up {repo.name}.",
    )

    # For VM targets, note whether a same-named binary exists, but do not treat
    # that as setup completion. CLIs can exist while the repo still needs its
    # documented README setup/run command.
    if _is_remote_target(execution_target):
        bin_name_hint = repo.name.lower().lstrip("@").split("/")[-1]
        wrapped_binary_check, _meta = _wrap_command_for_execution_target(
            config_dir=paths.config_dir,
            execution_target=execution_target,
            command=f"command -v {shlex.quote(bin_name_hint)} 2>/dev/null && echo '__binary_ready__'",
            cwd=None,
        )
        if wrapped_binary_check is not None:
            binary_check_result = runner_instance.run(wrapped_binary_check)
            if not binary_check_result.timed_out and "__binary_ready__" in (binary_check_result.stdout or ""):
                display(
                    f"✓ {repo.name} binary is present in the VM. "
                    "Duckln will still inspect the README before choosing the setup/run command."
                )

    needs_clone = False
    if execution_target == "local":
        needs_clone = not _local_project_workspace_ready(local_project_dir)
    else:
        wrapped_exists_command, _metadata = _wrap_command_for_execution_target(
            config_dir=paths.config_dir,
            execution_target=execution_target,
            command=_remote_project_workspace_ready_command(),
            cwd=runtime_project_dir,
        )
        if wrapped_exists_command is None:
            message = f"Duckln could not resolve the active {execution_target} target for {repo.name}."
            display(message)
            _record_repo_state(
                paths,
                repo,
                project_dir,
                status="setup_blocked",
                summary=message,
                execution_target=execution_target,
                metadata={"plan_path": str(plan_path)},
            )
            return RepoBringUpResult(
                repo=repo,
                project_dir=project_dir,
                detected_files=(),
                mode=current_mode,
                executed_commands=tuple(executed_commands),
                verification_passed=False,
                message=message,
                repo_family=RepoFamily.PYTHON,
                specialist_name="python",
                setup_outcome=None,
            )
        exists_result = runner_instance.run(wrapped_exists_command)
        needs_clone = exists_result.exit_code != 0 or exists_result.timed_out

    if needs_clone:
        clone_command = _build_clone_command(repo, runtime_project_dir)
        clone_decision, clone_command_to_run = _mode_decision_for_step(
            current_mode,
            clone_command,
            approve=approve,
            prompt="Clone repository",
        )
        if not clone_decision.allowed:
            message = (
                "Supervisor agent paused before clone because this step still needs approval in "
                f"{current_mode.label}."
            )
            display(message)
            _record_repo_state(
                paths,
                repo,
                project_dir,
                status="pending",
                summary=message,
                execution_target=execution_target,
                metadata={"plan_path": str(plan_path)},
            )
            return RepoBringUpResult(
                repo=repo,
                project_dir=project_dir,
                detected_files=(),
                mode=current_mode,
                executed_commands=tuple(executed_commands),
                verification_passed=False,
                message=message,
                repo_family=RepoFamily.PYTHON,
                specialist_name="python",
                setup_outcome=None,
            )

        if execution_target == "local":
            local_project_dir.parent.mkdir(parents=True, exist_ok=True)
        display(
            _agent_phase_status(
                "Supervisor agent",
                "Cloning the repository into Duckln’s managed workspace...",
                phase_index=1,
                phase_total=4,
                started_at=started_at,
            )
        )
        wrapped_clone_command, _metadata = _wrap_command_for_execution_target(
            config_dir=paths.config_dir,
            execution_target=execution_target,
            command=clone_command,
            cwd=None,
        )
        if wrapped_clone_command is None:
            message = f"Duckln could not resolve the active {execution_target} target for {repo.name}."
            display(message)
            _record_repo_state(
                paths,
                repo,
                project_dir,
                status="setup_blocked",
                summary=message,
                execution_target=execution_target,
                metadata={"plan_path": str(plan_path)},
            )
            return RepoBringUpResult(
                repo=repo,
                project_dir=project_dir,
                detected_files=(),
                mode=current_mode,
                executed_commands=tuple(executed_commands),
                verification_passed=False,
                message=message,
                repo_family=RepoFamily.PYTHON,
                specialist_name="python",
                setup_outcome=None,
            )
        clone_result = runner_instance.run(
            wrapped_clone_command.replace(clone_command, clone_command_to_run, 1) if clone_command_to_run != clone_command else wrapped_clone_command,
            cwd=str(local_project_dir.parent) if execution_target == "local" else None,
        )
        executed_commands.append(clone_command_to_run)
        if clone_result.exit_code != 0 or clone_result.timed_out:
            message = _command_failure_message(
                "Clone failed",
                clone_result.stderr,
                command=clone_result.command,
                display=display,
                execution_target=execution_target,
            )
            display(message)
            _record_repo_state(
                paths,
                repo,
                project_dir,
                status="clone_failed",
                summary=message,
                execution_target=execution_target,
                metadata={"plan_path": str(plan_path)},
            )
            return RepoBringUpResult(
                repo=repo,
                project_dir=project_dir,
                detected_files=(),
                mode=current_mode,
                executed_commands=tuple(executed_commands),
                verification_passed=False,
                message=message,
                repo_family=RepoFamily.PYTHON,
                specialist_name="python",
                setup_outcome=None,
            )

        verification_passed = _run_verification(
            runner_instance,
            "test -d .",
            cwd=runtime_project_dir,
            display=display,
            config_dir=paths.config_dir,
            execution_target=execution_target,
        )
        if not verification_passed:
            message = "Clone verification failed: managed project directory is not ready."
            display(message)
            _record_repo_state(
                paths,
                repo,
                project_dir,
                status="clone_verification_failed",
                summary=message,
                execution_target=execution_target,
                metadata={"plan_path": str(plan_path)},
            )
            return RepoBringUpResult(
                repo=repo,
                project_dir=project_dir,
                detected_files=(),
                mode=current_mode,
                executed_commands=tuple(executed_commands),
                verification_passed=False,
                message=message,
                repo_family=RepoFamily.PYTHON,
                specialist_name="python",
                setup_outcome=None,
            )

    display(
        _agent_phase_status(
            "Supervisor agent",
            "Inspecting setup files and routing to a specialist...",
            phase_index=2,
            phase_total=4,
            started_at=started_at,
        )
    )
    remote_detected_files: tuple[str, ...] | None = None
    if execution_target in _REMOTE_EXECUTION_TARGETS:
        remote_detected_files = inspect_remote_repo_setup_files(
            runner=runner_instance,
            config_dir=paths.config_dir,
            execution_target=execution_target,
            project_dir=runtime_project_dir,
        )
        if remote_detected_files:
            display(
                f"Duckln inspected the cloned repo inside the {execution_target} target and detected: "
                + ", ".join(remote_detected_files)
            )
    plan = infer_repo_setup_plan(
        repo,
        local_project_dir,
        supervisor=supervisor_agent,
        system_probe=system_probe,
        runtime_provider=runtime_provider,
        execution_target=execution_target,
        config_dir=paths.config_dir,
        detected_files_override=remote_detected_files,
    )
    if execution_target in _REMOTE_EXECUTION_TARGETS:
        plan = replace(plan, project_dir=Path(runtime_project_dir))
    detailed_plan = build_repo_plan_record(
        config_dir=paths.config_dir,
        repo=repo,
        execution_target=execution_target,
        understanding=(
            f"Duckln selected the {plan.specialist_name} specialist for {repo.name}."
            + (
                f" Active workspace slices: {', '.join(plan.subagent_workspace_files)}."
                if plan.subagent_workspace_files
                else ""
            )
        ),
        inspect_items=plan.detected_files or ("repo layout",),
        change_items=tuple(f"{step.purpose}: {step.command}" for step in plan.steps),
        completion_items=tuple(
            step.verification_command or f"Verify {step.purpose.lower()}"
            for step in plan.steps
        ) or ("A bounded verification step passes",),
        project_dir=project_dir,
        specialist_name=plan.specialist_name,
    )
    plan_path = write_repo_plan(detailed_plan)
    route_blocks = [
        paragraph_block(f"Supervisor agent routed {repo.name} to the {plan.specialist_name} specialist.")
    ]
    if plan.detected_files:
        route_blocks.append(
            file_map_block(
                "Checked setup files",
                tuple((name, str(project_dir / name)) for name in plan.detected_files[:4]),
            )
        )
    else:
        route_blocks.append(paragraph_block("Duckln checked the repo layout before picking the specialist."))
    display(join_blocks(*route_blocks))
    display(status_block("Plan saved", str(plan_path)).render())

    if current_mode is ControlMode.HITL:
        display(
            join_blocks(
                paragraph_block("Smallest bounded setup path:"),
                step_list_block(tuple(f"{step.purpose}: {step.command}" for step in plan.steps)),
            )
        )
        _record_repo_state(
            paths,
            repo,
            project_dir,
            status="suggested",
            summary=plan.summary,
            execution_target=plan.execution_target,
            metadata={"plan_path": str(plan_path)},
        )
        return RepoBringUpResult(
            repo=repo,
            project_dir=project_dir,
            detected_files=plan.detected_files,
            mode=current_mode,
            executed_commands=tuple(executed_commands),
            verification_passed=verification_passed,
            message=plan.summary,
            repo_family=plan.repo_family,
            specialist_name=plan.specialist_name,
            setup_outcome=None,
        )

    execution_result = _execute_plan_with_bounded_recovery(
        plan=plan,
        current_mode=current_mode,
        paths=paths,
        runner=runner_instance,
        supervisor=supervisor_agent,
        system_probe=system_probe,
        approve=approve,
        display=display,
        executed_commands=executed_commands,
        verification_passed=verification_passed,
        started_at=started_at,
        config_dir=paths.config_dir,
        on_repair_success=on_repair_success,
    )
    if execution_result is not None:
        return execution_result

    setup_outcome = _build_setup_outcome(
        repo=repo,
        plan=plan,
        project_dir=project_dir,
        executed_commands=tuple(executed_commands),
        verification=_run_post_setup_verification(
            repo=repo,
            plan=plan,
            project_dir=project_dir,
            runner=runner_instance,
            display=display,
            started_at=started_at,
            config_dir=paths.config_dir,
        ),
    )
    verification_passed = setup_outcome.verification.verified
    final_message = _supervisor_final_summary(
        repo=repo,
        outcome=setup_outcome,
        current_mode=current_mode,
    )
    display(final_message)
    repo_status = "ready" if verification_passed else "partially_ready"
    _record_repo_state(
        paths,
        repo,
        project_dir,
        status=repo_status,
        summary=final_message,
        execution_target=plan.execution_target,
        metadata={
            "active": True,
            **_repo_setup_outcome_metadata(
                repo=repo,
                plan=plan,
                project_dir=project_dir,
                outcome=setup_outcome,
            ),
        },
    )
    supervisor_agent._context_service.record_repo_learning(
        config_dir=paths.config_dir,
        repo=repo,
        summary=_compact_repo_summary(final_message),
        source="bringup-success" if verification_passed else "bringup-partial",
        metadata=_repo_setup_outcome_metadata(
            repo=repo,
            plan=plan,
            project_dir=project_dir,
            outcome=setup_outcome,
        ),
    )
    supervisor_agent._context_service.record_setup_learning(
        config_dir=paths.config_dir,
        repo=repo,
        summary=final_message,
        signal="verified_success" if verification_passed else "verified_failure",
        metadata=_repo_setup_outcome_metadata(
            repo=repo,
            plan=plan,
            project_dir=project_dir,
            outcome=setup_outcome,
        ),
    )
    if verification_passed:
        _write_repo_objective_state(
            paths,
            repo=repo,
            kind=None,
            status=None,
            goal=None,
            execution_target=None,
            clear=True,
        )
    else:
        _write_repo_objective_state(
            paths,
            repo=repo,
            kind="repo_setup",
            status="needs_user_decision",
            goal=f"Set up {repo.name} and verify the smallest documented path.",
            execution_target=execution_target,
            requires_user_decision=True,
            resume_hint=f"Last time Duckln was setting up {repo.name}.",
            last_blocker=final_message,
        )
    return RepoBringUpResult(
        repo=repo,
        project_dir=project_dir,
        detected_files=plan.detected_files,
        mode=current_mode,
        executed_commands=tuple(executed_commands),
        verification_passed=verification_passed,
        message=final_message,
        repo_family=plan.repo_family,
        specialist_name=plan.specialist_name,
        setup_outcome=setup_outcome,
    )


def _build_clone_command(repo: RepoCatalogRecord, project_dir: Path | str) -> str:
    dest = str(project_dir)
    if dest.startswith("~/") or dest == "~":
        # Single-quoting a tilde suppresses bash tilde expansion.
        # Use double-quote syntax with ${HOME} so bash expands it inside bash -lc '...'.
        quoted_dest = '"${HOME}' + dest[1:] + '"'
    else:
        quoted_dest = shlex.quote(dest)
    return f"git clone --depth 1 {shlex.quote(repo.repo_url)} {quoted_dest}"


def _local_project_workspace_ready(project_dir: Path) -> bool:
    if not project_dir.is_dir():
        return False
    if (project_dir / ".git").is_dir():
        return True
    return any((project_dir / name).exists() for name in SETUP_FILE_ORDER)


def _remote_project_workspace_ready_command() -> str:
    setup_checks = " || ".join(f"test -e {shlex.quote(name)}" for name in SETUP_FILE_ORDER)
    return f"test -d . && (test -d .git || {setup_checks})"


_README_NAMES = ("README.md", "README.rst", "README.txt", "README", "readme.md")
_README_CODE_FENCE_PATTERN = re.compile(r"```[^\n`]*\n(?P<body>.*?)```", re.DOTALL)
_README_INLINE_CODE_PATTERN = re.compile(r"`([^`\n]{2,180})`")
_README_PROMPT_PATTERN = re.compile(r"^\s*(?:\$|>|❯)\s+")
_README_ENV_ASSIGNMENT_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
_README_RUN_CONTEXT_TOKENS = (
    "usage",
    "quickstart",
    "quick start",
    "getting started",
    "run",
    "start",
    "serve",
    "launch",
    "develop",
    "development",
)
_README_REMOTE_CONTEXT_TOKENS = ("ubuntu", "linux", "vm", "cloud", "server")
_README_LOCAL_ONLY_TOKENS = ("macos", "mac os", "homebrew", "brew install", "windows", "powershell")
_README_GENERIC_RUN_TOKENS = {
    "python",
    "python3",
    "node",
    "npm",
    "pnpm",
    "yarn",
    "bun",
    "npx",
    "uvicorn",
    "gunicorn",
    "streamlit",
    "gradio",
    "flask",
    "django-admin",
    "jupyter",
    "docker",
    "make",
    "go",
    "cargo",
    "ollama",
}
_README_INSTALL_ONLY_PREFIXES = (
    "pip install",
    "pip3 install",
    "python -m pip install",
    "python3 -m pip install",
    "npm install",
    "pnpm install",
    "yarn install",
    "bun install",
    "brew install",
    "sudo apt",
    "apt install",
    "apt-get install",
    "sudo snap install",
    "git clone",
    "curl ",
    "wget ",
)
_README_START_VERB_PATTERN = re.compile(r"\b(?:start|serve|dev|run|up|launch|server|gateway|web)\b", re.IGNORECASE)
_README_CODE_BLOCK_LANGUAGE_PATTERN = re.compile(r"```(?P<lang>[^\n`]*)\n(?P<body>.*?)```", re.DOTALL)
_README_SKIP_COMMAND_PREFIXES = ("cd ", "git clone ", "echo ", "cat ", "export ", "source ")
_README_INSTALL_COMMAND_PREFIXES = (
    "npm install",
    "npm ci",
    "npm i",
    "pnpm install",
    "pnpm add",
    "yarn install",
    "yarn add",
    "bun install",
    "pip install",
    "pip3 install",
    "python -m pip install",
    "python3 -m pip install",
    "conda env create",
    "cargo build",
    "cargo install",
    "go mod download",
    "go install",
    "docker build",
    "docker compose build",
    "make install",
    "bash install.sh",
    "sh install.sh",
)
_README_SETUP_COMMAND_TOKENS = (" setup", " onboard", " init", " bootstrap", " configure", " doctor")
_README_LLM_MAX_SNIPPET_CHARS = 16000
_README_LLM_ALLOWED_PHASES = {"install", "setup", "verify", "run"}
# Block command-substitution and piping into shell interpreters, but allow `&&`/`;`/`||`
# segment chains — those are how most READMEs document install steps
# (`apt-get update && apt-get install -y …`). Each segment still goes through the
# safety assessor independently below.
_README_UNSAFE_SHELL_TOKENS = ("`", "$(")
_README_UNSAFE_PIPE_SINKS = (
    "| sh",
    "| bash",
    "| zsh",
    "| ksh",
    "|sh",
    "|bash",
    "|zsh",
)
_README_FATAL_PATTERNS = (
    "rm -rf /",
    "rm -rf ~",
    ":(){:|:&};:",
)
_README_LLM_MAC_PATTERN = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")
_README_LLM_IPV6_PATTERN = re.compile(r"\b(?:[0-9A-Fa-f]{1,4}:){2,7}[0-9A-Fa-f]{1,4}\b")


@dataclass(frozen=True)
class _ReadmeWorkflowCommand:
    command: str
    phase: str
    block_index: int
    order: int
    score: int


def scan_readme_for_run_commands(
    project_dir: Path,
    *,
    repo_name: str | None = None,
    execution_target: str = "local",
) -> tuple[str, ...]:
    """Scan the repo README for candidate run/start commands.

    Returns up to 4 unique plausible run commands found in code blocks or inline
    code, ordered by appearance. Used when Duckln has no stored runtime command."""
    readme_path: Path | None = None
    for name in _README_NAMES:
        candidate = project_dir / name
        if candidate.exists():
            readme_path = candidate
            break
    if readme_path is None:
        return ()
    try:
        text = readme_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ()
    return _extract_run_commands_from_readme_text(
        text,
        repo_name=repo_name or project_dir.name,
        execution_target=execution_target,
    )


def _extract_run_commands_from_readme_text(
    text: str,
    *,
    repo_name: str | None = None,
    execution_target: str = "local",
) -> tuple[str, ...]:
    """Apply the README run-command extractors to an in-memory README body."""
    repo_tokens = _readme_repo_tokens(repo_name)
    candidates: dict[str, tuple[int, int, str]] = {}
    order = 0

    def _add_candidate(raw: str, start: int, end: int) -> None:
        nonlocal order
        cmd = _clean_readme_command(raw)
        if not _looks_like_readme_run_command(cmd, repo_tokens=repo_tokens):
            return
        score = _score_readme_run_command(
            text,
            start=start,
            end=end,
            command=cmd,
            repo_tokens=repo_tokens,
            execution_target=execution_target,
        )
        key = " ".join(cmd.casefold().split())
        previous = candidates.get(key)
        if previous is None or (score, -order) > (previous[0], -previous[1]):
            candidates[key] = (score, order, cmd)
        order += 1

    for match in _README_CODE_FENCE_PATTERN.finditer(text):
        body = match.group("body")
        offset = match.start("body")
        for line in body.splitlines():
            _add_candidate(line, offset, offset + len(line))
            offset += len(line) + 1
    for match in _README_INLINE_CODE_PATTERN.finditer(text):
        _add_candidate(match.group(1), match.start(1), match.end(1))

    ranked = sorted(candidates.values(), key=lambda item: (-item[0], item[1]))
    return tuple(item[2] for item in ranked[:4])


def scan_remote_readme_for_run_commands(
    repo_url: str | None,
    *,
    repo_name: str | None = None,
    execution_target: str = "local",
) -> tuple[str, ...]:
    """Fetch the README from a public GitHub repo and extract run commands.

    Used when the repo was cloned inside a VM (so the local project_dir has no
    README to scan) but the upstream is a public github.com repo whose raw
    README is reachable from the host."""
    if not repo_url:
        return ()
    text = _fetch_github_readme_text(repo_url)
    if not text:
        return ()
    return _extract_run_commands_from_readme_text(
        text,
        repo_name=repo_name,
        execution_target=execution_target,
    )


def infer_readme_workflow_steps(
    inspection: RepoBringUpInspection,
    *,
    config_dir: Path | None = None,
    llm_classifier: Callable[[RepoBringUpInspection, str], str | None] | None = None,
) -> list[RepoBringUpStep]:
    """Infer an ordered install/setup workflow from the README before heuristics.

    This is intentionally repo-agnostic: it looks for command blocks under
    Install / Quick start / Getting started sections, separates install/setup/run
    phases, and returns only bounded setup steps. Runtime commands are still
    handled by `_infer_start_command`.
    """

    text = _read_readme_text_for_workflow(
        inspection.project_dir,
        repo_url=inspection.repo.repo_url,
    )
    if not text:
        return []
    candidates = _extract_readme_workflow_commands(
        text,
        repo_name=inspection.repo.name,
        execution_target=inspection.execution_target,
    )
    heuristic_selected = _select_readme_setup_commands(candidates) if candidates else ()
    # Always consult the LLM classifier when a README exists — heuristic alone misses
    # many real-world setup sections (multi-step commands, prose-embedded commands,
    # README structures that don't follow our keyword headings).
    llm_steps = _infer_llm_readme_workflow_steps(
        inspection,
        readme_text=text,
        config_dir=config_dir,
        llm_classifier=llm_classifier,
    )
    heuristic_steps: list[RepoBringUpStep] = []
    for candidate in heuristic_selected:
        heuristic_steps.append(
            RepoBringUpStep(
                purpose=_readme_step_purpose(candidate),
                command=candidate.command,
                verification_command=_verification_for_readme_command(
                    candidate.command,
                    repo_name=inspection.repo.name,
                    execution_target=inspection.execution_target,
                ),
                source="readme",
            )
        )
    # Prefer the LLM result when it produced ≥1 setup/install step; heuristic stays
    # as a fallback so we keep working when the LLM is unavailable. Heuristic prereq
    # detection still runs after selection so we add `node --version`/`pip --version`
    # gates uniformly.
    chosen = llm_steps if llm_steps else heuristic_steps
    return _with_readme_prerequisite_steps(chosen, execution_target=inspection.execution_target)


def _with_readme_prerequisite_steps(
    steps: list[RepoBringUpStep],
    *,
    execution_target: str,
) -> list[RepoBringUpStep]:
    prerequisite_steps = _infer_readme_prerequisite_steps(
        tuple(step.command for step in steps),
        execution_target=execution_target,
    )
    if not prerequisite_steps:
        return steps
    existing = {" ".join(step.command.casefold().split()) for step in steps}
    prefixed: list[RepoBringUpStep] = []
    for step in prerequisite_steps:
        key = " ".join(step.command.casefold().split())
        if key not in existing:
            prefixed.append(step)
    return prefixed + steps


def _infer_readme_prerequisite_steps(
    commands: tuple[str, ...],
    *,
    execution_target: str,
) -> list[RepoBringUpStep]:
    first_tokens = {_first_readme_command_token(command) for command in commands}
    steps: list[RepoBringUpStep] = []
    if first_tokens & {"npm", "npx"}:
        command = _node_npm_prerequisite_check(execution_target=execution_target)
        steps.append(
            RepoBringUpStep(
                purpose="Verify README prerequisite: Node.js and npm",
                command=command,
                verification_command=command,
                source="readme-prerequisite",
            )
        )
    if first_tokens & {"pnpm", "yarn"}:
        command = _node_package_manager_prerequisite_check(first_tokens, execution_target=execution_target)
        steps.append(
            RepoBringUpStep(
                purpose="Verify README prerequisite: Node.js package manager",
                command=command,
                verification_command=command,
                source="readme-prerequisite",
            )
        )
    if first_tokens & {"pip", "pip3", "python", "python3"}:
        command = "python3 -m pip --version" if _is_remote_target(execution_target) else "python -m pip --version"
        steps.append(
            RepoBringUpStep(
                purpose="Verify README prerequisite: Python and pip",
                command=command,
                verification_command=command,
                source="readme-prerequisite",
            )
        )
    if first_tokens & {"docker"}:
        steps.append(
            RepoBringUpStep(
                purpose="Verify README prerequisite: Docker",
                command="docker --version",
                verification_command="docker --version",
                source="readme-prerequisite",
            )
        )
    return steps


def _node_npm_prerequisite_check(*, execution_target: str) -> str:
    # Plan 76 Fix D: a simple, quote-safe check. The previous `node -e "...'...'"`
    # one-liner broke when wrapped for a VM (`bash -lc '...'`) because its inner
    # single quotes collided with the wrapper's. A plain version check verifies
    # both the runtime and the package manager and survives shell wrapping.
    return "node --version && npm --version"


def _node_package_manager_prerequisite_check(first_tokens: set[str], *, execution_target: str) -> str:
    if "pnpm" in first_tokens:
        return "node --version && pnpm --version"
    if "yarn" in first_tokens:
        return "node --version && yarn --version"
    return _node_npm_prerequisite_check(execution_target=execution_target)


def _read_readme_text_for_workflow(project_dir: Path, *, repo_url: str | None) -> str:
    for name in _README_NAMES:
        candidate = project_dir / name
        if candidate.exists():
            try:
                return candidate.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                return ""
    if repo_url:
        return _fetch_github_readme_text(repo_url)
    return ""


def _build_readme_prereq_preflight_steps(
    *,
    inspection: RepoBringUpInspection,
    execution_target: str,
) -> tuple[RepoBringUpStep, ...]:
    """Plan 57 Phase 2: read the repo's README and produce probe+install steps
    for each declared system prerequisite (Node, Python, Rust, uv, etc.).

    Repo-agnostic: works for any README that mentions known tools in a Requirements,
    Prerequisites, or Setup section. Returns empty tuple when README is absent or
    declares no known prerequisites.
    """
    # Avoid the import at module load to keep readme_skill import cheap.
    from duckln.readme_skill import extract_readme_metadata, build_prerequisite_preflight_steps

    readme_text = _read_readme_text_for_workflow(
        inspection.project_dir, repo_url=inspection.repo.repo_url
    )
    if not readme_text:
        return ()
    metadata = extract_readme_metadata(readme_text)
    # Plan 59 Fix 3: optionally refine the heuristic prereq list with an LLM
    # call. Fails open — if no LLM configured or the call errors, we use the
    # heuristic prereqs unchanged.
    refined_prereqs = _llm_refine_readme_prereqs(
        readme_text=readme_text,
        heuristic_prereqs=metadata.prerequisites,
        repo_name=inspection.repo.name,
        repo_url=inspection.repo.repo_url,
        execution_target=execution_target,
        config_dir=_resolve_config_dir_from_inspection(inspection),
    )
    if refined_prereqs is not None:
        # replace metadata.prerequisites in our local view; keep metadata
        # immutable as the dataclass demands (build a shallow copy via dataclasses.replace).
        from dataclasses import replace as _replace
        metadata = _replace(metadata, prerequisites=refined_prereqs)
    if not metadata.prerequisites:
        return ()
    # Plan 58 Bug A: the package manager must match the EXECUTION target's OS,
    # not the local host's. Duckln's remote targets (vm/aws/gcp/ssh) are Ubuntu
    # by default, so they need apt — regardless of whether Duckln itself is
    # running on macOS, Linux, or Windows.
    if execution_target in {"vm", "aws", "gcp", "ssh"}:
        # Remote execution targets default to Ubuntu Linux in Duckln.
        effective_os = "Linux"
    else:
        # Plan 61 Fix A: when running locally on Windows, pick winget; for
        # macOS/Linux local, use the host probe.
        effective_os = (
            inspection.system_probe.operating_system if inspection.system_probe else ""
        )
    # Plan 58 Bug B + Plan 61 Fix A: code-block scan can register tools we don't
    # know how to install (e.g. `vite` is provided by `npm install`, not its own
    # package). Drop these from the preflight plan so they don't false-positive
    # the whole setup. Now also recognises install_winget as a valid path.
    installable = tuple(
        p for p in metadata.prerequisites
        if (
            (p.install_apt is not None)
            or (p.install_brew is not None)
            or (p.install_fallback is not None)
            or (getattr(p, "install_winget", None) is not None)
        )
    )
    unknown_tools = tuple(
        p.name for p in metadata.prerequisites
        if p.install_apt is None and p.install_brew is None and p.install_fallback is None
    )
    # Plan 61 Fix G: surface authoritative-source guidance for unknown tools so
    # the user gets a clear next step instead of a silent fall-through. Best-effort,
    # bounded, cached 24h — never blocks the preflight plan.
    if unknown_tools:
        try:
            from duckln.authoritative_sources import (
                consult_authoritative_source,
                format_user_guidance,
            )
            cfg = _resolve_config_dir_from_inspection(inspection)
            for tool in unknown_tools[:3]:  # cap to avoid spamming
                if tool in _LLM_REFINEMENT_ALLOWLIST:
                    continue  # known allow-list tools handled elsewhere
                url = consult_authoritative_source(tool_name=tool, config_dir=cfg, timeout_seconds=5.0)
                # Display via the LLM module's logger? No — use repo_bringup's
                # logger so this doesn't introduce a new dependency. The plan
                # caller doesn't have a display callback at this layer; we
                # write the guidance into a skill memory note so the user
                # can read it later. The runtime repair path WILL surface it
                # interactively via the display callback when the missing
                # tool actually trips a failure.
                message = format_user_guidance(tool_name=tool, url=url)
                _persist_unknown_tool_guidance(
                    config_dir=cfg, repo_slug=repo_slug_safe(inspection), tool=tool, message=message
                )
        except Exception:
            pass
    if not installable:
        return ()
    preflight_triples = build_prerequisite_preflight_steps(
        installable,
        system_probe_os=effective_os,
        execution_target=execution_target,
    )
    # Plan 58 Bug D: before adding a preflight step, consult the persistent
    # failure log. If the same (command, target) failed within the past 24h
    # with the same fingerprint, skip this step — Duckln has learned not to
    # repeat the same wrong thing across sessions.
    config_dir = getattr(inspection, "config_dir", None)
    if config_dir is None:
        # Fall back: derive from project_dir's parent (~/.duckln/projects/<name> -> ~/.duckln).
        try:
            config_dir = inspection.project_dir.parent.parent
        except Exception:
            config_dir = None
    repo_slug = inspection.repo.repo_url or inspection.repo.name
    steps: list[RepoBringUpStep] = []
    for purpose, probe_command, install_command in preflight_triples:
        if config_dir is not None:
            try:
                from state.access import lookup_recent_failure
                prior = lookup_recent_failure(
                    config_dir,
                    repo_slug=repo_slug,
                    command=install_command,
                    execution_target=execution_target,
                )
                if prior is not None:
                    # Skip — Duckln learned this failed last time.
                    continue
            except Exception:
                # Failure-memory lookup is best-effort; never break setup.
                pass
        steps.append(
            RepoBringUpStep(
                purpose=purpose,
                command=install_command,
                verification_command=probe_command,
                source="readme-prereq",
            )
        )
    return tuple(steps)


def repo_slug_safe(inspection: RepoBringUpInspection) -> str:
    """Return a stable repo slug used for cache/memory file naming."""
    return inspection.repo.repo_url or inspection.repo.name or "unknown"


def _persist_unknown_tool_guidance(
    *, config_dir: Path | None, repo_slug: str, tool: str, message: str
) -> None:
    """Plan 61 Fix G: write the unknown-tool guidance into a per-repo skill
    note so the user can read it later (and so the runtime repair flow can
    surface it when the missing tool trips a failure)."""
    if config_dir is None:
        return
    try:
        from state.access import write_skill_memory_state
        write_skill_memory_state(
            config_dir,
            slug=f"unknown-tool-{_url_slug(repo_slug)}-{tool}",
            title=f"Unknown tool guidance: {tool}",
            summary=message,
        )
    except Exception:
        pass


def _resolve_config_dir_from_inspection(inspection: RepoBringUpInspection) -> Path | None:
    """Derive the Duckln config_dir from an inspection's project_dir.
    Project directory is typically ~/.duckln/projects/<name>; config_dir is
    the parent of `projects/`. Returns None if the layout doesn't match."""
    try:
        proj = inspection.project_dir
        if proj.parent.name == "projects":
            return proj.parent.parent
    except Exception:
        return None
    return None


# Plan 59 Fix 3: tools the LLM is allowed to add to / remove from the prereq list.
# Constrained to the known table so we never plan an install for an unknown tool.
_LLM_REFINEMENT_ALLOWLIST: frozenset[str] = frozenset({
    "node", "npm", "python", "rust", "cargo", "uv", "go", "docker", "git",
    "make", "cmake", "pnpm", "yarn", "poetry",
})


def _llm_refine_readme_prereqs(
    *,
    readme_text: str,
    heuristic_prereqs: tuple,
    repo_name: str,
    repo_url: str,
    execution_target: str,
    config_dir: Path | None,
) -> tuple | None:
    """Plan 59 Fix 3: optionally refine the heuristic prereq list with an LLM
    audit. Returns the refined prereq tuple, or None to mean "use heuristic".

    Fail-open: any missing config / LLM error / malformed JSON / low
    confidence returns None.
    """
    if not config_dir or not heuristic_prereqs or not readme_text:
        return None

    # Check 24h cache before calling LLM.
    cached = _read_llm_refinement_cache(config_dir, repo_url=repo_url)
    if cached is not None:
        return _apply_refinement_to_heuristic(heuristic_prereqs, cached)

    try:
        current = load_app_config(ConfigPaths(config_dir=config_dir, config_file=config_dir / "config.json"))
    except Exception:
        return None
    if current is None or not current.model:
        return None

    snippet = readme_text if len(readme_text) <= 12000 else readme_text[:12000]
    snippet = _redact_readme_snippet_for_llm(snippet)

    heuristic_names = [p.name for p in heuristic_prereqs]
    system_prompt = (
        "You audit a heuristic prerequisite list against a README. Return JSON only. "
        "Schema: {\"additions\":[{\"name\":str,\"reason\":str}],"
        "\"removals\":[{\"name\":str,\"reason\":str}],\"confidence\":0.0-1.0}. "
        "additions: tools NEEDED to run the README's documented commands but not in the list. "
        "removals: tools in the list that are NOT actually needed (e.g. project-local binaries). "
        "Only suggest tools from this allow-list: "
        "node, npm, python, rust, cargo, uv, go, docker, git, make, cmake, pnpm, yarn, poetry. "
        "Return at most 5 additions and 5 removals."
    )
    user_message = (
        f"Repo: {repo_name}\n"
        f"Target: {execution_target}\n"
        f"Heuristic prereqs: {heuristic_names}\n\n"
        f"README:\n{snippet}"
    )
    try:
        raw = generate_provider_reply(
            current.provider,
            model_id=current.model,
            api_key=current.api_key,
            base_url=current.base_url,
            system_prompt=system_prompt,
            user_message=user_message,
            config_dir=config_dir,
        )
    except (ProviderRequestError, ValueError, OSError, RuntimeError):
        return None
    if not raw:
        return None

    additions, removals, confidence = _parse_llm_refinement_json(raw)
    if confidence < 0.5:
        return None

    refinement = {"additions": additions, "removals": removals}
    _write_llm_refinement_cache(config_dir, repo_url=repo_url, refinement=refinement)
    return _apply_refinement_to_heuristic(heuristic_prereqs, refinement)


def _parse_llm_refinement_json(raw: str) -> tuple[list[str], list[str], float]:
    """Parse the LLM refinement reply. Returns (additions, removals, confidence)."""
    import json
    payload_text = raw.strip()
    if payload_text.startswith("```"):
        payload_text = re.sub(r"^```(?:json)?\s*", "", payload_text, flags=re.IGNORECASE).strip()
        payload_text = re.sub(r"\s*```$", "", payload_text).strip()
    try:
        payload = json.loads(payload_text)
    except (json.JSONDecodeError, TypeError):
        return [], [], 0.0
    if not isinstance(payload, dict):
        return [], [], 0.0
    additions_raw = payload.get("additions") or []
    removals_raw = payload.get("removals") or []
    confidence = payload.get("confidence", 0.0)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.0
    additions = []
    if isinstance(additions_raw, list):
        for item in additions_raw[:5]:
            if isinstance(item, dict):
                name = str(item.get("name", "")).strip().lower()
                if name and name in _LLM_REFINEMENT_ALLOWLIST:
                    additions.append(name)
    removals = []
    if isinstance(removals_raw, list):
        for item in removals_raw[:5]:
            if isinstance(item, dict):
                name = str(item.get("name", "")).strip().lower()
                if name and name in _LLM_REFINEMENT_ALLOWLIST:
                    removals.append(name)
    return additions, removals, confidence


def _apply_refinement_to_heuristic(heuristic: tuple, refinement: dict) -> tuple:
    """Apply additions and removals to the heuristic prereq tuple. Returns a
    new tuple (possibly identical when refinement is a no-op)."""
    from duckln.readme_skill import ReadmePrerequisite, _KNOWN_PREREQUISITES
    additions = refinement.get("additions") or []
    removals = set(refinement.get("removals") or [])
    by_name = {p.name: p for p in heuristic}
    # Drop removals.
    for name in removals:
        by_name.pop(name, None)
    # Add additions (only those we know how to install).
    for name in additions:
        if name in by_name:
            continue
        if name not in _KNOWN_PREREQUISITES:
            continue
        spec_known = _KNOWN_PREREQUISITES[name]
        probe = spec_known[0]
        install_apt = spec_known[1]
        install_brew = spec_known[2]
        install_fallback = spec_known[3]
        install_winget = spec_known[4] if len(spec_known) > 4 else None
        by_name[name] = ReadmePrerequisite(
            name=name,
            version_constraint=None,
            probe_command=probe,
            install_apt=install_apt,
            install_brew=install_brew,
            install_fallback=install_fallback,
            install_winget=install_winget,
            source_line=f"(LLM-refined)",
        )
    return tuple(by_name.values())


def _read_llm_refinement_cache(config_dir: Path, *, repo_url: str) -> dict | None:
    """Return cached refinement {additions, removals} if it's fresh (<24h old)."""
    import json
    import time as _time
    from agent.memory import AGENT_MEMORY_DIR_NAME, SKILLS_DIR_NAME
    try:
        cache_file = (
            config_dir / AGENT_MEMORY_DIR_NAME / SKILLS_DIR_NAME
            / f"llm-prereq-refine-{_url_slug(repo_url)}.md"
        )
        if not cache_file.exists():
            return None
        text = cache_file.read_text(encoding="utf-8")
    except Exception:
        return None
    # Cache file format: a single ```json block with {refinement, cached_at}.
    start = text.find("```json")
    if start < 0:
        return None
    after = text.find("\n", start)
    end = text.find("```", after + 1)
    if after < 0 or end < 0:
        return None
    try:
        payload = json.loads(text[after + 1:end].strip())
    except (json.JSONDecodeError, ValueError):
        return None
    cached_at = str(payload.get("cached_at", "")).strip()
    if not cached_at:
        return None
    try:
        struct = _time.strptime(cached_at, "%Y-%m-%dT%H:%M:%SZ")
        epoch = _time.mktime(struct) - _time.timezone
    except (ValueError, OverflowError):
        return None
    if (_time.time() - epoch) > (24 * 3600):
        return None
    refinement = payload.get("refinement")
    if isinstance(refinement, dict):
        return refinement
    return None


def _write_llm_refinement_cache(config_dir: Path, *, repo_url: str, refinement: dict) -> None:
    """Persist the refinement keyed by repo URL for 24h."""
    import json
    import time as _time
    from agent.memory import AGENT_MEMORY_DIR_NAME, SKILLS_DIR_NAME
    try:
        skills_dir = config_dir / AGENT_MEMORY_DIR_NAME / SKILLS_DIR_NAME
        skills_dir.mkdir(parents=True, exist_ok=True)
        cache_file = skills_dir / f"llm-prereq-refine-{_url_slug(repo_url)}.md"
        payload = {
            "refinement": refinement,
            "cached_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
        }
        content = (
            f"# LLM prereq refinement cache for {repo_url}\n\n"
            "Auto-generated. Delete to force re-call.\n\n"
            f"```json\n{json.dumps(payload, indent=2)}\n```\n"
        )
        cache_file.write_text(content, encoding="utf-8")
    except Exception:
        pass


def _url_slug(repo_url: str) -> str:
    """Reduce a repo URL to a filesystem-safe slug."""
    if not repo_url:
        return "unknown"
    return re.sub(r"[^A-Za-z0-9._-]+", "-", repo_url).strip("-").lower()[:80] or "unknown"


def _extract_readme_workflow_commands(
    text: str,
    *,
    repo_name: str | None,
    execution_target: str,
) -> tuple[_ReadmeWorkflowCommand, ...]:
    repo_tokens = _readme_repo_tokens(repo_name)
    candidates: list[_ReadmeWorkflowCommand] = []
    order = 0
    for block_index, match in enumerate(_README_CODE_BLOCK_LANGUAGE_PATTERN.finditer(text)):
        body = match.group("body")
        block_start = match.start("body")
        block_context = text[max(0, match.start() - 900) : min(len(text), match.end() + 200)]
        block_score = _score_readme_block_context(block_context, execution_target=execution_target)
        for line in body.splitlines():
            command = _clean_readme_command(line)
            phase = _classify_readme_workflow_command(command, repo_tokens=repo_tokens)
            if phase is None:
                block_start += len(line) + 1
                continue
            score = block_score + _score_readme_workflow_command(command, phase=phase, repo_tokens=repo_tokens)
            candidates.append(
                _ReadmeWorkflowCommand(
                    command=command,
                    phase=phase,
                    block_index=block_index,
                    order=order,
                    score=score,
                )
            )
            order += 1
            block_start += len(line) + 1
    for match in _README_INLINE_CODE_PATTERN.finditer(text):
        command = _clean_readme_command(match.group(1))
        phase = _classify_readme_workflow_command(command, repo_tokens=repo_tokens)
        if phase is None:
            continue
        context = text[max(0, match.start() - 700) : min(len(text), match.end() + 200)]
        candidates.append(
            _ReadmeWorkflowCommand(
                command=command,
                phase=phase,
                block_index=10_000 + order,
                order=order,
                score=_score_readme_block_context(context, execution_target=execution_target)
                + _score_readme_workflow_command(command, phase=phase, repo_tokens=repo_tokens),
            )
        )
        order += 1
    return tuple(candidates)


def _classify_readme_workflow_command(command: str, *, repo_tokens: set[str]) -> str | None:
    if not command or len(command) > 220:
        return None
    lowered = " ".join(command.casefold().split())
    if not lowered or lowered.startswith(("#", "//")):
        return None
    if any(lowered.startswith(prefix) for prefix in _README_SKIP_COMMAND_PREFIXES):
        return None
    if any(lowered.startswith(prefix) for prefix in _README_INSTALL_COMMAND_PREFIXES):
        return "install"
    first_token = _first_readme_command_token(command)
    if first_token in {"npm", "pnpm", "yarn", "bun"} and any(token in lowered for token in _README_SETUP_COMMAND_TOKENS):
        return "setup"
    if first_token in repo_tokens and any(token in lowered for token in _README_SETUP_COMMAND_TOKENS):
        return "setup"
    if first_token.startswith("./") and any(token in lowered for token in _README_SETUP_COMMAND_TOKENS):
        return "setup"
    if _looks_like_readme_run_command(command, repo_tokens=repo_tokens):
        return "run"
    return None


def _score_readme_block_context(context: str, *, execution_target: str) -> int:
    lowered = context.casefold()
    score = 0
    if "install (recommended)" in lowered or "preferred setup" in lowered:
        score += 55
    if "install" in lowered or "installation" in lowered:
        score += 35
    if "quick start" in lowered or "getting started" in lowered:
        score += 25
    if "tl;dr" in lowered or "recommended" in lowered:
        score += 18
    if "from source" in lowered or "development" in lowered or "dev loop" in lowered:
        score -= 18
    if _is_remote_target(execution_target) and any(token in lowered for token in _README_REMOTE_CONTEXT_TOKENS):
        score += 8
    if _is_remote_target(execution_target) and any(token in lowered for token in _README_LOCAL_ONLY_TOKENS):
        score -= 20
    return score


def _score_readme_workflow_command(command: str, *, phase: str, repo_tokens: set[str]) -> int:
    lowered = command.casefold()
    score = {"install": 40, "setup": 35, "run": 10}.get(phase, 0)
    if any(token in lowered for token in repo_tokens):
        score += 20
    if "--install-daemon" in lowered or "onboard" in lowered:
        score += 18
    if "-g " in lowered or " --global" in lowered:
        score += 8
    return score


def _select_readme_setup_commands(candidates: tuple[_ReadmeWorkflowCommand, ...]) -> tuple[_ReadmeWorkflowCommand, ...]:
    setup_candidates = [candidate for candidate in candidates if candidate.phase in {"install", "setup"}]
    if not setup_candidates:
        return ()
    block_scores: dict[int, int] = {}
    for candidate in setup_candidates:
        block_scores[candidate.block_index] = block_scores.get(candidate.block_index, 0) + candidate.score
    best_block = max(block_scores, key=lambda key: (block_scores[key], -key))
    selected = [candidate for candidate in setup_candidates if candidate.block_index == best_block]
    if not selected:
        selected = sorted(setup_candidates, key=lambda item: (-item.score, item.order))[:4]
    deduped: list[_ReadmeWorkflowCommand] = []
    seen: set[str] = set()
    for candidate in sorted(selected, key=lambda item: item.order):
        key = " ".join(candidate.command.casefold().split())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
        if len(deduped) >= 6:
            break
    return tuple(deduped)


def _readme_step_purpose(candidate: _ReadmeWorkflowCommand) -> str:
    if candidate.phase == "install":
        return "Run README install command"
    if candidate.phase == "setup":
        return "Run README setup command"
    return "Run README command"


def _infer_llm_readme_workflow_steps(
    inspection: RepoBringUpInspection,
    *,
    readme_text: str,
    config_dir: Path | None,
    llm_classifier: Callable[[RepoBringUpInspection, str], str | None] | None,
) -> list[RepoBringUpStep]:
    snippet = _redact_readme_snippet_for_llm(_readme_workflow_snippet(readme_text))
    if not snippet:
        return []
    raw_reply = None
    if llm_classifier is not None:
        raw_reply = llm_classifier(inspection, snippet)
    elif config_dir is not None:
        raw_reply = _default_llm_readme_classifier(inspection, snippet=snippet, config_dir=config_dir)
    if not raw_reply:
        return []
    candidates = _parse_llm_readme_workflow_json(
        raw_reply,
        repo_name=inspection.repo.name,
        execution_target=inspection.execution_target,
    )
    steps: list[RepoBringUpStep] = []
    for candidate in _select_readme_setup_commands(candidates):
        steps.append(
            RepoBringUpStep(
                purpose=_readme_step_purpose(candidate),
                command=candidate.command,
                verification_command=_verification_for_readme_command(
                    candidate.command,
                    repo_name=inspection.repo.name,
                    execution_target=inspection.execution_target,
                ),
                source="readme-llm",
            )
        )
    return steps


def _redact_readme_snippet_for_llm(snippet: str) -> str:
    redacted = redact_sensitive_data(snippet)
    redacted = _README_LLM_MAC_PATTERN.sub("[REDACTED_MAC]", redacted)
    redacted = _README_LLM_IPV6_PATTERN.sub("[REDACTED_IP]", redacted)
    return redacted


def _readme_workflow_snippet(readme_text: str) -> str:
    """Return the README content the LLM classifier should see.

    Previously this centered on keyword headings (Install/Setup/Usage/Run) and capped
    at 6k chars, which made the LLM miss commands documented elsewhere in the file. We
    now send the full README, truncated only at the safety budget — the LLM is better
    at scanning the whole thing than our heuristic at locating the right section.
    """

    if not readme_text:
        return ""
    if len(readme_text) <= _README_LLM_MAX_SNIPPET_CHARS:
        return readme_text
    # If we have to truncate, keep the top of the README (almost always where install
    # appears) and append a tail slice so usage/run sections aren't entirely lost.
    head_budget = int(_README_LLM_MAX_SNIPPET_CHARS * 0.7)
    tail_budget = _README_LLM_MAX_SNIPPET_CHARS - head_budget - 32
    head = readme_text[:head_budget]
    tail = readme_text[-tail_budget:] if tail_budget > 0 else ""
    return f"{head}\n…\n{tail}" if tail else head


def _default_llm_readme_classifier(
    inspection: RepoBringUpInspection,
    *,
    snippet: str,
    config_dir: Path,
) -> str | None:
    try:
        current = load_app_config(ConfigPaths(config_dir=config_dir, config_file=config_dir / "config.json"))
    except Exception:
        return None
    if current is None or not current.model:
        return None
    system_prompt = (
        "You classify README setup commands AND prerequisites for Duckln. "
        "Return only JSON. Do not include markdown. "
        "Schema: {"
        "\"commands\":[{\"phase\":\"install|setup|run|verify\","
        "\"command\":\"single shell command\",\"reason\":\"short\",\"confidence\":0.0}],"
        "\"prerequisites\":[{\"name\":\"node|npm|python|rust|cargo|uv|go|docker|git|"
        "make|cmake|pnpm|yarn|poetry\",\"version_constraint\":null,\"reason\":\"short\"}],"
        "\"usage_summary\":\"one paragraph\",\"usage_example\":\"single example or empty\""
        "}. "
        "Prefer documented recommended install/setup steps for the target OS. "
        "Prerequisites are SYSTEM TOOLS (not project deps) that must exist BEFORE setup commands run. "
        "Only use prerequisite names from the listed allow-list. "
        "Do not invent commands."
    )
    user_message = (
        f"Repo: {inspection.repo.name}\n"
        f"Repo URL: {inspection.repo.repo_url}\n"
        f"Target: {inspection.execution_target}\n"
        f"Detected files: {', '.join(inspection.detected_files)}\n"
        "Classify only commands that appear in this README snippet:\n\n"
        f"{snippet}"
    )
    try:
        return generate_provider_reply(
            current.provider,
            model_id=current.model,
            api_key=current.api_key,
            base_url=current.base_url,
            system_prompt=system_prompt,
            user_message=user_message,
            config_dir=config_dir,
        )
    except (ProviderRequestError, ValueError, OSError, RuntimeError):
        return None


def _parse_llm_readme_workflow_json(
    raw_reply: str,
    *,
    repo_name: str,
    execution_target: str,
) -> tuple[_ReadmeWorkflowCommand, ...]:
    payload_text = raw_reply.strip()
    if payload_text.startswith("```"):
        payload_text = re.sub(r"^```(?:json)?\s*", "", payload_text, flags=re.IGNORECASE).strip()
        payload_text = re.sub(r"\s*```$", "", payload_text).strip()
    try:
        payload = json.loads(payload_text)
    except (json.JSONDecodeError, TypeError):
        return ()
    raw_commands = payload.get("commands") if isinstance(payload, dict) else None
    if not isinstance(raw_commands, list):
        return ()
    repo_tokens = _readme_repo_tokens(repo_name)
    candidates: list[_ReadmeWorkflowCommand] = []
    for order, item in enumerate(raw_commands):
        if not isinstance(item, dict):
            continue
        phase = str(item.get("phase") or "").strip().casefold()
        command = _clean_readme_command(str(item.get("command") or ""))
        if phase not in _README_LLM_ALLOWED_PHASES:
            continue
        if not _is_valid_llm_readme_command(command, phase=phase, repo_tokens=repo_tokens, execution_target=execution_target):
            continue
        confidence = item.get("confidence")
        confidence_score = int(float(confidence) * 20) if isinstance(confidence, (int, float)) else 0
        candidates.append(
            _ReadmeWorkflowCommand(
                command=command,
                phase=phase,
                block_index=0,
                order=order,
                score=60 + confidence_score + _score_readme_workflow_command(command, phase=phase, repo_tokens=repo_tokens),
            )
        )
    return tuple(candidates)


def _is_valid_llm_readme_command(
    command: str,
    *,
    phase: str,
    repo_tokens: set[str],
    execution_target: str,
) -> bool:
    if not command or "\n" in command or len(command) > 320:
        return False
    lowered = command.casefold()
    if any(token in command for token in _README_UNSAFE_SHELL_TOKENS):
        return False
    if any(sink in lowered for sink in _README_UNSAFE_PIPE_SINKS):
        return False
    if any(pattern in lowered for pattern in _README_FATAL_PATTERNS):
        return False
    # Allow `&&`, `;`, `||` chains but assess each segment so destructive parts of a
    # composite command are still caught.
    for segment in re.split(r"&&|;|\|\|", command):
        segment = segment.strip()
        if not segment:
            continue
        if assess_command(segment).blocked:
            return False
    if not _readme_command_matches_target(command, execution_target=execution_target):
        return False
    # For chained commands (apt-get update && apt-get install -y curl), classify each
    # segment independently — if any segment matches the requested phase, accept the
    # whole command. This matches how READMEs typically document install steps.
    segments = [seg.strip() for seg in re.split(r"&&|;|\|\|", command) if seg.strip()]
    classified = _classify_readme_workflow_command(command, repo_tokens=repo_tokens)
    segment_classes = {
        _classify_readme_workflow_command(seg, repo_tokens=repo_tokens) for seg in segments
    }
    if phase in {"install", "setup"}:
        if classified in {"install", "setup"}:
            return True
        if segment_classes & {"install", "setup"}:
            return True
        if _looks_like_readme_setup_command(command, repo_tokens=repo_tokens):
            return True
        if any(_looks_like_readme_setup_command(seg, repo_tokens=repo_tokens) for seg in segments):
            return True
        # Trust the LLM when it labels a known-safe run-script command as setup
        # ("npm run build" / "yarn build" / "make setup"): the safety gate above
        # already rejected anything destructive.
        if phase == "setup" and _looks_like_node_run_script(command):
            return True
        return False
    if phase == "run":
        return classified == "run" or _looks_like_readme_run_command(command, repo_tokens=repo_tokens)
    return _looks_like_readme_verification_command(command, repo_tokens=repo_tokens)


def _readme_command_matches_target(command: str, *, execution_target: str) -> bool:
    lowered = command.casefold()
    if _is_remote_target(execution_target) and lowered.startswith("brew "):
        return False
    if platform.system() == "Darwin" and any(lowered.startswith(prefix) for prefix in ("apt ", "apt-get ", "sudo apt ", "sudo apt-get ", "snap ", "sudo snap ")):
        return False
    if platform.system() == "Windows" and lowered.startswith(("brew ", "apt ", "apt-get ", "sudo ", "snap ")):
        return False
    return True


def _looks_like_readme_setup_command(command: str, *, repo_tokens: set[str]) -> bool:
    lowered = " ".join(command.casefold().split())
    first_token = _first_readme_command_token(command)
    parts = lowered.split()
    if first_token in repo_tokens and 2 <= len(parts) <= 5:
        return True
    if first_token in repo_tokens and any(token in lowered for token in _README_SETUP_COMMAND_TOKENS):
        return True
    if first_token in {"npm", "pnpm", "yarn", "bun", "npx"} and any(token in lowered for token in _README_SETUP_COMMAND_TOKENS):
        return True
    return lowered in {"corepack enable"}


def _looks_like_node_run_script(command: str) -> bool:
    """True for known-safe Node script runners like `npm run build` / `yarn build`."""
    lowered = " ".join(command.casefold().split())
    if not lowered:
        return False
    parts = lowered.split()
    if not parts:
        return False
    head = parts[0]
    if head in {"npm", "pnpm", "yarn", "bun", "npx"} and len(parts) >= 2:
        return True
    if head == "make" and len(parts) == 2:
        return True
    return False


def _looks_like_readme_verification_command(command: str, *, repo_tokens: set[str]) -> bool:
    lowered = " ".join(command.casefold().split())
    first_token = _first_readme_command_token(command)
    return first_token in {"which", "command", "test"} or "--version" in lowered or " doctor" in lowered or first_token in repo_tokens


def _verification_for_readme_command(command: str, *, repo_name: str, execution_target: str) -> str | None:
    normalized = " ".join(command.casefold().split())
    bin_name = repo_name.lower().lstrip("@").split("/")[-1]
    if "npm install -g" in normalized or "pnpm add -g" in normalized or "yarn global add" in normalized:
        return f"command -v {shlex.quote(bin_name)}"
    if any(token in normalized for token in ("pnpm install", "npm install", "npm ci", "yarn install", "bun install")):
        return dir_exists_check("node_modules", execution_target=execution_target)
    if "pip install" in normalized:
        return "python -m pip --version"
    if any(token in normalized for token in (" setup", " onboard", " init", " bootstrap")):
        if bin_name:
            return f"command -v {shlex.quote(bin_name)} 2>/dev/null || test -d ."
        return "test -d ."
    return None


def _readme_repo_tokens(repo_name: str | None) -> set[str]:
    tokens: set[str] = set()
    if not repo_name:
        return tokens
    for raw in re.split(r"[/\s]+", repo_name.casefold()):
        token = re.sub(r"[^a-z0-9_.-]", "", raw).strip(".-_")
        if token:
            tokens.add(token)
            tokens.add(token.replace("_", "-"))
            tokens.add(token.replace("-", "_"))
    return {token for token in tokens if len(token) >= 3}


def _clean_readme_command(raw: str) -> str:
    cmd = raw.strip()
    cmd = _README_PROMPT_PATTERN.sub("", cmd).strip()
    if cmd.startswith("sudo "):
        return cmd
    if cmd.startswith("# "):
        return ""
    return cmd


def _first_readme_command_token(command: str) -> str:
    try:
        parts = shlex.split(command, posix=True)
    except ValueError:
        parts = command.split()
    while parts and _README_ENV_ASSIGNMENT_PATTERN.match(parts[0]):
        parts = parts[1:]
    if parts[:1] == ["sudo"]:
        parts = parts[1:]
    return parts[0].casefold() if parts else ""


def _looks_like_readme_run_command(command: str, *, repo_tokens: set[str]) -> bool:
    if not command or len(command) > 180:
        return False
    lowered = " ".join(command.casefold().split())
    if not lowered or lowered.startswith(("#", "//")):
        return False
    if any(lowered.startswith(prefix) for prefix in _README_INSTALL_ONLY_PREFIXES):
        return False
    first_token = _first_readme_command_token(command)
    if not first_token:
        return False
    if first_token.startswith("./"):
        return True
    if first_token in repo_tokens:
        return bool(_README_START_VERB_PATTERN.search(lowered))
    if first_token in _README_GENERIC_RUN_TOKENS:
        if first_token in {"npm", "pnpm", "yarn", "bun"} and " install" in lowered and not _README_START_VERB_PATTERN.search(lowered):
            return False
        if first_token == "npx" and repo_tokens and not any(token in lowered for token in repo_tokens):
            return False
        return True
    return False


def _score_readme_run_command(
    text: str,
    *,
    start: int,
    end: int,
    command: str,
    repo_tokens: set[str],
    execution_target: str,
) -> int:
    lowered_command = command.casefold()
    context = text[max(0, start - 700) : min(len(text), end + 250)].casefold()
    score = 0
    if any(token in lowered_command for token in repo_tokens):
        score += 35
    if _README_START_VERB_PATTERN.search(lowered_command):
        score += 25
    if any(token in context for token in _README_RUN_CONTEXT_TOKENS):
        score += 20
    if _is_remote_target(execution_target):
        if any(token in context for token in _README_REMOTE_CONTEXT_TOKENS):
            score += 18
        if any(token in context for token in _README_LOCAL_ONLY_TOKENS):
            score -= 30
    elif platform.system() != "Darwin" and any(token in context for token in ("macos", "brew install")):
        score -= 20
    return score


_GITHUB_REPO_URL_PATTERN = re.compile(
    r"^https?://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)
_GITHUB_README_BRANCHES = ("main", "master")
_GITHUB_README_NAMES = ("README.md", "README.rst", "README.txt", "README", "readme.md")
_GITHUB_README_TIMEOUT_SECONDS = 6.0


def _fetch_github_readme_text(repo_url: str) -> str:
    """Best-effort fetch of a public GitHub repo's raw README. Returns empty on any failure."""
    match = _GITHUB_REPO_URL_PATTERN.match(repo_url.strip())
    if match is None:
        return ""
    owner = match.group("owner")
    repo = match.group("repo")
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; DucklnReadmeFetcher/1.0)",
        "Accept": "text/plain, */*;q=0.1",
    }
    for branch in _GITHUB_README_BRANCHES:
        for name in _GITHUB_README_NAMES:
            url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{name}"
            request = Request(url, headers=headers)
            try:
                with urlopen(request, timeout=_GITHUB_README_TIMEOUT_SECONDS) as response:
                    body = response.read(120000)
            except (HTTPError, URLError, TimeoutError, OSError):
                continue
            try:
                return body.decode("utf-8", errors="ignore")
            except (UnicodeDecodeError, AttributeError):
                continue
    return ""


def _fetch_github_raw_file(repo_url: str, filename: str) -> str:
    """Best-effort fetch of a single raw file from a public GitHub repo (e.g.
    package.json, .nvmrc). Returns empty on any failure. Plan 77 Fix 1."""
    match = _GITHUB_REPO_URL_PATTERN.match(repo_url.strip())
    if match is None:
        return ""
    owner = match.group("owner")
    repo = match.group("repo")
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; DucklnReadmeFetcher/1.0)",
        "Accept": "text/plain, */*;q=0.1",
    }
    for branch in _GITHUB_README_BRANCHES:
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{filename}"
        request = Request(url, headers=headers)
        try:
            with urlopen(request, timeout=_GITHUB_README_TIMEOUT_SECONDS) as response:
                body = response.read(120000)
        except (HTTPError, URLError, TimeoutError, OSError):
            continue
        try:
            return body.decode("utf-8", errors="ignore")
        except (UnicodeDecodeError, AttributeError):
            continue
    return ""


_NODE_VERSION_HINT_RE = re.compile(
    r"node(?:\.js)?\s*(?:version)?\s*(?:>=?|≥|v)?\s*(\d{1,2})", re.IGNORECASE
)


def _int_or_zero(value: str | None) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _node_major_from_spec(spec: str) -> str | None:
    """Extract the minimum Node MAJOR from a version spec like '>=20.19', '^18',
    '20.x', 'v22.12.0', '18 || 20'. Returns the lowest acceptable major as a string.

    Each OR clause contributes its leading number (the MAJOR); the floor across
    clauses is the requirement. So '>=20.19' → 20 (not the minor 19), '18 || 20' → 18."""
    if not spec:
        return None
    clause_majors: list[int] = []
    for clause in re.split(r"\|\||\bor\b", spec, flags=re.IGNORECASE):
        m = re.search(r"(\d{1,2})", clause)
        if m:
            clause_majors.append(int(m.group(1)))
    if not clause_majors:
        return None
    return str(min(clause_majors))


# Plan 80 Fix 1: a framework's Node floor is usually transitive (declared by the
# dependency, not the app's own `engines`). Map a framework's declared MAJOR to the
# minimum Node MAJOR it needs. Each entry: (lower_bound_major, node_if_at_or_above, node_below).
_FRAMEWORK_NODE_REQUIREMENTS: dict[str, tuple[int, str, str]] = {
    "vite": (6, "20", "18"),          # Vite 6/7 require Node 20.19+
    "next": (15, "20", "18"),
    "@angular/cli": (19, "20", "18"),
    "@angular/core": (19, "20", "18"),
    "nuxt": (0, "18", "18"),
    "astro": (0, "18", "18"),
    "@sveltejs/kit": (0, "18", "18"),
}


def _framework_node_requirement(payload: dict) -> str | None:
    """Plan 80 Fix 1: infer the min Node MAJOR from framework dependencies declared
    in package.json (dependencies + devDependencies). Returns the HIGHEST required
    major across detected frameworks, or None."""
    if not isinstance(payload, dict):
        return None
    deps: dict[str, str] = {}
    for key in ("dependencies", "devDependencies"):
        section = payload.get(key)
        if isinstance(section, dict):
            deps.update({str(k).lower(): str(v) for k, v in section.items()})
    best: int = 0
    for name, (bound, at_or_above, below) in _FRAMEWORK_NODE_REQUIREMENTS.items():
        if name not in deps:
            continue
        dep_major = _node_major_from_spec(deps[name])
        required = at_or_above if (dep_major and int(dep_major) >= bound) else below
        best = max(best, int(required))
    return str(best) if best else None


def _detect_required_node_version(
    *,
    config_dir: Path,
    repo: RepoCatalogRecord,
    detected_files: tuple[str, ...],
    readme_excerpt: str = "",
) -> str | None:
    """Plan 77 Fix 1: determine the minimum required Node MAJOR for a repo without
    running anything. Reads package.json `engines.node`, then `.nvmrc`/`.node-version`,
    then a README hint. Prefers the local clone if present, else fetches over HTTP.
    Returns a major string (e.g. '20') or None when no requirement is declared."""
    if not (set(detected_files) & {"package.json", "pnpm-lock.yaml", "yarn.lock", "package-lock.json"}):
        return None

    def _read_local(name: str) -> str:
        try:
            project_dir = resolve_runtime_project_dir(config_dir, repo, execution_target="local")
            candidate = Path(project_dir) / name
            if candidate.exists():
                return candidate.read_text(encoding="utf-8", errors="ignore")
        except (OSError, ValueError):
            return ""
        return ""

    def _read(name: str) -> str:
        local = _read_local(name)
        if local:
            return local
        return _fetch_github_raw_file(repo.repo_url, name) if repo.repo_url else ""

    # 1. package.json engines.node (most explicit — wins).
    pkg_payload: dict | None = None
    pkg_text = _read("package.json")
    if pkg_text:
        try:
            pkg_payload = json.loads(pkg_text)
            engines = pkg_payload.get("engines") if isinstance(pkg_payload, dict) else None
            node_spec = engines.get("node") if isinstance(engines, dict) else None
            major = _node_major_from_spec(str(node_spec or ""))
            if major:
                return major
        except (ValueError, AttributeError):
            pkg_payload = None

    # 2. .nvmrc / .node-version
    for name in (".nvmrc", ".node-version"):
        raw = _read(name)
        major = _node_major_from_spec(raw.strip()) if raw else None
        if major:
            return major

    # 3. Plan 80 Fix 1: framework's transitive Node floor (vite/next/angular…).
    if pkg_payload is not None:
        framework_major = _framework_node_requirement(pkg_payload)
        if framework_major:
            return framework_major

    # 4. README prose hint ("requires Node 20", "Node.js >= 18")
    if readme_excerpt:
        m = _NODE_VERSION_HINT_RE.search(readme_excerpt)
        if m:
            return m.group(1)
    return None


def _node_version_install_step(*, required_major: str, execution_target: str) -> "RepoBringUpStep":
    """Plan 77 Fix 1: a setup step that installs the required Node MAJOR on the
    active target. Linux targets (vm/aws/gcp) use NodeSource (needs sudo → S3 in
    the plan); local macOS uses Homebrew. The step is visible in the plan so the
    user approves the correct version once."""
    is_linux_target = execution_target in _REMOTE_EXECUTION_TARGETS or platform.system() == "Linux"
    if is_linux_target:
        command = (
            f"curl -fsSL https://deb.nodesource.com/setup_{required_major}.x | sudo -E bash - "
            f"&& sudo apt-get install -y nodejs"
        )
    else:
        command = f"brew install node@{required_major} && brew link --overwrite --force node@{required_major}"
    return RepoBringUpStep(
        purpose=f"Install Node.js {required_major} (repo requires Node >={required_major})",
        command=command,
        verification_command="node --version",
        source="version-prereq",
    )


# --- Plan 80 Fix 5: generic, multi-ecosystem runtime-version detection ---------

def _xy_floor(text: str) -> str | None:
    """Extract a 'major.minor' floor (e.g. '3.11') from a version spec/string."""
    m = re.search(r"(\d+)\.(\d+)", text or "")
    return f"{m.group(1)}.{m.group(2)}" if m else None


def _xy_below(have: str | None, want: str | None) -> bool:
    """True when 'major.minor' `have` is strictly below `want`."""
    def _t(v: str | None) -> tuple[int, int]:
        m = re.search(r"(\d+)\.(\d+)", v or "")
        return (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    return _t(have) < _t(want)


def _tool_versions_map(text: str) -> dict[str, str]:
    """Parse an asdf/mise `.tool-versions` file into {tool: version}."""
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and not parts[0].startswith("#"):
            out[parts[0].lower()] = parts[1]
    return out


def _detect_required_python_version(*, config_dir: Path, repo: RepoCatalogRecord, detected_files: tuple[str, ...]) -> str | None:
    """Required Python 'major.minor' from pyproject `requires-python`, `.python-version`,
    setup.cfg `python_requires`, or `.tool-versions`. None when undeclared."""
    files = {str(f).lower() for f in detected_files}
    if not (files & {"pyproject.toml", "requirements.txt", "setup.py", "setup.cfg", ".python-version", ".tool-versions"}):
        return None

    def _read(name: str) -> str:
        return _read_repo_text_file(config_dir, repo, name)

    tv = _tool_versions_map(_read(".tool-versions"))
    if tv.get("python"):
        return _xy_floor(tv["python"])
    pv = _read(".python-version").strip()
    if pv:
        return _xy_floor(pv)
    for name, pattern in (
        ("pyproject.toml", r"requires-python\s*=\s*[\"']([^\"']+)[\"']"),
        ("setup.cfg", r"python_requires\s*=\s*([^\n]+)"),
        ("setup.py", r"python_requires\s*=\s*[\"']([^\"']+)[\"']"),
    ):
        text = _read(name)
        if text:
            m = re.search(pattern, text)
            if m:
                floor = _xy_floor(m.group(1))
                if floor:
                    return floor
    return None


def _detect_required_go_version(*, config_dir: Path, repo: RepoCatalogRecord, detected_files: tuple[str, ...]) -> str | None:
    """Required Go 'major.minor' from the `go 1.x` directive in go.mod or `.tool-versions`."""
    files = {str(f).lower() for f in detected_files}
    if "go.mod" not in files and ".tool-versions" not in files:
        return None
    tv = _tool_versions_map(_read_repo_text_file(config_dir, repo, ".tool-versions"))
    if tv.get("golang") or tv.get("go"):
        return _xy_floor(tv.get("golang") or tv.get("go") or "")
    gomod = _read_repo_text_file(config_dir, repo, "go.mod")
    if gomod:
        m = re.search(r"^\s*go\s+(\d+\.\d+)", gomod, re.MULTILINE)
        if m:
            return m.group(1)
    return None


def _detect_required_rust_version(*, config_dir: Path, repo: RepoCatalogRecord, detected_files: tuple[str, ...]) -> str | None:
    """Required Rust 'major.minor' from Cargo.toml `rust-version` or rust-toolchain[.toml]."""
    files = {str(f).lower() for f in detected_files}
    if not (files & {"cargo.toml", "rust-toolchain", "rust-toolchain.toml", ".tool-versions"}):
        return None
    tv = _tool_versions_map(_read_repo_text_file(config_dir, repo, ".tool-versions"))
    if tv.get("rust"):
        return _xy_floor(tv["rust"])
    for name, pattern in (
        ("rust-toolchain.toml", r"channel\s*=\s*[\"']([^\"']+)[\"']"),
        ("rust-toolchain", r"(\d+\.\d+(?:\.\d+)?)"),
        ("Cargo.toml", r"rust-version\s*=\s*[\"']([^\"']+)[\"']"),
    ):
        text = _read_repo_text_file(config_dir, repo, name)
        if text:
            m = re.search(pattern, text)
            if m:
                floor = _xy_floor(m.group(1))
                if floor:
                    return floor
    return None


def _read_repo_text_file(config_dir: Path, repo: RepoCatalogRecord, name: str) -> str:
    """Read a repo file (local clone first, then raw HTTP). Empty on failure."""
    try:
        project_dir = resolve_runtime_project_dir(config_dir, repo, execution_target="local")
        candidate = Path(project_dir) / name
        if candidate.exists():
            return candidate.read_text(encoding="utf-8", errors="ignore")
    except (OSError, ValueError):
        pass
    return _fetch_github_raw_file(repo.repo_url, name) if getattr(repo, "repo_url", "") else ""


def _python_version_install_step(*, required: str, execution_target: str) -> "RepoBringUpStep":
    is_linux = execution_target in _REMOTE_EXECUTION_TARGETS or platform.system() == "Linux"
    if is_linux:
        command = (
            f"sudo apt-get update && sudo apt-get install -y python{required} python{required}-venv "
            f"|| (curl -fsSL https://pyenv.run | bash && ~/.pyenv/bin/pyenv install -s {required} && ~/.pyenv/bin/pyenv local {required})"
        )
    else:
        # Plan 124: macOS parity with Linux — fall back to pyenv when brew can't
        # provide the pinned Python (or brew is missing), so a version-pinned repo
        # still gets its interpreter.
        command = (
            f"brew install python@{required} "
            f"|| (curl -fsSL https://pyenv.run | bash && ~/.pyenv/bin/pyenv install -s {required} && ~/.pyenv/bin/pyenv local {required})"
        )
    return RepoBringUpStep(
        purpose=f"Install Python {required} (repo requires Python {required})",
        command=command, verification_command=f"python{required} --version || python3 --version",
        source="version-prereq",
    )


def _go_version_install_step(*, required: str, execution_target: str) -> "RepoBringUpStep":
    is_linux = execution_target in _REMOTE_EXECUTION_TARGETS or platform.system() == "Linux"
    if is_linux:
        command = (
            f"curl -fsSL https://go.dev/dl/go{required}.0.linux-$(dpkg --print-architecture).tar.gz -o /tmp/go.tgz "
            f"&& sudo rm -rf /usr/local/go && sudo tar -C /usr/local -xzf /tmp/go.tgz "
            f"&& export PATH=$PATH:/usr/local/go/bin"
        )
    else:
        command = f"brew install go@{required} || brew install go"
    return RepoBringUpStep(
        purpose=f"Install Go {required} (repo's go.mod requires {required})",
        command=command, verification_command="go version", source="version-prereq",
    )


def _rust_version_install_step(*, required: str, execution_target: str) -> "RepoBringUpStep":
    command = (
        "command -v rustup >/dev/null 2>&1 || curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y; "
        f"rustup toolchain install {required} && rustup default {required}"
    )
    return RepoBringUpStep(
        purpose=f"Install Rust {required} (repo requires rust {required})",
        command=command, verification_command="rustc --version", source="version-prereq",
    )


def _read_repo_package_json(config_dir: Path, repo: RepoCatalogRecord) -> dict | None:
    """Plan 79 L3: read the repo's package.json (local clone first, then HTTP).
    Returns the parsed dict or None. Never raises."""
    text = ""
    try:
        project_dir = resolve_runtime_project_dir(config_dir, repo, execution_target="local")
        candidate = Path(project_dir) / "package.json"
        if candidate.exists():
            text = candidate.read_text(encoding="utf-8", errors="ignore")
    except (OSError, ValueError):
        text = ""
    if not text and getattr(repo, "repo_url", ""):
        text = _fetch_github_raw_file(repo.repo_url, "package.json")
    if not text:
        return None
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else None
    except (ValueError, TypeError):
        return None


def _package_manager_for(detected_files) -> str:
    """Plan 79 L3: the package manager implied by the present lockfile."""
    files = {str(f).lower() for f in (detected_files or ())}
    if "pnpm-lock.yaml" in files:
        return "pnpm"
    if "yarn.lock" in files:
        return "yarn"
    if "bun.lockb" in files:
        return "bun"
    return "npm"


# Plan 79 L3/L7: for "get it running" prefer a dev server (usually no build needed)
# over a production `start`, then other long-running script names.
_RUN_SCRIPT_PREFERENCE = ("dev", "develop", "start", "serve", "preview")

# Plan 87: build/package script names that produce artifacts (not a run) — never
# chosen as a desktop "run" command.
_DESKTOP_BUILD_TOKENS = ("build", "bundle", "package", "dist", "release", "publish")


def _detect_app_archetype(
    payload: object, detected_files, *, readme_excerpt: str = ""
) -> tuple[str, str]:
    """Plan 87: classify how the repo is meant to RUN, so the plan picks the right
    run command and access path. Returns (kind, flavor):
      kind   ∈ desktop_gui | web | cli | service | library
      flavor ∈ tauri | electron | "" (only set for desktop_gui)
    Deterministic + evidence-based; this feeds the README requirements spec's
    `archetype` field and is reconciled against it."""
    files = {str(f).lower() for f in (detected_files or ())}
    deps: dict[str, str] = {}
    scripts: dict = {}
    bin_field = None
    if isinstance(payload, dict):
        for key in ("dependencies", "devDependencies"):
            section = payload.get(key)
            if isinstance(section, dict):
                deps.update({str(k).lower(): str(v) for k, v in section.items()})
        maybe_scripts = payload.get("scripts")
        scripts = maybe_scripts if isinstance(maybe_scripts, dict) else {}
        bin_field = payload.get("bin")
    scripts_text = " ".join(f"{k} {v}" for k, v in scripts.items()).lower()
    readme = (readme_excerpt or "").lower()

    has_tauri = (
        any(d == "tauri" or d.startswith("@tauri-apps/") for d in deps)
        or "tauri.conf.json" in files
        or "src-tauri" in files
        or "tauri dev" in scripts_text
        or "tauri build" in scripts_text
    )
    has_electron = (
        any(d == "electron" or d == "electron-builder" or d.startswith("electron-") for d in deps)
        or "electron ." in scripts_text
        or "electron-builder" in scripts_text
        or bool(re.search(r"\belectron\b", scripts_text))
    )
    if has_tauri:
        return ("desktop_gui", "tauri")
    if has_electron:
        return ("desktop_gui", "electron")

    web_dep_markers = (
        "vite", "next", "react-scripts", "@angular", "vue", "nuxt", "@nuxt",
        "webpack", "astro", "svelte", "@sveltejs", "remix", "@remix-run",
        "gatsby", "react-dom",
    )
    has_web_dep = any(any(m in d for m in web_dep_markers) for d in deps)
    has_run_script = any(name in scripts for name in _RUN_SCRIPT_PREFERENCE)
    py_web = bool(files & {"manage.py"}) or any(
        k in readme for k in ("flask", "django", "fastapi", "uvicorn", "gunicorn", "rails", "streamlit")
    )
    if (has_web_dep and has_run_script) or py_web:
        return ("web", "")

    if isinstance(bin_field, (dict, str)) and bin_field:
        return ("cli", "")

    server_markers = ("express", "fastify", "koa", "@nestjs", "nestjs", "hapi")
    if any(any(m in d for m in server_markers) for d in deps) and has_run_script:
        return ("service", "")
    if has_run_script:
        return ("web", "")  # generic runnable web/dev server
    return ("library", "")


# --- Plan 87 Fix 0/0b: README comprehension → grounded requirements spec --------

# Canonical DB/cache engines + how to install/start/provision them on Debian/Ubuntu.
_ENGINE_ALIASES = {"postgresql": "postgres", "pg": "postgres", "postgis": "postgres", "mariadb": "mysql", "mongo": "mongodb"}
_DB_APT_PACKAGE = {"postgres": "postgresql", "mysql": "mysql-server", "redis": "redis-server", "mongodb": "mongodb"}
_DB_SERVICE_NAME = {"postgres": "postgresql", "mysql": "mysql", "redis": "redis-server", "mongodb": "mongod"}

_PROSE_DB_PATTERNS = (
    ("postgres", re.compile(r"\b(?:postgre?sql|postgres|postgis)\b(?:[^\n]{0,24}?\b(\d{1,2})(?:\.\d+)?\b)?", re.IGNORECASE)),
    ("mysql", re.compile(r"\b(?:mysql|mariadb)\b(?:[^\n]{0,24}?\b(\d{1,2})(?:\.\d+)?\b)?", re.IGNORECASE)),
    ("redis", re.compile(r"\bredis\b(?:[^\n]{0,24}?\b(\d{1,2})(?:\.\d+)?\b)?", re.IGNORECASE)),
    ("mongodb", re.compile(r"\bmongo(?:db)?\b(?:[^\n]{0,24}?\b(\d{1,2})(?:\.\d+)?\b)?", re.IGNORECASE)),
)


def _canonical_engine(name: str) -> str:
    low = str(name or "").lower()
    for key in _ENGINE_ALIASES:
        if key in low:
            return _ENGINE_ALIASES[key]
    for canon in _DB_APT_PACKAGE:
        if canon in low:
            return canon
    return low


def _detect_prose_services(readme_excerpt: str) -> tuple[tuple[str, str], ...]:
    """Plan 87 Fix 0b: detect DBs/caches a README declares in PROSE (e.g. "install
    Postgres 15", "requires Redis") so Duckln can provision them even without a
    docker-compose file. Returns (engine, version) with version "" when unstated."""
    text = readme_excerpt or ""
    found: dict[str, str] = {}
    for engine, pat in _PROSE_DB_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        version = (m.group(1) or "") if m.lastindex else ""
        found[engine] = version
    return tuple(found.items())


def _service_readiness_command(engine: str) -> str:
    """Plan 146 B1: WAIT for a just-started service to accept connections BEFORE migrations/
    the app run (so they don't race a not-yet-up DB). Bounded (~30s), engine-aware, with a
    generic TCP-port fallback. Best-effort (`|| true` semantics via the loop)."""
    engine = _canonical_engine(engine)
    probe = {
        "postgres": "pg_isready -h localhost -p 5432 >/dev/null 2>&1",
        "mysql": "mysqladmin ping -h localhost --silent >/dev/null 2>&1",
        "redis": "redis-cli ping 2>/dev/null | grep -q PONG",
        "mongodb": "(exec 3<>/dev/tcp/localhost/27017) 2>/dev/null",
    }.get(engine, "true")
    return f"for i in $(seq 1 30); do {probe} && break; sleep 1; done"


def _populate_env_defaults_command(env_keys: tuple[str, ...]) -> str | None:
    """Plan 146 B1: fill known env keys with safe DEV defaults in `.env` ONLY when absent —
    so the app boots without the user hand-filling every var. Secrets get a random dev value;
    service URLs point at the locally-provisioned service. A genuinely-external paid key
    (no safe default) is left out (an honest ask elsewhere). Signal-keyed on the key NAME."""
    def _default_for(key: str) -> str | None:
        k = key.upper()
        if k in ("REDIS_URL", "CACHE_URL"):
            return "redis://localhost:6379"
        if k in ("DATABASE_URL", "DB_URL", "POSTGRES_URL"):
            return "postgresql://postgres@localhost:5432/app"
        if any(t in k for t in ("SECRET_KEY", "SECRET", "JWT_SECRET", "SESSION", "ENCRYPTION_KEY")):
            return "duckln-dev-$(openssl rand -hex 16 2>/dev/null || echo devsecret0123456789)"
        if k in ("PORT",):
            return "8000"
        if k in ("NODE_ENV", "FLASK_ENV", "ENV", "ENVIRONMENT"):
            return "development"
        if k in ("DEBUG",):
            return "true"
        return None  # external/paid keys (API tokens etc.) → honest ask, never invented

    lines: list[str] = []
    for key in env_keys:
        val = _default_for(key)
        if val is None:
            continue
        # append only when the key isn't already present in .env
        lines.append(f"grep -q '^{key}=' .env 2>/dev/null || echo '{key}={val}' >> .env")
    if not lines:
        return None
    return "touch .env; " + "; ".join(lines)


# --- Plan 146 C2: repo runtime secrets (masked intake; never logged) -----------------
_SECRET_KEY_TOKENS = ("SECRET", "TOKEN", "PASSWORD", "PASSWD", "API_KEY", "APIKEY",
                      "PRIVATE_KEY", "CREDENTIAL", "DSN", "ACCESS_KEY", "CLIENT_SECRET")


def _is_secret_env_key(key: str) -> bool:
    """Plan 146 C2: a key whose VALUE is a secret (needs masked intake + must never be
    echoed/logged/persisted), keyed on the NAME."""
    k = (key or "").upper()
    return any(tok in k for tok in _SECRET_KEY_TOKENS)


def _secret_env_keys_needing_intake(env_keys: tuple[str, ...]) -> tuple[str, ...]:
    """Plan 146 C2: required SECRET keys with NO safe dev default → the user must be
    masked-prompted for them and the value injected into the runtime env (never logged).
    A SECRET_KEY/SESSION secret HAS a safe dev default (random), so it's excluded here."""
    out: list[str] = []
    for key in env_keys:
        if not _is_secret_env_key(key):
            continue
        if _populate_env_defaults_command((key,)) is not None:
            continue  # has a safe dev default (e.g. SECRET_KEY) → not an intake ask
        out.append(key)
    return tuple(out)


def _detected_env_example_files(project_dir: Path) -> tuple[str, ...]:
    """Plan 162 F2: the env-example filenames actually present in the cloned repo, so the
    env-key parser can read them (resume has no `inspection.detected_files`)."""
    return tuple(
        n for n in (".env.example", ".env.sample", ".env.template", ".env.dist", ".env.local.example")
        if (project_dir / n).exists()
    )


def _secret_upsert_command(secrets: dict[str, str]) -> str | None:
    """Plan 162 F2: UPSERT secret `KEY=value` lines into `.env` (drop an existing line for
    the key, then append). MUST be run via a NO-TRACE runner so the value is never displayed,
    logged, or persisted to the plan/executed list/thinking-log. Quote-safe."""
    if not secrets:
        return None
    parts = ["touch .env"]
    for key, val in secrets.items():
        pattern = shlex.quote(f"^{key}=")
        parts.append(
            f"grep -v {pattern} .env > .env.dkln 2>/dev/null; mv .env.dkln .env 2>/dev/null; "
            f"printf '%s=%s\\n' {shlex.quote(key)} {shlex.quote(val)} >> .env"
        )
    return "; ".join(parts)


# --- Plan 146 C1: concurrent multi-process apps (backend + frontend) ------------------
def _has_concurrent_run_script(scripts: dict | None) -> bool:
    """Plan 146 C1: True when the repo's dev/start script ALREADY orchestrates multiple
    processes (concurrently/npm-run-all/run-p/turbo) — then running that one script is the
    correct launch (Duckln doesn't multi-launch itself)."""
    for name in ("dev", "start"):
        body = str((scripts or {}).get(name, "")).lower()
        if any(tok in body for tok in ("concurrently", "npm-run-all", "run-p", "run-s", "turbo run", "foreman", "honcho")):
            return True
    return False


def _subdir_run_topology(project_dir: Path) -> tuple[tuple[str, str], ...]:
    """Plan 146 C1: detect a backend+frontend split (separate runnable subdirs with NO
    orchestrating root script) → ((name, subdir), ...) Duckln must launch BOTH and wire.
    Returns () when it's a single-process app (the common case)."""
    runnable: list[tuple[str, str]] = []
    for name in ("backend", "server", "api", "frontend", "client", "web", "ui"):
        sub = project_dir / name
        if not sub.is_dir():
            continue
        if (sub / "package.json").exists() or (sub / "requirements.txt").exists() \
           or (sub / "pyproject.toml").exists() or (sub / "manage.py").exists():
            runnable.append((name, name))
    # only a real MULTI-process topology (≥2 runnable subdirs)
    return tuple(runnable) if len(runnable) >= 2 else ()


# --- Plan 146 C5: service/process lifecycle teardown ----------------------------------
def _teardown_command(*, compose: bool = False, app_pids: tuple[str, ...] = (), tunnel_pids: tuple[str, ...] = ()) -> str | None:
    """Plan 146 C5: a SAFE teardown for everything Duckln started this session — compose
    services, app PIDs, tunnels — so a re-run doesn't collide on ports or leak processes.
    Never touches the user's pre-existing services. Idempotent (`|| true`)."""
    parts: list[str] = []
    if compose:
        parts.append("docker compose down --remove-orphans 2>/dev/null || true")
    for pid in (*app_pids, *tunnel_pids):
        p = str(pid).strip()
        if p.isdigit():
            parts.append(f"kill {p} 2>/dev/null || true")
    return "; ".join(parts) if parts else None


def _delete_target_command(*, execution_target: str, name: str | None = None,
                           instance_id: str | None = None, region: str | None = None) -> str | None:
    """Plan 150 F5: build the COMPLETE-delete command for a runtime target. Destructive +
    irreversible — the caller MUST confirm once first (F7). Returns None for a local target
    (Duckln can't delete the user's machine) or when the identifier is missing."""
    t = (execution_target or "").lower()
    if t == "vm" and name:
        return f"multipass delete {shlex.quote(name)} --purge"
    if t == "container" and name:
        # remove the container (force-stop + delete); anonymous volumes go with -v.
        return f"docker rm -f -v {shlex.quote(name)}"
    if t == "image" and name:
        # Plan 198 F3: remove a Docker IMAGE (force — even if tagged/used by a stopped container).
        return f"docker rmi -f {shlex.quote(name)}"
    if t == "aws" and instance_id:
        reg = f" --region {shlex.quote(region)}" if region else ""
        return f"aws ec2 terminate-instances --instance-ids {shlex.quote(instance_id)}{reg}"
    if t == "gcp" and name:
        zone = f" --zone {shlex.quote(region)}" if region else ""
        return f"gcloud compute instances delete {shlex.quote(name)}{zone} --quiet"
    return None  # local, or missing identifier


# --- Plan 146 C3: untrusted-code execution posture ------------------------------------
def _untrusted_local_notice(*, execution_target: str, repo_previously_trusted: bool) -> str | None:
    """Plan 146 C3: running a freshly-cloned repo's build/test/postinstall scripts on the
    user's REAL machine executes arbitrary third-party code. Surface a one-time notice
    recommending a sandbox (VM/container) for untrusted code. None on a sandbox target
    (VM/container/cloud) or for a repo Duckln has already run before."""
    if execution_target != "local" or repo_previously_trusted:
        return None
    return (
        "⚠ Setting this repo up runs its OWN build/test scripts — third-party code on your "
        "machine. For an untrusted repo, Duckln recommends a sandbox: set the target to a VM/"
        "container first. Continue on local only if you trust this repo."
    )


# --- Plan 146 C4: proactive right-sizing (size the target up front) -------------------
def _repo_is_resource_heavy(plan_steps, detected_files=()) -> bool:
    """Plan 146 C4: True when setup is heavy (a native/compiler build step, or an ML/GPU/
    native stack) → a FRESH target should be right-sized up front (feed `estimate_requirement`
    with heavy_build=True), not left to hit the disk/OOM loop reactively."""
    for s in plan_steps or ():
        if _is_heavy_build(getattr(s, "command", "") or ""):
            return True
    files = {str(f).lower() for f in (detected_files or ())}
    return bool(files & {"cargo.toml", "environment.yml", "cmakelists.txt", "build.gradle", "pom.xml"})


def _db_provision_steps(
    engine: str,
    version: str = "",
    *,
    needs_migrations: bool = False,
    migrate_command: str = "",
    seed_command: str = "",
    present_tools=(),
) -> tuple[tuple[str, str, str], ...]:
    """Plan 87 Fix 0b: ordered (title, command, safety_class) steps to install, start,
    and (for SQL DBs) create the app database + wire DATABASE_URL — for a service the
    README declares but no docker-compose provides. Idempotent; bounded."""
    engine = _canonical_engine(engine)
    pkg = _DB_APT_PACKAGE.get(engine)
    if not pkg:
        return ()
    present = {str(t).lower() for t in (present_tools or ())}
    steps: list[tuple[str, str, str]] = []
    if engine not in present and pkg not in present:
        label = f"{engine} {version}".strip()
        steps.append((f"Install {label}", f"sudo apt-get update && sudo apt-get install -y {pkg}", "S3"))
    svc = _DB_SERVICE_NAME.get(engine, engine)
    steps.append((f"Start {engine}", f"sudo service {svc} start || sudo systemctl start {svc} || true", "S2"))
    # Plan 146 B1: WAIT for readiness BEFORE creating the DB / migrating (don't race a
    # not-yet-up service).
    steps.append((f"Wait for {engine} to be ready", _service_readiness_command(engine), "S1"))
    if engine == "postgres":
        steps.append((
            "Create the app database + user and wire DATABASE_URL",
            "sudo -u postgres psql -tc \"SELECT 1 FROM pg_database WHERE datname='app'\" | grep -q 1 || "
            "sudo -u postgres psql -c \"CREATE DATABASE app;\"; "
            "echo 'DATABASE_URL=postgres://postgres@localhost:5432/app' >> .env",
            "S2",
        ))
    elif engine == "mysql":
        steps.append((
            "Create the app database and wire DATABASE_URL",
            "sudo mysql -e \"CREATE DATABASE IF NOT EXISTS app;\"; "
            "echo 'DATABASE_URL=mysql://root@localhost:3306/app' >> .env",
            "S2",
        ))
    if needs_migrations and migrate_command:
        steps.append(("Run database migrations", migrate_command, "S2"))
    # Plan 146 B1: seed the DB only when the repo DECLARES a seed (never invented).
    if seed_command:
        steps.append(("Seed the database", seed_command, "S2"))
    return tuple(steps)


def _detect_app_archetype_for_repo(*, config_dir: Path, repo, detected_files, readme_excerpt: str = "") -> tuple[str, str]:
    """Convenience: read package.json then classify. Returns (kind, flavor)."""
    detected = tuple(detected_files or ())
    payload = _read_repo_package_json(config_dir, repo) if "package.json" in {str(f).lower() for f in detected} else None
    return _detect_app_archetype(payload, detected, readme_excerpt=readme_excerpt)


def extract_requirements_spec(
    *,
    repo,
    config_dir: Path,
    inspection,
    detected_files=None,
    readme_excerpt: str | None = None,
    llm_complete: "Callable[[str, str], str] | None" = None,
) -> RequirementsSpec:
    """Plan 87 Fix 0: build a grounded RequirementsSpec by reading the README +
    manifests, BEFORE any recommendation. Deterministic baseline (the authoritative,
    file-grounded source); an optional LLM pass only ADDS items it can't contradict.
    Never raises — falls back to the deterministic baseline on any LLM failure."""
    detected = tuple(detected_files if detected_files is not None else (getattr(inspection, "detected_files", ()) or ()))
    readme = readme_excerpt if readme_excerpt is not None else (getattr(inspection, "readme_excerpt", "") or "")

    kind, flavor = _detect_app_archetype_for_repo(
        config_dir=config_dir, repo=repo, detected_files=detected, readme_excerpt=readme
    )

    runtimes: list[tuple[str, str]] = []
    try:
        node = _detect_required_node_version(
            config_dir=config_dir, repo=repo, detected_files=detected, readme_excerpt=readme
        )
        if node:
            runtimes.append(("node", str(node)))
    except Exception:
        pass
    for rt, detector in (
        ("python", _detect_required_python_version),
        ("go", _detect_required_go_version),
        ("rust", _detect_required_rust_version),
    ):
        try:
            v = detector(config_dir=config_dir, repo=repo, detected_files=detected)
        except Exception:
            v = None
        if v:
            runtimes.append((rt, str(v)))

    # Services: docker-compose (presence) reconciled with README prose (versions).
    svc: dict[str, str] = {}
    for name in _compose_services(config_dir=config_dir, repo=repo, detected_files=detected):
        svc.setdefault(_canonical_engine(name), "")
    for engine, version in _detect_prose_services(readme):
        engine = _canonical_engine(engine)
        if engine not in svc or (version and not svc.get(engine)):
            svc[engine] = version
    services = tuple(svc.items())

    try:
        env_keys = _required_env_vars(config_dir=config_dir, repo=repo, detected_files=detected)
    except Exception:
        env_keys = ()

    needs_migrations = bool(re.search(r"\bmigrat", readme, re.IGNORECASE))

    spec = RequirementsSpec(
        archetype=kind,
        archetype_flavor=flavor,
        runtimes=tuple(runtimes),
        services=services,
        env_keys=tuple(env_keys),
        needs_migrations=needs_migrations,
        notes=(),
        source="deterministic",
    )
    if llm_complete is not None:
        spec = _enrich_requirements_spec_with_llm(spec, readme_excerpt=readme, llm_complete=llm_complete)
    return spec


def _enrich_requirements_spec_with_llm(
    spec: RequirementsSpec, *, readme_excerpt: str, llm_complete: "Callable[[str, str], str]"
) -> RequirementsSpec:
    """Plan 87 Fix 0: let an LLM ADD requirements it understood from prose (extra env
    keys, manual notes) WITHOUT overriding the file-grounded baseline — grounding wins
    on any conflict. Redacts the README first. Returns the baseline on any failure."""
    from dataclasses import replace as _dc_replace

    system = (
        "You read a repository README and extract its runtime requirements as strict JSON. "
        "Return ONLY a JSON object with optional keys: env_keys (array of strings), "
        "services (array of {engine, version}), notes (array of strings). Do not invent secrets."
    )
    user = "README (redacted):\n" + redact_sensitive_data(readme_excerpt or "")[:6000]
    try:
        raw = llm_complete(system, user)
        data = json.loads(_strip_json_fence(raw))
        if not isinstance(data, dict):
            return spec
    except Exception:
        return spec  # honest fallback: keep the grounded deterministic spec

    env_keys = list(spec.env_keys)
    for k in data.get("env_keys", []) or []:
        k = str(k).strip()
        if k and k not in env_keys:
            env_keys.append(k)

    # LLM may name a service the files didn't — ADD it (with version) but never
    # override an engine the deterministic baseline already grounded from files.
    svc = dict(spec.services)
    for item in data.get("services", []) or []:
        if isinstance(item, dict):
            engine = _canonical_engine(str(item.get("engine", "")))
            version = str(item.get("version", "") or "")
            if engine and engine in _DB_APT_PACKAGE and engine not in svc:
                svc[engine] = version

    notes = list(spec.notes)
    for n in data.get("notes", []) or []:
        n = redact_sensitive_data(str(n)).strip()
        if n and n not in notes:
            notes.append(n)

    return _dc_replace(
        spec,
        env_keys=tuple(env_keys),
        services=tuple(svc.items()),
        notes=tuple(notes),
        source="llm+grounded",
    )


def _strip_json_fence(text: str) -> str:
    """Strip ```json fences / prose around a JSON object so json.loads can parse it."""
    s = str(text or "").strip()
    if "```" in s:
        s = re.sub(r"^.*?```(?:json)?\s*", "", s, flags=re.DOTALL)
        s = re.sub(r"\s*```.*$", "", s, flags=re.DOTALL)
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        return s[start : end + 1]
    return s


def _desktop_run_command_from_scripts(
    scripts: dict, package_manager: str, flavor: str
) -> str:
    """Plan 87: pick the DESKTOP run command for a Tauri/Electron app — the native
    shell (`tauri dev`/`electron .`), never the frontend-only `dev`/`vite` script.
    Falls back to the direct `npx` invocation when no desktop script exists."""
    kw = "tauri" if flavor == "tauri" else "electron"

    def _fmt(name: str, extra: str = "") -> str:
        base = (
            f"{package_manager} run {name}"
            if package_manager in ("npm", "bun")
            else f"{package_manager} {name}"
        )
        return f"{base} {extra}".strip()

    for name, body in scripts.items():
        n = str(name).lower()
        b = str(body).lower()
        if kw not in n and kw not in b:
            continue
        if any(tok in n or tok in b for tok in _DESKTOP_BUILD_TOKENS):
            continue  # a build/package script, not a run
        # A passthrough `"tauri": "tauri"` script needs the `dev` subcommand appended.
        if flavor == "tauri" and "dev" not in b:
            return _fmt(name, "dev")
        return _fmt(name)
    return "npx tauri dev" if flavor == "tauri" else "npx electron ."


def _run_command_from_scripts(
    scripts: object,
    package_manager: str,
    *,
    archetype: str = "",
    flavor: str = "",
) -> str | None:
    """Plan 79 L3: pick the best 'run' script and format it for the package manager.
    Plan 87: for a desktop app (archetype=desktop_gui) pick the native-shell run
    command, not the frontend-only `dev` script."""
    if not isinstance(scripts, dict):
        return None
    if archetype == "desktop_gui":
        return _desktop_run_command_from_scripts(scripts, package_manager, flavor)
    for name in _RUN_SCRIPT_PREFERENCE:
        if name in scripts:
            if package_manager in ("npm", "bun"):
                return f"{package_manager} run {name}"
            return f"{package_manager} {name}"  # pnpm/yarn: `pnpm dev`, `yarn dev`
    return None


# --- Plan 81: monorepo, compose-service, and codegen detection ----------------

_WORKSPACE_MARKERS = ("pnpm-workspace.yaml", "turbo.json", "nx.json", "lerna.json")


def _detect_project_subdir(*, config_dir: Path, repo: RepoCatalogRecord, detected_files) -> str:
    """Plan 81 Fix 2: in a monorepo, pick the runnable app subdirectory (e.g.
    `apps/web`). Returns "" for a single-package repo. Read-only / best-effort."""
    files = {str(f).lower() for f in (detected_files or ())}
    is_workspace = bool(files & {m.lower() for m in _WORKSPACE_MARKERS})
    root_pkg = _read_repo_package_json(config_dir, repo)
    if root_pkg is not None and root_pkg.get("workspaces"):
        is_workspace = True
    if not is_workspace:
        return ""
    # Probe the conventional app locations for one with a dev/start script.
    candidates = ("apps/web", "apps/app", "apps/frontend", "apps/client", "apps/api",
                  "packages/web", "packages/app", "web", "frontend", "client")
    for sub in candidates:
        text = _read_repo_text_file(config_dir, repo, f"{sub}/package.json")
        if not text:
            continue
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            continue
        if _run_command_from_scripts(payload.get("scripts"), "npm"):
            return sub
    return ""


_COMPOSE_SERVICE_KEYWORDS = ("postgres", "postgis", "mysql", "mariadb", "redis", "mongo", "rabbitmq", "kafka", "elasticsearch")


def _compose_services(*, config_dir: Path, repo: RepoCatalogRecord, detected_files) -> tuple[str, ...]:
    """Plan 81 Fix 3: parse docker-compose `services:` and return the backing-service
    names (postgres/redis/…) the app depends on. Lightweight indentation scan."""
    files = {str(f).lower(): str(f) for f in (detected_files or ())}
    compose_name = next((files[n] for n in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml") if n in files), None)
    if not compose_name:
        return ()
    text = _read_repo_text_file(config_dir, repo, compose_name)
    if not text:
        return ()
    services: list[str] = []
    in_services = False
    services_indent = None
    for raw in text.splitlines():
        if re.match(r"^\s*services\s*:\s*$", raw):
            in_services = True
            services_indent = len(raw) - len(raw.lstrip())
            continue
        if not in_services:
            continue
        if raw.strip() and not raw.startswith(" ") and not raw.startswith("\t"):
            break  # left the services block
        m = re.match(r"^(\s+)([A-Za-z0-9._-]+)\s*:\s*$", raw)
        if m and services_indent is not None and (len(m.group(1)) == services_indent + 2):
            services.append(m.group(2))
    # Keep only recognized backing services (by name or image keyword).
    keep: list[str] = []
    lowered_text = text.lower()
    for svc in services:
        if any(k in svc.lower() for k in _COMPOSE_SERVICE_KEYWORDS):
            keep.append(svc)
        elif any(k in lowered_text for k in _COMPOSE_SERVICE_KEYWORDS):
            # image keyword present somewhere — include the service that owns it heuristically
            keep.append(svc)
    return tuple(dict.fromkeys(keep))


def _codegen_steps(*, payload: dict | None, package_manager: str, run_command: str | None) -> tuple[tuple[str, str, str | None], ...]:
    """Plan 81 Fix 4: derive prisma/graphql codegen + build-before-start steps from
    package.json. Returns tuples of (title, command, verification)."""
    if not isinstance(payload, dict):
        return ()
    deps: dict[str, str] = {}
    for key in ("dependencies", "devDependencies"):
        section = payload.get(key)
        if isinstance(section, dict):
            deps.update({str(k).lower(): str(v) for k, v in section.items()})
    scripts = payload.get("scripts") if isinstance(payload.get("scripts"), dict) else {}
    out: list[tuple[str, str, str | None]] = []
    if "prisma" in deps or "@prisma/client" in deps:
        out.append(("Generate Prisma client", "npx prisma generate", None))
    if any("codegen" in str(k).lower() for k in scripts) or "@graphql-codegen/cli" in deps:
        gen_script = next((k for k in scripts if "codegen" in str(k).lower()), None)
        if gen_script:
            cmd = f"{package_manager} run {gen_script}" if package_manager in ("npm", "bun") else f"{package_manager} {gen_script}"
            out.append(("Run code generation", cmd, None))
    # Build-before-start: a production `start` that needs a prior `build`.
    rc = (run_command or "").lower()
    if rc and (" start" in rc or rc.endswith("start")) and "build" in scripts:
        build_cmd = f"{package_manager} run build" if package_manager in ("npm", "bun") else f"{package_manager} build"
        out.append(("Build the app", build_cmd, None))
    return tuple(out)


def _tauri_prebuild_steps(*, config_dir: Path, repo: RepoCatalogRecord, detected_files) -> tuple[tuple[str, str, str | None], ...]:
    """Plan 113: read the repo's `tauri.conf.json` and run its declared
    `beforeBuildCommand`/`beforeDevCommand` (which build sidecars/backends) BEFORE the
    run — generic to ANY Tauri repo, so a required prebuild artifact isn't skipped."""
    files = {str(f).lower() for f in (detected_files or ())}
    if "tauri.conf.json" not in files and "src-tauri" not in files:
        return ()
    text = _read_repo_text_file(config_dir, repo, "tauri.conf.json") or \
        _read_repo_text_file(config_dir, repo, "src-tauri/tauri.conf.json")
    if not text:
        return ()
    try:
        conf = json.loads(text)
    except (ValueError, TypeError):
        return ()
    build = conf.get("build") if isinstance(conf.get("build"), dict) else conf
    out: list[tuple[str, str, str | None]] = []
    for key in ("beforeBuildCommand", "beforeDevCommand"):
        cmd = build.get(key) if isinstance(build, dict) else None
        if isinstance(cmd, str) and cmd.strip():
            out.append((f"Tauri {key} (declared prebuild)", cmd.strip(), None))
            break  # one is enough; dev auto-runs beforeDev, so beforeBuild is the prebuild
    return tuple(out)


# package.json scripts whose NAME implies a required pre-run build artifact (generic).
_PREBUILD_SCRIPT_RE = re.compile(r"^(prepare|prebuild|presetup|generate|gen|sidecar|build:[a-z-]+|prebuild:[a-z-]+|setup:[a-z-]+)$", re.IGNORECASE)


def _declared_prebuild_script_steps(*, payload: dict | None, package_manager: str) -> tuple[tuple[str, str, str | None], ...]:
    """Plan 113: run a repo's declared lifecycle/prebuild scripts (`prepare`, `prebuild`,
    `generate`, `sidecar`, `build:*`…) before the run — keyed on script names, not repo.
    `postinstall` runs during install, so it's excluded here to avoid double-running."""
    if not isinstance(payload, dict):
        return ()
    scripts = payload.get("scripts") if isinstance(payload.get("scripts"), dict) else {}
    out: list[tuple[str, str, str | None]] = []
    for name in scripts:
        if _PREBUILD_SCRIPT_RE.match(str(name)):
            # Plan 129: a DECLARED prebuild script that isn't actually present on the
            # target (manifest divergence / optional script) must NO-OP, not fail the
            # whole bring-up. npm/pnpm `--if-present` runs it when present, else exits 0;
            # a present-but-failing script still fails (real errors not masked).
            if package_manager in ("npm", "pnpm"):
                cmd = f"{package_manager} run --if-present {name}"
            elif package_manager == "bun":
                cmd = f"bun run {name}"
            else:
                cmd = f"{package_manager} {name}"
            out.append((f"Run declared prebuild script `{name}`", cmd, None))
    return tuple(out)


_PYTHON_BACKEND_MANIFESTS = ("requirements.txt", "pyproject.toml", "setup.py")

# Plan 120: a single shell-conditional that runs ON THE TARGET (local/VM/container/
# cloud) at the repo root. For each immediate subdir holding a Python manifest, it
# creates a venv + installs deps IF one doesn't already exist — so a polyglot JS
# prebuild (e.g. `build:sidecar` needing `backend/.venv/bin/python`) finds it ready.
# Repo- AND target-agnostic (keyed on manifests, never a repo name); a no-op when
# there's no Python subdir; idempotent (skips an existing `.venv`).
_SECONDARY_PY_SETUP_CMD = (
    'for d in */; do d="${d%/}"; '
    'if [ -f "$d/requirements.txt" ] || [ -f "$d/pyproject.toml" ] || [ -f "$d/setup.py" ]; then '
    # Plan 127: completion-aware — skip ONLY when a successful run marked the venv
    # done. A half-built venv (no marker) is removed + rebuilt, not skipped-broken.
    'if [ ! -e "$d/.venv/.duckln-deps-ok" ]; then '
    '( cd "$d" && rm -rf .venv && python3 -m venv .venv && .venv/bin/pip install --upgrade pip && '
    # Plan 148 F5: a deps-install failure must NOT break the chain (it did: an editable build
    # of a flat-layout backend fails with setuptools "Multiple top-level packages…", which
    # then SKIPPED the PyInstaller install below → `build:sidecar` failed "No module named
    # PyInstaller"). Make every install best-effort: `-e .` → fall back to non-editable `.`
    # → continue regardless, so the build tool still lands.
    '{ if [ -f requirements.txt ]; then .venv/bin/pip install -r requirements.txt || true; fi; '
    'if [ -f pyproject.toml ] || [ -f setup.py ]; then '
    '.venv/bin/pip install -e . 2>/dev/null || .venv/bin/pip install . 2>/dev/null || true; fi; } && '
    # Plan 122: also install declared build/dev requirement files (best-effort) so
    # build tools like PyInstaller land before the prebuild that needs them.
    '{ for r in requirements-dev.txt requirements-build.txt dev-requirements.txt build-requirements.txt; do '
    'if [ -f "$r" ]; then .venv/bin/pip install -r "$r" || true; fi; done; } && '
    # Plan 134 F3: PLAN IT RIGHT THE FIRST TIME. A polyglot prebuild (e.g. `build:sidecar`)
    # commonly invokes a Python packager — PyInstaller — from this backend venv, but the
    # tool is almost never declared in requirements (it lives inside a JS build script).
    # A PyInstaller-shaped `*.spec` in THIS subdir is the signal: install PyInstaller up
    # front so the prebuild doesn't fail with "No module named PyInstaller". Signal-keyed
    # (the spec), repo-agnostic, idempotent (import-check first), best-effort.
    '{ if ls *.spec >/dev/null 2>&1 && grep -qiE "pyinstaller|Analysis\\(" *.spec 2>/dev/null; then '
    '.venv/bin/python -c "import PyInstaller" 2>/dev/null || .venv/bin/pip install pyinstaller || true; fi; } && '
    'touch .venv/.duckln-deps-ok ); '
    'fi; fi; done'
)


def _secondary_python_setup_steps(detected_files, *, has_prebuild: bool) -> tuple[tuple[str, str, str | None], ...]:
    """Plan 120: emit ONE target-side step that sets up any Python backend subdir's
    venv BEFORE the JS prebuild that depends on it. Unlike Plan 119's host-side scan
    (which missed VM/container/cloud repos), this detects the subdir at runtime on
    whatever target the step executes on. Emitted only for a Node-primary repo that
    actually declares a prebuild/codegen step; returns () otherwise."""
    top = {str(f).lower() for f in (detected_files or ())}
    if "package.json" not in top or not has_prebuild:
        return ()
    return ((
        "Set up Python backend venv before prebuild",
        _SECONDARY_PY_SETUP_CMD,
        None,
    ),)


# --- Plan 156 P2: is this repo non-trivial (so reasoning is REQUIRED to plan it)? ---------
_NONTRIVIAL_BUILD_SURFACE = {
    "package.json", "cargo.toml", "tauri.conf.json", "makefile", "cmakelists.txt",
    "build.gradle", "pom.xml", "go.mod", "pyproject.toml",
}


def _planning_is_nontrivial(detected_files, *, low_confidence_family: bool = False) -> bool:
    """Plan 156 P2: True when the repo is NOT trivially-simple — it has a build surface, an
    unknown/low-confidence family, or ≥3 manifests. A non-trivial repo REQUIRES reasoning to
    plan, so with no reachable model Duckln honest-stops (never a deterministic-only plan).
    A trivially-simple known repo (one manifest, no build) is False."""
    files_l = {str(f).lower() for f in (detected_files or ())}
    return bool(files_l & _NONTRIVIAL_BUILD_SURFACE) or len(tuple(detected_files or ())) >= 3 or low_confidence_family


# --- Plan 153 A3: does this repo NEED a GPU/CUDA? (deps/README/family signals) ----------
_GPU_REQUIRED_DEPS = (
    "bitsandbytes", "flash-attn", "flash_attn", "xformers", "deepspeed", "vllm",
    "auto-gptq", "autoawq", "apex", "tinycudann", "nvidia-", "cupy", "faiss-gpu",
)


def _repo_needs_gpu(*, requirements_text: str = "", readme: str = "", repo_family=None) -> bool:
    """Plan 153 A3: True when the repo HARD-needs an NVIDIA GPU/CUDA — keyed on GPU-only deps,
    explicit README statements, or the diffusion-heavy family. Used to route to a GPU VM/cloud
    (or honest-block) on a no-accelerator target instead of installing a doomed CPU build."""
    blob = f"{requirements_text}\n{readme}".lower()
    if any(dep in blob for dep in _GPU_REQUIRED_DEPS):
        return True
    if any(p in blob for p in ("requires cuda", "cuda required", "gpu required", "requires a gpu", "nvidia gpu required")):
        return True
    try:
        if repo_family is not None and getattr(repo_family, "value", "") == "diffusion_heavy":
            return True
    except Exception:
        pass
    return False


# --- Plan 153 B: conda/mamba environment setup (environment.yml repos) -------------------
def _with_context_hint(message: str) -> str:
    """Plan 164 F5: append the honest context-overflow hint when the last prompt is near/over
    the model's context — so a small-free-model failure reads as a SIZE problem, not a false
    "can't connect". No-op when the context window is unknown or the prompt fit comfortably."""
    try:
        from duckln.usage_meter import context_overflow_hint

        hint = context_overflow_hint()
    except Exception:
        hint = ""
    return f"{message} {hint}".strip() if hint else message


def _repo_uses_conda(detected_files) -> bool:
    """Plan 153 B1: True when the repo ships a conda environment file."""
    top = {str(f).lower() for f in (detected_files or ())}
    return bool(top & {"environment.yml", "environment.yaml"})


def _conda_env_name_from_yaml(text: str, *, default: str = "duckln-env") -> str:
    """Plan 153 B2: read `name:` from environment.yml so run/verify target the right env."""
    for line in (text or "").splitlines():
        s = line.strip()
        if s.startswith("name:"):
            name = s.split(":", 1)[1].strip().strip("'\"")
            if name:
                return name
    return default


def _conda_setup_command(env_name: str, *, env_file: str = "environment.yml") -> str:
    """Plan 153 B1/B4: ensure conda/mamba is present (install Miniforge headless if absent),
    then create the env from environment.yml — IDEMPOTENT (skip when the env already exists;
    `mamba` preferred, `conda` fallback). Completion-aware so a healthy env isn't recreated."""
    return (
        # ensure a conda/mamba on PATH; install Miniforge non-interactively if neither exists.
        'if ! command -v mamba >/dev/null 2>&1 && ! command -v conda >/dev/null 2>&1; then '
        'curl -fsSL -o /tmp/miniforge.sh "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-$(uname)-$(uname -m).sh" '
        '&& bash /tmp/miniforge.sh -b -p "$HOME/miniforge3" && export PATH="$HOME/miniforge3/bin:$PATH"; fi; '
        'export PATH="$HOME/miniforge3/bin:$PATH"; '
        'CONDA=$(command -v mamba || command -v conda); '
        # idempotent: only create when the env isn't already present.
        f'if ! "$CONDA" env list 2>/dev/null | grep -qE "(^|/){shlex.quote(env_name)}\\b"; then '
        f'"$CONDA" env create -n {shlex.quote(env_name)} -f {shlex.quote(env_file)}; fi'
    )


def _conda_run_prefix(env_name: str) -> str:
    """Plan 153 B2: run a command INSIDE the conda env (not the venv path)."""
    return f'conda run -n {shlex.quote(env_name)}'


def _conda_run_prefix_for_repo(*, config_dir: Path, repo, detected_files) -> str:
    """Plan 155 F2: the `conda run -n <env> ` prefix for a conda repo (env name from its
    environment.yml), or "" for a non-conda repo. Used to actually RUN/verify through the
    env the Plan-153 step created (previously `_conda_run_prefix` was defined but never used)."""
    if not _repo_uses_conda(detected_files):
        return ""
    text = (
        _read_repo_text_file(config_dir, repo, "environment.yml")
        or _read_repo_text_file(config_dir, repo, "environment.yaml")
        or ""
    )
    return _conda_run_prefix(_conda_env_name_from_yaml(text)) + " "


# --- Plan 153 A2: declared DL frameworks → install the build matching the accelerator -----
def _detect_dl_frameworks(requirements_text: str = "", pyproject_text: str = "") -> tuple[str, ...]:
    """Plan 153 A2: which DL frameworks the repo declares (torch/tensorflow/jax), from its
    requirements/pyproject — so Duckln installs the build matching the target accelerator."""
    blob = f"{requirements_text}\n{pyproject_text}".lower()
    found: list[str] = []
    if re.search(r"(^|[\s\"'=\[])torch\b", blob) or "pytorch" in blob:
        found.append("torch")
    if re.search(r"\btensorflow\b", blob):
        found.append("tensorflow")
    if re.search(r"(^|[\s\"'=\[])jax\b", blob):
        found.append("jax")
    # Plan 175 A1: ONNX Runtime (inference) — install the accelerator-matched wheel (gpu vs cpu).
    if re.search(r"\bonnxruntime(-gpu)?\b", blob):
        found.append("onnxruntime")
    return tuple(found)


def _ml_framework_install_steps(frameworks: tuple[str, ...], *, accelerator: str,
                                cuda_version: str = "", venv_pip: str = "pip") -> tuple[tuple[str, str, str], ...]:
    """Plan 153 A2: a matched-wheel install step per declared framework, AFTER the generic
    deps install so the accelerator-correct build wins. Reuses `diagnostics.framework_install_command`."""
    from duckln.diagnostics import framework_install_command

    steps: list[tuple[str, str, str]] = []
    for fw in frameworks:
        cmd = framework_install_command(fw, accelerator, cuda_version, pip=venv_pip)
        if cmd:
            steps.append((f"Install the {accelerator}-matched {fw} build", cmd, "S2"))
    return tuple(steps)


# --- Plan 154: Apple MLX / Metal — native-macOS-only; surface it + guard a Linux target ----
_METAL_FRAMEWORKS = ("mlx-lm", "mlx-vlm", "mlx-data", "mlx", "jax-metal", "tensorflow-metal")


def _detect_metal_frameworks(requirements_text: str = "", pyproject_text: str = "") -> tuple[str, ...]:
    """Plan 154 F1: the Apple-Metal frameworks the repo declares (MLX family + jax-metal/
    tensorflow-metal), from its requirements/pyproject. Signal-keyed; longest names first so
    `mlx-lm` isn't shadowed by `mlx`."""
    blob = f"{requirements_text}\n{pyproject_text}".lower()
    found: list[str] = []
    for pkg in _METAL_FRAMEWORKS:
        if re.search(rf"(^|[\s\"'=\[]){re.escape(pkg)}\b", blob) and not any(pkg in f for f in found):
            found.append(pkg)
    return tuple(found)


def _metal_accelerator_label(metal_frameworks: tuple[str, ...]) -> str:
    """Plan 154 F2: the accelerator label for a local Apple-Silicon target — name MLX when the
    repo declares it (MLX = Apple's native framework; MPS = torch-on-Metal). Empty → MPS line."""
    if any(f.startswith("mlx") for f in metal_frameworks):
        return "Apple Metal (MLX) — running natively on this Mac"
    if metal_frameworks:
        return "Apple Metal — running natively on this Mac"
    return "Apple Silicon GPU (MPS)"


def _repo_needs_apple_metal(*, requirements_text: str = "", readme: str = "", pyproject_text: str = "") -> bool:
    """Plan 154 F4: True when the repo needs Apple Metal/MLX — declared MLX/Metal framework, or
    an explicit README statement. Symmetric inverse of `_repo_needs_gpu` (Metal→local, not CUDA→cloud)."""
    if _detect_metal_frameworks(requirements_text, pyproject_text):
        return True
    blob = f"{requirements_text}\n{readme}".lower()
    return any(p in blob for p in ("apple silicon", "apple metal", "requires metal", " mlx ", "mlx framework", "metal performance"))


def _apple_metal_routing_note(*, needs_metal: bool, execution_target: str) -> str | None:
    """Plan 154 F4: warn + recommend `local` when an MLX/Metal repo is aimed at a Linux target
    (vm/docker/aws/gcp) — Metal only exists on the macOS host. Soft (recommend, never block)."""
    if needs_metal and (execution_target or "local").lower() != "local":
        return (
            "⚠ This repo uses Apple MLX/Metal, which only works on the local macOS host — a "
            "Linux VM/container/cloud has no Metal, so it will fail or fall back to CPU. "
            "Recommend setting the target to local (/target local)."
        )
    return None


def _conda_env_setup_steps(detected_files, *, environment_yml_text: str = "") -> tuple[tuple[str, str, str | None], ...]:
    """Plan 153 B1: emit ONE conda env-create step when the repo uses conda; () otherwise.
    Sibling of `_secondary_python_setup_steps`; pip-venv stays the default for non-conda repos."""
    if not _repo_uses_conda(detected_files):
        return ()
    env_name = _conda_env_name_from_yaml(environment_yml_text)
    env_file = "environment.yml" if "environment.yml" in {str(f).lower() for f in detected_files} else "environment.yaml"
    return ((f"Create the conda environment '{env_name}' from {env_file}",
             _conda_setup_command(env_name, env_file=env_file), "S2"),)


def _is_marker_guarded_setup(command: str) -> bool:
    """Plan 144 F3: a setup step guarded by the `.duckln-deps-ok` completion marker (the
    Python backend venv setup). Such a step is a cheap idempotent no-op when the venv is
    already complete, and it REBUILDS the venv if it was deleted — so on resume it must be
    re-run, never permanently skipped. A skipped-but-deleted venv is exactly what stranded
    the prebuild at 'venv not found' in the venv ↔ PyInstaller oscillation."""
    return ".duckln-deps-ok" in (command or "")


def _run_post_setup_verification(
    *,
    repo: RepoCatalogRecord,
    plan: RepoBringUpPlan,
    project_dir: Path,
    runner: ControlledCommandRunner,
    display: Callable[[str], None],
    started_at: float,
    config_dir: Path,
) -> RepoVerificationOutcome:
    """Run a bounded verification pass and report back to the supervisor."""

    display(
        _agent_phase_status(
            "Verification agent",
            "Running a bounded smoke check for the prepared repo...",
            phase_index=4,
            phase_total=5,
            started_at=started_at,
        )
    )
    checks_run = ["verified managed project directory exists"]
    project_dir_text = str(project_dir)
    if plan.execution_target == "local":
        if not project_dir.exists():
            return RepoVerificationOutcome(
                verified=False,
                summary="The managed project directory is missing after setup.",
                checks_run=tuple(checks_run),
                next_step="Re-run the repo bring-up after checking the clone step.",
                confidence="high",
            )
    else:
        directory_check = _wrap_command_for_execution_target(
            config_dir=config_dir,
            execution_target=plan.execution_target,
            command="test -d .",
            cwd=project_dir_text,
        )[0]
        if not directory_check:
            return RepoVerificationOutcome(
                verified=False,
                summary=f"Duckln could not resolve the active {plan.execution_target} target to verify the repo directory.",
                checks_run=tuple(checks_run),
                next_step="Reconnect the active target and rerun the repo bring-up.",
                confidence="high",
            )
        directory_result = runner.run(directory_check)
        if directory_result.exit_code != 0 or directory_result.timed_out:
            return RepoVerificationOutcome(
                verified=False,
                summary="The managed project directory is missing after setup.",
                checks_run=tuple(checks_run),
                next_step="Re-run the repo bring-up after checking the clone step.",
                confidence="high",
            )

    verification_commands: list[str] = []
    if _has_or_expects_project_venv(plan, project_dir):
        existing_python = _resolve_existing_venv_python(project_dir)
        if existing_python:
            verification_commands.extend((
                venv_python_test_command(execution_target=plan.execution_target),
                f"{existing_python} -m pip --version",
            ))
        else:
            verification_commands.extend((
                venv_python_test_command(execution_target=plan.execution_target),
                f"{venv_python_path(execution_target=plan.execution_target)} -m pip --version",
            ))
    for step in plan.steps:
        if step.verification_command:
            verification_commands.append(step.verification_command)
    inferred_verify = _infer_verify_command(plan, project_dir)
    if inferred_verify:
        verification_commands.append(inferred_verify)
    smoke_command = _safe_verification_smoke_command(repo=repo, plan=plan, project_dir=project_dir)
    if smoke_command:
        verification_commands.append(smoke_command)
    verification_candidates = tuple(dict.fromkeys(command for command in verification_commands if command))
    if not verification_candidates:
        checks_run.append("verified repository files are present")
        return RepoVerificationOutcome(
            verified=True,
            summary="Duckln verified the managed project directory and documented entrypoint prerequisites.",
            checks_run=tuple(checks_run),
            confidence="medium",
        )

    for verification_command in verification_candidates:
        wrapped_command, _metadata = _wrap_command_for_execution_target(
            config_dir=config_dir,
            execution_target=plan.execution_target,
            command=verification_command,
            cwd=project_dir_text,
        )
        if not wrapped_command:
            return RepoVerificationOutcome(
                verified=False,
                summary=f"Duckln could not resolve the active {plan.execution_target} target to run a verification command.",
                checks_run=tuple(checks_run),
                verification_command=verification_command,
                next_step="Reconnect the active target and rerun the bounded verification step.",
                confidence="high",
            )
        result = runner.run(
            wrapped_command,
            cwd=None if plan.execution_target in _REMOTE_EXECUTION_TARGETS else project_dir_text,
        )
        if verification_command.startswith("test -x ") or verification_command.startswith("if exist "):
            checks_run.append("verified project virtualenv exists")
        elif verification_command.endswith("-m pip --version"):
            checks_run.append("verified pip is available inside the virtualenv")
        else:
            checks_run.append(f"verified `{verification_command}`")
        if result.exit_code != 0 or result.timed_out:
            return RepoVerificationOutcome(
                verified=False,
                summary="Duckln prepared the repo, but the bounded verification path still failed.",
                checks_run=tuple(checks_run),
                verification_command=verification_command,
                next_step="Inspect the failing verification command or repair the missing dependency path before a full run.",
                confidence="medium",
            )

    binary_check = _verify_installed_binary_on_remote(
        plan=plan,
        runner=runner,
        config_dir=config_dir,
        display=display,
    )
    if binary_check is not None:
        passed, bin_name, summary = binary_check
        checks_run.append(f"verified `{bin_name}` is on PATH" if passed else f"`{bin_name}` is NOT on PATH after install")
        if not passed:
            return RepoVerificationOutcome(
                verified=False,
                summary=summary,
                checks_run=tuple(checks_run),
                verification_command=f"command -v {bin_name}",
                next_step=(
                    f"Install {bin_name} so it is reachable on PATH inside the {plan.execution_target} target "
                    "(e.g. `npm install -g .`, `pip install .`, `cargo install --path .`, or the project's documented install step)."
                ),
                confidence="high",
            )

    return RepoVerificationOutcome(
        verified=True,
        summary="Duckln verified the managed project directory and the bounded dependency/runtime checks for this repo.",
        checks_run=tuple(checks_run),
        verification_command=verification_candidates[-1],
        confidence="high",
    )


def _verify_installed_binary_on_remote(
    *,
    plan: RepoBringUpPlan,
    runner: ControlledCommandRunner,
    config_dir: Path,
    display: Callable[[str], None],
) -> tuple[bool, str, str] | None:
    """Check that the repo's expected CLI binary is on PATH inside a remote target.

    Returns (passed, binary_name, summary) when a check was attempted, or None
    when no binary name can be inferred or the target is local. This closes the
    NOT_INSTALLED → INSTALLED_HEALTHY gap for npm/cargo/go CLI repos that get
    cloned and dep-installed but not registered on PATH."""
    if plan.execution_target not in _REMOTE_EXECUTION_TARGETS:
        return None
    bin_name = _infer_remote_binary_name(plan)
    if not bin_name:
        return None
    wrapped, _meta = _wrap_command_for_execution_target(
        config_dir=config_dir,
        execution_target=plan.execution_target,
        command=f"command -v {shlex.quote(bin_name)} 2>/dev/null",
        cwd=None,
    )
    if wrapped is None:
        return None
    try:
        result = runner.run(wrapped)
    except Exception:
        return None
    if result.timed_out:
        return None
    explicit_missing = result.exit_code != 0
    if not explicit_missing:
        display(f"✓ INSTALLED_HEALTHY: `{bin_name}` is on PATH inside the {plan.execution_target}.")
        return True, bin_name, f"`{bin_name}` is installed and reachable on PATH."
    display(
        f"✗ NOT_INSTALLED: `{bin_name}` is missing from PATH inside the {plan.execution_target} after the install plan ran. "
        "The plan completed setup steps (e.g. clone, dependency install) but did not register the CLI binary."
    )
    return False, bin_name, (
        f"Setup ran, but `{bin_name}` is not on PATH inside the {plan.execution_target}. "
        "The repo likely needs a global-install step (e.g. `npm install -g .`) before it can be run."
    )


def _infer_remote_binary_name(plan: RepoBringUpPlan) -> str | None:
    """Infer the CLI binary name expected to land on PATH after install."""
    bin_name = _npm_global_binary_name(plan.project_dir)
    if bin_name:
        return bin_name
    for step in plan.steps:
        verify = (step.verification_command or "").strip()
        if verify.startswith("command -v "):
            tail = verify.split("command -v ", 1)[1].strip()
            candidate = tail.split()[0].strip("`'\"")
            if candidate:
                return candidate
    raw = plan.repo.name.strip()
    if not raw:
        return None
    return raw.lower().lstrip("@").split("/")[-1]


def _repo_test_command(project_dir: Path) -> str | None:
    """Plan 146 B2: the repo's OWN declared test/smoke — a REAL "does it work" signal
    (not a `--help`/presence no-op). Returns None when the repo declares no usable test
    surface (so the caller falls back). Signal-keyed, repo-agnostic, cheap to detect."""
    # Python: a pytest surface (tests dir or a pytest/tox config).
    py_test = (
        (project_dir / "tests").is_dir()
        or (project_dir / "test").is_dir()
        or (project_dir / "pytest.ini").exists()
        or (project_dir / "tox.ini").exists()
        or (project_dir / "conftest.py").exists()
    )
    _py_project = (
        (project_dir / ".venv").exists()
        or (project_dir / "requirements.txt").exists()
        or (project_dir / "pyproject.toml").exists()
        or (project_dir / "setup.py").exists()
    )
    if py_test and _py_project:
        py = _python_runtime_executable(project_dir)
        return f"{py} -m pytest -q --maxfail=1"
    # Node: a NON-trivial "test" script (skip the npm default "no test specified").
    pkg = project_dir / "package.json"
    if pkg.exists():
        try:
            import json as _json
            scripts = (_json.loads(pkg.read_text(encoding="utf-8")) or {}).get("scripts", {}) or {}
            test_script = str(scripts.get("test", "")).lower()
            if test_script and "no test specified" not in test_script and "exit 1" not in test_script:
                pm = _preferred_node_package_manager(project_dir)
                return f"{pm} test" if pm != "npm" else "npm test"
        except Exception:
            pass
    # Go / Rust: their standard test runners.
    if (project_dir / "go.mod").exists():
        return "go test ./..."
    if (project_dir / "Cargo.toml").exists():
        return "cargo test --quiet"
    return None


def _http_outcome_check_command(served_url: str) -> str | None:
    """Plan 146 B2: a real HTTP outcome probe — the app must actually RESPOND (2xx/3xx),
    not just print a banner. `curl -fsS` fails on a non-2xx, so a 500/refused → real failure."""
    if not served_url:
        return None
    return f"curl -fsS -o /dev/null --max-time 10 --retry 3 --retry-delay 2 {shlex.quote(served_url)}"


def _outcome_check_command(*, served_url: str | None, project_dir: Path) -> str | None:
    """Plan 146 B2: the single best "it actually works" check — an HTTP probe for a served
    web/service app, else the repo's own tests. None → caller uses the legacy smoke."""
    if served_url:
        return _http_outcome_check_command(served_url)
    return _repo_test_command(project_dir)


def _safe_verification_smoke_command(
    *,
    repo: RepoCatalogRecord,
    plan: RepoBringUpPlan,
    project_dir: Path,
) -> str | None:
    # Plan 146 B2: prefer a REAL outcome check (the repo's own tests) over a near-no-op
    # `--help`/presence check, so "verified" means the app actually works.
    real = _repo_test_command(project_dir)
    if real:
        return real
    inferred = _infer_verify_command(plan, project_dir)
    if inferred is None:
        return None
    lowered = inferred.lower()
    if "--help" in lowered or "-h" == lowered.strip():
        return inferred
    if "pip --version" in lowered:
        return inferred
    return None


def _build_setup_outcome(
    *,
    repo: RepoCatalogRecord,
    plan: RepoBringUpPlan,
    project_dir: Path,
    executed_commands: tuple[str, ...],
    verification: RepoVerificationOutcome,
) -> RepoSetupOutcome:
    knowledge = inspect_repo_for_bringup(
        repo,
        project_dir,
        context_service=AgentContextService(),
    ).repo_knowledge
    environment_path = None
    if _has_or_expects_project_venv(plan, project_dir):
        environment_path = str(project_dir / ".venv")
    run_command = _infer_start_command(plan, project_dir)
    verify_command = _infer_verify_command(plan, project_dir)
    manual_command = _infer_manual_command(plan, project_dir)
    runtime_kind = _infer_runtime_kind(plan, project_dir)
    auth_requirements = _normalize_auth_requirements(
        knowledge.auth_requirements if knowledge is not None else (),
        knowledge.metadata if knowledge is not None else {},
    )
    missing_auth_variables = _missing_required_auth_variables(auth_requirements)
    access_hint = _infer_access_hint(repo, plan, project_dir)
    changed_items = _describe_changed_items(project_dir, executed_commands)
    removal_hint = _build_removal_hint(project_dir, environment_path)
    return RepoSetupOutcome(
        install_location=str(project_dir),
        environment_path=environment_path,
        run_command=run_command,
        verify_command=verify_command,
        manual_command=manual_command,
        runtime_kind=runtime_kind,
        stack_family=knowledge.stack_family if knowledge is not None else None,
        auth_requirements=auth_requirements,
        missing_auth_variables=missing_auth_variables,
        access_hint=access_hint,
        verification=verification,
        removal_hint=removal_hint,
        changed_items=changed_items,
    )


_VENV_CREATE_COMMANDS = (
    "python -m venv .venv",
    "python3 -m venv .venv",
    "py -m venv .venv",
)


def _resolve_existing_venv_python(project_dir: Path) -> str | None:
    """Return the relative venv python path that actually exists on disk, or None."""

    for candidate in (".venv/bin/python", ".venv\\Scripts\\python.exe", ".venv/Scripts/python.exe"):
        # Use PurePath-style resolve so this works on either host.
        resolved = project_dir / Path(candidate.replace("\\", "/"))
        if resolved.exists():
            return candidate
    return None


def _has_or_expects_project_venv(plan: RepoBringUpPlan, project_dir: Path) -> bool:
    if _resolve_existing_venv_python(project_dir):
        return True
    if any(step.command in _VENV_CREATE_COMMANDS for step in plan.steps):
        return True
    return any(name in plan.detected_files for name in ("requirements.txt", "pyproject.toml", "setup.py"))


def _python_runtime_executable(project_dir: Path) -> str:
    existing = _resolve_existing_venv_python(project_dir)
    if existing:
        return existing
    return "python"


def _normalize_python_runtime_command(command: str | None, project_dir: Path) -> str | None:
    if not isinstance(command, str) or not command.strip():
        return None
    existing = _resolve_existing_venv_python(project_dir)
    if existing is None:
        for prefix in (".venv/bin/python ", ".venv\\Scripts\\python.exe ", ".venv/Scripts/python.exe "):
            if command.startswith(prefix):
                return command.replace(prefix.rstrip(), "python", 1)
    return command


def _infer_verify_command(plan: RepoBringUpPlan, project_dir: Path) -> str | None:
    repo_name = plan.repo.name.lower()
    if repo_name == "whisper" and _has_or_expects_project_venv(plan, project_dir):
        return f"{_python_runtime_executable(project_dir)} -m whisper --help"
    if repo_name == "speechbrain" and _has_or_expects_project_venv(plan, project_dir):
        return f"{_python_runtime_executable(project_dir)} -m speechbrain --help"
    if repo_name == "whisperx" and _has_or_expects_project_venv(plan, project_dir):
        return f"{_python_runtime_executable(project_dir)} -m whisperx --help"
    knowledge = inspect_repo_for_bringup(
        plan.repo,
        project_dir,
        context_service=AgentContextService(),
    ).repo_knowledge
    candidate = _best_entrypoint_for_kind(knowledge.entrypoints if knowledge is not None else (), "verification")
    if candidate is not None:
        return _normalize_python_runtime_command(candidate, project_dir)
    for step in plan.steps:
        if step.verification_command and not step.verification_command.startswith("test "):
            return _normalize_python_runtime_command(step.verification_command, project_dir)
    for step in plan.steps:
        if step.verification_command:
            return _normalize_python_runtime_command(step.verification_command, project_dir)
    if _has_or_expects_project_venv(plan, project_dir):
        return f"{_python_runtime_executable(project_dir)} -m pip --version"
    if (project_dir / "go.mod").exists():
        return "go list ./..."
    if (project_dir / "Cargo.toml").exists():
        return "cargo build --quiet"
    if any((project_dir / name).exists() for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")):
        return "docker compose config"
    if (project_dir / "package.json").exists():
        package_manager = _preferred_node_package_manager(project_dir)
        if package_manager == "bun":
            return "bun --version"
        return "test -d node_modules"
    return None


def _infer_start_command(plan: RepoBringUpPlan, project_dir: Path) -> str | None:
    repo_name = plan.repo.name.lower()
    if repo_name in {"whisper", "speechbrain", "whisperx"}:
        return None
    readme_candidates = scan_readme_for_run_commands(
        project_dir,
        repo_name=plan.repo.name,
        execution_target=plan.execution_target,
    )
    if not readme_candidates and _is_remote_target(plan.execution_target):
        readme_candidates = scan_remote_readme_for_run_commands(
            plan.repo.repo_url,
            repo_name=plan.repo.name,
            execution_target=plan.execution_target,
        )
    if readme_candidates:
        return _normalize_python_runtime_command(readme_candidates[0], project_dir)
    direct_file_hint = _infer_start_command_from_files(project_dir, execution_target=plan.execution_target)
    if direct_file_hint is not None:
        return direct_file_hint
    knowledge = inspect_repo_for_bringup(
        plan.repo,
        project_dir,
        context_service=AgentContextService(),
    ).repo_knowledge
    candidate = _best_entrypoint_for_kind(knowledge.entrypoints if knowledge is not None else (), "start")
    if candidate is not None:
        return _normalize_python_runtime_command(candidate, project_dir)
    return None


def _infer_manual_command(plan: RepoBringUpPlan, project_dir: Path) -> str | None:
    repo_name = plan.repo.name.lower()
    if repo_name == "whisper" and _has_or_expects_project_venv(plan, project_dir):
        return f"{_python_runtime_executable(project_dir)} -m whisper <audio-file> --model base"
    if repo_name == "whisperx" and _has_or_expects_project_venv(plan, project_dir):
        return f"{_python_runtime_executable(project_dir)} -m whisperx <audio-file>"
    if repo_name == "speechbrain" and _has_or_expects_project_venv(plan, project_dir):
        return f"{_python_runtime_executable(project_dir)} -m speechbrain <task-or-input>"
    if (project_dir / "go.mod").exists():
        return "go run ."
    if (project_dir / "Cargo.toml").exists():
        return "cargo run"
    start = _infer_start_command(plan, project_dir)
    if start is not None:
        return _normalize_python_runtime_command(start, project_dir)
    return None


def _infer_runtime_kind(plan: RepoBringUpPlan, project_dir: Path) -> str:
    repo_name = plan.repo.name.lower()
    if repo_name in {"whisper", "speechbrain", "whisperx"}:
        return "cli_tool"
    knowledge = inspect_repo_for_bringup(
        plan.repo,
        project_dir,
        context_service=AgentContextService(),
    ).repo_knowledge
    if knowledge is not None and knowledge.runtime_style == "cli_tool":
        return "cli_tool"
    if _infer_start_command(plan, project_dir) is not None:
        return "service"
    return "bounded_check_only"


def _best_entrypoint_for_kind(entrypoints: tuple[str, ...], desired_kind: str) -> str | None:
    for entry in entrypoints:
        if classify_runtime_command(entry) == desired_kind:
            return entry
    if desired_kind == "start":
        for entry in entrypoints:
            if classify_runtime_command(entry) == "unknown":
                return entry
    return None


def _npm_global_binary_name(project_dir: Path) -> str | None:
    """Return the CLI binary name if the package.json declares a `bin` field (npm global CLI tool)."""
    package_json = project_dir / "package.json"
    if not package_json.exists():
        return None
    try:
        payload = json.loads(package_json.read_text(encoding="utf-8"))
        bin_field = payload.get("bin")
        if isinstance(bin_field, dict) and bin_field:
            return next(iter(bin_field.keys()))
        if isinstance(bin_field, str) and bin_field.strip():
            name = payload.get("name", "")
            return name.lstrip("@").split("/")[-1] if name else None
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _infer_start_command_from_files(project_dir: Path, *, execution_target: str = "local") -> str | None:
    compose_candidates = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")
    if any((project_dir / candidate).exists() for candidate in compose_candidates):
        if not _is_remote_target(execution_target):
            return "docker compose up"

    # For VM/remote targets: prefer the npm global binary (CLI tool) or install.sh binary
    # over docker-compose or npm start — the VM is its own sandbox, no Docker wrapper needed.
    if _is_remote_target(execution_target):
        bin_name = _npm_global_binary_name(project_dir)
        if bin_name:
            return bin_name
        if (project_dir / "install.sh").exists():
            return project_dir.name.lower()

    package_json = project_dir / "package.json"
    if package_json.exists():
        package_manager = _preferred_node_package_manager(project_dir)
        try:
            payload = json.loads(package_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
        scripts = payload.get("scripts")
        if isinstance(scripts, dict):
            for candidate in ("start", "dev", "serve", "preview", "web", "frontend"):
                if isinstance(scripts.get(candidate), str) and scripts[candidate].strip():
                    return _render_node_script_command(package_manager, candidate)
            for candidate in ("api", "backend"):
                if isinstance(scripts.get(candidate), str) and scripts[candidate].strip():
                    return _render_node_script_command(package_manager, candidate)
            if isinstance(payload.get("main"), str) and payload["main"].strip():
                return f"node {payload['main'].strip()}"
    makefile_path = project_dir / "Makefile"
    if makefile_path.exists():
        content = makefile_path.read_text(encoding="utf-8", errors="ignore")
        for candidate in ("start", "serve", "run", "dev", "up", "web", "api", "backend"):
            if re.search(rf"^{re.escape(candidate)}\s*:", content, flags=re.MULTILINE):
                return f"make {candidate}"
    if (project_dir / "manage.py").exists():
        if (project_dir / ".venv" / "bin" / "python").exists():
            return ".venv/bin/python manage.py runserver"
        return "python manage.py runserver"
    if (project_dir / ".venv" / "bin" / "python").exists() and (project_dir / "app.py").exists():
        return ".venv/bin/python app.py"
    if (project_dir / ".venv" / "bin" / "python").exists() and (project_dir / "main.py").exists():
        return ".venv/bin/python main.py"
    if (project_dir / "app.py").exists():
        return "python app.py"
    if (project_dir / "main.py").exists():
        return "python main.py"
    pyproject_path = project_dir / "pyproject.toml"
    if pyproject_path.exists():
        script_cmd = _infer_pyproject_script_command(pyproject_path)
        if script_cmd is not None:
            return script_cmd
    if (project_dir / "Cargo.toml").exists():
        return "cargo run"
    cmd_dir = project_dir / "cmd"
    if (project_dir / "go.mod").exists() and cmd_dir.is_dir():
        subcommands = [child for child in cmd_dir.iterdir() if child.is_dir()]
        if len(subcommands) == 1:
            return f"go run ./cmd/{subcommands[0].name}"
        if subcommands:
            return "go run ./cmd/..."
    if (project_dir / "go.mod").exists() or (project_dir / "main.go").exists():
        return "go run ."
    if (project_dir / "CMakeLists.txt").exists() and (project_dir / "build").is_dir():
        for candidate in (project_dir / "build").iterdir():
            if candidate.is_file() and candidate.stat().st_mode & 0o111:
                return f"./build/{candidate.name}"
    return None


def _infer_pyproject_script_command(pyproject_path: Path) -> str | None:
    """Parse pyproject.toml for the first runnable script entry.

    Prefers stdlib tomllib (3.11+); falls back to a small section-scoped regex
    on older Pythons so script discovery still works for PEP 621 and Poetry
    layouts.
    """
    text: str | None = None
    try:
        text = pyproject_path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        import tomllib  # Python 3.11+

        data = tomllib.loads(text)
        if isinstance(data, dict):
            project = data.get("project")
            if isinstance(project, dict):
                scripts = project.get("scripts")
                if isinstance(scripts, dict):
                    for name in scripts:
                        if isinstance(name, str) and name.strip():
                            return name.strip()
            tool = data.get("tool")
            if isinstance(tool, dict):
                poetry = tool.get("poetry")
                if isinstance(poetry, dict):
                    poetry_scripts = poetry.get("scripts")
                    if isinstance(poetry_scripts, dict):
                        for name in poetry_scripts:
                            if isinstance(name, str) and name.strip():
                                return name.strip()
        return None
    except ImportError:
        pass
    except ValueError:
        return None
    return _pyproject_scripts_regex_fallback(text)


_PYPROJECT_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$", re.MULTILINE)
_PYPROJECT_KEY_RE = re.compile(r"^\s*([A-Za-z_][\w\-]*)\s*=", re.MULTILINE)


def _pyproject_scripts_regex_fallback(text: str) -> str | None:
    for target_section in ("project.scripts", "tool.poetry.scripts"):
        sections = list(_PYPROJECT_SECTION_RE.finditer(text))
        for index, match in enumerate(sections):
            if match.group(1).strip() != target_section:
                continue
            start = match.end()
            end = sections[index + 1].start() if index + 1 < len(sections) else len(text)
            body = text[start:end]
            key_match = _PYPROJECT_KEY_RE.search(body)
            if key_match is not None:
                return key_match.group(1).strip()
    return None


def _preferred_node_package_manager(project_dir: Path) -> str:
    if (project_dir / "pnpm-lock.yaml").exists():
        return "pnpm"
    if any((project_dir / candidate).exists() for candidate in ("yarn.lock", ".yarnrc.yml")):
        return "yarn"
    if any((project_dir / candidate).exists() for candidate in ("bun.lockb", "bun.lock")):
        return "bun"
    return "npm"


def _render_node_script_command(package_manager: str, script_name: str) -> str:
    if package_manager == "pnpm":
        return f"pnpm {script_name}"
    if package_manager == "yarn":
        return f"yarn {script_name}"
    if package_manager == "bun":
        return f"bun run {script_name}"
    return f"npm run {script_name}"


def _prefer_derived_runtime_command(existing_command: str | None, derived_command: str | None) -> bool:
    if not isinstance(derived_command, str) or not derived_command.strip():
        return False
    if not isinstance(existing_command, str) or not existing_command.strip():
        return True
    existing_kind = classify_runtime_command(existing_command)
    derived_kind = classify_runtime_command(derived_command)
    if existing_kind == "verification" and derived_kind != "verification":
        return True
    if existing_kind == "unknown" and derived_kind == "start":
        return True
    return False


def _managed_last_activity_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def _infer_access_hint(repo: RepoCatalogRecord, plan: RepoBringUpPlan, project_dir: Path) -> str | None:
    runtime_url = _infer_runtime_access_url(_infer_start_command(plan, project_dir))
    if runtime_url is not None:
        return f"After the app starts, Duckln can open {runtime_url} inside Duckln’s managed browser window."
    if repo.name.lower() == "open-webui":
        return "After the app starts, open the local web URL it prints, usually http://localhost:8080."
    if "package.json" in plan.detected_files:
        return "Run the project from the managed directory and use the documented start script."
    if repo.category == "Audio" or "whisper" in repo.name.lower():
        return "Use the CLI from the managed project directory after activating the virtualenv."
    if (project_dir / "go.mod").exists():
        return "Use the Go command from Duckln’s managed project directory, or ask Duckln to rerun it there."
    if (project_dir / "Cargo.toml").exists():
        return "Use Cargo from Duckln’s managed project directory, or ask Duckln to rerun the bounded command there."
    if (project_dir / "README.md").exists():
        return "The managed project directory now has the repo README for the next run/access step."
    return None


def _infer_runtime_access_url(command: str | None) -> str | None:
    if not isinstance(command, str) or not command.strip():
        return None
    normalized = " ".join(command.strip().split())
    lowered = normalized.casefold()
    host = "127.0.0.1"
    port = None

    host_match = re.search(r"--host\s+([^\s]+)", normalized)
    if host_match is not None:
        host = host_match.group(1)

    if " manage.py runserver" in f" {lowered} ":
        address_match = re.search(r"runserver\s+([0-9.]+:\d+)", normalized)
        if address_match is not None:
            host_part, port_part = address_match.group(1).split(":", 1)
            host = host_part
            port = int(port_part)
        else:
            port = 8000
    elif "uvicorn " in lowered or "hypercorn " in lowered:
        port_match = re.search(r"--port\s+(\d+)", normalized)
        port = int(port_match.group(1)) if port_match is not None else 8000
    elif "streamlit " in lowered:
        port_match = re.search(r"--server\.port\s+(\d+)", normalized)
        host = "localhost"
        port = int(port_match.group(1)) if port_match is not None else 8501
    elif "gradio " in lowered:
        port_match = re.search(r"--server-port\s+(\d+)", normalized)
        host = "127.0.0.1"
        port = int(port_match.group(1)) if port_match is not None else 7860
    elif "flask run" in lowered:
        port_match = re.search(r"--port\s+(\d+)", normalized)
        host_match = re.search(r"--host\s+([^\s]+)", normalized)
        if host_match is not None:
            host = host_match.group(1)
        port = int(port_match.group(1)) if port_match is not None else 5000
    elif "jupyter lab" in lowered or "jupyter notebook" in lowered:
        port_match = re.search(r"--port(?:=|\s+)(\d+)", normalized)
        host = "localhost"
        port = int(port_match.group(1)) if port_match is not None else 8888

    if port is None:
        return None
    if host in {"0.0.0.0", "127.0.0.1", "localhost"}:
        host = "localhost"
    return f"http://{host}:{port}"


def _describe_changed_items(project_dir: Path, executed_commands: tuple[str, ...]) -> tuple[str, ...]:
    changes: list[str] = []
    if project_dir.exists() or any(command.startswith("git clone ") for command in executed_commands):
        changes.append("cloned the repo into Duckln’s managed workspace")
    if (project_dir / ".venv").exists() or any(command == "python -m venv .venv" for command in executed_commands):
        changes.append("created a project virtualenv")
    if any("pip install" in command for command in executed_commands):
        changes.append("installed Python dependencies")
    if any("npm " in command or "pnpm " in command or "yarn " in command for command in executed_commands):
        changes.append("installed Node dependencies")
    if any("cargo " in command for command in executed_commands):
        changes.append("resolved Rust dependencies")
    if any(command.startswith("go mod") or command.startswith("go build") or command.startswith("go list") for command in executed_commands):
        changes.append("resolved Go module dependencies")
    if any(command.startswith("docker build") or command.startswith("docker compose build") for command in executed_commands):
        changes.append("built the container image or service stack")
    return tuple(dict.fromkeys(changes))


def _build_removal_hint(project_dir: Path, environment_path: str | None) -> str:
    removal_parts = [f"remove the project directory at {project_dir}"]
    if environment_path is not None:
        removal_parts.append(f"remove the virtualenv at {environment_path}")
    return "; ".join(removal_parts)


_VERIFICATION_RUN_MARKERS = (
    "--help",
    " --help",
    "--version",
    " --version",
    "pip --version",
    "go list",
    "cargo build",
    "docker compose config",
    "test -d node_modules",
    "checkhealth",
    "healthcheck",
    "pytest",
    "python -m pip",
)

_START_RUN_MARKERS = (
    "uvicorn ",
    "streamlit ",
    "gradio ",
    "node ",
    "flask run",
    "runserver",
    "cargo run",
    "go run",
    "npm start",
    "npm run dev",
    "npm run start",
    "npm run serve",
    "npm run preview",
    "pnpm dev",
    "pnpm start",
    "pnpm serve",
    "yarn dev",
    "yarn start",
    "yarn serve",
    "bun run dev",
    "bun run start",
    "bun run serve",
    "docker compose up",
    "docker run",
    "jupyter lab",
    "jupyter notebook",
    "ollama serve",
)


def classify_runtime_command(command: str | None) -> str:
    """Classify whether a stored command is a bounded verification path or a start path."""

    if not isinstance(command, str) or not command.strip():
        return "missing"
    normalized = " ".join(command.lower().split())
    if any(marker in normalized for marker in _VERIFICATION_RUN_MARKERS):
        return "verification"
    if any(marker in normalized for marker in _START_RUN_MARKERS):
        return "start"
    if re.match(r"^[a-z][a-z0-9_.-]+\s+(?:start|serve|dev|run|up|launch|server|gateway|web)\b", normalized):
        return "start"
    if re.match(r"^(?:npx|pnpm dlx|yarn dlx|bunx)\s+[a-z0-9@/_:.-]+", normalized) and _README_START_VERB_PATTERN.search(normalized):
        return "start"
    return "unknown"


def _normalize_auth_requirements(
    auth_requirements: tuple[object, ...],
    metadata: dict[str, object] | None = None,
) -> tuple[dict[str, object], ...]:
    normalized: list[dict[str, object]] = []
    for item in auth_requirements:
        if hasattr(item, "env_var") and hasattr(item, "provider"):
            env_var = str(getattr(item, "env_var", "") or "").strip()
            provider = str(getattr(item, "provider", "") or "").strip()
            reason = str(getattr(item, "reason", "") or "").strip()
            source = str(getattr(item, "source", "") or "").strip()
            required = bool(getattr(item, "required", False))
            if env_var:
                normalized.append(
                    {
                        "env_var": env_var,
                        "provider": provider or "external",
                        "reason": reason,
                        "source": source,
                        "required": required,
                    }
                )
            continue
        if isinstance(item, dict):
            env_var = str(item.get("env_var") or "").strip()
            if env_var:
                normalized.append(
                    {
                        "env_var": env_var,
                        "provider": str(item.get("provider") or "external").strip() or "external",
                        "reason": str(item.get("reason") or "").strip(),
                        "source": str(item.get("source") or "").strip(),
                        "required": bool(item.get("required")),
                    }
                )
    if not normalized and isinstance(metadata, dict):
        raw_metadata = metadata.get("auth_requirements")
        if isinstance(raw_metadata, list):
            normalized.extend(_normalize_auth_requirements(tuple(raw_metadata)))
    deduped: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for item in normalized:
        key = (str(item.get("env_var") or "").strip(), json.dumps(item, sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return tuple(deduped)


def _missing_required_auth_variables(auth_requirements: tuple[dict[str, object], ...]) -> tuple[str, ...]:
    missing: list[str] = []
    for item in auth_requirements:
        env_var = str(item.get("env_var") or "").strip()
        if env_var and bool(item.get("required")) and not os.environ.get(env_var):
            missing.append(env_var)
    return tuple(dict.fromkeys(missing))


def _command_uses_placeholders(command: str | None) -> bool:
    if not isinstance(command, str):
        return False
    return "<" in command and ">" in command


def _runtime_log_path(paths: ConfigPaths, repo: RepoCatalogRecord) -> Path:
    runtime_dir = paths.config_dir / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    return runtime_dir / f"{_slugify(repo.name)}.log"


def _load_runtime_run_approved(paths: ConfigPaths) -> bool:
    """Return True if the user already approved running the repo in this session."""
    from state.access import read_workflow_state
    state = read_workflow_state(paths.config_dir)
    return str(state.get("active_runtime_run_approved", "") or "").strip().lower() == "true"


def _persist_runtime_run_approved(paths: ConfigPaths) -> None:
    """Store that the user approved running the repo so retries skip the approval prompt."""
    from state.access import write_workflow_state
    write_workflow_state(paths.config_dir, {"active_runtime_run_approved": "true"})


def _write_runtime_workflow_state(
    paths: ConfigPaths,
    *,
    repo: RepoCatalogRecord,
    issue_kind: str | None,
    issue_summary: str | None,
    runtime_status: str | None,
    runtime_command: str | None,
    runtime_command_kind: str | None,
    runtime_cwd: str | None,
    runtime_pid: int | None = None,
    runtime_log_path: str | None = None,
    execution_target: str | None = None,
    vm_name: str | None = None,
    attach_hint: str | None = None,
    logs_hint: str | None = None,
    stop_hint: str | None = None,
    stop_command: str | None = None,
    docker_name: str | None = None,
    cloud_resource_key: str | None = None,
    cloud_vendor: str | None = None,
    cloud_region: str | None = None,
    cloud_shape: str | None = None,
) -> None:
    write_workflow_state(
        paths.config_dir,
        {
            "active_repo_key": repo.repo_url,
            "active_repo_name": repo.name,
            "active_issue_kind": issue_kind,
            "active_issue_summary": issue_summary,
            "active_runtime_status": runtime_status,
            "active_runtime_command": runtime_command,
            "active_runtime_command_kind": runtime_command_kind,
            "active_runtime_repo_key": repo.repo_url,
            "active_runtime_repo_name": repo.name,
            "active_runtime_cwd": runtime_cwd,
            "active_runtime_pid": runtime_pid,
            "active_runtime_log_path": runtime_log_path,
            "active_runtime_execution_target": execution_target,
            "active_runtime_vm_name": vm_name,
            "active_runtime_attach_hint": attach_hint,
            "active_runtime_logs_hint": logs_hint,
            "active_runtime_stop_hint": stop_hint,
            "active_runtime_stop_command": stop_command,
            "active_runtime_docker_name": docker_name,
            "active_runtime_cloud_resource_key": cloud_resource_key,
            "active_runtime_cloud_vendor": cloud_vendor,
            "active_runtime_cloud_region": cloud_region,
            "active_runtime_cloud_shape": cloud_shape,
        },
    )


def _write_repo_objective_state(
    paths: ConfigPaths,
    *,
    repo: RepoCatalogRecord,
    kind: str | None,
    status: str | None,
    goal: str | None,
    execution_target: str | None,
    requires_user_decision: bool | None = None,
    resume_hint: str | None = None,
    last_blocker: str | None = None,
    clear: bool = False,
) -> None:
    if clear:
        write_workflow_state(
            paths.config_dir,
            {
                "active_objective_id": None,
                "active_objective_kind": None,
                "active_objective_repo_key": None,
                "active_objective_repo_name": None,
                "active_objective_status": None,
                "active_objective_goal": None,
                "active_objective_execution_target": None,
                "active_objective_runtime_command": None,
                "active_objective_last_blocker": None,
                "active_objective_attempt_count": None,
                "active_objective_max_attempts": None,
                "active_objective_requires_user_decision": None,
                "active_objective_resume_hint": None,
                "active_objective_started_at": None,
                "active_objective_updated_at": None,
                "active_issue_kind": None,
                "active_issue_summary": None,
                "active_incident_category": None,
                "active_incident_summary": None,
                "active_repair_phase": None,
            },
        )
        return
    existing = AgentContextService().load_workflow_state(config_dir=paths.config_dir)
    objective_id = f"{kind}:{repo.repo_url}"
    started_at = (
        existing.active_objective_started_at
        if existing is not None
        and existing.active_objective_repo_key == repo.repo_url
        and existing.active_objective_kind == kind
        and existing.active_objective_started_at
        else datetime.now(timezone.utc).isoformat()
    )
    write_workflow_state(
        paths.config_dir,
        {
            "active_objective_id": objective_id,
            "active_objective_kind": kind,
            "active_objective_repo_key": repo.repo_url,
            "active_objective_repo_name": repo.name,
            "active_objective_status": status,
            "active_objective_goal": goal,
            "active_objective_execution_target": execution_target,
            "active_objective_last_blocker": last_blocker,
            "active_objective_requires_user_decision": requires_user_decision,
            "active_objective_resume_hint": resume_hint or goal,
            "active_objective_started_at": started_at,
            "active_objective_updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def _repo_setup_outcome_metadata(
    *,
    repo: RepoCatalogRecord,
    plan: RepoBringUpPlan,
    project_dir: Path,
    outcome: RepoSetupOutcome,
) -> dict[str, object]:
    return {
        "repo_family": plan.repo_family.value,
        "specialist": plan.specialist_name,
        "install_location": outcome.install_location,
        "environment_path": outcome.environment_path,
        "run_command": outcome.run_command,
        "verify_command": outcome.verify_command,
        "manual_command": outcome.manual_command,
        "runtime_kind": outcome.runtime_kind,
        "stack_family": outcome.stack_family,
        "auth_requirements": list(outcome.auth_requirements),
        "missing_auth_variables": list(outcome.missing_auth_variables),
        "access_hint": outcome.access_hint,
        "verification_summary": outcome.verification.summary,
        "verification_command": outcome.verification.verification_command,
        "verification_checks": list(outcome.verification.checks_run),
        "verification_verified": outcome.verification.verified,
        "removal_hint": outcome.removal_hint,
        "changed_items": list(outcome.changed_items),
        "repo_name": repo.name,
        "repo_url": repo.repo_url,
        "local_project_dir": str(project_dir),
    }


def _supervisor_final_summary(
    *,
    repo: RepoCatalogRecord,
    outcome: RepoSetupOutcome,
    current_mode: ControlMode,
) -> str:
    changed_text = ", ".join(outcome.changed_items) if outcome.changed_items else "prepared the repo workspace"
    lines = [
        f"Setup complete for {repo.name}." if outcome.verification.verified else f"Setup incomplete for {repo.name}.",
        f"Supervisor agent confirmed the smallest {repo.name} setup path for {current_mode.label}.",
        f"Duckln {changed_text}.",
        f"Installed at: {outcome.install_location}",
    ]
    if outcome.environment_path:
        lines.append(f"Environment: {outcome.environment_path}")
    if outcome.run_command:
        lines.append(f"Run: {outcome.run_command}")
        if outcome.verification.verified:
            lines.append(f"Setup complete. Run command found: {outcome.run_command}. Launch it?")
    if outcome.verify_command and outcome.verify_command != outcome.run_command:
        lines.append(f"Verify: {outcome.verify_command}")
    if outcome.manual_command and outcome.manual_command not in {outcome.run_command, outcome.verify_command}:
        lines.append(f"Manual use: {outcome.manual_command}")
    if outcome.stack_family:
        lines.append(f"Stack: {outcome.stack_family}")
    if outcome.missing_auth_variables:
        lines.append(f"Auth still needed: {', '.join(outcome.missing_auth_variables)}")
    if outcome.access_hint:
        lines.append(f"Access: {outcome.access_hint}")
    lines.append(f"Verified: {outcome.verification.summary}")
    if not outcome.verification.verified and outcome.verification.next_step:
        lines.append(f"Next step: {outcome.verification.next_step}")
    lines.append(f"Remove: {outcome.removal_hint}")
    return "\n".join(lines)


def run_prepared_repo(
    repo: RepoCatalogRecord,
    current_mode: ControlMode,
    paths: ConfigPaths,
    *,
    runner: ControlledCommandRunner | None = None,
    approve: Callable[[str], bool] | None = None,
    display: Callable[[str], None] = print,
    verification_only: bool = False,
    terminal_executor: object | None = None,
    runtime_command_override: str | None = None,
    runtime_step_preapproved: bool = False,
    pane_executor: object | None = None,
) -> RepoBringUpResult:
    """Run a prepared repo from Duckln's managed workspace."""

    service = AgentContextService()
    tracked_state = service.load_repo_state_snapshot(config_dir=paths.config_dir, repo=repo)
    workflow_state = service.load_workflow_state(config_dir=paths.config_dir)
    tracked_project_dir = None
    if tracked_state is not None and tracked_state.install_location:
        tracked_project_dir = Path(tracked_state.install_location)
    project_dir = tracked_project_dir or resolve_managed_project_dir(paths.config_dir, repo)
    runtime_cwd = str(project_dir)
    execution_target = tracked_state.execution_target if tracked_state is not None else "local"
    vm_name = tracked_state.vm_name if tracked_state is not None else None
    tracked_cloud_resource_key = tracked_state.cloud_resource_key if tracked_state is not None else None
    tracked_cloud_vendor = tracked_state.cloud_vendor if tracked_state is not None else None
    tracked_cloud_region = tracked_state.cloud_region if tracked_state is not None else None
    tracked_cloud_shape = tracked_state.cloud_shape if tracked_state is not None else None
    knowledge = service.resolve_repo_knowledge(repo=repo, config_dir=paths.config_dir, project_dir=project_dir)
    metadata = dict(knowledge.metadata) if knowledge is not None else {}
    detected_files = knowledge.setup_files if knowledge is not None else inspect_repo_setup_files(project_dir)
    if tracked_state is not None:
        if tracked_state.run_command and not isinstance(metadata.get("run_command"), str):
            metadata["run_command"] = tracked_state.run_command
        if tracked_state.verify_command and not isinstance(metadata.get("verify_command"), str):
            metadata["verify_command"] = tracked_state.verify_command
        if tracked_state.manual_command and not isinstance(metadata.get("manual_command"), str):
            metadata["manual_command"] = tracked_state.manual_command
        if tracked_state.runtime_kind and not isinstance(metadata.get("runtime_kind"), str):
            metadata["runtime_kind"] = tracked_state.runtime_kind
        if tracked_state.access_hint and not isinstance(metadata.get("access_hint"), str):
            metadata["access_hint"] = tracked_state.access_hint
        if tracked_state.removal_hint and not isinstance(metadata.get("removal_hint"), str):
            metadata["removal_hint"] = tracked_state.removal_hint
        if tracked_state.stack_family and not isinstance(metadata.get("stack_family"), str):
            metadata["stack_family"] = tracked_state.stack_family
        if tracked_state.auth_requirements and not metadata.get("auth_requirements"):
            metadata["auth_requirements"] = list(tracked_state.auth_requirements)
        if tracked_state.missing_auth_variables and not metadata.get("missing_auth_variables"):
            metadata["missing_auth_variables"] = list(tracked_state.missing_auth_variables)
    if project_dir.exists():
        derived_hints = derive_repo_runtime_hints(
            repo,
            project_dir,
            execution_target=execution_target,
            config_dir=paths.config_dir,
        )
        if _prefer_derived_runtime_command(
            tracked_state.run_command if tracked_state is not None else metadata.get("run_command"),
            derived_hints.run_command,
        ):
            metadata["run_command"] = derived_hints.run_command
        if not isinstance(metadata.get("verify_command"), str) and derived_hints.verify_command:
            metadata["verify_command"] = derived_hints.verify_command
        if not isinstance(metadata.get("manual_command"), str) and derived_hints.manual_command:
            metadata["manual_command"] = derived_hints.manual_command
        if not isinstance(metadata.get("runtime_kind"), str) and derived_hints.runtime_kind:
            metadata["runtime_kind"] = derived_hints.runtime_kind
        if not isinstance(metadata.get("access_hint"), str) and derived_hints.access_hint:
            metadata["access_hint"] = derived_hints.access_hint
        if not isinstance(metadata.get("stack_family"), str) and derived_hints.stack_family:
            metadata["stack_family"] = derived_hints.stack_family
        if not metadata.get("auth_requirements") and derived_hints.auth_requirements:
            metadata["auth_requirements"] = list(derived_hints.auth_requirements)
        if not metadata.get("missing_auth_variables") and derived_hints.missing_auth_variables:
            metadata["missing_auth_variables"] = list(derived_hints.missing_auth_variables)
    auth_requirements = _normalize_auth_requirements(
        knowledge.auth_requirements if knowledge is not None else (),
        metadata,
    )
    missing_auth_variables = _missing_required_auth_variables(auth_requirements)
    metadata["auth_requirements"] = list(auth_requirements)
    metadata["missing_auth_variables"] = list(missing_auth_variables)
    run_command = tracked_state.run_command if tracked_state is not None and tracked_state.run_command else metadata.get("run_command")
    if _prefer_derived_runtime_command(
        tracked_state.run_command if tracked_state is not None else None,
        metadata.get("run_command") if isinstance(metadata.get("run_command"), str) else None,
    ):
        run_command = metadata.get("run_command")
    if (
        (not isinstance(run_command, str) or not run_command.strip())
        and workflow_state is not None
        and str(workflow_state.active_runtime_repo_key or "").strip() == repo.repo_url
    ):
        workflow_runtime_command = str(workflow_state.active_runtime_command or "").strip()
        if workflow_runtime_command and not _command_uses_placeholders(workflow_runtime_command):
            run_command = workflow_runtime_command
            metadata["run_command"] = workflow_runtime_command
    verify_command = tracked_state.verify_command if tracked_state is not None and tracked_state.verify_command else metadata.get("verify_command")
    manual_command = tracked_state.manual_command if tracked_state is not None and tracked_state.manual_command else metadata.get("manual_command")
    if isinstance(runtime_command_override, str) and runtime_command_override.strip():
        run_command = runtime_command_override.strip()
    if (not isinstance(run_command, str) or not run_command.strip()) and isinstance(manual_command, str) and manual_command.strip() and not _command_uses_placeholders(manual_command):
        run_command = manual_command
    runtime_kind = tracked_state.runtime_kind if tracked_state is not None and tracked_state.runtime_kind else metadata.get("runtime_kind")
    wrapped_runtime_metadata = {
        "vm_name": vm_name,
        "cloud_resource_key": tracked_cloud_resource_key,
        "cloud_vendor": tracked_cloud_vendor,
        "cloud_region": tracked_cloud_region,
        "cloud_shape": tracked_cloud_shape,
    }
    command_kind = classify_runtime_command(run_command)
    cli_task_materialized = bool(
        not verification_only
        and runtime_kind == "cli_tool"
        and isinstance(run_command, str)
        and run_command.strip()
        and not _command_uses_placeholders(run_command)
        and command_kind != "verification"
    )
    if verification_only:
        runtime_command = verify_command if isinstance(verify_command, str) and verify_command.strip() else run_command
        runtime_command_kind = classify_runtime_command(runtime_command)
    else:
        runtime_command = run_command
        runtime_command_kind = command_kind
    if not verification_only and missing_auth_variables and runtime_command_kind != "verification":
        auth_message = (
            f"Supervisor agent paused before running {repo.name} because required auth is still missing: "
            f"{', '.join(missing_auth_variables)}."
        )
        if auth_requirements:
            provider_names = ", ".join(
                sorted(
                    {
                        str(item.get('provider') or 'external').strip()
                        for item in auth_requirements
                        if str(item.get("env_var") or "").strip() in missing_auth_variables
                    }
                )
            )
            if provider_names:
                auth_message = f"{auth_message} Providers involved: {provider_names}."
        display(auth_message)
        _record_repo_state(
            paths,
            repo,
            project_dir,
            status="run_blocked",
            summary=auth_message,
            execution_target=execution_target,
            vm_name=vm_name,
            metadata={
                "active": True,
                "last_run_result": "missing_auth",
                "last_blocker": auth_message,
                "run_command_kind": runtime_command_kind,
                **metadata,
            },
        )
        return RepoBringUpResult(
            repo=repo,
            project_dir=project_dir,
            detected_files=detected_files,
            mode=current_mode,
            executed_commands=(),
            verification_passed=False,
            message=auth_message,
            repo_family=RepoFamily.PYTHON,
            specialist_name="supervisor",
            setup_outcome=None,
            should_offer_repair=False,
            message_already_displayed=True,
        )
    transport_hint = describe_runtime_transport(
        runtime_command,
        cwd=str(project_dir),
        repo_name=repo.name,
    )
    runtime_execution_target = transport_hint.connection_type if transport_hint.connection_type != "local" else execution_target
    if not verification_only and runtime_kind == "cli_tool" and runtime_command_kind != "start" and not cli_task_materialized:
        message_parts = [f"Supervisor agent is not treating {repo.name} as a long-running repo service."]
        if isinstance(verify_command, str) and verify_command.strip():
            message_parts.append(f"Duckln can verify the install with {verify_command}.")
        if isinstance(manual_command, str) and manual_command.strip():
            message_parts.append(f"Manual use: {manual_command}.")
        message_parts.append("Give Duckln an input file or a concrete CLI task if you want it to run the tool for real.")
        message = " ".join(message_parts)
        display(message)
        _record_repo_state(
            paths,
            repo,
            project_dir,
            status="ready",
            summary=message,
            execution_target=execution_target,
            vm_name=vm_name,
            metadata={
                "active": True,
                "last_run_result": "awaiting_input",
                "last_blocker": None,
                "run_command_kind": runtime_command_kind,
                **metadata,
            },
        )
        _write_runtime_workflow_state(
            paths,
            repo=repo,
            issue_kind="usage_needed",
            issue_summary=message,
            runtime_status="ready",
            runtime_command=runtime_command if isinstance(runtime_command, str) else None,
            runtime_command_kind=runtime_command_kind,
            runtime_cwd=runtime_cwd,
            execution_target=runtime_execution_target,
            vm_name=vm_name,
            attach_hint=metadata.get("access_hint") if isinstance(metadata.get("access_hint"), str) else None,
            stop_command=transport_hint.stop_command,
            docker_name=transport_hint.docker_name,
            cloud_resource_key=wrapped_runtime_metadata.get("cloud_resource_key"),
            cloud_vendor=wrapped_runtime_metadata.get("cloud_vendor"),
            cloud_region=wrapped_runtime_metadata.get("cloud_region"),
            cloud_shape=wrapped_runtime_metadata.get("cloud_shape"),
        )
        return RepoBringUpResult(
            repo=repo,
            project_dir=project_dir,
            detected_files=detected_files,
            mode=current_mode,
            executed_commands=(),
            verification_passed=False,
            message=message,
            repo_family=RepoFamily.PYTHON,
            specialist_name="supervisor",
            setup_outcome=None,
            should_offer_repair=False,
            message_already_displayed=True,
        )
    if not isinstance(runtime_command, str) or not runtime_command.strip():
        message = f"Supervisor agent does not have a reliable run command for {repo.name} yet."
        display(message)
        _record_repo_state(
            paths,
            repo,
            project_dir,
            status="run_blocked",
            summary=message,
            execution_target=execution_target,
            vm_name=vm_name,
            metadata={
                "active": True,
                "last_run_result": "missing_command",
                "last_blocker": message,
                "run_command_kind": runtime_command_kind,
                **metadata,
            },
        )
        _write_runtime_workflow_state(
            paths,
            repo=repo,
            issue_kind="run_issue",
            issue_summary=message,
            runtime_status="blocked",
            runtime_command=runtime_command if isinstance(runtime_command, str) else None,
            runtime_command_kind=runtime_command_kind,
            runtime_cwd=runtime_cwd,
            execution_target=execution_target,
            vm_name=vm_name,
            cloud_resource_key=wrapped_runtime_metadata.get("cloud_resource_key"),
            cloud_vendor=wrapped_runtime_metadata.get("cloud_vendor"),
            cloud_region=wrapped_runtime_metadata.get("cloud_region"),
            cloud_shape=wrapped_runtime_metadata.get("cloud_shape"),
        )
        return RepoBringUpResult(
            repo=repo,
            project_dir=project_dir,
            detected_files=detected_files,
            mode=current_mode,
            executed_commands=(),
            verification_passed=False,
            message=message,
            repo_family=RepoFamily.PYTHON,
            specialist_name="supervisor",
            setup_outcome=None,
            should_offer_repair=False,
        )
    if not verification_only and _is_dependency_install_command(runtime_command):
        message = (
            f"Supervisor agent does not have a reliable run command for {repo.name} yet. "
            f"The stored command is an install/setup step (`{runtime_command}`), not a runtime entry point."
        )
        display(message)
        _record_repo_state(
            paths,
            repo,
            project_dir,
            status="run_blocked",
            summary=message,
            execution_target=execution_target,
            vm_name=vm_name,
            metadata={
                "active": True,
                "last_run_result": "install_command_not_runnable",
                "last_blocker": message,
                "run_command_kind": "missing",
                **metadata,
            },
        )
        _write_runtime_workflow_state(
            paths,
            repo=repo,
            issue_kind="run_issue",
            issue_summary=message,
            runtime_status="blocked",
            runtime_command=runtime_command,
            runtime_command_kind="missing",
            runtime_cwd=runtime_cwd,
            execution_target=execution_target,
            vm_name=vm_name,
            cloud_resource_key=wrapped_runtime_metadata.get("cloud_resource_key"),
            cloud_vendor=wrapped_runtime_metadata.get("cloud_vendor"),
            cloud_region=wrapped_runtime_metadata.get("cloud_region"),
            cloud_shape=wrapped_runtime_metadata.get("cloud_shape"),
        )
        return RepoBringUpResult(
            repo=repo,
            project_dir=project_dir,
            detected_files=detected_files,
            mode=current_mode,
            executed_commands=(),
            verification_passed=False,
            message=message,
            repo_family=RepoFamily.PYTHON,
            specialist_name="supervisor",
            setup_outcome=None,
            should_offer_repair=False,
            message_already_displayed=True,
        )

    runtime_approve = approve
    # If the user already approved running this repo in the current session or in a prior repair
    # turn, skip the HOOTLWO prompt so they are not asked again for each retry.
    workflow_run_approved = _load_runtime_run_approved(paths)
    if (runtime_step_preapproved or workflow_run_approved) and not _is_dependency_install_command(runtime_command):
        runtime_approve = lambda _prompt: True

    decision, runtime_command = _mode_decision_for_step(
        current_mode,
        runtime_command,
        approve=runtime_approve,
        prompt=f"Run {repo.name}",
        project_dir=project_dir,
        display=display,
    )
    if not decision.allowed:
        message = (
            f"Supervisor agent paused before running {repo.name} because this step still needs approval in "
            f"{current_mode.label}."
        )
        display(message)
        _record_repo_state(
            paths,
            repo,
            project_dir,
            status="ready",
            summary=message,
            execution_target=execution_target,
            vm_name=vm_name,
            metadata={"active": True, "run_command_kind": runtime_command_kind},
        )
        return RepoBringUpResult(
            repo=repo,
            project_dir=project_dir,
            detected_files=detected_files,
            mode=current_mode,
            executed_commands=(),
            verification_passed=False,
            message=message,
            repo_family=RepoFamily.PYTHON,
            specialist_name="supervisor",
            setup_outcome=None,
            message_already_displayed=True,
        )
    # Persist the approval so subsequent repair retries do not ask again.
    if not workflow_run_approved:
        _persist_runtime_run_approved(paths)

    display(
        _agent_phase_status(
            "Supervisor agent",
            f"{'Verifying' if verification_only else 'Starting'} {repo.name} from Duckln’s managed workspace...",
            phase_index=1,
            phase_total=2,
            started_at=time.monotonic(),
        )
    )
    runner_instance = runner or ControlledCommandRunner(
        trace=display,
        execution_target=execution_target,
        pane_executor=pane_executor,
    )
    if verification_only:
        wrapped_runtime_command, wrapped_runtime_metadata = _wrap_command_for_execution_target(
            config_dir=paths.config_dir,
            execution_target=execution_target,
            command=runtime_command,
            cwd=runtime_cwd,
            preferred_vm_name=vm_name,
            preferred_cloud_resource_key=tracked_cloud_resource_key,
            preferred_cloud_vendor=tracked_cloud_vendor,
            preferred_cloud_region=tracked_cloud_region,
            preferred_cloud_shape=tracked_cloud_shape,
        )
        if not wrapped_runtime_command:
            message = f"Supervisor agent could not resolve the active {execution_target} target for {repo.name}."
            display(message)
            return RepoBringUpResult(
                repo=repo,
                project_dir=project_dir,
                detected_files=detected_files,
                mode=current_mode,
                executed_commands=(),
                verification_passed=False,
                message=message,
                repo_family=RepoFamily.PYTHON,
                specialist_name="supervisor",
                setup_outcome=None,
                should_offer_repair=False,
                message_already_displayed=True,
            )
        result = runner_instance.run(
            wrapped_runtime_command,
            cwd=None if execution_target in _REMOTE_EXECUTION_TARGETS else runtime_cwd,
        )
        initialize_state_store(paths.config_dir).record_run(
            run_id=f"repo-verify:{_slugify(repo.name)}",
            command_name="repo_verify",
            mode=current_mode.label,
            status="pass" if result.exit_code == 0 and not result.timed_out else "fail",
            summary=_compact_repo_summary(
                f"Verify {repo.name}: {'ok' if result.exit_code == 0 and not result.timed_out else 'failed'}"
            ),
            repo_key=repo.repo_url,
            metadata={"command": run_command},
        )
        if result.exit_code == 0 and not result.timed_out:
            message = (
                f"Supervisor agent verified {repo.name} with a bounded live check. "
                f"Verification command: {runtime_command}. "
                f"{metadata.get('access_hint') if isinstance(metadata.get('access_hint'), str) else ''}"
            ).strip()
            _record_repo_state(
                paths,
                repo,
                project_dir,
                status="ready",
                summary=message,
                execution_target=execution_target,
                vm_name=vm_name,
                metadata={
                    "active": True,
                    "last_run_command": runtime_command,
                    "last_run_result": "verified",
                    "last_blocker": None,
                    "run_command_kind": runtime_command_kind,
                    **metadata,
                },
            )
            _write_runtime_workflow_state(
                paths,
                repo=repo,
                issue_kind=None,
                issue_summary=None,
                runtime_status="verified",
                runtime_command=runtime_command,
                runtime_command_kind=runtime_command_kind,
                runtime_cwd=runtime_cwd,
                execution_target=execution_target,
                vm_name=wrapped_runtime_metadata.get("vm_name"),
                attach_hint=metadata.get("access_hint") if isinstance(metadata.get("access_hint"), str) else None,
                cloud_resource_key=wrapped_runtime_metadata.get("cloud_resource_key"),
                cloud_vendor=wrapped_runtime_metadata.get("cloud_vendor"),
                cloud_region=wrapped_runtime_metadata.get("cloud_region"),
                cloud_shape=wrapped_runtime_metadata.get("cloud_shape"),
            )
            return RepoBringUpResult(
                repo=repo,
                project_dir=project_dir,
                detected_files=detected_files,
                mode=current_mode,
                executed_commands=(runtime_command,),
                verification_passed=True,
                message=message,
                repo_family=RepoFamily.PYTHON,
                specialist_name="supervisor",
                setup_outcome=None,
            )

        failure_message = _command_failure_message(
            f"Verification failed for {repo.name}",
            result.stderr or result.stdout,
            command=runtime_command,
            display=display,
            execution_target=execution_target,
        )
        supervisor_message = f"Supervisor agent saw a verification issue for {repo.name}. {failure_message}"
        display(supervisor_message)
        _record_repo_state(
            paths,
            repo,
            project_dir,
            status="failed",
            summary=supervisor_message,
            execution_target=execution_target,
            vm_name=vm_name,
            metadata={
                "active": True,
                "last_run_command": runtime_command,
                "last_run_error": failure_message,
                "last_run_result": "verify_failed",
                "last_blocker": failure_message,
                "run_command_kind": runtime_command_kind,
                **metadata,
            },
        )
        _write_runtime_workflow_state(
            paths,
            repo=repo,
            issue_kind="verify_issue",
            issue_summary=supervisor_message,
            runtime_status="failed",
            runtime_command=runtime_command,
            runtime_command_kind=runtime_command_kind,
            runtime_cwd=runtime_cwd,
            execution_target=runtime_execution_target,
            vm_name=wrapped_runtime_metadata.get("vm_name"),
            attach_hint=metadata.get("access_hint") if isinstance(metadata.get("access_hint"), str) else None,
            stop_command=transport_hint.stop_command,
            docker_name=transport_hint.docker_name,
            cloud_resource_key=wrapped_runtime_metadata.get("cloud_resource_key"),
            cloud_vendor=wrapped_runtime_metadata.get("cloud_vendor"),
            cloud_region=wrapped_runtime_metadata.get("cloud_region"),
            cloud_shape=wrapped_runtime_metadata.get("cloud_shape"),
        )
        return RepoBringUpResult(
            repo=repo,
            project_dir=project_dir,
            detected_files=detected_files,
            mode=current_mode,
            executed_commands=(runtime_command,),
            verification_passed=True,
            message=supervisor_message,
            repo_family=RepoFamily.PYTHON,
            specialist_name="supervisor",
            setup_outcome=None,
            message_already_displayed=True,
        )

    access_hint = metadata.get("access_hint") if isinstance(metadata.get("access_hint"), str) else None
    if not verification_only and (runtime_command_kind != "start" or cli_task_materialized) and terminal_executor is not None and hasattr(terminal_executor, "run_terminal_command"):
        wrapped_runtime_command, wrapped_runtime_metadata = _wrap_command_for_execution_target(
            config_dir=paths.config_dir,
            execution_target=execution_target,
            command=runtime_command,
            cwd=runtime_cwd,
            preferred_vm_name=vm_name,
            preferred_cloud_resource_key=tracked_cloud_resource_key,
            preferred_cloud_vendor=tracked_cloud_vendor,
            preferred_cloud_region=tracked_cloud_region,
            preferred_cloud_shape=tracked_cloud_shape,
        )
        if not wrapped_runtime_command:
            wrapped_runtime_command = runtime_command
        terminal_cwd = None if execution_target in _REMOTE_EXECUTION_TARGETS else runtime_cwd
        started_in_terminal = bool(terminal_executor.run_terminal_command(command=wrapped_runtime_command, cwd=terminal_cwd))
        if started_in_terminal:
            initialize_state_store(paths.config_dir).record_run(
                run_id=f"repo-run:{_slugify(repo.name)}",
                command_name="repo_run",
                mode=current_mode.label,
                status="pass",
                summary=_compact_repo_summary(f"Run {repo.name}: opened staged terminal session"),
                repo_key=repo.repo_url,
                metadata={"command": runtime_command, "transport": "terminal_pane", "command_kind": runtime_command_kind},
            )
            command_label = (
                "CLI task"
                if cli_task_materialized
                else "verification" if runtime_command_kind == "verification" else "runtime"
            )
            message = (
                f"Supervisor agent opened {repo.name} in Duckln’s terminal pane and staged the stored "
                f"{command_label} path there: {runtime_command}. "
                f"{'Duckln is treating this as a concrete CLI task rather than a background repo service. ' if cli_task_materialized else 'This is not being claimed as a long-running repo service yet. '}"
                f"{access_hint or f'Use Duckln’s right-hand terminal pane in {project_dir}.'}"
            ).strip()
            _record_repo_state(
                paths,
                repo,
                project_dir,
                status="ready",
                summary=message,
                execution_target=execution_target,
                vm_name=vm_name,
                metadata={
                    "active": True,
                    "last_run_command": runtime_command,
                    "last_run_result": "terminal_staged",
                    "last_blocker": None,
                    "run_command_kind": runtime_command_kind,
                    "runtime_transport": "terminal_pane",
                    **metadata,
                },
            )
            _write_runtime_workflow_state(
                paths,
                repo=repo,
                issue_kind=None,
                issue_summary=None,
                runtime_status="interactive",
                runtime_command=runtime_command,
                runtime_command_kind=runtime_command_kind,
                runtime_cwd=runtime_cwd,
                execution_target=runtime_execution_target,
                vm_name=wrapped_runtime_metadata.get("vm_name"),
                attach_hint=access_hint or f"Use Duckln’s right-hand terminal pane in {runtime_cwd}.",
                logs_hint=transport_hint.logs_command,
                stop_hint=(
                    f"Duckln can stop the tracked Docker runtime for {repo.name} when you ask."
                    if transport_hint.stop_command
                    else f"Duckln can interrupt the active terminal-pane work for {repo.name} when you ask."
                ),
                stop_command=_wrap_command_for_execution_target(
                    config_dir=paths.config_dir,
                    execution_target=execution_target,
                    command=transport_hint.stop_command,
                    cwd=runtime_cwd,
                    preferred_vm_name=vm_name,
                    preferred_cloud_resource_key=tracked_cloud_resource_key,
                    preferred_cloud_vendor=tracked_cloud_vendor,
                    preferred_cloud_region=tracked_cloud_region,
                    preferred_cloud_shape=tracked_cloud_shape,
                )[0]
                if transport_hint.stop_command and execution_target in _REMOTE_EXECUTION_TARGETS
                else transport_hint.stop_command,
                docker_name=transport_hint.docker_name,
                cloud_resource_key=wrapped_runtime_metadata.get("cloud_resource_key"),
                cloud_vendor=wrapped_runtime_metadata.get("cloud_vendor"),
                cloud_region=wrapped_runtime_metadata.get("cloud_region"),
                cloud_shape=wrapped_runtime_metadata.get("cloud_shape"),
            )
            if transport_hint.connection_type == "docker" and transport_hint.docker_name:
                initialize_state_store(paths.config_dir).upsert_managed_resource(
                    resource_key=f"docker:{transport_hint.docker_name}",
                    resource_kind=transport_hint.resource_kind or "docker_runtime",
                    provider="docker",
                    display_name=transport_hint.docker_name,
                    execution_target="docker",
                    install_root=str(project_dir),
                    status="interactive",
                    idle_timeout_minutes=DEFAULT_IDLE_SHUTDOWN_MINUTES,
                    last_activity_at=_managed_last_activity_at(),
                    metadata={
                        **default_resource_tags(
                            resource_name=transport_hint.docker_name,
                            resource_kind=transport_hint.resource_kind or "docker_runtime",
                            execution_target="docker",
                            repo_key=repo.repo_url,
                        ),
                        "logs_command": transport_hint.logs_command,
                        "stop_command": transport_hint.stop_command,
                    },
                )
            return RepoBringUpResult(
                repo=repo,
                project_dir=project_dir,
                detected_files=detected_files,
                mode=current_mode,
                executed_commands=(runtime_command,),
                verification_passed=False,
                message=message,
                repo_family=RepoFamily.PYTHON,
                specialist_name="supervisor",
                setup_outcome=None,
                should_offer_repair=False,
            )

    if not verification_only and runtime_command_kind != "start" and not cli_task_materialized:
        message = (
            f"Supervisor agent did not launch {repo.name} as a long-running repo session because the stored command is only a bounded "
            f"{'verification' if runtime_command_kind == 'verification' else 'runtime check'} path: {runtime_command}."
        )
        if access_hint:
            message = f"{message} {access_hint}"
        display(message)
        _record_repo_state(
            paths,
            repo,
            project_dir,
            status="ready",
            summary=message,
            execution_target=execution_target,
            vm_name=vm_name,
            metadata={
                "active": True,
                "last_run_command": runtime_command,
                "last_run_result": "verification_only",
                "last_blocker": message,
                "run_command_kind": runtime_command_kind,
                **metadata,
            },
        )
        _write_runtime_workflow_state(
            paths,
            repo=repo,
            issue_kind="run_issue",
            issue_summary=message,
            runtime_status="ready",
            runtime_command=runtime_command,
            runtime_command_kind=runtime_command_kind,
            runtime_cwd=runtime_cwd,
            execution_target=runtime_execution_target,
            vm_name=vm_name,
            attach_hint=access_hint,
            logs_hint=transport_hint.logs_command,
            stop_command=_wrap_command_for_execution_target(
                config_dir=paths.config_dir,
                execution_target=execution_target,
                command=transport_hint.stop_command,
                cwd=runtime_cwd,
                preferred_vm_name=vm_name,
                preferred_cloud_resource_key=tracked_cloud_resource_key,
                preferred_cloud_vendor=tracked_cloud_vendor,
                preferred_cloud_region=tracked_cloud_region,
                preferred_cloud_shape=tracked_cloud_shape,
            )[0]
            if transport_hint.stop_command and execution_target in _REMOTE_EXECUTION_TARGETS
            else transport_hint.stop_command,
            docker_name=transport_hint.docker_name,
            cloud_resource_key=wrapped_runtime_metadata.get("cloud_resource_key"),
            cloud_vendor=wrapped_runtime_metadata.get("cloud_vendor"),
            cloud_region=wrapped_runtime_metadata.get("cloud_region"),
            cloud_shape=wrapped_runtime_metadata.get("cloud_shape"),
        )
        return RepoBringUpResult(
            repo=repo,
            project_dir=project_dir,
            detected_files=detected_files,
            mode=current_mode,
            executed_commands=(),
            verification_passed=False,
            message=message,
            repo_family=RepoFamily.PYTHON,
            specialist_name="supervisor",
            setup_outcome=None,
            should_offer_repair=False,
            message_already_displayed=True,
        )

    if terminal_executor is not None and hasattr(terminal_executor, "run_terminal_command"):
        wrapped_runtime_command, wrapped_runtime_metadata = _wrap_command_for_execution_target(
            config_dir=paths.config_dir,
            execution_target=execution_target,
            command=runtime_command,
            cwd=runtime_cwd,
            preferred_vm_name=vm_name,
            preferred_cloud_resource_key=tracked_cloud_resource_key,
            preferred_cloud_vendor=tracked_cloud_vendor,
            preferred_cloud_region=tracked_cloud_region,
            preferred_cloud_shape=tracked_cloud_shape,
        )
        if not wrapped_runtime_command:
            wrapped_runtime_command = runtime_command
        terminal_cwd = None if execution_target in _REMOTE_EXECUTION_TARGETS else runtime_cwd
        started_in_terminal = bool(terminal_executor.run_terminal_command(command=wrapped_runtime_command, cwd=terminal_cwd))
        if started_in_terminal:
            initialize_state_store(paths.config_dir).record_run(
                run_id=f"repo-run:{_slugify(repo.name)}",
                command_name="repo_run",
                mode=current_mode.label,
                status="pass",
                summary=_compact_repo_summary(f"Run {repo.name}: started in terminal pane"),
                repo_key=repo.repo_url,
                metadata={"command": runtime_command, "transport": "terminal_pane"},
            )
            message = (
                f"Supervisor agent started {repo.name} in Duckln’s terminal pane from the managed workspace. "
                f"Run command: {runtime_command}. "
                f"{access_hint or ''}"
            ).strip()
            _record_repo_state(
                paths,
                repo,
                project_dir,
                status="running",
                summary=message,
                execution_target=execution_target,
                vm_name=vm_name,
                metadata={
                    "active": True,
                    "last_run_command": runtime_command,
                    "last_run_result": "running",
                    "last_blocker": None,
                    "run_command_kind": runtime_command_kind,
                    "runtime_transport": "terminal_pane",
                    **metadata,
                },
            )
            _write_runtime_workflow_state(
                paths,
                repo=repo,
                issue_kind=None,
                issue_summary=None,
                runtime_status="running",
                runtime_command=runtime_command,
                runtime_command_kind=runtime_command_kind,
                runtime_cwd=runtime_cwd,
                execution_target=runtime_execution_target,
                vm_name=wrapped_runtime_metadata.get("vm_name"),
                attach_hint=access_hint or f"Use Duckln’s right-hand terminal pane in {runtime_cwd}.",
                logs_hint=transport_hint.logs_command,
                stop_hint=(
                    f"Duckln can stop the tracked Docker runtime for {repo.name} when you ask."
                    if transport_hint.stop_command
                    else f"Duckln can interrupt {repo.name} from the terminal pane when you ask."
                ),
                stop_command=_wrap_command_for_execution_target(
                    config_dir=paths.config_dir,
                    execution_target=execution_target,
                    command=transport_hint.stop_command,
                    cwd=runtime_cwd,
                    preferred_vm_name=vm_name,
                    preferred_cloud_resource_key=tracked_cloud_resource_key,
                    preferred_cloud_vendor=tracked_cloud_vendor,
                    preferred_cloud_region=tracked_cloud_region,
                    preferred_cloud_shape=tracked_cloud_shape,
                )[0]
                if transport_hint.stop_command and execution_target in _REMOTE_EXECUTION_TARGETS
                else transport_hint.stop_command,
                docker_name=transport_hint.docker_name,
                cloud_resource_key=wrapped_runtime_metadata.get("cloud_resource_key"),
                cloud_vendor=wrapped_runtime_metadata.get("cloud_vendor"),
                cloud_region=wrapped_runtime_metadata.get("cloud_region"),
                cloud_shape=wrapped_runtime_metadata.get("cloud_shape"),
            )
            if transport_hint.connection_type == "docker" and transport_hint.docker_name:
                initialize_state_store(paths.config_dir).upsert_managed_resource(
                    resource_key=f"docker:{transport_hint.docker_name}",
                    resource_kind=transport_hint.resource_kind or "docker_runtime",
                    provider="docker",
                    display_name=transport_hint.docker_name,
                    execution_target="docker",
                    install_root=str(project_dir),
                    status="running",
                    idle_timeout_minutes=DEFAULT_IDLE_SHUTDOWN_MINUTES,
                    last_activity_at=_managed_last_activity_at(),
                    metadata={
                        **default_resource_tags(
                            resource_name=transport_hint.docker_name,
                            resource_kind=transport_hint.resource_kind or "docker_runtime",
                            execution_target="docker",
                            repo_key=repo.repo_url,
                        ),
                        "logs_command": transport_hint.logs_command,
                        "stop_command": transport_hint.stop_command,
                    },
                )
            return RepoBringUpResult(
                repo=repo,
                project_dir=project_dir,
                detected_files=detected_files,
                mode=current_mode,
                executed_commands=(runtime_command,),
                verification_passed=True,
                message=message,
                repo_family=RepoFamily.PYTHON,
                specialist_name="supervisor",
                setup_outcome=None,
            )

    log_path = _runtime_log_path(paths, repo)
    wrapped_runtime_command, wrapped_runtime_metadata = _wrap_command_for_execution_target(
        config_dir=paths.config_dir,
        execution_target=execution_target,
        command=runtime_command,
        cwd=runtime_cwd,
        preferred_vm_name=vm_name,
        preferred_cloud_resource_key=tracked_cloud_resource_key,
        preferred_cloud_vendor=tracked_cloud_vendor,
        preferred_cloud_region=tracked_cloud_region,
        preferred_cloud_shape=tracked_cloud_shape,
    )
    if not wrapped_runtime_command:
        message = f"Supervisor agent could not resolve the active {execution_target} target for {repo.name}."
        display(message)
        return RepoBringUpResult(
            repo=repo,
            project_dir=project_dir,
            detected_files=detected_files,
            mode=current_mode,
            executed_commands=(),
            verification_passed=False,
            message=message,
            repo_family=RepoFamily.PYTHON,
            specialist_name="supervisor",
            setup_outcome=None,
            should_offer_repair=False,
        )
    session = runner_instance.start_background(
        wrapped_runtime_command,
        cwd=None if execution_target in _REMOTE_EXECUTION_TARGETS else runtime_cwd,
        log_path=log_path,
    )
    initialize_state_store(paths.config_dir).record_run(
        run_id=f"repo-run:{_slugify(repo.name)}",
        command_name="repo_run",
        mode=current_mode.label,
        status="pass",
        summary=_compact_repo_summary(f"Run {repo.name}: started"),
        repo_key=repo.repo_url,
        metadata={"command": runtime_command, "pid": session.pid},
    )
    logs_hint = f"tail -n 40 {session.log_path}" if session.log_path else None
    message = (
        f"Supervisor agent started {repo.name} in Duckln’s managed workspace. "
        f"Run command: {runtime_command}. "
        f"PID: {session.pid}. "
        f"{f'Logs: {session.log_path}. ' if session.log_path else ''}"
        f"{access_hint or ''}"
    ).strip()
    _record_repo_state(
        paths,
        repo,
        project_dir,
        status="running",
        summary=message,
        execution_target=execution_target,
        vm_name=vm_name,
        metadata={
            "active": True,
            "last_run_command": runtime_command,
            "last_run_result": "running",
            "last_blocker": None,
            "run_command_kind": runtime_command_kind,
            "runtime_pid": session.pid,
            "runtime_log_path": session.log_path,
            **metadata,
        },
    )
    _write_runtime_workflow_state(
        paths,
        repo=repo,
        issue_kind=None,
        issue_summary=None,
        runtime_status="running",
        runtime_command=runtime_command,
        runtime_command_kind=runtime_command_kind,
        runtime_cwd=runtime_cwd,
        runtime_pid=session.pid,
        runtime_log_path=session.log_path,
        execution_target=runtime_execution_target,
        vm_name=wrapped_runtime_metadata.get("vm_name"),
        attach_hint=access_hint or f"Use the managed repo directory at {runtime_cwd}.",
        logs_hint=logs_hint,
        stop_hint=f"Duckln can stop {repo.name} from here when you ask.",
        stop_command=_wrap_command_for_execution_target(
            config_dir=paths.config_dir,
            execution_target=execution_target,
            command=transport_hint.stop_command,
            cwd=runtime_cwd,
            preferred_vm_name=vm_name,
            preferred_cloud_resource_key=tracked_cloud_resource_key,
            preferred_cloud_vendor=tracked_cloud_vendor,
            preferred_cloud_region=tracked_cloud_region,
            preferred_cloud_shape=tracked_cloud_shape,
        )[0]
        if transport_hint.stop_command and execution_target in _REMOTE_EXECUTION_TARGETS
        else transport_hint.stop_command,
        docker_name=transport_hint.docker_name,
        cloud_resource_key=wrapped_runtime_metadata.get("cloud_resource_key"),
        cloud_vendor=wrapped_runtime_metadata.get("cloud_vendor"),
        cloud_region=wrapped_runtime_metadata.get("cloud_region"),
        cloud_shape=wrapped_runtime_metadata.get("cloud_shape"),
    )
    if transport_hint.connection_type == "docker" and transport_hint.docker_name:
        initialize_state_store(paths.config_dir).upsert_managed_resource(
            resource_key=f"docker:{transport_hint.docker_name}",
            resource_kind=transport_hint.resource_kind or "docker_runtime",
            provider="docker",
            display_name=transport_hint.docker_name,
            execution_target="docker",
            install_root=str(project_dir),
            status="running",
            idle_timeout_minutes=DEFAULT_IDLE_SHUTDOWN_MINUTES,
            last_activity_at=_managed_last_activity_at(),
            metadata={
                **default_resource_tags(
                    resource_name=transport_hint.docker_name,
                    resource_kind=transport_hint.resource_kind or "docker_runtime",
                    execution_target="docker",
                    repo_key=repo.repo_url,
                ),
                "logs_command": transport_hint.logs_command,
                "stop_command": transport_hint.stop_command,
            },
        )
    return RepoBringUpResult(
        repo=repo,
        project_dir=project_dir,
        detected_files=detected_files,
        mode=current_mode,
        executed_commands=(runtime_command,),
        verification_passed=True,
        message=message,
        repo_family=RepoFamily.PYTHON,
        specialist_name="supervisor",
        setup_outcome=None,
    )


def _preferred_make_target(makefile_path: Path) -> str | None:
    content = makefile_path.read_text(encoding="utf-8", errors="ignore")
    targets = {match.group(1) for match in re.finditer(r"^([A-Za-z0-9_.-]+):", content, flags=re.MULTILINE)}
    for candidate in ("setup", "install", "init", "bootstrap"):
        if candidate in targets:
            return candidate
    return None


def _infer_make_steps(project_dir: Path, detected_files: tuple[str, ...]) -> list[RepoBringUpStep]:
    if "Makefile" not in detected_files:
        return []
    make_target = _preferred_make_target(project_dir / "Makefile")
    if make_target is None:
        return []
    return [
        RepoBringUpStep(
            purpose=f"Run make {make_target}",
            command=f"make {make_target}",
            verification_command="test -f Makefile",
        )
    ]


_ERROR_HINT_RE = re.compile(
    r"(error|err!|exception|not found|cannot find|no such|fatal|failed|unresolved|"
    r"missing|permission denied|enospc|killed|traceback|undefined)",
    re.IGNORECASE,
)


def _first_error_hint(text: str, *, max_len: int = 160) -> str:
    """Plan 183 F5: a short one-line hint of WHY a step failed — the most error-looking line in
    the output (preferring an explicit error line, else the last non-empty line). Bounded length."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return ""
    for ln in reversed(lines):  # the operative error is usually near the end
        if _ERROR_HINT_RE.search(ln):
            return ln[:max_len]
    return lines[-1][:max_len]


def _node_install_command(detected_files: tuple[str, ...]) -> tuple[str, str | None]:
    # Plan 173 F2: ALWAYS include devDependencies — the build tools (vite/@vitejs/plugin-react/
    # typescript/webpack/tsc) are devDeps; a `NODE_ENV=production` env would otherwise strip them
    # and the build fails (the JustHireMe `vite build` "Cannot find package 'vite'" failure).
    if "pnpm-lock.yaml" in detected_files:
        return "pnpm install --frozen-lockfile --prod=false", "pnpm-lock.yaml"
    if "yarn.lock" in detected_files:
        return "yarn install --frozen-lockfile --production=false", "yarn.lock"
    if "package-lock.json" in detected_files:
        return "npm ci --include=dev", "package-lock.json"
    return "npm install --include=dev", None


_MONOREPO_SUBDIR_NAMES = ("frontend", "client", "web", "ui", "backend", "server", "api", "app")
_MONOREPO_MARKERS = ("pnpm-workspace.yaml", "lerna.json", "turbo.json", "nx.json")


def _subdir_node_install_steps(
    project_dir: Path, detected_files: tuple[str, ...], target: str,
) -> list[RepoBringUpStep]:
    """Plan 183 F8/F9(a): emit a Node devDeps install for each SUB-PACKAGE (a subdir with its own
    package.json) so a monorepo's frontend/backend devDeps are present before its build — the
    root install alone misses them. Scans the local clone when available; otherwise (remote /
    no local files) falls back to ONE guarded step that installs any present common sub-package
    on the target. Always uses `--include=dev` so build tools (vite/tsc) aren't stripped."""
    steps: list[RepoBringUpStep] = []
    found_local: list[str] = []
    try:
        base = Path(project_dir)
        if base.is_dir():
            for name in _MONOREPO_SUBDIR_NAMES:
                if (base / name / "package.json").is_file():
                    found_local.append(name)
    except Exception:
        found_local = []
    if found_local:
        for name in found_local:
            try:
                sub_files = tuple(p.name for p in (base / name).iterdir() if p.is_file())
            except Exception:
                sub_files = ()
            install_cmd, _lock = _node_install_command(sub_files)
            steps.append(RepoBringUpStep(
                purpose=f"Install Node dependencies in {name}/ (sub-package)",
                command=f"(cd {name} && {install_cmd})",
                verification_command=file_exists_check(f"{name}/node_modules", execution_target=target),
            ))
        return steps
    # No local clone to scan — if a monorepo marker is present, install present sub-packages on
    # the target with one guarded, idempotent step (a no-op when a subdir is absent).
    if any(m in detected_files for m in _MONOREPO_MARKERS):
        names = " ".join(_MONOREPO_SUBDIR_NAMES)
        steps.append(RepoBringUpStep(
            purpose="Install sub-package Node dependencies (monorepo)",
            command=(
                f'for d in {names}; do if [ -f "$d/package.json" ]; then '
                f'(cd "$d" && npm install --include=dev) || true; fi; done'
            ),
            verification_command="true",
        ))
    return steps


def _read_readme_excerpt(readme_path: Path) -> str:
    if not readme_path.exists():
        return ""
    return readme_path.read_text(encoding="utf-8", errors="ignore")[:4000]


def _safe_probe_system() -> SystemProbe:
    return SystemProbe(
        operating_system=platform.system() or "Unknown",
        architecture=platform.machine() or "unknown",
        cpu_logical_cores=os.cpu_count(),
        ram_bytes=None,
        disk_free_bytes=None,
        python_version=platform.python_version(),
        gpu=GpuProbeState(
            backend="cpu",
            summary="Repo bring-up planning uses a CPU-safe local probe.",
            cuda_capable=False,
            cuda_available=False,
            mps_capable=False,
            mps_available=False,
        ),
    )


def _summarize_specialist_plan(
    *,
    detected_files: tuple[str, ...],
    steps: list[RepoBringUpStep],
    repo_family: RepoFamily,
    specialist_name: str,
) -> str:
    if steps:
        return (
            f"Supervisor classified this as {repo_family.value} and routed to {specialist_name} specialist. "
            f"Detected {', '.join(detected_files)} and inferred {len(steps)} setup step(s)."
        )
    if detected_files:
        return (
            f"Supervisor classified this as {repo_family.value} and routed to {specialist_name} specialist. "
            f"Detected {', '.join(detected_files)} but no safe automatic setup step was inferred yet."
        )
    return (
        f"Supervisor classified this as {repo_family.value} and routed to {specialist_name} specialist. "
        "No known setup files were detected, so Duckln cannot infer a safe setup step yet."
    )


def _attach_recovery_summary(plan: RepoBringUpPlan, recovery: DebugRecoveryAssessment) -> RepoBringUpPlan:
    return RepoBringUpPlan(
        repo=plan.repo,
        project_dir=plan.project_dir,
        detected_files=plan.detected_files,
        steps=plan.steps,
        summary=f"{plan.summary} {recovery.summary}",
        repo_family=recovery.reroute_repo_family or plan.repo_family,
        specialist_name=plan.specialist_name,
        playbook_path=plan.playbook_path,
        runtime_provider=plan.runtime_provider,
        execution_target=plan.execution_target,
        subagent_prompt=plan.subagent_prompt,
        subagent_workspace_files=plan.subagent_workspace_files,
    )


def _is_low_confidence_repo_family(inspection: RepoBringUpInspection, repo_family: RepoFamily) -> bool:
    if repo_family is not RepoFamily.PYTHON:
        return False
    if inspection.detected_files:
        return False
    signal_text = " ".join(
        (
            inspection.repo.name,
            inspection.repo.description,
            inspection.repo.category,
            inspection.repo.framework,
            inspection.readme_excerpt,
        )
    ).lower()
    return not any(
        signal in signal_text
        for signal in (
            "python",
            "pip",
            "venv",
            "torch",
            "node",
            "typescript",
            "go",
            "cmake",
            "c++",
            "rust",
            "docker",
            "whisper",
            "diffusion",
            "ollama",
        )
    )


def _suggest_alternate_repo_family(
    inspection: RepoBringUpInspection,
    *,
    current_family: RepoFamily,
) -> RepoFamily | None:
    detected_files = set(inspection.detected_files)
    if detected_files & {"package.json", "pnpm-lock.yaml", "yarn.lock", "package-lock.json"} and current_family is not RepoFamily.NODE_TYPESCRIPT:
        return RepoFamily.NODE_TYPESCRIPT
    if detected_files & {"go.mod", "main.go"} and current_family is not RepoFamily.GO_NATIVE:
        return RepoFamily.GO_NATIVE
    if detected_files & {"Cargo.toml", "rust-toolchain.toml", "rust-toolchain"} and current_family is not RepoFamily.RUST:
        return RepoFamily.RUST
    if detected_files & {"CMakeLists.txt", "configure"} and current_family is not RepoFamily.CPP_NATIVE:
        return RepoFamily.CPP_NATIVE
    if detected_files & {"requirements.txt", "pyproject.toml", "setup.py", "environment.yml"} and current_family is not RepoFamily.PYTHON:
        return RepoFamily.PYTHON
    return None


def assess_repo_preflight(
    repo: RepoCatalogRecord,
    *,
    config_dir: Path,
    execution_target: str = "local",
    system_probe: SystemProbe | None = None,
    context_service: AgentContextService | None = None,
) -> RepoPreflightAssessment:
    """Build a concise supervisor-owned setup-fit assessment."""

    project_dir = resolve_managed_project_dir(config_dir, repo)
    inspection = inspect_repo_for_bringup(
        repo,
        project_dir,
        system_probe=system_probe,
        execution_target=execution_target,
        config_dir=config_dir,
        context_service=context_service,
    )
    knowledge = inspection.repo_knowledge
    probe = inspection.system_probe
    feasibility = build_repo_feasibility_assessment(
        system_probe=probe,
        repo=repo,
        repo_knowledge=knowledge,
    )
    fit_status = feasibility.fit_label
    recommended_action = "set_it_up"
    if fit_status == "workable_but_tight":
        recommended_action = "inspect_requirements"
    elif fit_status == "not_recommended":
        recommended_action = "pick_lighter_repo"
    rationale_parts: list[str] = [feasibility.rationale, feasibility.trust.inferred]
    if knowledge is not None and knowledge.local_vm_recommendation:
        rationale_parts.append(knowledge.local_vm_recommendation)
    if feasibility.failure_modes:
        rationale_parts.append(f"Likely friction: {', '.join(feasibility.failure_modes[:3])}.")
    rationale = " ".join(dict.fromkeys(part.strip() for part in rationale_parts if part.strip()))
    if fit_status == "comfortable":
        summary = f"{repo.name} looks comfortable on this machine. {rationale}"
    elif fit_status == "workable_but_tight":
        summary = f"{repo.name} can work here, but it will be tight. {rationale}"
    else:
        summary = f"{repo.name} does not look like a strong fit on this machine. {rationale}"
    return RepoPreflightAssessment(
        repo=repo,
        fit_status=fit_status,
        rationale=rationale,
        recommended_action=recommended_action,
        summary=summary,
        feasibility=feasibility,
        local_vm_recommendation=knowledge.local_vm_recommendation if knowledge is not None else None,
    )


def _mode_decision_for_step(
    mode: ControlMode,
    command: str,
    *,
    approve: Callable[[str], bool] | None,
    prompt: str,
    project_dir: Path | None = None,
    display: Callable[[str], None] | None = None,
    step_source: str | None = None,
):
    if step_source == "readme-prerequisite" or _is_readonly_prerequisite_check(command):
        return ModeDecision(
            allowed=True,
            requires_approval=False,
            auto_run=True,
            reason="Read-only prerequisite checks are safe diagnostics.",
        ), command
    assessment = assess_command(command)
    if _is_dependency_install_command(command):
        approval_request = build_dependency_approval_request(
            prompt=prompt,
            command=command,
            project_dir=project_dir,
        )
        if display is not None:
            display(_dependency_install_activity_trace(request=approval_request))
        approval_decision = _approve_dependency_install(
            approve=approve,
            request=approval_request,
        )
        if not approval_decision.approved:
            return ModeDecision(
                allowed=False,
                requires_approval=True,
                auto_run=False,
                reason="Duckln requires explicit approval before dependency installs in every mode.",
            ), command
        effective_command = build_approved_dependency_command(
            request=approval_request,
            decision=approval_decision,
        )
        if not effective_command:
            return ModeDecision(
                allowed=False,
                requires_approval=True,
                auto_run=False,
                reason="Duckln could not materialize a safe dependency command from the selected approval scope.",
            ), command
        effective_assessment = assess_command(effective_command)
        return evaluate_mode_action(
            mode,
            effective_assessment,
            is_ai_suggested=True,
            user_approved=approval_decision.approved,
        ), effective_command
    decision = evaluate_mode_action(mode, assessment, is_ai_suggested=True)
    if decision.allowed:
        return decision, command
    if not decision.requires_approval:
        return decision, command

    approved = False if approve is None else approve(f"{prompt}: {command}")
    return evaluate_mode_action(mode, assessment, is_ai_suggested=True, user_approved=approved), command


def _is_readonly_prerequisite_check(command: str) -> bool:
    normalized = " ".join(str(command or "").strip().casefold().split())
    readonly_prefixes = (
        "node -e ",
        "python -m pip --version",
        "python3 -m pip --version",
        "docker --version",
        "git --version",
        "go version",
        "cargo --version",
        "rustc --version",
        "cmake --version",
        "make --version",
    )
    return any(normalized.startswith(prefix) for prefix in readonly_prefixes)


def _run_verification(
    runner: ControlledCommandRunner,
    command: str,
    *,
    cwd: str,
    display: Callable[[str], None],
    config_dir: Path | None = None,
    execution_target: str = "local",
) -> bool:
    wrapped_command, _metadata = _wrap_command_for_execution_target(
        config_dir=config_dir or Path.cwd(),
        execution_target=execution_target,
        command=command,
        cwd=cwd,
    )
    if wrapped_command is None:
        display(f"Duckln could not resolve the active {execution_target} execution target for verification.")
        return False
    result = runner.run(wrapped_command, cwd=None if execution_target in _REMOTE_EXECUTION_TARGETS else cwd)
    if result.exit_code == 0 and not result.timed_out:
        return True
    display(_command_failure_message("Verification failed", result.stderr, command=result.command, display=display))
    return False


def _execute_plan_with_bounded_recovery(
    *,
    plan: RepoBringUpPlan,
    current_mode: ControlMode,
    paths: ConfigPaths,
    runner: ControlledCommandRunner,
    supervisor: RepoBringUpSupervisor,
    system_probe: SystemProbe | None,
    approve: Callable[[str], bool] | None,
    display: Callable[[str], None],
    executed_commands: list[str],
    verification_passed: bool,
    started_at: float,
    config_dir: Path,
    on_repair_success: Callable[[str, str, str, str, str, str], None] | None = None,
) -> RepoBringUpResult | None:
    active_plan = plan
    recovery_count = 0
    max_recovery_attempts = 3
    last_recovery: DebugRecoveryAssessment | None = None
    _last_failed_command: str = ""
    _last_raw_error_output: str = ""
    _last_recovery_command: str = ""
    # Maps (command, exit_code) -> stderr fingerprint of the FIRST observed failure.
    # On the second identical (command, exit_code, fingerprint) failure we stop the
    # loop and surface a clear "tried this twice, same error" message. This is the
    # hard guardrail against the 5x-repeat hangs we saw on JustHireMe.
    seen_failures: dict[tuple[str, int], str] = {}

    while True:
        for step in active_plan.steps:
            decision, command_to_run = _mode_decision_for_step(
                current_mode,
                step.command,
                approve=approve,
                prompt=step.purpose,
                project_dir=active_plan.project_dir,
                display=display,
                step_source=step.source,
            )
            if not decision.allowed:
                message = (
                    f"{_agent_label(active_plan.specialist_name)} paused before '{step.purpose}' because "
                    f"approval is still required in {current_mode.label}."
                )
                display(message)
                write_workflow_state(
                    paths.config_dir,
                    {
                        "active_repo_key": active_plan.repo.repo_url,
                        "active_repo_name": active_plan.repo.name,
                        "active_issue_kind": "setup_approval",
                        "active_issue_summary": message,
                        "active_repair_phase": "awaiting_setup_approval",
                    },
                )
                _write_repo_objective_state(
                    paths,
                    repo=active_plan.repo,
                    kind="repo_setup",
                    status="needs_user_decision",
                    goal=f"Set up {active_plan.repo.name} and verify the smallest documented path.",
                    execution_target=active_plan.execution_target,
                    requires_user_decision=True,
                    resume_hint=f"Last time Duckln was setting up {active_plan.repo.name}.",
                    last_blocker=message,
                )
                _record_repo_state(
                    paths,
                    active_plan.repo,
                    active_plan.project_dir,
                    status="awaiting_approval",
                    summary=message,
                    execution_target=active_plan.execution_target,
                )
                return RepoBringUpResult(
                    repo=active_plan.repo,
                    project_dir=active_plan.project_dir,
                    detected_files=active_plan.detected_files,
                    mode=current_mode,
                    executed_commands=tuple(executed_commands),
                    verification_passed=False,
                    message=message,
                    repo_family=active_plan.repo_family,
                    specialist_name=active_plan.specialist_name,
                    setup_outcome=None,
                )

            total_steps = max(1, len(active_plan.steps))
            current_step_index = active_plan.steps.index(step) + 1
            write_workflow_state(
                paths.config_dir,
                {
                    "active_repo_key": active_plan.repo.repo_url,
                    "active_repo_name": active_plan.repo.name,
                    "active_issue_kind": "setup_in_progress",
                    "active_issue_summary": f"Duckln is on setup step {current_step_index}/{total_steps}: {step.purpose}.",
                    "active_repair_phase": f"setup_step_{current_step_index}_pending",
                },
            )
            _write_repo_objective_state(
                paths,
                repo=active_plan.repo,
                kind="repo_setup",
                status="active",
                goal=f"Set up {active_plan.repo.name} and verify the smallest documented path.",
                execution_target=active_plan.execution_target,
                requires_user_decision=False,
                resume_hint=f"Last time Duckln was setting up {active_plan.repo.name}.",
            )
            display(
                _agent_phase_status(
                    _agent_label(active_plan.specialist_name),
                    step.purpose,
                    phase_index=min(4, 2 + current_step_index),
                    phase_total=max(4, 2 + total_steps),
                    started_at=started_at,
                    detail=f"step {current_step_index}/{total_steps}",
                )
            )
            wrapped_command, _metadata = _wrap_command_for_execution_target(
                config_dir=config_dir,
                execution_target=active_plan.execution_target,
                command=command_to_run,
                cwd=str(active_plan.project_dir),
            )
            if not wrapped_command:
                message = f"Duckln could not resolve the active {active_plan.execution_target} target for '{step.purpose}'."
                display(message)
                _record_repo_state(
                    paths,
                    active_plan.repo,
                    active_plan.project_dir,
                    status="setup_blocked",
                    summary=message,
                    execution_target=active_plan.execution_target,
                )
                return RepoBringUpResult(
                    repo=active_plan.repo,
                    project_dir=active_plan.project_dir,
                    detected_files=active_plan.detected_files,
                    mode=current_mode,
                    executed_commands=tuple(executed_commands),
                    verification_passed=False,
                    message=message,
                    repo_family=active_plan.repo_family,
                    specialist_name=active_plan.specialist_name,
                    setup_outcome=None,
                )
            result = runner.run(
                wrapped_command,
                cwd=None if active_plan.execution_target in _REMOTE_EXECUTION_TARGETS else str(active_plan.project_dir),
            )
            executed_commands.append(command_to_run)
            if result.exit_code != 0 or result.timed_out:
                failure_message = _command_failure_message(
                    f"{step.purpose} failed",
                    result.stderr,
                    command=command_to_run,
                    display=display,
                    execution_target=active_plan.execution_target,
                )
                # Only feed actual command output to the recovery classifier — never
                # let Duckln's own narrative summary (which contains tokens like "rust"
                # or "cargo" from prior reroutes) become the input that decides the
                # next reroute. That feedback loop was the cause of bogus rust/cargo
                # rerouting on Node repos.
                raw_failure_output = (
                    (result.stderr or "").strip()
                    or (result.stdout or "").strip()
                )
                # Duplicate-failure guard: if the same (command, exit_code) just
                # failed with the same stderr fingerprint, stop the recovery loop
                # immediately. This prevents the 5x-repeat hangs we saw when an
                # install hint failed identically each time.
                # Plan 60 Bug F: result.exit_code can be None (timeouts, killed
                # processes). int(None) raises TypeError — use -1 sentinel so
                # the dedup key is still hashable.
                dup_key = (command_to_run, int(result.exit_code) if result.exit_code is not None else -1)
                fingerprint = fingerprint_stderr(raw_failure_output)
                prior_fingerprint = seen_failures.get(dup_key)
                if prior_fingerprint is not None and prior_fingerprint == fingerprint:
                    first_stderr_line = (raw_failure_output or "").splitlines()[0].strip() if raw_failure_output else ""
                    safe_stderr_line = redact_sensitive_data(first_stderr_line)
                    repeat_segments = [
                        f"Duckln tried `{command_to_run}` twice and got the same error both times: "
                        f"{safe_stderr_line}.",
                    ]
                    if last_recovery is not None:
                        repeat_segments.append(
                            f"Debug/Recovery classified {last_recovery.failure_type.value}: "
                            f"{last_recovery.summary}"
                        )
                    repeat_segments.append("Stopping the repair loop — manual fix needed.")
                    repeat_message = " ".join(repeat_segments)
                    display(repeat_message)
                    _record_repo_state(
                        paths,
                        active_plan.repo,
                        active_plan.project_dir,
                        status="setup_failed",
                        summary=repeat_message,
                        execution_target=active_plan.execution_target,
                    )
                    # Plan 58 Bug D: persist the failure so future Duckln runs
                    # (after a restart) can skip this command before re-proposing it.
                    try:
                        from state.access import write_failure_memory_state
                        write_failure_memory_state(
                            paths.config_dir,
                            repo_slug=active_plan.repo.repo_url or active_plan.repo.name,
                            command=command_to_run,
                            execution_target=active_plan.execution_target,
                            exit_code=int(result.exit_code) if result.exit_code is not None else -1,
                            stderr_fingerprint=fingerprint,
                        )
                    except Exception:
                        pass
                    return RepoBringUpResult(
                        repo=active_plan.repo,
                        project_dir=active_plan.project_dir,
                        detected_files=active_plan.detected_files,
                        mode=current_mode,
                        executed_commands=tuple(executed_commands),
                        verification_passed=False,
                        should_offer_repair=False,
                        message=repeat_message,
                        repo_family=active_plan.repo_family,
                        specialist_name=active_plan.specialist_name,
                        recovery_decision=None if last_recovery is None else last_recovery.decision,
                        failure_type=None if last_recovery is None else last_recovery.failure_type,
                        setup_outcome=None,
                    )
                # Plan 59 Fix 2: record EVERY failed install command (not just
                # dedup-fires), so future Duckln runs see it. The 24h window in
                # lookup_recent_failure prevents stale records from being sticky.
                try:
                    from state.access import write_failure_memory_state
                    write_failure_memory_state(
                        paths.config_dir,
                        repo_slug=active_plan.repo.repo_url or active_plan.repo.name,
                        command=command_to_run,
                        execution_target=active_plan.execution_target,
                        exit_code=int(result.exit_code) if result.exit_code is not None else -1,
                        stderr_fingerprint=fingerprint,
                    )
                except Exception:
                    pass
                seen_failures[dup_key] = fingerprint
                if recovery_count >= max_recovery_attempts:
                    message = (
                        failure_message
                        if last_recovery is None
                        else f"{failure_message} {last_recovery.summary}"
                    )
                    display(message)
                    _record_repo_state(
                        paths,
                        active_plan.repo,
                        active_plan.project_dir,
                        status="setup_failed",
                        summary=message,
                        execution_target=active_plan.execution_target,
                    )
                    # Plan 59 Fix 2: also persist at the recovery-exhausted
                    # branch (after 3 retries) so this distinct exit path is
                    # captured. This catches failures that don't fire dedup
                    # (e.g. each retry tried a slightly different command).
                    try:
                        from state.access import write_failure_memory_state
                        write_failure_memory_state(
                            paths.config_dir,
                            repo_slug=active_plan.repo.repo_url or active_plan.repo.name,
                            command=command_to_run,
                            execution_target=active_plan.execution_target,
                            exit_code=int(result.exit_code) if result.exit_code is not None else -1,
                            stderr_fingerprint=fingerprint,
                        )
                    except Exception:
                        pass
                    # Plan 65 Phase 6: when DUCKLN_HARNESS=1, escalate the
                    # exhausted recovery to the multi-agent flow before giving
                    # up. The legacy cheap classifier already exhausted; the
                    # harness spawns investigate + search + memory agents in
                    # parallel to diagnose and propose a fix.
                    try:
                        from duckln.harness import (
                            harness_enabled, run_multi_agent_recovery, RecoveryRequest,
                        )
                        if harness_enabled():
                            from duckln.ai_client import build_default_llm_client_or_none
                            llm = build_default_llm_client_or_none(paths.config_dir)
                            if llm is not None:
                                outcome = run_multi_agent_recovery(
                                    RecoveryRequest(
                                        failed_command=command_to_run,
                                        step_purpose=step.purpose,
                                        stderr=result.stderr or "",
                                        stdout=result.stdout or "",
                                        exit_code=int(result.exit_code) if result.exit_code is not None else -1,
                                        repo_slug=active_plan.repo.repo_url or active_plan.repo.name,
                                        project_dir=active_plan.project_dir,
                                        execution_target=active_plan.execution_target,
                                        config_dir=paths.config_dir,
                                        mode=current_mode,
                                    ),
                                    llm_client=llm,
                                    display=display,
                                    approve=approve,
                                )
                                display(f"Harness recovery: {outcome.summary} (session {outcome.session_id})")
                                if outcome.succeeded:
                                    # Reset attempt counter so the step is re-run on the next loop iteration.
                                    recovery_count = 0
                                    last_recovery = None
                                    continue
                    except Exception as _harness_exc:
                        display(f"Harness recovery unavailable: {_harness_exc}")
                    return RepoBringUpResult(
                        repo=active_plan.repo,
                        project_dir=active_plan.project_dir,
                        detected_files=active_plan.detected_files,
                        mode=current_mode,
                        executed_commands=tuple(executed_commands),
                        verification_passed=False,
                        message=message,
                        repo_family=active_plan.repo_family,
                        specialist_name=active_plan.specialist_name,
                        recovery_decision=None if last_recovery is None else last_recovery.decision,
                        failure_type=None if last_recovery is None else last_recovery.failure_type,
                        setup_outcome=None,
                    )
                _show_setup_recovery_evidence(
                    repo_name=active_plan.repo.name,
                    failed_command=command_to_run,
                    failure_message=raw_failure_output,
                    display=display,
                    execution_target=active_plan.execution_target,
                )
                _last_failed_command = command_to_run
                _last_raw_error_output = raw_failure_output
                active_plan, last_recovery, terminal_result = _apply_recovery_decision(
                    plan=active_plan,
                    failed_step=step,
                    failed_command=step.command,
                    failure_message=failure_message,
                    raw_error_output=raw_failure_output,
                    verification_failure=f"{step.purpose} command failed.",
                    current_mode=current_mode,
                    paths=paths,
                    supervisor=supervisor,
                    system_probe=system_probe,
                    display=display,
                    executed_commands=executed_commands,
                    started_at=started_at,
                )
                if active_plan.steps:
                    _last_recovery_command = active_plan.steps[0].command
                recovery_count += 1
                if terminal_result is not None:
                    return terminal_result
                break

            if step.verification_command is not None:
                verification_passed = _run_verification(
                    runner,
                    step.verification_command,
                    cwd=str(active_plan.project_dir),
                    display=display,
                    config_dir=config_dir,
                    execution_target=active_plan.execution_target,
                )
                if not verification_passed:
                    failure_message = f"Verification failed after '{step.purpose}'."
                    write_workflow_state(
                        paths.config_dir,
                        {
                            "active_repo_key": active_plan.repo.repo_url,
                            "active_repo_name": active_plan.repo.name,
                            "active_issue_kind": "setup_verification_failed",
                            "active_issue_summary": failure_message,
                            "active_repair_phase": f"setup_step_{current_step_index}_verification_failed",
                        },
                    )
                    _write_repo_objective_state(
                        paths,
                        repo=active_plan.repo,
                        kind="repo_setup",
                        status="needs_user_decision",
                        goal=f"Set up {active_plan.repo.name} and verify the smallest documented path.",
                        execution_target=active_plan.execution_target,
                        requires_user_decision=True,
                        resume_hint=f"Last time Duckln was setting up {active_plan.repo.name}.",
                        last_blocker=failure_message,
                    )
                    if recovery_count >= max_recovery_attempts:
                        message = (
                            failure_message
                            if last_recovery is None
                            else f"{failure_message} {last_recovery.summary}"
                        )
                        display(message)
                        _record_repo_state(
                            paths,
                            active_plan.repo,
                            active_plan.project_dir,
                            status="verification_failed",
                            summary=message,
                            execution_target=active_plan.execution_target,
                        )
                        return RepoBringUpResult(
                            repo=active_plan.repo,
                            project_dir=active_plan.project_dir,
                            detected_files=active_plan.detected_files,
                            mode=current_mode,
                            executed_commands=tuple(executed_commands),
                            verification_passed=False,
                            message=message,
                            repo_family=active_plan.repo_family,
                            specialist_name=active_plan.specialist_name,
                            recovery_decision=None if last_recovery is None else last_recovery.decision,
                            failure_type=None if last_recovery is None else last_recovery.failure_type,
                            setup_outcome=None,
                        )
                    _show_setup_recovery_evidence(
                        repo_name=active_plan.repo.name,
                        failed_command=step.verification_command,
                        failure_message=failure_message,
                        display=display,
                        execution_target=active_plan.execution_target,
                    )
                    active_plan, last_recovery, terminal_result = _apply_recovery_decision(
                        plan=active_plan,
                        failed_step=step,
                        failed_command=step.verification_command,
                        failure_message=failure_message,
                        raw_error_output=failure_message,
                        verification_failure=failure_message,
                        current_mode=current_mode,
                        paths=paths,
                        supervisor=supervisor,
                        system_probe=system_probe,
                        display=display,
                        executed_commands=executed_commands,
                        started_at=started_at,
                    )
                    recovery_count += 1
                    if terminal_result is not None:
                        return terminal_result
                    break
                write_workflow_state(
                    paths.config_dir,
                    {
                        "active_repo_key": active_plan.repo.repo_url,
                        "active_repo_name": active_plan.repo.name,
                        "active_issue_kind": "setup_in_progress",
                        "active_issue_summary": f"Duckln verified setup step {current_step_index}/{total_steps}: {step.purpose}.",
                        "active_repair_phase": f"setup_step_{current_step_index}_verified",
                    },
                )
                _write_repo_objective_state(
                    paths,
                    repo=active_plan.repo,
                    kind="repo_setup",
                    status="active",
                    goal=f"Set up {active_plan.repo.name} and verify the smallest documented path.",
                    execution_target=active_plan.execution_target,
                    requires_user_decision=False,
                    resume_hint=f"Last time Duckln was setting up {active_plan.repo.name}.",
                )
        else:
            if recovery_count > 0 and on_repair_success is not None:
                try:
                    on_repair_success(
                        _last_failed_command,
                        _last_raw_error_output,
                        _last_recovery_command,
                        active_plan.repo_family.value,
                        active_plan.repo.name,
                        active_plan.repo.framework or "",
                    )
                except Exception:
                    pass
            return None


def _load_prior_skill_hint(inspection: RepoBringUpInspection, config_dir: Path) -> str | None:
    """Return the first skill note whose slug matches the repo family, or None."""
    family = classify_repo_family(inspection)
    skills_dir = config_dir / "memory" / "skills"
    if not skills_dir.is_dir():
        return None
    family_token = family.value.replace("_", "-")
    for skill_file in sorted(skills_dir.glob("*.md")):
        if family_token in skill_file.stem:
            try:
                return skill_file.read_text(encoding="utf-8")
            except OSError:
                return None
    return None


def _generate_repair_skill_note(
    *,
    failed_command: str,
    error_output: str,
    recovery_command: str,
    repo_family: str,
    repo_name: str,
    framework: str,
    config_dir: Path,
) -> tuple[str, str, str] | None:
    """Ask the LLM to produce a structured skill note from a successful repair.

    Returns (slug, title, summary_markdown) or None if the LLM produces no
    usable JSON.
    """
    safe_error = strip_synthetic_and_prompt_lines(error_output)
    prompt = build_repair_skill_prompt(
        failed_command=failed_command,
        error_output=safe_error,
        recovery_command=recovery_command,
        repo_family=repo_family,
        repo_name=repo_name,
        framework=framework,
    )
    try:
        current = load_app_config(ConfigPaths(config_dir=config_dir, config_file=config_dir / "config.json"))
    except Exception:
        return None
    if current is None or not current.model:
        return None
    try:
        raw = generate_provider_reply(
            current.provider,
            model_id=current.model,
            api_key=current.api_key,
            base_url=current.base_url,
            system_prompt="You generate structured JSON skill notes. Respond with valid JSON only.",
            user_message=prompt,
            config_dir=config_dir,
        )
    except (ProviderRequestError, ValueError, OSError, RuntimeError):
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw.strip())
    except (json.JSONDecodeError, AttributeError):
        return None
    slug = str(data.get("slug", "")).strip()
    title = str(data.get("title", "")).strip()
    trigger = str(data.get("trigger_pattern", "")).strip()
    fix_seq = data.get("fix_sequence", [])
    verification = str(data.get("verification", "")).strip()
    notes = str(data.get("notes", "")).strip()
    if not slug or not title or not fix_seq:
        return None
    numbered = "\n".join(f"{i + 1}. {cmd}" for i, cmd in enumerate(fix_seq) if isinstance(cmd, str))
    summary_parts = [
        f"## Trigger\nError pattern: {trigger}\nFailed command: {failed_command}",
        f"## Fix sequence\n{numbered}",
    ]
    if verification:
        summary_parts.append(f"## Verification\n{verification}")
    context_line = f"Context: {repo_name} ({framework}), family={repo_family}"
    if notes:
        summary_parts.append(f"## Notes\n{notes}\n{context_line}")
    else:
        summary_parts.append(context_line)
    return slug, title, "\n\n".join(summary_parts)


def _apply_recovery_decision(
    *,
    plan: RepoBringUpPlan,
    failed_step: RepoBringUpStep,
    failed_command: str,
    failure_message: str,
    raw_error_output: str,
    verification_failure: str,
    current_mode: ControlMode,
    paths: ConfigPaths,
    supervisor: RepoBringUpSupervisor,
    system_probe: SystemProbe | None,
    display: Callable[[str], None],
    executed_commands: list[str],
    started_at: float,
) -> tuple[RepoBringUpPlan, DebugRecoveryAssessment, RepoBringUpResult | None]:
    display(
        _agent_phase_status(
            "Debug / Recovery agent",
            "Checking a recovery route for the setup failure...",
            phase_index=1,
            phase_total=2,
            started_at=started_at,
        )
    )
    display(
        _activity_trace_message(
            "failure analysis",
            (
                f"Command examined: {failed_command}",
                f"Failure signal: {verification_failure}",
                "Duckln is classifying the blocker before attempting a bounded retry.",
            ),
        )
    )
    recovery = supervisor.assess_failed_bringup(
        plan=plan,
        failed_command=failed_command,
        verification_failure=verification_failure,
        error_output=raw_error_output,
        system_probe=system_probe,
        config_dir=paths.config_dir,
    )
    recovery_message = f"{failure_message} {recovery.summary}"
    display(
        _activity_trace_message(
            "recovery decision",
            (
                f"Failure class: {recovery.failure_type.value}",
                f"Chosen action: {recovery.decision.value}",
                recovery.summary,
            ),
        )
    )
    _record_recovery_memory(paths, plan.repo, recovery)

    if recovery.decision is RecoveryDecision.REQUEST_MISSING_PREREQUISITE:
        prerequisite_plan = _prerequisite_recovery_plan(
            plan=plan,
            failed_step=failed_step,
            failed_command=failed_command,
            recovery=recovery,
            raw_error_output=raw_error_output,
            display=display,
        )
        if prerequisite_plan is not None:
            display(prerequisite_plan.summary)
            return prerequisite_plan, recovery, None

    if recovery.decision is RecoveryDecision.RETRY_SAME_SPECIALIST:
        retry_plan = _retry_current_specialist_plan(plan, failed_step=failed_step, recovery=recovery)
        display(retry_plan.summary)
        return retry_plan, recovery, None

    if recovery.decision is RecoveryDecision.REROUTE_TO_OTHER_SPECIALIST and recovery.reroute_repo_family is not None:
        rerouted_inspection = inspect_repo_for_bringup(
            plan.repo,
            plan.project_dir,
            system_probe=system_probe,
            runtime_provider=plan.runtime_provider,
            execution_target=plan.execution_target,
            config_dir=paths.config_dir,
            context_service=supervisor._context_service,
        )
        rerouted_specialist = supervisor._select_specialist(rerouted_inspection, recovery.reroute_repo_family)
        rerouted_plan = _attach_recovery_summary(
            rerouted_specialist.build_plan(rerouted_inspection, recovery.reroute_repo_family, config_dir=paths.config_dir),
            recovery,
        )
        display(rerouted_plan.summary)
        return rerouted_plan, recovery, None

    status = "setup_blocked" if recovery.decision is RecoveryDecision.REQUEST_MISSING_PREREQUISITE else "setup_failed"
    supervisor_message = (
        f"Supervisor agent paused {plan.repo.name} setup. {failure_message} "
        f"Best next step: {recovery.summary}"
    )
    display(supervisor_message)
    _record_repo_state(
        paths,
        plan.repo,
        plan.project_dir,
        status=status,
        summary=supervisor_message,
        execution_target=plan.execution_target,
    )
    supervisor._context_service.record_repo_learning(
        config_dir=paths.config_dir,
        repo=plan.repo,
        summary=_compact_repo_summary(supervisor_message),
        source="bringup-recovery",
        metadata={
            "recovery_decision": recovery.decision.value,
            "failure_type": recovery.failure_type.value,
        },
    )
    return plan, recovery, RepoBringUpResult(
        repo=plan.repo,
        project_dir=plan.project_dir,
        detected_files=plan.detected_files,
        mode=current_mode,
        executed_commands=tuple(executed_commands),
        verification_passed=False,
        message=supervisor_message,
        repo_family=plan.repo_family,
        specialist_name=plan.specialist_name,
        recovery_decision=recovery.decision,
        failure_type=recovery.failure_type,
        setup_outcome=None,
    )


def _prerequisite_recovery_plan(
    *,
    plan: RepoBringUpPlan,
    failed_step: RepoBringUpStep,
    failed_command: str,
    recovery: DebugRecoveryAssessment,
    raw_error_output: str = "",
    display: Callable[[str], None] | None = None,
) -> RepoBringUpPlan | None:
    remaining = _remaining_steps_from(plan, failed_step)
    recovery_steps: list[RepoBringUpStep] = []
    command_text = failed_command.casefold()

    if recovery.failure_type is FailureType.PERMISSION_DENIED and _is_global_package_install(failed_command):
        elevated = _elevated_install_command(failed_command)
        if elevated:
            adjusted_remaining = _replace_first_step_command(
                remaining,
                original=failed_step,
                command=elevated,
                purpose=f"{failed_step.purpose} with approved elevated permission",
            )
            return replace(
                plan,
                steps=adjusted_remaining,
                summary=f"{plan.summary} {recovery.summary} Duckln will retry the README install path with approved elevated permission.",
            )

    # Prefer the OS's own install hint when stderr volunteers one — this is
    # safer (no curl-to-bash, no sudo password gate) and matches what the
    # error message literally tells the user to do.
    hint_command, hint_verification, hint_purpose = _parsed_install_hint_step(
        raw_error_output, recovery=recovery, failed_command=failed_command, plan=plan
    )
    if hint_command:
        assessment = assess_command(hint_command)
        if not assessment.blocked:
            if display is not None:
                display(f"Duckln using the OS install hint: {hint_command}")
            recovery_steps.append(
                RepoBringUpStep(
                    purpose=hint_purpose,
                    command=hint_command,
                    verification_command=hint_verification,
                    source="recovery-prerequisite-os-hint",
                )
            )

    if not recovery_steps and (
        recovery.failure_type is FailureType.NODE_NPM_MISMATCH
        or any(token in command_text for token in ("npm", "npx", "pnpm", "yarn", "node"))
    ):
        recovery_steps.append(
            RepoBringUpStep(
                purpose="Install or upgrade Node.js prerequisite",
                command=_node_runtime_install_command(execution_target=plan.execution_target),
                verification_command=_node_npm_prerequisite_check(execution_target=plan.execution_target),
                source="recovery-prerequisite",
            )
        )
    elif not recovery_steps and recovery.failure_type is FailureType.PIP_VENV_MISMATCH:
        recovery_steps.append(
            RepoBringUpStep(
                purpose="Install Python packaging prerequisite",
                command=_python_runtime_install_command(execution_target=plan.execution_target),
                verification_command="python3 -m pip --version" if _is_remote_target(plan.execution_target) else "python -m pip --version",
                source="recovery-prerequisite",
            )
        )
    elif not recovery_steps and recovery.failure_type is FailureType.MISSING_COMPILER_BUILD_TOOLS:
        recovery_steps.append(
            RepoBringUpStep(
                purpose="Install build-tool prerequisite",
                command=_build_tools_install_command(execution_target=plan.execution_target, failed_command=failed_command),
                verification_command=_build_tools_verification_command(failed_command),
                source="recovery-prerequisite",
            )
        )

    if not recovery_steps:
        return None
    return replace(
        plan,
        steps=tuple(recovery_steps) + remaining,
        summary=f"{plan.summary} {recovery.summary} Duckln will fix the prerequisite, verify it, then return to the documented README setup path.",
    )


def _parsed_install_hint_step(
    raw_error_output: str,
    *,
    recovery: DebugRecoveryAssessment,
    failed_command: str,
    plan: RepoBringUpPlan,
) -> tuple[str | None, str | None, str]:
    """Return (install_command, verification_command, purpose) from an OS hint.

    Returns (None, None, "") when no usable hint is present in stderr.
    """
    parsed = parse_install_hint(raw_error_output)
    if parsed is None:
        return None, None, ""
    _manager, install_command = parsed
    # Decide a verification command from the failed command shape — re-run the
    # original probe is the most direct check.
    command_text = failed_command.casefold()
    if any(token in command_text for token in ("node", "npm", "npx", "pnpm", "yarn")):
        verification = _node_npm_prerequisite_check(execution_target=plan.execution_target)
        purpose = "Install Node.js using the OS-suggested package"
    elif "python" in command_text or "pip" in command_text or recovery.failure_type is FailureType.PIP_VENV_MISMATCH:
        verification = "python3 -m pip --version" if _is_remote_target(plan.execution_target) else "python -m pip --version"
        purpose = "Install Python using the OS-suggested package"
    else:
        # Generic prereq install — verify by re-running the original failed command.
        verification = failed_command
        purpose = "Install the missing prerequisite using the OS-suggested package"
    return install_command, verification, purpose


def _remaining_steps_from(plan: RepoBringUpPlan, failed_step: RepoBringUpStep) -> tuple[RepoBringUpStep, ...]:
    for index, step in enumerate(plan.steps):
        if step is failed_step or step == failed_step:
            return plan.steps[index:]
    return (failed_step,)


def _replace_first_step_command(
    steps: tuple[RepoBringUpStep, ...],
    *,
    original: RepoBringUpStep,
    command: str,
    purpose: str,
) -> tuple[RepoBringUpStep, ...]:
    replaced: list[RepoBringUpStep] = []
    changed = False
    for step in steps:
        if not changed and (step is original or step == original):
            replaced.append(
                replace(
                    step,
                    purpose=purpose,
                    command=command,
                    source="recovery-prerequisite",
                )
            )
            changed = True
        else:
            replaced.append(step)
    return tuple(replaced) if changed else (replace(original, purpose=purpose, command=command, source="recovery-prerequisite"), *steps)


def _node_runtime_install_command(*, execution_target: str) -> str:
    if execution_target in _REMOTE_EXECUTION_TARGETS or platform.system() == "Linux":
        return (
            "if [ -f /etc/os-release ]; then . /etc/os-release; "
            "case \"${ID:-}\" in "
            "ubuntu|debian) curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt-get install -y nodejs && sudo corepack enable ;; "
            "fedora) sudo dnf install -y nodejs npm && sudo corepack enable ;; "
            "rhel|centos|amzn) sudo yum install -y nodejs npm && sudo corepack enable ;; "
            "*) echo \"Duckln: unsupported Linux distro for automatic Node.js install: ${ID:-unknown}\"; exit 3 ;; "
            "esac; else echo \"Duckln: cannot detect Linux distro for Node.js install\"; exit 3; fi"
        )
    if platform.system() == "Darwin":
        return "brew install node"
    if platform.system() == "Windows":
        return "winget install OpenJS.NodeJS.LTS"
    return "node -v"


def _python_runtime_install_command(*, execution_target: str) -> str:
    if execution_target in _REMOTE_EXECUTION_TARGETS or platform.system() == "Linux":
        return (
            "if [ -f /etc/os-release ]; then . /etc/os-release; "
            "case \"${ID:-}\" in "
            "ubuntu|debian) sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip ;; "
            "fedora) sudo dnf install -y python3 python3-pip ;; "
            "rhel|centos|amzn) sudo yum install -y python3 python3-pip ;; "
            "*) echo \"Duckln: unsupported Linux distro for automatic Python install: ${ID:-unknown}\"; exit 3 ;; "
            "esac; else echo \"Duckln: cannot detect Linux distro for Python install\"; exit 3; fi"
        )
    if platform.system() == "Darwin":
        return "brew install python"
    if platform.system() == "Windows":
        return "winget install Python.Python.3.12"
    return "python --version"


def _build_tools_install_command(*, execution_target: str, failed_command: str) -> str:
    lowered = failed_command.casefold()
    if "cargo" in lowered or "rustc" in lowered:
        package = "cargo rustc"
        mac_package = "rust"
        win_package = "Rustlang.Rustup"
    elif "go " in lowered or lowered.startswith("go"):
        package = "golang-go"
        mac_package = "go"
        win_package = "GoLang.Go"
    elif "cmake" in lowered:
        package = "cmake build-essential"
        mac_package = "cmake"
        win_package = "Kitware.CMake"
    else:
        package = "build-essential"
        mac_package = "make"
        win_package = "GnuWin32.Make"
    if execution_target in _REMOTE_EXECUTION_TARGETS or platform.system() == "Linux":
        return (
            "if [ -f /etc/os-release ]; then . /etc/os-release; "
            "case \"${ID:-}\" in "
            f"ubuntu|debian) sudo apt-get update && sudo apt-get install -y {package} ;; "
            f"fedora) sudo dnf install -y {package} ;; "
            f"rhel|centos|amzn) sudo yum install -y {package} ;; "
            "*) echo \"Duckln: unsupported Linux distro for automatic build-tool install: ${ID:-unknown}\"; exit 3 ;; "
            "esac; else echo \"Duckln: cannot detect Linux distro for build tools\"; exit 3; fi"
        )
    if platform.system() == "Darwin":
        return f"brew install {mac_package}"
    if platform.system() == "Windows":
        return f"winget install {win_package}"
    return "make --version"


def _build_tools_verification_command(failed_command: str) -> str:
    lowered = failed_command.casefold()
    if "cargo" in lowered:
        return "cargo --version"
    if "go " in lowered or lowered.startswith("go"):
        return "go version"
    if "cmake" in lowered:
        return "cmake --version"
    return "make --version"


def _is_global_package_install(command: str) -> bool:
    normalized = " ".join(command.casefold().split())
    return any(
        marker in normalized
        for marker in (
            "npm install -g ",
            "npm install --global ",
            "pnpm add -g ",
            "yarn global add ",
            "pip install --user",
        )
    )


def _elevated_install_command(command: str) -> str | None:
    cleaned = command.strip()
    if not cleaned or cleaned.startswith("sudo "):
        return None
    if cleaned.startswith(("npm ", "pnpm ", "yarn ", "pip ", "pip3 ", "python -m pip ", "python3 -m pip ")):
        return f"sudo {cleaned}"
    return None


def _retry_current_specialist_plan(
    plan: RepoBringUpPlan,
    *,
    failed_step: RepoBringUpStep,
    recovery: DebugRecoveryAssessment,
) -> RepoBringUpPlan:
    retry_step = failed_step
    if (
        recovery.failure_type is FailureType.PIP_VENV_MISMATCH
        and plan.specialist_name in {"python", "audio", "diffusion-heavy"}
    ):
        retry_step = RepoBringUpStep(
            purpose="Recreate project virtualenv",
            command=venv_create_command(execution_target=plan.execution_target),
            verification_command=venv_python_test_command(execution_target=plan.execution_target),
        )
    if (
        recovery.failure_type is FailureType.DEPENDENCY_INSTALL_FAILURE
        and any(
            failed_step.command.startswith(prefix)
            for prefix in (".venv/bin/python -m pip install", ".venv\\Scripts\\python.exe -m pip install", ".venv/Scripts/python.exe -m pip install")
        )
    ):
        retry_step = RepoBringUpStep(
            purpose=f"Retry {failed_step.purpose.lower()}",
            command=failed_step.command,
            verification_command=failed_step.verification_command,
        )
    return RepoBringUpPlan(
        repo=plan.repo,
        project_dir=plan.project_dir,
        detected_files=plan.detected_files,
        steps=(retry_step,),
        summary=f"{plan.summary} {recovery.summary}",
        repo_family=plan.repo_family,
        specialist_name=plan.specialist_name,
        playbook_path=plan.playbook_path,
        runtime_provider=plan.runtime_provider,
        execution_target=plan.execution_target,
        subagent_prompt=plan.subagent_prompt,
        subagent_workspace_files=plan.subagent_workspace_files,
    )


def _agent_label(specialist_name: str) -> str:
    labels = {
        "python": "Python specialist agent",
        "cpp-native": "C++ / native specialist agent",
        "go-native": "Go specialist agent",
        "node-typescript": "Node / TypeScript specialist agent",
        "audio": "Audio specialist agent",
        "diffusion-heavy": "Diffusion specialist agent",
        "vm-environment": "VM / environment specialist agent",
        "provider-routing": "Provider routing specialist agent",
        "debug-recovery": "Debug / Recovery agent",
    }
    return labels.get(specialist_name, f"{specialist_name} agent")


def _agent_phase_status(
    agent_name: str,
    phase: str,
    *,
    phase_index: int,
    phase_total: int,
    started_at: float,
    detail: str | None = None,
) -> str:
    bounded_total = max(1, phase_total)
    bounded_index = max(1, min(phase_index, bounded_total))
    detail_prefix = f"{detail} • " if detail else ""
    elapsed = _format_elapsed(time.monotonic() - started_at)
    return render_status_box(
        f"{detail_prefix}{phase} {_progress_bar(bounded_index, bounded_total)} {bounded_index}/{bounded_total} • {elapsed}",
        title=agent_name,
    )


def _progress_bar(current: int, total: int) -> str:
    slots = max(4, min(total, 8))
    filled = max(1, round((current / max(total, 1)) * slots))
    return "[" + ("#" * filled) + ("-" * max(0, slots - filled)) + "]"


def _format_elapsed(seconds: float) -> str:
    whole_seconds = max(0, int(seconds))
    if whole_seconds < 60:
        return f"{whole_seconds}s"
    minutes, remainder = divmod(whole_seconds, 60)
    return f"{minutes}m {remainder}s"


def _command_failure_message(
    prefix: str,
    stderr: str,
    *,
    command: str | None = None,
    display: Callable[[str], None] | None = None,
    execution_target: str = "local",
) -> str:
    return build_failure_message(
        prefix,
        stderr,
        command=command,
        trace=display,
        execution_target=execution_target,
    )


def _show_setup_recovery_evidence(
    *,
    repo_name: str,
    failed_command: str,
    failure_message: str,
    display: Callable[[str], None],
    execution_target: str,
) -> None:
    """Search exact setup failures before choosing a repair, without leaking local identifiers."""

    try:
        from duckln.web_runtime import gather_runtime_repair_evidence
    except (ImportError, RuntimeError):
        return
    redacted_failure = redact_sensitive_data(failure_message)
    redacted_command = redact_sensitive_data(failed_command)
    try:
        evidence = gather_runtime_repair_evidence(
            command=redacted_command,
            error_text=redacted_failure,
            fetch_live=True,
            trace=display,
            execution_target=execution_target,
            os_hint="Linux VM" if execution_target in _REMOTE_EXECUTION_TARGETS else platform.system(),
        )
    except (OSError, RuntimeError, ValueError):
        return
    if evidence.search_query:
        display(f"DuckDuckGo exact-error search query: {evidence.search_query}")
    if evidence.note:
        display(evidence.note)


def _activity_trace_message(title: str, items: tuple[str, ...]) -> str:
    return render_execution_trace(title, items)


def _is_dependency_install_command(command: str) -> bool:
    normalized = " ".join(command.lower().split())
    padded = f" {normalized} "
    markers = (
        " pip install ",
        " -m pip install ",
        " npm install ",
        " pnpm install ",
        " pnpm add ",
        " yarn install ",
        " yarn add ",
        " bun install ",
        " sudo apt install ",
        " sudo apt-get install ",
        " apt install ",
        " apt-get install ",
        " sudo dnf install ",
        " dnf install ",
        " sudo yum install ",
        " yum install ",
        " brew install ",
        " winget install ",
        " sudo snap install ",
        " conda env create ",
        " cargo build ",
        " cargo fetch ",
        " go mod download ",
        " go build ",
        " docker build ",
        " docker compose build ",
    )
    return any(marker in padded for marker in markers)


def _dependency_install_activity_trace(*, request: DependencyApprovalRequest) -> str:
    items = [f"Planned install command: {request.command}"]
    if request.manifest_paths:
        items.append(f"Repo dependency evidence: {', '.join(request.manifest_paths)}")
    items.append("Duckln will wait for approval before it installs any packages.")
    if request.items:
        preview = ", ".join(item.dependency for item in request.items[:4])
        items.append(f"Dependency preview: {preview}")
    return render_tool_invocation_trace(
        title="dependency install review",
        tool_id="shell.command_runner",
        action="Review a bounded dependency install command before execution",
        detail_lines=tuple(items),
        source_urls=request.source_urls[:1],
    )


def _dependency_install_approval_prompt(*, request: DependencyApprovalRequest) -> str:
    lines = [
        f"{request.prompt}: {request.command}",
        "Duckln will not install packages silently.",
    ]
    if request.manifest_paths:
        lines.append(f"Dependency list came from: {', '.join(request.manifest_paths)}")
    if request.source_urls:
        lines.append(f"Official install reference: {request.source_urls[0]}")
    lines.extend(render_dependency_approval_lines(request)[2:8])
    lines.append("Approve this dependency install?")
    return "\n".join(lines)


def _approve_dependency_install(
    *,
    approve: Callable[[str], bool] | None,
    request: DependencyApprovalRequest,
) -> DependencyApprovalDecision:
    if approve is None:
        return DependencyApprovalDecision(approved=False, approve_all=False, selected_item_ids=())
    live_approver = getattr(approve, "approve_dependency_install", None)
    if callable(live_approver):
        decision = live_approver(request)
        if isinstance(decision, DependencyApprovalDecision):
            return decision
        return DependencyApprovalDecision(
            approved=bool(decision),
            approve_all=bool(decision),
            selected_item_ids=tuple(item.item_id for item in request.items) if decision else (),
        )
    approved = bool(approve(_dependency_install_approval_prompt(request=request)))
    return DependencyApprovalDecision(
        approved=approved,
        approve_all=approved,
        selected_item_ids=tuple(item.item_id for item in request.items) if approved else (),
    )


def _record_repo_state(
    paths: ConfigPaths,
    repo: RepoCatalogRecord,
    project_dir: Path,
    *,
    status: str,
    summary: str,
    execution_target: str = "local",
    vm_name: str | None = None,
    managed_by_duckln: bool = True,
    metadata: dict[str, object] | None = None,
) -> None:
    existing = initialize_state_store(paths.config_dir).get_latest_repo_state()
    preserved: dict[str, object] = {}
    if existing is not None and existing.repo_key == repo.repo_url and "plan_path" in existing.metadata:
        preserved["plan_path"] = existing.metadata["plan_path"]
    payload_metadata = {**preserved, **(metadata or {})}
    # Plan 196 F8: record the repo↔container link so the NL inventory answerer (F6) can map
    # a Docker container/image back to its project ("list docker images and their project").
    # Stored in repo_state metadata (no schema change); accurate for Duckln-managed containers.
    if execution_target in {"container", "docker"}:
        _cname = _active_container_name(paths.config_dir)
        if _cname:
            payload_metadata.setdefault("container_name", _cname)
    if execution_target in {"aws", "gcp"}:
        workflow = AgentContextService().load_workflow_state(config_dir=paths.config_dir)
        if workflow is not None:
            payload_metadata.setdefault("cloud_resource_key", workflow.active_runtime_cloud_resource_key)
            payload_metadata.setdefault("cloud_vendor", workflow.active_runtime_cloud_vendor)
            payload_metadata.setdefault("cloud_region", workflow.active_runtime_cloud_region)
            payload_metadata.setdefault("cloud_shape", workflow.active_runtime_cloud_shape)
    active_flag = bool(payload_metadata.get("active"))
    initialize_state_store(paths.config_dir).upsert_repo_state(
        repo_key=repo.repo_url,
        repo_path=str(project_dir),
        repo_url=repo.repo_url,
        execution_target=execution_target,
        vm_name=vm_name,
        active_flag=active_flag,
        managed_by_duckln=managed_by_duckln,
        status=status,
        summary=_compact_repo_summary(redact_sensitive_data(summary)),
        metadata={"repo_name": repo.name, **payload_metadata},
    )


def _record_recovery_memory(
    paths: ConfigPaths,
    repo: RepoCatalogRecord,
    recovery: DebugRecoveryAssessment,
) -> None:
    write_session_summary_state(
        paths.config_dir,
        session_id=f"repo bring-up recovery {repo.name}",
        summary=f"{repo.name}: {recovery.summary}",
    )


_PORTABLE_COMMAND_FAMILIES: dict[str, str] = {
    # Map host-OS-specific command tokens onto the canonical family declared in playbooks.
    # Keeps the validator strict while allowing cross-OS rewrites of the same operation.
    "python3": "python",
    "python.exe": "python",
    "py": "python",
    "if": "test",  # Windows `if exist` plays the role of POSIX `test -x` in venv checks.
}


def _validate_steps_against_playbook(
    steps: list[RepoBringUpStep],
    playbook_path: Path,
) -> None:
    playbook_text = playbook_path.read_text(encoding="utf-8").lower().strip()
    if not playbook_text:
        raise ValueError(f"Playbook {playbook_path.name} is empty.")
    for step in steps:
        if step.source in {"readme", "readme-llm", "readme-prereq", "readme-prerequisite", "recovery-prerequisite"}:
            continue
        raw_token = Path(shlex.split(step.command)[0]).name.lstrip("./").lower()
        command_token = _PORTABLE_COMMAND_FAMILIES.get(raw_token, raw_token)
        if command_token == "test":
            continue
        if command_token not in playbook_text:
            raise ValueError(
                f"{playbook_path.name} does not declare command family '{command_token}' for '{step.command}'."
            )


def _slugify(value: str) -> str:
    return "".join(character if character.isalnum() or character in "._-" else "-" for character in value.strip()).strip(
        "-._"
    ) or "repo"


def _compact_repo_summary(summary: str) -> str:
    lines = [line.strip() for line in summary.splitlines() if line.strip()]
    if not lines:
        return "Duckln updated repo state."
    compact = lines[0]
    if len(compact) <= 280:
        return compact
    return compact[:277].rstrip() + "..."


# --- Plan 67: Plan-Mode integration ------------------------------------------


def gate_mutation_or_draft(
    *,
    intended_steps: tuple[tuple[str, str, str | None], ...],
    objective: str,
    repo_slug: str | None,
    paths: ConfigPaths,
    current,
    display: Callable[[str], None],
    context_summary: str = "",
) -> bool:
    """Plan 73 Phase A: when Plan Mode is ON, turn a set of intended mutating
    provisioning steps into an approvable pending plan instead of executing them.

    `intended_steps` is a tuple of `(purpose, command, verification)`. Returns
    True when the action was GATED (a plan was drafted; caller must NOT execute),
    or False when Plan Mode is OFF (caller proceeds with legacy direct behavior).
    S0-only step sets still draft a plan when ON (user stays in control).
    """
    if not getattr(current, "plan_mode_enabled", False):
        return False

    from duckln.plan_mode import PlanStep, critic_review, finalize_plan_from_steps
    from duckln.ui import render_plan_panel
    from state.access import write_pending_plan, clear_pending_plan, read_pending_plan

    # Don't clobber an actionable pending plan.
    pending = read_pending_plan(paths.config_dir)
    if pending is not None and str(pending.get("status") or "") in {"pending", "amended", "approved", "edited"}:
        display(
            "Plan Mode: a plan is already pending. Run `/plan show` to view, "
            "`/plan approve` to execute, or `/plan reject` to discard."
        )
        return True
    if pending is not None:
        clear_pending_plan(paths.config_dir)

    steps = tuple(
        PlanStep(
            index=i,
            title=purpose,
            description="",
            command=command,
            safety_class="S0",  # reclassified by Stage 4
            verification=verification,
            rationale="Provisioning step gated by Plan Mode.",
            estimated_seconds=30,
            confidence=1.0,
            origin="planner",
        )
        for i, (purpose, command, verification) in enumerate(intended_steps, start=1)
    )
    plan = finalize_plan_from_steps(
        objective=objective,
        repo_slug=repo_slug,
        context_summary=context_summary or objective,
        steps=steps,
        mode=getattr(current, "mode", None) or "hitl",
    )
    try:
        from duckln.ai_client import build_llm_client_for_role
        from duckln.plan_mode import MODEL_UNREACHABLE_BLOCKER, MODEL_UNRESPONSIVE_BLOCKER

        verdict = critic_review(
            plan=plan, understanding=None,
            # Plan 179 C2: the critic runs under the SUPERVISOR role.
            llm_client=build_llm_client_for_role(paths.config_dir, "SUPERVISOR"),
            config_dir=paths.config_dir,
            model_id=str(read_config_snapshot(paths.config_dir).get("model") or ""),
        )
        from dataclasses import replace as _dc_replace

        if verdict.external_blocker in (MODEL_UNREACHABLE_BLOCKER, MODEL_UNRESPONSIVE_BLOCKER):
            # The supervisor MODEL couldn't review (unreachable, or reachable-but-too-slow/weak)
            # — but these are EXPLICIT user-provided provisioning steps. Don't refuse to DRAFT
            # them; still draft the pending plan (run nothing) for the USER to approve, noting
            # the review gap. (The user is the decision-maker on what they explicitly asked for.)
            plan = _dc_replace(plan, critic_reasoning="Supervisor review unavailable (model unreachable or too slow/weak) — review the steps carefully before approving.")
        elif verdict.verdict == "block":
            display(
                "Plan Mode blocked this action: "
                + (verdict.missing_question or verdict.external_blocker or verdict.reason)
            )
            return True
        else:
            banner = (
                f"⚠ {verdict.reason}" if verdict.verdict == "skipped"
                else (f"Supervisor: {verdict.reason}" if verdict.reason else "Supervisor approved.")
            )
            plan = _dc_replace(plan, critic_reasoning=banner)
    except Exception:
        pass

    write_pending_plan(paths.config_dir, plan.to_dict())
    display(render_plan_panel(plan))
    display(
        "Plan Mode is on — this provisioning was NOT run. "
        "Review above, then `/plan approve` to execute (or `/plan reject`)."
    )
    return True


# Plan 82: the probe tools, and a single delimited script that reports everything
# in ONE round-trip (no per-command bleed, no DUCKLN-DONE marker corruption). The
# script contains NO single quotes so it survives `bash -lc '<script>'` wrapping.
_PRECHECK_TOOLS = ("node", "npm", "pnpm", "yarn", "python3", "pip3", "cargo", "go", "docker", "git", "uv")
_PRECHECK_BEGIN = "DUCKLN_PRECHECK_BEGIN"
_PRECHECK_END = "DUCKLN_PRECHECK_END"
_PROBE_TIMEOUT_SECONDS = 45


def _build_precheck_script(runtime_dir: str) -> str:
    tool_list = " ".join(_PRECHECK_TOOLS)
    return (
        f"echo {_PRECHECK_BEGIN}; "
        f"for t in {tool_list}; do command -v \"$t\" >/dev/null 2>&1 && echo \"TOOL:$t\"; done; "
        "echo \"NODEV:$(node --version 2>/dev/null)\"; "
        "echo \"PYV:$(python3 --version 2>&1)\"; "
        "echo \"GOV:$(go version 2>/dev/null)\"; "
        "echo \"RUSTV:$(rustc --version 2>/dev/null)\"; "
        f"test -d {runtime_dir}/.git && echo CLONED:yes; "
        # Plan 88: detect a desktop app from the VM-side clone so a remote/private
        # repo is still classified desktop_gui (Tauri checked first → wins).
        f"{{ test -e {runtime_dir}/src-tauri || test -e {runtime_dir}/tauri.conf.json; }} && echo DESKTOP:tauri; "
        f"grep -q electron {runtime_dir}/package.json 2>/dev/null && echo DESKTOP:electron; "
        # Plan 89: report RAM + swap so a heavy (Tauri/Rust) build on a small VM gets
        # swap + single-threaded compilation BEFORE it OOMs. `tr -dc 0-9` avoids the
        # single quotes that `bash -lc '<script>'` wrapping would break.
        "echo \"MEMKB:$(grep MemTotal /proc/meminfo 2>/dev/null | tr -dc 0-9)\"; "
        "swapon --show 2>/dev/null | grep -q . && echo SWAP:yes || echo SWAP:no; "
        # Plan 137 Fix 2: detect a HALF-INSTALL (a prior attempt that died mid-way) so the
        # planner can add an explicit cleanup step + resume from the failed part. A
        # marker-less node_modules/.venv, stale build scratch, or a broken dpkg state.
        f"if [ -d {runtime_dir}/node_modules ] && ! {{ [ -e {runtime_dir}/node_modules/.package-lock.json ] || "
        f"[ -e {runtime_dir}/node_modules/.pnpm ] || [ -e {runtime_dir}/node_modules/.yarn-integrity ] || "
        f"[ -e {runtime_dir}/node_modules/.modules.yaml ]; }}; then echo PARTIAL:node_modules; fi; "
        f"for d in {runtime_dir} {runtime_dir}/*; do if [ -d \"$d/.venv\" ] && [ ! -e \"$d/.venv/.duckln-deps-ok\" ]; "
        "then echo PARTIAL:venv; break; fi; done; "
        f"{{ [ -d {runtime_dir}/build_cache ] || [ -e {runtime_dir}/.codex-temp-sidecar ]; }} && echo PARTIAL:build_cache; "
        "dpkg --audit 2>/dev/null | grep -q . && echo PARTIAL:dpkg; "
        f"echo {_PRECHECK_END}"
    )


def _runtime_major(label: str, output: str) -> str | None:
    """Plan 82 Fix B: parse a runtime's `--version` OUTPUT (not a semver range),
    stripping DUCKLN-DONE marker noise. Node → MAJOR; others → 'major.minor'."""
    cleaned = "\n".join(
        ln for ln in (output or "").splitlines() if "DUCKLN-DONE" not in ln and "DUCKLN_PRECHECK" not in ln
    )
    if label == "node":
        m = re.search(r"v?(\d+)\.\d+", cleaned)
        return m.group(1) if m else None
    return _xy_floor(cleaned)


def _parse_precheck_block(stdout: str, *, runtime_dir: str) -> tuple[set[str], bool, dict[str, str]]:
    """Plan 82 Fix A: parse ONLY the delimited pre-check block, line-prefix matched,
    ignoring any marker / bled noise. Returns (present_tools, already_cloned, versions)."""
    present: set[str] = set()
    versions: dict[str, str] = {}
    already_cloned = False
    text = stdout or ""
    if _PRECHECK_BEGIN in text:
        text = text.split(_PRECHECK_BEGIN, 1)[1]
    if _PRECHECK_END in text:
        text = text.split(_PRECHECK_END, 1)[0]
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("TOOL:"):
            tool = line[len("TOOL:"):].strip()
            if tool:
                present.add("python" if tool in ("python3", "pip3") else tool)
        elif line.startswith("NODEV:"):
            major = _runtime_major("node", line[len("NODEV:"):])
            if major:
                versions["node"] = major
        elif line.startswith("PYV:"):
            floor = _runtime_major("python", line[len("PYV:"):])
            if floor:
                versions["python"] = floor
        elif line.startswith("GOV:"):
            floor = _runtime_major("go", line[len("GOV:"):])
            if floor:
                versions["go"] = floor
        elif line.startswith("RUSTV:"):
            floor = _runtime_major("rust", line[len("RUSTV:"):])
            if floor:
                versions["rust"] = floor
        elif line.startswith("CLONED:"):
            already_cloned = True
        elif line.startswith("DESKTOP:"):
            # Plan 88: stash the desktop flavor under a non-runtime key so existing
            # callers (summary, version loop) ignore it; tauri is emitted first → wins.
            flavor = line[len("DESKTOP:"):].strip()
            if flavor and "desktop" not in versions:
                versions["desktop"] = flavor
        elif line.startswith("MEMKB:"):
            val = line[len("MEMKB:"):].strip()
            if val.isdigit():
                versions["mem_kb"] = val  # Plan 89: total RAM in kB (non-runtime key)
        elif line.startswith("SWAP:"):
            versions["swap"] = line[len("SWAP:"):].strip()  # Plan 89: yes|no
        elif line.startswith("PARTIAL:"):
            # Plan 137 Fix 2: a half-installed artifact from a prior interrupted attempt.
            kind = line[len("PARTIAL:"):].strip()
            if kind:
                existing = versions.get("partial", "")
                parts = [p for p in existing.split(",") if p]
                if kind not in parts:
                    parts.append(kind)
                versions["partial"] = ",".join(parts)
    return present, already_cloned, versions


def _format_precheck_summary(present: set[str], versions: dict[str, str], already_cloned: bool) -> str:
    """Plan 83 Fix 2: a one-line human summary of what the pre-check found, for the
    plan panel's 'Pre-check' heading."""
    parts: list[str] = []
    for tool in sorted(present):
        v = versions.get(tool)
        parts.append(f"{tool} v{v}" if v else tool)
    head = (", ".join(parts) + " present") if parts else "no relevant tools found"
    return head + ("; repo already cloned" if already_cloned else "; repo not cloned yet")


def assess_existing_setup(versions: dict, *, verification_passed: bool | None = None) -> tuple[bool, tuple[str, ...]]:
    """Plan 152 F1: judge whether an ALREADY-cloned repo is actually HEALTHY (not just
    present). Reads the precheck's `PARTIAL:*` signals + the verification result and returns
    (healthy, issues) with each issue a concrete, fixable phrase. Pure/deterministic."""
    issues: list[str] = []
    partial = {p for p in str((versions or {}).get("partial", "")).split(",") if p}
    _ISSUE = {
        "venv": "the Python virtual environment is incomplete (no completion marker)",
        "node_modules": "node_modules is incomplete (a prior install was interrupted)",
        "build_cache": "stale build scratch from an interrupted build is present",
        "dpkg": "the system package state is broken (dpkg needs configuring)",
    }
    for kind in partial:
        issues.append(_ISSUE.get(kind, f"a prior step left a partial '{kind}'"))
    if verification_passed is False:
        issues.append("the repo's verification/run check did not pass")
    return (len(issues) == 0, tuple(issues))


def build_fix_only_plan_steps(issues: tuple[str, ...], *, runtime_dir: str = ".",
                              detected_files: tuple[str, ...] = (), has_prebuild: bool = False,
                              package_manager: str = "npm") -> tuple[tuple[str, str, str], ...]:
    """Plan 152 F3: map DETECTED issues (from `assess_existing_setup`) to the MINIMAL repair
    step(s) — NOT a full setup. Returns (title, command, safety_class) steps, reusing the
    deterministic builders. A novel/unmapped issue yields a generic re-verify step (the
    caller may route it to reasoned recovery). Repo-agnostic, signal-keyed."""
    steps: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for issue in issues:
        low = issue.lower()
        if "virtual environment" in low or "venv" in low:
            key = "venv"
            if key not in seen:
                steps.append(("Recreate the Python backend venv", _SECONDARY_PY_SETUP_CMD, "S2"))
        elif "node_modules" in low:
            key = "node_modules"
            if key not in seen:
                steps.append(("Reinstall node dependencies (clean)",
                              f"rm -rf node_modules && {package_manager} install", "S2"))
        elif "dpkg" in low or "system package" in low:
            key = "dpkg"
            if key not in seen:
                steps.append(("Repair the broken system package state",
                              "sudo dpkg --configure -a 2>/dev/null || true; sudo apt-get -f install -y 2>/dev/null || true", "S3"))
        elif "build" in low or "verification" in low or "run check" in low:
            key = "build"
            if key not in seen and has_prebuild:
                steps.append(("Re-run the declared build/prebuild", f"{package_manager} run --if-present build", "S2"))
        else:
            key = f"other:{low[:20]}"
            if key not in seen:
                steps.append((f"Investigate + fix: {issue[:60]}", "", "S2"))  # routed to reasoned recovery
        seen.add(key)
    return tuple(steps)


def _target_label(execution_target: str, vm_name: str | None) -> str:
    """Plan 83 Fix 2: a friendly label for WHERE the repo installs."""
    t = (execution_target or "local").lower()
    if t == "vm":
        return f"Ubuntu VM '{vm_name}'" if vm_name else "Ubuntu VM"
    if t == "aws":
        return "AWS EC2 instance"
    if t == "gcp":
        return "Google Cloud VM"
    if t == "container":
        return "Docker container"
    return "Local machine"


def composite_target_label(execution_target: str, resource_name: str | None) -> str:
    """Plan 110 Fix 4: a precise `location-resource` heading so the user always knows
    WHERE things run. A multipass VM is LOCALLY hosted → `local-<vm>`; a Docker
    container → `local-<name>`; cloud → `<provider>-<name>`; bare host → `local`."""
    t = (execution_target or "local").lower()
    name = (resource_name or "").strip()
    if t == "vm":
        return f"local-{name}" if name else "local-vm"
    if t == "container":
        return f"local-{name}" if name else "local-container"
    if t in ("aws", "gcp"):
        return f"{t}-{name}" if name else t
    return "local"


def _plan_precheck_probe(
    *,
    repo: RepoCatalogRecord,
    execution_target: str,
    config_dir: Path,
    vm_name: str | None,
    pane_executor: object | None,
    display: Callable[[str], None],
    emit_thought: Callable[[str], None] | None = None,
) -> tuple[set[str], bool, dict[str, str]]:
    """Plan 76 Fix C / Plan 77 Fix 1 / Plan 82: read-only (S0) pre-check of the
    target in ONE round-trip — what tools are present, whether the repo is already
    cloned, and the installed versions of key runtimes. Executes a single delimited
    `command -v`/`--version`/`test -d` script (no mutation), so output can't bleed
    between commands and the DUCKLN-DONE marker can't corrupt parsing.
    Returns (present_tools, already_cloned, present_versions)."""
    def _think(text: str) -> None:
        if emit_thought is not None:
            try:
                emit_thought(text)
            except Exception:
                pass

    _think("Pre-check — probing the target for installed tools and runtime versions…")
    try:
        runner = ControlledCommandRunner(
            trace=lambda _m: None,
            execution_target=execution_target,
            vm_name=vm_name,
            pane_executor=pane_executor,
        )
        runtime_dir = resolve_runtime_project_dir(config_dir, repo, execution_target=execution_target)
        wrapped, _meta = _wrap_command_for_execution_target(
            config_dir=config_dir, execution_target=execution_target,
            command=_build_precheck_script(runtime_dir), cwd=None,
            preferred_vm_name=vm_name,
        )
        if wrapped is None:
            return set(), False, {}
        try:
            res = runner.run(wrapped, timeout_seconds=_PROBE_TIMEOUT_SECONDS)
        except TypeError:
            res = runner.run(wrapped)  # runner without a timeout kwarg
        if getattr(res, "timed_out", False):
            _think("Pre-check timed out — drafting from the repo's evidence instead.")
            display("Pre-check timed out — Duckln will draft from the repo's files.")
            return set(), False, {}
        present, already_cloned, present_versions = _parse_precheck_block(
            getattr(res, "stdout", "") or "", runtime_dir=runtime_dir
        )
        # Plan 183 F4: a RACED/partial first capture can come back with NO tools/versions even
        # though the target has node/python/etc. — which made the plan say "no relevant tools
        # found" and propose installing Node 20 over an existing Node 24. Re-run the probe ONCE
        # and re-parse when `present` is empty (mirrors the cloned-confirm re-probe below), so the
        # plan uses the real toolchain instead of a dumb default.
        if not present:
            try:
                res_rp = runner.run(wrapped, timeout_seconds=_PROBE_TIMEOUT_SECONDS)
            except TypeError:
                res_rp = runner.run(wrapped)
            except Exception:
                res_rp = None
            if res_rp is not None and not getattr(res_rp, "timed_out", False):
                present2, cloned2, versions2 = _parse_precheck_block(
                    getattr(res_rp, "stdout", "") or "", runtime_dir=runtime_dir
                )
                if present2:
                    present, present_versions = present2, versions2
                    already_cloned = already_cloned or cloned2
        # Plan 85 Fix 0: a partial capture must not silently flip already_cloned to
        # False (→ a redundant clone). If the block didn't report CLONED, confirm with
        # one explicit check so the skip is reliable, not race-dependent.
        if not already_cloned:
            wrapped2, _m2 = _wrap_command_for_execution_target(
                config_dir=config_dir, execution_target=execution_target,
                command=f"test -d {runtime_dir}/.git && echo DUCKLN_CLONED_CONFIRMED", cwd=None,
                preferred_vm_name=vm_name,
            )
            if wrapped2 is not None:
                try:
                    res2 = runner.run(wrapped2)
                    if "DUCKLN_CLONED_CONFIRMED" in (getattr(res2, "stdout", "") or ""):
                        already_cloned = True
                except Exception:
                    pass
    except Exception:
        return set(), False, {}
    note = []
    if present:
        note.append("present: " + ", ".join(sorted(present)))
    for rt in ("node", "python", "go", "rust"):
        if present_versions.get(rt):
            note.append(f"{rt} {present_versions[rt]}")
    note.append("repo already cloned" if already_cloned else "repo not cloned yet")
    summary = "Pre-check — " + "; ".join(note) + "."
    display(summary)
    _think(summary)
    return present, already_cloned, present_versions


def _generate_plan_for_bringup(
    *,
    repo: RepoCatalogRecord,
    current_mode: ControlMode,
    paths: ConfigPaths,
    execution_target: str,
    display: Callable[[str], None],
    llm_client: object | None,
    vm_name: str | None = None,
    pane_executor: object | None = None,
    system_probe: SystemProbe | None = None,
    approve: Callable[[str], bool] | None = None,
    emit_thought: Callable[[str], None] | None = None,
) -> RepoBringUpResult:
    """Plan Mode branch: generate a PlanRecord, persist it, render it, return.

    Plan 78 Fix F: a genuine Duckln CODE failure (unexpected exception) is
    surfaced cleanly as `duckln_internal_bug_reported` with a REDACTED diagnostic
    — never a half-finished plan or a stale spinner. (A handled model-unreachable
    case is `waiting_on_user`, not an internal bug — see Fix A.)
    """
    try:
        return _generate_plan_for_bringup_impl(
            repo=repo,
            current_mode=current_mode,
            paths=paths,
            execution_target=execution_target,
            display=display,
            llm_client=llm_client,
            vm_name=vm_name,
            pane_executor=pane_executor,
            system_probe=system_probe,
            approve=approve,
            emit_thought=emit_thought,
        )
    except Exception as exc:  # Duckln's own defect path
        try:
            from state.access import write_workflow_state

            write_workflow_state(
                paths.config_dir,
                {"active_objective_status": "duckln_internal_bug_reported"},
            )
        except Exception:
            pass
        diagnostic = redact_sensitive_data(f"{type(exc).__name__}: {exc}")
        display(
            "Duckln hit an internal error while preparing the plan and stopped "
            f"cleanly (no changes were made). Diagnostic: {diagnostic}"
        )
        return RepoBringUpResult(
            repo=repo,
            project_dir=resolve_managed_project_dir(paths.config_dir, repo),
            detected_files=(),
            mode=current_mode,
            executed_commands=(),
            verification_passed=False,
            message="Plan Mode: duckln_internal_bug_reported.",
            should_offer_repair=False,
            message_already_displayed=True,
        )


def _generate_plan_for_bringup_impl(
    *,
    repo: RepoCatalogRecord,
    current_mode: ControlMode,
    paths: ConfigPaths,
    execution_target: str,
    display: Callable[[str], None],
    llm_client: object | None,
    vm_name: str | None = None,
    pane_executor: object | None = None,
    system_probe: SystemProbe | None = None,
    approve: Callable[[str], bool] | None = None,
    emit_thought: Callable[[str], None] | None = None,
) -> RepoBringUpResult:
    """Implementation body for `_generate_plan_for_bringup` (wrapped for the
    internal-bug guard). No commands are executed; the user reviews via
    `/plan show` and runs `/plan approve` to start strict execution."""
    from duckln.plan_mode import gather_repo_understanding, PLAN_STATUS_FAILED
    from duckln.ui import render_plan_panel
    from state.access import read_config_snapshot, read_pending_plan, write_pending_plan

    def _think(text: str) -> None:
        if emit_thought is not None:
            try:
                emit_thought(text)
            except Exception:
                pass

    # Trust the live session target over whatever the caller passed.
    execution_target = (
        read_config_snapshot(paths.config_dir).get("execution_target", execution_target)
        or execution_target
        or "local"
    )
    local_project_dir = resolve_managed_project_dir(paths.config_dir, repo)
    detected_runtimes = _detect_runtimes_for_understanding(paths.config_dir)
    objective = f"Set up {repo.name} on {execution_target}"
    repo_slug = repo.repo_url or repo.name
    os_name, arch = _detect_os_and_arch()

    # Plan 73 fix: selecting a repo via `/repos` is an explicit "plan this repo
    # now" intent, so ALWAYS regenerate a fresh, grounded plan. A previously
    # pending plan (even one the user hasn't approved) must NOT pin them to a
    # stale draft — that was the "a plan is already pending" trap that showed a
    # days-old, wrong plan. Clear any existing pending plan and re-plan.
    pending = read_pending_plan(paths.config_dir)
    if pending is not None:
        from state.access import clear_pending_plan

        clear_pending_plan(paths.config_dir)

    # Plan 72 Phase 1: build a GROUNDED, COMPLETE plan from the deterministic
    # per-family setup specialists (clone → install → build → run), reusing the
    # same machinery the legacy flow uses. Repo understanding comes from a
    # read-only inspection (README + manifests fetched over HTTP) — it does NOT
    # execute anything on the target. The LLM is no longer required for a
    # correct plan; it is only an optional enricher (added in Phase 3 critic).
    from duckln.plan_mode import PlanStep, finalize_plan_from_steps

    # Plan 76 Fix A: tell the user Duckln is preparing the plan (with its
    # reasoning), not just "working". These also feed the collapsible thoughts
    # box (Fix E) when the chat surfaces it.
    display(f"Preparing the plan for {repo.name} — reading the repo and reasoning about its stack…")

    # Plan 77 Fix 3 / Plan 85 Fix 3: emit reasoning BEFORE each slow step so the
    # thinking box keeps moving — the network fetch below can take a few seconds.
    _think("Inspector — reading the README and manifests to work out the stack…")
    _think("Inspector — fetching the README and manifest files over the network…")
    display(f"Reading {repo.name}'s README and manifests…")

    try:
        inspection, repo_family, bringup_plan = _grounded_setup_plan(
            repo=repo,
            execution_target=execution_target,
            config_dir=paths.config_dir,
            system_probe=system_probe,
        )
    except Exception as exc:
        display(f"Plan Mode: could not inspect the repo to build a plan ({exc}).")
        return RepoBringUpResult(
            repo=repo,
            project_dir=local_project_dir,
            detected_files=(),
            mode=current_mode,
            executed_commands=(),
            verification_passed=False,
            message=f"Plan Mode: inspection failed ({exc}).",
            should_offer_repair=False,
            message_already_displayed=True,
        )

    _think(f"Detected repo family: {repo_family.value} (from its README and manifests).")

    # Plan 76 Fix C: optional read-only pre-check BEFORE drafting, so the plan
    # only includes what's actually missing. `plan_precheck` is on|off|ask.
    from state.access import read_config_snapshot as _read_cfg

    precheck_pref = str(_read_cfg(paths.config_dir).get("plan_precheck", "ask") or "ask").lower()
    do_precheck = False
    if precheck_pref == "on":
        do_precheck = True
    elif precheck_pref == "ask" and approve is not None:
        do_precheck = bool(approve(
            "Run a quick pre-check (what's already installed, is the repo cloned) "
            "before Duckln drafts the plan? [y/n]"
        ))
    present_tools: set[str] = set()
    already_cloned = False
    present_versions: dict[str, str] = {}
    if do_precheck:
        present_tools, already_cloned, present_versions = _plan_precheck_probe(
            repo=repo, execution_target=execution_target, config_dir=paths.config_dir,
            vm_name=vm_name, pane_executor=pane_executor, display=display,
            emit_thought=emit_thought,
        )
        # Plan 83 Fix 1: the actual drop happens AFTER the version blocks below, using
        # the full "satisfied" set (present tools + runtimes a version step installs).
        if present_tools:
            _think("Pre-check found already installed: " + ", ".join(sorted(present_tools)) + " — skipping their install steps.")
        if already_cloned:
            _think("Pre-check found the repo already cloned — skipping the clone step.")

    # Plan 79 L8: pull per-repo facts learned from a prior verified run so this
    # draft starts smarter (known-good run command + Node version for THIS repo).
    learned_facts: dict[str, str] = {}
    try:
        from state.access import read_repo_facts

        learned_facts = read_repo_facts(paths.config_dir, repo.repo_url or repo.name)
    except Exception:
        learned_facts = {}
    if learned_facts:
        _think("Recalling what worked last time for this repo: " + ", ".join(sorted(learned_facts)) + ".")

    # Plan 77 Fix 1: detect the repo's required runtime version and compare it to
    # the target's installed version — BEFORE drafting — so the plan names the
    # correct version and includes an install step the user approves once.
    _think("Planner — checking the runtime version the repo needs vs. what the target has…")
    required_node = _detect_required_node_version(
        config_dir=paths.config_dir,
        repo=repo,
        detected_files=tuple(inspection.detected_files),
        readme_excerpt=getattr(inspection, "readme_excerpt", "") or "",
    )
    # Fall back to the Node version that worked for this repo before.
    if not required_node and learned_facts.get("node_version"):
        required_node = learned_facts["node_version"]
    version_context_note = ""
    # Plan 83 Fix 1: runtimes a version step installs count as "satisfied" so the
    # README's "Ensure/Verify <runtime>" steps are dropped (no overlap with the install).
    version_managed: set[str] = set()
    if required_node:
        target_node = present_versions.get("node")  # None when precheck off / node absent
        # Plan 82 Fix B: treat "0"/falsy/unparseable as UNKNOWN, never "Node 0".
        target_known = bool(target_node) and _int_or_zero(target_node) > 0
        needs_upgrade = (not target_known) or (_int_or_zero(target_node) < _int_or_zero(required_node))
        if needs_upgrade:
            from dataclasses import replace as _dc_replace2

            install_step = _node_version_install_step(
                required_major=required_node, execution_target=execution_target
            )
            bringup_plan = _dc_replace2(
                bringup_plan, steps=(install_step, *bringup_plan.steps)
            )
            version_managed.update({"node", "npm"})  # NodeSource installs npm too
            have = f"target has Node {target_node}" if target_known else "target's Node version unknown"
            version_context_note = f"requires Node >={required_node}, {have} — plan installs Node {required_node}"
            _think(f"Planner — repo {version_context_note}.")
        else:
            version_context_note = f"requires Node >={required_node}, target has Node {target_node} — OK"
            _think(f"Planner — repo {version_context_note}.")

    # Plan 80 Fix 5: do the same generically for Python / Go / Rust — read each
    # ecosystem's OWN declared constraint and inject the right install step when the
    # target is missing or below it (a durable, multi-ecosystem version guard).
    _detected_files_tuple = tuple(inspection.detected_files)
    for _rt, _detector, _step_builder, _label in (
        ("python", _detect_required_python_version, _python_version_install_step, "Python"),
        ("go", _detect_required_go_version, _go_version_install_step, "Go"),
        ("rust", _detect_required_rust_version, _rust_version_install_step, "Rust"),
    ):
        try:
            _required = _detector(config_dir=paths.config_dir, repo=repo, detected_files=_detected_files_tuple)
        except Exception:
            _required = None
        if not _required:
            continue
        _present = present_versions.get(_rt)
        if _present is None or _xy_below(_present, _required):
            from dataclasses import replace as _dc_replace3

            _step = _step_builder(required=_required, execution_target=execution_target)
            bringup_plan = _dc_replace3(bringup_plan, steps=(_step, *bringup_plan.steps))
            version_managed.add(_rt)
            _have = f"target has {_label} {_present}" if _present else f"target's {_label} version unknown"
            _note = f"requires {_label} {_required}, {_have} — plan installs {_label} {_required}"
            version_context_note = f"{version_context_note}; {_note}" if version_context_note else _note
            _think(f"Repo {_note}.")

    # Plan 83 Fix 1: now drop any "Ensure/Verify <tool>" steps for tools that are
    # already present OR being installed by a version step — a lean plan that never
    # re-checks a satisfied prerequisite.
    _satisfied = set(present_tools) | version_managed
    if _satisfied:
        from dataclasses import replace as _dc_replace_sat

        bringup_plan = _dc_replace_sat(
            bringup_plan, steps=tuple(_drop_satisfied_prereqs(bringup_plan.steps, _satisfied))
        )

    # Plan 80 Fix 9: surface cross-repo lessons relevant to this stack so the user
    # sees Duckln applying what other repos taught it (the requirement lessons like
    # "Vite→Node 20" are already pre-empted above; this notes the rest).
    try:
        from state.access import read_common_lessons

        _family_kw = str(getattr(repo_family, "value", "") or "").split("_")[0]
        for _sig, _payload in (read_common_lessons(paths.config_dir) or {}).items():
            _lesson = _payload.get("lesson", "")
            if _lesson and _family_kw and _family_kw in _lesson.lower():
                _think(f"From other repos: {_lesson}")
                break
    except Exception:
        pass

    runtime_dir = resolve_runtime_project_dir(paths.config_dir, repo, execution_target=execution_target)

    # Plan 81: detect monorepo app subdir, backing services, and codegen needs so the
    # plan runs from the right place, starts services, and generates code before run.
    _detected = tuple(getattr(inspection, "detected_files", ()) or ())
    project_subdir = _detect_project_subdir(config_dir=paths.config_dir, repo=repo, detected_files=_detected)
    if project_subdir:
        _think(f"Monorepo detected — Duckln will run from `{project_subdir}`.")
    services = _compose_services(config_dir=paths.config_dir, repo=repo, detected_files=_detected)
    docker_available = ("docker" in present_tools) if do_precheck else True
    if services:
        if docker_available:
            _think("Repo declares backing services (" + ", ".join(services) + ") — starting them before the app.")
        else:
            _think("Repo needs services (" + ", ".join(services) + ") but Docker isn't available on the target.")
    _pkg_payload = _read_repo_package_json(paths.config_dir, repo) if "package.json" in {str(f).lower() for f in _detected} else None
    _pm = _package_manager_for(_detected)
    _run_cmd_for_codegen = learned_facts.get("run_command") or _infer_run_command(inspection, repo=repo, config_dir=paths.config_dir)
    codegen = list(_codegen_steps(payload=_pkg_payload, package_manager=_pm, run_command=_run_cmd_for_codegen))
    # Plan 113: honor the repo's FULL declared build sequence — config-declared prebuilds
    # (tauri.conf beforeBuild/beforeDev) + declared prebuild/lifecycle scripts (prepare,
    # prebuild, generate, sidecar, build:*) — so a required pre-run artifact isn't skipped.
    # Generic to ANY repo (keyed on manifests/script names), deduped against codegen.
    _prebuild_extra = (
        *_tauri_prebuild_steps(config_dir=paths.config_dir, repo=repo, detected_files=_detected),
        *_declared_prebuild_script_steps(payload=_pkg_payload, package_manager=_pm),
    )
    # Plan 120: a polyglot repo's secondary ecosystem (e.g. a Python backend in a
    # subdir) must be set up BEFORE the JS prebuild that depends on it — so a
    # `build:sidecar` needing `backend/.venv/bin/python` doesn't fail. This runs ON
    # THE TARGET (it failed before because Plan 119 scanned only the host). Prepend
    # it so it precedes the declared prebuild scripts. Emitted only when a prebuild/
    # codegen actually exists (i.e. something that could depend on a backend).
    _secondary_setup = _secondary_python_setup_steps(
        _detected, has_prebuild=bool(_prebuild_extra or codegen),
    )
    if _secondary_setup:
        codegen = list(_secondary_setup) + codegen
        _think("Polyglot repo — setting up the Python backend before the JS prebuild that needs it.")

    # Plan 153 B: a conda/mamba repo (environment.yml) sets up its env BEFORE the
    # prebuild/setup that depends on it — sibling of the pip-venv secondary setup.
    _env_yml_text = (
        _read_repo_text_file(paths.config_dir, repo, "environment.yml")
        or _read_repo_text_file(paths.config_dir, repo, "environment.yaml")
        or ""
    )
    _conda_steps = _conda_env_setup_steps(_detected, environment_yml_text=_env_yml_text)
    if _conda_steps:
        # codegen tuples are (title, command, verification_or_None) — the builder's 3rd
        # element is a SAFETY class, so drop it (None = no extra verification) before insert.
        codegen = [(t, c, None) for (t, c, _s) in _conda_steps] + list(codegen)
        _think("Repo ships an environment.yml — creating the conda/mamba env before setup (idempotent; skips if it already exists).")

    # Plan 153 A: accelerator awareness — sense the target's GPU, install the framework
    # build that MATCHES it (cu-wheel / CPU / MPS), and honestly route a GPU-required
    # repo on a CPU-only target. Best-effort + guarded so plan generation never breaks.
    try:
        _req_text = _read_repo_text_file(paths.config_dir, repo, "requirements.txt") or ""
        _pyproject_text = _read_repo_text_file(paths.config_dir, repo, "pyproject.toml") or ""
        _frameworks = _detect_dl_frameworks(_req_text, _pyproject_text)
        _readme_text = getattr(inspection, "readme_excerpt", "") or ""
        _needs_gpu = _repo_needs_gpu(requirements_text=_req_text, readme=_readme_text, repo_family=repo_family)
        # Plan 154: Apple MLX / Metal — native-macOS-only frameworks.
        _metal = _detect_metal_frameworks(_req_text, _pyproject_text)
        _needs_metal = _repo_needs_apple_metal(
            requirements_text=_req_text, readme=_readme_text, pyproject_text=_pyproject_text,
        )
        if _frameworks or _needs_gpu or _metal or _needs_metal:
            if execution_target in _REMOTE_EXECUTION_TARGETS:
                from duckln import resource_manager as _rm153
                _snap = _rm153.probe_target_resources(
                    config_dir=paths.config_dir, execution_target=execution_target, vm_name=vm_name,
                )
                _accel, _cuda_ver, _gpu_name = _snap.accelerator, _snap.cuda_version, _snap.gpu_name
            else:
                # Local target: Apple Silicon → MPS; otherwise treat as CPU (the reactive
                # macOS wheel rules / a local CUDA host are covered by recovery).
                import platform as _plat153
                _accel = "mps" if (_plat153.system() == "Darwin" and _plat153.machine() in ("arm64", "aarch64")) else "cpu"
                _cuda_ver, _gpu_name = "", ""
            if _accel == "cuda":
                _think(f"Planner → target accelerator: GPU {_gpu_name or 'NVIDIA'} (CUDA {_cuda_ver or 'unknown'}).")
            elif _accel == "mps":
                # Plan 154: name MLX when the repo declares it (Apple's native framework).
                _think(f"Planner → target accelerator: {_metal_accelerator_label(_metal)}.")
            else:
                _think("Planner → target accelerator: no GPU detected (CPU).")
            if _needs_gpu and _accel not in ("cuda", "mps"):
                # Plan 175 A2: be HONEST about which accelerators Duckln auto-configures, so an
                # AMD/Intel-GPU user isn't silently handed a CPU wheel that looks like "no GPU".
                _think("Planner → ⚠ this repo needs a GPU but the target has none — it may fail or run slowly. "
                       "Duckln auto-configures NVIDIA CUDA, Apple MPS/MLX, or CPU; AMD ROCm and Intel GPUs aren't "
                       "auto-configured — use a CUDA/MPS target (a GPU VM/cloud via /cloud), set ROCm up manually, "
                       "or run the smaller/CPU config.")
            # Plan 154 F4: an MLX/Metal repo aimed at a Linux target → warn + recommend local.
            _metal_note = _apple_metal_routing_note(needs_metal=_needs_metal, execution_target=execution_target)
            if _metal_note:
                _think("Planner → " + _metal_note)
            if _frameworks:
                _fw_pip = f"{venv_python_path(execution_target=execution_target)} -m pip"
                _fw_steps = _ml_framework_install_steps(
                    _frameworks, accelerator=_accel, cuda_version=_cuda_ver, venv_pip=_fw_pip,
                )
                _seen_fw = {c[1] for c in codegen}
                # codegen's 3rd element is a verification (or None) — strip the builder's
                # safety class so it isn't mistaken for a verify command at execution.
                _added_fw = [(s[0], s[1], None) for s in _fw_steps if s[1] not in _seen_fw]
                if _added_fw:
                    codegen = list(codegen) + _added_fw
                    _think(f"Planner → installing the {_accel}-matched build for: {', '.join(_frameworks)} (after the generic deps install).")
            # Plan 154 F3: install MLX natively ONLY on a local Apple-Silicon target (Metal-only).
            if _metal and _accel == "mps":
                _mlx_pip = f"{venv_python_path(execution_target=execution_target)} -m pip"
                _mlx_steps = _ml_framework_install_steps(
                    tuple(f for f in _metal if f.startswith("mlx")), accelerator="mps", venv_pip=_mlx_pip,
                )
                _seen_mlx = {c[1] for c in codegen}
                _added_mlx = [(s[0], s[1], None) for s in _mlx_steps if s[1] not in _seen_mlx]
                if _added_mlx:
                    codegen = list(codegen) + _added_mlx
                    _think("Planner → installing Apple MLX natively for the Mac GPU (Metal).")
    except Exception:
        pass

    # Plan 152/155 F3: an ALREADY-cloned repo isn't blindly treated as healthy — re-verify
    # via the precheck signals and, if it's set up but BROKEN, PREPEND the minimal scoped
    # repair steps (not a full re-setup) and say so, so the user sees a fix-only plan.
    if do_precheck and already_cloned:
        try:
            _healthy, _issues = assess_existing_setup(present_versions, verification_passed=None)
            if _healthy:
                _think("Pre-check → the existing setup looks healthy (no partial/broken artifacts).")
            else:
                _think("Pre-check → the repo is set up but has issues: " + "; ".join(_issues) + " — adding minimal repair steps (not a full re-setup).")
                _fix_steps = build_fix_only_plan_steps(
                    _issues, runtime_dir=str(runtime_dir), detected_files=tuple(_detected),
                    has_prebuild=bool(_prebuild_extra or codegen), package_manager=_pm,
                )
                _seen_fix = {c[1] for c in codegen}
                # (title, command, safety) → (title, command, verification=None) for codegen.
                _new_fix = [(s[0], s[1], None) for s in _fix_steps if s[1] and s[1] not in _seen_fix]
                if _new_fix:
                    codegen = list(_new_fix) + list(codegen)
        except Exception:
            pass

    _seen_cmds = {c[1] for c in codegen}
    for extra in _prebuild_extra:
        if extra[1] not in _seen_cmds:
            codegen.append(extra)
            _seen_cmds.add(extra[1])
    codegen = tuple(codegen)
    if codegen:
        _think("Repo declares prebuild/generate steps to run before it starts: " + ", ".join(c[0] for c in codegen) + ".")

    # Plan 87 Fix 0: build a GROUNDED requirements spec by understanding the whole
    # README + manifests — BEFORE recommending — then probe/plan from it. This is the
    # single source for archetype (run command), services, and env keys.
    spec = extract_requirements_spec(
        repo=repo, config_dir=paths.config_dir, inspection=inspection,
        detected_files=_detected, readme_excerpt=getattr(inspection, "readme_excerpt", "") or "",
    )
    archetype_kind, archetype_flavor = spec.archetype, spec.archetype_flavor
    # Plan 88: the pre-check probe can see the VM-side clone (src-tauri / electron in
    # package.json) when the host has no clone and GitHub-raw is unavailable (private
    # repo). Trust that desktop signal so a remote desktop app is classified correctly.
    probe_desktop = present_versions.get("desktop") if do_precheck else None
    if probe_desktop in ("tauri", "electron") and archetype_kind != "desktop_gui":
        _think(f"Inspector → the target clone shows this is a desktop ({probe_desktop}) app — reclassifying from {archetype_kind}.")
        archetype_kind, archetype_flavor = "desktop_gui", probe_desktop
    _think("Inspector → read the README + manifests; " + spec.summary_line())
    if archetype_kind == "desktop_gui":
        _think(f"Inspector → this is a desktop ({archetype_flavor}) app — it runs a native window, not just a web server.")
        _think(f"Planner → will run the {archetype_flavor} native shell, not the frontend-only `dev` script.")
    else:
        _think(f"Inspector → Classifier: archetype={archetype_kind}.")
    for _note in spec.notes:
        _think(f"Inspector → needs confirmation: {_note}")
    try:
        from state.access import write_repo_facts as _write_repo_facts

        _write_repo_facts(paths.config_dir, repo.repo_url or repo.name, {
            "archetype": archetype_kind + (f":{archetype_flavor}" if archetype_flavor else ""),
            "services": ",".join(f"{e}{(':' + v) if v else ''}" for e, v in spec.services),
        })
    except Exception:
        pass

    # Plan 87 Fix 0b: provision DBs/caches the README declares in PROSE (not just
    # docker-compose). Skip engines already covered by compose services above.
    _prose_services = _detect_prose_services(getattr(inspection, "readme_excerpt", "") or "")
    _compose_engines = {_canonical_engine(n) for n in services}
    db_provision: list[tuple[str, str, str]] = []
    if not docker_available or not services:
        _migrate_cmd = ""
        if _pkg_payload is not None and isinstance(_pkg_payload.get("scripts"), dict):
            for _mk in ("migrate", "db:migrate", "migration", "migrate:up"):
                if _mk in _pkg_payload["scripts"]:
                    _migrate_cmd = f"{_pm} run {_mk}" if _pm in ("npm", "bun") else f"{_pm} {_mk}"
                    break
        for _engine, _version in _prose_services:
            if _canonical_engine(_engine) in _compose_engines:
                continue
            _steps = _db_provision_steps(
                _engine, _version, needs_migrations=spec.needs_migrations,
                migrate_command=_migrate_cmd, present_tools=present_tools,
            )
            if _steps:
                _think(f"Planner → README needs {_engine} {_version}".rstrip() + " (no compose) — adding install + start + DB setup steps.")
                db_provision.extend(_steps)

    _think("Planner — drafting the clone → install → run steps…")
    plan_steps = _assemble_plansteps_with_clone_and_run(
        PlanStep=PlanStep,
        repo=repo,
        runtime_dir=runtime_dir,
        bringup_plan=bringup_plan,
        inspection=inspection,
        skip_clone=already_cloned,
        execution_target=execution_target,
        config_dir=paths.config_dir,
        readme_excerpt=getattr(inspection, "readme_excerpt", "") or "",
        preferred_run_command=learned_facts.get("run_command") or None,
        project_subdir=project_subdir,
        compose_services=services,
        codegen_steps=codegen,
        docker_available=docker_available,
    )

    # Plan 89: make re-runnable steps idempotent so a re-draft/re-run skips finished
    # work (apt-install when present, npm ci when node_modules exists, cp .env when set).
    from dataclasses import replace as _dc_replace_guard

    plan_steps = list(plan_steps)
    for i, s in enumerate(plan_steps):
        cmd = getattr(s, "command", "") or ""
        if _looks_like_run_command(cmd):
            continue  # never guard the run/serve step
        guarded = _idempotent_guard(cmd)
        if guarded != cmd:
            plan_steps[i] = _dc_replace_guard(s, command=guarded)

    # Plan 137 Fix 2: the pre-check found a HALF-INSTALL from a prior interrupted attempt —
    # add an EXPLICIT, visible cleanup step that removes the marker-less partial (and, for a
    # broken dpkg, reconfigures it) BEFORE the installs, then resume from the failed part
    # (completed steps still skip via `_done_steps`). The user sees the cleanup in the plan.
    _partial = (present_versions or {}).get("partial", "")
    if _partial:
        from duckln.resource_manager import reclaim_commands as _rm_partial

        _cleanup_cmd = _partial_cleanup_command()
        if "dpkg" in _partial.split(","):
            _cleanup_cmd += "; sudo dpkg --configure -a 2>/dev/null || true; sudo apt-get -f install -y 2>/dev/null || true"
        _cleanup_cmd += "; " + " && ".join(_rm_partial(execution_target=execution_target))
        cleanup_step = PlanStep(
            index=0, title="Remove the half-installed deps from an interrupted run (reclaim space)",
            description=f"A prior attempt left partial artifacts ({_partial}); remove the incomplete ones (never completed work) and reclaim space so the re-install starts clean.",
            command=_cleanup_cmd, safety_class="S2", verification=None,
            rationale="Pre-check detected a half-install — clean it before re-installing, then resume from the failed step.",
            estimated_seconds=20, confidence=0.9, origin="planner",
        )
        _clone_i = next((i for i, s in enumerate(plan_steps) if "git clone" in (getattr(s, "command", "") or "")), None)
        _at = (_clone_i + 1) if _clone_i is not None else 0
        plan_steps = [*plan_steps[:_at], cleanup_step, *plan_steps[_at:]]
        plan_steps = [_dc_replace_guard(s, index=i) for i, s in enumerate(plan_steps, start=1)]
        _think(f"Pre-check found a half-install ({_partial}) — added a cleanup step to remove it before re-installing.")

    # Plan 88: if this is a desktop app, the RUN step must be the native shell
    # (`tauri dev` / `electron .`), not the frontend `dev`/`vite` script — even when
    # the run command was inferred from repo-knowledge (private repo, no package.json).
    if archetype_kind == "desktop_gui":
        from dataclasses import replace as _dc_replace_run

        plan_steps = list(plan_steps)
        desired_run = None
        if isinstance(_pkg_payload, dict) and isinstance(_pkg_payload.get("scripts"), dict):
            desired_run = _desktop_run_command_from_scripts(_pkg_payload["scripts"], _pm, archetype_flavor)
        if not desired_run:
            desired_run = "npx tauri dev" if archetype_flavor == "tauri" else "npx electron ."
        for i, s in enumerate(plan_steps):
            cmd = getattr(s, "command", "") or ""
            if _looks_like_run_command(cmd) and not _looks_like_desktop_run_command(cmd):
                _think(f"Planner → rewriting the run step to the desktop command `{desired_run}` (was `{cmd}`).")
                plan_steps[i] = _dc_replace_run(s, command=desired_run)
                break

    # Plan 87 Fix 0b: insert README-declared DB/service provisioning steps before the
    # run step (after clone/install) so the app has its database when it starts.
    if db_provision:
        from dataclasses import replace as _dc_replace_db

        run_idx = next(
            (i for i, s in enumerate(plan_steps) if _looks_like_run_command(getattr(s, "command", "") or "")),
            len(plan_steps),
        )
        new_db_steps = [
            PlanStep(
                index=0, title=title, description="README-declared service Duckln provisions.",
                command=command, safety_class=safety, verification="command exits 0",
                rationale="README declares this service; no docker-compose provides it.",
                estimated_seconds=60, confidence=0.85, origin="planner",
            )
            for (title, command, safety) in db_provision
        ]
        plan_steps = [*plan_steps[:run_idx], *new_db_steps, *plan_steps[run_idx:]]
        plan_steps = [_dc_replace_db(s, index=i) for i, s in enumerate(plan_steps, start=1)]

    # Plan 110 Fix 2 (generalizes Plan 89): ANY heavy native/compiler build (Rust,
    # C/C++, Gradle/Maven, large webpack/vite build) OOMs on a small remote target.
    # When ANY plan step is a heavy build on a remote target with low/unknown RAM and
    # no swap, add a swap + single-threaded-build step BEFORE it. Keyed on the detected
    # build command — never on a repo name. Idempotent (swap added only if absent).
    _heavy_idx = next(
        (i for i, s in enumerate(plan_steps) if _is_heavy_build(getattr(s, "command", "") or "")),
        None,
    )
    if _heavy_idx is not None and execution_target in _REMOTE_EXECUTION_TARGETS:
        # Plan 117: proactively surface a DISK crunch BEFORE the heavy build (the 96%-full
        # case), with real numbers — so the user can grow it via `/resources` up front
        # instead of discovering it only when the build runs out of space.
        try:
            from duckln import resource_manager as _rm

            _snap = _rm.probe_target_resources(config_dir=paths.config_dir, execution_target=execution_target, vm_name=vm_name)
            if _snap.disk_total_mb and _snap.disk_used_pct >= 90:
                _think(f"Planner → ⚠ {_target_label(execution_target, vm_name)} disk is {_snap.disk_used_pct}% full "
                       f"({_snap.disk_free_mb // 1024 or '<1'} GB free) — reclaiming caches before the build, "
                       f"and `/resources` can grow it if needed.")
                # Plan 127: actually FREE space (not just warn) — clear reclaimable
                # caches/stale temp builds (never the repo) before the heavy build, so
                # repeated runs don't fill the VM. Inserted before the heavy step.
                from dataclasses import replace as _dc_replace_disk

                reclaim_step = PlanStep(
                    index=0, title="Reclaim disk space (clear caches) before the heavy build",
                    description="The target disk is nearly full; clear package-manager caches + stale temp builds (never the repo) so the build doesn't run out of space.",
                    command=" && ".join(_rm.reclaim_commands(execution_target=execution_target)),
                    safety_class="S2", verification=None,
                    rationale="Disk ≥90% full before a heavy build — reclaim reclaimable space first.",
                    estimated_seconds=30, confidence=0.85, origin="planner",
                )
                plan_steps = [*plan_steps[:_heavy_idx], reclaim_step, *plan_steps[_heavy_idx:]]
                plan_steps = [_dc_replace_disk(s, index=i) for i, s in enumerate(plan_steps, start=1)]
                _heavy_idx += 1
        except Exception:
            pass
        _mem_kb = int(present_versions.get("mem_kb", "0") or 0) if do_precheck else 0
        _has_swap = (present_versions.get("swap") == "yes") if do_precheck else False
        # Apply when RAM is known-low, OR when RAM is unknown (no pre-check) — the swap
        # command is idempotent, so it's safe to pre-empt a heavy build either way.
        _apply = _has_swap is False and (_mem_kb == 0 or _needs_low_memory_guard(mem_kb=_mem_kb, has_swap=_has_swap))
        if _apply:
            from dataclasses import replace as _dc_replace_mem

            ram_note = f"~{_mem_kb // 1024} MB RAM" if _mem_kb else "RAM unknown"
            _think(f"Planner → heavy build on the {execution_target} ({ram_note}, no swap) — adding swap + a single-threaded build so the compile isn't OOM-killed.")
            # Plan 110 Fix 6: be honest when the target is genuinely too small — swap
            # lets it compile but slowly; recommend more RAM.
            if 0 < _mem_kb < 2_500_000:
                _resize = f"`multipass set local.{vm_name}.memory=6G` then restart" if (execution_target == "vm" and vm_name) else "increasing the target's RAM to ≥6 GB"
                _think(f"Planner → heads up: {_mem_kb // 1024} MB is small for this build — it'll work with swap but be slow; for a smooth build consider {_resize}.")
            mem_step = PlanStep(
                index=0, title="Add swap + cap the build to one job (heavy build, low-RAM target)",
                description="A heavy native/compiler build needs more memory than this target has; swap + single-threaded compilation prevents an OOM kill.",
                command=_low_memory_swap_command(), safety_class="S3",
                verification="command exits 0",
                rationale="Heavy build on a low/unknown-RAM remote target — pre-empt the OOM that SIGKILLs the compile.",
                estimated_seconds=60, confidence=0.9, origin="planner",
            )
            plan_steps = [*plan_steps[:_heavy_idx], mem_step, *plan_steps[_heavy_idx:]]
            plan_steps = [_dc_replace_mem(s, index=i) for i, s in enumerate(plan_steps, start=1)]

    # Plan 162 F4 (C4): PROACTIVE right-sizing. A repo can be resource-heavy by its STACK
    # (Cargo/conda/CMake/Gradle/Maven) even without an obviously-heavy build COMMAND, so the
    # block above (keyed on `_is_heavy_build`) wouldn't have fired. When the repo is heavy and
    # the target is remote, probe the target, estimate the need (heavy_build=True), and surface
    # a sizing recommendation up front (user-approved `/resources` or `/vm` — never auto-resize).
    if (
        _heavy_idx is None
        and execution_target in _REMOTE_EXECUTION_TARGETS
        and _repo_is_resource_heavy(plan_steps, _detected)
    ):
        try:
            from duckln import resource_manager as _rm4

            _snap4 = _rm4.probe_target_resources(config_dir=paths.config_dir, execution_target=execution_target, vm_name=vm_name)
            _need4 = _rm4.estimate_requirement(snapshot=_snap4, heavy_build=True)
            if _need4.disk_total_mb and _snap4.disk_total_mb and _need4.disk_total_mb > _snap4.disk_total_mb:
                _grow_gb = (_need4.disk_total_mb - _snap4.disk_total_mb) / 1024.0
                _think(
                    f"Planner → {_target_label(execution_target, vm_name)} looks tight for this heavy stack "
                    f"({_need4.reason or 'heavy build needs more free disk'}). Right-size up front (~+{_grow_gb:.1f} GB) "
                    "via `/resources` (or `/vm` for a fresh, larger target) before the build — "
                    "Duckln never resizes without your approval."
                )
            elif _snap4.ram_mb and 0 < _snap4.ram_mb < 2500:
                _think(
                    f"Planner → {_target_label(execution_target, vm_name)} has ~{_snap4.ram_mb} MB RAM — small for this "
                    "heavy stack; consider a larger target via `/resources` or `/vm` (user-approved) for a smooth build."
                )
        except Exception:
            pass

    # Plan 87: for a desktop app on a headless remote target, add the streaming-stack
    # install step (Xvfb + noVNC + webview libs) right before the run step, so the
    # user approves it and the native window can be streamed to their browser.
    if archetype_kind == "desktop_gui" and execution_target in _REMOTE_EXECUTION_TARGETS:
        stream_install = _desktop_stream_install_command(archetype_flavor, present_tools=present_tools)
        if stream_install:
            _think("Planner → headless target — adding a step to install the virtual-display + noVNC streaming stack so the app's window streams to your browser.")
            run_idx = next(
                (i for i, s in enumerate(plan_steps) if _looks_like_run_command(getattr(s, "command", "") or "")),
                len(plan_steps),
            )
            stream_step = PlanStep(
                index=run_idx + 1,
                title="Install the virtual-display + noVNC streaming stack",
                description="A desktop app needs a display; this lets the headless target stream its window to your browser.",
                command=stream_install,
                safety_class="S3",
                verification="command exits 0",
                rationale="Desktop (GUI) app on a headless target — Xvfb + x11vnc/noVNC stream the native window to the host browser.",
                estimated_seconds=120,
                confidence=0.9,
                origin="planner",
            )
            from dataclasses import replace as _dc_replace_idx

            plan_steps = [*plan_steps[:run_idx], stream_step, *plan_steps[run_idx:]]
            plan_steps = [_dc_replace_idx(s, index=i) for i, s in enumerate(plan_steps, start=1)]

    # Plan 73 D1 (Inject): if a VERIFIED skill exists for this exact
    # family+OS+target signature (recorded after a prior successful run), prefer
    # its known-good command sequence as the backbone — this is the learning
    # loop closing: each run injects what the last verified run learned.
    injected_skill_note = ""
    try:
        from duckln.plan_mode import (
            PlanStep as _PlanStep,
            find_matching_skill,
            load_skill_commands,
        )

        matched = find_matching_skill(
            paths.config_dir,
            repo_family=repo_family.value,
            os_name=os_name,
            execution_target=execution_target,
        )
        if matched:
            skill_cmds = load_skill_commands(paths.config_dir, matched)
            if skill_cmds:
                plan_steps = [
                    _PlanStep(
                        index=i,
                        title=("Clone repo" if cmd.startswith("git clone") else f"Step {i}"),
                        description="",
                        command=cmd,
                        safety_class="S0",  # reclassified in Stage 4
                        verification=None,
                        rationale=f"From verified skill `{matched}` (a prior successful run on this family/OS/target).",
                        estimated_seconds=30,
                        confidence=1.0,
                        origin="planner",
                    )
                    for i, cmd in enumerate(skill_cmds, start=1)
                ]
                injected_skill_note = f"Injected verified skill `{matched}` from a prior successful run."
    except Exception:
        injected_skill_note = ""

    # Plan 156 P2: reasoning is the CORE — a non-trivial repo CANNOT be planned without a
    # reachable model. If reasoning is on, the repo is non-trivial, and NO model is reachable
    # (none configured, or build returns None) → HARD honest-stop, never a deterministic-only
    # plan. (A configured-but-UNREACHABLE model is caught later by the critic's model_unreachable.)
    from duckln import recovery as _rec
    _nontrivial156 = _planning_is_nontrivial(
        inspection.detected_files,
        low_confidence_family=_is_low_confidence_repo_family(inspection, repo_family),
    )
    # Plan 170 F1: this honest-stop is ONLY for "no model CONFIGURED" (its documented intent;
    # a configured-but-unreachable model is caught later by the critic's real connection check).
    # Key it on whether a provider+model is configured in the snapshot — NOT on a transient
    # client-build that can return None for a fully-configured, working model (the false-stop bug).
    _build_client156 = None
    try:
        _snap156 = read_config_snapshot(paths.config_dir)
        _configured156 = bool(str(_snap156.get("provider") or "").strip()) and bool(str(_snap156.get("model") or "").strip())
    except Exception:
        _configured156 = False
    try:
        _build_client156 = build_default_llm_client_or_none(paths.config_dir)
    except Exception:
        _build_client156 = None
    _model_reachable156 = (llm_client is not None) or _configured156 or (_build_client156 is not None)
    if _nontrivial156 and _rec.reasoning_enabled() and not _model_reachable156:
        from state.access import clear_pending_plan
        from duckln.plan_mode import model_unreachable_message

        clear_pending_plan(paths.config_dir)
        _provider156 = read_config_snapshot(paths.config_dir).get("provider")
        display(_with_context_hint(model_unreachable_message(_provider156)))
        # Plan 168 F2/F4: this is a SETUP blocked by the model — record a RESUMABLE setup
        # (repo_deploy) objective (superseding any stale runtime_repair for this repo) so the
        # status reads "setting up" and a later "continue the setup" resumes the BRING-UP from
        # where it left (via _done_steps), not the runtime-repair flow.
        try:
            _write_repo_objective_state(
                paths,
                repo=repo,
                kind="repo_deploy",
                status="needs_user_decision",
                goal=f"Set up {repo.name}",
                execution_target=execution_target,
                requires_user_decision=True,
                resume_hint=(
                    "The model was unavailable, so planning paused. Switch/fix the model "
                    "(e.g. `/provider`), then say 'continue the setup' (or run `/plan continue`) "
                    "to resume from where Duckln left off — completed steps are skipped."
                ),
                last_blocker="model unavailable during planning",
            )
        except Exception:
            pass
        display(
            "Setup is paused, not failed — fix the model above, then say 'continue the setup' "
            "(or run `/plan continue`; `/plan show` shows the plan once re-drafted). Duckln "
            "resumes from where it left and skips the steps already done."
        )
        # Plan 170 F1: capture WHY we decided "no model" so a real future occurrence is
        # traceable (configured? did the client build?) instead of guessed.
        _think(
            f"No model configured — honest-stop (configured={_configured156}, "
            f"client_built={_build_client156 is not None}, llm_client={'set' if llm_client is not None else 'none'}). "
            "Reasoning is the core for a non-trivial repo; NOT emitting a deterministic-only plan."
        )
        try:
            _rec.append_thinking_log(
                paths.config_dir, repo_slug=repo.name, surface="plan",
                title="Planning honest-stop — no reachable model",
                lines=["Non-trivial repo + no reachable model → honest-stop. Reasoning is the core; "
                       "Duckln will not emit a deterministic-only plan that pretends to be complete. "
                       "Connect/fix the configured provider/model (valid API key + network, or for a "
                       "local runtime `ollama serve`) and retry."],
            )
        except Exception:
            pass
        return RepoBringUpResult(
            repo=repo, project_dir=local_project_dir,
            detected_files=tuple(inspection.detected_files), mode=current_mode,
            executed_commands=(), verification_passed=False,
            message="Plan Mode: no reachable model — reasoning required.",
            should_offer_repair=False, message_already_displayed=True,
        )

    # Plan 132 A2: for a COMPLEX/uncertain repo, REASON about the setup before proposing
    # steps — a read-only investigation pass (read manifests/scripts, probe toolchain)
    # whose brief enriches the planning context. Common/clear repos skip this (fast path).
    # Off under the unit suite (reasoning_enabled) and bounded by a wall-clock timeout.
    _readme_for_understanding = inspection.readme_excerpt
    try:

        _files_l = {str(f).lower() for f in inspection.detected_files}
        _polyglot = "package.json" in _files_l and bool(_files_l & {"requirements.txt", "pyproject.toml", "setup.py"})
        # Plan 145 F0: ANTICIPATE — investigate by DEFAULT for any non-trivial repo, so Duckln
        # reasons the full setup/dependency chain UPFRONT (reads declared build/prebuild scripts
        # and the files they call) instead of discovering needs reactively via errors. Only a
        # trivially-simple repo (one manifest, no build, known family) skips this fast path.
        # NB: a polyglot backend often lives in a SUBDIR (e.g. backend/requirements.txt), which
        # top-level file checks miss — so we trigger broadly on the presence of a JS/desktop/
        # native build surface where implicit deps (PyInstaller, codegen, etc.) hide.
        _build_surface = bool(_files_l & {
            "package.json", "cargo.toml", "tauri.conf.json", "makefile", "cmakelists.txt",
            "build.gradle", "pom.xml", "go.mod", "pyproject.toml",
        })
        _nontrivial = (
            _is_low_confidence_repo_family(inspection, repo_family)
            or _polyglot
            or _build_surface
            or len(inspection.detected_files) >= 3
        )
        if _nontrivial and llm_client is not None and _rec.reasoning_enabled():
            _think("Investigating the repo to anticipate the full setup before proposing steps…")
            # Plan 147 F4: show the one-line depth note ONLY when the configured model is weak
            # (a capable model shows nothing). The depth ceiling is a model choice, surfaced
            # minimally — not a nag, not on a good model.
            try:
                _mdl = str(read_config_snapshot(paths.config_dir).get("model") or "")
                # Plan 156 P1: behavior-probe the model ONCE (cached) so the note reflects what
                # it DOES (follows the structured protocol), not just its name — fair to Ollama/
                # OpenRouter-free models.
                _rec.probe_model_reasoning_capability(llm_client, model_id=_mdl, config_dir=paths.config_dir)
                _note = _rec.weak_model_reasoning_note(_mdl, config_dir=paths.config_dir)
                if _note:
                    display(_note)
            except Exception:
                pass
            # Plan 133 F4: investigate the LIVE target — pass the local clone when it
            # exists (fs tools), else the runtime/target repo path so the agent's
            # shell.probe reads the ACTUAL repo on the VM/container/cloud (real scripts,
            # build files, tree) instead of getting None and only seeing the README.
            _investigate_dir = local_project_dir if local_project_dir.exists() else Path(runtime_dir)
            _brief = _rec.reason_about_plan(
                config_dir=paths.config_dir, repo_name=repo.name,
                project_dir=_investigate_dir,
                execution_target=execution_target, vm_name=vm_name, objective=objective,
                mode=current_mode, approve=approve, llm_client=llm_client,
                emit_thought=emit_thought, display=display, budget=_reasoning_budget_for(repo),
            )
            if _brief:
                _readme_for_understanding = (inspection.readme_excerpt or "") + "\n\n## Duckln planning brief\n" + _brief
    except Exception:
        _readme_for_understanding = inspection.readme_excerpt

    understanding = gather_repo_understanding(
        objective=objective,
        project_dir=local_project_dir if local_project_dir.exists() else None,
        repo_slug=repo_slug,
        config_dir=paths.config_dir,
        os_name=os_name,
        arch=arch,
        execution_target=execution_target,
        control_mode=current_mode.value,
        detected_runtimes=detected_runtimes,
        override_detected_files=tuple(inspection.detected_files),
        override_readme_excerpt=_readme_for_understanding,
        override_family=repo_family.value,
    )

    # Plan 156 P4: run the multi-agent PLANNING pipeline (supervisor → inspector → planner →
    # critic) LIVE for a non-trivial repo on a reachable model. It emits the agent TRACE
    # (observable via `/agents trace`), captures its reasoning to logical-thinking.md, and its
    # critic reasoning is surfaced. The deterministic bring-up plan stays the structural base
    # (richer for known families); the supervisor is the reasoning + observability layer.
    # Bounded + best-effort — a failure NEVER blocks planning.
    _supervisor_reasoning = ""
    if (llm_client is not None and _rec.reasoning_enabled()
            and _planning_is_nontrivial(inspection.detected_files,
                                        low_confidence_family=_is_low_confidence_repo_family(inspection, repo_family))):
        try:
            from duckln.harness.plan_supervisor import run_plan_supervisor as _run_supervisor

            _sup = _rec.run_bounded_agent(
                lambda: _run_supervisor(
                    objective=objective, understanding=understanding, llm_client=llm_client,
                    config_dir=paths.config_dir, mode=current_mode, display=None,
                ),
                timeout_seconds=_rec.REASONING_WALLCLOCK_SECONDS,
            )
            if _sup is not None:
                _supervisor_reasoning = str(getattr(getattr(_sup, "plan", None), "critic_reasoning", "") or "")
                _think("Supervisor pipeline (inspector→planner→critic) reviewed the setup"
                       + (f": {_supervisor_reasoning}" if _supervisor_reasoning else "."))
                try:
                    _rec.append_thinking_log(
                        paths.config_dir, repo_slug=repo.name, surface="plan",
                        title="Multi-agent planning (supervisor→inspector→planner→critic)",
                        lines=[f"Pipeline ran live (harness={getattr(_sup, 'used_harness', False)}, "
                               f"session={getattr(_sup, 'session_id', '')}, "
                               f"candidate_steps={len(getattr(getattr(_sup, 'plan', None), 'steps', ()) or ())}).",
                               f"Critic reasoning: {_supervisor_reasoning or '(none)'}"],
                    )
                except Exception:
                    pass
        except Exception:
            pass

    run_step = next((s for s in plan_steps if _looks_like_run_command(getattr(s, "command", "") or "")), None)
    if run_step is not None:
        _think(f"Chosen run command: {run_step.command}")
    if injected_skill_note:
        _think(injected_skill_note)

    context_summary = _summarize_understanding_safe(understanding)
    if injected_skill_note:
        context_summary = f"{context_summary}; {injected_skill_note}"
    if version_context_note:
        context_summary = f"{context_summary}; Node {version_context_note}"

    # Plan 83 Fix 2: surface pre-check findings + the target machine on the plan, and
    # remember the env keys the user must set (for the post-setup prompt).
    precheck_summary = _format_precheck_summary(present_tools, present_versions, already_cloned) if do_precheck else ""
    target_label = _target_label(execution_target, vm_name)
    required_env_keys = _required_env_vars(
        config_dir=paths.config_dir, repo=repo,
        detected_files=tuple(getattr(inspection, "detected_files", ()) or ()),
    )

    plan = finalize_plan_from_steps(
        objective=objective,
        repo_slug=repo_slug,
        context_summary=context_summary,
        steps=tuple(plan_steps),
        mode=current_mode,
        understanding=understanding,
        precheck_summary=precheck_summary,
        target_label=target_label,
        required_env_keys=required_env_keys,
    )

    # Plan 72 Phase 3: mandatory supervisor critic. The verdict is recorded on
    # the plan (shown under "Supervisor"); a `block` surfaces the exact missing
    # question / external blocker and does NOT show an approvable plan; a
    # `skipped` verdict (no model) is DISCLOSED, never a silent pass.
    if plan.status != PLAN_STATUS_FAILED:
        from duckln.ai_client import build_default_llm_client_or_none
        from duckln.plan_mode import critic_review

        display("Supervisor is reviewing the plan… (a local model can take a moment)")
        _think("Supervisor — reviewing the plan for safety and completeness… (local models can take a moment)")
        from duckln.plan_mode import MODEL_UNREACHABLE_BLOCKER, MODEL_UNRESPONSIVE_BLOCKER, model_unreachable_message

        from duckln.ai_client import build_llm_client_for_role as _build_llm_client_for_role
        # Plan 179 C2: the plan supervisor/critic runs under the SUPERVISOR role.
        critic_client = llm_client or _build_llm_client_for_role(paths.config_dir, "SUPERVISOR")
        _critic_model = str(read_config_snapshot(paths.config_dir).get("model") or "")
        verdict = critic_review(plan=plan, understanding=understanding, llm_client=critic_client,
                                config_dir=paths.config_dir, model_id=_critic_model)
        # Plan 78 Fix D: one replan loop — if the supervisor asks to revise, repair
        # the mechanical issues it named and re-review ONCE before showing the user.
        if verdict.verdict == "revise" and verdict.external_blocker not in (MODEL_UNREACHABLE_BLOCKER, MODEL_UNRESPONSIVE_BLOCKER):
            _think("Supervisor asked for a change — revising the plan once…")
            repaired = _repair_plan_for_revise(plan, verdict, execution_target=execution_target)
            if repaired is not None and repaired is not plan:
                plan = repaired
                verdict = critic_review(plan=plan, understanding=understanding, llm_client=critic_client,
                                        config_dir=paths.config_dir, model_id=_critic_model)
        # Plan 78 / Plan 160: the LLM is the decision-maker. If the model could not REVIEW the
        # plan — unreachable, OR reachable but too slow/weak to deliver a usable review — Duckln
        # did NOT review it, so it must NOT present a (deterministic-only) plan. Honest-stop;
        # waiting_on_user (start/fix/strengthen the model, retry).
        if verdict.external_blocker in (MODEL_UNREACHABLE_BLOCKER, MODEL_UNRESPONSIVE_BLOCKER):
            from state.access import clear_pending_plan

            clear_pending_plan(paths.config_dir)
            if verdict.external_blocker == MODEL_UNREACHABLE_BLOCKER:
                display(_with_context_hint(model_unreachable_message(read_config_snapshot(paths.config_dir).get("provider"))))
                _msg = "Plan Mode: model unreachable."
            else:
                display(verdict.reason)  # "reachable but too slow/weak — retry or stronger model"
                _msg = "Plan Mode: model could not review the plan (too slow/weak)."
            return RepoBringUpResult(
                repo=repo,
                project_dir=local_project_dir,
                detected_files=tuple(understanding.detected_files),
                mode=current_mode,
                executed_commands=(),
                verification_passed=False,
                message=_msg,
                should_offer_repair=False,
                message_already_displayed=True,
            )
        if verdict.verdict == "block":
            from state.access import clear_pending_plan

            clear_pending_plan(paths.config_dir)
            if verdict.missing_question:
                display(f"Plan Mode needs one detail before planning: {verdict.missing_question}")
            else:
                display(
                    "Plan Mode is blocked by an external prerequisite: "
                    f"{verdict.external_blocker or verdict.reason}"
                )
            return RepoBringUpResult(
                repo=repo,
                project_dir=local_project_dir,
                detected_files=tuple(understanding.detected_files),
                mode=current_mode,
                executed_commands=(),
                verification_passed=False,
                message="Plan Mode: blocked by supervisor critic.",
                should_offer_repair=False,
                message_already_displayed=True,
            )
        if verdict.verdict == "revise":
            banner = f"Supervisor suggests: {verdict.reason}"
        else:
            banner = f"Supervisor approved: {verdict.reason}" if verdict.reason else "Supervisor approved."
        _think(f"Supervisor review — {banner}")
        from dataclasses import replace as _dc_replace

        plan = _dc_replace(plan, critic_reasoning=banner)

    if plan.status == PLAN_STATUS_FAILED:
        from state.access import clear_pending_plan

        clear_pending_plan(paths.config_dir)
        display(render_plan_panel(plan))
        display(
            "Plan Mode could not derive runnable steps for this repo. "
            "Run `/repos` again to retry — nothing was changed."
        )
        return RepoBringUpResult(
            repo=repo,
            project_dir=local_project_dir,
            detected_files=tuple(understanding.detected_files),
            mode=current_mode,
            executed_commands=(),
            verification_passed=False,
            message="Plan Mode: no runnable steps derived.",
            should_offer_repair=False,
            message_already_displayed=True,
        )

    write_pending_plan(paths.config_dir, plan.to_dict())
    display(render_plan_panel(plan))
    display(
        "Run `/plan approve` to execute this plan, `/plan reject` to discard, "
        "or `/plan edit` to revise."
    )

    return RepoBringUpResult(
        repo=repo,
        project_dir=local_project_dir,
        detected_files=tuple(understanding.detected_files),
        mode=current_mode,
        executed_commands=(),
        verification_passed=False,
        message=f"Plan Mode: plan {plan.plan_id[:8]} pending review.",
        should_offer_repair=False,
        message_already_displayed=True,
    )


def _detect_runtimes_for_understanding(config_dir: Path) -> tuple[str, ...]:
    """Best-effort detection of installed runtimes for the understanding payload."""
    import shutil as _shutil

    detected: list[str] = []
    for name, key in (
        ("node", "node"),
        ("python3", "python"),
        ("python", "python"),
        ("cargo", "cargo"),
        ("go", "go"),
        ("java", "java"),
        ("docker", "docker"),
    ):
        if _shutil.which(name) is not None and key not in detected:
            detected.append(key)
    return tuple(detected)


def _detect_os_and_arch() -> tuple[str, str]:
    """Lightweight OS+arch detection for the plan-mode context payload."""
    import platform as _platform

    return _platform.system() or "", _platform.machine() or ""


def _summarize_understanding_safe(understanding) -> str:
    """Context line for the plan panel, derived from the grounded understanding."""
    from duckln.plan_mode import _summarize_understanding

    return _summarize_understanding(understanding)


def _grounded_setup_plan(
    *,
    repo: RepoCatalogRecord,
    execution_target: str,
    config_dir: Path,
    system_probe: SystemProbe | None,
):
    """Plan 72: read-only inspection (HTTP) + family classification + the
    deterministic per-family `build_plan` backbone. Executes NOTHING on the
    target. Returns ``(inspection, repo_family, RepoBringUpPlan)``."""
    supervisor = RepoBringUpSupervisor()
    local_project_dir = resolve_managed_project_dir(config_dir, repo)
    inspection = inspect_repo_for_bringup(
        repo,
        local_project_dir,
        system_probe=system_probe,
        execution_target=execution_target,
        config_dir=config_dir,
        context_service=supervisor._context_service,
    )
    repo_family = classify_repo_family(inspection)
    # Plan 73 fix: when the remote read-only fetch yields too little to classify
    # confidently (e.g. a VM/cloud repo not cloned, or a flaky raw fetch), fall
    # back to the repo-catalog metadata (framework/category/description) so we
    # still pick the RIGHT family/specialist instead of defaulting to Python.
    if _is_low_confidence_repo_family(inspection, repo_family):
        from duckln.plan_mode import family_hint_from_metadata

        hint = family_hint_from_metadata(
            framework=getattr(repo, "framework", "") or "",
            category=getattr(repo, "category", "") or "",
            description=getattr(repo, "description", "") or "",
        )
        mapped = {
            "node_typescript": RepoFamily.NODE_TYPESCRIPT,
            "python": RepoFamily.PYTHON,
            "rust": RepoFamily.RUST,
            "go": RepoFamily.GO_NATIVE,
            "cpp_native": RepoFamily.CPP_NATIVE,
        }.get(hint or "")
        if mapped is not None:
            repo_family = mapped
    specialist = supervisor._select_specialist(inspection, repo_family)
    bringup_plan = specialist.build_plan(inspection, repo_family, config_dir=config_dir)
    # Plan 74 fix: THINK about the repo instead of installing every tool the
    # README mentions. Scope the prerequisite-install steps to the detected
    # family + tools actually referenced by the install/build/run commands, so
    # a Node repo doesn't install rust/go/docker/uv just because they appear in
    # the README prose.
    scoped_steps = _scope_prereqs_to_family(
        steps=bringup_plan.steps,
        repo_family=repo_family,
        detected_files=inspection.detected_files,
    )
    if scoped_steps != bringup_plan.steps:
        from dataclasses import replace as _dc_replace

        bringup_plan = _dc_replace(bringup_plan, steps=tuple(scoped_steps))
    return inspection, repo_family, bringup_plan


# Family → the runtime/tool tokens that are legitimately relevant to install.
_FAMILY_RELEVANT_TOOLS: dict[str, set[str]] = {
    "node_typescript": {"node", "nodejs", "npm", "npx", "pnpm", "yarn", "corepack"},
    "python": {"python", "python3", "pip", "pip3", "uv", "poetry", "conda", "pipx", "pyenv"},
    "rust": {"rust", "rustc", "cargo", "rustup"},
    "go_native": {"go", "golang"},
    "cpp_native": {"cmake", "make", "gcc", "g++", "clang", "ninja", "pkg-config"},
}

_PREREQ_PURPOSE_RE = re.compile(r"ensure\s+([A-Za-z0-9_.+-]+)\s+.*\bis installed", re.IGNORECASE)

# Maps a leading command token to the runtime it implies, so a real install/run
# command (e.g. `npm ci`, `cargo build`) keeps that tool's prerequisite.
_COMMAND_TOOL_TOKENS: dict[str, str] = {
    "npm": "node", "npx": "node", "pnpm": "node", "yarn": "node", "node": "node",
    "pip": "python", "pip3": "python", "python": "python", "python3": "python",
    "uv": "python", "poetry": "python", "conda": "python",
    "cargo": "rust", "rustc": "rust",
    "go": "go",
    "cmake": "cmake", "make": "make", "gcc": "gcc", "g++": "g++", "clang": "clang",
    "docker": "docker",
}


def _scope_prereqs_to_family(
    *,
    steps: tuple[RepoBringUpStep, ...],
    repo_family: RepoFamily,
    detected_files: tuple[str, ...],
) -> list[RepoBringUpStep]:
    """Drop prerequisite-install steps for tools that aren't part of the repo's
    actual stack. A prereq is KEPT only when its tool is (a) the detected
    family's runtime, (b) git (always — needed to clone), (c) referenced by an
    actual install/build/run command in the plan, or (d) docker when a
    Dockerfile/compose file is present."""
    family_key = getattr(repo_family, "value", str(repo_family))
    relevant: set[str] = set(_FAMILY_RELEVANT_TOOLS.get(family_key, set()))
    relevant.add("git")

    files_lower = {f.lower() for f in detected_files}
    if any("dockerfile" in f or "docker-compose" in f or "compose.y" in f for f in files_lower):
        relevant.add("docker")

    def _is_prereq(step: RepoBringUpStep) -> bool:
        return bool(_PREREQ_PURPOSE_RE.search(step.purpose or ""))

    # Tools referenced by ACTUAL (non-prereq) commands become relevant too.
    for step in steps:
        if _is_prereq(step):
            continue
        cmd = (step.command or "").strip()
        for token in cmd.split():
            base = token.rsplit("/", 1)[-1]
            mapped = _COMMAND_TOOL_TOKENS.get(base)
            if mapped:
                relevant.add(mapped)
            if base == "docker":
                relevant.add("docker")

    kept: list[RepoBringUpStep] = []
    for step in steps:
        m = _PREREQ_PURPOSE_RE.search(step.purpose or "")
        if not m:
            kept.append(step)
            continue
        tool = m.group(1).strip().lower()
        # Normalise a few aliases to the relevance vocabulary.
        tool_norm = {"nodejs": "node", "golang": "go", "python3": "python"}.get(tool, tool)
        if tool_norm in relevant or tool in relevant:
            kept.append(step)
        # else: drop — irrelevant to this repo's stack.
    return kept


_RUN_COMMAND_PATTERNS = (
    re.compile(r"\b(npm|yarn|pnpm|bun)\s+(run\s+)?(dev|start|serve|preview)\b"),
    # Plan 135 F1 (3a): `npm run <script> <dev-keyword>` — e.g. `npm run tauri dev`,
    # `pnpm run web serve` — the keyword isn't directly after `run`, so the pattern
    # above misses it. Match a single script token before the run keyword.
    re.compile(r"\b(npm|yarn|pnpm|bun)\s+run\s+\S+\s+(dev|start|serve|preview)\b"),
    re.compile(r"\bpython[0-9.]*\s+.*\b(main|app|server|run)\b"),
    re.compile(r"\bmanage\.py\s+runserver\b"),
    re.compile(r"\b(uvicorn|gunicorn|flask\s+run|streamlit\s+run)\b"),
    re.compile(r"\bcargo\s+run\b"),
    re.compile(r"\bgo\s+run\b"),
    re.compile(r"\bdocker\s+(compose\s+up|run)\b"),
    re.compile(r"\./[A-Za-z0-9._/-]+"),
)


_BUILD_TIER_TOKENS = (
    "run build", "run tauri", "run sidecar", "run runtime", "run vector",
    "cargo build", "cargo test", "cargo tauri", "tauri build", "make ",
    "cmake --build", "gradle", "mvn ", "go build", "vite build", "tsc -b",
    "webpack", "next build", "ninja", "build:", "compile", "bundle",
)


def _is_build_tier_command(command: str) -> bool:
    """Plan 135 F2: True for a heavy build/compile step (cargo/tauri/make/gradle, a
    `npm run build:*`/`run tauri` script, etc.) whose timeout means 'needs more time,'
    not a code bug. Signal-keyed on the command, repo-agnostic."""
    c = " ".join((command or "").casefold().split())
    if any(t in c for t in _BUILD_TIER_TOKENS):
        return True
    return bool(re.search(r"\b(npm|pnpm|yarn|bun)\s+run\b", c)) and any(
        k in c for k in ("build", "tauri", "sidecar", "runtime", "vector", "compile", "bundle", "dist")
    )


def _looks_like_run_command(command: str) -> bool:
    if not command:
        return False
    # Plan 135 F1 (3a): a desktop run command (e.g. `npm run tauri dev`) IS a run
    # command — `_RUN_COMMAND_PATTERNS` needs the dev/start keyword right after
    # `npm run`, so `npm run tauri dev` (script named `tauri`, `dev` as its arg) was
    # MISSED and wrongly routed to the synchronous 30s path. Route it to the detached,
    # progress-aware path instead.
    if _looks_like_desktop_run_command(command):
        return True
    return any(p.search(command) for p in _RUN_COMMAND_PATTERNS)


# --- Plan 87: desktop-app (Tauri/Electron) GUI streaming -----------------------

_VNC_DISPLAY = ":99"
_VNC_WEB_PORT = 6080
_VNC_RFB_PORT = 5900


def _looks_like_desktop_run_command(command: str) -> str:
    """Plan 87: return the desktop flavor ('tauri'/'electron') a run command launches,
    or "" if it's not a desktop-app run. Used at execution time to route a desktop
    run through the headless-display + noVNC streaming path."""
    c = (command or "").lower()
    if "tauri" in c and ("tauri dev" in c or "npx tauri" in c or "run tauri" in c):
        return "tauri"
    if re.search(r"\belectron\b", c):
        return "electron"
    return ""


def _desktop_stream_packages(flavor: str) -> tuple[str, ...]:
    """Plan 87/88: the apt packages needed to run a GUI app headlessly and stream it.
    `xvfb` (virtual display) + `x11vnc`/`novnc`/`websockify` (stream), plus the
    FULL build + webview/GTK deps each desktop toolkit needs. Plan 88: for Tauri this
    is the complete Tauri-on-Linux prerequisite set — without `build-essential`/
    `libssl-dev`/`libwebkit2gtk`/`patchelf` the Rust backend can't compile (the
    `__TAURI_INTERNALS__ is undefined` symptom)."""
    # Plan 141 F1: a window manager is required for ANY GUI app (Tauri/Electron/Qt/GTK/
    # X11) to be visible on a bare Xvfb — without one, the top-level window is never
    # mapped/raised and the noVNC view is BLACK. `fluxbox` is tiny + fast. In the BASE
    # set so it's universal across desktop flavors, signal-keyed on "GUI on headless".
    base = ["xvfb", "fluxbox", "x11vnc", "novnc", "websockify"]
    if flavor == "tauri":
        base += [
            "build-essential", "curl", "wget", "file", "libssl-dev",
            "libwebkit2gtk-4.1-dev", "libgtk-3-dev",
            "libayatana-appindicator3-dev", "librsvg2-dev", "patchelf",
        ]
    elif flavor == "electron":
        base += ["libnss3", "libasound2", "libgbm1", "libgtk-3-0"]
    return tuple(base)


def _desktop_stream_install_command(flavor: str, *, present_tools=()) -> str | None:
    """Plan 87/88: the single bounded apt-get step that installs the streaming +
    build stack. Returns None when everything is already present (idempotent / lean).
    Plan 88: for Tauri, fall back to `libwebkit2gtk-4.0-dev` on older Ubuntu where the
    4.1 package is unavailable, so the install never hard-fails on one package name."""
    present = {str(t).lower() for t in (present_tools or ())}
    missing = [p for p in _desktop_stream_packages(flavor) if p.lower() not in present]
    if not missing:
        return None
    cmd = "sudo apt-get update && sudo apt-get install -y " + " ".join(missing)
    if flavor == "tauri" and "libwebkit2gtk-4.1-dev" in missing:
        # Older Ubuntu ships only the 4.0 webview headers — try 4.1, else 4.0.
        cmd += " || sudo apt-get install -y " + " ".join(
            "libwebkit2gtk-4.0-dev" if p == "libwebkit2gtk-4.1-dev" else p for p in missing
        )
    return cmd


def _tauri_toolchain_steps(*, present_tools=()) -> tuple[tuple[str, str, str], ...]:
    """Plan 88: install the Tauri RUNTIME toolchain a Tauri app needs to BUILD its
    Rust backend before `tauri dev` — the Rust toolchain (rustup) and the Tauri CLI.
    Idempotent (command-guarded); skipped when already present. Without this, the
    bundled backend never starts and the frontend shows `__TAURI_INTERNALS__ undefined`."""
    present = {str(t).lower() for t in (present_tools or ())}
    steps: list[tuple[str, str, str]] = []
    if "cargo" not in present and "rust" not in present and "rustup" not in present:
        steps.append((
            "Install the Rust toolchain (Tauri's backend is Rust)",
            "command -v cargo >/dev/null 2>&1 || "
            "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y",
            "S3",
        ))
    # Tauri CLI: a project that lists @tauri-apps/cli gets it from `npm install`; this
    # is a belt-and-suspenders fallback so `tauri` resolves either way. Idempotent.
    steps.append((
        "Ensure the Tauri CLI is available",
        ". \"$HOME/.cargo/env\" 2>/dev/null; "
        "command -v tauri >/dev/null 2>&1 || npx --yes @tauri-apps/cli --version >/dev/null 2>&1 || "
        "cargo install tauri-cli >/dev/null 2>&1 || true",
        "S1",
    ))
    return tuple(steps)


def _desktop_stream_steps(
    *, flavor: str, present_tools=()
) -> tuple[tuple[str, str, str], ...]:
    """Plan 87: ordered (title, command, safety_class) steps that, on a remote
    headless target, install the streaming stack, start the virtual display, and
    start the noVNC bridge. The app itself is launched (with DISPLAY exported)
    separately by `_launch_desktop_stream`. All start commands are idempotent."""
    steps: list[tuple[str, str, str]] = []
    install = _desktop_stream_install_command(flavor, present_tools=present_tools)
    if install:
        steps.append(("Install the virtual-display + noVNC + build stack", install, "S3"))
    # Plan 88: a Tauri app's backend is Rust — install the Rust toolchain + Tauri CLI
    # before the run step, or the bundled backend never starts.
    if flavor == "tauri":
        steps.extend(_tauri_toolchain_steps(present_tools=present_tools))
    steps.append((
        "Start the virtual display (Xvfb)",
        f"pgrep -f 'Xvfb {_VNC_DISPLAY}' >/dev/null 2>&1 || "
        f"(mkdir -p ~/.duckln/logs; nohup Xvfb {_VNC_DISPLAY} -screen 0 1280x800x24 "
        f">~/.duckln/logs/xvfb.log 2>&1 &)",
        "S1",
    ))
    # Plan 141 F1: start a window manager on the virtual display so the GUI app's window
    # is mapped/raised and actually paints (no WM → black noVNC screen). Universal for any
    # desktop toolkit; idempotent. `sleep 1` lets Xvfb come up before fluxbox attaches.
    steps.append((
        "Start a window manager on the virtual display (so the app window is visible)",
        f"pgrep -f 'fluxbox' >/dev/null 2>&1 || "
        f"(mkdir -p ~/.duckln/logs; sleep 1; DISPLAY={_VNC_DISPLAY} nohup fluxbox "
        f">~/.duckln/logs/fluxbox.log 2>&1 &)",
        "S1",
    ))
    steps.append((
        "Stream the app window to your browser (x11vnc + noVNC)",
        f"pgrep -f 'x11vnc.*{_VNC_DISPLAY}' >/dev/null 2>&1 || "
        f"(nohup x11vnc -display {_VNC_DISPLAY} -forever -shared -nopw -rfbport {_VNC_RFB_PORT} "
        f">~/.duckln/logs/x11vnc.log 2>&1 &); "
        f"pgrep -f 'websockify.*{_VNC_WEB_PORT}' >/dev/null 2>&1 || "
        f"(nohup websockify --web=/usr/share/novnc {_VNC_WEB_PORT} localhost:{_VNC_RFB_PORT} "
        f">~/.duckln/logs/novnc.log 2>&1 &)",
        "S1",
    ))
    return tuple(steps)


_LOW_RAM_KB_THRESHOLD = 6 * 1024 * 1024  # ~6 GB — below this, a Tauri/Rust build OOMs


def _low_memory_swap_command() -> str:
    """Plan 89: add 4G of swap if none is active and pin cargo to a single build job,
    so a heavy Rust build (Tauri) fits in RAM on a small VM. Idempotent."""
    return (
        "if ! sudo swapon --show 2>/dev/null | grep -q .; then "
        "sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile && "
        "sudo mkswap /swapfile && sudo swapon /swapfile; fi; "
        "mkdir -p ~/.cargo && printf '[build]\\njobs = 1\\n' > ~/.cargo/config.toml"
    )


# Native/compiler toolchains that can exhaust RAM (heavy regardless of context).
_COMPILER_TOKENS = (
    "cargo", "rustc", "tauri", "cmake", "make ", "gcc", "g++", "clang", "node-gyp",
    "gradle", "mvn ", "ninja", "bazel", "go build",
)
# Bundler BUILDS (not dev servers) that can be memory-heavy.
_HEAVY_BUNDLE_TOKENS = ("webpack", "vite build", "next build", "rollup", "esbuild")


def _is_heavy_build(command: str) -> bool:
    """Plan 110: True when a command runs a heavy native/compiler or bundler BUILD that
    can exhaust RAM on a small target (Rust/C++/Java/large-JS). Repo-agnostic — keyed on
    the build command, not the repo. Light dev servers (`npm run dev`, `vite`) are False
    unless they invoke a compiler toolchain (e.g. `tauri dev`)."""
    c = (command or "").lower()
    if any(t in c for t in _COMPILER_TOKENS):
        return True
    if any(t in c for t in _HEAVY_BUNDLE_TOKENS):
        return True
    return False


def _needs_low_memory_guard(*, mem_kb: int, has_swap: bool) -> bool:
    """Plan 89: True when the target has little RAM and no swap — a Tauri/Rust build
    would be OOM-killed without help."""
    return 0 < mem_kb < _LOW_RAM_KB_THRESHOLD and not has_swap


def _idempotent_guard(command: str) -> str:
    """Plan 89: prepend a cheap guard so re-running a plan skips already-finished work
    (the user's 'don't redo from scratch'). Only wraps recognized, safe-to-skip
    commands; everything else is returned unchanged. Idempotent if already guarded."""
    c = (command or "").strip()
    if not c or "||" in c or c.startswith("[") or c.startswith("if "):
        return command
    low = c.lower()
    # Dependency install → Plan 127: skip ONLY when the install genuinely COMPLETED
    # (a package-manager completion marker exists). A half-installed `node_modules`
    # (OOM-killed/interrupted) has the dir but no marker → clean it and reinstall, so
    # a partial install is healed rather than skipped-broken or piled onto.
    if re.search(r"\b(npm (ci|install|i)|pnpm (install|i)|yarn( install)?|bun install)\b", low):
        marker = (
            "[ -e node_modules/.package-lock.json ] || [ -e node_modules/.pnpm ] || "
            "[ -e node_modules/.yarn-integrity ] || [ -e node_modules/.modules.yaml ]"
        )
        return f"if [ -d node_modules ] && {{ {marker}; }}; then echo duckln-deps-present; else rm -rf node_modules && {c}; fi"
    # Copy the example env → skip when .env already exists.
    m = re.match(r"cp\s+\.env\.(example|sample)\s+\.env\s*$", c)
    if m:
        return f"[ -f .env ] || {c}"
    # Install a single tool via apt → skip when it's already on PATH.
    m = re.search(r"apt(?:-get)?\s+install\s+-y\s+([a-z0-9.+-]+)\s*$", low)
    if m:
        tool = m.group(1)
        bin_name = {"nodejs": "node", "build-essential": "gcc"}.get(tool, tool)
        return f"command -v {bin_name} >/dev/null 2>&1 || {c}"
    return command


def _target_is_headless(runner, *, config_dir: Path, execution_target: str, vm_name: str | None) -> bool:
    """Plan 89: True when the target has no usable display (Linux + empty $DISPLAY) —
    so a desktop GUI app needs the Xvfb + noVNC streaming path even if the session is
    labelled 'local' (e.g. Duckln driving a headless multipass VM). Best-effort; on
    any failure assume NOT headless (caller keeps the native-window path)."""
    probe = 'echo "DUCKLN_OS:$(uname -s)"; echo "DUCKLN_DISP:${DISPLAY:-}"'
    wrapped, _meta = _wrap_command_for_execution_target(
        config_dir=config_dir, execution_target=execution_target,
        command=probe, cwd=None, preferred_vm_name=vm_name,
    )
    if wrapped is None:
        return False
    try:
        res = runner.run(wrapped)
    except Exception:
        return False
    out = f"{getattr(res, 'stdout', '') or ''}\n{getattr(res, 'stderr', '') or ''}"
    is_linux = "DUCKLN_OS:Linux" in out
    m = re.search(r"DUCKLN_DISP:(.*)", out)
    has_display = bool(m and m.group(1).strip())
    return is_linux and not has_display


def _novnc_url(*, execution_target: str, vm_name: str | None) -> str:
    """Plan 87: the host-reachable noVNC URL that shows the streamed app window.
    Plan 139: AUTO-CONNECT + scale so the link lands DIRECTLY on the app, not noVNC's
    "Connect" landing page. x11vnc runs `-nopw`, so no credentials are needed.
    `autoconnect` skips the dialog, `resize=scale` fits the app to the window, `reconnect`
    silently re-attaches if the socket drops (the first `tauri dev` compile can bounce it)."""
    base = f"http://localhost:{_VNC_WEB_PORT}/vnc.html"
    url = _host_reachable_url(base, execution_target=execution_target, vm_name=vm_name)
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}autoconnect=true&resize=scale&reconnect=true"


def _launch_desktop_stream(
    *,
    runner,
    raw_command: str,
    flavor: str,
    project_cwd: str | None,
    execution_target: str,
    vm_name: str | None,
    config_dir: Path,
    repo,
    present_tools=(),
    display: Callable[[str], None],
    think: Callable[[str], None],
    ready_window_seconds: float | None = None,
):
    """Plan 87/88: run a desktop (Tauri/Electron) app on a HEADLESS remote target and
    stream its window to the user's browser. Provisions Xvfb + noVNC + build toolchain
    (idempotent), launches the real native app against DISPLAY=:99, and returns
    (status, novnc_url) where status ∈ {"ready","dead","unconfirmed"}."""
    think("Executor → this is a desktop app on a headless target — starting a virtual display and a browser stream (noVNC).")
    # 1) Provision + start the streaming stack (install → toolchain → Xvfb → noVNC).
    for title, command, _safety in _desktop_stream_steps(flavor=flavor, present_tools=present_tools):
        wrapped, _meta = _wrap_command_for_execution_target(
            config_dir=config_dir, execution_target=execution_target,
            command=command, cwd=project_cwd, preferred_vm_name=vm_name,
        )
        if wrapped is None:
            return "dead", None, ""
        display(f"   · {title}")
        try:
            runner.run(wrapped)
        except Exception:
            return "dead", None, ""
    # 2) Launch the real desktop app against the virtual display, detached + polled.
    # Plan 88: a Tauri webview needs the WEBKIT_DISABLE_* flags to render under Xvfb
    # (no GPU/dmabuf), and `cargo` on PATH so the Rust backend can build. The first
    # build can take minutes, so the readiness window is longer for Tauri.
    display_cmd = _desktop_launch_command(raw_command, flavor)
    if ready_window_seconds is None:
        ready_window_seconds = 300.0 if flavor == "tauri" else 60.0
    if flavor == "tauri":
        think("Executor → building the Rust backend — the first `tauri dev` compile can take a few minutes…")
    status, _served, log_tail = _launch_and_await_server(
        runner=runner, raw_command=display_cmd, project_cwd=project_cwd,
        execution_target=execution_target, vm_name=vm_name, config_dir=config_dir,
        repo=repo, display=display, think=think, ready_window_seconds=ready_window_seconds,
    )
    if status == "dead":
        return "dead", None, log_tail
    # Never surface the frontend (Vite) URL for a desktop app — only the noVNC stream.
    return status, _novnc_url(execution_target=execution_target, vm_name=vm_name), log_tail


def _desktop_launch_command(raw_command: str, flavor: str, *, headless: bool = True) -> str:
    """Plan 88/89/138: prefix a desktop run command with the right env. Plan 138 F1: the
    env MUST be applied via `env VAR=val … <cmd>` (NOT a bare `VAR=val …` prefix) — because
    the detached launcher wraps this as `nohup <this>`, and `nohup VAR=val cmd` makes nohup
    try to EXEC a program literally named `VAR=val` → "No such file or directory" / exit
    127. `nohup env VAR=val cmd` runs correctly and `$!` still captures the pid. `$HOME`/
    `$PATH` are expanded by the surrounding `bash -lc` before `env` runs. Cargo is put on
    PATH via rustup's bin dir; headless adds DISPLAY=:99 + the WEBKIT_DISABLE_* flags so the
    Tauri webview renders under Xvfb. Still a SINGLE simple command (no `;`/`.`)."""
    if flavor == "tauri":
        cargo = "PATH=\"$HOME/.cargo/bin:$PATH\" "
        if headless:
            return (
                f"env {cargo}DISPLAY={_VNC_DISPLAY} WEBKIT_DISABLE_COMPOSITING_MODE=1 "
                f"WEBKIT_DISABLE_DMABUF_RENDERER=1 {raw_command}"
            )
        return f"env {cargo}{raw_command}"
    return f"env DISPLAY={_VNC_DISPLAY} {raw_command}" if headless else raw_command


_TOOL_NAME_ALIASES = {"nodejs": "node", "node.js": "node", "golang": "go", "python3": "python", "pip3": "python", "pip": "python", "cargo": "rust"}


def _verify_step_referenced_tools(purpose: str) -> set[str]:
    """Plan 83 Fix 1: the runtime tools a 'Verify README prerequisite: …' step checks,
    so it can be dropped when all of them are already satisfied."""
    low = (purpose or "").lower()
    tools: set[str] = set()
    if "node" in low:
        tools.add("node")
    if "npm" in low:
        tools.add("npm")
    if "python" in low or "pip" in low:
        tools.add("python")
    if "docker" in low:
        tools.add("docker")
    if "go" in low or "golang" in low:
        tools.add("go")
    if "rust" in low or "cargo" in low:
        tools.add("rust")
    return tools


def _drop_satisfied_prereqs(steps: tuple, satisfied_tools: set[str]) -> list:
    """Plan 83 Fix 1: drop BOTH 'Ensure X is installed' AND 'Verify README
    prerequisite: …' steps when every tool they concern is already satisfied (present
    on the target, or being installed/managed by a version step). Generalises the
    Plan 76 present-tools drop so a lean plan never re-checks satisfied prerequisites."""
    if not satisfied_tools:
        return list(steps)
    kept = []
    for s in steps:
        purpose = s.purpose or ""
        # "Ensure <tool> … is installed"
        m = _PREREQ_PURPOSE_RE.search(purpose)
        if m:
            tool = m.group(1).strip().lower()
            tool_norm = _TOOL_NAME_ALIASES.get(tool, tool)
            if tool_norm in satisfied_tools or tool in satisfied_tools:
                continue
        # "Verify README prerequisite: <names>" — drop only when ALL referenced
        # runtimes are satisfied (a genuinely-missing tool still surfaces).
        if "verify readme prerequisite" in purpose.lower():
            refs = _verify_step_referenced_tools(purpose)
            if refs and refs <= satisfied_tools:
                continue
        kept.append(s)
    return kept


# Back-compat alias (Plan 76 name) — same behaviour, satisfied == present.
def _assemble_plansteps_with_clone_and_run(
    *,
    PlanStep,
    repo: RepoCatalogRecord,
    runtime_dir: str,
    bringup_plan,
    inspection,
    skip_clone: bool = False,
    execution_target: str = "local",
    config_dir: Path | None = None,
    readme_excerpt: str = "",
    preferred_run_command: str | None = None,
    project_subdir: str = "",
    compose_services: tuple[str, ...] = (),
    codegen_steps: tuple[tuple[str, str, str | None], ...] = (),
    docker_available: bool = True,
):
    """Build the ordered PlanStep list: clone (first, if absent) → the
    specialist's install/build steps → optional service provisioning + codegen →
    a run/start step (last, if absent). Indices are provisional;
    ``finalize_plan_from_steps`` renumbers 1..N. ``skip_clone`` omits the clone
    step when a pre-check found the repo present.
    Plan 78 Fix B: every step carries target + cwd + source evidence.
    Plan 81: ``project_subdir`` (monorepo app dir) sets the cwd for env/codegen/run;
    ``compose_services`` adds a `docker compose up -d` step; ``codegen_steps`` add
    prisma/graphql/build steps before run."""
    steps: list = []
    bringup_steps = list(bringup_plan.steps)
    has_clone = any("git clone" in (s.command or "") for s in bringup_steps)
    has_run = any(_looks_like_run_command(s.command or "") for s in bringup_steps)
    # Plan 81 Fix 2: in a monorepo, env/codegen/run happen INSIDE the app subdir;
    # clone + workspace install stay at the repo root.
    project_cwd = f"{runtime_dir}/{project_subdir}" if project_subdir else runtime_dir

    if not has_clone and not skip_clone and repo.repo_url:
        steps.append(
            PlanStep(
                index=1,
                title=f"Clone {repo.name}",
                description="Fetch the repository into the managed workspace before any setup.",
                # Plan 85 Fix 1: idempotent — if the repo is already cloned, skip rather
                # than fail (git clone into a non-empty dir exits 128). Quote-safe.
                command=f"if [ -d {runtime_dir}/.git ]; then echo duckln-already-cloned; else git clone --depth 1 {repo.repo_url} {runtime_dir}; fi",
                safety_class="S0",
                verification=f"test -d {runtime_dir}/.git",
                rationale="The repo must be present locally/in the target before dependencies can be installed.",
                estimated_seconds=20,
                confidence=1.0,
                origin="planner",
                target=execution_target,
                source="duckln-deterministic",
                cwd="",  # clone runs from the parent (the dir does not exist yet)
            )
        )

    for s in bringup_steps:
        step_source = getattr(s, "source", "") or "duckln-deterministic"
        # Plan 79 L5: when a step traces to the README, attach the exact (redacted)
        # README line it came from as evidence, so the reviewer can verify it.
        evidence = ""
        if "readme" in step_source.lower():
            evidence = _evidence_line_for(s.command, readme_excerpt)
        steps.append(
            PlanStep(
                index=len(steps) + 1,
                title=s.purpose,
                description="",
                command=s.command,
                safety_class="S0",  # reclassified in Stage 4
                verification=s.verification_command,
                rationale="Proposed by the repo setup specialist (playbook-validated).",
                estimated_seconds=30,
                confidence=1.0,
                origin="planner",
                target=execution_target,
                source=step_source,
                evidence_excerpt=evidence,
                cwd=runtime_dir,
            )
        )

    # Plan 79 L2 / Plan 81 Fix 1: many repos ship a `.env.example` and won't start
    # without a `.env`. Copy it BEFORE running; name the required secret keys so the
    # user knows what to fill in.
    env_example = _env_example_file(getattr(inspection, "detected_files", ()) or ())
    already_copies_env = any(".env" in (s.command or "") for s in steps)
    if env_example and not already_copies_env:
        required = ()
        if config_dir is not None:
            required = _required_env_vars(
                config_dir=config_dir, repo=repo,
                detected_files=getattr(inspection, "detected_files", ()) or (),
            )
        fill_note = (
            f" After copying, fill in real values for: {', '.join(required)}." if required else ""
        )
        # Plan 146 B1: don't just copy — POPULATE safe dev defaults for known keys (DB/cache
        # URLs → the provisioned service, random dev secrets) so the app boots; external paid
        # keys (no safe default) are left for the user (an honest ask via fill_note).
        _env_defaults = _populate_env_defaults_command(tuple(required))
        _env_command = f"cp {env_example} .env" + (f"; {_env_defaults}" if _env_defaults else "")
        steps.append(
            PlanStep(
                index=len(steps) + 1,
                title="Create the environment file",
                description=f"Copy {env_example} to .env and fill safe dev defaults so the app's required environment variables exist.{fill_note}",
                command=_env_command,
                safety_class="S0",  # reclassified in Stage 4
                verification="test -f .env",
                rationale=(
                    f"The repo ships {env_example}; the app needs a .env to run."
                    + (f" You must fill: {', '.join(required)}." if required else "")
                ),
                estimated_seconds=5,
                confidence=0.9,
                origin="planner",
                target=execution_target,
                source=env_example,
                evidence_excerpt=(", ".join(required) if required else ""),
                cwd=project_cwd,
            )
        )

    # Plan 81 Fix 3: start backing services (postgres/redis…) BEFORE the app, when
    # the repo declares them in docker-compose AND docker is available on the target.
    if compose_services and docker_available:
        svc = " ".join(compose_services)
        steps.append(
            PlanStep(
                index=len(steps) + 1,
                title="Start backing services",
                description=f"Start the repo's docker-compose services ({svc}) the app depends on.",
                command=f"docker compose up -d {svc}".strip(),
                safety_class="S2",
                verification="docker compose ps",
                rationale="The app connects to these services at runtime; they must be up first.",
                estimated_seconds=30,
                confidence=0.85,
                origin="planner",
                target=execution_target,
                source="docker-compose",
                cwd=runtime_dir,
            )
        )

    # Plan 81 Fix 4: codegen / pre-build steps (prisma generate, graphql codegen,
    # build-before-start) run AFTER install and BEFORE the run step.
    for cg_title, cg_cmd, cg_verify in codegen_steps:
        steps.append(
            PlanStep(
                index=len(steps) + 1,
                title=cg_title,
                description="Generate code / build artifacts the app needs before it can run.",
                command=cg_cmd,
                safety_class="S1",
                verification=cg_verify,
                rationale="The repo requires this generation/build step before the app will start.",
                estimated_seconds=30,
                confidence=0.85,
                origin="planner",
                target=execution_target,
                source="package.json",
                cwd=project_cwd,
            )
        )

    if not has_run:
        # Plan 79 L8: prefer the run command that worked for this repo before.
        run_command = preferred_run_command or _infer_run_command(inspection, repo=repo, config_dir=config_dir)
        # Plan 155 F2: a conda repo must RUN through its env (`conda run -n <env>`), not the
        # pip-venv path — otherwise a pure-conda repo (no requirements.txt) starts with the
        # wrong interpreter and fails. The Plan-153 step created the env; now we use it.
        if run_command:
            _conda_prefix = _conda_run_prefix_for_repo(
                config_dir=config_dir, repo=repo,
                detected_files=getattr(inspection, "detected_files", ()) or (),
            )
            if _conda_prefix and not run_command.strip().startswith("conda run"):
                run_command = _conda_prefix + run_command
        if run_command:
            steps.append(
                PlanStep(
                    index=len(steps) + 1,
                    title=f"Run {repo.name}",
                    description="Start the application so the repo is actually running.",
                    command=run_command,
                    safety_class="S0",
                    verification=_verification_for_run_command(run_command),
                    rationale="The objective is a running repo — this is the final step that starts it.",
                    estimated_seconds=15,
                    confidence=0.8,
                    origin="planner",
                    target=execution_target,
                    source="repo-knowledge",
                    cwd=project_cwd,
                )
            )
    return steps


def _evidence_line_for(command: str | None, readme_excerpt: str) -> str:
    """Plan 79 L5: find the README line a command came from (redacted, trimmed),
    so a reviewer can confirm the step is grounded in the repo's own docs."""
    cmd = (command or "").strip()
    if not cmd or not readme_excerpt:
        return ""
    cleaned = [
        (raw.strip().lstrip("$#> ").strip(), raw)
        for raw in readme_excerpt.splitlines()
        if raw.strip()
    ]
    # 1. Prefer an exact full-command match.
    for line, _raw in cleaned:
        if cmd in line:
            return redact_sensitive_data(line[:120])
    # 2. Fall back to the most distinctive token (the longest word).
    tokens = [t for t in re.split(r"\s+", cmd) if len(t) >= 4]
    needle = max(tokens, key=len) if tokens else ""
    if needle:
        for line, _raw in cleaned:
            if needle in line:
                return redact_sensitive_data(line[:120])
    return ""


_ENV_EXAMPLE_NAMES = (".env.example", ".env.sample", ".env.template", "env.example", ".env.dist")


def _env_example_file(detected_files) -> str | None:
    """Plan 79 L2: the env-template filename a repo ships, if any."""
    lowered = {str(f).lower(): str(f) for f in (detected_files or ())}
    for name in _ENV_EXAMPLE_NAMES:
        if name in lowered:
            return lowered[name]
    return None


_ENV_PLACEHOLDER_HINTS = ("changeme", "change_me", "your_", "your-", "yourkey", "xxx", "<", "replace", "todo", "placeholder", "example", "dummy")


def _required_env_vars(*, config_dir: Path, repo: RepoCatalogRecord, detected_files) -> tuple[str, ...]:
    """Plan 81 Fix 1: the env keys a repo ships with EMPTY or placeholder values —
    i.e. the ones a human likely must fill before the app truly runs."""
    env_file = _env_example_file(detected_files)
    if not env_file:
        return ()
    text = _read_repo_text_file(config_dir, repo, env_file)
    if not text:
        return ()
    required: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().lstrip("export ").strip()
        value = value.strip().strip("'\"")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        looks_placeholder = (not value) or any(h in value.lower() for h in _ENV_PLACEHOLDER_HINTS)
        looks_secret = any(t in key.upper() for t in ("KEY", "SECRET", "TOKEN", "PASSWORD", "DATABASE_URL", "DSN", "CREDENTIAL", "API"))
        if looks_placeholder and looks_secret:
            required.append(key)
    return tuple(dict.fromkeys(required))


# Plan 83 Fix 4: AI-provider key names that are ALTERNATIVES — a repo lists several
# but the user only needs the one for the provider they'll use.
_AI_PROVIDER_KEYS = {
    "OPENAI_API_KEY": "OpenAI", "ANTHROPIC_API_KEY": "Anthropic", "GROQ_API_KEY": "Groq",
    "DEEPSEEK_API_KEY": "DeepSeek", "NVIDIA_API_KEY": "NVIDIA", "GEMINI_API_KEY": "Gemini",
    "GOOGLE_API_KEY": "Google", "MISTRAL_API_KEY": "Mistral", "COHERE_API_KEY": "Cohere",
    "TOGETHER_API_KEY": "Together", "OPENROUTER_API_KEY": "OpenRouter", "XAI_API_KEY": "xAI",
    "PERPLEXITY_API_KEY": "Perplexity", "FIREWORKS_API_KEY": "Fireworks",
}


def _classify_required_env(keys) -> tuple[dict[str, str], tuple[str, ...]]:
    """Plan 83 Fix 4: split required env keys into (alternative AI-provider keys
    {KEY: provider-label}) and (individually-required keys). Repo-driven, name-based,
    ecosystem-agnostic — lets Duckln ask 'which provider?' only when it makes sense."""
    alts: dict[str, str] = {}
    individual: list[str] = []
    for key in keys:
        upper = str(key).upper()
        if upper in _AI_PROVIDER_KEYS:
            alts[upper] = _AI_PROVIDER_KEYS[upper]
        else:
            individual.append(str(key))
    # A lone AI key isn't really an "alternative" — treat it as individually required.
    if len(alts) < 2:
        individual.extend(alts.keys())
        alts = {}
    return alts, tuple(dict.fromkeys(individual))


def offer_env_key_setup(
    *,
    plan,
    paths: ConfigPaths,
    execution_target: str,
    vm_name: str | None,
    display: Callable[[str], None],
    text_prompt: Callable[[str, str], str | None] | None,
    select_prompt: Callable[[str, tuple[str, ...]], str | None] | None,
) -> None:
    """Plan 83 Fix 4: after a successful setup, intelligently help the user set the
    required API keys — ask which provider (only when the keys are alternatives),
    prompt for each needed key, and write it into the project's `.env` on the target.
    The secret VALUE is redacted from every log/thought; only 'set KEY' is shown.
    Repo-driven and optional (Enter skips a key); no-op when no keys are required."""
    keys = tuple(getattr(plan, "required_env_keys", ()) or ())
    if not keys or text_prompt is None:
        return
    alts, individual = _classify_required_env(keys)
    to_set: list[str] = list(individual)
    if alts and select_prompt is not None:
        labels = tuple(alts.values()) + ("Skip — I'll set it later",)
        choice = select_prompt("This repo supports several AI providers — which will you use?", labels)
        if choice and not choice.lower().startswith("skip"):
            for k, label in alts.items():
                if label == choice:
                    to_set.insert(0, k)
                    break
    elif alts:
        # No select prompt available — fall back to offering all alternatives.
        to_set.extend(alts.keys())
    if not to_set:
        return
    runtime_dir = resolve_runtime_project_dir(paths.config_dir, repo=_repo_from_plan(plan), execution_target=execution_target) if _repo_from_plan(plan) else None
    project_dir = runtime_dir or "."
    for key in to_set:
        value = text_prompt(f"Paste {key} for .env (press Enter to skip):", "")
        if not value or not value.strip():
            display(f"Skipped {key} — set it in .env when you're ready.")
            continue
        value = value.strip()
        # Update-or-append the line in .env; the value is shell-single-quoted.
        safe_val = value.replace("'", "'\\''")
        write_cmd = (
            f"touch .env; "
            f"if grep -q '^{key}=' .env; then "
            f"sed -i.bak \"s|^{key}=.*|{key}='{safe_val}'|\" .env && rm -f .env.bak; "
            f"else printf \"{key}='%s'\\n\" '{safe_val}' >> .env; fi"
        )
        try:
            wrapped, _meta = _wrap_command_for_execution_target(
                config_dir=paths.config_dir, execution_target=execution_target,
                command=write_cmd, cwd=project_dir, preferred_vm_name=vm_name,
            )
            if wrapped is None:
                display(f"Could not resolve the target to write {key}; set it in .env manually.")
                continue
            runner = ControlledCommandRunner(trace=lambda _m: None, execution_target=execution_target, vm_name=vm_name)
            runner.run(wrapped)
            display(f"✓ Set {key} in .env (value hidden).")
        except Exception:
            display(f"Could not write {key} automatically; set it in .env manually.")


def _repo_from_plan(plan):
    """Best-effort RepoCatalogRecord from a plan's repo_slug (for path resolution)."""
    slug = getattr(plan, "repo_slug", None)
    if not slug:
        return None
    name = str(slug).rstrip("/").split("/")[-1] or "repo"
    return RepoCatalogRecord(name, str(slug), 0, "", "", "", "")


_WEB_SERVER_RUN_HINTS = (
    "dev", "serve", "preview", "start", "uvicorn", "gunicorn", "flask run",
    "streamlit", "runserver", "next", "vite", "http.server", "rails server",
)


_SERVED_URL_RE = re.compile(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::\]|\[::1\])(?::\d+)?(?:/\S*)?", re.IGNORECASE)
_SERVED_PORT_RE = re.compile(r"(?:listening|running|started|serving|local|port).{0,30}?:\s*(\d{2,5})\b", re.IGNORECASE)


def _vm_ipv4(vm_name: str | None) -> str | None:
    """Best-effort VM IPv4 from `multipass info` so a served localhost URL can be
    rewritten to something reachable from the host. Returns None on any failure."""
    if not vm_name:
        return None
    try:
        runner = ControlledCommandRunner(trace=lambda _m: None, execution_target="local")
        res = runner.run(f"multipass info {shlex.quote(vm_name)} --format json")
        if getattr(res, "exit_code", 1) != 0:
            return None
        payload = json.loads(getattr(res, "stdout", "") or "{}")
        ipv4 = payload.get("info", {}).get(vm_name, {}).get("ipv4") or []
        return str(ipv4[0]) if ipv4 else None
    except Exception:
        return None


def _port_from_url(url: str) -> str:
    """Extract the port from a served URL (defaults to 80/443 by scheme)."""
    m = re.search(r":(\d{2,5})\b", url or "")
    if m:
        return m.group(1)
    return "443" if (url or "").startswith("https") else "80"


def _vm_port_reachable(url: str, *, runner: ControlledCommandRunner | None = None) -> bool:
    """Plan 81 Fix 6: check from the HOST whether a VM-served URL actually responds
    (multipass NAT/firewall can block it). Best-effort; False on any failure."""
    if not url:
        return False
    try:
        r = runner or ControlledCommandRunner(trace=lambda _m: None, execution_target="local")
        res = r.run(f"curl -sf -o /dev/null --max-time 3 {shlex.quote(url)}")
        return getattr(res, "exit_code", 1) == 0 and not getattr(res, "timed_out", False)
    except Exception:
        return False


def _extract_served_url(
    output: str,
    *,
    execution_target: str = "local",
    config_dir: Path | None = None,
    vm_name: str | None = None,
) -> str | None:
    """Plan 79 L4: pull the local URL a started server prints, and (on a VM) rewrite
    localhost → the VM IP so the link is reachable from the host. Returns None when
    no URL/port is found."""
    text = output or ""
    match = _SERVED_URL_RE.search(text)
    url = match.group(0).rstrip(".,)") if match else None
    if url is None:
        port_match = _SERVED_PORT_RE.search(text)
        if port_match:
            url = f"http://localhost:{port_match.group(1)}"
    if url is None:
        return None
    if execution_target in _REMOTE_EXECUTION_TARGETS:
        ip = _vm_ipv4(vm_name)
        if ip:
            url = re.sub(r"localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1?\]", ip, url)
    return url


def _verification_for_run_command(command: str) -> str:
    """Plan 79 L6: a concrete verification for a run step. Web/dev servers verify
    by the printed local URL responding; other long-running processes verify by
    staying alive."""
    c = (command or "").lower()
    if any(h in c for h in _WEB_SERVER_RUN_HINTS):
        return "the server stays up and its printed http://localhost:<port> URL returns a response"
    return "the process starts and stays alive without an immediate error"


# Plan 86 Fix 2: signals that a started server is UP (so a run step that never exits
# is a SUCCESS, not a timeout failure).
_SERVER_READY_HINTS = (
    "ready in", "ready -", "listening", "compiled successfully", "compiled client",
    "server running", "running at", "running on", "local:", "started server",
    "now listening", "watching for file changes", "dev server running",
    "serving at", "available on", "accepting connections", "started on",
    # Plan 98: cover more server banners (python http.server, generic).
    "serving http on", "serving on", "listening on", "http://", "https://",
    "uvicorn running", "application startup complete",
)


def _looks_server_ready(output: str) -> bool:
    """Plan 86 Fix 2: True when server output indicates it has come up."""
    low = (output or "").lower()
    return any(h in low for h in _SERVER_READY_HINTS)


_BUILD_PROGRESS_MARKERS = (
    "compiling ", "building [", "checking ", "downloading ", "fetching ",
    "installing ", "linking ", "bundling ", "transpiling ", "generating ",
    " building ", "compiled ", "[==", "downloaded ",
)


def _looks_build_progressing(output: str) -> bool:
    """Plan 135 F1 (3b): True when the run log shows an ACTIVE build/compile in flight
    (cargo `Compiling …`, `Building [==>] N/M`, npm/webpack progress, etc.) — used to
    keep the readiness wait alive while a healthy build is still working, instead of
    abandoning it on a fixed timer."""
    low = (output or "").lower()
    return any(m in low for m in _BUILD_PROGRESS_MARKERS)


# Plan 86 Fix 5: per-tool flag to bind a dev server to ALL interfaces, so a headless
# VM/container/cloud server is reachable from the user's machine (not localhost-only).
def _ensure_host_bind(command: str, *, execution_target: str, underlying: str = "") -> str:
    """Inject a 0.0.0.0 host bind into a known dev-server run command on REMOTE targets
    when it's absent, so a headless VM/container server is reachable from the host.
    `underlying` is the resolved package.json script body when `command` is an opaque
    `npm run <x>` — used to pick the correct per-tool flag. Unknown tools / local
    target → returned unchanged."""
    if execution_target not in _REMOTE_EXECUTION_TARGETS:
        return command
    c = command
    low = c.lower()
    if "0.0.0.0" in low or "--host" in low or "-h 0.0.0.0" in low:
        return c
    # Resolve the real tool from the underlying script (for `npm run dev`) or the
    # command itself (for direct `vite`/`uvicorn`/… commands).
    probe = (underlying or "") + " " + low
    is_pm_run = c.split()[0] in ("npm", "pnpm", "yarn", "bun") if c.split() else False

    def _append_dashdash(flag: str) -> str:
        # `npm/pnpm/yarn run dev -- <flag>` passes the flag to the underlying tool.
        return f"{c} -- {flag}" if is_pm_run else f"{c} {flag}"

    if "next" in probe:                              # Next.js → -H (rejects --host)
        return _append_dashdash("-H 0.0.0.0")
    if "react-scripts" in probe:                     # CRA → HOST env
        return f"HOST=0.0.0.0 {c}"
    if any(t in probe for t in ("vite", "vue-cli-service", "webpack", "astro", "nuxt", "svelte")):
        return _append_dashdash("--host 0.0.0.0")
    if "flask" in probe:
        return f"{c} --host=0.0.0.0"
    if "uvicorn" in probe:
        return f"{c} --host 0.0.0.0"
    if "gunicorn" in probe:
        return f"{c} -b 0.0.0.0"
    if "rails" in probe and "server" in probe:
        return f"{c} -b 0.0.0.0"
    if "manage.py" in probe and "runserver" in probe:
        return f"{c} 0.0.0.0:8000"
    # Opaque `npm run dev/serve/preview` with no resolvable tool → vite is the most
    # common dev server and accepts `--host`; safe default for the broad majority.
    if is_pm_run and re.search(r"\brun\s+(dev|serve|preview|develop)\b", low):
        return _append_dashdash("--host 0.0.0.0")
    return c


def _host_reachable_url(served_url: str, *, execution_target: str, vm_name: str | None) -> str:
    """Plan 86 Fix 5: rewrite a target-localhost URL to one the HOST can open."""
    if not served_url:
        return served_url
    if execution_target == "vm":
        ip = _vm_ipv4(vm_name)
        if ip:
            return re.sub(r"localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1?\]", ip, served_url)
    # local / docker (published port) / cloud-with-public-ip → as-is (best effort).
    return served_url


def _container_published_url(served_url: str, container_name: str | None) -> str | None:
    """Plan 98: map a container's internal served URL to the HOST-published URL via
    `docker port <name> <internal>`. Returns the host URL, or None when the port isn't
    published (caller advises re-running with `-p`)."""
    if not container_name:
        return None
    port = _port_from_url(served_url)
    if not port:
        return None
    try:
        from duckln.shell import ControlledCommandRunner

        res = ControlledCommandRunner(execution_target="local").run(
            f"docker port {shlex.quote(container_name)} {port}", timeout_seconds=15.0
        )
        out = (getattr(res, "stdout", "") or "").strip().splitlines()
        if out:
            mapped = out[0].strip()  # e.g. "0.0.0.0:32768"
            host_port = mapped.rsplit(":", 1)[-1]
            if host_port.isdigit():
                return f"http://localhost:{host_port}/"
    except Exception:
        return None
    return None


def _start_cloud_tunnel_url(served_url: str, *, config_dir: Path, resource_key: str | None) -> str | None:
    """Plan 98: open an `ssh -L <port>:localhost:<port>` tunnel to the cloud instance
    (no firewall mutation needed) and return the local URL. Best-effort; None on failure."""
    port = _port_from_url(served_url)
    if not port:
        return None
    try:
        record = resolve_managed_resource(config_dir, resource_key=resource_key) if resource_key else None
        if record is None:
            return None
        tunnel = build_cloud_tunnel_command(record, local_port=int(port), remote_port=int(port))
        if not tunnel:
            return None
        from duckln.shell import ControlledCommandRunner

        # Launch the tunnel detached; it stays up for the session.
        ControlledCommandRunner(execution_target="local").run(
            f"nohup {tunnel} >/dev/null 2>&1 & echo DUCKLN_TUNNEL:$!", timeout_seconds=15.0
        )
        return f"http://localhost:{port}/"
    except Exception:
        return None


def expose_app_and_open(
    served_url: str,
    *,
    execution_target: str,
    vm_name: str | None,
    config_dir: Path,
    cloud_resource_key: str | None = None,
    display: Callable[[str], None],
    think: Callable[[str], None] = lambda _t: None,
) -> tuple[str | None, str]:
    """Plan 98: make a freshly-served app URL reachable from the HOST's browser on ANY
    target and open it. Returns (opened_url, note). When reach can't be auto-established,
    opened_url is None and note carries the exact remedy (never a dead link)."""
    if not served_url:
        return None, "no served URL detected"
    et = (execution_target or "local").lower()
    if et == "local":
        _open_in_local_browser(served_url, display=display)
        return served_url, f"opened {served_url} in your browser"
    if et == "vm":
        host_url = _host_reachable_url(served_url, execution_target="vm", vm_name=vm_name)
        if _vm_port_reachable(host_url):
            _open_in_local_browser(host_url, display=display)
            return host_url, f"opened {host_url} (VM) in your browser"
        port = _port_from_url(host_url)
        return None, (
            f"{host_url} isn't reachable yet — bind the server to 0.0.0.0 and "
            f"`multipass exec {vm_name or '<vm>'} -- sudo ufw allow {port}`, or forward "
            f"`ssh -L {port}:localhost:{port} …`."
        )
    if et == "container":
        host_url = _container_published_url(served_url, vm_name)  # vm_name carries the container name here
        if host_url:
            _open_in_local_browser(host_url, display=display)
            return host_url, f"opened {host_url} (container published port) in your browser"
        port = _port_from_url(served_url)
        return None, f"the container isn't publishing port {port} — re-run the container with `-p {port}:{port}`."
    if et in ("aws", "gcp"):
        tunnel_url = _start_cloud_tunnel_url(served_url, config_dir=config_dir, resource_key=cloud_resource_key)
        if tunnel_url:
            _open_in_local_browser(tunnel_url, display=display)
            return tunnel_url, f"opened {tunnel_url} via an SSH tunnel to the {et} instance"
        port = _port_from_url(served_url)
        return None, (
            f"couldn't auto-tunnel — forward it yourself: `ssh -L {port}:localhost:{port} <instance>`, "
            f"or open port {port} in the {et} security group/firewall."
        )
    _open_in_local_browser(served_url, display=display)
    return served_url, f"opened {served_url}"


def _open_in_local_browser(url: str, *, display: Callable[[str], None]) -> None:
    """Plan 86 Fix 5: open the URL in the HOST's default browser (Duckln runs locally
    even when the target is remote). Best-effort; never raises/blocks."""
    if not url:
        return
    try:
        import webbrowser

        webbrowser.open(url, new=2)
    except Exception:
        pass


def _subdir_run_command(subdir: Path) -> str | None:
    """Plan 162 F3 (C1): a best-effort run command for ONE runnable subdir (backend/frontend),
    from its package.json dev/start/serve script or a Python entrypoint. None when unclear."""
    pkg = subdir / "package.json"
    if pkg.exists():
        try:
            scripts = (json.loads(pkg.read_text(encoding="utf-8")) or {}).get("scripts") or {}
        except Exception:
            scripts = {}
        pm = "npm"
        if (subdir / "pnpm-lock.yaml").exists():
            pm = "pnpm"
        elif (subdir / "yarn.lock").exists():
            pm = "yarn"
        for name in ("dev", "start", "serve"):
            if name in scripts:
                return f"{pm} run {name}"
    if (subdir / "manage.py").exists():
        return "python manage.py runserver 0.0.0.0:8000"
    for entry in ("main.py", "app.py", "server.py", "run.py"):
        if (subdir / entry).exists():
            return f"python {entry}"
    return None


def _launch_concurrent_processes(
    *,
    topology: tuple[tuple[str, str], ...],
    runner,
    runtime_cwd: str | None,
    project_dir: Path,
    execution_target: str,
    vm_name: str | None,
    config_dir: Path,
    repo,
    display: Callable[[str], None],
    think: Callable[[str], None],
) -> dict[str, str]:
    """Plan 162 F3 (C1): launch EACH runnable subdir (backend before frontend), await each
    readiness via the (now outcome-gated) `_launch_and_await_server`, wire the backend's
    served URL into the frontend subdir's `.env` (API_URL), and return {name: served_url}.
    Best-effort per process; a subdir with no inferable run command is skipped."""
    # Backend-ish first so its URL can be wired into the frontend.
    ordered = sorted(topology, key=lambda t: 0 if t[0] in ("backend", "server", "api") else 1)
    served_map: dict[str, str] = {}
    backend_url: str | None = None
    for name, sub in ordered:
        sub_dir = project_dir / sub
        cmd = _subdir_run_command(sub_dir)
        if not cmd:
            think(f"Couldn't infer a run command for {name}/ — skipping that process.")
            continue
        sub_cwd = f"{runtime_cwd.rstrip('/')}/{sub}" if runtime_cwd else sub
        # Wire the backend URL into a frontend subdir's env before launching it.
        if backend_url and name in ("frontend", "client", "web", "ui"):
            wire = f"touch .env; grep -q '^API_URL=' .env 2>/dev/null || printf 'API_URL=%s\\n' {shlex.quote(backend_url)} >> .env"
            wrapped_wire, _wm = _wrap_command_for_execution_target(
                config_dir=config_dir, execution_target=execution_target, command=wire,
                cwd=sub_cwd, preferred_vm_name=vm_name,
            )
            if wrapped_wire is not None:
                try:
                    runner.run(wrapped_wire)
                    think(f"Wired {name} → backend at {backend_url} (API_URL).")
                except Exception:
                    pass
        think(f"Launching {name} ({cmd}) in {sub}/ …")
        status, served, _log = _launch_and_await_server(
            runner=runner, raw_command=cmd, project_cwd=sub_cwd,
            execution_target=execution_target, vm_name=vm_name, config_dir=config_dir,
            repo=repo, display=display, think=think,
        )
        if served:
            served_map[name] = served
            if name in ("backend", "server", "api"):
                backend_url = served
    return served_map


def _probe_served_outcome(
    *,
    served_url: str,
    runner,
    execution_target: str,
    vm_name: str | None,
    config_dir: Path,
    project_cwd: str | None,
    think: Callable[[str], None],
) -> tuple[bool, str]:
    """Plan 162 F1 (B2): run the HTTP outcome probe ON THE TARGET. Returns (ok, log).
    `curl -fsS` exits non-zero on a non-2xx/refused, so exit 0 == the app actually serves."""
    probe = _outcome_check_command(served_url=served_url, project_dir=Path(project_cwd) if project_cwd else config_dir)
    if not probe or not probe.strip().startswith("curl"):
        # Only the target-agnostic HTTP probe is gated here; the CLI/test outcome is
        # covered by the separate verification step (`_safe_verification_smoke_command`).
        return True, ""
    think("Confirming the served URL actually responds (2xx/3xx)…")
    wrapped, _meta = _wrap_command_for_execution_target(
        config_dir=config_dir, execution_target=execution_target,
        command=probe, cwd=project_cwd, preferred_vm_name=vm_name,
    )
    if wrapped is None:
        return True, ""  # can't reach the target to probe — don't false-fail the launch
    try:
        res = runner.run(wrapped)
    except Exception as exc:  # noqa: BLE001 — a probe error must not crash the launch
        return True, f"(outcome probe could not run: {exc})"
    exit_code = getattr(res, "exit_code", None)
    log = f"{getattr(res, 'stdout', '') or ''}\n{getattr(res, 'stderr', '') or ''}".strip()
    return (exit_code == 0), log


def _launch_and_await_server(
    *,
    runner,
    raw_command: str,
    project_cwd: str | None,
    execution_target: str,
    vm_name: str | None,
    config_dir: Path,
    repo,
    display: Callable[[str], None],
    think: Callable[[str], None],
    ready_window_seconds: float = 45.0,
):
    """Plan 86 Fix 2/5: start a long-running run/serve command DETACHED (so it keeps
    running and the watchdog can't kill it), then poll until the server is ready.
    Returns (status, served_url, log_tail) where status ∈ {"ready","dead","unconfirmed"}
    and log_tail is the captured run output (the real error on death)."""
    import time as _time

    repo_name = getattr(repo, "name", "") or "app"
    # Resolve the underlying dev tool for an opaque `npm run <x>` so the host-bind flag
    # is correct (vite --host, next -H, react-scripts HOST=…).
    underlying = ""
    m_run = re.search(r"\brun\s+([A-Za-z0-9_:-]+)", raw_command)
    if m_run:
        try:
            payload = _read_repo_package_json(config_dir, repo)
            scripts = payload.get("scripts") if isinstance(payload, dict) else None
            if isinstance(scripts, dict):
                underlying = str(scripts.get(m_run.group(1)) or "")
        except Exception:
            underlying = ""
    bound_cmd = _ensure_host_bind(raw_command, execution_target=execution_target, underlying=underlying)
    if bound_cmd != raw_command:
        think("Binding the dev server to 0.0.0.0 so you can open it from your machine…")
    slug = "".join(c if c.isalnum() else "-" for c in repo_name).strip("-") or "app"
    log_path = f"~/.duckln/logs/run-{slug}.log"
    # Plan 98: the multi-target wrapper applies cwd for vm/container/cloud, but the
    # LOCAL passthrough does not — so cd into the repo here for local runs.
    if execution_target == "local" and project_cwd:
        bound_cmd = f"cd {shlex.quote(project_cwd)} && {bound_cmd}"
    launch = (
        f"mkdir -p ~/.duckln/logs; nohup {bound_cmd} </dev/null > {log_path} 2>&1 & "
        f"echo DUCKLN_RUN_PID:$!"
    )
    wrapped, _meta = _wrap_command_for_execution_target(
        config_dir=config_dir, execution_target=execution_target,
        command=launch, cwd=project_cwd, preferred_vm_name=vm_name,
    )
    if wrapped is None:
        return "dead", None, ""
    try:
        res = runner.run(wrapped)
    except Exception:
        return "dead", None, ""
    out = f"{getattr(res, 'stdout', '') or ''}\n{getattr(res, 'stderr', '') or ''}"
    m = re.search(r"DUCKLN_RUN_PID:(\d+)", out)
    pid = m.group(1) if m else ""

    poll_cmd = (
        f"cat {log_path} 2>/dev/null; "
        + (f"kill -0 {pid} 2>/dev/null && echo DUCKLN_ALIVE || echo DUCKLN_DEAD" if pid else "echo DUCKLN_ALIVE")
    )
    wrapped_poll, _m2 = _wrap_command_for_execution_target(
        config_dir=config_dir, execution_target=execution_target,
        command=poll_cmd, cwd=project_cwd, preferred_vm_name=vm_name,
    )
    # Plan 135 F1 (3b): ACTIVE, progress-aware readiness — don't kill a healthy build.
    # `ready_window_seconds` becomes an IDLE budget (how long with NO new output before we
    # give up), reset every time the run log GROWS or shows build-progress markers. So an
    # actively-compiling build (hundreds of crates on a small VM) keeps waiting; only a
    # genuinely STALLED process (no output for the idle window) or a crash stops it. A
    # large absolute cap is the final safety bound.
    idle_window = max(float(ready_window_seconds), 180.0)
    hard_cap = _time.monotonic() + max(float(ready_window_seconds) * 8, 3600.0)
    idle_deadline = _time.monotonic() + idle_window
    last_log = ""
    prev_len = 0
    while _time.monotonic() < idle_deadline and _time.monotonic() < hard_cap:
        _time.sleep(4)
        if wrapped_poll is None:
            break
        try:
            pres = runner.run(wrapped_poll)
        except Exception:
            break
        last_log = getattr(pres, "stdout", "") or ""
        served = _extract_served_url(last_log, execution_target=execution_target, config_dir=config_dir, vm_name=vm_name)
        if served or _looks_server_ready(last_log):
            # Plan 162 F1 (B2): a served URL must actually RESPOND (2xx/3xx), not just print
            # a ready banner. Probe it on the target; a non-2xx/refused means the app started
            # but is broken → a REAL failure fed into recovery, never a silent "running".
            if served:
                ok, probe_log = _probe_served_outcome(
                    served_url=served, runner=runner, execution_target=execution_target,
                    vm_name=vm_name, config_dir=config_dir, project_cwd=project_cwd, think=think,
                )
                if not ok:
                    return "dead", served, (
                        f"{last_log}\n[outcome-check] the served URL {served} did not return a "
                        f"healthy response (2xx/3xx).\n{probe_log}".strip()
                    )
            return "ready", served, last_log
        if "DUCKLN_DEAD" in last_log:
            # Plan 110 Fix 1: return the REAL captured log so attribution sees the
            # actual error (OOM/compiler/missing-lib/disk-full), not a placeholder.
            return "dead", None, last_log
        # Still alive AND making progress → reset the idle budget so the build runs to
        # completion instead of being abandoned mid-compile.
        if len(last_log) > prev_len or _looks_build_progressing(last_log):
            prev_len = len(last_log)
            idle_deadline = _time.monotonic() + idle_window
    # Idle/cap elapsed: alive but no ready banner → don't false-fail.
    return ("dead" if "DUCKLN_DEAD" in last_log else "unconfirmed"), None, last_log


def _repair_plan_for_revise(plan, verdict, *, execution_target: str):
    """Plan 78 Fix D: deterministically repair the mechanical issues the supervisor
    named on a `revise` verdict (missing target / missing verification on mutating
    steps), then return a new plan to re-review ONCE. Returns the same plan object
    when nothing could be auto-repaired (so the caller stops the loop)."""
    from dataclasses import replace as _dc_replace

    changed = False
    new_steps = []
    for s in plan.steps:
        patched = s
        sclass = str(getattr(s, "safety_class", "") or "").upper()
        if sclass in ("S2", "S3"):
            if not (getattr(s, "target", "") or "").strip():
                patched = _dc_replace(patched, target=execution_target)
                changed = True
            if not (getattr(patched, "verification", "") or ""):
                patched = _dc_replace(patched, verification="command exits 0")
                changed = True
        new_steps.append(patched)
    if not changed:
        return plan
    return _dc_replace(plan, steps=tuple(new_steps))


def _infer_run_command(inspection, *, repo: RepoCatalogRecord | None = None, config_dir: Path | None = None) -> str | None:
    """Best-effort run command. Plan 79 L3/L7: for Node repos, prefer a real
    package.json script (dev > start > serve …) formatted for the repo's package
    manager, since that is the authoritative source of how to run it. Falls back
    to repo-knowledge entrypoints / README for other stacks."""
    detected_files = tuple(getattr(inspection, "detected_files", ()) or ())
    if repo is not None and config_dir is not None and "package.json" in {str(f).lower() for f in detected_files}:
        payload = _read_repo_package_json(config_dir, repo)
        if payload is not None:
            # Plan 87: choose the desktop run (tauri/electron) over the frontend `dev`
            # script when the repo is a desktop app, so we don't start the wrong half.
            kind, flavor = _detect_app_archetype(
                payload, detected_files,
                readme_excerpt=getattr(inspection, "readme_excerpt", "") or "",
            )
            cmd = _run_command_from_scripts(
                payload.get("scripts"), _package_manager_for(detected_files),
                archetype=kind, flavor=flavor,
            )
            if cmd:
                return cmd
    knowledge = getattr(inspection, "repo_knowledge", None)
    if knowledge is not None:
        entrypoints = getattr(knowledge, "entrypoints", ()) or ()
        for ep in entrypoints:
            if ep and _looks_like_run_command(ep):
                return ep
        if entrypoints:
            return entrypoints[0]
    return None


def resume_with_approved_plan(
    *,
    plan,
    paths: ConfigPaths,
    current,
    approve: Callable[[str], bool] | None,
    display: Callable[[str], None],
    runner: ControlledCommandRunner | None = None,
    vm_name: str | None = None,
    pane_executor: object | None = None,
    emit_thought: Callable[[str], None] | None = None,
    text_prompt: Callable[[str, str], str | None] | None = None,
    secret_prompt: Callable[[str], str | None] | None = None,
    select: Callable[[str, tuple[str, ...]], str | None] | None = None,
) -> RepoBringUpResult:
    """Plan 67 — strict-adherence executor for an approved PlanRecord.

    Differs from `_execute_plan_with_bounded_recovery` in two ways:
    (1) it runs ONLY the steps in plan.steps — no improvised recovery commands.
    (2) on failure, it triggers the visible attribution + amendment flow:
        the user sees `cause` and the proposed `fix` and is prompted to
        `/plan approve` the amendment (HOTL) or it auto-approves an S0/S1
        amendment (HOOTLWO). Hard cap of MAX_AMENDMENTS amendments.
    """
    from dataclasses import replace as _dc_replace
    from duckln.ai_client import build_default_llm_client_or_none
    from duckln.modes import ControlMode
    from duckln.plan_mode import (
        MAX_AMENDMENTS,
        PLAN_STATUS_COMPLETED,
        PLAN_STATUS_FAILED,
        PLAN_STATUS_IRRECOVERABLE,
        PlanRecord,
        amend_plan,
        attribute_failure,
        gather_repo_understanding,
        mark_status,
    )
    from duckln.ui import render_error_attribution
    from state.access import read_config_snapshot

    # Plan 68: run on the live session target, not a hardcoded "local". The
    # target string also drives per-command wrapping below.
    execution_target = (
        read_config_snapshot(paths.config_dir).get("execution_target", "local") or "local"
    )

    runner_instance = runner or ControlledCommandRunner(
        trace=display,
        execution_target=execution_target,
        vm_name=vm_name,
        pane_executor=pane_executor,
    )

    # Plan 196 F1: a NO-REPO plan (an infra/provisioning/tooling plan drafted by
    # `gate_mutation_or_draft` for `/vm`·`/cloud`, which pass repo_slug=None) must NOT
    # be `cd`'d into a project dir. The old `plan.repo_slug or "plan-mode"` default
    # made runtime_cwd = ~/.duckln/projects/plan-mode — a dir that never exists (nothing
    # was cloned) — so even a trivial `multipass version` ran as `cd "…/plan-mode" &&
    # multipass version` → `cd: no such file or directory`. When there is no repo, run
    # every step from the shell's default cwd (correct for multipass/aws/gcloud).
    is_repo_plan = bool((plan.repo_slug or "").strip())
    repo_slug = plan.repo_slug or "plan-mode"
    # Plan 74 fix: derive a CLEAN repo name (last path segment) so the resolved
    # project dir matches the clone target in the plan. Using the full URL as the
    # name slugified to a bogus dir like `https---github.com-...` that the clone
    # `cd` then failed on.
    clean_name = repo_slug.rstrip("/").split("/")[-1] or "plan-mode"
    if clean_name.endswith(".git"):
        clean_name = clean_name[:-4]
    fake_repo = RepoCatalogRecord(
        name=clean_name,
        repo_url=repo_slug if (plan.repo_slug and "/" in plan.repo_slug) else "",
        stars=0,
        description="",
        category="",
        framework="",
        last_updated="",
    )
    # project_dir stays a Path for the existing `project_dir.exists()` guards (secret
    # injection / understanding / topology / result) — all correctly no-op for a no-repo
    # plan since the dir doesn't exist. runtime_cwd drives the per-step `cd`, so it is
    # None for a no-repo plan (F1) → step_cwd becomes None for every step below.
    project_dir = resolve_managed_project_dir(paths.config_dir, fake_repo)
    runtime_cwd = (
        resolve_runtime_project_dir(
            paths.config_dir, fake_repo, execution_target=execution_target
        )
        if is_repo_plan
        else None
    )

    # Plan 179 C2: failure recovery runs under the ERROR_AGENT role.
    from duckln.ai_client import build_llm_client_for_role as _build_llm_client_for_role
    llm_client = _build_llm_client_for_role(paths.config_dir, "ERROR_AGENT")
    detected_runtimes = _detect_runtimes_for_understanding(paths.config_dir)
    os_name, arch = _detect_os_and_arch()
    understanding = gather_repo_understanding(
        objective=plan.objective,
        project_dir=project_dir if project_dir.exists() else None,
        repo_slug=plan.repo_slug,
        config_dir=paths.config_dir,
        os_name=os_name,
        arch=arch,
        execution_target=execution_target,
        control_mode=current.mode.value,
        detected_runtimes=detected_runtimes,
    )

    executed: list[str] = []

    # Plan 162 F5 (Plan 160-A): one session-scoped decider for DESTRUCTIVE (S4) steps —
    # offers Yes / No / Approve-all-this-session and REMEMBERS 'approve all' across steps.
    _destructive_decider = make_destructive_decider(select=select, approve=approve)

    # Plan 133 F2: RESUME — load steps already completed in a prior run so a re-run
    # (e.g. after a provider switch) skips them instead of re-installing/re-building.
    _resume_slug = str(plan.repo_slug or getattr(fake_repo, "repo_url", "") or getattr(fake_repo, "name", "") or "repo")
    _done_steps = _load_done_steps(paths.config_dir, _resume_slug)

    # Plan 162 F7 (C3): first-run untrusted-code posture — setting up a FRESH repo runs its
    # own build/test/lifecycle scripts (third-party code) on the user's real machine. Surface
    # a one-time sandbox recommendation on a local target for a repo Duckln hasn't run before.
    # Informational + a recommendation; the S-class gates and mode policy are unchanged.
    _untrusted_notice = _untrusted_local_notice(
        execution_target=execution_target, repo_previously_trusted=bool(_done_steps)
    )
    if _untrusted_notice:
        display(_untrusted_notice)

    # Plan 80 Fix 3: stream live reasoning into the thinking box DURING execution.
    # Plan 138 F3: buffer the live "Duckln's thinking" so it can be persisted to
    # logical-thinking.md and surfaced as a clickable link at each major point.
    _think_buffer: list[str] = []

    def _think(text: str) -> None:
        if text:
            _think_buffer.append(str(text))
        if emit_thought is not None:
            try:
                emit_thought(text)
            except Exception:
                pass

    def _flush_thinking(title: str, surface: str = "step") -> None:
        """Plan 138 F3: append the buffered reasoning to logical-thinking.md and surface a
        CLICKABLE link — at plan-created / each step success / each failure / pre-summary."""
        from duckln.recovery import append_thinking_log, surface_thinking_link

        lines = list(_think_buffer)
        _think_buffer.clear()
        if not lines:
            return
        try:
            p = append_thinking_log(paths.config_dir, repo_slug=_resume_slug, title=title, lines=lines, surface=surface)
            surface_thinking_link(p, display)
        except Exception:
            pass

    _think(f"Executing the approved plan for {getattr(fake_repo, 'name', 'the repo')} — {len(plan.steps)} step(s).")
    _flush_thinking(f"Plan ready: {getattr(fake_repo, 'name', 'repo')} — {len(plan.steps)} step(s)", surface="plan")

    # Plan 162 F2 (C2): before the app launches, masked-prompt for any required repo SECRET
    # that has no safe dev default (API key/token/DSN) and inject it into `.env`. The value
    # is collected via the masked `secret_prompt`, written via a NO-TRACE runner, and never
    # added to the plan/executed list/thinking-log — so it is never displayed, logged, or
    # persisted. Only attempted when the repo is readable locally (clone present).
    _secret_intake_done = False

    def _inject_repo_secrets_once() -> None:
        nonlocal _secret_intake_done
        if _secret_intake_done or secret_prompt is None or not project_dir.exists():
            return
        _secret_intake_done = True
        try:
            detected = _detected_env_example_files(project_dir)
            required = _required_env_vars(config_dir=paths.config_dir, repo=fake_repo, detected_files=detected)
        except Exception:
            return
        needing = _secret_env_keys_needing_intake(required)
        if not needing:
            return
        collected: dict[str, str] = {}
        for key in needing:
            try:
                value = secret_prompt(f"This repo needs a secret value for {key} (input hidden, never stored): ")
            except Exception:
                value = None
            if value:
                collected[key] = value
        if not collected:
            display("Note — no secret provided; the app may not start until you set: " + ", ".join(needing))
            return
        command = _secret_upsert_command(collected)
        wrapped_secret, _sm = _wrap_command_for_execution_target(
            config_dir=paths.config_dir, execution_target=execution_target,
            command=command or "", cwd=runtime_cwd, preferred_vm_name=vm_name,
        )
        if not command or wrapped_secret is None:
            return
        # NO trace, NO pane_executor → the secret value is never echoed to the user or logs.
        quiet_runner = ControlledCommandRunner(trace=None, execution_target=execution_target, vm_name=vm_name)
        try:
            quiet_runner.run(wrapped_secret)
            display(f"✓ Injected {len(collected)} repo secret(s) into .env (values hidden, never stored).")
        except Exception:
            pass

    def _exec_thought(step) -> str:
        cmd = (step.command or "").lower()
        if "git clone" in cmd:
            return "Fetching the repo…"
        if "nodesource" in cmd or "nvm" in cmd or "apt-get install -y nodejs" in cmd:
            return "Installing the Node version this repo needs…"
        if any(k in cmd for k in ("install", " ci")):
            return "Installing dependencies — this can take a moment…"
        if _looks_like_run_command(step.command or ""):
            return "Starting the app and watching for the served URL…"
        return f"Running: {step.title}…"

    for step in plan.steps:
        if step.command is None:
            display(f"• Step {step.index} — {step.title} (no command; informational only)")
            continue

        # Plan 133 F2: skip a step already completed in a prior run (resume, not restart)
        # — but never skip the run/serve step (it must start the app each session).
        # Plan 144 F3: also never permanently skip a marker-guarded setup step (the venv
        # setup that checks `.duckln-deps-ok`): it's a cheap no-op when complete and it
        # self-heals (rebuilds) if its venv was deleted by a later cleanup/crunch.
        if (
            step.command in _done_steps
            and not _looks_like_run_command(step.command or "")
            and not _is_marker_guarded_setup(step.command or "")
        ):
            display(f"↳ Step {step.index}/{len(plan.steps)} — {step.title} — already done (resuming)")
            continue

        # Mode + safety gate. Plan 77 Fix 2: the plan was already approved as a
        # batch, so original planner steps run without a per-step re-prompt;
        # amendments added after approval still prompt; S4 is still blocked.
        if not _step_allowed_in_mode(
            step.safety_class,
            current.mode,
            approve,
            plan_approved=True,
            step_origin=getattr(step, "origin", "planner"),
            destructive_decider=_destructive_decider,
        ):
            display(
                f"✗ Step {step.index} requires approval but no approve callback "
                "is configured. Halting strict execution."
            )
            return _bringup_result(
                fake_repo, project_dir, current.mode, executed,
                False, "Plan execution halted: step required approval and none was given.",
            )

        # Plan 74 fix: the clone step CREATES the project dir, so it must run
        # from the parent (no cwd into a not-yet-existing dir). Every later step
        # runs INSIDE the cloned dir.
        is_clone = "git clone" in (step.command or "")
        step_cwd = None if is_clone else runtime_cwd
        # Wrap the bare command for the active target (local→passthrough,
        # vm→multipass exec, aws/gcp→cloud remote exec).
        wrapped_command, _wrap_meta = _wrap_command_for_execution_target(
            config_dir=paths.config_dir,
            execution_target=execution_target,
            command=step.command,
            cwd=step_cwd,
            preferred_vm_name=vm_name,
        )
        if wrapped_command is None:
            display(
                f"✗ Step {step.index} cannot run: the {execution_target} target "
                "is not resolvable (no active VM/cloud resource). Halting."
            )
            return _bringup_result(
                fake_repo, project_dir, current.mode, executed, False,
                f"Plan halted: {execution_target} target not resolvable.",
            )

        display(f"▶ Step {step.index}/{len(plan.steps)} — {step.title}  [{step.safety_class}]")
        display(f"   $ {step.command}")
        _think(_exec_thought(step))

        # Plan 86 Fix 2: a run/serve step is a LONG-RUNNING server — never wait for it
        # to exit (it won't). Launch it detached and poll until it's serving; "serving"
        # = SUCCESS (left running), only a real crash is a failure.
        if _looks_like_run_command(step.command or ""):
            app_name = getattr(fake_repo, "name", "") or "the app"
            # Plan 162 F2 (C2): inject required repo secrets into .env right before launch.
            _inject_repo_secrets_once()
            # Plan 87/89: a desktop (Tauri/Electron) app is NOT a web server. On any
            # HEADLESS target (remote, OR a 'local' that's a GUI-less Linux box/VM with
            # no $DISPLAY) run the real native app under a virtual display and stream it
            # via noVNC. Only a target with a real display opens a native window.
            desktop_flavor = _looks_like_desktop_run_command(step.command or "")
            desktop_headless = bool(desktop_flavor) and (
                execution_target in _REMOTE_EXECUTION_TARGETS
                or _target_is_headless(
                    runner_instance, config_dir=paths.config_dir,
                    execution_target=execution_target, vm_name=vm_name,
                )
            )
            if desktop_headless:
                _think(f"Executor → {app_name} is a desktop ({desktop_flavor}) app on a headless target — streaming its window to your browser via noVNC.")
                status, novnc, log_tail = _launch_desktop_stream(
                    runner=runner_instance, raw_command=step.command or "", flavor=desktop_flavor,
                    project_cwd=runtime_cwd, execution_target=execution_target, vm_name=vm_name,
                    config_dir=paths.config_dir, repo=fake_repo, display=display, think=_think,
                )
                executed.append(step.command)
                if status == "dead":
                    display(f"✗ Step {step.index} — {app_name} (desktop) exited before it started.")
                    _think("The desktop app crashed on startup — reading the actual error to find a fix…")
                    return _attempt_amendment_and_halt(
                        plan=plan, paths=paths, step=step,
                        stderr=(log_tail or f"{app_name} (desktop {desktop_flavor}) exited before starting; see ~/.duckln/logs/run-*.log"),
                        stdout="", exit_code=137, understanding=understanding, llm_client=llm_client,
                        display=display, current=current, approve=approve, executed=executed,
                        fake_repo=fake_repo, project_dir=project_dir, text_prompt=text_prompt,
                    )
                display(f"☑ Step {step.index}/{len(plan.steps)} — {step.title} — done")
                if novnc and _vm_port_reachable(novnc):
                    _open_in_local_browser(novnc, display=display)
                    display(f"  → {app_name} (desktop) is streaming at {novnc} — opening it in your browser. (left running)")
                    _think(f"✓ {app_name} is running on the {execution_target} and streamed to {novnc} — left running.")
                else:
                    _think(f"✓ {app_name} (desktop) started under a virtual display; the noVNC port isn't reachable from the host yet.")
                    display(
                        f"  → {app_name} (desktop) is running inside the {execution_target} under a virtual display, "
                        f"but the noVNC stream ({novnc}) isn't reachable from your machine yet. Forward the port: "
                        f"`ssh -L {_VNC_WEB_PORT}:localhost:{_VNC_WEB_PORT} …` / `multipass exec {vm_name or '<vm>'} -- sudo ufw allow {_VNC_WEB_PORT}`."
                    )
                continue
            if desktop_flavor:
                # Local desktop app: launch detached; it opens its own native window.
                # Plan 88: source cargo's env for a Tauri Rust backend; first compile
                # can take minutes, so use a longer readiness window for Tauri.
                _think(f"Executor → {app_name} is a desktop ({desktop_flavor}) app — launching its native window locally.")
                local_cmd = _desktop_launch_command(step.command or "", desktop_flavor, headless=False)
                status, _served, log_tail = _launch_and_await_server(
                    runner=runner_instance, raw_command=local_cmd,
                    project_cwd=runtime_cwd, execution_target=execution_target, vm_name=vm_name,
                    config_dir=paths.config_dir, repo=fake_repo, display=display, think=_think,
                    ready_window_seconds=(300.0 if desktop_flavor == "tauri" else 60.0),
                )
                executed.append(step.command)
                if status == "dead":
                    display(f"✗ Step {step.index} — {app_name} (desktop) exited before it started.")
                    _think("The desktop app crashed on startup — reading the actual error to find a fix…")
                    return _attempt_amendment_and_halt(
                        plan=plan, paths=paths, step=step,
                        stderr=(log_tail or f"{app_name} (desktop {desktop_flavor}) exited before starting; see ~/.duckln/logs/run-*.log"),
                        stdout="", exit_code=137, understanding=understanding, llm_client=llm_client,
                        display=display, current=current, approve=approve, executed=executed,
                        fake_repo=fake_repo, project_dir=project_dir, text_prompt=text_prompt,
                    )
                display(f"☑ Step {step.index}/{len(plan.steps)} — {step.title} — done")
                display(f"  → {app_name} (desktop) launched — its native window should be open on your screen. (left running)")
                _think(f"✓ {app_name} (desktop) launched locally — native window open, left running.")
                continue
            # Plan 162 F3 (C1): a backend+frontend split (≥2 runnable subdirs, no root script
            # already orchestrating both) → launch BOTH, await each, wire backend→frontend,
            # report all URLs. A single-process app falls through to the single launch below.
            _root_scripts: dict = {}
            try:
                _pkg = _read_repo_package_json(paths.config_dir, fake_repo)
                if isinstance(_pkg, dict):
                    _root_scripts = _pkg.get("scripts") or {}
            except Exception:
                _root_scripts = {}
            _topology = (
                _subdir_run_topology(project_dir)
                if project_dir.exists() and not _has_concurrent_run_script(_root_scripts)
                else ()
            )
            if _topology:
                _think(f"Executor → {app_name} is a multi-process app ({', '.join(n for n, _ in _topology)}) — launching each and wiring them together.")
                served_map = _launch_concurrent_processes(
                    topology=_topology, runner=runner_instance, runtime_cwd=runtime_cwd,
                    project_dir=project_dir, execution_target=execution_target, vm_name=vm_name,
                    config_dir=paths.config_dir, repo=fake_repo, display=display, think=_think,
                )
                executed.append(step.command)
                if not served_map:
                    display(f"✗ Step {step.index} — none of {app_name}'s processes reached readiness.")
                    return _attempt_amendment_and_halt(
                        plan=plan, paths=paths, step=step,
                        stderr=f"{app_name}: no concurrent process (of {', '.join(n for n, _ in _topology)}) reached readiness; see ~/.duckln/logs/run-*.log",
                        stdout="", exit_code=137, understanding=understanding, llm_client=llm_client,
                        display=display, current=current, approve=approve, executed=executed,
                        fake_repo=fake_repo, project_dir=project_dir, text_prompt=text_prompt,
                    )
                _mark_step_done(paths.config_dir, _resume_slug, step.command, _done_steps)
                display(f"☑ Step {step.index}/{len(plan.steps)} — {step.title} — done (multi-process)")
                for _n, _u in served_map.items():
                    _host = _host_reachable_url(_u, execution_target=execution_target, vm_name=vm_name)
                    if execution_target not in _REMOTE_EXECUTION_TARGETS or _vm_port_reachable(_host):
                        _open_in_local_browser(_host, display=display)
                    display(f"  → {_n} is serving at {_host} (left running)")
                _think(f"✓ {app_name} launched {len(served_map)} wired process(es) — left running.")
                continue
            status, served, log_tail = _launch_and_await_server(
                runner=runner_instance, raw_command=step.command or "",
                project_cwd=runtime_cwd, execution_target=execution_target, vm_name=vm_name,
                config_dir=paths.config_dir, repo=fake_repo, display=display, think=_think,
            )
            executed.append(step.command)
            if status == "dead":
                display(f"✗ Step {step.index} — {app_name} exited before it started serving.")
                _think("The app crashed on startup — reading the actual error to find a fix…")
                return _attempt_amendment_and_halt(
                    plan=plan, paths=paths, step=step,
                    stderr=(log_tail or f"{app_name} exited before serving; see ~/.duckln/logs/run-*.log"),
                    stdout="", exit_code=137, understanding=understanding, llm_client=llm_client,
                    display=display, current=current, approve=approve, executed=executed,
                    fake_repo=fake_repo, project_dir=project_dir, text_prompt=text_prompt,
                )
            display(f"☑ Step {step.index}/{len(plan.steps)} — {step.title} — done")
            # Plan 86 Fix 5: surface a HOST-reachable URL and open it in the user's
            # local browser (the VM/container has no GUI). Fall back to guidance.
            if served:
                host_url = _host_reachable_url(served, execution_target=execution_target, vm_name=vm_name)
                if execution_target in _REMOTE_EXECUTION_TARGETS and not _vm_port_reachable(host_url):
                    port = _port_from_url(host_url)
                    cmd = step.command or ""
                    bind_note = (" Start it with `--host 0.0.0.0`" if ("0.0.0.0" not in cmd and "--host" not in cmd) else "")
                    display(
                        f"  → {app_name} is running inside the {execution_target} but {host_url} isn't reachable "
                        f"from your machine yet.{bind_note} Or forward the port: "
                        f"`ssh -L {port}:localhost:{port} …` / `multipass exec {vm_name or '<vm>'} -- sudo ufw allow {port}`."
                    )
                    _think(f"✓ {app_name} started; the port isn't reachable from the host yet — gave the forward/bind remedy.")
                else:
                    _open_in_local_browser(host_url, display=display)
                    display(f"  → {app_name} is serving at {host_url} — opening it in your browser. (left running)")
                    _think(f"✓ {app_name} is now serving at {host_url} — left running.")
            elif status == "unconfirmed":
                display(f"  → {app_name} started but Duckln couldn't confirm the URL yet — it may still be booting (log: ~/.duckln/logs).")
                _think(f"✓ {app_name} started; URL not confirmed within the window — left running.")
            continue

        try:
            result = runner_instance.run(wrapped_command)
        except KeyboardInterrupt:
            # Plan 133 F7a: a step cancelled mid-flight (Ctrl-C / user cancel /
            # provider switch) must never be silently dropped — surface a tracked
            # "Tool interrupted" line (its dot recolors red via _message_dot_color)
            # before propagating, so the message window records the interruption.
            display(f"✗ Tool interrupted — Step {step.index}/{len(plan.steps)} ({step.title}) was cancelled before it finished.")
            _think("That step was interrupted — leaving completed work in place so a re-run resumes from here.")
            raise
        except Exception as exc:
            display(f"✗ Step {step.index} raised: {exc}")
            _think("That step errored — reading the output to find a fix…")
            _amend = _attempt_amendment_and_halt(
                plan=plan,
                paths=paths,
                step=step,
                stderr=str(exc),
                stdout="",
                exit_code=-1,
                understanding=understanding,
                llm_client=llm_client,
                display=display,
                current=current,
                approve=approve,
                executed=executed,
                fake_repo=fake_repo,
                project_dir=project_dir, text_prompt=text_prompt,
            )
            if _amend is _RECOVERY_CONTINUE:
                continue
            return _amend
        # Plan 135 F2: a build/long step that merely TIMED OUT with no real error is
        # "needs more time," NOT a code bug. Be an active agent: extend the window and
        # wait, instead of handing a timeout artifact to the model (which hallucinates a
        # fix like `rustup update`). Retry ONCE with a generous budget before amending.
        if (
            getattr(result, "timed_out", False)
            and not (getattr(result, "stderr", "") or "").strip()
            and _is_build_tier_command(step.command or "")
        ):
            display(f"⏱ Step {step.index} is a heavy build still running — giving it more time instead of treating it as an error…")
            _think("That build didn't error — it just needs more time. Extending the budget and waiting (active, not a fixed timer).")
            # Plan 137: a timeout means "needs more time" — do NOT reclaim/disturb the
            # in-progress build's caches here (that would force re-downloads). Partial
            # reclaim is for interrupt/OOM/disk-full (handled by the resource-crunch +
            # auto-apply seams), not a healthy-but-slow build.
            try:
                result = runner_instance.run(wrapped_command, timeout_seconds=5400.0)
            except KeyboardInterrupt:
                display(f"✗ Tool interrupted — Step {step.index}/{len(plan.steps)} ({step.title}) was cancelled before it finished.")
                raise
            except Exception:
                pass
        ok = (not getattr(result, "timed_out", False)) and (getattr(result, "exit_code", 1) == 0)
        executed.append(step.command)
        if not ok:
            stderr = getattr(result, "stderr", "") or ""
            stdout = getattr(result, "stdout", "") or ""
            exit_code = getattr(result, "exit_code", 1)
            # A still-timing-out build (no captured error) is an honest "took too long",
            # not a fabricated code fix.
            if getattr(result, "timed_out", False) and not stderr.strip():
                stderr = (
                    "Build did not finish within the extended time budget (no error emitted). "
                    "It may need a larger/faster target or more RAM."
                )
            display(f"✗ Step {step.index} ({step.title}) failed (exit_code={exit_code}).")
            # Plan 183 F5: name the step + a one-line error hint so it's not a vague "that step failed".
            _err_hint = _first_error_hint(stderr or stdout)
            _think(
                f"Step {step.index} — “{step.title}” failed"
                + (f": {_err_hint}" if _err_hint else "")
                + " — reading the error to find a fix…"
            )
            # Plan 145 F5: do NOT write a pre-diagnosis stub episode here (it dead-ended the
            # log at "reading the error…"). `_attempt_amendment_and_halt` now writes the FULL
            # recovery episode (symptom → diagnosis → fix → outcome). Just clear the on-screen
            # buffer so stale pre-failure status lines don't leak into the next episode.
            _think_buffer.clear()
            _amend = _attempt_amendment_and_halt(
                plan=plan,
                paths=paths,
                step=step,
                stderr=stderr,
                stdout=stdout,
                exit_code=exit_code,
                understanding=understanding,
                llm_client=llm_client,
                display=display,
                current=current,
                approve=approve,
                executed=executed,
                fake_repo=fake_repo,
                project_dir=project_dir, text_prompt=text_prompt,
            )
            # Plan 130: reasoned recovery resolved/skipped this step → continue the plan.
            if _amend is _RECOVERY_CONTINUE:
                continue
            return _amend

        display(f"☑ Step {step.index}/{len(plan.steps)} — {step.title} — done")
        # Plan 133 F2: remember this step as completed so a re-run resumes past it.
        _mark_step_done(paths.config_dir, _resume_slug, step.command, _done_steps)
        # Plan 138 F3: persist + surface the reasoning after EACH successful step.
        _flush_thinking(f"Step {step.index}/{len(plan.steps)} ✓ {step.title}", surface="step")

    # All steps passed.
    _think("All steps passed — the repo is set up and running.")
    _flush_thinking(f"Done: {getattr(fake_repo, 'name', 'repo')} set up and running", surface="summary")
    display("✅ Plan executed — your repo is ready! 🎉")
    _clear_done_steps(paths.config_dir, _resume_slug)  # Plan 133 F2: reset resume state on success
    try:  # Plan 136 F4: reset the attempted-fix signatures on success.
        from state.access import write_config_snapshot as _wcs
        import json as _json2
        _wcs(paths.config_dir, {_attempted_fixes_key(_resume_slug): _json2.dumps([])})
    except Exception:
        pass
    final = mark_status(plan, PLAN_STATUS_COMPLETED, note=f"executed {len(executed)} command(s)")
    from state.access import write_pending_plan

    write_pending_plan(paths.config_dir, final.to_dict())
    # Plan 72 Phase 8 / Plan 73 D2-D3: learning loop on verified success —
    # extract a reusable skill AND persist a redacted reflection.
    try:
        from duckln.plan_mode import persist_reflection, record_plan_skill, reflect_on_run

        record_plan_skill(
            config_dir=paths.config_dir,
            repo_family=getattr(understanding, "repo_family", "unknown"),
            os_name=getattr(understanding, "os_name", ""),
            execution_target=execution_target,
            commands=tuple(executed),
        )
        note = reflect_on_run(plan=final, executed=tuple(executed), succeeded=True)
        persist_reflection(paths.config_dir, repo_slug=plan.repo_slug, note=note)
    except Exception:
        pass
    # Plan 79 L8: remember per-repo facts so the NEXT plan for this exact repo
    # starts smarter — the working run command and the Node version that worked.
    try:
        from state.access import write_repo_facts

        run_cmd = next((s.command for s in plan.steps if _looks_like_run_command(s.command or "")), "")
        node_ver = ""
        for s in plan.steps:
            m = re.search(r"setup_(\d{1,2})\.x|node@(\d{1,2})|Node\.js\s+(\d{1,2})", s.command or "" + (s.title or ""))
            if m:
                node_ver = next((g for g in m.groups() if g), "")
                break
        facts = {"run_command": run_cmd or "", "node_version": node_ver}
        if plan.repo_slug:
            write_repo_facts(paths.config_dir, plan.repo_slug, {k: v for k, v in facts.items() if v})
    except Exception:
        pass
    return _bringup_result(
        fake_repo, project_dir, current.mode, executed, True,
        f"✅ Plan executed — your repo is ready! 🎉 ({len(executed)} command(s) executed).",
    )


def _step_allowed_in_mode(
    safety_class: str,
    mode: object,
    approve: Callable[[str], bool] | None,
    *,
    plan_approved: bool = False,
    step_origin: str = "planner",
    destructive_decider: "Callable[[str], str] | None" = None,
) -> bool:
    """Plan 72 Phase 6 / Plan 160 Phase A: enforce the mode policy.

    Auto-run (no approval): HITL → S0 only; HOTL → S0/S1; HOOTLWO → S0/S1.
    S2/S3 ALWAYS require explicit approval (HOOTLWO no longer auto-runs them).
    Unknown safety classes are treated as S3.

    Plan 160 Phase A: a DESTRUCTIVE (S4) step is NOT auto-blocked — the USER is the final
    authority. `destructive_decider(cmd_or_msg)` returns 'yes' / 'no' / 'all' (Approve-all-
    this-session; the decider itself remembers 'all' so later destructive steps don't re-ask);
    'yes'/'all' → allowed, 'no' → not. With no decider it falls back to the binary `approve`
    (y/n, no 'all'); with neither it returns False (can't run destructive unattended).

    Plan 77 Fix 2: when ``plan_approved`` is True, the original planner steps run without a
    per-step prompt — approving the plan IS the approval — EXCEPT a destructive step, which
    always asks the user (above).
    """
    from duckln.modes import ControlMode as _ControlMode

    sclass = str(safety_class or "").upper()
    if sclass not in ("S0", "S1", "S2", "S3", "S4"):
        sclass = "S3"
    if sclass == "S4":
        # Plan 160 Phase A: the user decides on a destructive step (Yes / No / Approve-all).
        if destructive_decider is not None:
            choice = str(destructive_decider("This step is DESTRUCTIVE — run it?") or "").strip().lower()
            return choice in ("yes", "y", "all", "approve_all", "approve all this session")
        if approve is not None:
            return approve("This step is DESTRUCTIVE — run it? [y/n]")
        return False  # no way to ask the user → don't run destructive unattended

    mode_value = getattr(mode, "value", str(mode))
    is_hitl = mode == _ControlMode.HITL or mode_value == "hitl"
    is_hotl = mode == _ControlMode.HOTL or mode_value == "hotl"
    is_hootlwo = mode == _ControlMode.HOOTLWO or mode_value == "hootlwo"

    if sclass == "S0":
        return True
    if sclass == "S1" and (is_hotl or is_hootlwo):
        return True
    # Plan 77 Fix 2: a planner step inside an already-approved plan needs no
    # second prompt — the user approved the whole plan (S4 already returned above).
    if plan_approved and step_origin == "planner":
        return True
    # S1 under HITL, and all S2/S3 in every mode → explicit approval required.
    if approve is None:
        return False
    return approve(f"Approve {sclass} step? [y/n]")


def make_destructive_decider(select=None, approve=None):
    """Plan 160 Phase A: build a per-session decider for DESTRUCTIVE steps that asks the user
    Yes / No / Approve-all-this-session and REMEMBERS 'approve all' so later destructive steps
    in the same session don't re-ask. `select(message, options)->label` is the 3-way chooser
    (preferred); `approve(message)->bool` is the binary fallback (offers only yes/no, no 'all').
    Returns a `decide(message)->'yes'|'no'|'all'` callable. Pass it to `_step_allowed_in_mode`."""
    state = {"approve_all": False}

    def decide(message: str) -> str:
        if state["approve_all"]:
            return "all"
        if select is not None:
            label = str(select(message, ("Yes", "No", "Approve all this session")) or "").strip().lower()
            if label.startswith("approve all") or label == "all":
                state["approve_all"] = True
                return "all"
            return "yes" if label in ("yes", "y") else "no"
        if approve is not None:
            return "yes" if approve(f"{message} [y/n]") else "no"
        return "no"

    return decide


_CRASH_SIGNATURES = (
    "signal: 9", "sigkill", "could not compile", "segmentation fault", "out of memory",
    "killed", "panicked", "core dumped", "cannot allocate memory",
)


def _amendment_fix_is_plausible(error_text: str, fix_command: str) -> bool:
    """Plan 110 Fix 7: reject an LLM-proposed fix that's obviously wrong for the error
    class — e.g. adding a `timeout` flag to a crash/compile/OOM error (a common weak-
    model hallucination), or an invented flag that ignores the real failure. Generic;
    conservative (only rejects clear mismatches)."""
    e = (error_text or "").lower()
    f = (fix_command or "").lower()
    crashy = any(k in e for k in _CRASH_SIGNATURES)
    timed_out = "timed out" in e or "timeout" in e
    if crashy and not timed_out and ("timeout" in f or "--timeout" in f):
        return False
    # Plan 138 F2: an apt DEPENDENCY CONFLICT / held-broken-packages error is NOT a missing
    # compiler — reject an "install build-essential / a compiler" fix for it (the wrong
    # diagnosis Duckln made when the apt output merely listed `node-gyp` as a dependency).
    apt_conflict = any(k in e for k in ("held broken packages", "unable to correct problems")) or "conflicts:" in e
    if apt_conflict and ("build-essential" in f or "xcode-select" in f):
        return False
    return True


def _web_evidence_for_failed_step(
    *,
    config_dir: Path,
    command: str,
    error_text: str,
    execution_target: str,
    display: Callable[[str], None],
) -> str | None:
    """Plan 77 Fix 4: when local attribution fails, look up the exact error on
    public sources (only if the internet skill is enabled). Returns a redacted
    evidence string (title + url + excerpt) to feed back into attribution, or
    None when internet is off or nothing is found. Never raises."""
    try:
        from duckln.internet_skill import is_internet_enabled
        if not is_internet_enabled(config_dir):
            # Plan 138 F4: don't give up silently — tell the user Duckln CAN look this up.
            display("🌐 I can search the web for this fix — run `/internet on`, then `/plan continue`, so I can look it up.")
            return None
    except Exception:
        return None
    # Plan 138 F4: announce that Duckln is actively researching (the user asked for this).
    display("🌐 Let me check the web to find a solution…")
    try:
        from duckln.web_runtime import search_runtime_issue
        os_hint = "Linux VM" if execution_target in _REMOTE_EXECUTION_TARGETS else platform.system()
        # Plan 80 Fix 7: browse LIVE (surface each URL via `display`) and read the
        # actual page content — not just the search snippet — so a real fix is found.
        from duckln.web_runtime import gather_repair_fix

        evidence = gather_repair_fix(
            command=redact_sensitive_data(command or ""),
            error_text=redact_sensitive_data(error_text or ""),
            trace=display,
            execution_target=execution_target,
            os_hint=os_hint,
            max_pages=3,
        )
    except Exception:
        return None
    if not evidence:
        display("Checked the top sources online but found no usable fix.")
        return None
    # Plan 181: ENRICH (never replace) the deterministic evidence — when a WEB_READER model is
    # configured and the gathered page body is large, distill its verbatim fix commands and
    # append them. The deterministic evidence stays the floor; the reader only adds signal.
    try:
        from duckln.web_runtime import read_web_page

        digest = read_web_page(
            excerpt=evidence, url="", config_dir=config_dir,
            context=redact_sensitive_data((error_text or "")[:400]),
        )
        if digest and digest.get("fix_commands"):
            evidence = evidence + "\n\nExtracted fix commands: " + "; ".join(digest["fix_commands"])
    except Exception:
        pass
    return redact_sensitive_data(evidence)


def _translate_error_and_fix_plain(error_text: str, fix_command: str, *, llm_client) -> str:
    """Plan 196 F12 step 2: rewrite the technical error + fix into NON-TECHNICAL language for a
    non-technical user, stripping devops jargon. LLM-voiced when a model is present; a plain
    deterministic sentence otherwise (never raw stderr)."""
    err = redact_sensitive_data((error_text or "").strip())[:600]
    fix = (fix_command or "").strip()
    if llm_client is not None:
        try:
            system = (
                "You explain a technical setup error and its fix to a NON-TECHNICAL person. In 2-3 "
                "short sentences: (1) what went wrong, in plain words with NO jargon (e.g. say 'another "
                "app is using the same connection channel' instead of 'port 8080 in use'); (2) what the "
                "fix will do and why it's safe. Do NOT show raw error text or commands. Warm, calm, brief."
            )
            out = str(llm_client(
                system_prompt=system,
                user_message=f"Error: {err}\nProposed fix command (do not quote it verbatim): {fix[:200]}",
            ) or "").strip()
            if out:
                return out[:600]
        except Exception:
            pass
    # Deterministic floor — factual, no raw stderr dump.
    first = next((ln.strip() for ln in (error_text or "").splitlines() if ln.strip()), "the step didn't complete")
    return (
        "Something went wrong while setting things up, and I found a change that should fix it. "
        "I'll apply it and check that it worked — nothing on your machine is deleted."
    )


def _write_diagnostics_bundle(
    config_dir: Path, *, repo_name: str, error_text: str, tried: str, fix_command: str = ""
) -> Path | None:
    """Plan 196 F13: write a REDACTED local diagnostics bundle (a one-click 'support ticket')
    the user can review/send. Never contains raw secrets. Returns the file path, or None on
    failure. Producing it is local — actually sending it is a separate consented step."""
    try:
        import datetime as _dt

        bundle_dir = Path(config_dir) / "diagnostics"
        bundle_dir.mkdir(parents=True, exist_ok=True)
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        slug = "".join(c if c.isalnum() else "-" for c in (repo_name or "repo")).strip("-").lower() or "repo"
        path = bundle_dir / f"support-{slug}-{stamp}.md"
        body = (
            f"# Duckln support bundle — {repo_name}\n\n"
            f"When: {stamp}\n\n"
            "## What went wrong (redacted)\n\n"
            f"{redact_sensitive_data((error_text or '').strip())[:2000]}\n\n"
            "## What Duckln tried\n\n"
            f"{redact_sensitive_data((tried or '').strip())[:1000]}\n\n"
            + (f"## Candidate fix (not applied)\n\n`{redact_sensitive_data(fix_command)[:300]}`\n" if fix_command else "")
        )
        path.write_text(body, encoding="utf-8")
        return path
    except Exception:
        return None


# Plan 80 Fix 9: a small curated baseline of well-known cross-repo lessons so value
# exists before learning accumulates. Keyed by an error/framework signature substring.
_SEEDED_COMMON_LESSONS: tuple[tuple[str, str, str], ...] = (
    ("err_ossl_evp_unsupported", "Old react-scripts on Node 17+ hits an OpenSSL error — set NODE_OPTIONS=--openssl-legacy-provider.", "export NODE_OPTIONS=--openssl-legacy-provider"),
    ("digital envelope routines", "OpenSSL legacy error on newer Node — set NODE_OPTIONS=--openssl-legacy-provider.", "export NODE_OPTIONS=--openssl-legacy-provider"),
)


def _common_lesson_fix(
    *, stderr: str, stdout: str, command: str, execution_target: str, config_dir: Path, understanding=None
):
    """Plan 80 Fix 9: match a learned/seeded cross-repo lesson to this failure and
    return a DeterministicFix, or None. Lessons solved on any repo apply everywhere."""
    from duckln.diagnostics import DeterministicFix, ErrorCategory

    blob = f"{stderr or ''}\n{stdout or ''}".lower()
    try:
        from state.access import read_common_lessons

        learned = read_common_lessons(config_dir)
    except Exception:
        learned = {}
    # Learned lessons: signature slug appears in the error text.
    for sig, payload in learned.items():
        needle = sig.replace("_", " ").strip()
        if needle and needle in blob and payload.get("fix"):
            return DeterministicFix(
                category=ErrorCategory.UNKNOWN,
                cause=payload.get("lesson", "A pitfall other repos hit."),
                fix_title="Apply learned fix", fix_command=payload["fix"], safety_class="S2",
                verification=None,
            )
    for needle, lesson, fix_cmd in _SEEDED_COMMON_LESSONS:
        if needle in blob and fix_cmd:
            return DeterministicFix(
                category=ErrorCategory.UNKNOWN, cause=lesson,
                fix_title="Apply known fix", fix_command=fix_cmd, safety_class="S2", verification=None,
            )
    return None


def _record_common_lesson_from_fix(*, config_dir: Path, stderr: str, command: str, fix, understanding=None) -> None:
    """Plan 80 Fix 9: persist a generalized, redacted lesson from a deterministic fix
    so a pitfall solved once is pre-empted on every future repo."""
    try:
        from state.access import write_common_lesson

        signature = getattr(fix, "category", None)
        sig = signature.value if signature is not None else "lesson"
        write_common_lesson(
            config_dir,
            signature=str(sig),
            lesson=redact_sensitive_data(getattr(fix, "cause", "") or ""),
            fix_command=redact_sensitive_data(getattr(fix, "fix_command", "") or ""),
        )
    except Exception:
        pass


def _match_failure_fix(
    *,
    stderr: str,
    stdout: str,
    exit_code: int | None,
    command: str,
    execution_target: str,
    config_dir: Path,
    understanding=None,
):
    """Plan 80 Fix 6/9: resolve a failure to a concrete fix WITHOUT the LLM —
    first via the cross-repo common-lessons store (Fix 9), then the deterministic
    rule library (Fix 6). Returns a DeterministicFix or None."""
    from duckln.diagnostics import match_deterministic_fix

    # Fix 9: consult lessons learned from other repos first.
    lesson_fix = _common_lesson_fix(
        stderr=stderr, stdout=stdout, command=command,
        execution_target=execution_target, config_dir=config_dir, understanding=understanding,
    )
    if lesson_fix is not None:
        return lesson_fix
    det = match_deterministic_fix(
        stderr=stderr, stdout=stdout, exit_code=exit_code,
        command=command, execution_target=execution_target,
    )
    if det is not None:
        # Fix 9: remember this so any future repo benefits.
        _record_common_lesson_from_fix(config_dir=config_dir, stderr=stderr, command=command, fix=det, understanding=understanding)
    return det


def _partial_cleanup_command(*, aggressive: bool = True) -> str:
    """Plan 137 + 144 F2: a marker-aware shell snippet that removes incomplete PARTIAL
    artifacts (no completion marker) left by an interrupted/killed/timed-out step — so a
    retry starts clean and the wasted space is reclaimed. NEVER removes a COMPLETE
    artifact (marker present) or repo/user data. Safe to run from the repo root.

    `aggressive=True` (the default, used for a genuine resource crunch / a precheck-detected
    half-install) ALSO removes a marker-less `node_modules`/`.venv`. `aggressive=False` (a
    generic fix re-run) removes ONLY stale build scratch — it must NOT delete a venv/
    node_modules, because the fix that's about to run often operates on that very venv
    (e.g. `pip install pyinstaller` into `backend/.venv`); deleting it first is self-
    defeating and was the cause of the venv ↔ PyInstaller oscillation."""
    # Stale PyInstaller / build scratch (workpath/distpath), root or subdir — always safe.
    scratch = "rm -rf build_cache */build_cache .codex-temp-sidecar */.codex-temp-sidecar 2>/dev/null || true"
    if not aggressive:
        return scratch
    return (
        # marker-less node_modules (an interrupted/OOM-killed install)
        'if [ -d node_modules ] && ! { [ -e node_modules/.package-lock.json ] || '
        '[ -e node_modules/.pnpm ] || [ -e node_modules/.yarn-integrity ] || '
        '[ -e node_modules/.modules.yaml ]; }; then rm -rf node_modules; fi; '
        # marker-less venv in the repo root or any immediate subdir (interrupted venv setup)
        'for d in . */; do dd="${d%/}"; if [ -d "$dd/.venv" ] && [ ! -e "$dd/.venv/.duckln-deps-ok" ]; '
        'then rm -rf "$dd/.venv"; fi; done; '
        + scratch
    )


# Plan 150 F1: the DELIBERATE fresh-install wipe — installed/generated artifacts removed
# UNCONDITIONALLY (unlike _partial_cleanup_command, which only removes marker-less partials),
# while the user's SOURCE + git + lockfiles + .env are KEPT. Run from the repo root.
_FRESH_INSTALL_WIPE_DIRS = (
    "node_modules", "*/node_modules",
    ".venv", "*/.venv", "venv", "*/venv", "env", "*/env",
    "build", "*/build", "dist", "*/dist", "target", "*/target", "src-tauri/target",
    "build_cache", "*/build_cache", ".codex-temp-sidecar", "*/.codex-temp-sidecar",
    "__pycache__", "*/__pycache__", "*.egg-info", "*/*.egg-info",
    ".pytest_cache", ".next", ".nuxt", ".turbo", ".parcel-cache", ".gradle",
)


def _fresh_install_cleanup_command() -> str:
    """Plan 150 F1: wipe everything INSTALLED/generated (deps, venvs, build outputs, caches,
    the venv completion marker) so a 'start fresh' reinstalls from scratch — while KEEPING the
    repo source, `.git`, lockfiles, and the user-filled `.env`. Repo-root-confined, `|| true`,
    never touches `..`/absolute paths/`.git`/source. This is the deliberate (unconditional)
    sibling of `_partial_cleanup_command`."""
    targets = " ".join(_FRESH_INSTALL_WIPE_DIRS)
    # also remove any leftover venv completion markers so the venv setup re-runs fully
    return (
        f"rm -rf {targets} 2>/dev/null || true; "
        "for d in . */; do dd=\"${d%/}\"; rm -f \"$dd/.venv/.duckln-deps-ok\" 2>/dev/null || true; done"
    )


def reclaim_partial_before_retry(
    *, config_dir, execution_target: str, vm_name: str | None, cwd: str | None,
    reclaim_caches: bool = True, display: Callable[[str], None] | None = None,
) -> None:
    """Plan 137 Fix 1: BEFORE re-running a step that ended mid-way (interrupt / SIGKILL /
    OOM / disk-full), remove its marker-less partial artifact so the retry starts clean.
    On a genuine resource crunch (`reclaim_caches=True`) also clear caches + apt partials
    to recover space; for a generic fix re-run (`reclaim_caches=False`) only the marker-less
    partial is removed (shared caches are kept so the retry isn't slowed). Conservative +
    best-effort (never raises)."""
    from duckln import resource_manager as rm

    # Plan 144 F2: only a genuine resource crunch (reclaim_caches=True) may delete a
    # marker-less venv/node_modules; a generic fix re-run keeps them (the fix often
    # installs INTO that venv) and clears only stale build scratch.
    combined = _partial_cleanup_command(aggressive=reclaim_caches)
    if reclaim_caches:
        combined += "; " + " && ".join(rm.reclaim_commands(execution_target=execution_target))
    wrapped, _ = _wrap_command_for_execution_target(
        config_dir=config_dir, execution_target=execution_target, command=combined,
        cwd=cwd, preferred_vm_name=vm_name,
    )
    if wrapped is None:
        return
    if display is not None:
        display("Reclaiming the partial/incomplete artifacts from the interrupted attempt before retrying…")
    try:
        ControlledCommandRunner(execution_target="local").run(
            wrapped, cwd=(cwd if execution_target == "local" else None), timeout_seconds=600.0,
        )
    except Exception:
        pass


def _handle_resource_crunch(
    *, stderr, stdout, paths, plan, understanding, display, approve, executed, fake_repo, project_dir, current,
    text_prompt=None, step=None,
):
    """Plan 111 + 135 F6: when a failure is a resource crunch (disk/RAM), AUTO-HEAL it on
    a disposable sandbox — reclaim caches + stale build scratch, resize (host-/cost-bounded),
    re-run the failed step, and RESUME (no `/plan approve`). On a local host, or a non-auto
    mode, or when it can't be grown, fall back to the honest explain-and-pause. Returns a
    RepoBringUpResult (paused), `_RECOVERY_CONTINUE` (auto-healed → resume), or None (not a
    resource issue)."""
    from duckln import resource_manager as rm
    from duckln.modes import ControlMode
    from duckln.plan_mode import mark_status
    from state.access import write_pending_plan, write_resource_event

    error_text = f"{stderr or ''}\n{stdout or ''}"
    crunch = rm.crunch_from_error(error_text)
    if crunch is None:
        return None

    et = getattr(understanding, "execution_target", "local") or "local"
    vm_name = _active_container_name(paths.config_dir) if et == "container" else _active_vm_name(paths.config_dir)
    target_label = composite_target_label(et, vm_name)
    snapshot = rm.probe_target_resources(config_dir=paths.config_dir, execution_target=et, vm_name=vm_name)
    host = rm.probe_host_resources()
    need = rm.estimate_requirement(snapshot=snapshot, error_text=error_text, heavy_build=True)
    rec = rm.recommend(crunch, snapshot=snapshot, need=need, host=host, execution_target=et, vm_name=vm_name)

    display(f"⚠ Resource crunch on {target_label}: {rec.resource} is the blocker.")
    try:
        write_resource_event(paths.config_dir, target=target_label, event=f"{crunch.resource} crunch: {rec.explanation}")
    except Exception:
        pass

    # Plan 172 F5: a remote target whose resource probe returns NOTHING is unreachable/down (e.g.
    # it powered off mid-build, the screenshot case) — report it honestly instead of "reclaiming"
    # a VM that isn't there. (disk_total_mb == 0 means df read nothing on the target.)
    if et in ("vm", "container", "aws", "gcp") and int(getattr(snapshot, "disk_total_mb", 0) or 0) == 0:
        _down = (f"The {et} target '{vm_name or target_label}' appears unreachable (it may have powered off). "
                 "Restart it via `/vm`, then `/plan continue` to resume from where it left.")
        try:
            write_pending_plan(paths.config_dir, mark_status(plan, "awaiting_user", note=_down).to_dict())
        except Exception:
            pass
        display(f"⚠ {_down}")
        return _bringup_result(fake_repo, project_dir, current.mode, executed, False, f"Plan paused: {_down}")

    # Plan 143: Duckln auto-RECLAIMS (safe) and verifies, but NEVER auto-resizes — a resize
    # changes the user's machine footprint (and can cost), so it always asks (F4).
    def _run_on_target(cmd: str, *, timeout: float = 600.0):
        wrapped, _ = _wrap_command_for_execution_target(
            config_dir=paths.config_dir, execution_target=et, command=cmd,
            cwd=str(getattr(step, "cwd", "") or "") or None, preferred_vm_name=vm_name,
        )
        if wrapped is None:
            return None
        try:
            return ControlledCommandRunner(execution_target="local").run(wrapped, timeout_seconds=timeout)
        except Exception:
            return None

    # Plan 143: how much FREE space the build actually needs (extra over the current disk),
    # used both to gate the retry and to SUGGEST a minimum resize. Default ~5 GB headroom.
    _is_disk = crunch.resource == "disk"
    needed_free_mb = max((need.disk_total_mb - snapshot.disk_total_mb) if need.disk_total_mb else 0, 5120)
    min_gb = max(1, int(math.ceil(needed_free_mb / 1024.0)))
    rec_gb = max(min_gb, (rec.recommended_mb // 1024) if rec.recommended_mb else min_gb)

    # Plan 171 F4: SURFACE the real numbers up front (used / free / total + how much MORE is
    # needed) so the user is informed BEFORE any action — not buried inside the resize prompt.
    if _is_disk:
        display(
            f"Disk on {target_label}: {rm._gb(max(0, snapshot.disk_total_mb - snapshot.disk_free_mb))} used / "
            f"{rm._gb(snapshot.disk_free_mb)} free of {rm._gb(snapshot.disk_total_mb)} "
            f"({snapshot.disk_used_pct or 0}% used) — this build needs ≈{rm._gb(needed_free_mb)} more free."
        )
    else:
        display(
            f"RAM on {target_label}: {rm._gb(snapshot.ram_mb)} total"
            + (", swap on" if snapshot.swap_on else ", no swap")
            + f" — the build was killed (out of memory); recommend ≳{rm._gb(rec.recommended_mb)}."
        )

    def _free_mb() -> int:
        try:
            s = rm.probe_target_resources(config_dir=paths.config_dir, execution_target=et, vm_name=vm_name)
            return int(getattr(s, "disk_free_mb", 0) or 0)
        except Exception:
            return 0

    def _build_output_footprint_mb() -> int:
        # Plan 172 F1: the size of the build artifacts the FAILING build REGENERATES (target/,
        # dist/, build/, node_modules) — the honest signal for how big the disk must be, whether
        # or not we delete them. Read-only `du` (no single quotes — the command is bash -lc wrapped).
        cmd = ("du -scm target */target src-tauri/target dist */dist build */build "
               "node_modules */node_modules 2>/dev/null | tail -1")
        try:
            r = _run_on_target(cmd, timeout=120.0)
            txt = (getattr(r, "stdout", "") or "").strip()
            first = txt.split()[0] if txt else ""
            return int(first) if first.isdigit() else 0
        except Exception:
            return 0

    # Plan 172 F2: does the FAILING step regenerate build output? Then reclaiming target/ then
    # rebuilding it is circular on a too-small disk — keep the cache + resize instead. Signal-keyed
    # (heavy-build OR a build/compile/bundle command), repo-agnostic; `_is_heavy_build` alone misses
    # `npm run build:all` (the cargo/tauri work is inside the npm script).
    _step_is_build = bool(
        _is_disk and step and (
            _is_heavy_build(step.command or "")
            or any(t in (step.command or "").lower() for t in ("build", "compile", "bundle"))
        )
    )

    def _retry_and_resume():
        # A run/serve step blocks synchronously — don't verify those here.
        if step is None or not step.command or _looks_like_run_command(step.command or ""):
            return None
        display("Retrying the step now that space has been freed…")
        _res = _run_on_target(step.command, timeout=5400.0)
        if _res is not None and not getattr(_res, "timed_out", False) and getattr(_res, "exit_code", 1) == 0:
            display("  ✓ resolved — the step completed. Continuing.")
            try:
                _slug = str(plan.repo_slug or getattr(fake_repo, "repo_url", "") or getattr(fake_repo, "name", "") or "repo")
                _mark_step_done(paths.config_dir, _slug, step.command or "", _load_done_steps(paths.config_dir, _slug))
            except Exception:
                pass
            # Plan 171 F8: the crunch is resolved — clear the persisted facts so a later
            # conversation turn isn't hinted with a stale "disk full" state.
            try:
                write_workflow_state(paths.config_dir, {"active_resource_crunch": None})
            except Exception:
                pass
            return _RECOVERY_CONTINUE
        return None

    def _honest_pause(extra: str = ""):
        free_gb = _free_mb() // 1024
        note = (f"{rec.resource} still too small: ~{free_gb} GB free, this build needs ≥{min_gb} GB free."
                if _is_disk else f"needs more {rec.resource}; {rec.explanation}")
        if extra:
            note = f"{note} {extra}"
        halted = mark_status(plan, "awaiting_user", note=note)
        write_pending_plan(paths.config_dir, halted.to_dict())
        display(
            f"Plan paused — {note} Recreate the VM larger via `/vm` (suggest ≥{rec_gb} GB disk), "
            "or approve a resize, then run `/plan continue` to resume."
        )
        return _bringup_result(fake_repo, project_dir, current.mode, executed, False, f"Plan paused: {note}")

    # 1) RECLAIM is automatic + safe (package caches, stale build scratch). Plan 172 F2: for a
    #    BUILD step, do NOT aggressively delete target//node_modules — the build just regenerates
    #    them (a slow full rebuild that still won't fit a too-small disk); keep the cache and
    #    resize instead (F1). Aggressive delete is the last resort only if resize is declined.
    _aggressive = bool(_is_disk and not _step_is_build)
    _free_before = _free_mb() if _is_disk else 0
    try:
        display(
            "Reclaiming package caches + stale build scratch to free space…" if not _aggressive
            else "Reclaiming caches + Rust target/ + stale build artifacts to free space…"
        )
        _run_on_target(" && ".join(rm.reclaim_commands(execution_target=et, aggressive=_aggressive)), timeout=600.0)
        reclaim_partial_before_retry(
            config_dir=paths.config_dir, execution_target=et, vm_name=vm_name,
            cwd=str(getattr(step, "cwd", "") or "") or None, reclaim_caches=False, display=None,
        )
    except Exception:
        pass
    # Plan 171 F5: MEASURE + report how much the reclaim actually freed, so the user sees the
    # delete's effect (and whether it was enough) — not a silent cleanup.
    if _is_disk:
        _free_after = _free_mb()
        _freed_mb = _free_after - _free_before
        if _freed_mb >= 1024:
            display(f"Reclaimed ~{rm._gb(_freed_mb)} — now {rm._gb(_free_after)} free of {rm._gb(snapshot.disk_total_mb)}.")
        else:
            display(f"Reclaimed caches; freed space is marginal — {rm._gb(_free_after)} free of {rm._gb(snapshot.disk_total_mb)}.")

    # Plan 172 F1: SIZE the need from the REAL build-output footprint (what the build regenerates),
    # not a flat 5 GB — so an approved resize ACTUALLY fits. Probe target//dist//build/ in place
    # (kept by F2 for a build step) or use the measured reclaimed size; then require the disk hold
    # that + headroom. This replaces the old `total + 5 GB` guess that resized to 21 GB and still ran out.
    if _is_disk:
        _regen_mb = max(_build_output_footprint_mb(), _freed_mb if _step_is_build else 0)
        if _regen_mb > 0:
            needed_free_mb = max(needed_free_mb, _regen_mb + rm._HOST_DISK_HEADROOM_MB)
            try:
                _need2 = rm.ResourceNeed(
                    ram_mb=0, disk_total_mb=snapshot.disk_total_mb + needed_free_mb,
                    reason=f"build regenerates ~{rm._gb(_regen_mb)}; grow to hold it + headroom",
                )
                rec = rm.recommend(crunch, snapshot=snapshot, need=_need2, host=host,
                                   execution_target=et, vm_name=vm_name)
            except Exception:
                pass
            min_gb = max(1, int(math.ceil(needed_free_mb / 1024.0)))
            rec_gb = max(min_gb, (rec.recommended_mb // 1024) if rec.recommended_mb else min_gb)
            display(
                f"This build regenerates ~{rm._gb(_regen_mb)} of artifacts, so the disk needs "
                f"≈{rm._gb(snapshot.disk_total_mb + needed_free_mb)} total (≥{min_gb} GB free) to finish — "
                "reclaim alone can't fit it."
            )

    # Plan 171 F8: persist the deterministic crunch FACTS so the conversation layer hands them
    # to the LLM as a HINT — the LLM owns the explanation/recommendation OVER these facts, while
    # the sensing + reclaim + resize-math + the user-gated resize remain the deterministic floor.
    # Plan 172 F7: the accelerator state (GPU/VRAM/CUDA, or MPS on Apple Silicon) so the LLM
    # reasons over the WHOLE resource matrix, not just disk — the resource_management.md SKILL
    # tells it how to use these (CUDA-required → route to a GPU target; MLX/Metal → run on local).
    try:
        if et == "local" and platform.system() == "Darwin" and platform.machine() in ("arm64", "aarch64"):
            _accel = "mps"
        elif bool(getattr(snapshot, "gpu_present", False)):
            _accel = "cuda"
        else:
            _accel = "cpu"
    except Exception:
        _accel = "cpu"
    # Plan 172 F4: a deterministic one-line GUIDANCE nudge (so even the weak model + the
    # conversation surface get an actionable hint), naming the `resource_management` SKILL a
    # capable model can load for the full reasoning.
    if _is_disk and _regen_mb > 0:
        _guidance = (
            f"Disk too small for this build: it regenerates ~{rm._gb(_regen_mb)}, so grow the disk to "
            f"~{rm._gb(snapshot.disk_total_mb + needed_free_mb)} (≥{min_gb} GB free) or recreate the VM larger — "
            "reclaim alone can't fit it. See the resource_management skill."
        )
    elif _is_disk:
        _guidance = (f"Disk crunch: ~{rm._gb(_free_after)} free of {rm._gb(snapshot.disk_total_mb)}; "
                     "reclaim caches, then resize/recreate larger if still tight.")
    else:
        _guidance = (f"Out of memory on {target_label}: add swap + single-job build, or grow RAM "
                     f"(≳{rm._gb(rec.recommended_mb)}). See the resource_management skill.")
    try:
        _crunch_facts = {
            "skill": "resource_management",
            "guidance": _guidance,
            "target": target_label,
            "resource": rec.resource,
            "disk_total_mb": snapshot.disk_total_mb,
            "disk_free_mb": (_free_after if _is_disk else snapshot.disk_free_mb),
            "disk_used_pct": snapshot.disk_used_pct,
            "ram_mb": snapshot.ram_mb,
            "swap_on": bool(getattr(snapshot, "swap_on", False)),
            "cpu_cores": snapshot.cpu_cores,
            "gpu_present": bool(getattr(snapshot, "gpu_present", False)),
            "gpu_name": getattr(snapshot, "gpu_name", "") or "",
            "vram_mb": int(getattr(snapshot, "vram_mb", 0) or 0),
            "cuda_version": getattr(snapshot, "cuda_version", "") or "",
            "accelerator": _accel,
            "build_regen_mb": (_regen_mb if _is_disk else 0),
            "needed_free_mb": (needed_free_mb if _is_disk else 0),
            "reclaimed_mb": (_freed_mb if _is_disk else 0),
            "recommended_gb": rec_gb,
            "min_gb": min_gb,
            "feasible": bool(rec.feasible),
            "host_capped": bool(rec.host_capped),
            "options": [o for o in (
                "reclaim_done",
                ("resize" if (et in ("vm", "container") and rec.feasible) else None),
                ("cloud_resize" if (et in ("aws", "gcp") and _is_disk and rec.feasible) else None),
                "recreate_larger",
                "honest_stop",
            ) if o],
        }
        write_workflow_state(paths.config_dir, {"active_resource_crunch": json.dumps(_crunch_facts)})
    except Exception:
        pass

    # 2) If reclaim alone freed enough, retry — no resize needed.
    if (not _is_disk) or _free_mb() >= needed_free_mb:
        cont = _retry_and_resume()
        if cont is not None:
            return cont

    # 3) Still tight → ASK the user to resize (NEVER auto — a resize changes their machine /
    #    can cost). SUGGEST the minimum + recommended. Reclaim already ran (free, safe).
    if et in ("vm", "container") and rec.feasible:
        display(
            f"Reclaim wasn't enough. This build needs at least ~{min_gb} GB free "
            f"(recommended {rec_gb} GB total). I won't resize without your OK."
        )
        try:
            resized = rm.apply_resize(
                rec, execution_target=et, vm_name=vm_name, approve=approve,
                prompt_value=text_prompt or getattr(current, "text_prompt", None), display=display,
            )
        except Exception:
            resized = False
        if resized:
            try:
                write_resource_event(paths.config_dir, target=target_label, event=f"resized {rec.resource} (user-approved)")
            except Exception:
                pass
            # multipass_resize_commands now grows the guest FS (Plan 143 F1). Verify + retry.
            if (not _is_disk) or _free_mb() >= needed_free_mb:
                cont = _retry_and_resume()
                if cont is not None:
                    return cont
            return _honest_pause("After the resize the filesystem still reports tight.")
        return _honest_pause("Resize declined.")

    # Plan 162 F9 (Plan 143): a CLOUD (AWS/GCP) disk crunch can grow the volume in place —
    # user-approved (cloud storage changes can cost), then verify + retry. The aws/gcloud
    # commands run on the HOST CLI (not on the target). RAM/CPU need an instance-type change
    # (a separate, larger concern) → honest pause.
    if et in ("aws", "gcp") and _is_disk and rec.feasible:
        record = None
        try:
            from state.store import initialize_state_store as _iss

            _recs = [r for r in _iss(paths.config_dir).list_managed_resources() if (r.provider or "").lower() == et]
            record = _recs[-1] if _recs else None
        except Exception:
            record = None
        if record is not None:
            from duckln.cloud_runtime import build_cloud_resize_commands

            cmds = build_cloud_resize_commands(record, resource="disk", disk_gb=rec_gb)
            if cmds:
                approved = approve(
                    f"Grow the {et} disk to ~{rec_gb} GB? Cloud storage changes can incur cost. [y/n]"
                ) if approve is not None else False
                if not approved:
                    return _honest_pause("Cloud resize declined.")
                local = ControlledCommandRunner(execution_target="local")
                ok = True
                for c in cmds:
                    try:
                        r = local.run(c, timeout_seconds=600.0)
                    except Exception:
                        ok = False
                        break
                    if getattr(r, "exit_code", 1) != 0:
                        ok = False
                        break
                if not ok:
                    return _honest_pause("The cloud resize command failed.")
                try:
                    write_resource_event(paths.config_dir, target=target_label, event="resized cloud disk (user-approved)")
                except Exception:
                    pass
                if _free_mb() >= needed_free_mb:
                    cont = _retry_and_resume()
                    if cont is not None:
                        return cont
                return _honest_pause("Grew the cloud disk; the guest filesystem may need growpart/reboot to use the new space.")

    # Local / container-disk / not feasible → honest explain-and-pause.
    if not (et in ("vm", "container") and rec.feasible):
        display(rec.explanation + (f" {rec.cost_note}" if rec.cost_note else ""))
    return _honest_pause()


# Plan 130: sentinel returned by _attempt_amendment_and_halt when reasoned recovery
# RESOLVED or SKIPPED the failed step — the executor loop continues to the next step
# instead of halting.
_RECOVERY_CONTINUE = object()

# Plan 132 B3: global per-repo reasoning budget so cost can't run away across many
# failed steps in one bring-up (keyed by repo URL; reasoning is off under unittest so
# tests never consume it).
_REASONING_BUDGETS: dict[str, object] = {}


def _reasoning_budget_for(repo) -> object:
    from duckln.recovery import ReasoningBudget

    key = str(getattr(repo, "repo_url", "") or getattr(repo, "name", "") or "default")
    bud = _REASONING_BUDGETS.get(key)
    if bud is None:
        bud = ReasoningBudget()
        _REASONING_BUDGETS[key] = bud
    return bud


# Plan 133 F2: RESUME, don't restart. Persist completed steps (keyed by command, which
# survives amendment renumbering) so a re-run (incl. after a provider switch) skips work
# already done — no re-install/re-build that fills the disk.
def _done_steps_key(repo_slug: str) -> str:
    return f"bringup.done_steps.{repo_slug}"


def _load_done_steps(config_dir, repo_slug: str) -> set[str]:
    try:
        import json as _json
        from state.access import read_config_snapshot

        raw = read_config_snapshot(config_dir).get(_done_steps_key(repo_slug))
        return set(_json.loads(raw)) if raw else set()
    except Exception:
        return set()


def _mark_step_done(config_dir, repo_slug: str, command: str, done: set[str]) -> None:
    if not command:
        return
    done.add(command)
    try:
        import json as _json
        from state.access import write_config_snapshot

        write_config_snapshot(config_dir, {_done_steps_key(repo_slug): _json.dumps(sorted(done))})
    except Exception:
        pass


def _clear_done_steps(config_dir, repo_slug: str) -> None:
    try:
        import json as _json
        from state.access import write_config_snapshot

        write_config_snapshot(config_dir, {_done_steps_key(repo_slug): _json.dumps([])})
    except Exception:
        pass


# Plan 136 F4: the amendment hard cap must count only DISTINCT genuine blockers — not the
# same failure attempted with the same fix over and over (which loops and exhausts the cap).
# Track attempted (failed_command → fix_command) signatures; a repeat halts honestly
# instead of burning another amendment slot.
def _attempted_fixes_key(repo_slug: str) -> str:
    return f"bringup.attempted_fixes.{repo_slug}"


def _failure_fix_signature(failed_command: str, fix_command: str) -> str:
    return f"{(failed_command or '').strip()}=>{(fix_command or '').strip()}"


def _was_fix_attempted(config_dir, repo_slug: str, signature: str) -> bool:
    try:
        import json as _json
        from state.access import read_config_snapshot

        raw = read_config_snapshot(config_dir).get(_attempted_fixes_key(repo_slug))
        return signature in set(_json.loads(raw)) if raw else False
    except Exception:
        return False


def _mark_fix_attempted(config_dir, repo_slug: str, signature: str) -> None:
    try:
        import json as _json
        from state.access import read_config_snapshot, write_config_snapshot

        raw = read_config_snapshot(config_dir).get(_attempted_fixes_key(repo_slug))
        seen = set(_json.loads(raw)) if raw else set()
        seen.add(signature)
        write_config_snapshot(config_dir, {_attempted_fixes_key(repo_slug): _json.dumps(sorted(seen))})
    except Exception:
        pass


def _auto_apply_recovery_fix(
    *,
    fix_command: str,
    fix_cause: str,
    failed_step,
    plan,
    paths: ConfigPaths,
    understanding,
    fake_repo,
    exec_target: str,
    vm: str | None,
    mode,
    stderr: str,
    display: Callable[[str], None],
    label: str = "recovery",
):
    """Plan 134 F2: apply a fix → re-run the failed step → verify → resume, when the
    fix is autonomy-approved AND the mode acts autonomously. Returns `_RECOVERY_CONTINUE`
    on a VERIFIED pass (plan continues), else None (caller falls back to the approval
    path). Shared by BOTH the deterministic (known-fix) and the reasoned-recovery branches
    so a recognized repo-scoped fix self-heals instead of pausing for `/plan approve`."""
    from duckln import recovery as _recovery
    from duckln.modes import ControlMode

    if not fix_command:
        return None
    # Blast-radius autonomy: repo-scoped reversible → auto on any target; system-wide →
    # auto on a sandbox / ask on local; destructive/secret → block. (Plan 129 policy.)
    if _recovery.recovery_autonomy(fix_command, execution_target=exec_target) != "auto":
        return None
    # Mode contract: only the fully-autonomous mode (HOOTLWO) acts without a prompt.
    # HITL/HOTL keep the user in the loop → fall through to the proposed amendment.
    if mode != ControlMode.HOOTLWO:
        return None

    def _run_cmd(_command: str) -> tuple[int, str]:
        _wrapped, _ = _wrap_command_for_execution_target(
            config_dir=paths.config_dir, execution_target=exec_target,
            command=_command, cwd=str(getattr(failed_step, "cwd", "") or "") or None,
            preferred_vm_name=vm,
        )
        if _wrapped is None:
            return (1, "")
        _res = ControlledCommandRunner(execution_target="local").run(_wrapped)
        return (_res.exit_code, (_res.stdout or "") + (_res.stderr or ""))

    # Plan 145 F5: build a structured reasoning EPISODE (symptom → diagnosis → fix →
    # progress → outcome) and persist it to logical-thinking.md at the end — so a recovery
    # that goes through this path leaves a real, auditable chain, not a shallow "reading
    # the error…" stub. (This is the exact path the venv↔PyInstaller screenshots hit.)
    _slug_ep = str(getattr(fake_repo, "name", "") or plan.repo_slug or getattr(fake_repo, "repo_url", "") or "repo")  # Plan 147 F2: repo name, consistent
    _err_line = next((ln.strip() for ln in reversed((stderr or "").splitlines()) if ln.strip()), "")
    _episode: list[str] = [f"Symptom: `{failed_step.command or failed_step.title}` failed."]
    if _err_line:
        _episode.append(f"Error: {_err_line[:300]}")
    _episode += [f"Diagnosis: {fix_cause}", f"Fix applied: `{fix_command}`"]

    def _write_episode(outcome: str) -> None:
        try:
            from duckln.recovery import append_thinking_log, surface_thinking_link
            _p = append_thinking_log(
                paths.config_dir, repo_slug=_slug_ep,
                title=f"{label}: {failed_step.title}", lines=list(_episode) + [f"Outcome: {outcome}"],
                surface="recover",
            )
            surface_thinking_link(_p, display)
        except Exception:
            pass

    display(f"{label} — applying + verifying: {fix_cause}")
    # Plan 137: before applying the fix + re-running the failed step, remove any marker-less
    # PARTIAL artifact the prior attempt left (so the retry is clean). Keep shared caches
    # (reclaim_caches=False) — this is a generic fix re-run, not a space crunch.
    try:
        reclaim_partial_before_retry(
            config_dir=paths.config_dir, execution_target=exec_target, vm_name=vm,
            cwd=str(getattr(failed_step, "cwd", "") or "") or None, reclaim_caches=False, display=None,
        )
    except Exception:
        pass
    def _next_stage_fix(new_output: str) -> str | None:
        # Plan 144 F1: the step still fails after the fix — but if a DIFFERENT known fix
        # matches the NEW error, the prior fix advanced the build to a later stage (real
        # progress, e.g. venv-created → now PyInstaller-missing). Chain it (same autonomy
        # gate as the entry fix), so a multi-stage failure resolves instead of being
        # mis-judged as "fix did not verify".
        try:
            from duckln.diagnostics import match_deterministic_fix as _mdf
            det2 = _mdf(stderr=new_output, command=failed_step.command or "", execution_target=exec_target)
        except Exception:
            return None
        if det2 is None or getattr(det2, "block", False):
            return None
        nxt_cmd = getattr(det2, "fix_command", "") or ""
        if not nxt_cmd or _recovery.recovery_autonomy(nxt_cmd, execution_target=exec_target) != "auto":
            return None
        _new_cause = getattr(det2, 'cause', '') or 'applying the next fix'
        display(f"  → fix made progress; new blocker surfaced: {_new_cause}")
        _episode.append(f"Progress: prior fix worked — new blocker surfaced ({_new_cause}); chaining `{nxt_cmd}`.")
        return nxt_cmd

    if not _recovery.apply_fix_and_verify(
        fix_command=fix_command, failed_command=failed_step.command or failed_step.title,
        run_cmd=_run_cmd, next_fix=_next_stage_fix,
    ):
        display("  ✗ fix did not verify — falling back to a proposed amendment.")
        _write_episode("fix did not verify — the step still fails; proposing an amendment for approval.")
        return None
    display("  ✓ fix verified — continuing.")
    _write_episode("fix applied + verified — the step now passes; continuing the plan.")
    # Resume hygiene: mark the now-passing step done so a re-run skips it (never a
    # run/serve step — those must restart each session).
    try:
        if not _looks_like_run_command(failed_step.command or ""):
            _slug = str(plan.repo_slug or getattr(fake_repo, "repo_url", "") or getattr(fake_repo, "name", "") or "repo")
            _done = _load_done_steps(paths.config_dir, _slug)
            _mark_step_done(paths.config_dir, _slug, failed_step.command or "", _done)
    except Exception:
        pass
    # Plan 132 C1/D2: LEARN the verified fix into the cross-repo lessons store + telemetry.
    try:
        from duckln.diagnostics import DeterministicFix, ErrorCategory
        _record_common_lesson_from_fix(
            config_dir=paths.config_dir, stderr=stderr, command=failed_step.command or failed_step.title,
            fix=DeterministicFix(
                category=ErrorCategory.UNKNOWN, cause=fix_cause,
                fix_title=f"Auto-applied fix: {fix_cause[:60]}", fix_command=fix_command, safety_class="S2",
            ),
            understanding=understanding,
        )
    except Exception:
        pass
    try:
        from state.access import write_session_summary_state
        write_session_summary_state(
            paths.config_dir, session_id=f"reasoning-telemetry-{fake_repo.name}",
            summary=f"- auto-recovery RESOLVED: `{failed_step.command or failed_step.title}` → fix applied + verified\n",
        )
    except Exception:
        pass
    return _RECOVERY_CONTINUE


def _attempt_amendment_and_halt(
    *,
    plan,
    paths: ConfigPaths,
    step,
    stderr: str,
    stdout: str,
    exit_code: int,
    understanding,
    llm_client,
    display: Callable[[str], None],
    current,
    approve: Callable[[str], bool] | None,
    executed: list[str],
    fake_repo,
    project_dir: Path,
    text_prompt: Callable[[str, str], str | None] | None = None,
) -> RepoBringUpResult:
    """On a failed step, run attribution. If a fix is proposed and the
    amendment cap is not exhausted, write the amended plan back to pending
    and halt. The user runs `/plan approve` again to apply the amendment."""
    from duckln.plan_mode import (
        MAX_AMENDMENTS,
        PLAN_STATUS_IRRECOVERABLE,
        AttributionResult,
        PlanStep,
        amend_plan,
        attribute_failure,
        mark_status,
    )
    from duckln.ui import render_error_attribution
    from state.access import write_pending_plan

    # Plan 145 F5: persist a structured reasoning EPISODE for THIS recovery outcome to
    # logical-thinking.md (so a block/skip/amendment never dead-ends at "reading the
    # error…"; the auto-apply path logs its own outcome episode separately).
    # Plan 147 F2: key the episode by the repo NAME (consistent with the recovery-agent
    # episode) so the log isn't a confusing path-here / clone-URL-there split for one repo.
    _repo_slug_ep = str(getattr(fake_repo, "name", "") or plan.repo_slug or getattr(fake_repo, "repo_url", "") or "repo")
    _err_line_ep = next((ln.strip() for ln in reversed((stderr or stdout or "").splitlines()) if ln.strip()), "")

    def _log_episode(title: str, outcome: str, cause: str = "") -> None:
        try:
            from duckln.recovery import append_thinking_log, surface_thinking_link
            _lines = [f"Symptom: `{step.command or step.title}` failed (exit {exit_code})."]
            if _err_line_ep:
                _lines.append(f"Error: {_err_line_ep[:300]}")
            if cause:
                _lines.append(f"Diagnosis: {cause}")
            _lines.append(f"Outcome: {outcome}")
            _p = append_thinking_log(paths.config_dir, repo_slug=_repo_slug_ep, title=title, lines=_lines, surface="recover")
            surface_thinking_link(_p, display)
        except Exception:
            pass

    _exec_target = getattr(understanding, "execution_target", "local")
    # Plan 134 F2: resolve the active sandbox name once — both the deterministic and the
    # reasoned-recovery branches feed it to `_auto_apply_recovery_fix` (apply on target).
    _vm = _active_vm_name(paths.config_dir) if _exec_target == "vm" else (
        _active_container_name(paths.config_dir) if _exec_target == "container" else None
    )

    # Plan 111: a RESOURCE crunch (disk full / OOM) is not a code bug — escalate to the
    # Resource sub-agent: explain with real numbers, offer a sensible (host-bounded,
    # cost-aware) resize, log it. This is a clean waiting_on_user PAUSE that does NOT
    # consume the amendment cap, so it never hard-caps as "irrecoverable".
    _resource_result = _handle_resource_crunch(
        stderr=stderr, stdout=stdout, paths=paths, plan=plan, understanding=understanding,
        display=display, approve=approve, executed=executed, fake_repo=fake_repo,
        project_dir=project_dir, current=current, text_prompt=text_prompt, step=step,
    )
    if _resource_result is not None:
        return _resource_result

    # Plan 145 F1: the MODEL is Duckln's reasoner — a hard-coded matcher must NEVER decide a
    # fix on its own (that's how Duckln "blindly followed" and skipped thinking). Compute the
    # deterministic match as a HINT only. In PRODUCTION (reasoning enabled) the reasoning agent
    # runs FIRST with that hint, the supervisor challenges it, and there is NO offline/
    # deterministic fallback — no model/quota → honest stop (like Claude offline). The legacy
    # deterministic decider runs ONLY when reasoning is disabled (the unit suite), for fast,
    # model-free, deterministic tests.
    from duckln.recovery import reasoning_enabled as _reasoning_enabled
    _model_is_reasoner = _reasoning_enabled()

    attribution = None
    det = _match_failure_fix(
        stderr=stderr, stdout=stdout, exit_code=exit_code,
        command=step.command or step.title, execution_target=_exec_target,
        config_dir=paths.config_dir, understanding=understanding,
    )
    # The deterministic match, surfaced to the model as a non-authoritative HINT it must
    # verify against the real repo (never trust blindly).
    _det_hint = ""
    if det is not None and not getattr(det, "block", False) and getattr(det, "fix_command", ""):
        _det_hint = (
            f"HINT (a known-pattern heuristic — verify against the REAL repo, do not trust "
            f"blindly): possible cause='{det.cause}', candidate fix=`{det.fix_command}`."
        )
    # Plan 81 Fix 7: a deterministic BLOCK (private repo / brew-missing) is an honest USER-ASK
    # guardrail (not an auto-fix), so it's kept in both modes — it only asks the user.
    if det is not None and getattr(det, "block", False):
        display(det.block_question or det.cause)
        _log_episode(f"Blocked: {step.title}", f"blocked — needs the user: {det.cause}", det.cause)
        halted = mark_status(plan, "awaiting_user", note=det.cause)
        write_pending_plan(paths.config_dir, halted.to_dict())
        return _bringup_result(
            fake_repo, project_dir, current.mode, executed, False,
            f"Plan halted: {det.cause}",
        )
    # Plan 183 F8/F9: a BASIC, recognized failure class (missing devDeps/venv/module/compiler,
    # apt-conflict, port-in-use, …) is the deterministic floor's job, NOT the model's. When the
    # known fix is repo-scoped + reversible + carries a verification, AUTO-APPLY + verify FIRST
    # (proof beats reasoning — Plan 145 F3), EVEN with a model configured, and in the FAILING
    # step's directory (dir-aware via `_auto_apply_recovery_fix`). This fixes a basic command
    # failure cheaply + model-independently and skips the expensive weak-model recovery loop
    # (the ~100k-token sink) for known classes; only an UNRECOGNIZED error reaches the model.
    if (
        det is not None
        and _model_is_reasoner
        and not getattr(det, "block", False)
        and getattr(det, "fix_command", "")
        and getattr(det, "verification", "")
    ):
        _cont_known = _auto_apply_recovery_fix(
            fix_command=det.fix_command, fix_cause=det.cause, failed_step=step,
            plan=plan, paths=paths, understanding=understanding, fake_repo=fake_repo,
            exec_target=_exec_target, vm=_vm, mode=current.mode, stderr=stderr,
            display=display, label="Known fix",
        )
        if _cont_known is not None:
            display(f"✓ Applied a known fix for a basic error ({det.fix_title}) and the step now passes.")
            return _cont_known
        # Didn't verify (or mode isn't autonomous) → fall through to the model for the real cause.

    if det is not None and not _model_is_reasoner:
        # LEGACY (reasoning disabled / unit suite only): the deterministic matcher decides.
        display(f"Known fix applies: {det.fix_title}.")
        # Plan 134 F2: a RECOGNIZED, repo-scoped, reversible fix (e.g. install PyInstaller
        # into the backend venv) must AUTO-APPLY + verify + resume — same autonomy+mode
        # policy the reasoned-recovery branch uses — instead of always pausing for
        # `/plan approve`. This is the fix for "Duckln had the right fix but stalled".
        _cont = _auto_apply_recovery_fix(
            fix_command=det.fix_command, fix_cause=det.cause, failed_step=step,
            plan=plan, paths=paths, understanding=understanding, fake_repo=fake_repo,
            exec_target=_exec_target, vm=_vm, mode=current.mode, stderr=stderr,
            display=display, label="Known fix",
        )
        if _cont is not None:
            return _cont
        fix_step = PlanStep(
            index=0, title=det.fix_title, description=det.cause,
            command=det.fix_command, safety_class=det.safety_class,
            verification=det.verification, rationale="Deterministic fix for a recognized error pattern.",
            estimated_seconds=30, confidence=0.9, origin="amendment",
            target=_exec_target, source="duckln-known-fix",
            cwd=str(getattr(step, "cwd", "") or ""),
        )
        attribution = AttributionResult(cause=det.cause, fix_step=fix_step, confidence=0.9)

    if attribution is None:
        if llm_client is None:
            display("Cannot attribute failure: no LLM provider configured. Plan halted.")
            irrecoverable = mark_status(plan, PLAN_STATUS_IRRECOVERABLE, note="no llm")
            write_pending_plan(paths.config_dir, irrecoverable.to_dict())
            return _bringup_result(
                fake_repo, project_dir, current.mode, executed, False,
                "Plan halted: failed step without LLM-driven attribution.",
            )
        # Plan 129: REASON before guessing — let the tool-using agent investigate the
        # failure (read manifest/log, list scripts, probe the toolchain) and DECIDE,
        # instead of the blind one-shot below. Runs on whatever target the bring-up uses
        # (Mac/VM/container/cloud). Degrades to the one-shot path when the agent harness
        # can't run in this context (returns None → falls through unchanged).
        try:
            from duckln import recovery as _recovery

            # Plan 142 F2: on a REMOTE target the repo lives on the VM/container/cloud, not
            # the local path — pass the runtime/target repo dir so the agent's fs/shell
            # tools (which use project_dir as the remote cwd) actually find + read the repo.
            # Without this the agent gets "Directory not found on target" and goes blind.
            _investigate_dir = project_dir
            if _exec_target in _REMOTE_EXECUTION_TARGETS:
                try:
                    _investigate_dir = Path(resolve_runtime_project_dir(
                        paths.config_dir, fake_repo, execution_target=_exec_target,
                    ))
                except Exception:
                    _investigate_dir = project_dir
            _report = _recovery.recover_failed_step_with_agent(
                config_dir=paths.config_dir, repo_name=fake_repo.name, project_dir=_investigate_dir,
                execution_target=_exec_target, vm_name=_vm,
                failed_command=step.command or step.title, stderr=stderr, stdout=stdout,
                mode=current.mode, approve=approve, llm_client=llm_client, display=display,
                budget=_reasoning_budget_for(fake_repo), hint=_det_hint,
            )
        except Exception:
            _report = None
        if _report is not None and _report.decision is _recovery.RecoveryDecision.BLOCK:
            display(_report.cause)
            # Plan 162 F8 (B4): when the block is a CAPABILITY gap (not a fixable error),
            # surface the concrete next step — connect the named tool/MCP via `/mcp`.
            _gap = _recovery.capability_gap_from_text(f"{_report.cause}\n{getattr(_report, 'reason', '') or ''}")
            if _gap:
                display(f"→ This needs a capability Duckln doesn't have yet: connect `{_gap}` with `/mcp` (or `/tools add`), then `/plan continue`.")
            _log_episode(f"Blocked: {step.title}", f"reasoned recovery blocked — needs the user: {_report.cause}", _report.cause)
            halted = mark_status(plan, "awaiting_user", note=_report.cause)
            write_pending_plan(paths.config_dir, halted.to_dict())
            return _bringup_result(
                fake_repo, project_dir, current.mode, executed, False, f"Plan halted: {_report.cause}",
            )
        # Plan 130: SKIP an optional/absent step → continue the plan (the executor
        # treats the sentinel as "this step is resolved, move on").
        if _report is not None and _report.decision is _recovery.RecoveryDecision.SKIP:
            display(f"Reasoned recovery — skipping an optional/absent step: {_report.cause}")
            _log_episode(f"Skip: {step.title}", f"skipped an optional/absent step: {_report.cause}", _report.cause)
            return _RECOVERY_CONTINUE
        if _report is not None and _report.decision is _recovery.RecoveryDecision.FIXED and _report.command:
            # Plan 130/134: ACT → VERIFY → continue when the fix is autonomy-approved and
            # the mode acts autonomously — via the SAME shared helper the deterministic
            # branch uses. On a verified pass the plan continues; otherwise we propose.
            _cont = _auto_apply_recovery_fix(
                fix_command=_report.command, fix_cause=_report.cause, failed_step=step,
                plan=plan, paths=paths, understanding=understanding, fake_repo=fake_repo,
                exec_target=_exec_target, vm=_vm, mode=current.mode, stderr=stderr,
                display=display, label="Reasoned recovery",
            )
            if _cont is not None:
                return _cont
            # autonomy 'ask'/'block', non-HOOTLWO mode, or unverified → propose for approval.
            _sc = "S3" if _recovery.classify_blast_radius(_report.command) != "repo" else "S2"
            fix_step = PlanStep(
                index=0, title=f"Reasoned recovery: {_report.cause[:80]}", description=_report.reason,
                command=_report.command, safety_class=_sc, verification="command exits 0",
                rationale="Reasoned recovery — investigated with tools, then fixed.",
                estimated_seconds=60, confidence=0.75, origin="amendment",
                target=_exec_target, source="duckln-recovery-agent",
                cwd=str(getattr(step, "cwd", "") or ""),
            )
            attribution = AttributionResult(cause=_report.cause, fix_step=fix_step, confidence=0.75)

    if attribution is None and _model_is_reasoner:
        # Plan 145 F1: NO offline/one-shot fallback in production — the model is Duckln's
        # reasoner. If it produced no evidence-backed, supervisor-accepted fix, stop HONESTLY.
        _rep = locals().get("_report")
        _cause = (getattr(_rep, "cause", "") if _rep is not None else "") or "the reasoning agent could not produce an evidence-backed fix"
        # Plan 148 F3: the tail must be MODEL-AWARE — never blame a capable model. Only a
        # genuinely weak model gets the "stronger model" note; a capable model is pointed at
        # the REAL next action (search the web — which needs internet on).
        from duckln.internet_skill import is_internet_enabled as _net_on
        from duckln.recovery import weak_model_reasoning_note as _weak_note
        try:
            _mdl = str(read_config_snapshot(paths.config_dir).get("model") or "")
        except Exception:
            _mdl = ""
        _wnote = _weak_note(_mdl)
        if _wnote:
            _tail = f" ({_wnote.lstrip('Note — ').rstrip('.')}.)"
        elif not _net_on(paths.config_dir):
            _tail = " I can look this exact error up — run `/internet on`, then `/plan continue`, so I can search the web for it."
        else:
            _tail = ""
        # Plan 162 F8 (B4): if the honest-stop cause is a missing CAPABILITY, name the
        # concrete next step (connect the tool/MCP) instead of only "fix it manually".
        _gap = _recovery.capability_gap_from_text(_cause)
        if _gap:
            _tail += f" This needs a capability Duckln lacks — connect `{_gap}` with `/mcp` (or `/tools add`), then `/plan continue`."
        # Plan 175 F1 + G3: be AWARE — surface WHAT was investigated (so the user sees Duckln
        # actually looked), and frame the next options clearly instead of a dumb dead-end.
        _investigated = ""
        try:
            _ev = (getattr(_rep, "evidence", "") if _rep is not None else "") or ""
            if _ev:
                _investigated = f" I checked: {' '.join(_ev.split())[:240]}."
        except Exception:
            _investigated = ""
        # Plan 183 F6: an ACTIONABLE honest-stop, not a dead-end. Surface the candidate command
        # and offer Continue (run it) / I'll do it manually / Cancel, instead of only a text pause.
        _candidate = (
            (det.fix_command if (det is not None and getattr(det, "fix_command", "")) else "")
            or (getattr(_rep, "command", "") if _rep is not None else "")
        ).strip()
        try:
            from duckln.interaction import propose_and_confirm as _propose
            _situation = (
                f"The step `{step.command or step.title}` failed and I couldn't reach an evidence-backed "
                f"fix — {_cause}.{_investigated}" + _tail
            )
            _rec = (f"run `{_candidate}`" if _candidate else "fix it manually")
            _decision = _propose(
                situation=_situation, recommendation=_rec,
                why="I searched what I could but couldn't confirm a fix; you decide how to proceed.",
                display=display, approve=approve, text_prompt=text_prompt,
                accept_label=(f"Continue — run `{_candidate}`" if _candidate else "Continue — re-run the step"),
            )
        except Exception:
            _decision = None
        if _decision is not None and (_decision.outcome == "accept" or _decision.outcome == "custom"):
            _run_cmd = _candidate if _decision.outcome == "accept" else str(getattr(_decision, "custom_text", "") or "").strip()
            if _run_cmd:
                _cont_f6 = _auto_apply_recovery_fix(
                    fix_command=_run_cmd, fix_cause=_cause, failed_step=step,
                    plan=plan, paths=paths, understanding=understanding, fake_repo=fake_repo,
                    exec_target=_exec_target, vm=_vm, mode=current.mode, stderr=stderr,
                    display=display, label="User-chosen fix",
                )
                if _cont_f6 is not None:
                    display("✓ Applied your chosen fix and the step now passes.")
                    return _cont_f6
                display(f"That didn't resolve it. Run `{_run_cmd}` manually, then `/plan continue`.")
        _log_episode(f"Halt: {step.title}", f"no evidence-backed fix from reasoning — pausing for the user.", _cause)
        halted = mark_status(plan, "awaiting_user", note=f"no evidence-backed fix: {_cause}")
        write_pending_plan(paths.config_dir, halted.to_dict())
        return _bringup_result(
            fake_repo, project_dir, current.mode, executed, False,
            f"Plan paused: no evidence-backed fix — {_cause}",
        )

    if attribution is None:
        attribution = attribute_failure(
            plan=plan,
            failed_step=step,
            stderr=stderr,
            stdout=stdout,
            exit_code=exit_code,
            understanding=understanding,
            llm_client=llm_client,
        )
        # Plan 110 Fix 7: reject a weak model's implausible fix (e.g. an invented
        # `--timeout` for a SIGKILL/compile/OOM crash) so we don't present a fake fix.
        if attribution.fix_step is not None and not _amendment_fix_is_plausible(
            stderr or stdout, getattr(attribution.fix_step, "command", "")
        ):
            from dataclasses import replace as _dc_replace_attr

            display("Discarding an implausible auto-fix from the model — looking for real evidence instead.")
            attribution = _dc_replace_attr(attribution, fix_step=None)

    # Plan 77 Fix 4: when local attribution can't produce a fix, search the web
    # for the exact error (if the internet skill is on) and re-attribute with that
    # evidence — so Duckln actually self-repairs instead of giving up.
    if attribution.fix_step is None:
        web_note = _web_evidence_for_failed_step(
            config_dir=paths.config_dir,
            command=step.command or step.title,
            error_text=stderr or stdout,
            execution_target=getattr(understanding, "execution_target", "local"),
            display=display,
        )
        if web_note:
            second = attribute_failure(
                plan=plan,
                failed_step=step,
                stderr=stderr,
                stdout=stdout,
                exit_code=exit_code,
                understanding=understanding,
                llm_client=llm_client,
                web_evidence=web_note,
            )
            if second.fix_step is not None:
                attribution = second
                # Plan 196 F12/F13: a WEB-DISCOVERED fix is lower-confidence and the user may be
                # non-technical — so it ALWAYS goes through a plain-English Approve/Reject gate
                # (even in HOOTLWO — this overrides the Plan-129 auto-apply for the web-fix path),
                # and the user never sees raw shell at the decision point.
                _web_fix_cmd = str(getattr(second.fix_step, "command", "") or "").strip()
                if _web_fix_cmd:
                    _plain = _translate_error_and_fix_plain(
                        stderr or stdout, _web_fix_cmd, llm_client=llm_client
                    )
                    display("🔎 Here's what I found:")
                    display(_plain)
                    display(f"I'd like to apply this fix now and check it worked (nothing gets deleted).")
                    try:
                        from duckln.interaction import propose_and_confirm as _propose_web
                        _web_decision = _propose_web(
                            situation=_plain,
                            recommendation="apply this fix and verify it worked",
                            why="I found this online; you decide whether I should apply it.",
                            display=display, approve=approve, text_prompt=text_prompt,
                            accept_label="Yes — apply the fix",
                        )
                    except Exception:
                        _web_decision = None
                    if _web_decision is not None and _web_decision.outcome == "accept":
                        _web_cont = _auto_apply_recovery_fix(
                            fix_command=_web_fix_cmd, fix_cause=attribution.cause, failed_step=step,
                            plan=plan, paths=paths, understanding=understanding, fake_repo=fake_repo,
                            exec_target=_exec_target, vm=_vm, mode=current.mode, stderr=stderr,
                            display=display, label="Web-discovered fix",
                        )
                        if _web_cont is not None:
                            display("✓ Applied the fix and the step now passes.")
                            # F13: learn the recipe into the RUNTIME store (not checked-in
                            # playbooks) so the next similar failure resolves from the fast-path.
                            try:
                                from state.access import write_common_lesson as _wcl
                                _sig = next((ln.strip() for ln in (stderr or stdout or "").splitlines() if ln.strip()), "")
                                if _sig:
                                    _wcl(paths.config_dir, signature=redact_sensitive_data(_sig)[:160],
                                         lesson=redact_sensitive_data(attribution.cause or "")[:200],
                                         fix_command=_web_fix_cmd[:200])
                            except Exception:
                                pass
                            return _web_cont
                        # Applied but didn't verify → drop the fix and fall through to honest-stop.
                        from dataclasses import replace as _dc_replace_web
                        attribution = _dc_replace_web(attribution, fix_step=None)
                    else:
                        # Rejected → honest-stop + a redacted diagnostics bundle (F13).
                        from dataclasses import replace as _dc_replace_web2
                        attribution = _dc_replace_web2(attribution, fix_step=None)
                        _bundle = _write_diagnostics_bundle(
                            paths.config_dir, repo_name=getattr(fake_repo, "name", "repo"),
                            error_text=stderr or stdout, tried=f"Searched the web; proposed a fix you declined.",
                            fix_command=_web_fix_cmd,
                        )
                        display("No problem — I'll pause here to keep your machine safe.")
                        if _bundle is not None:
                            display(f"If you'd like help, I saved a support summary (no secrets) at: {_bundle}")

    if plan.amendment_count >= MAX_AMENDMENTS:
        display(
            render_error_attribution(
                failed_step=step,
                cause=attribution.cause,
                fix_step=None,
                amendment_count=plan.amendment_count,
            )
        )
        display(
            f"Hard cap reached: {MAX_AMENDMENTS} amendments exhausted. "
            "Plan halted irrecoverably. Run `/plan reject` to clear and start over."
        )
        irrecoverable = mark_status(plan, PLAN_STATUS_IRRECOVERABLE, note="amendment cap")
        write_pending_plan(paths.config_dir, irrecoverable.to_dict())
        # Plan 73 D2-D3: learning loop on failure — record a failure-pattern
        # skill (so the next run avoids this dead-end) and a redacted reflection.
        try:
            from duckln.plan_mode import persist_reflection, record_failure_skill, reflect_on_run

            record_failure_skill(
                config_dir=paths.config_dir,
                repo_family=getattr(understanding, "repo_family", "unknown"),
                os_name=getattr(understanding, "os_name", ""),
                execution_target=getattr(understanding, "execution_target", "local"),
                failed_command=step.command or step.title,
                cause=attribution.cause,
            )
            note = reflect_on_run(
                plan=plan, executed=tuple(executed), succeeded=False,
                attribution_cause=attribution.cause, failed_command=step.command or step.title,
            )
            persist_reflection(paths.config_dir, repo_slug=plan.repo_slug, note=note)
        except Exception:
            pass
        return _bringup_result(
            fake_repo, project_dir, current.mode, executed, False,
            f"Plan halted irrecoverably after {plan.amendment_count} amendment(s).",
        )

    if attribution.fix_step is None:
        display(
            render_error_attribution(
                failed_step=step,
                cause=attribution.cause,
                fix_step=None,
                amendment_count=plan.amendment_count,
            )
        )
        try:
            from duckln.internet_skill import is_internet_enabled as _internet_on
            internet_on = bool(_internet_on(paths.config_dir))
        except Exception:
            internet_on = False
        if internet_on:
            display(
                "No automatic fix available even after a web lookup. Run `/plan reject` "
                "to abort, or fix the issue manually and run `/plan approve` to retry."
            )
        else:
            display(
                "Local attribution couldn't find a fix and the internet skill is off. "
                "Run `/internet on` so Duckln can look up this exact error, then "
                "`/plan approve` to retry — or fix it manually and `/plan approve`."
            )
        amended = mark_status(plan, "awaiting_user", note=attribution.cause)
        write_pending_plan(paths.config_dir, amended.to_dict())
        return _bringup_result(
            fake_repo, project_dir, current.mode, executed, False,
            "Plan halted: failure without a fix.",
        )

    # Plan 136 F4: if THIS exact fix was already tried for THIS command and we're back
    # here, the fix didn't resolve it — don't loop/burn another amendment slot. Halt with
    # an honest "already tried" message naming the real cause, so the cap is reserved for
    # DISTINCT genuine blockers (a self-inflicted typo auto-applies earlier and never
    # reaches here).
    _fix_slug = str(plan.repo_slug or getattr(fake_repo, "repo_url", "") or getattr(fake_repo, "name", "") or "repo")
    _fix_sig = _failure_fix_signature(step.command or step.title, getattr(attribution.fix_step, "command", "") or "")
    if _was_fix_attempted(paths.config_dir, _fix_slug, _fix_sig):
        display(
            f"I already tried `{getattr(attribution.fix_step, 'command', '')}` for this step and it "
            f"didn't resolve it. Real blocker: {attribution.cause} — this needs your input "
            "(`/plan reject` to start over, or fix it manually and `/plan continue`)."
        )
        _log_episode(
            f"Halt: {step.title}",
            f"already attempted `{getattr(attribution.fix_step, 'command', '')}`; it didn't resolve it — pausing for the user.",
            attribution.cause,
        )
        halted = mark_status(plan, "awaiting_user", note=f"repeat failure: {attribution.cause}")
        write_pending_plan(paths.config_dir, halted.to_dict())
        return _bringup_result(
            fake_repo, project_dir, current.mode, executed, False,
            f"Plan paused: already attempted this fix — {attribution.cause}",
        )
    _mark_fix_attempted(paths.config_dir, _fix_slug, _fix_sig)

    amended = amend_plan(
        plan=plan,
        fix_step=attribution.fix_step,
        before_index=step.index,
        cause=attribution.cause,
    )

    # Plan 78 Fix E: the amendment is itself supervisor-reviewed BEFORE it is
    # offered — a recovery step must clear the same bar as the original plan.
    from duckln.plan_mode import MODEL_UNREACHABLE_BLOCKER, MODEL_UNRESPONSIVE_BLOCKER, critic_review

    amend_verdict = critic_review(
        plan=amended, understanding=understanding, llm_client=llm_client,
        config_dir=paths.config_dir, model_id=str(read_config_snapshot(paths.config_dir).get("model") or ""),
    )
    if amend_verdict.external_blocker in (MODEL_UNREACHABLE_BLOCKER, MODEL_UNRESPONSIVE_BLOCKER):
        from duckln.plan_mode import model_unreachable_message

        if amend_verdict.external_blocker == MODEL_UNREACHABLE_BLOCKER:
            display(_with_context_hint(model_unreachable_message(read_config_snapshot(paths.config_dir).get("provider"))))
        else:
            display(amend_verdict.reason)
        halted = mark_status(plan, "awaiting_user", note="model could not review the amendment (unreachable/too slow)")
        write_pending_plan(paths.config_dir, halted.to_dict())
        return _bringup_result(
            fake_repo, project_dir, current.mode, executed, False,
            "Plan halted: model could not review the amendment.",
        )
    if amend_verdict.verdict == "block":
        blocker = amend_verdict.missing_question or amend_verdict.external_blocker or amend_verdict.reason
        display(f"Supervisor blocked the recovery step: {blocker}")
        halted = mark_status(plan, "awaiting_user", note=f"amendment blocked: {amend_verdict.reason}")
        write_pending_plan(paths.config_dir, halted.to_dict())
        return _bringup_result(
            fake_repo, project_dir, current.mode, executed, False,
            "Plan halted: recovery step blocked by supervisor.",
        )
    if amend_verdict.reason:
        banner = (
            f"Supervisor approved the recovery step: {amend_verdict.reason}"
            if amend_verdict.verdict == "approve"
            else f"Supervisor suggests on the recovery step: {amend_verdict.reason}"
        )
        from dataclasses import replace as _dc_replace

        amended = _dc_replace(amended, critic_reasoning=banner)

    write_pending_plan(paths.config_dir, amended.to_dict())
    display(
        render_error_attribution(
            failed_step=step,
            cause=attribution.cause,
            fix_step=attribution.fix_step,
            amendment_count=amended.amendment_count,
        )
    )
    # Plan 175 G3 / 176 F3: be AWARE, not a dumb dead-end — use the `aware_interaction` SKILL +
    # the deterministic FACTS (the matched fix) to VOICE the situation + best approach + why (LLM
    # when present, deterministic floor otherwise), then offer apply / skip / redirect. The actual
    # primitive (`build_failure_proposal`) is used here, not an inline string.
    _g3_fix = getattr(attribution.fix_step, "command", "") or ""
    if _g3_fix:
        import types as _g3_types

        from duckln.interaction import build_failure_proposal as _g3_build
        _g3_sit, _g3_rec, _g3_why = _g3_build(
            failed_command=step.command or step.title,
            error_text=stderr or stdout,
            repo_name=str(getattr(fake_repo, "name", "") or ""),
            det_fix=_g3_types.SimpleNamespace(fix_command=_g3_fix, cause=attribution.cause),
            llm_client=llm_client,
        )
        display(
            f"{_g3_sit} Best approach: {_g3_rec}" + (f" — {_g3_why}" if _g3_why else "") + ". "
            "Reply 'yes' (or `/plan continue`) to apply it, 'no' (or `/plan reject`) to skip, "
            "or tell me what you'd prefer instead."
        )
    _log_episode(
        f"Amendment proposed: {step.title}",
        f"proposed a recovery step (`{getattr(attribution.fix_step, 'command', '')}`) for your approval.",
        attribution.cause,
    )
    return _bringup_result(
        fake_repo, project_dir, current.mode, executed, False,
        f"Plan amended (#{amended.amendment_count}); run `/plan approve` to apply.",
    )


def _bringup_result(
    repo,
    project_dir: Path,
    mode,
    executed: list[str],
    succeeded: bool,
    message: str,
) -> RepoBringUpResult:
    return RepoBringUpResult(
        repo=repo,
        project_dir=project_dir,
        detected_files=(),
        mode=mode,
        executed_commands=tuple(executed),
        verification_passed=succeeded,
        message=message,
        should_offer_repair=False,
        message_already_displayed=True,
    )
