"""Shared agent context, memory retrieval, and repo knowledge grounding."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from agent.probe import SystemProbe
from duckln.config import AppConfig
from state.access import (
    initialize_managed_memory_state,
    materialize_managed_memory_state,
    read_config_snapshot,
    read_followup_state,
    read_workflow_state,
)
from state.repo_catalog import RepoCatalogRecord
from state.store import RepoKnowledgeRecord, initialize_state_store


TARGET_REMOTE_REPO_FILES = (
    "README.md",
    "requirements.txt",
    "pyproject.toml",
    "setup.py",
    "environment.yml",
    "package.json",
    "go.mod",
    "Cargo.toml",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
    "Makefile",
    ".env.example",
    ".env.template",
    ".env.sample",
    "manage.py",
    "main.go",
)
REMOTE_FETCH_TIMEOUT_SECONDS = 5.0
FRESH_KNOWLEDGE_TTL_HOURS = 24
MAX_REMOTE_CONTENT_CHARS = 8000
RECOMMENDATION_MEMORY_KEY = "session:repo-recommendation"
RECOMMENDATION_MEMORY_PATH = "sessions/repo_recommendation.json"


def _normalize_source_repo_url(value: object) -> str | None:
    text = str(value or "").strip()
    if not text or text.startswith("linked://"):
        return None
    return text


def _session_summary_records(store) -> list[str]:
    summaries: list[str] = []
    for record in store.list_managed_memory_records():
        if record.memory_kind != "session":
            continue
        if record.memory_key == RECOMMENDATION_MEMORY_KEY:
            continue
        if str(record.relative_path or "").endswith(".json"):
            continue
        content = record.content.strip()
        if content:
            summaries.append(content)
    return summaries


@dataclass(frozen=True)
class MemoryContext:
    """Small bounded memory bundle used by all agent paths."""

    config_snapshot: dict[str, str]
    session_summary: str | None
    project_summary: str | None
    repo_memory: tuple[str, ...]
    recommendation_context: dict[str, object] | None = None
    promoted_heuristics: tuple[dict[str, object], ...] = ()
    followup_state: dict[str, object] | None = None


@dataclass(frozen=True)
class RepoStateSnapshot:
    """Tracked repo lifecycle state retrieved separately from repo knowledge."""

    repo_key: str | None
    repo_name: str
    status: str
    source_repo_url: str | None
    install_location: str | None
    access_hint: str | None
    run_command: str | None
    verify_command: str | None = None
    manual_command: str | None = None
    runtime_kind: str | None = None
    removal_hint: str | None = None
    last_run_result: str | None = None
    last_blocker: str | None = None
    execution_target: str = "local"
    vm_name: str | None = None
    docker_name: str | None = None
    cloud_resource_key: str | None = None
    cloud_vendor: str | None = None
    cloud_region: str | None = None
    cloud_shape: str | None = None
    managed_by_duckln: bool = True
    active_flag: bool = False
    stack_family: str | None = None
    auth_requirements: tuple[dict[str, object], ...] = ()
    missing_auth_variables: tuple[str, ...] = ()


@dataclass(frozen=True)
class RepoInventoryEntry:
    """One tracked repo entry for lifecycle inventory answers."""

    repo_name: str
    status: str
    install_location: str | None
    source_repo_url: str | None = None
    execution_target: str = "local"
    vm_name: str | None = None
    docker_name: str | None = None
    cloud_vendor: str | None = None
    cloud_region: str | None = None
    managed_by_duckln: bool = True
    active_flag: bool = False
    created_at: str | None = None
    updated_at: str | None = None
    last_verified_at: str | None = None


@dataclass(frozen=True)
class LiveRepoSessionEntry:
    """One live repo runtime session tracked separately from prepared inventory."""

    repo_key: str | None
    repo_name: str
    status: str
    install_location: str | None
    execution_target: str = "local"
    vm_name: str | None = None
    docker_name: str | None = None
    cloud_vendor: str | None = None
    cloud_region: str | None = None
    attach_hint: str | None = None
    log_path: str | None = None
    pid: int | None = None
    managed_by_duckln: bool = True


@dataclass(frozen=True)
class ThreadSummarySnapshot:
    """Recent thread-level summary retrieved separately from durable repo state."""

    session_summary: str | None
    followup_state: dict[str, object] | None


@dataclass(frozen=True)
class SessionProjectSummary:
    """Short session and project summaries loaded without bundling extra memory."""

    session_summary: str | None
    project_summary: str | None


@dataclass(frozen=True)
class WorkflowStateSnapshot:
    """Shared workflow state used to prioritize the current repo issue over stale threads."""

    repo_key: str | None
    repo_name: str | None
    tracked_status: str | None
    install_location: str | None
    run_command: str | None
    access_hint: str | None
    execution_target: str | None
    vm_name: str | None
    last_run_result: str | None
    last_blocker: str | None
    active_issue_kind: str | None
    active_issue_summary: str | None
    active_objective_id: str | None = None
    active_objective_kind: str | None = None
    active_objective_repo_key: str | None = None
    active_objective_repo_name: str | None = None
    active_objective_status: str | None = None
    active_objective_goal: str | None = None
    active_objective_execution_target: str | None = None
    active_objective_runtime_command: str | None = None
    active_objective_last_blocker: str | None = None
    active_objective_attempt_count: int | None = None
    active_objective_max_attempts: int | None = None
    active_objective_requires_user_decision: bool = False
    active_objective_resume_hint: str | None = None
    active_objective_started_at: str | None = None
    active_objective_updated_at: str | None = None
    active_incident_category: str | None = None
    active_incident_summary: str | None = None
    active_repair_phase: str | None = None
    active_runtime_status: str | None = None
    active_runtime_command: str | None = None
    active_runtime_command_kind: str | None = None
    active_runtime_repo_key: str | None = None
    active_runtime_repo_name: str | None = None
    active_runtime_cwd: str | None = None
    active_runtime_pid: int | None = None
    active_runtime_log_path: str | None = None
    active_runtime_execution_target: str | None = None
    active_runtime_vm_name: str | None = None
    active_runtime_attach_hint: str | None = None
    active_runtime_logs_hint: str | None = None
    active_runtime_stop_hint: str | None = None
    active_runtime_stop_command: str | None = None
    active_runtime_docker_name: str | None = None
    active_runtime_cloud_resource_key: str | None = None
    active_runtime_cloud_vendor: str | None = None
    active_runtime_cloud_region: str | None = None
    active_runtime_cloud_shape: str | None = None
    pending_destructive_action: str | None = None
    pending_destructive_repo_key: str | None = None
    pending_destructive_repo_name: str | None = None
    pending_destructive_path: str | None = None
    pending_destructive_target: str | None = None
    last_confirmation_outcome: str | None = None
    # Plan 171 F8: JSON facts of the latest resource crunch (used/free/needed/reclaimed +
    # options) — a HINT for the LLM voice; deterministic sensing/reclaim/resize stay the floor.
    active_resource_crunch: str | None = None


@dataclass(frozen=True)
class RepoKnowledgeContext:
    """Normalized repo knowledge for one repo from cache/local/remote sources."""

    repo: RepoCatalogRecord
    source: str
    summary: str
    setup_complexity: str | None
    local_vm_recommendation: str | None
    cpu_profile: str | None
    ram_profile: str | None
    gpu_profile: str | None
    apple_silicon_notes: str | None
    required_tools: tuple[str, ...]
    setup_files: tuple[str, ...]
    entrypoints: tuple[str, ...]
    stack_family: str | None
    runtime_style: str | None
    auth_requirements: tuple["RepoAuthRequirement", ...]
    freshness_checked_at: str
    metadata: dict[str, object]
    local_project_dir: Path | None = None


@dataclass(frozen=True)
class RepoAuthRequirement:
    """One documented auth prerequisite Duckln detected from repo evidence."""

    env_var: str
    provider: str
    reason: str
    source: str
    required: bool = False


@dataclass(frozen=True)
class AgentContext:
    """One normalized context contract for conversation and bring-up."""

    current: AppConfig | None
    system_probe: SystemProbe
    memory: MemoryContext
    repo_knowledge: RepoKnowledgeContext | None


@dataclass(frozen=True)
class HardwareReasoningSummary:
    """Normalized hardware-fit reasoning shared across supervisor decisions."""

    compute_path: str
    repo_preference: str
    practical_effect: str
    usable_ram_gib: float | None
    minimum_recommended_ram_gib: float | None
    comfortable_ram_gib: float | None
    summary: str


@dataclass(frozen=True)
class TrustEvidenceSummary:
    """Concise trust evidence markers for supervisor-facing responses."""

    detected: str
    inferred: str
    verified: str | None
    summary: str


@dataclass(frozen=True)
class RepoFeasibilityAssessment:
    """Canonical feasibility contract shared across conversation and preflight."""

    fit_label: str
    practical_effect: str
    compute_path: str
    repo_preference: str
    usable_ram_gib: float | None
    lower_bound_ram_gib: float | None
    comfortable_ram_gib: float | None
    failure_modes: tuple[str, ...]
    rationale: str
    trust: TrustEvidenceSummary


RemoteRepoFetcher = Callable[[RepoCatalogRecord], dict[str, str]]


class AgentContextService:
    """Build shared memory + repo knowledge context and persist concise learnings."""

    def __init__(self, *, remote_fetcher: RemoteRepoFetcher | None = None) -> None:
        self._remote_fetcher = remote_fetcher or _fetch_targeted_remote_repo_files

    def build_memory_context(
        self,
        *,
        config_dir: Path | None,
        repo_key: str | None = None,
    ) -> MemoryContext:
        if config_dir is None:
            return MemoryContext(config_snapshot={}, session_summary=None, project_summary=None, repo_memory=())

        config_snapshot = read_config_snapshot(config_dir)
        store = initialize_state_store(config_dir)
        latest_repo_state = store.get_latest_repo_state()
        project_summary = latest_repo_state.summary if latest_repo_state is not None else None
        repo_memory = self.load_repo_memory_notes(config_dir=config_dir, repo_key=repo_key)
        thread_summary = self.load_recent_thread_summary(config_dir=config_dir)
        recommendation_context = self.load_recommendation_memory(config_dir=config_dir)
        heuristics = tuple(
            {
                "family": record.family,
                "subject_key": record.subject_key,
                "summary": record.summary,
                "confidence": record.confidence,
                "metadata": record.metadata,
            }
            for record in store.list_promoted_heuristics()
        )
        return MemoryContext(
            config_snapshot=config_snapshot,
            session_summary=thread_summary.session_summary,
            project_summary=project_summary,
            repo_memory=repo_memory,
            recommendation_context=recommendation_context,
            promoted_heuristics=heuristics,
            followup_state=thread_summary.followup_state,
        )

    def load_recommendation_memory(
        self,
        *,
        config_dir: Path | None,
    ) -> dict[str, object] | None:
        """Load durable recommendation memory without bundling unrelated context."""

        if config_dir is None:
            return None
        return _read_recommendation_context(initialize_state_store(config_dir))

    def load_repo_memory_notes(
        self,
        *,
        config_dir: Path | None,
        repo_key: str | None,
    ) -> tuple[str, ...]:
        """Load only the durable repo-note summaries for the requested repo."""

        if config_dir is None or not repo_key:
            return ()
        store = initialize_state_store(config_dir)
        record = store.get_repo_knowledge(repo_key)
        if record is None:
            return ()
        return (record.summary,)

    def load_recent_thread_summary(
        self,
        *,
        config_dir: Path | None,
    ) -> ThreadSummarySnapshot:
        """Load only recent thread summary state, separate from repo and recommendation memory."""

        if config_dir is None:
            return ThreadSummarySnapshot(session_summary=None, followup_state=None)
        store = initialize_state_store(config_dir)
        session_summaries = _session_summary_records(store)
        followup_state = read_followup_state(config_dir)
        return ThreadSummarySnapshot(
            session_summary=session_summaries[-1] if session_summaries else None,
            followup_state=followup_state or None,
        )

    def load_session_project_summary(
        self,
        *,
        config_dir: Path | None,
    ) -> SessionProjectSummary:
        """Load only the session summary and latest project summary."""

        if config_dir is None:
            return SessionProjectSummary(session_summary=None, project_summary=None)
        store = initialize_state_store(config_dir)
        session_summaries = _session_summary_records(store)
        latest_repo_state = store.get_latest_repo_state()
        return SessionProjectSummary(
            session_summary=session_summaries[-1] if session_summaries else None,
            project_summary=latest_repo_state.summary if latest_repo_state is not None else None,
        )

    def load_promoted_heuristics(
        self,
        *,
        config_dir: Path | None,
    ) -> tuple[dict[str, object], ...]:
        """Load promoted heuristics separately from other memory payloads."""

        if config_dir is None:
            return ()
        store = initialize_state_store(config_dir)
        return tuple(
            {
                "family": record.family,
                "subject_key": record.subject_key,
                "summary": record.summary,
                "confidence": record.confidence,
                "metadata": record.metadata,
            }
            for record in store.list_promoted_heuristics()
        )

    def load_repo_state_snapshot(
        self,
        *,
        config_dir: Path | None,
        repo: RepoCatalogRecord | None,
        execution_target: str | None = None,
        vm_name: str | None = None,
    ) -> RepoStateSnapshot | None:
        """Load tracked repo lifecycle state separately from repo knowledge and thread memory."""

        if config_dir is None or repo is None:
            return None
        rows = initialize_state_store(config_dir).list_repo_states()
        for row in rows:
            if execution_target is not None and row.execution_target != execution_target:
                continue
            if vm_name is not None and row.vm_name != vm_name:
                continue
            repo_name = str(row.metadata.get("repo_name") or _repo_name_from_state_row(row)).strip()
            repo_url = str(row.repo_url or row.repo_key or "").strip()
            if repo_name.lower() == repo.name.lower() or (repo.repo_url and repo.repo_url.lower() == repo_url.lower()):
                source_repo_url = _normalize_source_repo_url(row.metadata.get("source_repo_url") or row.repo_url)
                location = row.metadata.get("install_location") or row.repo_path
                install_location = location.strip() if isinstance(location, str) and location.strip() else None
                access_hint = row.metadata.get("access_hint")
                run_command = row.metadata.get("run_command")
                verify_command = row.metadata.get("verify_command")
                manual_command = row.metadata.get("manual_command")
                runtime_kind = row.metadata.get("runtime_kind")
                removal_hint = row.metadata.get("removal_hint")
                last_run_result = row.metadata.get("last_run_result")
                last_blocker = row.metadata.get("last_blocker")
                stack_family = row.metadata.get("stack_family")
                auth_requirements = row.metadata.get("auth_requirements")
                missing_auth_variables = row.metadata.get("missing_auth_variables")
                return RepoStateSnapshot(
                    repo_key=row.repo_key,
                    repo_name=repo_name,
                    status=row.status,
                    source_repo_url=source_repo_url,
                    install_location=install_location,
                    access_hint=access_hint.strip() if isinstance(access_hint, str) and access_hint.strip() else None,
                    run_command=run_command.strip() if isinstance(run_command, str) and run_command.strip() else None,
                    verify_command=verify_command.strip() if isinstance(verify_command, str) and verify_command.strip() else None,
                    manual_command=manual_command.strip() if isinstance(manual_command, str) and manual_command.strip() else None,
                    runtime_kind=runtime_kind.strip() if isinstance(runtime_kind, str) and runtime_kind.strip() else None,
                    removal_hint=removal_hint.strip() if isinstance(removal_hint, str) and removal_hint.strip() else None,
                    last_run_result=last_run_result.strip() if isinstance(last_run_result, str) and last_run_result.strip() else None,
                    last_blocker=last_blocker.strip() if isinstance(last_blocker, str) and last_blocker.strip() else None,
                    execution_target=row.execution_target,
                    vm_name=row.vm_name,
                    docker_name=(
                        str(row.metadata.get("docker_name") or row.metadata.get("last_docker_name") or "").strip() or None
                    ),
                    cloud_resource_key=(
                        str(row.metadata.get("cloud_resource_key") or "").strip() or None
                    ),
                    cloud_vendor=(
                        str(row.metadata.get("cloud_vendor") or "").strip() or None
                    ),
                    cloud_region=(
                        str(row.metadata.get("cloud_region") or row.metadata.get("region") or "").strip() or None
                    ),
                    cloud_shape=(
                        str(row.metadata.get("cloud_shape") or "").strip() or None
                    ),
                    managed_by_duckln=row.managed_by_duckln,
                    active_flag=row.active_flag,
                    stack_family=stack_family.strip() if isinstance(stack_family, str) and stack_family.strip() else None,
                    auth_requirements=tuple(item for item in auth_requirements if isinstance(item, dict)) if isinstance(auth_requirements, list) else (),
                    missing_auth_variables=tuple(
                        str(item).strip() for item in missing_auth_variables if str(item).strip()
                    ) if isinstance(missing_auth_variables, list) else (),
                )
        return None

    def load_repo_inventory_snapshot(
        self,
        *,
        config_dir: Path | None,
        execution_target: str | None = None,
        vm_name: str | None = None,
    ) -> tuple[RepoInventoryEntry, ...]:
        """Load tracked repo inventory separately from recommendation memory and thread context."""

        if config_dir is None:
            return ()
        rows = initialize_state_store(config_dir).list_repo_states()
        seen: set[str] = set()
        inventory: list[RepoInventoryEntry] = []
        for row in rows:
            if execution_target is not None and row.execution_target != execution_target:
                continue
            if vm_name is not None and row.vm_name != vm_name:
                continue
            repo_name = str(row.metadata.get("repo_name") or _repo_name_from_state_row(row)).strip()
            if not repo_name or repo_name.lower() in seen:
                continue
            seen.add(repo_name.lower())
            location = row.metadata.get("install_location") or row.repo_path
            install_location = location.strip() if isinstance(location, str) and location.strip() else None
            inventory.append(
                RepoInventoryEntry(
                    repo_name=repo_name,
                    status=row.status,
                    install_location=install_location,
                    source_repo_url=_normalize_source_repo_url(row.metadata.get("source_repo_url") or row.repo_url),
                    execution_target=row.execution_target,
                    vm_name=row.vm_name,
                    docker_name=(
                        str(row.metadata.get("docker_name") or row.metadata.get("last_docker_name") or "").strip() or None
                    ),
                    cloud_vendor=(
                        str(row.metadata.get("cloud_vendor") or "").strip() or None
                    ),
                    cloud_region=(
                        str(row.metadata.get("cloud_region") or "").strip() or None
                    ),
                    managed_by_duckln=row.managed_by_duckln,
                    active_flag=row.active_flag,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                    last_verified_at=row.last_verified_at,
                )
            )
        return tuple(inventory)

    def load_active_repo_snapshot(
        self,
        *,
        config_dir: Path | None,
        execution_target: str | None = None,
        vm_name: str | None = None,
    ) -> RepoStateSnapshot | None:
        """Load the latest tracked repo state as the active repo snapshot."""

        if config_dir is None:
            return None
        latest: object | None = None
        for row in initialize_state_store(config_dir).list_repo_states():
            if execution_target is not None and row.execution_target != execution_target:
                continue
            if vm_name is not None and row.vm_name != vm_name:
                continue
            latest = row
            break
        if latest is None:
            return None
        repo_name = str(latest.metadata.get("repo_name") or _repo_name_from_state_row(latest)).strip()
        if not repo_name:
            return None
        source_repo_url = _normalize_source_repo_url(latest.metadata.get("source_repo_url") or latest.repo_url)
        location = latest.metadata.get("install_location") or latest.repo_path
        install_location = location.strip() if isinstance(location, str) and location.strip() else None
        access_hint = latest.metadata.get("access_hint")
        run_command = latest.metadata.get("run_command")
        verify_command = latest.metadata.get("verify_command")
        manual_command = latest.metadata.get("manual_command")
        runtime_kind = latest.metadata.get("runtime_kind")
        removal_hint = latest.metadata.get("removal_hint")
        last_run_result = latest.metadata.get("last_run_result")
        last_blocker = latest.metadata.get("last_blocker")
        stack_family = latest.metadata.get("stack_family")
        auth_requirements = latest.metadata.get("auth_requirements")
        missing_auth_variables = latest.metadata.get("missing_auth_variables")
        return RepoStateSnapshot(
            repo_key=latest.repo_key,
            repo_name=repo_name,
            status=latest.status,
            source_repo_url=source_repo_url,
            install_location=install_location,
            access_hint=access_hint.strip() if isinstance(access_hint, str) and access_hint.strip() else None,
            run_command=run_command.strip() if isinstance(run_command, str) and run_command.strip() else None,
            verify_command=verify_command.strip() if isinstance(verify_command, str) and verify_command.strip() else None,
            manual_command=manual_command.strip() if isinstance(manual_command, str) and manual_command.strip() else None,
            runtime_kind=runtime_kind.strip() if isinstance(runtime_kind, str) and runtime_kind.strip() else None,
            removal_hint=removal_hint.strip() if isinstance(removal_hint, str) and removal_hint.strip() else None,
            last_run_result=last_run_result.strip() if isinstance(last_run_result, str) and last_run_result.strip() else None,
            last_blocker=last_blocker.strip() if isinstance(last_blocker, str) and last_blocker.strip() else None,
            execution_target=latest.execution_target,
            vm_name=latest.vm_name,
            docker_name=(
                str(latest.metadata.get("docker_name") or latest.metadata.get("last_docker_name") or "").strip() or None
            ),
            cloud_resource_key=(
                str(latest.metadata.get("cloud_resource_key") or "").strip() or None
            ),
            cloud_vendor=(
                str(latest.metadata.get("cloud_vendor") or "").strip() or None
            ),
            cloud_region=(
                str(latest.metadata.get("cloud_region") or latest.metadata.get("region") or "").strip() or None
            ),
            cloud_shape=(
                str(latest.metadata.get("cloud_shape") or "").strip() or None
            ),
            managed_by_duckln=latest.managed_by_duckln,
            active_flag=latest.active_flag,
            stack_family=stack_family.strip() if isinstance(stack_family, str) and stack_family.strip() else None,
            auth_requirements=tuple(item for item in auth_requirements if isinstance(item, dict)) if isinstance(auth_requirements, list) else (),
            missing_auth_variables=tuple(
                str(item).strip() for item in missing_auth_variables if str(item).strip()
            ) if isinstance(missing_auth_variables, list) else (),
        )

    def load_live_repo_sessions_snapshot(
        self,
        *,
        config_dir: Path | None,
        execution_target: str | None = None,
        vm_name: str | None = None,
    ) -> tuple[LiveRepoSessionEntry, ...]:
        """Load only live runtime sessions, distinct from tracked/prepared repo inventory."""

        if config_dir is None:
            return ()
        sessions: list[LiveRepoSessionEntry] = []
        seen: set[str] = set()
        store = initialize_state_store(config_dir)
        for row in store.list_repo_states():
            if row.status not in {"running", "interactive"}:
                continue
            if execution_target is not None and row.execution_target != execution_target:
                continue
            if vm_name is not None and row.vm_name != vm_name:
                continue
            repo_name = str(row.metadata.get("repo_name") or _repo_name_from_state_row(row)).strip()
            if not repo_name:
                continue
            dedupe_key = str(row.repo_key or row.repo_url or repo_name).strip().lower()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            location = row.metadata.get("install_location") or row.repo_path
            install_location = location.strip() if isinstance(location, str) and location.strip() else None
            log_path = str(row.metadata.get("last_log_path") or row.metadata.get("log_path") or "").strip() or None
            sessions.append(
                LiveRepoSessionEntry(
                    repo_key=row.repo_key,
                    repo_name=repo_name,
                    status=row.status,
                    install_location=install_location,
                    execution_target=row.execution_target,
                    vm_name=row.vm_name,
                    docker_name=(str(row.metadata.get("docker_name") or row.metadata.get("last_docker_name") or "").strip() or None),
                    cloud_vendor=(str(row.metadata.get("cloud_vendor") or "").strip() or None),
                    cloud_region=(str(row.metadata.get("cloud_region") or row.metadata.get("region") or "").strip() or None),
                    attach_hint=(str(row.metadata.get("access_hint") or "").strip() or None),
                    log_path=log_path,
                    pid=(int(row.metadata.get("pid")) if isinstance(row.metadata.get("pid"), int) else None),
                    managed_by_duckln=row.managed_by_duckln,
                )
            )

        explicit = read_workflow_state(config_dir)
        runtime_status = str(explicit.get("active_runtime_status") or "").strip() or None
        runtime_repo_name = str(explicit.get("active_runtime_repo_name") or "").strip() or None
        runtime_repo_key = str(explicit.get("active_runtime_repo_key") or "").strip() or None
        runtime_target = str(explicit.get("active_runtime_execution_target") or "").strip() or None
        runtime_vm_name = str(explicit.get("active_runtime_vm_name") or "").strip() or None
        if runtime_status in {"running", "interactive"} and runtime_repo_name:
            if execution_target is None or runtime_target == execution_target:
                if vm_name is None or runtime_vm_name == vm_name:
                    dedupe_key = (runtime_repo_key or runtime_repo_name).lower()
                    if dedupe_key not in seen:
                        sessions.append(
                            LiveRepoSessionEntry(
                                repo_key=runtime_repo_key or None,
                                repo_name=runtime_repo_name,
                                status=runtime_status,
                                install_location=str(explicit.get("active_runtime_cwd") or "").strip() or None,
                                execution_target=runtime_target or "local",
                                vm_name=runtime_vm_name or None,
                                docker_name=str(explicit.get("active_runtime_docker_name") or "").strip() or None,
                                cloud_vendor=str(explicit.get("active_runtime_cloud_vendor") or "").strip() or None,
                                cloud_region=str(explicit.get("active_runtime_cloud_region") or "").strip() or None,
                                attach_hint=str(explicit.get("active_runtime_attach_hint") or "").strip() or None,
                                log_path=str(explicit.get("active_runtime_log_path") or "").strip() or None,
                                pid=(int(explicit.get("active_runtime_pid")) if isinstance(explicit.get("active_runtime_pid"), int) else None),
                            )
                        )
        return tuple(sessions)

    def load_workflow_state(
        self,
        *,
        config_dir: Path | None,
        active_repo_snapshot: RepoStateSnapshot | None = None,
    ) -> WorkflowStateSnapshot | None:
        """Load shared workflow state from tracked repo state plus explicit pending confirmation state."""

        if config_dir is None:
            return None
        explicit = read_workflow_state(config_dir)
        active = active_repo_snapshot or self.load_active_repo_snapshot(config_dir=config_dir)
        if active is None and not explicit:
            return None
        repo_key = str(explicit.get("active_repo_key") or (active.repo_key if active is not None else "")).strip() or None
        repo_name = str(explicit.get("active_repo_name") or (active.repo_name if active is not None else "")).strip() or None
        tracked_status = active.status if active is not None else None
        install_location = active.install_location if active is not None else None
        run_command = active.run_command if active is not None else None
        access_hint = active.access_hint if active is not None else None
        execution_target = active.execution_target if active is not None else None
        vm_name = active.vm_name if active is not None else None
        last_run_result = active.last_run_result if active is not None else None
        last_blocker = active.last_blocker if active is not None else None
        active_issue_kind = str(explicit.get("active_issue_kind") or "").strip() or None
        active_issue_summary = str(explicit.get("active_issue_summary") or "").strip() or None
        if active_issue_kind is None and active is not None:
            if active.last_run_result in {"failed", "missing_command"} or active.status in {"failed", "run_blocked"}:
                active_issue_kind = "run_issue"
                active_issue_summary = active.last_blocker
        runtime_pid_value = explicit.get("active_runtime_pid")
        runtime_pid = int(runtime_pid_value) if isinstance(runtime_pid_value, int | float) else None
        objective_attempt_value = explicit.get("active_objective_attempt_count")
        objective_attempt_count = int(objective_attempt_value) if isinstance(objective_attempt_value, int | float) else None
        objective_max_value = explicit.get("active_objective_max_attempts")
        objective_max_attempts = int(objective_max_value) if isinstance(objective_max_value, int | float) else None
        return WorkflowStateSnapshot(
            repo_key=repo_key,
            repo_name=repo_name,
            tracked_status=tracked_status,
            install_location=install_location,
            run_command=run_command,
            access_hint=access_hint,
            execution_target=execution_target,
            vm_name=vm_name,
            last_run_result=last_run_result,
            last_blocker=last_blocker,
            active_issue_kind=active_issue_kind,
            active_issue_summary=active_issue_summary,
            active_objective_id=str(explicit.get("active_objective_id") or "").strip() or None,
            active_objective_kind=str(explicit.get("active_objective_kind") or "").strip() or None,
            active_objective_repo_key=str(explicit.get("active_objective_repo_key") or "").strip() or None,
            active_objective_repo_name=str(explicit.get("active_objective_repo_name") or "").strip() or None,
            active_objective_status=str(explicit.get("active_objective_status") or "").strip() or None,
            active_objective_goal=str(explicit.get("active_objective_goal") or "").strip() or None,
            active_objective_execution_target=str(explicit.get("active_objective_execution_target") or "").strip() or None,
            active_objective_runtime_command=str(explicit.get("active_objective_runtime_command") or "").strip() or None,
            active_objective_last_blocker=str(explicit.get("active_objective_last_blocker") or "").strip() or None,
            active_objective_attempt_count=objective_attempt_count,
            active_objective_max_attempts=objective_max_attempts,
            active_objective_requires_user_decision=bool(explicit.get("active_objective_requires_user_decision")),
            active_objective_resume_hint=str(explicit.get("active_objective_resume_hint") or "").strip() or None,
            active_objective_started_at=str(explicit.get("active_objective_started_at") or "").strip() or None,
            active_objective_updated_at=str(explicit.get("active_objective_updated_at") or "").strip() or None,
            active_incident_category=str(explicit.get("active_incident_category") or "").strip() or None,
            active_incident_summary=str(explicit.get("active_incident_summary") or "").strip() or None,
            active_repair_phase=str(explicit.get("active_repair_phase") or "").strip() or None,
            active_runtime_status=str(explicit.get("active_runtime_status") or "").strip() or None,
            active_runtime_command=str(explicit.get("active_runtime_command") or "").strip() or None,
            active_runtime_command_kind=str(explicit.get("active_runtime_command_kind") or "").strip() or None,
            active_runtime_repo_key=str(explicit.get("active_runtime_repo_key") or "").strip() or None,
            active_runtime_repo_name=str(explicit.get("active_runtime_repo_name") or "").strip() or None,
            active_runtime_cwd=str(explicit.get("active_runtime_cwd") or "").strip() or None,
            active_runtime_pid=runtime_pid,
            active_runtime_log_path=str(explicit.get("active_runtime_log_path") or "").strip() or None,
            active_runtime_execution_target=str(explicit.get("active_runtime_execution_target") or "").strip() or None,
            active_runtime_vm_name=str(explicit.get("active_runtime_vm_name") or "").strip() or None,
            active_runtime_attach_hint=str(explicit.get("active_runtime_attach_hint") or "").strip() or None,
            active_runtime_logs_hint=str(explicit.get("active_runtime_logs_hint") or "").strip() or None,
            active_runtime_stop_hint=str(explicit.get("active_runtime_stop_hint") or "").strip() or None,
            active_runtime_stop_command=str(explicit.get("active_runtime_stop_command") or "").strip() or None,
            active_runtime_docker_name=str(explicit.get("active_runtime_docker_name") or "").strip() or None,
            active_runtime_cloud_resource_key=str(explicit.get("active_runtime_cloud_resource_key") or "").strip() or None,
            active_runtime_cloud_vendor=str(explicit.get("active_runtime_cloud_vendor") or "").strip() or None,
            active_runtime_cloud_region=str(explicit.get("active_runtime_cloud_region") or "").strip() or None,
            active_runtime_cloud_shape=str(explicit.get("active_runtime_cloud_shape") or "").strip() or None,
            pending_destructive_action=str(explicit.get("pending_destructive_action") or "").strip() or None,
            pending_destructive_repo_key=str(explicit.get("pending_destructive_repo_key") or "").strip() or None,
            pending_destructive_repo_name=str(explicit.get("pending_destructive_repo_name") or "").strip() or None,
            pending_destructive_path=str(explicit.get("pending_destructive_path") or "").strip() or None,
            pending_destructive_target=str(explicit.get("pending_destructive_target") or "").strip() or None,
            last_confirmation_outcome=str(explicit.get("last_confirmation_outcome") or "").strip() or None,
            active_resource_crunch=str(explicit.get("active_resource_crunch") or "").strip() or None,
        )

    def resolve_repo_knowledge(
        self,
        *,
        repo: RepoCatalogRecord,
        config_dir: Path | None,
        project_dir: Path | None = None,
        prefer_remote_when_stale: bool = True,
    ) -> RepoKnowledgeContext:
        cached = self._load_cached_knowledge(repo=repo, config_dir=config_dir)
        local_project_dir = project_dir if project_dir is not None and project_dir.exists() else None
        if local_project_dir is not None:
            local_files = _read_local_repo_files(local_project_dir)
            if local_files:
                context = _summarize_repo_knowledge(
                    repo=repo,
                    source="local",
                    file_contents=local_files,
                    local_project_dir=local_project_dir,
                )
                self._persist_repo_knowledge(config_dir=config_dir, context=context)
                return context

        if cached is not None and (not prefer_remote_when_stale or not _repo_knowledge_is_stale(cached)):
            return cached

        remote_files = self._safe_remote_fetch(repo)
        if remote_files:
            context = _summarize_repo_knowledge(repo=repo, source="remote", file_contents=remote_files, local_project_dir=None)
            self._persist_repo_knowledge(config_dir=config_dir, context=context)
            return context

        if cached is not None:
            return cached

        fallback = _summarize_repo_knowledge(repo=repo, source="catalog", file_contents={}, local_project_dir=None)
        self._persist_repo_knowledge(config_dir=config_dir, context=fallback)
        return fallback

    def build_agent_context(
        self,
        *,
        current: AppConfig | None,
        system_probe: SystemProbe,
        config_dir: Path | None,
        repo: RepoCatalogRecord | None = None,
        project_dir: Path | None = None,
    ) -> AgentContext:
        repo_knowledge = None
        repo_key = None
        if repo is not None:
            repo_key = repo.repo_url or repo.name
            repo_knowledge = self.resolve_repo_knowledge(repo=repo, config_dir=config_dir, project_dir=project_dir)
        memory = self.build_memory_context(config_dir=config_dir, repo_key=repo_key)
        return AgentContext(
            current=current,
            system_probe=system_probe,
            memory=memory,
            repo_knowledge=repo_knowledge,
        )

    def record_repo_learning(
        self,
        *,
        config_dir: Path | None,
        repo: RepoCatalogRecord,
        summary: str,
        source: str,
        metadata: dict[str, object] | None = None,
    ) -> None:
        if config_dir is None:
            return
        store = initialize_state_store(config_dir)
        existing = store.get_repo_knowledge(repo.repo_url or repo.name)
        merged_metadata = dict(existing.metadata) if existing is not None else {}
        if metadata:
            merged_metadata.update(metadata)
        store.upsert_repo_knowledge(
            repo_key=repo.repo_url or repo.name,
            repo_name=repo.name,
            repo_url=repo.repo_url,
            source=source,
            summary=_compact_learning_summary(summary),
            setup_complexity=existing.setup_complexity if existing is not None else None,
            local_vm_recommendation=existing.local_vm_recommendation if existing is not None else None,
            cpu_profile=existing.cpu_profile if existing is not None else None,
            ram_profile=existing.ram_profile if existing is not None else None,
            gpu_profile=existing.gpu_profile if existing is not None else None,
            apple_silicon_notes=existing.apple_silicon_notes if existing is not None else None,
            required_tools=existing.required_tools if existing is not None else (),
            setup_files=existing.setup_files if existing is not None else (),
            entrypoints=existing.entrypoints if existing is not None else (),
            validation_status=existing.validation_status if existing is not None else "observed",
            freshness_checked_at=existing.freshness_checked_at if existing is not None else None,
            metadata=merged_metadata,
        )

    def record_recommendation_context(
        self,
        *,
        config_dir: Path | None,
        repo: RepoCatalogRecord,
        reason: str,
        alternatives: tuple[str, ...],
        caveat: str | None,
        system_hint: str,
    ) -> None:
        if config_dir is None:
            return
        initialize_managed_memory_state(config_dir)
        payload = {
            "repo_name": repo.name,
            "repo_key": repo.repo_url or repo.name,
            "reason": reason,
            "alternatives": list(alternatives),
            "caveat": caveat,
            "system_hint": system_hint,
        }
        store = initialize_state_store(config_dir)
        store.upsert_managed_memory(
            memory_key=RECOMMENDATION_MEMORY_KEY,
            memory_kind="session",
            relative_path=RECOMMENDATION_MEMORY_PATH,
            title="repo recommendation",
            content=json.dumps(payload, sort_keys=True),
            metadata={"kind": "repo_recommendation"},
        )
        materialize_managed_memory_state(config_dir)
        store.upsert_learning_record(
            learning_key=f"recommendation:{repo.repo_url or repo.name}",
            family="recommendation",
            subject_key=repo.repo_url or repo.name,
            summary=f"{repo.name} recommended for {system_hint}. {reason}",
            signal="success",
            metadata={
                "repo_name": repo.name,
                "alternatives": list(alternatives),
                "caveat": caveat,
                "system_hint": system_hint,
            },
        )

    def record_conversation_learning(
        self,
        *,
        config_dir: Path | None,
        intent: str,
        message: str,
        reply_text: str,
        signal: str,
        repo: RepoCatalogRecord | None = None,
        subject_key: str | None = None,
    ) -> None:
        if config_dir is None:
            return
        resolved_subject_key = subject_key or intent
        if subject_key is None and repo is not None:
            resolved_subject_key = f"{intent}:{repo.repo_url or repo.name}"
        initialize_state_store(config_dir).upsert_learning_record(
            learning_key=f"conversation:{resolved_subject_key}",
            family="conversation",
            subject_key=resolved_subject_key,
            summary=_compact_learning_summary(
                f"Conversation intent {intent} handled as: {reply_text} User said: {message}"
            ),
            signal=signal,
            metadata={
                "intent": intent,
                "repo_name": repo.name if repo is not None else None,
                "message_fragment": " ".join(message.strip().split())[:120],
            },
        )

    def record_setup_learning(
        self,
        *,
        config_dir: Path | None,
        repo: RepoCatalogRecord,
        summary: str,
        signal: str,
        metadata: dict[str, object] | None = None,
    ) -> None:
        if config_dir is None:
            return
        initialize_state_store(config_dir).upsert_learning_record(
            learning_key=f"setup:{repo.repo_url or repo.name}",
            family="setup",
            subject_key=repo.repo_url or repo.name,
            summary=_compact_learning_summary(summary),
            signal=signal,
            metadata=metadata,
        )
        store = initialize_state_store(config_dir)
        recommendation_context = _read_recommendation_context(store)
        if recommendation_context is not None:
            recommended_key = str(recommendation_context.get("repo_key") or "").strip().lower()
            repo_key = (repo.repo_url or repo.name).lower()
            if recommended_key and recommended_key == repo_key:
                store.upsert_learning_record(
                    learning_key=f"recommendation:{repo.repo_url or repo.name}",
                    family="recommendation",
                    subject_key=repo.repo_url or repo.name,
                    summary=_compact_learning_summary(
                        f"{repo.name} recommendation later led to setup outcome: {summary}"
                    ),
                    signal=signal,
                    metadata={
                        "repo_name": repo.name,
                        "outcome_source": "setup",
                        **(metadata or {}),
                    },
                )

    def record_repair_learning(
        self,
        *,
        config_dir: Path | None,
        repo: RepoCatalogRecord,
        summary: str,
        signal: str,
        metadata: dict[str, object] | None = None,
    ) -> None:
        if config_dir is None:
            return
        initialize_state_store(config_dir).upsert_learning_record(
            learning_key=f"repair:{repo.repo_url or repo.name}",
            family="repair",
            subject_key=repo.repo_url or repo.name,
            summary=_compact_learning_summary(summary),
            signal=signal,
            metadata=metadata,
        )

    def _load_cached_knowledge(
        self,
        *,
        repo: RepoCatalogRecord,
        config_dir: Path | None,
    ) -> RepoKnowledgeContext | None:
        if config_dir is None:
            return None
        store = initialize_state_store(config_dir)
        record = store.get_repo_knowledge(repo.repo_url or repo.name)
        if record is None:
            for candidate in store.list_repo_knowledge_records():
                if candidate.repo_name.lower() == repo.name.lower():
                    record = candidate
                    break
        if record is None:
            return None
        return _record_to_repo_knowledge_context(repo, record)

    def _persist_repo_knowledge(
        self,
        *,
        config_dir: Path | None,
        context: RepoKnowledgeContext,
    ) -> None:
        if config_dir is None:
            return
        initialize_state_store(config_dir).upsert_repo_knowledge(
            repo_key=context.repo.repo_url or context.repo.name,
            repo_name=context.repo.name,
            repo_url=context.repo.repo_url,
            source=context.source,
            summary=context.summary,
            setup_complexity=context.setup_complexity,
            local_vm_recommendation=context.local_vm_recommendation,
            cpu_profile=context.cpu_profile,
            ram_profile=context.ram_profile,
            gpu_profile=context.gpu_profile,
            apple_silicon_notes=context.apple_silicon_notes,
            required_tools=context.required_tools,
            setup_files=context.setup_files,
            entrypoints=context.entrypoints,
            validation_status="verified" if context.source == "local" else "observed",
            freshness_checked_at=context.freshness_checked_at,
            metadata={
                "repo_name": context.repo.name,
                "repo_url": context.repo.repo_url,
                "local_project_dir": None if context.local_project_dir is None else str(context.local_project_dir),
                **context.metadata,
            },
        )

    def _safe_remote_fetch(self, repo: RepoCatalogRecord) -> dict[str, str]:
        try:
            return self._remote_fetcher(repo)
        except (HTTPError, URLError, TimeoutError, ValueError, OSError):
            return {}


def _record_to_repo_knowledge_context(repo: RepoCatalogRecord, record: RepoKnowledgeRecord) -> RepoKnowledgeContext:
    metadata = record.metadata or {}
    local_project_dir_value = metadata.get("local_project_dir")
    local_project_dir = Path(local_project_dir_value) if isinstance(local_project_dir_value, str) and local_project_dir_value else None
    return RepoKnowledgeContext(
        repo=repo,
        source=record.source,
        summary=record.summary,
        setup_complexity=record.setup_complexity,
        local_vm_recommendation=record.local_vm_recommendation,
        cpu_profile=record.cpu_profile,
        ram_profile=record.ram_profile,
        gpu_profile=record.gpu_profile,
        apple_silicon_notes=record.apple_silicon_notes,
        required_tools=record.required_tools,
        setup_files=record.setup_files,
        entrypoints=record.entrypoints,
        stack_family=str(metadata.get("stack_family") or "").strip() or None,
        runtime_style=str(metadata.get("runtime_style") or "").strip() or None,
        auth_requirements=tuple(
            RepoAuthRequirement(
                env_var=str(item.get("env_var") or "").strip(),
                provider=str(item.get("provider") or "").strip(),
                reason=str(item.get("reason") or "").strip(),
                source=str(item.get("source") or "").strip(),
                required=bool(item.get("required")),
            )
            for item in metadata.get("auth_requirements", ())
            if isinstance(item, dict) and str(item.get("env_var") or "").strip()
        ),
        freshness_checked_at=record.freshness_checked_at,
        metadata=dict(metadata),
        local_project_dir=local_project_dir,
    )


def _read_recommendation_context(store) -> dict[str, object] | None:
    record = store.get_managed_memory_record(RECOMMENDATION_MEMORY_KEY)
    if record is None:
        return None
    try:
        payload = json.loads(record.content)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _repo_name_from_state_row(row) -> str:
    if getattr(row, "repo_path", None):
        return Path(row.repo_path).name
    repo_url = getattr(row, "repo_url", None) or getattr(row, "repo_key", "")
    if repo_url:
        return str(repo_url).rstrip("/").rsplit("/", 1)[-1]
    return "repo"


def _compact_learning_summary(value: str) -> str:
    return " ".join(value.split())[:560].strip()


def build_hardware_reasoning(
    *,
    system_probe: SystemProbe,
    repo_knowledge: RepoKnowledgeContext | None,
) -> HardwareReasoningSummary:
    """Build one shared CPU/MPS/GPU reasoning summary for supervisor-owned decisions."""

    if system_probe.gpu.cuda_available:
        compute_path = "CUDA-capable"
    elif system_probe.gpu.mps_available or system_probe.gpu.mps_capable:
        compute_path = "MPS-capable"
    else:
        compute_path = "CPU-only"

    repo_preference = "CPU-safe"
    practical_effect = "acceptable"
    minimum_recommended_ram_gib: float | None = None
    comfortable_ram_gib: float | None = None
    if repo_knowledge is not None:
        gpu_profile = (repo_knowledge.gpu_profile or "").lower()
        apple_notes = (repo_knowledge.apple_silicon_notes or "").lower()
        metadata = repo_knowledge.metadata or {}
        cuda_dependence = str(metadata.get("cuda_dependence") or "").strip().lower()
        if "cuda" in gpu_profile:
            repo_preference = "CUDA-heavy"
        elif "gpu preferred" in gpu_profile or "gpu" in gpu_profile:
            repo_preference = "GPU-preferred"
        elif system_probe.is_apple_silicon and "mps" in apple_notes:
            repo_preference = "MPS-safe"
        elif "optional" in gpu_profile or "not required" in gpu_profile:
            repo_preference = "CPU-safe"
        if cuda_dependence in {"required", "high"}:
            repo_preference = "CUDA-heavy"
        elif cuda_dependence == "preferred" and repo_preference != "CUDA-heavy":
            repo_preference = "GPU-preferred"
        elif bool(metadata.get("mps_safe")) and repo_preference not in {"CUDA-heavy", "GPU-preferred"}:
            repo_preference = "MPS-safe"
        elif bool(metadata.get("cpu_only_viable")) and repo_preference not in {"CUDA-heavy", "GPU-preferred"}:
            repo_preference = "CPU-safe"

        lower_bound_from_metadata = _coerce_float(metadata.get("lower_bound_ram_gib"))
        comfortable_from_metadata = _coerce_float(metadata.get("comfortable_ram_gib"))
        ram_profile = (repo_knowledge.ram_profile or "").lower()
        numbers = [float(match) for match in re.findall(r"(\d+(?:\.\d+)?)", ram_profile)]
        if lower_bound_from_metadata is not None:
            minimum_recommended_ram_gib = lower_bound_from_metadata
        if comfortable_from_metadata is not None:
            comfortable_ram_gib = comfortable_from_metadata
        if numbers and minimum_recommended_ram_gib is None and comfortable_ram_gib is None:
            minimum_recommended_ram_gib = min(numbers)
            comfortable_ram_gib = max(numbers)
        elif "8+" in ram_profile and minimum_recommended_ram_gib is None and comfortable_ram_gib is None:
            minimum_recommended_ram_gib = 8.0
            comfortable_ram_gib = 8.0
        elif "16+" in ram_profile and minimum_recommended_ram_gib is None and comfortable_ram_gib is None:
            minimum_recommended_ram_gib = 16.0
            comfortable_ram_gib = 16.0

    usable_ram_gib: float | None = None
    if system_probe.ram_bytes is not None:
        total_ram_gib = system_probe.ram_bytes / (1024**3)
        reserve = 3.0 if total_ram_gib <= 16 else 4.0
        usable_ram_gib = max(0.0, total_ram_gib - reserve)

    if repo_preference == "CUDA-heavy" and not system_probe.gpu.cuda_available:
        practical_effect = "not recommended"
    elif repo_preference == "GPU-preferred" and compute_path == "CPU-only":
        practical_effect = "likely frustrating"
    elif repo_preference == "GPU-preferred" and compute_path == "MPS-capable":
        practical_effect = "slow but usable"

    if usable_ram_gib is not None and comfortable_ram_gib is not None:
        if usable_ram_gib + 0.1 < comfortable_ram_gib:
            practical_effect = "likely frustrating" if practical_effect == "acceptable" else practical_effect
        lower_bound = minimum_recommended_ram_gib or comfortable_ram_gib
        if usable_ram_gib + 0.1 < lower_bound:
            # Keep CPU-safe and MPS-safe repos in the "tight" class unless the memory gap is extreme.
            if repo_preference in {"CPU-safe", "MPS-safe"} and lower_bound > 0:
                if usable_ram_gib / lower_bound < 0.55:
                    practical_effect = "not recommended"
                elif practical_effect == "acceptable":
                    practical_effect = "likely frustrating"
            else:
                practical_effect = "not recommended"

    summary = f"Your compute path is {compute_path}; this repo looks {repo_preference.lower()}; practical effect: {practical_effect}."
    if usable_ram_gib is not None:
        summary += f" Duckln estimates about {usable_ram_gib:.1f} GiB RAM is comfortably usable after system overhead."
    return HardwareReasoningSummary(
        compute_path=compute_path,
        repo_preference=repo_preference,
        practical_effect=practical_effect,
        usable_ram_gib=usable_ram_gib,
        minimum_recommended_ram_gib=minimum_recommended_ram_gib,
        comfortable_ram_gib=comfortable_ram_gib,
        summary=summary,
    )


def build_repo_feasibility_assessment(
    *,
    system_probe: SystemProbe,
    repo: RepoCatalogRecord,
    repo_knowledge: RepoKnowledgeContext | None,
    verified_summary: str | None = None,
) -> RepoFeasibilityAssessment:
    """Build one canonical feasibility contract for recommendation and preflight."""

    hardware = build_hardware_reasoning(system_probe=system_probe, repo_knowledge=repo_knowledge)
    fit_label = "comfortable"
    if hardware.practical_effect in {"slow but usable", "likely frustrating"}:
        fit_label = "workable_but_tight"
    elif hardware.practical_effect == "not recommended":
        fit_label = "not_recommended"

    failure_modes: list[str] = []
    if fit_label != "comfortable":
        failure_modes.extend(("swap pressure", "slower inference", "verification risk"))
    if hardware.repo_preference in {"GPU-preferred", "CUDA-heavy"} and hardware.compute_path != "CUDA-capable":
        failure_modes.append("accelerator mismatch for GPU-heavy paths")
    if system_probe.disk_free_bytes is not None and (system_probe.disk_free_bytes / (1024**3)) < 20:
        failure_modes.append("tight disk headroom for downloads and caches")
    if system_probe.is_apple_silicon and hardware.repo_preference == "CUDA-heavy":
        failure_modes.append("CUDA-specific acceleration is unavailable on Apple Silicon")
    deduped_failure_modes = tuple(dict.fromkeys(failure_modes))

    repo_name = repo.name
    rationale_parts: list[str] = [
        f"{repo_name} maps to a {hardware.repo_preference.lower()} profile on this {system_probe.operating_system} system.",
        f"Compute path: {hardware.compute_path}.",
    ]
    if hardware.usable_ram_gib is not None:
        rationale_parts.append(f"Usable RAM after system overhead is about {hardware.usable_ram_gib:.1f} GiB.")
    if hardware.minimum_recommended_ram_gib is not None and hardware.comfortable_ram_gib is not None:
        rationale_parts.append(
            f"Estimated lower bound is {hardware.minimum_recommended_ram_gib:.0f} GiB and comfortable target is {hardware.comfortable_ram_gib:.0f} GiB."
        )
    rationale_parts.append(f"Practical effect is {hardware.practical_effect}.")

    detected = (
        f"Detected: {system_probe.operating_system} {system_probe.architecture}, "
        f"{system_probe.cpu_logical_cores if system_probe.cpu_logical_cores is not None else 'unknown'} CPU cores, "
        f"{(system_probe.ram_bytes / (1024**3)):.1f} GiB RAM."
        if system_probe.ram_bytes is not None
        else f"Detected: {system_probe.operating_system} {system_probe.architecture}; CPU/RAM are partially bounded."
    )
    inferred = (
        f"Inferred: {repo_name} is {fit_label.replace('_', ' ')} with {hardware.repo_preference.lower()} requirements."
    )
    verified = f"Verified: {verified_summary}" if verified_summary else None
    trust_summary = f"{detected} {inferred}" if verified is None else f"{detected} {inferred} {verified}"

    return RepoFeasibilityAssessment(
        fit_label=fit_label,
        practical_effect=hardware.practical_effect,
        compute_path=hardware.compute_path,
        repo_preference=hardware.repo_preference,
        usable_ram_gib=hardware.usable_ram_gib,
        lower_bound_ram_gib=hardware.minimum_recommended_ram_gib,
        comfortable_ram_gib=hardware.comfortable_ram_gib,
        failure_modes=deduped_failure_modes,
        rationale=" ".join(rationale_parts),
        trust=TrustEvidenceSummary(
            detected=detected,
            inferred=inferred,
            verified=verified,
            summary=trust_summary,
        ),
    )


def _coerce_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
    return None


def _repo_knowledge_is_stale(record: RepoKnowledgeContext) -> bool:
    try:
        checked_at = datetime.fromisoformat(record.freshness_checked_at)
    except ValueError:
        return True
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    return checked_at < datetime.now(tz=timezone.utc) - timedelta(hours=FRESH_KNOWLEDGE_TTL_HOURS)


def _read_local_repo_files(project_dir: Path) -> dict[str, str]:
    results: dict[str, str] = {}
    for file_name in TARGET_REMOTE_REPO_FILES:
        candidate = project_dir / file_name
        if candidate.exists() and candidate.is_file():
            results[file_name] = candidate.read_text(encoding="utf-8", errors="ignore")[:MAX_REMOTE_CONTENT_CHARS]
    return results


def _fetch_targeted_remote_repo_files(repo: RepoCatalogRecord) -> dict[str, str]:
    owner, project = _parse_github_owner_repo(repo.repo_url)
    branch = _guess_default_branch(owner, project)
    files: dict[str, str] = {}
    for file_name in TARGET_REMOTE_REPO_FILES:
        raw_url = f"https://raw.githubusercontent.com/{owner}/{project}/{branch}/{file_name}"
        try:
            request = Request(raw_url, headers={"User-Agent": "Duckln"})
            with urlopen(request, timeout=REMOTE_FETCH_TIMEOUT_SECONDS) as response:
                files[file_name] = response.read().decode("utf-8", errors="ignore")[:MAX_REMOTE_CONTENT_CHARS]
        except HTTPError as exc:
            if exc.code == 404:
                continue
            raise
    return files


def _guess_default_branch(owner: str, project: str) -> str:
    api_url = f"https://api.github.com/repos/{owner}/{project}"
    request = Request(api_url, headers={"Accept": "application/vnd.github+json", "User-Agent": "Duckln"})
    try:
        with urlopen(request, timeout=REMOTE_FETCH_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8", errors="ignore"))
            branch = payload.get("default_branch")
            if isinstance(branch, str) and branch.strip():
                return branch.strip()
    except (HTTPError, URLError, TimeoutError, ValueError, OSError, json.JSONDecodeError):
        pass
    return "main"


def _parse_github_owner_repo(repo_url: str) -> tuple[str, str]:
    parsed = urlparse(repo_url)
    if parsed.netloc not in {"github.com", "www.github.com"}:
        raise ValueError(f"Unsupported repo host for remote inspection: {repo_url}")
    parts = [segment for segment in parsed.path.split("/") if segment]
    if len(parts) < 2:
        raise ValueError(f"Unsupported GitHub repo URL: {repo_url}")
    return parts[0], parts[1].removesuffix(".git")


def _summarize_repo_knowledge(
    *,
    repo: RepoCatalogRecord,
    source: str,
    file_contents: dict[str, str],
    local_project_dir: Path | None,
) -> RepoKnowledgeContext:
    setup_files = tuple(sorted(file_contents))
    combined_text = "\n".join(file_contents.values()).lower()
    required_tools = _detect_required_tools(file_contents, combined_text)
    entrypoints = _detect_entrypoints(file_contents)
    stack_family = _detect_stack_family(file_contents, combined_text)
    runtime_style = _detect_runtime_style(file_contents=file_contents, entrypoints=entrypoints, combined_text=combined_text)
    auth_requirements = _detect_auth_requirements(file_contents)
    setup_complexity = _infer_setup_complexity(repo, setup_files, combined_text)
    local_vm_recommendation = _infer_local_vm_recommendation(repo, combined_text, setup_complexity)
    cpu_profile, ram_profile, gpu_profile = _infer_resource_profiles(repo, combined_text)
    apple_silicon_notes = _infer_apple_silicon_notes(repo, combined_text)
    inferred_metadata = _infer_repo_runtime_metadata(
        repo=repo,
        setup_complexity=setup_complexity,
        ram_profile=ram_profile,
        gpu_profile=gpu_profile,
        combined_text=combined_text,
    )
    inferred_metadata["stack_family"] = stack_family
    inferred_metadata["runtime_style"] = runtime_style
    inferred_metadata["auth_requirements"] = [asdict(item) for item in auth_requirements]
    summary = _build_repo_summary(
        repo=repo,
        source=source,
        setup_complexity=setup_complexity,
        required_tools=required_tools,
        entrypoints=entrypoints,
        stack_family=stack_family,
        runtime_style=runtime_style,
        auth_requirements=auth_requirements,
        cpu_profile=cpu_profile,
        ram_profile=ram_profile,
        gpu_profile=gpu_profile,
        apple_silicon_notes=apple_silicon_notes,
        local_vm_recommendation=local_vm_recommendation,
    )
    return RepoKnowledgeContext(
        repo=repo,
        source=source,
        summary=summary,
        setup_complexity=setup_complexity,
        local_vm_recommendation=local_vm_recommendation,
        cpu_profile=cpu_profile,
        ram_profile=ram_profile,
        gpu_profile=gpu_profile,
        apple_silicon_notes=apple_silicon_notes,
        required_tools=required_tools,
        setup_files=setup_files,
        entrypoints=entrypoints,
        stack_family=stack_family,
        runtime_style=runtime_style,
        auth_requirements=auth_requirements,
        freshness_checked_at=datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        metadata=inferred_metadata,
        local_project_dir=local_project_dir,
    )


def _detect_required_tools(file_contents: dict[str, str], combined_text: str) -> tuple[str, ...]:
    tools: list[str] = []
    if any(name in file_contents for name in ("requirements.txt", "pyproject.toml", "setup.py", "environment.yml")):
        tools.append("Python")
    if "package.json" in file_contents:
        tools.append("Node.js")
    if any(name in file_contents for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")):
        tools.append("Docker Compose")
    if "Dockerfile" in file_contents:
        tools.append("Docker")
    if "Cargo.toml" in file_contents:
        tools.extend(("Rust", "Cargo"))
    if "go.mod" in file_contents or "main.go" in file_contents:
        tools.append("Go")
    if "Makefile" in file_contents:
        tools.append("make")
    if "ffmpeg" in combined_text:
        tools.append("ffmpeg")
    if "cmake" in combined_text:
        tools.append("cmake")
    if "ollama" in combined_text:
        tools.append("Ollama")
    if "git clone" in combined_text or "github.com/" in combined_text:
        tools.append("git")
    return tuple(dict.fromkeys(tools))


def _detect_entrypoints(file_contents: dict[str, str]) -> tuple[str, ...]:
    entrypoints: list[str] = []
    if any(name in file_contents for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")):
        entrypoints.append("docker compose up")
    package_json = file_contents.get("package.json")
    if package_json:
        package_manager = _preferred_node_package_manager_from_files(file_contents)
        try:
            payload = json.loads(package_json)
            scripts = payload.get("scripts", {})
            if isinstance(scripts, dict):
                for key in ("start", "dev", "serve", "preview", "web", "frontend", "api", "backend"):
                    if key in scripts:
                        entrypoints.append(_render_node_script_command(package_manager, key))
                if isinstance(payload.get("main"), str) and payload["main"].strip():
                    entrypoints.append(f"node {payload['main'].strip()}")
        except json.JSONDecodeError:
            pass
    if "Cargo.toml" in file_contents:
        entrypoints.append("cargo run")
    if "go.mod" in file_contents or "main.go" in file_contents:
        entrypoints.append("go run .")
    if "manage.py" in file_contents:
        entrypoints.append(".venv/bin/python manage.py runserver")
    makefile = file_contents.get("Makefile", "")
    for target in ("setup", "install", "init", "bootstrap", "run", "start", "serve", "dev", "up", "web", "api", "backend"):
        if re.search(rf"^{re.escape(target)}\s*:", makefile, flags=re.MULTILINE):
            entrypoints.append(f"make {target}")
    if "README.md" in file_contents:
        readme = file_contents["README.md"]
        for pattern in (
            r"`(python [^`]+)`",
            r"`(python -m [^`]+)`",
            r"`(\.venv/bin/python [^`]+)`",
            r"`(node [^`]+)`",
            r"`(cargo [^`]+)`",
            r"`(go [^`]+)`",
            r"`(uvicorn [^`]+)`",
            r"`(streamlit [^`]+)`",
            r"`(gradio [^`]+)`",
            r"`(pip install [^`]+)`",
            r"`(npm run [^`]+)`",
            r"`(pnpm [^`]+)`",
            r"`(yarn [^`]+)`",
            r"`(bun run [^`]+)`",
            r"`(docker compose [^`]+)`",
            r"`(docker run [^`]+)`",
        ):
            for match in re.findall(pattern, readme, flags=re.IGNORECASE):
                entrypoints.append(match.strip())
                if len(entrypoints) >= 3:
                    return tuple(dict.fromkeys(entrypoints))
    return tuple(dict.fromkeys(entrypoints))


def _preferred_node_package_manager_from_files(file_contents: dict[str, str]) -> str:
    if "pnpm-lock.yaml" in file_contents:
        return "pnpm"
    if "yarn.lock" in file_contents or ".yarnrc.yml" in file_contents:
        return "yarn"
    if "bun.lockb" in file_contents or "bun.lock" in file_contents:
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


def _detect_stack_family(file_contents: dict[str, str], combined_text: str) -> str:
    has_python = any(name in file_contents for name in ("requirements.txt", "pyproject.toml", "setup.py", "environment.yml", "manage.py"))
    has_node = "package.json" in file_contents
    has_go = "go.mod" in file_contents or "main.go" in file_contents
    has_rust = "Cargo.toml" in file_contents
    has_docker = "Dockerfile" in file_contents or any(name in file_contents for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"))
    families = [name for name, present in (("python", has_python), ("node", has_node), ("go", has_go), ("rust", has_rust), ("docker", has_docker)) if present]
    if len(families) > 1:
        return "mixed"
    if families:
        return families[0]
    if "rust" in combined_text:
        return "rust"
    if "golang" in combined_text or re.search(r"\bgo\b", combined_text) is not None:
        return "go"
    if "node" in combined_text or "typescript" in combined_text:
        return "node"
    return "python"


def _detect_runtime_style(
    *,
    file_contents: dict[str, str],
    entrypoints: tuple[str, ...],
    combined_text: str,
) -> str:
    if any("docker compose up" in entry.lower() for entry in entrypoints):
        return "multi_service"
    if any(token in combined_text for token in ("cli", "command line", "usage:", "transcribe", "audio-file", "input file")):
        return "cli_tool"
    if any(token in combined_text for token in ("localhost", "http://", "https://", "web ui", "browser", "runserver", "uvicorn", "streamlit", "gradio")):
        return "service"
    if "jupyter" in combined_text or "notebook" in combined_text:
        return "notebook"
    if "package.json" in file_contents or "manage.py" in file_contents:
        return "service"
    if "Cargo.toml" in file_contents or "go.mod" in file_contents:
        return "interactive_app"
    return "library_or_setup_only"


_AUTH_REQUIREMENT_CATALOG: tuple[tuple[str, str, str], ...] = (
    ("OPENAI_API_KEY", "OpenAI", "Repo docs mention OpenAI access."),
    ("ANTHROPIC_API_KEY", "Anthropic", "Repo docs mention Anthropic access."),
    ("HF_TOKEN", "Hugging Face", "Repo docs mention Hugging Face access."),
    ("HUGGINGFACEHUB_API_TOKEN", "Hugging Face", "Repo docs mention Hugging Face Hub access."),
    ("HUGGING_FACE_HUB_TOKEN", "Hugging Face", "Repo docs mention Hugging Face Hub access."),
    ("GITHUB_TOKEN", "GitHub", "Repo docs mention GitHub-authenticated access."),
    ("REPLICATE_API_TOKEN", "Replicate", "Repo docs mention Replicate access."),
    ("AWS_ACCESS_KEY_ID", "AWS", "Repo docs mention AWS access."),
    ("GOOGLE_APPLICATION_CREDENTIALS", "GCP", "Repo docs mention GCP access."),
)


def _detect_auth_requirements(file_contents: dict[str, str]) -> tuple[RepoAuthRequirement, ...]:
    padded_sources = {name: f" {content.lower()} " for name, content in file_contents.items()}
    results: list[RepoAuthRequirement] = []
    seen: set[str] = set()
    for env_var, provider, default_reason in _AUTH_REQUIREMENT_CATALOG:
        env_var_lower = env_var.lower()
        for source_name, padded in padded_sources.items():
            if env_var_lower not in padded:
                continue
            if env_var in seen:
                break
            required = any(
                token in padded
                for token in (
                    f"required {env_var_lower}",
                    f"must set {env_var_lower}",
                    f"need {env_var_lower}",
                    f"set {env_var_lower} before",
                )
            )
            results.append(
                RepoAuthRequirement(
                    env_var=env_var,
                    provider=provider,
                    reason=default_reason,
                    source=source_name,
                    required=required,
                )
            )
            seen.add(env_var)
            break
    return tuple(results)


def _infer_setup_complexity(repo: RepoCatalogRecord, setup_files: tuple[str, ...], combined_text: str) -> str:
    if "Dockerfile" in setup_files and ("cuda" in combined_text or "gpu" in combined_text):
        return "high"
    if repo.category in {"Stable Diffusion", "Computer Vision"} and ("cuda" in combined_text or "gpu" in combined_text):
        return "high"
    if len(setup_files) >= 4 or ("docker" in combined_text and "python" in combined_text):
        return "medium"
    return "low"


def _infer_local_vm_recommendation(repo: RepoCatalogRecord, combined_text: str, setup_complexity: str) -> str:
    if "docker" in combined_text or repo.category == "Stable Diffusion":
        return "local preferred if hardware is ready; VM only for isolation"
    if setup_complexity == "low":
        return "local preferred"
    return "local first, VM if dependencies become noisy"


def _infer_resource_profiles(repo: RepoCatalogRecord, combined_text: str) -> tuple[str | None, str | None, str | None]:
    if "whisper" in repo.name.lower() or repo.category == "Audio":
        return ("a few CPU cores", "8-16 GB RAM", "GPU optional")
    if repo.category == "Stable Diffusion" or "cuda" in combined_text or "gpu" in combined_text:
        return ("high CPU not required", "16 GB+ RAM preferred", "GPU preferred")
    if repo.name.lower() in {"open-webui", "ollama"}:
        return ("modest CPU", "4-8 GB RAM for UI/runtime shell", "depends on selected model")
    return ("moderate CPU", "8+ GB RAM", "not required unless the repo says so")


def _infer_repo_runtime_metadata(
    *,
    repo: RepoCatalogRecord,
    setup_complexity: str,
    ram_profile: str | None,
    gpu_profile: str | None,
    combined_text: str,
) -> dict[str, object]:
    name = repo.name.lower()
    category = repo.category.lower()
    metadata: dict[str, object] = {
        "setup_complexity": setup_complexity,
    }

    if category == "stable diffusion":
        metadata.update(
            {
                "lower_bound_ram_gib": 16.0,
                "comfortable_ram_gib": 24.0,
                "cpu_only_viable": False,
                "mps_safe": False,
                "cuda_dependence": "high",
                "disk_burden": "high",
                "maintenance_risk": "high",
                "wow_moment_score": 0.5,
            }
        )
    elif category == "audio":
        metadata.update(
            {
                "lower_bound_ram_gib": 8.0,
                "comfortable_ram_gib": 12.0,
                "cpu_only_viable": True,
                "mps_safe": True,
                "cuda_dependence": "optional",
                "disk_burden": "medium",
                "maintenance_risk": "medium",
                "wow_moment_score": 0.8,
            }
        )
    elif category == "llm":
        metadata.update(
            {
                "lower_bound_ram_gib": 8.0,
                "comfortable_ram_gib": 16.0,
                "cpu_only_viable": True,
                "mps_safe": True,
                "cuda_dependence": "optional",
                "disk_burden": "medium",
                "maintenance_risk": "medium",
                "wow_moment_score": 0.7,
            }
        )
    else:
        metadata.update(
            {
                "lower_bound_ram_gib": 8.0,
                "comfortable_ram_gib": 12.0,
                "cpu_only_viable": True,
                "mps_safe": True,
                "cuda_dependence": "optional",
                "disk_burden": "medium",
                "maintenance_risk": "medium",
                "wow_moment_score": 0.6,
            }
        )

    if name in {"whisperx"}:
        metadata.update(
            {
                "lower_bound_ram_gib": 10.0,
                "comfortable_ram_gib": 16.0,
                "cpu_only_viable": True,
                "mps_safe": True,
                "cuda_dependence": "preferred",
                "maintenance_risk": "medium_high",
                "wow_moment_score": 0.7,
            }
        )
    elif name in {"whisper", "faster-whisper", "speechbrain"}:
        metadata.update(
            {
                "lower_bound_ram_gib": 8.0,
                "comfortable_ram_gib": 12.0,
                "cpu_only_viable": True,
                "mps_safe": True,
                "cuda_dependence": "optional",
                "maintenance_risk": "low",
                "wow_moment_score": 0.85,
            }
        )
    elif name in {"comfyui", "stable-diffusion-webui", "invokeai", "diffusers"}:
        metadata.update(
            {
                "lower_bound_ram_gib": 16.0,
                "comfortable_ram_gib": 24.0,
                "cpu_only_viable": False,
                "mps_safe": False,
                "cuda_dependence": "high",
                "disk_burden": "high",
                "maintenance_risk": "high",
                "wow_moment_score": 0.45,
            }
        )
    elif name in {"open-webui", "localai", "ollama", "llama.cpp"}:
        metadata.update(
            {
                "lower_bound_ram_gib": 6.0,
                "comfortable_ram_gib": 10.0,
                "cpu_only_viable": True,
                "mps_safe": True,
                "cuda_dependence": "optional",
                "disk_burden": "medium",
                "maintenance_risk": "medium",
                "wow_moment_score": 0.8,
            }
        )

    lower, comfortable = _extract_ram_bounds_from_profile(ram_profile)
    if lower is not None:
        metadata["lower_bound_ram_gib"] = lower
    if comfortable is not None:
        metadata["comfortable_ram_gib"] = comfortable

    profile = (gpu_profile or "").lower()
    if "cuda" in profile:
        metadata["cuda_dependence"] = "high"
    elif ("gpu preferred" in profile or "gpu" in profile) and metadata.get("cuda_dependence") not in {"high", "required"}:
        metadata["cuda_dependence"] = "preferred"
    elif "optional" in profile or "not required" in profile:
        metadata["cuda_dependence"] = "optional"

    text = combined_text.lower()
    if "cuda required" in text or "requires cuda" in text:
        metadata["cuda_dependence"] = "required"
    if "mps" in text and metadata.get("cuda_dependence") in {"high", "required"}:
        metadata["mps_safe"] = True

    return metadata


def _extract_ram_bounds_from_profile(ram_profile: str | None) -> tuple[float | None, float | None]:
    if not ram_profile:
        return (None, None)
    numbers = [float(match) for match in re.findall(r"(\d+(?:\.\d+)?)", ram_profile.lower())]
    if numbers:
        return (min(numbers), max(numbers))
    if "8+" in ram_profile:
        return (8.0, 8.0)
    if "16+" in ram_profile:
        return (16.0, 16.0)
    return (None, None)


def _infer_apple_silicon_notes(repo: RepoCatalogRecord, combined_text: str) -> str | None:
    if "cuda" in combined_text:
        return "CUDA-specific acceleration is not available on Apple Silicon; prefer CPU or MPS-safe paths."
    if repo.category == "Stable Diffusion":
        return "Apple Silicon may run this, but setup is usually heavier and more sensitive than audio or UI repos."
    return None


def _build_repo_summary(
    *,
    repo: RepoCatalogRecord,
    source: str,
    setup_complexity: str | None,
    required_tools: tuple[str, ...],
    entrypoints: tuple[str, ...],
    stack_family: str | None,
    runtime_style: str | None,
    auth_requirements: tuple[RepoAuthRequirement, ...],
    cpu_profile: str | None,
    ram_profile: str | None,
    gpu_profile: str | None,
    apple_silicon_notes: str | None,
    local_vm_recommendation: str | None,
) -> str:
    pieces = [f"{repo.name} knowledge ({source})"]
    if stack_family:
        pieces.append(f"stack {stack_family}")
    if runtime_style:
        pieces.append(f"runtime style {runtime_style}")
    if setup_complexity:
        pieces.append(f"setup complexity {setup_complexity}")
    if required_tools:
        pieces.append(f"tools: {', '.join(required_tools)}")
    if entrypoints:
        pieces.append(f"entrypoints: {', '.join(entrypoints[:2])}")
    if auth_requirements:
        pieces.append(
            "auth: "
            + ", ".join(f"{item.env_var} ({item.provider})" for item in auth_requirements[:3])
        )
    profiles = [profile for profile in (cpu_profile, ram_profile, gpu_profile) if profile]
    if profiles:
        pieces.append(f"requirements: {'; '.join(profiles)}")
    if local_vm_recommendation:
        pieces.append(local_vm_recommendation)
    if apple_silicon_notes:
        pieces.append(apple_silicon_notes)
    return ". ".join(pieces).strip() + "."
