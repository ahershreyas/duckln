"""Repo bring-up foundation for selected cached repositories."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
import os
import platform
from pathlib import Path
import re
import shutil
import shlex
from typing import Callable

from agent.probe import GpuProbeState, SystemProbe
from duckln.config import ConfigPaths
from duckln.diagnostics import redact_sensitive_data
from duckln.modes import ControlMode, evaluate_mode_action
from duckln.safety import assess_command
from duckln.shell import ControlledCommandRunner
from state.access import write_session_summary_state
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
    "Cargo.toml",
    "CMakeLists.txt",
    "configure",
    "docker-compose.yml",
    "docker-compose.yaml",
    "Dockerfile",
    "Makefile",
)
PLAYBOOK_DIR = Path(__file__).resolve().parents[1] / "agent" / "playbooks"


class RepoFamily(str, Enum):
    """Repo families used by the bring-up supervisor."""

    PYTHON = "python"
    CPP_NATIVE = "cpp_native"
    NODE_TYPESCRIPT = "node_typescript"
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


@dataclass(frozen=True)
class RepoBringUpStep:
    """A single inferred repo bring-up step."""

    purpose: str
    command: str
    verification_command: str | None = None


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


@dataclass(frozen=True)
class RepoBringUpInspection:
    """Repo metadata, setup files, and local environment signals."""

    repo: RepoCatalogRecord
    project_dir: Path
    detected_files: tuple[str, ...]
    readme_excerpt: str
    system_probe: SystemProbe
    runtime_provider: str | None = None
    execution_target: str = "local"


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

    def build_plan(self, inspection: RepoBringUpInspection, repo_family: RepoFamily) -> RepoBringUpPlan:
        steps = self.infer_steps(inspection, repo_family)
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
        if "requirements.txt" in detected_files:
            return [
                RepoBringUpStep(
                    purpose="Create project virtualenv",
                    command="python -m venv .venv",
                    verification_command="test -x .venv/bin/python",
                ),
                RepoBringUpStep(
                    purpose="Install requirements",
                    command=".venv/bin/python -m pip install -r requirements.txt",
                    verification_command=".venv/bin/python -m pip --version",
                ),
            ]
        if "pyproject.toml" in detected_files or "setup.py" in detected_files:
            return [
                RepoBringUpStep(
                    purpose="Create project virtualenv",
                    command="python -m venv .venv",
                    verification_command="test -x .venv/bin/python",
                ),
                RepoBringUpStep(
                    purpose="Install editable package",
                    command=".venv/bin/python -m pip install -e .",
                    verification_command=".venv/bin/python -m pip --version",
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
        if "CMakeLists.txt" in detected_files:
            return [
                RepoBringUpStep(
                    purpose="Configure native build",
                    command="cmake -S . -B build",
                    verification_command="test -d build",
                ),
                RepoBringUpStep(
                    purpose="Build native target",
                    command="cmake --build build",
                    verification_command="test -d build",
                ),
            ]
        if "Cargo.toml" in detected_files:
            return [
                RepoBringUpStep(
                    purpose="Build Rust/native crate",
                    command="cargo build",
                    verification_command="test -d target",
                )
            ]
        if "configure" in detected_files:
            return [
                RepoBringUpStep(
                    purpose="Configure native source tree",
                    command="./configure",
                    verification_command="test -f Makefile",
                ),
                *_infer_make_steps(inspection.project_dir, detected_files),
            ]
        return _infer_make_steps(inspection.project_dir, detected_files)


class NodeTypescriptRepoSetupSpecialist(RepoSetupSpecialist):
    """Node/TypeScript specialist with package-manager-first setup."""

    specialist_name = "node-typescript"
    playbook_file_name = "node_typescript.md"

    def infer_steps(self, inspection: RepoBringUpInspection, repo_family: RepoFamily) -> list[RepoBringUpStep]:
        detected_files = inspection.detected_files
        if "package.json" in detected_files:
            install_command, lockfile = _node_install_command(detected_files)
            verify_path = lockfile or "package.json"
            steps = [
                RepoBringUpStep(
                    purpose="Install Node dependencies",
                    command=install_command,
                    verification_command=f"test -f {verify_path}",
                )
            ]
            if repo_family is RepoFamily.MULTI_SERVICE:
                compose_file = "docker-compose.yml" if "docker-compose.yml" in detected_files else "docker-compose.yaml"
                if compose_file in detected_files:
                    steps.append(
                        RepoBringUpStep(
                            purpose="Verify compose definition",
                            command=f"test -f {compose_file}",
                            verification_command=f"test -f {compose_file}",
                        )
                    )
            return steps
        if "Dockerfile" in detected_files:
            return [
                RepoBringUpStep(
                    purpose="Build container image",
                    command=f"docker build -t {_slugify(inspection.repo.name)} .",
                    verification_command="test -f Dockerfile",
                )
            ]
        return _infer_make_steps(inspection.project_dir, detected_files)


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
        if "Dockerfile" in inspection.detected_files and inspection.execution_target == "vm":
            return [
                RepoBringUpStep(
                    purpose="Verify VM container recipe",
                    command="test -f Dockerfile",
                    verification_command="test -f Dockerfile",
                )
            ]
        return []


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
        error_text = " ".join(
            value
            for value in (
                failed_command,
                verification_failure,
                error_output,
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
        if any(signal in error_text for signal in ("connection refused", "failed to connect", "server is not running", "service unavailable")):
            return FailureType.SERVICE_NOT_RUNNING
        if any(signal in error_text for signal in ("ollama", "model not found", "no such model", "runtime missing")) or "ollama" in repo_text:
            return FailureType.MODEL_RUNTIME_MISSING
        if any(signal in error_text for signal in ("npm: command not found", "pnpm: command not found", "yarn: command not found", "node: command not found")):
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
        if "command not found" in error_text or "not recognized as an internal or external command" in error_text:
            return FailureType.COMMAND_NOT_FOUND
        if any(signal in error_text for signal in ("could not find a version", "no matching distribution", "failed building wheel", "dependency resolution", "npm err!", "pnpm err!", "yarn error")):
            return FailureType.DEPENDENCY_INSTALL_FAILURE
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

    def __init__(self) -> None:
        self._python_specialist = PythonRepoSetupSpecialist()
        self._cpp_specialist = CppNativeRepoSetupSpecialist()
        self._node_specialist = NodeTypescriptRepoSetupSpecialist()
        self._audio_specialist = AudioRepoSetupSpecialist()
        self._diffusion_specialist = DiffusionRepoSetupSpecialist()
        self._vm_specialist = VmEnvironmentRepoSetupSpecialist()
        self._provider_specialist = ProviderRoutingRepoSetupSpecialist()
        self._debug_recovery_specialist = DebugRecoverySpecialist()

    def plan_repo_bringup(
        self,
        repo: RepoCatalogRecord,
        project_dir: Path,
        *,
        system_probe: SystemProbe | None = None,
        runtime_provider: str | None = None,
        execution_target: str = "local",
    ) -> RepoBringUpPlan:
        inspection = inspect_repo_for_bringup(
            repo,
            project_dir,
            system_probe=system_probe,
            runtime_provider=runtime_provider,
            execution_target=execution_target,
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
                rerouted_plan = specialist.build_plan(inspection, recovery.reroute_repo_family)
                return _attach_recovery_summary(rerouted_plan, recovery)
            return RepoBringUpPlan(
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
            )
        specialist = self._select_specialist(inspection, repo_family)
        return specialist.build_plan(inspection, repo_family)

    def assess_failed_bringup(
        self,
        *,
        plan: RepoBringUpPlan,
        failed_command: str,
        verification_failure: str,
        error_output: str,
        system_probe: SystemProbe | None = None,
    ) -> DebugRecoveryAssessment:
        inspection = inspect_repo_for_bringup(
            plan.repo,
            plan.project_dir,
            system_probe=system_probe,
            runtime_provider=plan.runtime_provider,
            execution_target=plan.execution_target,
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
        if repo_family is RepoFamily.CPP_NATIVE:
            return self._cpp_specialist
        if repo_family is RepoFamily.NODE_TYPESCRIPT:
            return self._node_specialist
        if repo_family is RepoFamily.AUDIO:
            return self._audio_specialist
        if repo_family is RepoFamily.DIFFUSION_HEAVY:
            return self._diffusion_specialist
        if repo_family is RepoFamily.VM_ENVIRONMENT:
            return self._vm_specialist
        if repo_family is RepoFamily.PROVIDER_ROUTING:
            return self._provider_specialist
        if repo_family is RepoFamily.MULTI_SERVICE:
            if "package.json" in inspection.detected_files:
                return self._node_specialist
            if any(file_name in inspection.detected_files for file_name in ("CMakeLists.txt", "Cargo.toml", "configure")):
                return self._cpp_specialist
        return self._python_specialist


def resolve_managed_project_dir(config_dir: Path, repo: RepoCatalogRecord) -> Path:
    """Return the managed local project path for a selected repository."""

    return config_dir / "projects" / _slugify(repo.name)


def inspect_repo_setup_files(project_dir: Path) -> tuple[str, ...]:
    """Return the setup-related files Duckln should consider."""

    return tuple(file_name for file_name in SETUP_FILE_ORDER if (project_dir / file_name).exists())


def inspect_repo_for_bringup(
    repo: RepoCatalogRecord,
    project_dir: Path,
    *,
    system_probe: SystemProbe | None = None,
    runtime_provider: str | None = None,
    execution_target: str = "local",
) -> RepoBringUpInspection:
    """Inspect repo files and environment signals for supervisor routing."""

    return RepoBringUpInspection(
        repo=repo,
        project_dir=project_dir,
        detected_files=inspect_repo_setup_files(project_dir),
        readme_excerpt=_read_readme_excerpt(project_dir / "README.md"),
        system_probe=system_probe or _safe_probe_system(),
        runtime_provider=runtime_provider,
        execution_target=execution_target,
    )


def classify_repo_family(inspection: RepoBringUpInspection) -> RepoFamily:
    """Classify the repo family before specialist routing."""

    detected_files = set(inspection.detected_files)
    searchable_text = " ".join(
        (
            inspection.repo.name,
            inspection.repo.description,
            inspection.repo.category,
            inspection.repo.framework,
            inspection.execution_target,
            inspection.readme_excerpt,
            " ".join(inspection.detected_files),
        )
    ).lower()

    if inspection.execution_target == "vm":
        return RepoFamily.VM_ENVIRONMENT
    if inspection.runtime_provider == "ollama":
        return RepoFamily.PROVIDER_ROUTING
    if "ollama" in searchable_text:
        return RepoFamily.PROVIDER_ROUTING
    if any(signal in searchable_text for signal in ("openrouter", "openai api", "anthropic", "openai-compatible", "openai compatible")):
        return RepoFamily.PROVIDER_ROUTING
    if detected_files & {"docker-compose.yml", "docker-compose.yaml"}:
        return RepoFamily.MULTI_SERVICE
    if any(signal in searchable_text for signal in ("stable diffusion", "diffusion", "comfyui", "invokeai", "txt2img")):
        return RepoFamily.DIFFUSION_HEAVY
    if any(signal in searchable_text for signal in ("tts", "speech", "whisper", "audio", "voice", "coqui")):
        return RepoFamily.AUDIO
    if detected_files & {"package.json", "pnpm-lock.yaml", "yarn.lock", "package-lock.json"}:
        return RepoFamily.NODE_TYPESCRIPT
    if detected_files & {"CMakeLists.txt", "Cargo.toml", "configure"}:
        return RepoFamily.CPP_NATIVE
    if any(signal in searchable_text for signal in ("typescript", "node.js", "nodejs", "next.js", "vite")):
        return RepoFamily.NODE_TYPESCRIPT
    if any(signal in searchable_text for signal in ("c++", "cmake", "native runtime", "llama.cpp", "ggml", "rust")):
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
) -> RepoBringUpPlan:
    """Infer a bounded safe setup plan from the cloned repository files."""

    supervisor_agent = supervisor or RepoBringUpSupervisor()
    return supervisor_agent.plan_repo_bringup(
        repo,
        project_dir,
        system_probe=system_probe,
        runtime_provider=runtime_provider,
        execution_target=execution_target,
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
) -> RepoBringUpResult:
    """Clone, inspect, and present or execute the minimal bring-up path."""

    project_dir = resolve_managed_project_dir(paths.config_dir, repo)
    executed_commands: list[str] = []
    verification_passed = False
    runner_instance = runner or ControlledCommandRunner()
    supervisor_agent = supervisor or RepoBringUpSupervisor()

    if not project_dir.exists():
        clone_command = _build_clone_command(repo, project_dir)
        clone_decision = _mode_decision_for_step(current_mode, clone_command, approve=approve, prompt="Clone repository")
        if not clone_decision.allowed:
            message = "Repo clone suggested only. Approve or run the clone step to continue bring-up."
            display(message)
            _record_repo_state(paths, repo, project_dir, status="pending", summary=message)
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
            )

        project_dir.parent.mkdir(parents=True, exist_ok=True)
        display("Cloning the repository into Duckln’s managed workspace...")
        clone_result = runner_instance.run(clone_command, cwd=str(project_dir.parent))
        executed_commands.append(clone_command)
        if clone_result.exit_code != 0 or clone_result.timed_out:
            message = _command_failure_message("Clone failed", clone_result.stderr)
            display(message)
            _record_repo_state(paths, repo, project_dir, status="clone_failed", summary=message)
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
            )

        verification_passed = _run_verification(
            runner_instance,
            "test -d .",
            cwd=str(project_dir),
            display=display,
        )
        if not verification_passed:
            message = "Clone verification failed: managed project directory is not ready."
            display(message)
            _record_repo_state(paths, repo, project_dir, status="clone_verification_failed", summary=message)
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
            )

    display("Inspecting setup files and routing to a specialist...")
    plan = infer_repo_setup_plan(
        repo,
        project_dir,
        supervisor=supervisor_agent,
        runtime_provider=runtime_provider,
        execution_target=execution_target,
    )
    display(plan.summary)

    if current_mode is ControlMode.HITL:
        for step in plan.steps:
            display(f"{step.purpose}: {step.command}")
        _record_repo_state(paths, repo, project_dir, status="suggested", summary=plan.summary)
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
        )

    execution_result = _execute_plan_with_bounded_recovery(
        plan=plan,
        current_mode=current_mode,
        paths=paths,
        runner=runner_instance,
        supervisor=supervisor_agent,
        approve=approve,
        display=display,
        executed_commands=executed_commands,
        verification_passed=verification_passed,
    )
    if execution_result is not None:
        return execution_result

    final_message = "Repo bring-up foundation completed."
    display(final_message)
    _record_repo_state(paths, repo, project_dir, status="ready", summary=final_message)
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
    )


def _build_clone_command(repo: RepoCatalogRecord, project_dir: Path) -> str:
    return f"git clone --depth 1 {shlex.quote(repo.repo_url)} {shlex.quote(str(project_dir))}"


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


def _node_install_command(detected_files: tuple[str, ...]) -> tuple[str, str | None]:
    if "pnpm-lock.yaml" in detected_files:
        return "pnpm install --frozen-lockfile", "pnpm-lock.yaml"
    if "yarn.lock" in detected_files:
        return "yarn install --frozen-lockfile", "yarn.lock"
    if "package-lock.json" in detected_files:
        return "npm ci", "package-lock.json"
    return "npm install", None


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
    if detected_files & {"CMakeLists.txt", "Cargo.toml", "configure"} and current_family is not RepoFamily.CPP_NATIVE:
        return RepoFamily.CPP_NATIVE
    if detected_files & {"requirements.txt", "pyproject.toml", "setup.py", "environment.yml"} and current_family is not RepoFamily.PYTHON:
        return RepoFamily.PYTHON
    return None


def _mode_decision_for_step(
    mode: ControlMode,
    command: str,
    *,
    approve: Callable[[str], bool] | None,
    prompt: str,
):
    assessment = assess_command(command)
    decision = evaluate_mode_action(mode, assessment, is_ai_suggested=True)
    if decision.allowed:
        return decision
    if not decision.requires_approval:
        return decision

    approved = False if approve is None else approve(f"{prompt}: {command}")
    return evaluate_mode_action(mode, assessment, is_ai_suggested=True, user_approved=approved)


def _run_verification(
    runner: ControlledCommandRunner,
    command: str,
    *,
    cwd: str,
    display: Callable[[str], None],
) -> bool:
    result = runner.run(command, cwd=cwd)
    if result.exit_code == 0 and not result.timed_out:
        return True
    display(_command_failure_message("Verification failed", result.stderr))
    return False


def _execute_plan_with_bounded_recovery(
    *,
    plan: RepoBringUpPlan,
    current_mode: ControlMode,
    paths: ConfigPaths,
    runner: ControlledCommandRunner,
    supervisor: RepoBringUpSupervisor,
    approve: Callable[[str], bool] | None,
    display: Callable[[str], None],
    executed_commands: list[str],
    verification_passed: bool,
) -> RepoBringUpResult | None:
    active_plan = plan
    recovery_used = False
    last_recovery: DebugRecoveryAssessment | None = None

    while True:
        for step in active_plan.steps:
            decision = _mode_decision_for_step(current_mode, step.command, approve=approve, prompt=step.purpose)
            if not decision.allowed:
                message = f"Stopped before '{step.purpose}' because approval is still required."
                display(message)
                _record_repo_state(paths, active_plan.repo, active_plan.project_dir, status="awaiting_approval", summary=message)
                return RepoBringUpResult(
                    repo=active_plan.repo,
                    project_dir=active_plan.project_dir,
                    detected_files=active_plan.detected_files,
                    mode=current_mode,
                    executed_commands=tuple(executed_commands),
                    verification_passed=verification_passed,
                    message=message,
                    repo_family=active_plan.repo_family,
                    specialist_name=active_plan.specialist_name,
                )

            display(f"Working on: {step.purpose}...")
            result = runner.run(step.command, cwd=str(active_plan.project_dir))
            executed_commands.append(step.command)
            if result.exit_code != 0 or result.timed_out:
                failure_message = _command_failure_message(f"{step.purpose} failed", result.stderr)
                if recovery_used:
                    message = (
                        failure_message
                        if last_recovery is None
                        else f"{failure_message} {last_recovery.summary}"
                    )
                    display(message)
                    _record_repo_state(paths, active_plan.repo, active_plan.project_dir, status="setup_failed", summary=message)
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
                    )
                active_plan, last_recovery, terminal_result = _apply_recovery_decision(
                    plan=active_plan,
                    failed_step=step,
                    failed_command=step.command,
                    failure_message=failure_message,
                    verification_failure=f"{step.purpose} command failed.",
                    current_mode=current_mode,
                    paths=paths,
                    supervisor=supervisor,
                    display=display,
                    executed_commands=executed_commands,
                )
                recovery_used = True
                if terminal_result is not None:
                    return terminal_result
                break

            if step.verification_command is not None:
                verification_passed = _run_verification(
                    runner,
                    step.verification_command,
                    cwd=str(active_plan.project_dir),
                    display=display,
                )
                if not verification_passed:
                    failure_message = f"Verification failed after '{step.purpose}'."
                    if recovery_used:
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
                        )
                    active_plan, last_recovery, terminal_result = _apply_recovery_decision(
                        plan=active_plan,
                        failed_step=step,
                        failed_command=step.verification_command,
                        failure_message=failure_message,
                        verification_failure=failure_message,
                        current_mode=current_mode,
                        paths=paths,
                        supervisor=supervisor,
                        display=display,
                        executed_commands=executed_commands,
                    )
                    recovery_used = True
                    if terminal_result is not None:
                        return terminal_result
                    break
        else:
            return None


def _apply_recovery_decision(
    *,
    plan: RepoBringUpPlan,
    failed_step: RepoBringUpStep,
    failed_command: str,
    failure_message: str,
    verification_failure: str,
    current_mode: ControlMode,
    paths: ConfigPaths,
    supervisor: RepoBringUpSupervisor,
    display: Callable[[str], None],
    executed_commands: list[str],
) -> tuple[RepoBringUpPlan, DebugRecoveryAssessment, RepoBringUpResult | None]:
    display("Checking a recovery route for the setup failure...")
    recovery = supervisor.assess_failed_bringup(
        plan=plan,
        failed_command=failed_command,
        verification_failure=verification_failure,
        error_output=failure_message,
    )
    recovery_message = f"{failure_message} {recovery.summary}"
    _record_recovery_memory(paths, plan.repo, recovery)

    if recovery.decision is RecoveryDecision.RETRY_SAME_SPECIALIST:
        retry_plan = _retry_current_specialist_plan(plan, failed_step=failed_step, recovery=recovery)
        display(retry_plan.summary)
        return retry_plan, recovery, None

    if recovery.decision is RecoveryDecision.REROUTE_TO_OTHER_SPECIALIST and recovery.reroute_repo_family is not None:
        rerouted_inspection = inspect_repo_for_bringup(
            plan.repo,
            plan.project_dir,
            runtime_provider=plan.runtime_provider,
            execution_target=plan.execution_target,
        )
        rerouted_specialist = supervisor._select_specialist(rerouted_inspection, recovery.reroute_repo_family)
        rerouted_plan = _attach_recovery_summary(
            rerouted_specialist.build_plan(rerouted_inspection, recovery.reroute_repo_family),
            recovery,
        )
        display(rerouted_plan.summary)
        return rerouted_plan, recovery, None

    display(recovery_message)
    status = "setup_blocked" if recovery.decision is RecoveryDecision.REQUEST_MISSING_PREREQUISITE else "setup_failed"
    _record_repo_state(paths, plan.repo, plan.project_dir, status=status, summary=recovery_message)
    return plan, recovery, RepoBringUpResult(
        repo=plan.repo,
        project_dir=plan.project_dir,
        detected_files=plan.detected_files,
        mode=current_mode,
        executed_commands=tuple(executed_commands),
        verification_passed=False,
        message=recovery_message,
        repo_family=plan.repo_family,
        specialist_name=plan.specialist_name,
        recovery_decision=recovery.decision,
        failure_type=recovery.failure_type,
    )


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
            command="python -m venv .venv",
            verification_command="test -x .venv/bin/python",
        )
    if (
        recovery.failure_type is FailureType.DEPENDENCY_INSTALL_FAILURE
        and failed_step.command.startswith(".venv/bin/python -m pip install")
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
    )


def _command_failure_message(prefix: str, stderr: str) -> str:
    detail = " ".join(stderr.split()) if stderr.strip() else "No stderr output."
    detail = redact_sensitive_data(detail)
    return f"{prefix}: {detail}"


def _record_repo_state(
    paths: ConfigPaths,
    repo: RepoCatalogRecord,
    project_dir: Path,
    *,
    status: str,
    summary: str,
) -> None:
    initialize_state_store(paths.config_dir).upsert_repo_state(
        repo_key=repo.repo_url,
        repo_path=str(project_dir),
        repo_url=repo.repo_url,
        status=status,
        summary=redact_sensitive_data(summary),
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


def _validate_steps_against_playbook(
    steps: list[RepoBringUpStep],
    playbook_path: Path,
) -> None:
    playbook_text = playbook_path.read_text(encoding="utf-8").lower().strip()
    if not playbook_text:
        raise ValueError(f"Playbook {playbook_path.name} is empty.")
    for step in steps:
        command_token = Path(shlex.split(step.command)[0]).name.lstrip("./").lower()
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
