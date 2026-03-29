"""Repo bring-up foundation for selected cached repositories."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shlex
from typing import Callable

from duckln.config import ConfigPaths
from duckln.modes import ControlMode, evaluate_mode_action
from duckln.safety import assess_command
from duckln.shell import ControlledCommandRunner
from state.repo_catalog import RepoCatalogRecord
from state.store import initialize_state_store


SETUP_FILE_ORDER = (
    "README.md",
    "requirements.txt",
    "pyproject.toml",
    "setup.py",
    "environment.yml",
    "Dockerfile",
    "Makefile",
)


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


def resolve_managed_project_dir(config_dir: Path, repo: RepoCatalogRecord) -> Path:
    """Return the managed local project path for a selected repository."""

    return config_dir / "projects" / _slugify(repo.name)


def inspect_repo_setup_files(project_dir: Path) -> tuple[str, ...]:
    """Return the setup-related files Duckln should consider."""

    return tuple(file_name for file_name in SETUP_FILE_ORDER if (project_dir / file_name).exists())


def infer_repo_setup_plan(repo: RepoCatalogRecord, project_dir: Path) -> RepoBringUpPlan:
    """Infer a bounded safe setup plan from the cloned repository files."""

    detected_files = inspect_repo_setup_files(project_dir)
    steps: list[RepoBringUpStep] = []

    if "requirements.txt" in detected_files:
        steps.extend(
            (
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
            )
        )
    elif "pyproject.toml" in detected_files or "setup.py" in detected_files:
        steps.extend(
            (
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
            )
        )
    elif "environment.yml" in detected_files:
        steps.append(
            RepoBringUpStep(
                purpose="Create conda environment",
                command="conda env create -f environment.yml",
                verification_command="test -f environment.yml",
            )
        )
    elif "Dockerfile" in detected_files:
        steps.append(
            RepoBringUpStep(
                purpose="Build container image",
                command=f"docker build -t {_slugify(repo.name)} .",
                verification_command="test -f Dockerfile",
            )
        )
    elif "Makefile" in detected_files:
        make_target = _preferred_make_target(project_dir / "Makefile")
        if make_target is not None:
            steps.append(
                RepoBringUpStep(
                    purpose=f"Run make {make_target}",
                    command=f"make {make_target}",
                    verification_command="test -f Makefile",
                )
            )

    if steps:
        summary = f"Detected {', '.join(detected_files)} and inferred {len(steps)} setup step(s)."
    elif detected_files:
        summary = f"Detected {', '.join(detected_files)} but no safe automatic setup step was inferred yet."
    else:
        summary = "No known setup files were detected, so Duckln cannot infer a safe setup step yet."

    return RepoBringUpPlan(
        repo=repo,
        project_dir=project_dir,
        detected_files=detected_files,
        steps=tuple(steps),
        summary=summary,
    )


def bring_up_selected_repo(
    repo: RepoCatalogRecord,
    current_mode: ControlMode,
    paths: ConfigPaths,
    *,
    runner: ControlledCommandRunner | None = None,
    approve: Callable[[str], bool] | None = None,
    display: Callable[[str], None] = print,
) -> RepoBringUpResult:
    """Clone, inspect, and present or execute the minimal bring-up path."""

    project_dir = resolve_managed_project_dir(paths.config_dir, repo)
    executed_commands: list[str] = []
    verification_passed = False
    runner_instance = runner or ControlledCommandRunner()

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
            )

        project_dir.parent.mkdir(parents=True, exist_ok=True)
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
            )

    plan = infer_repo_setup_plan(repo, project_dir)
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
        )

    for step in plan.steps:
        decision = _mode_decision_for_step(current_mode, step.command, approve=approve, prompt=step.purpose)
        if not decision.allowed:
            message = f"Stopped before '{step.purpose}' because approval is still required."
            display(message)
            _record_repo_state(paths, repo, project_dir, status="awaiting_approval", summary=message)
            return RepoBringUpResult(
                repo=repo,
                project_dir=project_dir,
                detected_files=plan.detected_files,
                mode=current_mode,
                executed_commands=tuple(executed_commands),
                verification_passed=verification_passed,
                message=message,
            )

        result = runner_instance.run(step.command, cwd=str(project_dir))
        executed_commands.append(step.command)
        if result.exit_code != 0 or result.timed_out:
            message = _command_failure_message(f"{step.purpose} failed", result.stderr)
            display(message)
            _record_repo_state(paths, repo, project_dir, status="setup_failed", summary=message)
            return RepoBringUpResult(
                repo=repo,
                project_dir=project_dir,
                detected_files=plan.detected_files,
                mode=current_mode,
                executed_commands=tuple(executed_commands),
                verification_passed=False,
                message=message,
            )

        if step.verification_command is not None:
            verification_passed = _run_verification(
                runner_instance,
                step.verification_command,
                cwd=str(project_dir),
                display=display,
            )
            if not verification_passed:
                message = f"Verification failed after '{step.purpose}'."
                display(message)
                _record_repo_state(paths, repo, project_dir, status="verification_failed", summary=message)
                return RepoBringUpResult(
                    repo=repo,
                    project_dir=project_dir,
                    detected_files=plan.detected_files,
                    mode=current_mode,
                    executed_commands=tuple(executed_commands),
                    verification_passed=False,
                    message=message,
                )

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


def _command_failure_message(prefix: str, stderr: str) -> str:
    detail = " ".join(stderr.split()) if stderr.strip() else "No stderr output."
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
        summary=summary,
    )


def _slugify(value: str) -> str:
    return "".join(character if character.isalnum() or character in "._-" else "-" for character in value.strip()).strip(
        "-._"
    ) or "repo"
