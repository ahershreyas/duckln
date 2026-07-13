"""Duckln CLI entrypoint."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import os
import random
import re
import shlex
import shutil
import platform
import sys
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from agent.memory import resolve_agent_memory_paths
from duckln.ai_client import Provider, get_provider_adapter_for_base_url
from duckln.agent_context import AgentContextService
from duckln.conversation_agent import ConversationSupervisor, ConversationTurn, FreeTextReply
from agent.agent_loop import (
    ActionResult as CloudLoopActionResult,
    IssueClassification as CloudLoopIssue,
    LoopChoice as CloudLoopChoice,
    LoopStep as CloudLoopStep,
    Remediation as CloudLoopRemediation,
    run_agent_loop as run_cloud_agent_loop,
)
from duckln.cloud_remediation import (
    audit_gcp_api_keys,
    classify_cloud_failure,
    classify_gcp_api_key_findings,
    resolve_cloud_remediation,
    resolve_gcp_api_key_remediation,
)
from duckln.cloud_shapes import (
    DiscoveredCloudShape,
    derived_default_disk_gb,
    discover_all_cloud_shapes,
    group_shapes_into_categories,
    render_shape_label,
)
from duckln.explore_runtime import (
    CONNECTION_ERROR_MESSAGE as EXPLORE_CONNECTION_ERROR_MESSAGE,
    EMPTY_PARSE_MESSAGE as EXPLORE_EMPTY_PARSE_MESSAGE,
    run_explore_loop,
)
from duckln.explore_trending import TrendingPeriod
from duckln.cloud_runtime import (
    build_cloud_remote_exec_command,
    build_cloud_cli_install_plan,
    CloudLaunchRequest,
    cleanup_managed_resource,
    create_cloud_resource,
    create_gcp_project,
    discover_available_cloud_shapes,
    discover_aws_key_pairs,
    discover_aws_security_groups,
    discover_aws_subnets,
    discover_aws_regions_detailed,
    discover_cloud_regions,
    discover_gcp_projects,
    discover_gcp_zones_detailed,
    discover_tagged_cloud_resources,
    enforce_managed_resource_idle_policies,
    extend_managed_resource_keepalive,
    inspect_cloud_auth,
    record_managed_resource_activity,
    resolve_managed_resource,
    render_managed_resource_summary,
    stage_cloud_terminal_attach,
)
from duckln.loop_runtime import (
    create_loop_from_answers,
    infer_loop_spec,
    next_expires_at,
    render_loop_confirmation,
    render_loop_history,
    render_loops,
    run_loop_now,
    schedule_loop,
    start_loop_scheduler,
    shutdown_loop_scheduler_if_idle,
    unschedule_loop,
)
from duckln.config import (
    _resolve_agent_contract_source,
    AppConfig,
    ConfigPaths,
    OnboardingError,
    SessionExitRequested,
    ensure_first_run_preferences,
    initialize_runtime_storage,
    load_app_config,
    open_runtime_config_menu,
    resolve_config_paths,
    run_onboarding,
    save_app_config,
    update_runtime_mode,
    update_runtime_model,
    update_runtime_provider,
)
from duckln.diagnostics import redact_sensitive_data, run_healthcheck
from duckln.execution_trace import build_failure_message, render_tool_invocation_trace
from duckln.modes import ControlMode
from duckln.repo_bringup import (
    _wrap_command_for_execution_target,
    assess_repo_preflight,
    bring_up_selected_repo,
    classify_runtime_command,
    derive_repo_runtime_hints,
    _generate_repair_skill_note,
    resolve_managed_project_dir,
    run_prepared_repo,
    scan_readme_for_run_commands,
    scan_remote_readme_for_run_commands,
)
from duckln.render_blocks import (
    action_block,
    bullet_block,
    comparison_block,
    join_blocks,
    paragraph_block,
    status_block,
)
from duckln.repos import open_repo_catalog
from duckln.repair_intake import (
    build_approved_dependency_command,
    DependencyApprovalDecision,
    DependencyApprovalItem,
    DependencyApprovalRequest,
    fingerprint_stderr,
    parse_install_hint,
    plan_runtime_repair,
    render_dependency_approval_lines,
    summarize_failure_incident,
)
from duckln.runtime_governance import approved_cloud_shapes, format_usage_footer
from duckln.tool_registry import ToolVisibilityPolicy, render_tools_manifest
from duckln.selections import (
    ConversationChoiceOption,
    ConversationChoicePrompt,
    conversation_choice_labels,
    duckln_confirm,
    duckln_search_select,
    duckln_select,
    resolve_conversation_choice,
)
from duckln.ui import (
    build_chat_interface,
    build_chat_input,
    build_terminal_display,
    build_terminal_input,
    render_banner,
    render_input_footer,
    render_session_header,
)
from duckln.uninstall import run_uninstall_flow
from duckln.usage_meter import current_usage_snapshot, reset_usage_snapshot
from duckln.vm import (
    build_local_service_install_plan,
    configure_existing_multipass_vm,
    create_multipass_vm,
    ensure_multipass_vm_running,
    invalidate_multipass_vm_cache,
    installation_guidance as vm_installation_guidance,
    is_multipass_installed,
    list_multipass_vm_names,
    list_multipass_vms_or_none,
    render_remediation_steps,
    restart_multipass_vm,
    verify_vm_exec_connectivity,
    vm_exists_in_multipass,
    VmSetupFailureType,
    VmProvisionResult,
)
from duckln.web_runtime import (
    RuntimeRepairEvidence,
    gather_runtime_repair_evidence,
    search_for_repo_install_hint,
    search_for_run_command_hint,
)
from agent.probe import probe_system
from duckln.shell import CommandResult, ControlledCommandRunner
from duckln.safety import assess_command
from state.access import (
    clear_memory_scope,
    clear_pending_cloud_remediation,
    materialize_managed_memory_state,
    read_config_snapshot,
    read_followup_state,
    read_pending_cloud_remediation,
    read_workflow_state,
    record_system_probe,
    write_config_snapshot,
    write_followup_state,
    write_pending_cloud_remediation,
    write_session_summary_state,
    write_skill_memory_state,
    write_workflow_state,
)
from state.repo_catalog import (
    load_sorted_local_repo_catalog,
    refresh_local_repo_catalog,
    resolve_public_github_repo_record,
    RepoCatalogRecord,
)
from state.store import initialize_state_store
from duckln.modes import evaluate_mode_action


OLLAMA_SESSION_START_MAX_ATTEMPTS = 3
OLLAMA_SESSION_START_RETRY_SECONDS = 0.1
_startup_messages_shown = False
_FREE_TEXT_REPEAT_WINDOW = 4
_CLI_PLACEHOLDER_PATTERN = re.compile(r"<([^>]+)>")
_ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-9;]*m")
_ACTIVE_OBJECTIVE_MAX_REPAIR_ATTEMPTS = 5
_CONVERSATION_SUPERVISOR = ConversationSupervisor()
_AGENT_CONTEXT_SERVICE = AgentContextService()


@dataclass(frozen=True)
class SlashCommandDescriptor:
    """Slash command metadata for the runtime command palette."""

    command: str
    description: str

    @property
    def choice_label(self) -> str:
        return f"{self.command} — {self.description}"


@dataclass(frozen=True)
class RuntimePrerequisitePlan:
    """Bounded prerequisite-install plan Duckln can review before repair."""

    dependency: str
    install_command: str
    source_url: str
    installer: str
    reason: str
    execution_target: str
    verification_command: str | None = None


@dataclass(frozen=True)
class TerminalConnectionContext:
    """What the embedded terminal pane is actually attached to."""

    connection_type: str
    vm_name: str | None = None
    docker_name: str | None = None
    cloud_vendor: str | None = None
    cloud_region: str | None = None
    cloud_shape: str | None = None


def get_slash_command_descriptors() -> tuple[SlashCommandDescriptor, ...]:
    """Return the supported runtime slash commands."""

    return (
        SlashCommandDescriptor("/help", "Show the available slash commands."),
        SlashCommandDescriptor("/mode", "Change how much Duckln can do for you."),
        SlashCommandDescriptor("/provider", "Change provider, enter a new key, and pick a model."),
        SlashCommandDescriptor("/model", "Pick a different model for the current provider."),
        SlashCommandDescriptor("/models", "Assign models per agent (Unified or Specialized mode)."),
        SlashCommandDescriptor("/status", "Show which model powers each agent node (+ host memory/GPU)."),
        SlashCommandDescriptor("/config", "Open the configuration menu."),
        SlashCommandDescriptor("/repos", "Browse the cached repo catalog and select a repository."),
        SlashCommandDescriptor("/repos tracked", "Show the repos Duckln is currently tracking."),
        SlashCommandDescriptor("/repos active", "Show the active tracked repo."),
        SlashCommandDescriptor("/repos status", "Show the live deploy objective for the active repo (phase, step, duration)."),
        SlashCommandDescriptor("/repos history", "Show the last 10 recorded deploy/run events for the active repo."),
        SlashCommandDescriptor("/repos live", "Show the live repo sessions Duckln is currently tracking."),
        SlashCommandDescriptor("/repos path", "Show the active repo path Duckln has recorded."),
        SlashCommandDescriptor("/repos link", "Track an existing repo path so Duckln can run and inspect it later."),
        SlashCommandDescriptor("/repos remove", "Remove a tracked repo with confirmation."),
        SlashCommandDescriptor("/repos refresh", "Refresh the cached repo catalog from GitHub."),
        SlashCommandDescriptor("/memory clear", "Clear Duckln memory with confirmation."),
        SlashCommandDescriptor("/remember", "Save a concise high-signal summary of the current Duckln context."),
        SlashCommandDescriptor("/failures", "Show, clear, or tune Duckln's persistent install-failure memory."),
        SlashCommandDescriptor("/failures show", "List the 20 most recent install-command failures Duckln remembers."),
        SlashCommandDescriptor("/failures clear", "Wipe all persisted install-failure records with confirmation."),
        SlashCommandDescriptor("/failures window", "Set the freshness window (hours) Duckln uses to skip known failures."),
        SlashCommandDescriptor("/agents", "List Duckln harness agents (supervisor + specialists) and their roles."),
        SlashCommandDescriptor("/agents trace", "Render the timeline of a harness session's agent activity."),
        SlashCommandDescriptor("/agents costs", "Show token + wall-clock totals for a harness session."),
        SlashCommandDescriptor("/plan", "Show the pending plan summary (Plan Mode is always on — Duckln auto-decides)."),
        SlashCommandDescriptor("/plan precheck", "Set pre-check (on/off/ask): probe the target before drafting a plan."),
        SlashCommandDescriptor("/plan show", "Re-render the currently pending plan."),
        SlashCommandDescriptor("/plan approve", "Approve the pending plan and start strict execution."),
        SlashCommandDescriptor("/plan continue", "Resume a paused/amended plan from where it left (skips completed steps)."),
        SlashCommandDescriptor("/plan reject", "Discard the pending plan and append the decision to history."),
        SlashCommandDescriptor("/plan edit", "Open the pending plan in your editor; then `/plan reload` to apply."),
        SlashCommandDescriptor("/plan reload", "Re-read the edited plan file and replace the pending plan."),
        SlashCommandDescriptor("/plan retry", "Discard the pending plan and re-plan with the last run's learning injected."),
        SlashCommandDescriptor("/plan history", "Show the 20 most recent plan decisions."),
        SlashCommandDescriptor("/vm", "Create an Ubuntu VM with Multipass."),
        SlashCommandDescriptor("/cloud", "Manage approved cloud auth, launch, keepalive, and cleanup flows."),
        SlashCommandDescriptor("/create aws vm", "Install/check AWS CLI, then choose an approved AWS VM type to create."),
        SlashCommandDescriptor("/create gcp vm", "Install/check gcloud, then choose an approved GCP VM type to create."),
        SlashCommandDescriptor("/loop", "Create a scheduled background loop in plain English."),
        SlashCommandDescriptor("/loops", "List saved loops with last and next run status."),
        SlashCommandDescriptor("/loop pause", "Pause a loop without deleting it."),
        SlashCommandDescriptor("/loop resume", "Resume a paused loop."),
        SlashCommandDescriptor("/loop delete", "Delete a loop after confirmation."),
        SlashCommandDescriptor("/loop history", "Show the last 10 results for a loop."),
        SlashCommandDescriptor("/loop edit", "Change a loop schedule or behaviour."),
        SlashCommandDescriptor("/loop run", "Run a loop immediately."),
        SlashCommandDescriptor("/healthcheck", "Validate Python, dependencies, and provider connectivity."),
        SlashCommandDescriptor("/internet", "Toggle the DuckDuckGo internet skill on/off (used by local models that lack web access)."),
        SlashCommandDescriptor("/reasoning", "Open the maintained reasoning log (logical-thinking.md)."),
        SlashCommandDescriptor("/ui", "Set how Duckln renders: inline (flows in the terminal) | full (split-pane) | auto."),
        SlashCommandDescriptor("/skills", "List Duckln skills, including ones auto-learned from verified runs."),
        SlashCommandDescriptor("/skills show", "Show a single skill note by slug."),
        SlashCommandDescriptor("/skills clear", "Delete auto-learned skills (with confirmation)."),
        SlashCommandDescriptor("/skill add", "Draft a new skill from a natural-language description."),
        SlashCommandDescriptor("/learn", "Show the self-learning loop status and what the next plan will inject."),
        SlashCommandDescriptor("/tools", "List Duckln tools and target availability."),
        SlashCommandDescriptor("/tools add", "Draft a new tool or MCP connector from a natural-language description."),
        SlashCommandDescriptor("/mcp", "List MCP servers and MCP-backed capabilities Duckln can see."),
        SlashCommandDescriptor("/ask", "Ask a question about the active repo — Duckln reads/searches its code to answer."),
        SlashCommandDescriptor("/do", "Tell Duckln to perform an action on the active repo (edit/fix/run) — plans, edits, verifies."),
        SlashCommandDescriptor("/policy", "Configure per-tool approval: /policy allow|deny|default <tool> (e.g. fs.write)."),
        SlashCommandDescriptor("/prefs", "View or set your preferences: /prefs set <key> <value>."),
        SlashCommandDescriptor("/resources", "Show the active target's RAM/disk/CPU, flag any crunch, and the resource-change log."),
    )


def _normalize_runtime_slash_command(command: str) -> str:
    """Map common runtime slash aliases onto the canonical command set."""

    compact = " ".join(command.strip().split())
    repo_aliases = {
        "/repo": "/repos",
        "/repo tracked": "/repos tracked",
        "/repo active": "/repos active",
        "/repo live": "/repos live",
        "/repo path": "/repos path",
        "/repo link": "/repos link",
        "/repo remove": "/repos remove",
        "/repo refresh": "/repos refresh",
        "/repo status": "/repos status",
        "/health": "/healthcheck",
        "/skill": "/skills",
        "/tool": "/tools",
        "/create aws vm": "/cloud create aws vm",
        "/create gcp vm": "/cloud create gcp vm",
        "/create aws": "/cloud create aws vm",
        "/create gcp": "/cloud create gcp vm",
    }
    return repo_aliases.get(compact.lower(), repo_aliases.get(compact, compact))


_MULTIPASS_DOCS_URL = "https://multipass.run/docs/troubleshooting"
_VM_AUTO_RECOVERY_MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class _VmFailureRecipe:
    """Curated fix recipe sourced from official Multipass docs.

    Looked up by error-signature substrings; surfaced when auto-recovery exhausts.
    The fix_command is auto-applied only when the user explicitly chose VM and the
    command is safe (idempotent, bounded scope).
    """

    signature_tokens: tuple[str, ...]
    description: str
    fix_command: str | None
    docs_url: str
    auto_apply: bool = False


_VM_FAILURE_RECIPES: tuple[_VmFailureRecipe, ...] = (
    _VmFailureRecipe(
        signature_tokens=("instance", "already exists"),
        description="A previous VM with this name still exists. Delete it (purge), then retry.",
        fix_command="multipass delete {vm_name} --purge",
        docs_url="https://multipass.run/docs/delete-command",
        auto_apply=True,
    ),
    _VmFailureRecipe(
        signature_tokens=("invalid", "instance name"),
        description="Multipass instance names must be lowercase alphanumeric, hyphens only.",
        fix_command=None,
        docs_url="https://multipass.run/docs/instance-name",
    ),
    _VmFailureRecipe(
        signature_tokens=("invalid", "memory size"),
        description="Memory must be specified as integer GB (e.g. 4G). Re-run /vm and pick a valid size.",
        fix_command=None,
        docs_url="https://multipass.run/docs/launch-command",
    ),
    _VmFailureRecipe(
        signature_tokens=("invalid", "disk size"),
        description="Disk size must be at least 5G. Re-run /vm and increase the disk allocation.",
        fix_command=None,
        docs_url="https://multipass.run/docs/launch-command",
    ),
    _VmFailureRecipe(
        signature_tokens=("could not connect", "multipassd"),
        description="The Multipass daemon is unreachable. Auto-recovery already attempted a restart; if it persists, reinstall Multipass.",
        fix_command=None,
        docs_url="https://multipass.run/docs/troubleshooting#multipass-daemon-not-running",
    ),
    _VmFailureRecipe(
        signature_tokens=("not enough", "available"),
        description="Host doesn't have enough free memory or disk for the requested VM size. Try smaller --cpus / --memory / --disk.",
        fix_command=None,
        docs_url="https://multipass.run/docs/launch-command",
    ),
    _VmFailureRecipe(
        signature_tokens=("could not resolve", "host"),
        description="DNS resolution failed — Multipass cannot reach the image server. Check VPN / proxy / firewall.",
        fix_command="multipass find",
        docs_url="https://multipass.run/docs/launch-command",
    ),
    _VmFailureRecipe(
        signature_tokens=("ssh", "timeout"),
        description="VM provisioned but cloud-init / SSH didn't come up in time. A restart usually unblocks this.",
        fix_command="multipass restart {vm_name}",
        docs_url="https://multipass.run/docs/restart-command",
        auto_apply=True,
    ),
    _VmFailureRecipe(
        signature_tokens=("network", "is unreachable"),
        description="Host has no network route. Bring the network back up before retrying.",
        fix_command=None,
        docs_url="https://multipass.run/docs/troubleshooting",
    ),
)


def _lookup_vm_failure_recipe(failure: VmProvisionResult) -> _VmFailureRecipe | None:
    """Match a recipe whose signature tokens all appear in the failure message/details."""

    haystack = f"{failure.message or ''}\n{failure.technical_details or ''}".lower()
    if not haystack.strip():
        return None
    for recipe in _VM_FAILURE_RECIPES:
        if all(token.lower() in haystack for token in recipe.signature_tokens):
            return recipe
    return None


@dataclass(frozen=True)
class _RepoFailureRecipe:
    """Curated fix recipe for non-Multipass errors hit during repo bring-up / run.

    Same lookup mechanism as `_VmFailureRecipe`, broader coverage. signature_tokens are
    matched as a substring AND set against the failure message + stderr (case-insensitive).
    """

    signature_tokens: tuple[str, ...]
    description: str
    fix_command: str | None
    docs_url: str
    auto_apply: bool = False


_REPO_FAILURE_RECIPES: tuple[_RepoFailureRecipe, ...] = (
    _RepoFailureRecipe(
        signature_tokens=("npm", "eacces"),
        description="npm cannot write to its cache directory. Reset cache permissions.",
        fix_command="sudo chown -R $(whoami) $(npm config get cache)",
        docs_url="https://docs.npmjs.com/resolving-eacces-permissions-errors-when-installing-packages-globally",
    ),
    _RepoFailureRecipe(
        signature_tokens=("npm err", "enotempty"),
        description="npm hit a stale node_modules directory. Remove it and reinstall.",
        fix_command="rm -rf node_modules && npm install",
        docs_url="https://docs.npmjs.com/cli/v9/commands/npm-install",
        auto_apply=True,
    ),
    _RepoFailureRecipe(
        signature_tokens=("resolutionimpossible", "pip"),
        description="pip's resolver hit incompatible version constraints. Try the legacy resolver or relax the requirements.",
        fix_command=None,
        docs_url="https://pip.pypa.io/en/stable/topics/dependency-resolution/",
    ),
    _RepoFailureRecipe(
        signature_tokens=("could not find a version", "pip"),
        description="pip couldn't find a matching wheel for this Python version / platform. Pin a different package version or upgrade Python.",
        fix_command=None,
        docs_url="https://pip.pypa.io/en/stable/cli/pip_install/",
    ),
    _RepoFailureRecipe(
        signature_tokens=("verifying checksum", "go.sum"),
        description="Go module checksum mismatch. Regenerate the verification file.",
        fix_command="go mod tidy",
        docs_url="https://go.dev/ref/mod#go-sum-files",
        auto_apply=True,
    ),
    _RepoFailureRecipe(
        signature_tokens=("cannot connect", "docker daemon"),
        description="Docker daemon isn't running. Start it before retrying.",
        fix_command=None,
        docs_url="https://docs.docker.com/config/daemon/start/",
    ),
    _RepoFailureRecipe(
        signature_tokens=("port", "already in use"),
        description="Another process is bound to the same port. Stop it or pick a different port.",
        fix_command=None,
        docs_url="https://docs.docker.com/network/",
    ),
    _RepoFailureRecipe(
        signature_tokens=("permission denied", ".venv"),
        description="The .venv directory permissions block writes. Recreate it under your user.",
        fix_command="rm -rf .venv",
        docs_url="https://docs.python.org/3/library/venv.html",
        auto_apply=True,
    ),
)


def lookup_repo_failure_recipe(message: str, *, stderr: str = "") -> _RepoFailureRecipe | None:
    """Match a curated recipe against repo-setup or runtime failure output."""

    haystack = f"{message or ''}\n{stderr or ''}".lower()
    if not haystack.strip():
        return None
    for recipe in _REPO_FAILURE_RECIPES:
        if all(token.lower() in haystack for token in recipe.signature_tokens):
            return recipe
    return None


_PIP_BAD_PKG_PATTERN = re.compile(
    r"(?:Could not find a version that satisfies the requirement|No matching distribution found for|ERROR: Could not install packages due to .*?: )\s*([A-Za-z0-9._\-]+)",
    re.IGNORECASE,
)
_NPM_BAD_PKG_PATTERN = re.compile(
    r"npm ERR! 404 .*?'([A-Za-z0-9._\-@/]+)'",
    re.IGNORECASE,
)


def extract_failing_package(message: str, *, stderr: str = "") -> tuple[str, str] | None:
    """Item 4: extract a single bad-package name from setup output for isolate-and-skip.

    Returns (manager, package) when the failure is a recognizable single-package case
    (pip 'No matching distribution', npm 404 fetch). Returns None otherwise — caller
    should fall back to the existing repair workflow.
    """

    haystack = f"{message or ''}\n{stderr or ''}"
    if not haystack.strip():
        return None
    match = _PIP_BAD_PKG_PATTERN.search(haystack)
    if match:
        return ("pip", match.group(1).strip())
    match = _NPM_BAD_PKG_PATTERN.search(haystack)
    if match:
        return ("npm", match.group(1).strip())
    return None


_DOC_EXCERPT_TIMEOUT = 6.0
_DOC_EXCERPT_MAX_CHARS = 600


def _fetch_official_doc_excerpt(url: str, *, http_client_factory: Callable[[], object] | None = None) -> str | None:
    """Item 3: when no recipe matches, fetch the docs page and extract a short excerpt.

    Tightly bounded — short timeout, max ~600 char body slice, ANSI-stripped, HTML tags
    removed via a minimal regex strip (we want a hint, not a parse). Returns None on any
    failure (no httpx, network down, non-200, etc.). Caller decides whether to display.
    """

    try:
        if http_client_factory is None:
            try:
                import httpx  # local import — httpx may not be installed everywhere
            except ImportError:
                return None
            client = httpx.Client(timeout=_DOC_EXCERPT_TIMEOUT, follow_redirects=True)
        else:
            client = http_client_factory()
    except Exception:
        return None
    try:
        response = client.get(url)
        if getattr(response, "status_code", 0) != 200:
            return None
        body = getattr(response, "text", "") or ""
    except Exception:
        return None
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
    if not body:
        return None
    # Strip HTML tags + collapse whitespace. Cheap, not perfect, but enough for a hint.
    text = re.sub(r"<script[^>]*>.*?</script>", " ", body, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None
    return text[:_DOC_EXCERPT_MAX_CHARS].rstrip() + ("…" if len(text) > _DOC_EXCERPT_MAX_CHARS else "")


def _classify_vm_failure(result: VmProvisionResult | None) -> str:
    """Map VM failure type to bounded recovery action."""
    if result is None:
        return "unknown_failure"
    if result.failure_type == VmSetupFailureType.MULTIPASS_NOT_INSTALLED:
        return "install_multipass_first"
    elif result.failure_type == VmSetupFailureType.USER_CANCELLED:
        return "cancelled_by_user"
    elif result.failure_type in (
        VmSetupFailureType.COMMAND_TIMEOUT,
        VmSetupFailureType.NETWORK_ERROR,
    ):
        return "retry_with_diagnostics"
    elif result.failure_type == VmSetupFailureType.DAEMON_NOT_RUNNING:
        return "restart_daemon_and_retry"
    elif result.failure_type == VmSetupFailureType.IMAGE_NOT_FOUND:
        return "purge_image_cache_and_retry"
    elif result.failure_type == VmSetupFailureType.DISK_FULL:
        return "free_disk_space_first"
    elif result.failure_type == VmSetupFailureType.VM_ALREADY_EXISTS:
        return "use_existing_vm"
    elif result.failure_type == VmSetupFailureType.PERMISSION_DENIED:
        return "check_permissions"
    else:
        return "unknown_failure"


def _restart_multipass_daemon_for_os(
    operating_system: str,
    *,
    display: Callable[[str], None],
    runner: ControlledCommandRunner,
) -> bool:
    """Try the OS-canonical command to restart the multipass daemon."""

    os_key = (operating_system or "").strip().lower()
    if os_key in {"darwin", "macos", "mac"}:
        command = "sudo launchctl kickstart -k system/com.canonical.multipassd"
    elif os_key == "linux":
        command = "sudo snap restart multipass"
    elif os_key == "windows":
        command = "powershell -NoProfile -Command \"Restart-Service Multipass\""
    else:
        display("Unknown host OS — cannot auto-restart Multipass daemon.")
        return False
    display(f"Restarting Multipass daemon: {command}")
    result = runner.run(command, timeout_seconds=30.0)
    if result.exit_code == 0 and not result.timed_out:
        display("Multipass daemon restart command sent. Waiting 3 s for it to come back…")
        time.sleep(3)
        return True
    display("Could not auto-restart the Multipass daemon — manual intervention needed.")
    return False


def _attempt_vm_auto_recovery(
    failure: VmProvisionResult,
    *,
    display: Callable[[str], None],
    runner: ControlledCommandRunner,
    operating_system: str,
) -> tuple[bool, str]:
    """Attempt OS-aware automatic recovery. Returns (recovered, summary)."""

    if failure.failure_type == VmSetupFailureType.DAEMON_NOT_RUNNING:
        ok = _restart_multipass_daemon_for_os(operating_system, display=display, runner=runner)
        return ok, ("Restarted Multipass daemon." if ok else "Daemon restart failed.")
    if failure.failure_type == VmSetupFailureType.NETWORK_ERROR:
        display("Likely a transient network blip — waiting 5 s before retrying…")
        time.sleep(5)
        return True, "Waited for network to settle."
    if failure.failure_type == VmSetupFailureType.COMMAND_TIMEOUT:
        display("Launch timed out. Will retry once with the same parameters.")
        return True, "Will retry after timeout."
    if failure.failure_type == VmSetupFailureType.IMAGE_NOT_FOUND:
        display("Purging stale Multipass image cache…")
        result = runner.run("multipass purge", timeout_seconds=30.0)
        ok = result.exit_code == 0 and not result.timed_out
        return ok, ("Purged image cache." if ok else "Could not purge image cache.")
    return False, ""


def _present_vm_failure_recovery(
    failure: VmProvisionResult | None,
    paths: ConfigPaths,
    display: Callable[[str], None],
    select: Callable[[str, tuple[str, ...]], str | None] | None,
    system_probe: Any | None = None,
    *,
    vm_chosen_explicitly: bool = True,
) -> tuple[str, bool]:
    """Present VM failure with recovery options.

    When `vm_chosen_explicitly=True`, the user picked VM as the target — Duckln does NOT
    offer a silent fallback to local. It auto-attempts the OS-aware recovery for
    daemon-down / network / image-not-found and surfaces the official Multipass docs
    when human intervention is required.
    Returns (recovery_action, should_continue).
    """

    if failure is None:
        display("VM setup encountered an unknown error.")
        return "unknown_failure", False

    recovery_action = _classify_vm_failure(failure)

    if recovery_action == "install_multipass_first":
        # Plan 197 F2: Duckln INSTALLS Multipass itself (one Yes) then retries — instead of a
        # hint + "run duckln /vm". On a verified install, return retry so VM creation resumes.
        if system_probe is None:
            system_probe = probe_system()
        _installed = _offer_local_service_install(
            service="multipass",
            runner=ControlledCommandRunner(trace=display, execution_target="local"),
            approve_prompt=None, select_prompt=select, display_output=display, system_probe=system_probe,
        )
        if _installed:
            return "retry_vm_creation", True
        return recovery_action, False

    if recovery_action == "cancelled_by_user":
        display("VM creation was cancelled.")
        return recovery_action, False

    if recovery_action == "use_existing_vm":
        display(f"A VM named '{failure.vm_name}' already exists on this machine.")
        if select is None:
            display(f"Duckln will use the existing VM '{failure.vm_name}'.")
            return recovery_action, True
        choice = select(
            f"VM '{failure.vm_name}' already exists. What would you like to do?",
            ("Use the existing VM", "Try a different name", "Abort"),
        )
        if choice == "Use the existing VM":
            display(f"Okay. Duckln will use the existing VM '{failure.vm_name}'.")
            return "use_existing_vm", True
        elif choice == "Try a different name":
            display("Okay. Duckln will prompt for a new VM name.")
            return "retry_vm_creation", True
        display("VM setup aborted.")
        return "cancelled_by_user", False

    # All remaining cases are recoverable failures. Auto-attempt fixes when possible.
    auto_recoverable = recovery_action in {
        "retry_with_diagnostics",
        "restart_daemon_and_retry",
        "purge_image_cache_and_retry",
    }
    if auto_recoverable:
        if system_probe is None:
            system_probe = probe_system()
        runner = ControlledCommandRunner(trace=display, execution_target="local")
        recovered, summary = _attempt_vm_auto_recovery(
            failure,
            display=display,
            runner=runner,
            operating_system=system_probe.operating_system,
        )
        if recovered:
            display(f"Auto-recovery: {summary}")
            return "retry_vm_creation", True
        display(f"Auto-recovery did not resolve the issue: {summary or 'no automatic fix available'}.")

    # Item C: look up an official-docs-backed fix recipe before falling back to manual prompts.
    recipe = _lookup_vm_failure_recipe(failure)
    if recipe is not None:
        display(f"Duckln researched this in official Multipass docs:")
        display(f"  • {recipe.description}")
        display(f"  • Reference: {recipe.docs_url}")
        if recipe.fix_command is not None:
            applied_command = recipe.fix_command.format(vm_name=failure.vm_name or "")
            if recipe.auto_apply and vm_chosen_explicitly:
                display(f"Applying suggested fix: {applied_command}")
                if system_probe is None:
                    system_probe = probe_system()
                runner = ControlledCommandRunner(trace=display, execution_target="local")
                fix_result = runner.run(applied_command, timeout_seconds=60.0)
                if fix_result.exit_code == 0 and not fix_result.timed_out:
                    display("Fix applied. Retrying VM creation…")
                    return "retry_vm_creation", True
                display("Fix command failed; falling back to manual recovery options.")
            else:
                display(f"Suggested manual fix: {applied_command}")
    else:
        # Item 3: no curated recipe — try a bounded live fetch of the official docs URL
        # so the user gets a relevant excerpt instead of just a link they must click.
        excerpt = _fetch_official_doc_excerpt(_MULTIPASS_DOCS_URL)
        if excerpt:
            display("Duckln fetched the latest Multipass troubleshooting docs:")
            display(f"  {excerpt}")

    # Human intervention needed. Compose a tight error display with concrete next steps.
    display(f"VM setup failed: {failure.message}")
    if failure.technical_details:
        display(f"Technical details: {failure.technical_details}")
    display(f"Official docs (Multipass troubleshooting): {_MULTIPASS_DOCS_URL}")

    if select is None:
        return recovery_action, False

    if vm_chosen_explicitly:
        # User picked VM. Do NOT offer a silent local fallback — only retry / abort.
        choice = select(
            "What would you like to do? (Duckln will keep trying to set up the VM.)",
            ("Retry now", "Cancel — I'll fix this and come back", "Switch to local machine"),
        )
        if choice == "Retry now":
            return "retry_vm_creation", True
        if choice == "Switch to local machine":
            display("Okay. Switching the target to the local machine.")
            return "fallback_to_local", True
        return "cancelled_by_user", False

    choice = select(
        "What would you like to do?",
        ("Retry now", "Use local machine instead", "Cancel"),
    )
    if choice == "Retry now":
        return "retry_vm_creation", True
    if choice == "Use local machine instead":
        return "fallback_to_local", True
    return "cancelled_by_user", False


def _chat_supports_thoughts(chat: object | None) -> bool:
    """Plan 76 Fix E: True when the chat surface has a collapsible thoughts box."""
    return chat is not None and callable(getattr(chat, "add_thought", None))


def _emit_thought(
    chat: object | None,
    text: str,
    *,
    display: Callable[[str], None] | None = None,
) -> None:
    """Route a redacted reasoning line into the chat's collapsible "Duckln's
    thinking" box. Falls back to a dim display line in the plain CLI."""
    line = redact_sensitive_data(str(text)).strip()
    if not line:
        return
    if _chat_supports_thoughts(chat):
        try:
            chat.add_thought(line)
            return
        except Exception:
            pass
    if display is not None:
        display(f"· {line}")


def _open_vm_shell_in_terminal(
    vm_name: str,
    terminal_interface: object | None,
    display: Callable[[str], None],
) -> None:
    """Open an interactive VM shell in the built-in terminal panel."""
    shell_command = f"multipass shell {vm_name}"
    sent = False
    if terminal_interface is not None and hasattr(terminal_interface, "run_terminal_command"):
        sent = bool(terminal_interface.run_terminal_command(command=shell_command))
    if sent:
        display(f"Opening VM shell in the terminal panel — you can run commands inside '{vm_name}' there.")
    else:
        display(f"To access the VM, run: {shell_command}")


def _handoff_vm_transport_blocker(
    *,
    repo: RepoCatalogRecord,
    vm_name: str,
    current: AppConfig,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    approve_prompt: Callable[[str], bool] | None,
    terminal_interface: object | None,
) -> None:
    managed_dir = resolve_managed_project_dir(paths.config_dir, repo)
    failure_summary = (
        f"VM transport failure for {repo.name}. Multipass could not reach VM '{vm_name}' over SSH after restart. "
        f"Bounded probe failed while checking `multipass exec {vm_name} -- echo duckln-vm-ready`. "
        "Signal: ssh connection failed / no route to host."
    )
    write_workflow_state(
        paths.config_dir,
        {
            "active_repo_key": repo.repo_url,
            "active_repo_name": repo.name,
            "active_runtime_execution_target": "vm",
            "active_runtime_repo_key": repo.repo_url,
            "active_runtime_repo_name": repo.name,
            "active_runtime_vm_name": vm_name,
            "active_runtime_cwd": str(managed_dir),
            "active_issue_kind": "run_issue",
            "active_issue_summary": failure_summary,
            "active_incident_category": "vm_bootstrap_failure",
            "active_incident_summary": failure_summary,
            "active_repair_phase": "runtime_failed",
        },
    )
    _sync_chat_objective_status(chat=terminal_interface, config_dir=paths.config_dir)
    display_output(
        f"Duckln could not reach VM '{vm_name}' after restart. Repo setup for {repo.name} has not started yet. "
        "The blocker is VM transport, not the repo itself."
    )
    _run_runtime_repair_workflow(
        repo=repo,
        current=current,
        paths=paths,
        display_output=display_output,
        approve_prompt=approve_prompt,
        terminal_interface=terminal_interface,
        run_summary=failure_summary,
        runtime_command_override=None,
        workflow=_AGENT_CONTEXT_SERVICE.load_workflow_state(config_dir=paths.config_dir),
    )


def _ensure_vm_ready_for_repo_setup(
    *,
    repo: RepoCatalogRecord,
    vm_name: str,
    current: AppConfig,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    approve_prompt: Callable[[str], bool] | None,
    terminal_interface: object | None,
    runner: ControlledCommandRunner,
    ready_message: str,
    inaccessible_message: str,
) -> bool:
    if not ensure_multipass_vm_running(vm_name, display=display_output, runner=runner):
        display_output(inaccessible_message)
        # Plan 193 F1 (WRITE): record the blocker so "what is the issue?" can answer from it.
        try:
            from state.access import write_active_incident

            write_active_incident(
                paths.config_dir,
                summary=f"The VM ‘{vm_name}’ isn’t accessible, so Duckln couldn’t start the setup for {repo.name}.",
                category="vm_not_accessible",
                command=f"multipass info {vm_name}",
                excerpt=f"VM ‘{vm_name}’ is not accessible (state Unknown). Check with: multipass list",
            )
        except Exception:
            pass
        return False
    if not verify_vm_exec_connectivity(vm_name, runner=runner):
        conn_ok = restart_multipass_vm(vm_name, display=display_output, runner=runner)
        if not conn_ok:
            try:
                from state.access import write_active_incident

                write_active_incident(
                    paths.config_dir,
                    summary=f"The VM ‘{vm_name}’ can be seen but Duckln can’t run commands in it (transport blocker), so setup for {repo.name} is paused.",
                    category="vm_transport_blocker",
                    command=f"multipass exec {vm_name} -- true",
                )
            except Exception:
                pass
            _handoff_vm_transport_blocker(
                repo=repo,
                vm_name=vm_name,
                current=current,
                paths=paths,
                display_output=display_output,
                approve_prompt=approve_prompt,
                terminal_interface=terminal_interface,
            )
            return False
    display_output(ready_message)
    _open_vm_shell_in_terminal(vm_name, terminal_interface, display_output)
    return True


_VM_WORD_PATTERN = re.compile(r"(?<![a-z0-9])(vm|vms|virtual machine|virtual machines|multipass)(?![a-z0-9])")
_VM_DELETE_PATTERN = re.compile(r"(?<![a-z0-9])(delete|remove|destroy|purge)(?![a-z0-9])")
_VM_OPEN_PHRASE_PATTERN = re.compile(
    r"(?<![a-z0-9])(?:run|start|open|connect(?:\s+to)?|shell(?:\s+into)?|use|launch)\s+(?:the\s+)?(?:vm|virtual machine|multipass|[a-z0-9_.-]*vm[a-z0-9_.-]*)(?![a-z0-9])",
    re.IGNORECASE,
)
_VM_EXCEPT_PATTERN = re.compile(r"(?<![a-z0-9])except\s+([a-z0-9][a-z0-9_.-]{0,80})(?![a-z0-9_.-])", re.IGNORECASE)


def _vm_free_text_action(message: str) -> str | None:
    normalized = " ".join(str(message or "").strip().lower().split())
    if not normalized or _VM_WORD_PATTERN.search(normalized) is None:
        return None
    if _VM_DELETE_PATTERN.search(normalized) is not None:
        return "delete"
    if _VM_OPEN_PHRASE_PATTERN.search(normalized) is not None:
        return "open"
    return None


def _matching_vm_names_from_text(message: str, vm_names: tuple[str, ...]) -> tuple[str, ...]:
    folded = f" {str(message or '').casefold()} "
    matched = tuple(name for name in vm_names if f" {name.casefold()} " in folded or name.casefold() in folded)
    return matched


def _select_vm_name_for_action(
    *,
    prompt: str,
    vm_names: tuple[str, ...],
    select_prompt: Callable[[str, tuple[str, ...]], str | None] | None,
    display_output: Callable[[str], None],
    verb: str,
) -> str | None:
    if not vm_names:
        display_output("No Multipass VMs found on this machine. Run `/vm` to create one.")
        return None
    if len(vm_names) == 1 and select_prompt is None:
        return vm_names[0]
    choices = tuple(f"{verb} VM '{name}'" for name in vm_names) + ("Cancel",)
    if select_prompt is None:
        display_output(join_blocks(prompt, bullet_block(choices[:-1])))
        return None
    selected = select_prompt(prompt, choices)
    if selected is None or selected == "Cancel":
        display_output("VM action cancelled.")
        return None
    match = re.search(r"'([^']+)'", selected)
    return match.group(1) if match is not None else None


def _handle_vm_open_free_text(
    *,
    message: str,
    current: AppConfig,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    select_prompt: Callable[[str, tuple[str, ...]], str | None] | None,
    terminal_interface: object | None,
    runner: ControlledCommandRunner | None = None,
) -> bool:
    runner_instance = runner or ControlledCommandRunner(trace=display_output, execution_target="local")
    # Plan 197 F2/F6: if Multipass is missing, Duckln offers to install it (one Yes) + verifies,
    # then continues here — instead of a bare hint. On decline/failure, detailed steps were shown.
    if not _ensure_service_available(
        service="multipass", runner=runner_instance, approve_prompt=None,
        select_prompt=select_prompt, display_output=display_output, system_probe=current,
    ):
        return True
    vm_names = list_multipass_vm_names(runner=runner_instance)
    matched = _matching_vm_names_from_text(message, vm_names)
    vm_name = matched[0] if matched else _select_vm_name_for_action(
        prompt="Which VM should Duckln open?",
        vm_names=vm_names,
        select_prompt=select_prompt,
        display_output=display_output,
        verb="Open",
    )
    if not vm_name:
        return True
    if vm_name not in vm_names:
        display_output(f"Duckln could not find VM '{vm_name}'. Run `multipass list` or choose one from `/vm`.")
        return True
    if not ensure_multipass_vm_running(vm_name, display=display_output, runner=runner_instance):
        return True
    _persist_session_execution_target(paths=paths, execution_target="vm", vm_name=vm_name)
    _open_vm_shell_in_terminal(vm_name, terminal_interface, display_output)
    display_output(f"Duckln is now targeting VM '{vm_name}'.")
    return True


def _vm_delete_targets_from_message(message: str, vm_names: tuple[str, ...]) -> tuple[str, ...]:
    normalized = " ".join(str(message or "").strip().lower().split())
    matched = _matching_vm_names_from_text(message, vm_names)
    except_match = _VM_EXCEPT_PATTERN.search(message or "")
    if except_match is not None and ("all vm" in normalized or "all vms" in normalized or "all multipass" in normalized):
        keep_name = except_match.group(1).casefold()
        return tuple(name for name in vm_names if name.casefold() != keep_name)
    return matched


def _handle_vm_delete_free_text(
    *,
    message: str,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    select_prompt: Callable[[str, tuple[str, ...]], str | None] | None,
    approve_prompt: Callable[[str], bool] | None,
    runner: ControlledCommandRunner | None = None,
) -> bool:
    if not is_multipass_installed():
        display_output("Multipass is not installed, so there are no local VMs for Duckln to delete.")
        return True
    runner_instance = runner or ControlledCommandRunner(trace=display_output, execution_target="local")
    vm_names = list_multipass_vm_names(runner=runner_instance)
    targets = _vm_delete_targets_from_message(message, vm_names)
    if not targets:
        selected = _select_vm_name_for_action(
            prompt="Which VM should Duckln delete?",
            vm_names=vm_names,
            select_prompt=select_prompt,
            display_output=display_output,
            verb="Delete",
        )
        targets = (selected,) if selected else ()
    if not targets:
        return True
    missing = tuple(name for name in targets if name not in vm_names)
    if missing:
        display_output(f"Duckln could not find VM(s): {', '.join(missing)}.")
        return True
    target_text = ", ".join(targets)
    if approve_prompt is None:
        display_output(f"Duckln needs confirmation before deleting VM(s): {target_text}.")
        return True
    if not approve_prompt(f"Delete Multipass VM(s) permanently with purge: {target_text}?"):
        display_output(f"Duckln cancelled deleting VM(s): {target_text}.")
        return True
    failed: list[str] = []
    for vm_name in targets:
        result = runner_instance.run(f"multipass delete {shlex.quote(vm_name)} --purge", timeout_seconds=120.0)
        if result.exit_code != 0 or result.timed_out:
            failed.append(vm_name)
    invalidate_multipass_vm_cache()
    active_vm = _session_vm_name(paths.config_dir)
    if active_vm in targets and active_vm not in failed:
        _persist_session_execution_target(paths=paths, execution_target="local", vm_name=None)
    if failed:
        display_output(f"Duckln could not delete VM(s): {', '.join(failed)}.")
    deleted = tuple(name for name in targets if name not in failed)
    if deleted:
        # Plan 150: record each deletion (kept 7 days) and TELL the user.
        try:
            store = initialize_state_store(paths.config_dir)
            for name in deleted:
                store.record_deletion(kind="vm", name=name, detail="multipass delete --purge")
        except Exception:
            pass
        display_output(f"Duckln deleted VM(s): {', '.join(deleted)}. A record of this is kept for 7 days.")
    return True


def _handle_unified_delete_free_text(
    *,
    message: str,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    approve_prompt: Callable[[str], bool] | None,
    runner: ControlledCommandRunner | None = None,
) -> bool:
    """Plan 155 F4: route a 'delete @name' / 'delete the container/cloud/repo' message through
    the unified `delete_controls` flow (container, AWS/GCP, custom-repo) with ONE confirm + the
    7-day audit. Returns True if it handled the delete; False (e.g. a VM target or unresolved)
    so the existing VM handler still takes VMs. No `/delete` command — natural language + @name."""
    from duckln import delete_controls

    if not delete_controls.message_requests_delete(message) or approve_prompt is None:
        return False
    target = delete_controls.resolve_delete_target(message, paths.config_dir)
    if target is None or target.kind == "vm":
        return False  # VM / ambiguous → let the VM handler (with its picker) take it
    runner_instance = runner or ControlledCommandRunner(trace=display_output, execution_target="local")

    def _run(cmd: str) -> int:
        result = runner_instance.run(cmd, timeout_seconds=120.0)
        return 1 if getattr(result, "timed_out", False) else int(getattr(result, "exit_code", 1) or 0)

    delete_controls.perform_delete(
        target, config_dir=paths.config_dir, run_cmd=_run,
        confirm=approve_prompt, display=display_output, execution_target=target.kind,
    )
    # Clear the active runtime reference if we just deleted the active container/cloud target.
    try:
        if target.kind in {"container", "aws", "gcp"} and _session_execution_target(paths.config_dir) == target.kind:
            _persist_session_execution_target(paths=paths, execution_target="local", vm_name=None)
    except Exception:
        pass
    return True


def _handle_cleanup_command(
    *,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    select_prompt: Callable[[str, tuple[str, ...]], str | None] | None,
    approve_prompt: Callable[[str], bool] | None,
    runner: ControlledCommandRunner | None = None,
) -> None:
    """Plan 198 F3: `/cleanup` — a first-class provision to DELETE installed local resources
    (Multipass VMs, Docker containers + images, cloud instances, custom repos). Lists whatever is
    actually installed (no hardcoded names), lets the user pick, and deletes after ONE confirm
    (the delete_controls audit + safety floor). Also the RESET path for a corrupt VM."""
    from duckln import delete_controls

    targets = delete_controls.mention_suggestions(paths.config_dir)
    if not targets:
        display_output("Nothing to clean up — no VMs, Docker containers/images, cloud instances, or custom repos found.")
        return
    if select_prompt is None or approve_prompt is None:
        display_output("Cleanup needs interactive selection + confirmation. " + "; ".join(t.label for t in targets))
        return
    labels = tuple(f"{t.label}" + (f" — {t.detail}" if t.detail else "") for t in targets) + ("Cancel",)
    picked = select_prompt("Choose a resource to DELETE (irreversible):", labels)
    if picked is None or picked == "Cancel":
        display_output("Cleanup cancelled — nothing was deleted.")
        return
    target = next((t for t, lbl in zip(targets, labels) if lbl == picked), None)
    if target is None:
        display_output("Cleanup cancelled — nothing was deleted.")
        return
    runner_instance = runner or ControlledCommandRunner(trace=display_output, execution_target="local")

    def _run(cmd: str) -> int:
        result = runner_instance.run(cmd, timeout_seconds=120.0)
        return 1 if getattr(result, "timed_out", False) else int(getattr(result, "exit_code", 1) or 0)

    deleted = delete_controls.perform_delete(
        target, config_dir=paths.config_dir, run_cmd=_run,
        confirm=approve_prompt, display=display_output, execution_target=target.kind,
    )
    # Clear the active runtime reference if we just deleted the active VM/container/cloud target.
    try:
        if deleted and target.kind in {"vm", "container", "aws", "gcp"} and _session_execution_target(paths.config_dir) == target.kind:
            _persist_session_execution_target(paths=paths, execution_target="local", vm_name=None)
    except Exception:
        pass


def _handle_vm_management_free_text(
    *,
    message: str,
    current: AppConfig,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    select_prompt: Callable[[str, tuple[str, ...]], str | None] | None,
    approve_prompt: Callable[[str], bool] | None,
    terminal_interface: object | None,
    runner: ControlledCommandRunner | None = None,
) -> bool:
    action = _vm_free_text_action(message)
    if action == "open":
        return _handle_vm_open_free_text(
            message=message,
            current=current,
            paths=paths,
            display_output=display_output,
            select_prompt=select_prompt,
            terminal_interface=terminal_interface,
            runner=runner,
        )
    if action == "delete":
        return _handle_vm_delete_free_text(
            message=message,
            paths=paths,
            display_output=display_output,
            select_prompt=select_prompt,
            approve_prompt=approve_prompt,
            runner=runner,
        )
    return False


def _vm_runtime_install_completed(sync_result: object) -> bool:
    if not bool(getattr(sync_result, "ok", False)):
        return False
    message = str(getattr(sync_result, "message", "") or "").casefold()
    if "without duckln installed inside it" in message:
        return False
    connection_commands = getattr(sync_result, "connection_commands", ())
    if isinstance(connection_commands, (tuple, list)):
        return any(str(command).strip().casefold() == "duckln" for command in connection_commands)
    return True


def handle_session_command(
    command: str,
    current: AppConfig,
    paths: ConfigPaths,
    *,
    system_probe=None,
    select: Callable[[str, tuple[str, ...]], str | None] | None = None,
    secret_prompt: Callable[[str], str | None] | None = None,
    text_prompt: Callable[[str, str], str | None] | None = None,
    approve: Callable[[str], bool] | None = None,
    display: Callable[[str], None] = print,
    client: Any | None = None,
    terminal_interface: object | None = None,
    preselected_repo: "RepoCatalogRecord | None" = None,
) -> AppConfig:
    """Handle a runtime slash command without losing the last good config.

    Plan 184 F10: `preselected_repo` lets a pasted GitHub URL flow straight into the `/repos`
    setup path (environment picker → bring-up) without re-opening the catalog UI."""

    initialize_runtime_storage(paths)
    command = _normalize_runtime_slash_command(command)

    if command == "/":
        _display_help(display=display)
        return open_command_palette(
            current,
            paths,
            select=select,
            secret_prompt=secret_prompt,
            text_prompt=text_prompt,
            display=display,
            client=client,
            terminal_interface=terminal_interface,
        )
    if command == "/help":
        _display_help(display=display)
        return current
    if command == "/mode":
        return update_runtime_mode(current, paths, select=select, display=display)
    if command == "/provider":
        return update_runtime_provider(
            current,
            paths,
            select=select,
            secret_prompt=secret_prompt,
            text_prompt=text_prompt,
            display=display,
            client=client,
        )
    if command == "/model":
        return update_runtime_model(
            current,
            paths,
            select=select,
            text_prompt=text_prompt,
            display=display,
            client=client,
        )
    if command == "/status":
        # Plan 179 C3: show which model powers each agent node (+ host memory/GPU).
        display(_render_agent_routing_status(paths.config_dir, current))
        return current
    if command == "/models":
        # Plan 179 C3: two-tier config — Unified vs Specialized per-agent model routing.
        return _handle_agent_routing_command(
            current, paths, select=select, text_prompt=text_prompt, display=display
        )
    if command == "/config":
        return open_runtime_config_menu(
            current,
            paths,
            select=select,
            secret_prompt=secret_prompt,
            text_prompt=text_prompt,
            display=display,
            client=client,
        )
    if command == "/repos refresh":
        try:
            display("Refreshing the cached repo catalog...")
            result = refresh_local_repo_catalog(paths.config_dir)
        except Exception as exc:
            display(f"Retryable error: {exc}")
            return current
        if result.ok:
            display(result.message)
        else:
            display(f"Retryable error: {result.message}")
        return current
    if command == "/repos tracked":
        display(_render_tracked_repos_summary(paths.config_dir))
        return current
    if command.startswith("/repos ") and command not in {
        "/repos active",
        "/repos status",
        "/repos history",
        "/repos live",
        "/repos path",
        "/repos link",
        "/repos remove",
        "/repos refresh",
        "/repos tracked",
    }:
        target_filter = command.split(" ", 1)[1].strip()
        display(_render_tracked_repos_summary(paths.config_dir, target_filter=target_filter))
        return current
    if command == "/repos active":
        display(_render_active_repo_summary(paths.config_dir))
        return current
    if command == "/repos status":
        display(_render_active_deploy_status_summary(paths.config_dir))
        return current
    if command == "/repos history":
        display(_render_repo_history_summary(paths.config_dir, limit=10))
        return current
    if command == "/repos live":
        display(_render_live_repo_sessions_summary(paths.config_dir))
        return current
    if command == "/repos path":
        display(_render_active_repo_path(paths.config_dir))
        return current
    if command == "/repos link":
        _handle_repo_link_command(
            paths=paths,
            system_probe=system_probe,
            text_prompt=text_prompt,
            display_output=display,
        )
        return current
    if command == "/repos remove":
        _handle_repo_remove_command(
            current=current,
            paths=paths,
            select_prompt=select,
            approve_prompt=approve,
            display_output=display,
        )
        return current
    if command == "/repos":
        if select is None:
            from duckln.config import _default_select_prompt

            select_prompt = _default_select_prompt
        else:
            select_prompt = select
        try:
            selected_repo = open_repo_catalog(
                paths,
                select=select_prompt,
                text_prompt=text_prompt,
                client=client,
                preselected_repo=preselected_repo,
            )
        except ValueError as exc:
            display(f"Retryable error: {exc}")
            return current

        if selected_repo is None:
            display("No repository selected.")
            return current

        current_execution_target = _session_execution_target(paths.config_dir)
        current_vm_name = _session_vm_name(paths.config_dir)
        environment_prompt = _repo_environment_choice_prompt(
            repo_name=selected_repo.name,
            current_execution_target=current_execution_target,
            current_vm_name=current_vm_name,
        )
        environment_label = select_prompt(
            environment_prompt.message,
            conversation_choice_labels(environment_prompt),
        )
        chosen_environment = resolve_conversation_choice(environment_prompt, selected_label=environment_label)
        if chosen_environment is None or chosen_environment.action_key == "cancel":
            display(f"You selected {selected_repo.name}.")
            display(f"Okay. I’ll leave {selected_repo.name} untouched.")
            _persist_repo_followup_state(
                paths=paths,
                system_probe=system_probe,
                repo=selected_repo,
                preflight=None,
                choice_prompt=environment_prompt,
                chosen_option=chosen_environment,
                completed_step="cancelled",
                pending_next_action=None,
                execution_target=current_execution_target,
                vm_name=current_vm_name,
            )
            return current

        execution_target = current_execution_target
        vm_name = current_vm_name
        auto_trigger_repo_setup = False  # Track if we should skip repo_choice_prompt
        
        if chosen_environment.action_key == "use_local":
            execution_target = "local"
            vm_name = None
            _persist_session_execution_target(paths=paths, execution_target=execution_target, vm_name=vm_name)
            display(f"Duckln will use the local machine for {selected_repo.name}.")
        elif chosen_environment.action_key == "use_vm":
            _create_new_vm = True  # default: run the VM creation flow
            stale_vm_runner = ControlledCommandRunner(trace=display, execution_target="local")
            # Plan 196 F4: distinguish a PROBE FAILURE (multipass daemon slow/down → None)
            # from a genuine empty list (()). A transient `multipass list` failure must NOT
            # discard the saved VM as "stale" (that was the "can't load existing VMs" bug).
            probed_vm_names = list_multipass_vms_or_none(runner=stale_vm_runner)
            probe_failed = probed_vm_names is None
            existing_vm_names = probed_vm_names or ()
            if (
                not existing_vm_names
                and current_execution_target == "vm"
                and current_vm_name
                and vm_exists_in_multipass(current_vm_name)
            ):
                existing_vm_names = (current_vm_name,)
            saved_vm_is_real = current_execution_target == "vm" and bool(current_vm_name) and current_vm_name in existing_vm_names
            if current_execution_target == "vm" and current_vm_name and probe_failed and not saved_vm_is_real:
                # Probe failed — keep the saved VM; don't wipe it on a transient error.
                display(
                    f"Couldn't reach Multipass to confirm your VM right now — keeping '{current_vm_name}'. "
                    "If it's genuinely gone, I'll create a fresh one when you retry."
                )
                existing_vm_names = (current_vm_name,)
                saved_vm_is_real = True
            elif current_execution_target == "vm" and current_vm_name and not saved_vm_is_real:
                display(
                    f"Saved VM '{current_vm_name}' is not in multipass list — clearing stale state and creating a fresh VM."
                )
                _persist_session_execution_target(paths=paths, execution_target="local", vm_name=None)
                current_vm_name = None
                current_execution_target = "local"
            if existing_vm_names:
                vm_choices: list[str] = []
                if saved_vm_is_real and current_vm_name:
                    vm_choices.append(f"Use existing VM '{current_vm_name}'")
                for existing_vm_name in existing_vm_names:
                    if existing_vm_name != current_vm_name:
                        vm_choices.append(f"Use existing VM '{existing_vm_name}'")
                vm_choices.extend(("Create a new VM (configure CPU, RAM, disk)", "Cancel"))
                vm_prompt = (
                    f"VM '{current_vm_name}' is already active. Choose the Ubuntu VM Duckln should use for this repo:"
                    if saved_vm_is_real and current_vm_name
                    else "Choose the Ubuntu VM Duckln should use for this repo:"
                )
                vm_choice_label = select_prompt(vm_prompt, tuple(vm_choices))
                if vm_choice_label is None or "cancel" in str(vm_choice_label).lower():
                    display(f"Okay. I'll leave {selected_repo.name} untouched.")
                    return current
                if "create a new vm" in str(vm_choice_label).lower():
                    _create_new_vm = True
                else:
                    # User wants to reuse the existing VM — verify it's reachable first.
                    _create_new_vm = False
                    selected_vm_match = re.search(r"'([^']+)'", str(vm_choice_label))
                    selected_vm_name = selected_vm_match.group(1) if selected_vm_match is not None else current_vm_name
                    current_vm_name = selected_vm_name
                    runner_for_check = ControlledCommandRunner(trace=display, execution_target="local")
                    if not _ensure_vm_ready_for_repo_setup(
                        repo=selected_repo,
                        vm_name=current_vm_name,
                        current=current,
                        paths=paths,
                        display_output=display,
                        approve_prompt=approve,
                        terminal_interface=terminal_interface,
                        runner=runner_for_check,
                        ready_message=f"VM '{current_vm_name}' is ready.",
                        inaccessible_message=f"Cannot start repo setup: VM '{current_vm_name}' is not accessible.",
                    ):
                        return current
                    execution_target = "vm"
                    vm_name = current_vm_name
                    display(f"Duckln will use Ubuntu VM '{current_vm_name}' for {selected_repo.name}.")
                    auto_trigger_repo_setup = True
            if _create_new_vm:
                display(f"Duckln will create an Ubuntu VM for {selected_repo.name}...")
                create_result: VmProvisionResult | None = None
                final_recovery_action = "unknown_failure"
                final_should_continue = False
                for attempt in range(_VM_AUTO_RECOVERY_MAX_ATTEMPTS):
                    if attempt > 0:
                        display(f"Auto-recovery attempt {attempt + 1}/{_VM_AUTO_RECOVERY_MAX_ATTEMPTS} — retrying VM creation…")
                    create_result = create_multipass_vm(
                        paths,
                        text_prompt=text_prompt,
                        display=display,
                        approve_prompt=approve,
                        system_probe=system_probe,
                        chat=terminal_interface,
                    )
                    if create_result is not None and create_result.ok:
                        break
                    final_recovery_action, final_should_continue = _present_vm_failure_recovery(
                        create_result,
                        paths,
                        display,
                        select_prompt,
                        system_probe=system_probe,
                        vm_chosen_explicitly=True,
                    )
                    if final_recovery_action == "retry_vm_creation" and final_should_continue:
                        # Loop and retry create_multipass_vm with auto-recovered state.
                        continue
                    break

                if create_result is None or not create_result.ok:
                    if final_should_continue and final_recovery_action == "fallback_to_local":
                        display(f"Okay. I'll set up {selected_repo.name} on the local machine instead.")
                        execution_target = "local"
                        vm_name = None
                        _persist_session_execution_target(paths=paths, execution_target=execution_target, vm_name=vm_name)
                        auto_trigger_repo_setup = True
                    elif final_should_continue and final_recovery_action == "use_existing_vm":
                        display(f"Duckln will use the existing VM '{create_result.vm_name}' for {selected_repo.name}.")
                        configure_existing_multipass_vm(
                            create_result.vm_name,
                            paths,
                            select=select_prompt,
                            display=display,
                        )
                        execution_target = "vm"
                        vm_name = create_result.vm_name
                        auto_trigger_repo_setup = True
                        _persist_session_execution_target(paths=paths, execution_target=execution_target, vm_name=vm_name)
                    else:
                        _persist_repo_followup_state(
                            paths=paths,
                            system_probe=system_probe,
                            repo=selected_repo,
                            preflight=None,
                            choice_prompt=environment_prompt,
                            chosen_option=chosen_environment,
                            completed_step="vm_create_failed",
                            pending_next_action=None,
                            execution_target=current_execution_target,
                            vm_name=current_vm_name,
                        )
                        return current
                else:
                    # VM creation succeeded
                    display(f"VM {create_result.vm_name} created successfully.")
                    configure_result = configure_existing_multipass_vm(
                        create_result.vm_name,
                        paths,
                        select=select_prompt,
                        display=display,
                    )
                    runtime_installed_in_vm = _vm_runtime_install_completed(configure_result)
                    if not configure_result.ok or not runtime_installed_in_vm:
                        display(f"Warning: {configure_result.message}")
                        display(
                            f"Duckln paused {selected_repo.name} setup because VM runtime install is not complete yet."
                        )
                        _persist_repo_followup_state(
                            paths=paths,
                            system_probe=system_probe,
                            repo=selected_repo,
                            preflight=None,
                            choice_prompt=environment_prompt,
                            chosen_option=chosen_environment,
                            completed_step="vm_runtime_install_pending",
                            pending_next_action="set_up_repo",
                            execution_target="vm",
                            vm_name=create_result.vm_name,
                        )
                        _persist_session_execution_target(
                            paths=paths,
                            execution_target="vm",
                            vm_name=create_result.vm_name,
                        )
                        return current

                    execution_target = "vm"
                    vm_name = create_result.vm_name
                    auto_trigger_repo_setup = True
                    # Item 5: drop the user into the VM via the embedded terminal pane so the
                    # uniform-experience promise holds — they can see and interact with the VM.
                    _open_vm_shell_in_terminal(create_result.vm_name, terminal_interface, display)
            
            _persist_session_execution_target(paths=paths, execution_target=execution_target, vm_name=vm_name)
        elif chosen_environment.action_key == "use_cloud":
            display(f"Duckln will only continue with {selected_repo.name} after you choose a cloud target.")
            _handle_cloud_command(
                paths=paths,
                select_prompt=select_prompt,
                text_prompt=text_prompt,
                approve_prompt=approve,
                display_output=display,
                terminal_interface=None,
            )
            workflow = _AGENT_CONTEXT_SERVICE.load_workflow_state(config_dir=paths.config_dir)
            cloud_target = (
                str(workflow.active_runtime_execution_target or "").strip().lower()
                if workflow is not None and workflow.active_runtime_execution_target
                else ""
            )
            if cloud_target not in {"aws", "gcp"}:
                display(f"Duckln left {selected_repo.name} untouched because no cloud VM is active yet.")
                _persist_repo_followup_state(
                    paths=paths,
                    system_probe=system_probe,
                    repo=selected_repo,
                    preflight=None,
                    choice_prompt=environment_prompt,
                    chosen_option=chosen_environment,
                    completed_step="cloud_target_pending",
                    pending_next_action=None,
                    execution_target=current_execution_target,
                    vm_name=current_vm_name,
                )
                return current
            execution_target = cloud_target
            vm_name = None
            _persist_session_execution_target(paths=paths, execution_target=execution_target, vm_name=vm_name)
            display(f"Duckln will use the active {cloud_target.upper()} target for {selected_repo.name}.")
        elif chosen_environment.action_key == "use_docker":
            execution_target = "local"
            vm_name = None
            _persist_session_execution_target(paths=paths, execution_target=execution_target, vm_name=vm_name)
            # Plan 197 F3: you chose Docker — Duckln takes the initiative to make sure it's there,
            # offering to install it (one Yes) when missing, instead of failing later with a hint.
            if shutil.which("docker") is None:
                _ensure_service_available(
                    service="docker",
                    runner=ControlledCommandRunner(trace=display, execution_target="local"),
                    approve_prompt=approve, select_prompt=select, display_output=display,
                    system_probe=system_probe,
                )
            display(
                f"Duckln will keep {selected_repo.name} on the local machine and prefer documented Docker or Compose steps when the repo points there."
            )

        _track_catalog_repo_selection(
            paths=paths,
            repo=selected_repo,
            execution_target=execution_target,
            vm_name=vm_name,
        )
        display(f"You selected {selected_repo.name}.")
        choice_prompt = None
        # NEW: Auto-trigger repo setup if VM was successfully created from environment choice
        if auto_trigger_repo_setup:
            display(f"Okay. I'll start the smallest verifiable setup path for {selected_repo.name}.")
            _surface_repo_recall(selected_repo, paths.config_dir, display)  # Plan 194 F6
            display("Reading the repo files and checking the safest setup route.")
            # Auto-use "Set it up" action without showing choice prompt
            chosen_action = ConversationChoiceOption(
                label="Set it up",
                action_key="set_it_up",
                description="",
                recommended=True,
            )
        else:
            choice_prompt = _repo_choice_prompt(repo_name=selected_repo.name)
            next_action_label = select_prompt(
                choice_prompt.message,
                conversation_choice_labels(choice_prompt),
            )
            chosen_action = resolve_conversation_choice(choice_prompt, selected_label=next_action_label)

        if chosen_action is None or chosen_action.action_key == "cancel":
            display(f"Okay. I’ll leave {selected_repo.name} untouched.")
            _persist_repo_followup_state(
                paths=paths,
                system_probe=system_probe,
                repo=selected_repo,
                preflight=None,
                choice_prompt=choice_prompt,
                chosen_option=chosen_action,
                completed_step="cancelled",
                pending_next_action=None,
                execution_target=execution_target,
                vm_name=vm_name,
            )
            return current

        preflight = assess_repo_preflight(
            selected_repo,
            config_dir=paths.config_dir,
            execution_target=execution_target,
            system_probe=system_probe,
            context_service=_AGENT_CONTEXT_SERVICE,
        )
        if chosen_action.action_key == "inspect_requirements":
            repo_knowledge = _AGENT_CONTEXT_SERVICE.resolve_repo_knowledge(
                repo=selected_repo,
                config_dir=paths.config_dir,
                project_dir=resolve_managed_project_dir(paths.config_dir, selected_repo),
            )
            display(_render_repo_requirements_summary(repo=selected_repo, preflight=preflight, repo_knowledge=repo_knowledge))
            _persist_repo_followup_state(
                paths=paths,
                system_probe=system_probe,
                repo=selected_repo,
                preflight=preflight,
                choice_prompt=choice_prompt,
                chosen_option=chosen_action,
                completed_step="inspect_requirements",
                pending_next_action="set_up_repo",
                execution_target=execution_target,
                vm_name=vm_name,
            )
            return current

        if preflight.fit_status == "not_recommended":
            warning_prompt = _repo_warning_choice_prompt(
                repo_name=selected_repo.name,
                offer_vm=bool(preflight.local_vm_recommendation) and execution_target != "vm",
            )
            followup_label = select_prompt(
                warning_prompt.message,
                conversation_choice_labels(warning_prompt),
            )
            followup = resolve_conversation_choice(warning_prompt, selected_label=followup_label)
            if followup is None or followup.action_key == "cancel":
                display(f"Okay. I’ll leave {selected_repo.name} alone for now.")
                _persist_repo_followup_state(
                    paths=paths,
                    system_probe=system_probe,
                    repo=selected_repo,
                    preflight=preflight,
                    choice_prompt=warning_prompt,
                    chosen_option=followup,
                    completed_step="cancelled",
                    pending_next_action=None,
                    execution_target=execution_target,
                    vm_name=vm_name,
                )
                return current
            if followup.action_key == "pick_lighter_repo":
                display("Okay. I’ll leave this one here so you can pick something lighter.")
                _persist_repo_followup_state(
                    paths=paths,
                    system_probe=system_probe,
                    repo=selected_repo,
                    preflight=preflight,
                    choice_prompt=warning_prompt,
                    chosen_option=followup,
                    completed_step="pick_lighter_repo",
                    pending_next_action="recommend_best_fit",
                    execution_target=execution_target,
                    vm_name=vm_name,
                )
                return current
            if followup.action_key == "use_vm":
                display(f"Okay. I’ll switch {selected_repo.name} onto a VM path instead.")
                create_result = create_multipass_vm(
                    paths,
                    text_prompt=text_prompt,
                    display=display,
                    approve_prompt=approve,
                    system_probe=system_probe,
                )
                if create_result is None or not create_result.ok:
                    _persist_repo_followup_state(
                        paths=paths,
                        system_probe=system_probe,
                        repo=selected_repo,
                        preflight=preflight,
                        choice_prompt=warning_prompt,
                        chosen_option=followup,
                        completed_step="vm_create_failed",
                        pending_next_action="set_up_repo",
                        execution_target=execution_target,
                        vm_name=vm_name,
                    )
                    return current
                configure_result = configure_existing_multipass_vm(
                    create_result.vm_name,
                    paths,
                    select=select_prompt,
                    display=display,
                )
                runtime_installed_in_vm = _vm_runtime_install_completed(configure_result)
                if not configure_result.ok or not runtime_installed_in_vm:
                    display(f"Warning: {configure_result.message}")
                    display(
                        f"Duckln paused {selected_repo.name} setup because VM runtime install is not complete yet."
                    )
                    _persist_repo_followup_state(
                        paths=paths,
                        system_probe=system_probe,
                        repo=selected_repo,
                        preflight=preflight,
                        choice_prompt=warning_prompt,
                        chosen_option=followup,
                        completed_step="vm_runtime_install_pending",
                        pending_next_action="set_up_repo",
                        execution_target="vm",
                        vm_name=create_result.vm_name,
                    )
                    return current
                execution_target = "vm"
                vm_name = create_result.vm_name
                _persist_session_execution_target(paths=paths, execution_target=execution_target, vm_name=vm_name)
                display(f"Okay. I’ll continue with {selected_repo.name} inside the VM.")
                if execution_target == "vm" and vm_name:
                    _vm_runner2 = ControlledCommandRunner(trace=display, execution_target="local")
                    if not _ensure_vm_ready_for_repo_setup(
                        repo=selected_repo,
                        vm_name=vm_name,
                        current=current,
                        paths=paths,
                        display_output=display,
                        approve_prompt=approve,
                        terminal_interface=terminal_interface,
                        runner=_vm_runner2,
                        ready_message=f"VM ‘{vm_name}’ is ready.",
                        inaccessible_message=f"Cannot start repo setup: VM ‘{vm_name}’ is not accessible.",
                    ):
                        return current
                _persist_repo_followup_state(
                    paths=paths,
                    system_probe=system_probe,
                    repo=selected_repo,
                    preflight=preflight,
                    choice_prompt=warning_prompt,
                    chosen_option=followup,
                    completed_step="use_vm",
                    pending_next_action="set_up_repo",
                    execution_target=execution_target,
                    vm_name=vm_name,
                )
                bring_up_selected_repo(
                    selected_repo,
                    current.mode,
                    paths,
                    approve=approve,
                    display=display,
                    runtime_provider=current.provider.value,
                    execution_target=execution_target,
                    vm_name=vm_name,
                    system_probe=system_probe,
                    plan_mode_enabled=getattr(current, "plan_mode_enabled", False),
                )
                _persist_repo_followup_state(
                    paths=paths,
                    system_probe=system_probe,
                    repo=selected_repo,
                    preflight=preflight,
                    choice_prompt=warning_prompt,
                    chosen_option=followup,
                    completed_step="use_vm",
                    pending_next_action="set_up_repo",
                    execution_target=execution_target,
                    vm_name=vm_name,
                )
                return current

        display(f"Okay. I’ll start the smallest verifiable setup path for {selected_repo.name}.")
        _surface_repo_recall(selected_repo, paths.config_dir, display)  # Plan 194 F6
        display("Reading the repo files and checking the safest setup route.")
        if execution_target == "vm" and vm_name:
            _vm_runner = ControlledCommandRunner(trace=display, execution_target="local")
            if not _ensure_vm_ready_for_repo_setup(
                repo=selected_repo,
                vm_name=vm_name,
                current=current,
                paths=paths,
                display_output=display,
                approve_prompt=approve,
                terminal_interface=terminal_interface,
                runner=_vm_runner,
                ready_message=f"VM ‘{vm_name}’ is ready. Starting repo setup inside the VM.",
                inaccessible_message=f"Cannot start repo setup: VM ‘{vm_name}’ is not accessible. Check with: multipass list",
            ):
                return current
        _persist_repo_followup_state(
            paths=paths,
            system_probe=system_probe,
            repo=selected_repo,
            preflight=preflight,
            choice_prompt=choice_prompt,
            chosen_option=chosen_action,
            completed_step="set_up_repo",
            pending_next_action="run_repo",
            execution_target=execution_target,
            vm_name=vm_name,
        )
        # Item B: drive the repo through clone+install → run → repair-on-fail in one orchestrated
        # pass so the user is not left at "What now?" between phases.
        _orchestrate_repo_to_running(
            repo=selected_repo,
            current=current,
            paths=paths,
            approve=approve,
            display=display,
            chat=terminal_interface,
            runtime_provider=current.provider.value,
            execution_target=execution_target,
            vm_name=vm_name,
            system_probe=system_probe,
            terminal_interface=terminal_interface,
        )
        _persist_repo_followup_state(
            paths=paths,
            system_probe=system_probe,
            repo=selected_repo,
            preflight=preflight,
            choice_prompt=choice_prompt,
            chosen_option=chosen_action,
            completed_step="set_up_repo",
            # Plan 79 Fix 2 / Plan 188: Plan Mode is always on — the resumable next action is to
            # re-draft / finish the plan (so "retry"/"check now" picks up here, e.g. after the
            # model becomes reachable).
            pending_next_action="set_up_repo",
            execution_target=execution_target,
            vm_name=vm_name,
        )
        return current
    if command == "/vm":
        _vm_list_runner = ControlledCommandRunner(trace=display, execution_target="local")
        # Plan 197 F2/F6: Multipass is required for /vm. If it's missing, Duckln offers to install
        # it (one Yes) + verifies, then continues — instead of a hint. On decline/failure the
        # detailed step-by-step remediation was shown; stop here so the user can follow it.
        if not _ensure_service_available(
            service="multipass", runner=_vm_list_runner, approve_prompt=approve,
            select_prompt=select, display_output=display, system_probe=system_probe,
        ):
            return current
        # Plan 196 F2: LIST existing VMs first (read-only, S0 — never gated) so the user can
        # ATTACH an existing VM, not only ever create a new one. Only the CREATE mutation is
        # gated by Plan Mode below. This restores the list/attach flow the always-on gate had
        # bypassed (the "can't list VMs / can't load existing VMs" bug). Uses the F4 failure-
        # aware probe so a transient `multipass list` failure isn't reported as "no VMs".
        _probed_vms = list_multipass_vms_or_none(runner=_vm_list_runner)
        if _probed_vms is None:
            display(
                "Couldn't reach Multipass to list your VMs (is it installed and running?). "
                "Continuing to the create flow — run `multipass list` in a terminal to check."
            )
            _existing_vms: tuple[str, ...] = ()
        else:
            _existing_vms = _probed_vms
        if _existing_vms and select is not None:
            display(f"You have {len(_existing_vms)} existing VM(s): " + ", ".join(_existing_vms) + ".")
            _vm_use_prefix = "Use existing VM '"
            _vm_opts = tuple(
                [f"{_vm_use_prefix}{n}'" for n in _existing_vms] + ["Create a new VM", "Cancel"]
            )
            _vm_pick = select("Choose the VM Duckln should use, or create a new one:", _vm_opts)
            if _vm_pick is None or _vm_pick == "Cancel":
                display("No VM change made.")
                return current
            if _vm_pick.startswith(_vm_use_prefix):
                _chosen_vm = _vm_pick[len(_vm_use_prefix):].rstrip("'")
                # Plan 199 F6: drive the chosen existing VM to Running (start/recover/daemon-restart,
                # delete+recreate on consent) before using it — an existing VM may be stopped/corrupt.
                from duckln.vm import ensure_vm_ready as _ensure_vm_ready
                _ensure_vm_ready(
                    _chosen_vm, display=display, runner=_vm_list_runner, confirm=approve,
                    os_type=getattr(system_probe, "operating_system", None) or platform.system(),
                )
                # Attaching to an existing VM is non-destructive (session/config only) → NOT gated.
                configure_result = configure_existing_multipass_vm(
                    _chosen_vm, paths, select=select, display=display
                )
                if not configure_result.ok:
                    display(f"Warning: {configure_result.message}")
                    return current
                _persist_session_execution_target(
                    paths=paths, execution_target="vm", vm_name=_chosen_vm
                )
                display(f"Duckln is now using VM '{_chosen_vm}'.")
                return current
            # else "Create a new VM" → fall through to the gate/legacy create below.
        # Plan 73 Phase A: gate VM provisioning behind an approved plan when
        # Plan Mode is ON — no direct Multipass mutation without approval.
        if getattr(current, "plan_mode_enabled", False):
            from duckln.repo_bringup import gate_mutation_or_draft

            gate_mutation_or_draft(
                intended_steps=(
                    ("Check Multipass is installed", "multipass version", "version prints"),
                    ("Launch the Ubuntu VM", "multipass launch --name duckln-vm", "VM reaches Running state"),
                    ("Verify the VM is reachable", "multipass exec duckln-vm -- echo ok", "prints ok"),
                ),
                objective="Provision an Ubuntu Multipass VM",
                repo_slug=None,
                paths=paths,
                current=current,
                display=display,
                context_summary="vm provisioning; target=vm",
            )
            return current
        try:
            display("Starting VM creation flow...")
            max_retries = 1
            attempt = 0
            create_result = None
            
            while attempt <= max_retries:
                create_result = create_multipass_vm(
                    paths,
                    text_prompt=text_prompt,
                    display=display,
                    approve_prompt=approve,
                    system_probe=system_probe,
                )
                
                if create_result is None or not create_result.ok:
                    recovery_action, should_continue = _present_vm_failure_recovery(
                        create_result,
                        paths,
                        display,
                        select,
                        system_probe=system_probe,
                    )
                    
                    if should_continue and recovery_action == "retry_vm_creation":
                        attempt += 1
                        if attempt <= max_retries:
                            display("Retrying VM creation...")
                            continue
                        else:
                            display("Max retry attempts reached.")
                            return current
                    elif should_continue and recovery_action == "use_existing_vm":
                        break  # proceed to configure step below using existing vm_name
                    else:
                        # Other recovery actions or user cancelled
                        return current
                else:
                    # VM creation succeeded
                    break

            use_existing = (
                create_result is not None
                and not create_result.ok
                and create_result.failure_type is not None
                and create_result.failure_type.value == "vm_already_exists"
            )
            if create_result is not None and (create_result.ok or use_existing):
                label = f"VM {create_result.vm_name} created successfully." if create_result.ok else f"Duckln will use existing VM '{create_result.vm_name}'."
                display(label)
                configure_result = configure_existing_multipass_vm(
                    create_result.vm_name,
                    paths,
                    select=select,
                    display=display,
                )
                if not configure_result.ok:
                    display(f"Warning: {configure_result.message}")
            
        except KeyboardInterrupt:
            from duckln.shell import clear_global_abort
            clear_global_abort()
            display("VM operation cancelled.")
        except ValueError as exc:
            display(f"Retryable error: {exc}")
        return current
    if command == "/cloud":
        _handle_cloud_command(
            paths=paths,
            select_prompt=select,
            text_prompt=text_prompt,
            approve_prompt=approve,
            display_output=display,
            terminal_interface=terminal_interface,
        )
        return current
    if command in ("/cleanup", "/delete"):
        # Plan 198 F3: delete installed local resources (VMs / Docker containers+images / cloud /
        # custom repos) — pick + confirm once. Also the reset path for a corrupt VM.
        _handle_cleanup_command(
            paths=paths, display_output=display, select_prompt=select, approve_prompt=approve,
        )
        return current
    if command == "/explore":
        _handle_explore_command(
            paths=paths,
            current=current,
            select_prompt=select,
            text_prompt=text_prompt,
            approve_prompt=approve,
            display_output=display,
            terminal_interface=terminal_interface,
            system_probe=system_probe,
        )
        return current
    if command in {"/cloud create aws vm", "/cloud create aws"}:
        if select is None:
            display("Cloud VM creation needs interactive selection so Duckln can show approved AWS VM types.")
            return current
        _handle_cloud_create_command(
            provider="aws",
            paths=paths,
            select_prompt=select,
            text_prompt=text_prompt,
            approve_prompt=approve,
            display_output=display,
            terminal_interface=terminal_interface,
        )
        return current
    if command in {"/cloud create gcp vm", "/cloud create gcp"}:
        if select is None:
            display("Cloud VM creation needs interactive selection so Duckln can show approved GCP VM types.")
            return current
        _handle_cloud_create_command(
            provider="gcp",
            paths=paths,
            select_prompt=select,
            text_prompt=text_prompt,
            approve_prompt=approve,
            display_output=display,
            terminal_interface=terminal_interface,
        )
        return current
    if command == "/loops" or command.startswith("/loop"):
        _handle_loop_command(
            command=command,
            paths=paths,
            text_prompt=text_prompt,
            select_prompt=select,
            approve_prompt=approve,
            display_output=display,
        )
        return current
    if command == "/memory clear":
        try:
            factory_reset = _handle_memory_clear_command(paths, select=select, display=display)
        except ValueError as exc:
            display(f"Retryable error: {exc}")
            return current
        if factory_reset:
            raise SessionExitRequested("Factory reset completed; session state was cleared.")
        return current
    if command == "/remember":
        display(_remember_current_context(current=current, paths=paths))
        return current
    if command.startswith("/failures"):
        return _handle_failures_command(
            command=command,
            current=current,
            paths=paths,
            display=display,
            approve=approve,
        )
    if command.startswith("/agents"):
        return _handle_agents_command(
            command=command,
            current=current,
            paths=paths,
            display=display,
        )
    if command.startswith("/plan"):
        return _handle_plan_command(
            command=command,
            current=current,
            paths=paths,
            display=display,
            approve=approve,
            client=client,
            terminal_interface=terminal_interface,
            text_prompt=text_prompt,
            select_prompt=select,
        )
    if command == "/healthcheck":
        report = run_healthcheck(current, client=client)
        from state.store import initialize_state_store

        initialize_state_store(paths.config_dir).upsert_healthcheck_state(
            scope_key="active-config",
            status="pass" if report.ok else "fail",
            summary="Environment healthcheck passed." if report.ok else "Environment healthcheck requires attention.",
            metadata={"checks": list(report.lines)},
        )
        display(report.render())
        return current

    if command == "/tools":
        display(_render_tools_summary(paths.config_dir))
        return current

    if command == "/mcp":
        display(_render_mcp_summary(paths.config_dir))
        return current

    if command == "/ask" or command.startswith("/ask "):
        _handle_ask_command(
            command=command,
            current=current,
            paths=paths,
            display=display,
            approve=approve,
            terminal_interface=terminal_interface,
        )
        return current

    if command == "/do" or command.startswith("/do "):
        _handle_do_command(
            command=command,
            current=current,
            paths=paths,
            display=display,
            approve=approve,
            terminal_interface=terminal_interface,
        )
        return current

    if command == "/policy" or command.startswith("/policy "):
        _handle_policy_command(command=command, paths=paths, display=display)
        return current

    if command == "/prefs" or command.startswith("/prefs "):
        _handle_prefs_command(command=command, paths=paths, display=display)
        return current

    if command == "/resources" or command.startswith("/resources "):
        _handle_resources_command(command=command, paths=paths, display=display)
        return current

    if command.startswith("/skill add") or command.startswith("/tools add"):
        _handle_extension_add_command(
            command=command,
            config_dir=paths.config_dir,
            text_prompt=text_prompt,
            select_prompt=select,
            display_output=display,
        )
        return current

    if command == "/skills" or command.startswith("/skills "):
        parts = command.split()
        sub = parts[1] if len(parts) > 1 else ""
        if sub == "show" and len(parts) > 2:
            display(_render_skill_detail(paths.config_dir, parts[2]))
            return current
        if sub == "clear":
            display(_clear_learned_skills(paths.config_dir, approve=approve))
            return current
        # Default: list materialized skill notes (read-only).
        display(_render_skills_summary(paths.config_dir))
        return current

    if command == "/learn" or command.startswith("/learn "):
        display(_render_learning_status(paths.config_dir))
        return current

    if command == "/ui" or command.startswith("/ui "):
        # Plan 80 Fix 4: choose how Duckln renders — inline (flows in the terminal,
        # doesn't take over the screen) | full (split-pane TUI with the live VM
        # terminal) | auto. Takes effect on the next launch.
        from duckln.config import save_app_config
        sub = command[len("/ui"):].strip().lower()
        if sub in {"", "status"}:
            display(f"UI mode: {getattr(current, 'duckln_ui', 'auto')}. Use `/ui inline | full | auto` (applies next launch).")
            return current
        if sub not in {"auto", "inline", "full"}:
            display("Usage: /ui inline | full | auto")
            return current
        updated = replace(current, duckln_ui=sub)
        save_app_config(updated, paths)
        if sub == "inline":
            display("UI mode set to inline — Duckln will flow in the terminal (not fullscreen) on next launch.")
        elif sub == "full":
            display("UI mode set to full — split-pane TUI with the live terminal on next launch.")
        else:
            display("UI mode set to auto on next launch.")
        return updated

    if command == "/reasoning" or command == "/think":
        # Plan 141 F2: open the latest maintained logical-thinking.md reasoning log. A
        # reliable way to open it from inside the TUI (where a terminal link click is
        # captured by the app); also works as the click target.
        opener = getattr(terminal_interface, "open_last_reasoning_file", None) if terminal_interface is not None else None
        opened = bool(opener()) if callable(opener) else False
        if opened:
            display("Opened the reasoning log (logical-thinking.md) in your default app.")
        else:
            from duckln.recovery import thinking_log_path
            p = thinking_log_path(paths.config_dir)
            if p.exists():
                display(f"Reasoning log: {p}")
            else:
                display("No reasoning log yet — it's written when Duckln plans or recovers.")
        return current

    if command == "/internet" or command.startswith("/internet "):
        # Item D: toggle the DuckDuckGo internet skill. Sub-commands: on / off / status.
        from duckln.internet_skill import (
            is_internet_enabled,
            render_internet_status,
            set_internet_enabled,
        )

        sub = command[len("/internet"):].strip().lower()
        if sub in {"", "status"}:
            display(render_internet_status(paths.config_dir))
            return current
        if sub in {"on", "enable", "yes"}:
            set_internet_enabled(paths.config_dir, True)
            display("Internet skill ON. Local models can now search the web via DuckDuckGo.")
            return current
        if sub in {"off", "disable", "no"}:
            set_internet_enabled(paths.config_dir, False)
            display("Internet skill OFF. No web calls will be made.")
            return current
        display(f"Usage: /internet on | /internet off | /internet status. Currently: {render_internet_status(paths.config_dir)}")
        return current

    display(f"Retryable error: Unknown slash command {command}.")
    return current


def _handle_agents_command(
    *,
    command: str,
    current: AppConfig,
    paths: ConfigPaths,
    display: Callable[[str], None],
) -> AppConfig:
    """Plan 65 Phase 5: /agents slash command suite.

    Subcommands:
    - `/agents` (no args) — list harness agents and their roles.
    - `/agents trace <session_id> [agent]` — render the timeline.
    - `/agents costs <session_id>` — token + wall-clock totals.
    """
    parts = command.strip().split()
    subcommand = parts[1] if len(parts) > 1 else "list"
    try:
        from duckln.harness.trace import (
            render_agents_list, render_costs, render_trace_timeline,
        )
    except Exception as exc:
        display(f"Retryable error: harness observability unavailable ({exc}).")
        return current
    if subcommand == "list":
        display(render_agents_list(config_dir=paths.config_dir))
        return current
    if subcommand == "trace":
        if len(parts) < 3:
            display("Usage: /agents trace <session_id> [agent_name]")
            return current
        session_id = parts[2]
        agent = parts[3] if len(parts) > 3 else None
        display(render_trace_timeline(
            config_dir=paths.config_dir, session_id=session_id, agent=agent,
        ))
        return current
    if subcommand == "costs":
        if len(parts) < 3:
            display("Usage: /agents costs <session_id>")
            return current
        display(render_costs(config_dir=paths.config_dir, session_id=parts[2]))
        return current
    display(
        f"Unknown /agents subcommand '{subcommand}'. "
        "Use: /agents | /agents trace <session> | /agents costs <session>"
    )
    return current


def _handle_failures_command(
    *,
    command: str,
    current: AppConfig,
    paths: ConfigPaths,
    display: Callable[[str], None],
    approve: Callable[[str], bool] | None,
) -> AppConfig:
    """Plan 61 Fix C: /failures slash command — show, clear, or tune the
    persistent install-failure memory."""
    from agent.memory import AGENT_MEMORY_DIR_NAME, FAILURES_DIR_NAME
    from state.access import read_failure_memory_state
    from duckln.config import save_app_config
    from dataclasses import replace as _dc_replace

    parts = command.strip().split()
    subcommand = parts[1] if len(parts) > 1 else "show"

    failures_dir = paths.config_dir / AGENT_MEMORY_DIR_NAME / FAILURES_DIR_NAME

    if subcommand == "show":
        if not failures_dir.exists():
            display("No failure records yet. Duckln has no install failures to forget.")
            return current
        records: list[tuple[str, dict]] = []
        for fp in sorted(failures_dir.iterdir()):
            if fp.suffix != ".md":
                continue
            slug = fp.stem
            for rec in read_failure_memory_state(paths.config_dir, repo_slug=slug):
                records.append((slug, rec))
        if not records:
            display("No failure records yet.")
            return current
        # Sort by last_seen descending, take top 20.
        records.sort(key=lambda x: str(x[1].get("last_seen", "")), reverse=True)
        display(f"Persistent install-failure memory (most recent 20 of {len(records)}):")
        display(f"  Freshness window: {current.failure_window_hours:.1f}h")
        for slug, rec in records[:20]:
            cmd = str(rec.get("command", ""))[:80]
            target = rec.get("execution_target", "?")
            retries = rec.get("retry_count", 1)
            last = rec.get("last_seen", "?")
            display(f"  [{slug}] {target} • {cmd} • retries={retries} • last_seen={last}")
        return current

    if subcommand == "clear":
        if not failures_dir.exists():
            display("Nothing to clear — failure memory is already empty.")
            return current
        files = [fp for fp in failures_dir.iterdir() if fp.suffix == ".md"]
        if not files:
            display("Nothing to clear — failure memory is already empty.")
            return current
        if approve is not None:
            if not approve(f"Delete {len(files)} failure-memory file(s) under {failures_dir}? [y/n]"):
                display("Cancelled — no files deleted.")
                return current
        deleted = 0
        for fp in files:
            try:
                fp.unlink()
                deleted += 1
            except OSError:
                pass
        display(f"Cleared {deleted} failure-memory file(s).")
        return current

    if subcommand == "window":
        if len(parts) < 3:
            display(
                f"Current freshness window: {current.failure_window_hours:.1f}h. "
                f"Usage: /failures window <hours>  (range 0.5 to 168)."
            )
            return current
        try:
            new_hours = float(parts[2])
        except ValueError:
            display(f"Retryable error: '{parts[2]}' is not a valid number of hours.")
            return current
        if new_hours < 0.5 or new_hours > 168.0:
            display("Retryable error: hours must be between 0.5 and 168 (one week).")
            return current
        updated = _dc_replace(current, failure_window_hours=new_hours)
        save_app_config(updated, paths)
        display(f"Failure freshness window updated to {new_hours:.1f}h.")
        return updated

    display(
        f"Unknown /failures subcommand '{subcommand}'. "
        "Use: /failures show | /failures clear | /failures window <hours>"
    )
    return current


def _handle_plan_command(
    *,
    command: str,
    current: AppConfig,
    paths: ConfigPaths,
    display: Callable[[str], None],
    approve: Callable[[str], bool] | None,
    client: Any | None = None,
    terminal_interface: object | None = None,
    text_prompt: Callable[[str, str], str | None] | None = None,
    select_prompt: Callable[[str, tuple[str, ...]], str | None] | None = None,
) -> AppConfig:
    """Plan 67: /plan slash command — toggle Plan Mode and manage pending plans."""
    from dataclasses import replace as _dc_replace
    from duckln.config import save_app_config
    from duckln.plan_mode import (
        MAX_AMENDMENTS,
        PLAN_STATUS_APPROVED,
        PLAN_STATUS_EDITED,
        PLAN_STATUS_REJECTED,
        PlanRecord,
        ensure_plan_dir,
        mark_status,
        parse_plan_markdown,
        plan_markdown_path,
        render_plan_markdown,
    )
    from duckln.ui import render_plan_oneline, render_plan_panel
    from state.access import (
        append_plan_history,
        clear_pending_plan,
        list_plan_history,
        read_pending_plan,
        write_pending_plan,
    )

    parts = command.strip().split()
    subcommand = parts[1] if len(parts) > 1 else "status"

    if subcommand == "status" or subcommand in {"", "show"}:
        pending = read_pending_plan(paths.config_dir)
        # Plan 188: Plan Mode is always on.
        state_label = "on"
        if pending is None:
            display(f"Plan Mode: {state_label}. No pending plan.")
            if subcommand == "show":
                display("Ask Duckln to set up or deploy a repo and it'll draft a plan to review.")
            return current
        try:
            plan = PlanRecord.from_dict(pending)
        except (KeyError, ValueError, TypeError) as exc:
            display(f"Plan Mode: {state_label}. Pending plan is corrupted ({exc}).")
            return current
        if subcommand == "show":
            display(render_plan_panel(plan))
        else:
            display(f"Plan Mode: {state_label}. Pending: {render_plan_oneline(plan)}")
        return current

    # Plan 188: Plan Mode is ALWAYS ON — `/plan on` and `/plan off` no longer toggle anything.
    # Duckln auto-decides: it plans a repo setup/deploy (with a quick pre-check you approve) and
    # answers everything else directly.
    if subcommand in {"on", "off"}:
        display(
            "Plan Mode is always on now — Duckln decides for you: it plans a repo setup or deploy "
            "(with a quick pre-check you approve) and answers everything else directly. There's "
            "nothing to turn on or off. Use `/plan show` to see a pending plan and `/plan approve` "
            "to run it."
        )
        return current

    if subcommand == "precheck":
        value = (parts[2].lower() if len(parts) > 2 else "").strip()
        if value not in {"on", "off", "ask"}:
            display(
                f"Pre-check is '{current.plan_precheck}'. Before drafting a plan, "
                "Duckln can probe the target (what's installed, is the repo cloned) "
                "so the plan only includes what's missing. "
                "Use `/plan precheck on|off|ask`."
            )
            return current
        updated = _dc_replace(current, plan_precheck=value)
        save_app_config(updated, paths)
        if value == "on":
            display("Pre-check on. Duckln will probe the target before drafting each plan.")
        elif value == "off":
            display("Pre-check off. Duckln drafts plans without probing first.")
        else:
            display("Pre-check set to ask. Duckln will ask before each plan whether to probe first.")
        return updated

    # Plan 135 F3: `/plan continue` is the clear verb for resuming a paused/amended plan
    # — it shares the approve path, which already RESUMES via `resume_with_approved_plan`
    # + `_done_steps` (skips completed steps), so a re-run after a failure/amendment or a
    # mid-setup model/provider switch continues from where it left, never from scratch.
    if subcommand in {"approve", "continue"}:
        pending = read_pending_plan(paths.config_dir)
        if pending is None:
            _noun = "continue" if subcommand == "continue" else "approve"
            # Plan 168 F3: when planning was interrupted (e.g. a model 429) there's no written
            # plan, but a paused SETUP objective may exist — don't dead-end at "Run /repos".
            _wf = _AGENT_CONTEXT_SERVICE.load_workflow_state(config_dir=paths.config_dir)
            _kind = str(getattr(_wf, "active_objective_kind", "") or "").strip()
            _repo_name = str(getattr(_wf, "active_objective_repo_name", "") or "").strip()
            if _kind == "repo_deploy" and _repo_name:
                display(
                    f"Setup of {_repo_name} is paused (planning was interrupted, so there's no "
                    f"drafted plan yet). It resumes from where it left — say 'continue setting up "
                    f"{_repo_name}' (or 're-run {_repo_name}'); completed steps (clone/install) are "
                    "skipped. A plan will be drafted now that the model is reachable."
                )
                return current
            display(f"No pending plan to {_noun}. Run `/repos` (with Plan Mode on) first.")
            return current
        try:
            plan = PlanRecord.from_dict(pending)
        except (KeyError, ValueError, TypeError) as exc:
            display(f"Retryable error: pending plan is corrupted ({exc}). Run `/plan reject` to discard.")
            return current
        approved = mark_status(plan, PLAN_STATUS_APPROVED, note="approved by user")
        write_pending_plan(paths.config_dir, approved.to_dict())
        display(render_plan_panel(approved))
        display(
            "Plan approved. Duckln will now follow it strictly. If a step fails, "
            "you will see a cause + fix and a chance to /plan approve the amendment."
        )
        try:
            from duckln.repo_bringup import resume_with_approved_plan

            session_target = _session_execution_target(paths.config_dir)
            session_vm = _session_vm_name(paths.config_dir)
            if _chat_supports_thoughts(terminal_interface):
                try:
                    terminal_interface.clear_thoughts()
                except Exception:
                    pass
            outcome = resume_with_approved_plan(
                plan=approved,
                paths=paths,
                current=current,
                approve=approve,
                display=display,
                vm_name=session_vm,
                pane_executor=terminal_interface,
                emit_thought=lambda t: _emit_thought(terminal_interface, t, display=display),
                # Plan 133 F1: a value-prompt so a resource resize can take a CUSTOM GB
                # (not just y/n). Adapts the TUI's keyword-default prompt to the
                # positional (message, default) shape apply_resize expects.
                text_prompt=(
                    (lambda msg, default="": terminal_interface.prompt_text(msg, default=default))
                    if hasattr(terminal_interface, "prompt_text") else None
                ),
                # Plan 162 F2 (C2): a MASKED prompt so a required repo secret (API key/token/
                # DSN) is collected hidden and injected into .env — never echoed or stored.
                secret_prompt=(
                    (lambda msg: terminal_interface.prompt_secret(msg))
                    if hasattr(terminal_interface, "prompt_secret") else None
                ),
                # Plan 162 F5 (Plan 160-A): a 3-way chooser so a destructive step can be
                # approved-for-the-whole-session, not re-prompted each time.
                select=(
                    (lambda msg, options: terminal_interface.select_choice(msg, options))
                    if hasattr(terminal_interface, "select_choice") else None
                ),
            )
            # A pending amendment means the plan paused for re-approval, not a
            # terminal failure — don't record it as completed/failed yet.
            pending_after = read_pending_plan(paths.config_dir)
            amended = bool(pending_after) and str(pending_after.get("status")) in (
                "amended", "awaiting_user",
            )
            if amended:
                display("Plan paused for an amendment — run `/plan continue` to resume from where it left.")
                return current
            final_status = (
                "completed" if outcome and getattr(outcome, "verification_passed", False) else "failed"
            )
            append_plan_history(paths.config_dir, approved.to_dict(), decision=final_status)
            clear_pending_plan(paths.config_dir)
            # Plan 80 Fix 2: on success, show Duckln's own celebration message
            # (carries "your repo is ready! 🎉"); otherwise the plain status.
            if final_status == "completed" and outcome is not None and getattr(outcome, "message", ""):
                display(outcome.message)
            else:
                display(f"Plan {final_status}.")
            # Plan 83 Fix 4: after a successful setup, intelligently help the user
            # set any required API keys (ask provider when they're alternatives,
            # write the value into .env on the target; value redacted in logs).
            if final_status == "completed":
                try:
                    from duckln.repo_bringup import offer_env_key_setup

                    offer_env_key_setup(
                        plan=approved,
                        paths=paths,
                        execution_target=_session_execution_target(paths.config_dir),
                        vm_name=session_vm,
                        display=display,
                        text_prompt=text_prompt,
                        select_prompt=select_prompt,
                    )
                except Exception:
                    pass
        except Exception as exc:
            display(f"Plan execution raised: {exc}")
        return current

    if subcommand == "reject":
        pending = read_pending_plan(paths.config_dir)
        if pending is None:
            display("No pending plan to reject.")
            return current
        try:
            plan = PlanRecord.from_dict(pending)
            decision_note = "rejected by user"
            append_plan_history(
                paths.config_dir,
                mark_status(plan, PLAN_STATUS_REJECTED, note=decision_note).to_dict(),
                decision=PLAN_STATUS_REJECTED,
            )
        except (KeyError, ValueError, TypeError):
            pass
        clear_pending_plan(paths.config_dir)
        display("Pending plan rejected and discarded.")
        return current

    if subcommand == "edit":
        # `/plan edit apply` is an alias for `/plan reload`.
        if len(parts) > 2 and parts[2].strip().lower() == "apply":
            return _reload_pending_plan_from_disk(paths=paths, display=display)
        pending = read_pending_plan(paths.config_dir)
        if pending is None:
            display("No pending plan to edit.")
            return current
        try:
            plan = PlanRecord.from_dict(pending)
        except (KeyError, ValueError, TypeError) as exc:
            display(f"Retryable error: pending plan is corrupted ({exc}). Run `/plan reject` to discard.")
            return current
        ensure_plan_dir(paths.config_dir)
        path = plan_markdown_path(paths.config_dir, plan.plan_id)
        path.write_text(render_plan_markdown(plan), encoding="utf-8")
        # Plan 69 Fix 2: never launch a TTY editor inside the Textual split-pane
        # UI — it can't get a clean terminal and hangs/garbles. Instead write the
        # file, open it non-blocking in a GUI editor when possible, and let the
        # user apply changes with `/plan reload`.
        opened = _open_path_non_blocking(str(path))
        if opened:
            display(f"Opened the plan in your editor: {path}")
        else:
            display(f"Plan written to: {path}")
        in_textual_ui = terminal_interface is not None
        if (not in_textual_ui) and (os.environ.get("EDITOR") or "").strip():
            # Pure-TTY session with an explicit $EDITOR: safe to spawn it.
            editor = os.environ["EDITOR"].strip()
            try:
                import subprocess as _subproc

                result = _subproc.run([editor, str(path)])
                if result.returncode != 0:
                    display(f"Editor exited with status {result.returncode}; pending plan unchanged.")
                    return current
            except FileNotFoundError:
                display(f"Editor '{editor}' not found; edit the file directly, then run `/plan reload`.")
                return current
            return _reload_pending_plan_from_disk(paths=paths, display=display)
        display("Edit the file, then run `/plan reload` to apply your changes (or `/plan reject` to discard).")
        return current

    if subcommand == "reload":
        return _reload_pending_plan_from_disk(paths=paths, display=display)

    if subcommand == "history":
        history = list_plan_history(paths.config_dir, limit=20)
        if not history:
            display("No plan history yet.")
            return current
        display(f"Plan history (most recent {len(history)}):")
        for entry in history:
            display(
                f"  • {entry.get('plan_id', '?')[:8]} "
                f"[{entry.get('decision', '?')}] "
                f"steps={entry.get('step_count', '?')} "
                f"amendments={entry.get('amendment_count', 0)} "
                f"objective={(entry.get('objective') or '')[:60]}"
            )
        return current

    if subcommand == "retry":
        # Plan 73 D4 (Loop): discard any pending plan and re-run the task with
        # the latest learning injected. Regeneration happens on the next
        # `/repos` (which now injects the matching verified skill + reflections).
        from state.access import clear_pending_plan

        clear_pending_plan(paths.config_dir)
        display(
            "Cleared the pending plan. Re-run `/repos` and pick the repo — the new "
            "plan will inject what Duckln learned from the last run (verified skills "
            "+ reflections). Run `/learn` to see what will be injected."
        )
        return current

    display(
        f"Unknown /plan subcommand '{subcommand}'. "
        "Use: /plan | /plan on | /plan off | /plan show | /plan approve | "
        "/plan reject | /plan edit | /plan reload | /plan retry | /plan history"
    )
    return current


def _reload_pending_plan_from_disk(*, paths: ConfigPaths, display: Callable[[str], None]) -> AppConfig:
    """Plan 69: re-read the edited plan markdown and replace the pending plan.

    Loaded by `/plan reload` (and `/plan edit apply`). Keeps the original on
    any validation failure so a bad edit never destroys the pending plan.
    """
    from duckln.plan_mode import (
        PlanRecord,
        parse_plan_markdown,
        plan_markdown_path,
    )
    from duckln.ui import render_plan_panel
    from state.access import read_pending_plan, write_pending_plan

    pending = read_pending_plan(paths.config_dir)
    if pending is None:
        display("No pending plan to reload.")
        return _load_or_default_config(paths)
    try:
        plan = PlanRecord.from_dict(pending)
    except (KeyError, ValueError, TypeError) as exc:
        display(f"Retryable error: pending plan is corrupted ({exc}). Run `/plan reject` to discard.")
        return _load_or_default_config(paths)
    path = plan_markdown_path(paths.config_dir, plan.plan_id)
    if not path.exists():
        display(f"No edited plan file found at {path}. Run `/plan edit` first.")
        return _load_or_default_config(paths)
    try:
        edited = parse_plan_markdown(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        display(f"Retryable error: edited plan failed validation — {exc}. Original kept.")
        return _load_or_default_config(paths)
    write_pending_plan(paths.config_dir, edited.to_dict())
    display(render_plan_panel(edited))
    display("Plan reloaded from your edits. Run `/plan approve` to execute.")
    return _load_or_default_config(paths)


def _load_or_default_config(paths: ConfigPaths) -> AppConfig:
    """Best-effort load of the current config for handlers that must return one."""
    try:
        from duckln.config import load_app_config

        cfg = load_app_config(paths)
        if cfg is not None:
            return cfg
    except Exception:
        pass
    return AppConfig(
        provider=Provider.OPENAI, model="", api_key=None, mode=ControlMode.HITL,
    )


# Plan 185: the Plan-184 deterministic capability/recommendation chat handler (which emitted
# hardcoded recommendation/why sentences) was REMOVED. These queries now flow to the LLM
# conversation, grounded in deterministic host+repo facts via context_assembler (capability_facts),
# so the model explains the 'why' in its own words. Fact-gathering lives in duckln.capability_facts.


_MODE_ELEVATION_RE = re.compile(
    r"(don'?t ask|do not ask|without (asking|permission)|no permission|stop asking|"
    r"complete (the )?(full|whole|entire)|do it all|do everything|just do it|run everything|"
    r"finish it (all|completely)|report (at|in) the end|auto[- ]?(run|complete|mode)|"
    r"no need to ask|do the whole)",
    re.IGNORECASE,
)


def _maybe_offer_mode_elevation(
    message: str,
    *,
    current,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    approve_prompt: Callable[[str], bool] | None,
):
    """Plan 183 F11: "complete it without asking / do it all and report at the end" is a request
    for higher autonomy. In a non-autonomous mode (HITL/HOTL) Duckln can't silently execute and it
    won't refuse — it OFFERS a CONSENTED switch to auto (HOOTLWO) for the session (one confirm, not
    per-step), with the destructive/irreversible (S4) floor kept intact. Returns the (possibly
    updated) config when this was a mode-elevation message, else None (fall through to normal chat)."""
    from dataclasses import replace as _dc_replace
    from duckln.modes import ControlMode

    if not _MODE_ELEVATION_RE.search(message or ""):
        return None
    mode = getattr(current, "mode", None)
    if mode is ControlMode.HOOTLWO:
        display_output(
            "You're already in auto (HOOTLWO) — I run safe setup steps without asking and only "
            "pause for a genuinely destructive/irreversible step. Crack on."
        )
        return current
    label = getattr(mode, "value", str(mode))
    display_output(
        f"You're in {label}, which keeps you in the loop — so I don't run things unprompted. To "
        "complete the whole operation autonomously and report at the end, I'll switch to auto "
        "(HOOTLWO) for this session: one confirm now instead of asking each step. A genuinely "
        "destructive/irreversible step still pauses — that's a safety floor I can't bypass even on request."
    )
    if approve_prompt is None or not approve_prompt("Switch to auto (HOOTLWO) and run autonomously?"):
        display_output("Keeping the current mode — I'll keep explaining and suggesting as before.")
        return current
    updated = _dc_replace(current, mode=ControlMode.HOOTLWO)
    try:
        save_app_config(updated, paths)
    except Exception:
        pass
    display_output("Switched to auto (HOOTLWO). I'll run the operation end-to-end and report at the end.")
    return updated


_PASTED_CMD_BINARIES = frozenset((
    "npm", "pnpm", "yarn", "bun", "npx", "pip", "pip3", "python", "python3", "node", "cargo",
    "go", "git", "make", "cmake", "docker", "docker-compose", "brew", "apt", "apt-get", "sudo",
    "bash", "sh", "export", "mkdir", "ls", "cat", "curl", "wget", "gradle", "mvn", "rustup",
    "nvm", "conda", "mamba", "uv", "poetry", "ruby", "bundle", "php", "composer", "dotnet",
    "./gradlew", "pytest", "tsc", "vite", "rm", "cp", "mv", "chmod",
))
_PASTED_CMD_SUBCOMMANDS = frozenset((
    "install", "i", "ci", "run", "build", "test", "add", "exec", "compose", "clone", "pull",
    "push", "update", "start", "dev", "serve", "init", "create", "launch", "rebuild", "up",
    "down", "env", "venv", "check", "fmt", "lint", "publish", "pack",
))
_RUN_INTENT_RE = re.compile(
    r"^\s*(run|execute)\s+(?:this|the following|that)?\s*(?:command)?\s*[:`]*\s*(?P<cmd>.+?)`*\s*$",
    re.IGNORECASE | re.DOTALL,
)
_BACKTICK_CMD_RE = re.compile(r"^`{1,3}(?:bash|sh|shell)?\s*(?P<cmd>.+?)`{1,3}$", re.DOTALL)


def _first_token_is_command(text: str) -> bool:
    tok = (text or "").strip().split(maxsplit=1)
    if not tok:
        return False
    head = tok[0]
    if head in _PASTED_CMD_BINARIES or head.startswith("./") or head.startswith("/"):
        return True
    return False


def _looks_like_command(text: str) -> bool:
    """A bare line is a command when its first token is a known binary AND it has a flag,
    path, or known subcommand — so prose ('git is great') is never treated as a command."""
    s = (text or "").strip()
    if not s or "?" in s or "\n" in s or not _first_token_is_command(s):
        return False
    parts = s.split()
    if len(parts) < 2:
        return len(parts) == 1 and (parts[0].startswith("./") or parts[0].startswith("/"))
    second = parts[1]
    return (
        second.startswith("-")
        or second in _PASTED_CMD_SUBCOMMANDS
        or "/" in second
        or "." in second
        or any(p.startswith("-") for p in parts[1:])
    )


def _extract_pasted_command(message: str) -> str | None:
    """Plan 184 F10b: extract a shell command the user pasted/typed — backtick-wrapped, an
    explicit 'run <cmd>', or a bare known-binary line — or None when it's prose. Conservative:
    the command's first token must always be a known binary or path."""
    msg = (message or "").strip()
    if not msg:
        return None
    bt = _BACKTICK_CMD_RE.match(msg)
    if bt:
        cand = bt.group("cmd").strip()
        return cand if _first_token_is_command(cand) else None
    mi = _RUN_INTENT_RE.match(msg)
    if mi:
        cand = mi.group("cmd").strip().strip("`").strip()
        if cand and _first_token_is_command(cand):
            return cand
    return msg if _looks_like_command(msg) else None


def _maybe_run_pasted_command(
    message: str,
    *,
    current,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    approve_prompt: Callable[[str], bool] | None,
    chat,
) -> bool:
    """Plan 184 F10b: a pasted shell command → echo Duckln's understanding, reconfirm ONCE (the
    reconfirm IS the approval — no second prompt), then run it exactly as written in the terminal
    pane. User-authored, so it runs in any mode; the destructive (S4) safety floor still blocks.
    Returns True when consumed (it was a command), else False (fall through to normal chat)."""
    cmd = _extract_pasted_command(message)
    if not cmd:
        return False
    try:
        from duckln.safety import assess_command, SafetyClass

        target = _session_execution_target(paths.config_dir)
        assessment = assess_command(cmd, execution_target=target)
        if getattr(assessment, "blocked", False) or getattr(assessment, "safety_class", None) == SafetyClass.S4:
            display_output(
                f"I won't run `{cmd}` — {getattr(assessment, 'reason', None) or 'it trips the destructive/irreversible safety floor'}. "
                "That's the one thing I can't bypass, even on request."
            )
            return True
    except Exception:
        pass
    display_output(f"Got it — you pasted a command. I'll run it exactly as written:\n  $ {cmd}")
    if approve_prompt is None or not approve_prompt(f"Run `{cmd}` now?"):
        display_output("Okay — not running it.")
        return True
    try:
        if chat is not None and hasattr(chat, "run_command_in_terminal"):
            chat.run_command_in_terminal(cmd)
            display_output(f"Running `{cmd}` in the terminal pane.")
        else:
            from duckln.shell import ControlledCommandRunner

            res = ControlledCommandRunner(execution_target=_session_execution_target(paths.config_dir)).run(cmd)
            out = ((res.stdout or "") + (res.stderr or "")).strip()
            display_output(out[-2000:] if out else "(command produced no output)")
    except Exception as exc:  # noqa: BLE001
        display_output(f"Couldn't run it: {exc}")
    return True


_REPO_SETUP_VERBS = ("set up", "setup", "install", "clone", "deploy", "bring up", "build", "run ")
_REPO_QUESTION_CUES = ("what", "how ", "does ", "explain", "tell me about", "is this", "why ")


def _maybe_offer_repo_url_setup(
    message: str,
    *,
    current,
    paths: ConfigPaths,
    select_prompt: Callable[[str, tuple[str, ...]], str | None] | None,
    text_prompt: Callable[[str, str], str | None] | None,
    approve_prompt: Callable[[str], bool] | None,
    display_output: Callable[[str], None],
    system_probe,
    client,
    terminal_interface,
):
    """Plan 184 F10: a pasted GitHub repo LINK → recognize it, ask "set up this repo?", and on
    yes open the EXISTING target picker + bring-up (via `/repos` with the repo pre-selected). A URL
    inside a QUESTION is left to the conversation agent (which inspects the repo). Returns the
    (possibly updated) config when handled, else None (fall through to normal chat)."""
    from duckln.conversation_agent import _extract_github_repo_url

    url = _extract_github_repo_url(message or "")
    if not url:
        return None
    low = (message or "").lower()
    bare = low.replace(url.lower(), "").strip()
    is_question = ("?" in low) or any(cue in low for cue in _REPO_QUESTION_CUES)
    setup_intent = any(v in low for v in _REPO_SETUP_VERBS)
    if not (setup_intent or (len(bare) <= 12 and not is_question)):
        return None  # a URL mentioned inside a question → let the conversation agent answer it
    try:
        from state.repo_catalog import resolve_public_github_repo_record

        record = resolve_public_github_repo_record(url, client=client)
    except Exception as exc:  # noqa: BLE001 — surface a clean message, stay consumed
        display_output(f"That looks like a GitHub repo, but I couldn't resolve it: {exc}")
        return current
    if approve_prompt is None or not approve_prompt(f"Set up {record.name} ({record.repo_url})?"):
        display_output(f"Right, I'll leave {record.name} be — say 'set up {record.name}' or use /repos whenever.")
        return current
    display_output(f"On it — let's get {record.name} set up. Pick where to run it…")
    return handle_session_command(
        "/repos", current, paths, system_probe=system_probe, select=select_prompt,
        text_prompt=text_prompt, approve=approve_prompt, display=display_output,
        client=client, terminal_interface=terminal_interface, preselected_repo=record,
    )


def _open_path_non_blocking(path: str) -> bool:
    """Open a file in a GUI editor/opener WITHOUT blocking on a TTY.

    Tries `code -r` (VS Code), then macOS `open`, then `xdg-open`. Returns
    True when a launcher was started. Never waits — mirrors the existing
    non-blocking `webbrowser.open` pattern, so it is safe inside the Textual UI.
    """
    import shutil as _shutil
    import subprocess as _subproc

    candidates: tuple[tuple[str, list[str]], ...] = (
        ("code", ["code", "-r", path]),
        ("open", ["open", path]),
        ("xdg-open", ["xdg-open", path]),
    )
    for binary, argv in candidates:
        if _shutil.which(binary) is None:
            continue
        try:
            _subproc.Popen(
                argv,
                stdout=_subproc.DEVNULL,
                stderr=_subproc.DEVNULL,
                stdin=_subproc.DEVNULL,
            )
            return True
        except Exception:
            continue
    return False


def _handle_memory_clear_command(
    paths: ConfigPaths,
    *,
    select: Callable[[str, tuple[str, ...]], str | None] | None = None,
    display: Callable[[str], None] = print,
) -> bool:
    if select is None:
        from duckln.config import _default_select_prompt

        select_prompt = _default_select_prompt
    else:
        select_prompt = select

    scope_descriptions = {
        "Cancel and return": None,
        "Clear session history only": (
            "session",
            "Delete session history and saved session summaries only.",
        ),
        "Clear project-specific memory": (
            "project",
            "Delete tracked memory for the current project only.",
        ),
        "Clear everything and reset to factory": (
            "factory",
            "Delete Duckln state and reset managed memory to its default contract.",
        ),
    }
    selected_scope = select_prompt("Choose what to clear:", tuple(scope_descriptions))
    if selected_scope is None:
        display("Memory clear cancelled.")
        return False
    if selected_scope == "Cancel and return":
        return False

    scope_key, summary = scope_descriptions[selected_scope]
    display(summary)
    confirmed = select_prompt("Are you sure?", ("Yes", "No"))
    if confirmed != "Yes":
        display("No memory was cleared.")
        return False

    result = clear_memory_scope(
        paths.config_dir,
        scope=scope_key,
        contract_source=_resolve_agent_contract_source(),
        config_file=paths.config_file,
    )
    display(result.summary)
    return scope_key == "factory"


def open_command_palette(
    current: AppConfig,
    paths: ConfigPaths,
    *,
    select: Callable[[str, tuple[str, ...]], str | None] | None = None,
    secret_prompt: Callable[[str], str | None] | None = None,
    text_prompt: Callable[[str, str], str | None] | None = None,
    approve: Callable[[str], bool] | None = None,
    display: Callable[[str], None] = print,
    client: Any | None = None,
    terminal_interface: object | None = None,
) -> AppConfig:
    """Open the runtime slash command palette."""

    if select is None:
        from duckln.config import _default_select_prompt

        select_prompt = _default_select_prompt
    else:
        select_prompt = select

    choices = tuple(descriptor.choice_label for descriptor in get_slash_command_descriptors()) + ("Cancel",)
    selected = select_prompt("Duckln command palette:", choices)
    if selected is None or selected == "Cancel":
        display("No slash command selected.")
        return current

    for descriptor in get_slash_command_descriptors():
        if selected == descriptor.choice_label:
            display(f"> {descriptor.command}")
            return handle_session_command(
                descriptor.command,
                current,
                paths,
                select=select_prompt,
                secret_prompt=secret_prompt,
                text_prompt=text_prompt,
                approve=approve,
                display=display,
                client=client,
                terminal_interface=terminal_interface,
            )

    display(f"Retryable error: Unknown palette selection {selected}.")
    return current


def _display_help(*, display: Callable[[str], None]) -> None:
    descriptors = get_slash_command_descriptors()
    if not descriptors:
        return
    display(
        join_blocks(
            paragraph_block("Available slash commands:"),
            bullet_block(tuple(descriptor.choice_label for descriptor in descriptors)),
        )
    )


_CANCEL_HINT_SUFFIX = "(Press Q or Esc to cancel)"


def _with_cancel_hint(prompt: str) -> str:
    """Append a Q/Esc cancel hint to a picker prompt unless it's already there."""

    text = prompt or ""
    if _CANCEL_HINT_SUFFIX in text:
        return text
    return f"{text}\n{_CANCEL_HINT_SUFFIX}"


def _wrap_select_with_cancel_hint(
    inner: Callable[[str, tuple[str, ...]], str | None] | None,
) -> Callable[[str, tuple[str, ...]], str | None] | None:
    """Decorate a select_prompt callable so every cloud-flow prompt advertises Q/Esc cancel."""

    if inner is None:
        return None

    def wrapped(prompt: str, options: tuple[str, ...]) -> str | None:
        return inner(_with_cancel_hint(prompt), options)

    return wrapped


def _wrap_text_with_cancel_hint(
    inner: Callable[[str, str], str | None] | None,
) -> Callable[[str, str], str | None] | None:
    """Decorate a text_prompt callable so every cloud-flow input advertises Q/Esc cancel."""

    if inner is None:
        return None

    def wrapped(prompt: str, default: str) -> str | None:
        return inner(_with_cancel_hint(prompt), default)

    return wrapped


def _handle_explore_command(
    *,
    paths: ConfigPaths,
    current,
    select_prompt: Callable[[str, tuple[str, ...]], str | None] | None,
    text_prompt: Callable[[str, str], str | None] | None,
    approve_prompt: Callable[[str], bool] | None,
    display_output: Callable[[str], None],
    terminal_interface: object | None = None,
    system_probe=None,
) -> None:
    """Open the GitHub trending browser and hand off the picked repo to bring_up_selected_repo."""

    if select_prompt is None:
        display_output("/explore needs an interactive terminal — run Duckln in TTY mode and retry.")
        return
    chosen_url = run_explore_loop(
        config_dir=paths.config_dir,
        select_prompt=select_prompt,
        text_prompt=text_prompt,
        display_output=display_output,
        initial_period=TrendingPeriod.TODAY,
        initial_language=None,
    )
    if not chosen_url:
        display_output("Explore cancelled.")
        return
    display_output(f"Selected {chosen_url}. Resolving GitHub metadata…")
    try:
        record = resolve_public_github_repo_record(chosen_url)
    except Exception as exc:
        display_output(f"Could not resolve {chosen_url} from GitHub. {exc}")
        return
    display_output(f"Launching setup for {record.name}…")
    try:
        bring_up_selected_repo(
            record,
            current.mode,
            paths,
            approve=approve_prompt,
            display=display_output,
            runtime_provider=getattr(current.provider, "value", None),
            system_probe=system_probe,
            plan_mode_enabled=getattr(current, "plan_mode_enabled", False),
        )
    except Exception as exc:
        display_output(f"Setup encountered an error: {exc}")


def _reassert_cloud_terminal_target_from_state(
    *,
    paths: ConfigPaths,
    terminal_interface: object | None,
) -> None:
    """Re-read persisted execution target and force the terminal header to reflect it."""

    if terminal_interface is None:
        return
    updater = getattr(terminal_interface, "update_connection", None)
    if not callable(updater):
        return
    try:
        workflow = read_workflow_state(paths.config_dir)
    except Exception:
        return
    target = str(workflow.get("active_runtime_execution_target") or "local").strip().lower()
    if target not in {"local", "vm", "aws", "gcp", "docker"}:
        target = "local"
    # Plan 134 F1: a VM bring-up runs via a LOCAL terminal pane + `multipass exec`, so the
    # chip reads "local" — the same tested decision as `_terminal_connection_context`
    # (VM→"local"). Resolving it here too keeps startup/cloud-command refreshes consistent
    # with the per-command `_sync_chat_execution_context`, so the chip never shows the
    # stale generic "local-vm" the (now-removed) stdout inference used to produce.
    if target == "vm":
        target = "local"
    if target in {"aws", "gcp"} and not _has_live_managed_resource(paths.config_dir, provider=target):
        target = "local"
    cloud_vendor = workflow.get("active_runtime_cloud_vendor")
    cloud_region = workflow.get("active_runtime_cloud_region")
    cloud_shape = workflow.get("active_runtime_cloud_shape")
    vm_name = workflow.get("active_runtime_vm_name")
    try:
        updater(
            connection_type=target,
            cloud_vendor=str(cloud_vendor) if cloud_vendor else None,
            cloud_region=str(cloud_region) if cloud_region else None,
            cloud_shape=str(cloud_shape) if cloud_shape else None,
            vm_name=str(vm_name) if vm_name else None,
        )
    except Exception:
        pass


def _handle_cloud_command(
    *,
    paths: ConfigPaths,
    select_prompt: Callable[[str, tuple[str, ...]], str | None] | None,
    text_prompt: Callable[[str, str], str | None] | None,
    approve_prompt: Callable[[str], bool] | None,
    display_output: Callable[[str], None],
    terminal_interface: object | None = None,
) -> None:
    # Surface explicit consent for the internet skill before any cloud action runs —
    # cloud users get internet on by default but they should know it's flipped on.
    try:
        from duckln.internet_skill import (
            cloud_internet_consent_prompt,
            ensure_internet_for_cloud_target,
            is_internet_enabled,
        )

        snapshot = read_config_snapshot(paths.config_dir)
        cloud_target = str(snapshot.get("execution_target") or "").strip().lower()
        if cloud_target in {"aws", "gcp"} and not is_internet_enabled(paths.config_dir):
            display_output(cloud_internet_consent_prompt(cloud_target))
            ensure_internet_for_cloud_target(
                paths.config_dir,
                execution_target=cloud_target,
                display=display_output,
            )
    except Exception:
        pass
    _reassert_cloud_terminal_target_from_state(paths=paths, terminal_interface=terminal_interface)
    _surface_pending_cloud_remediation_on_login(paths=paths, display_output=display_output)
    if select_prompt is None:
        display_output(render_managed_resource_summary(paths.config_dir))
        return
    # Every nested cloud picker inherits the Q/Esc cancel hint via the wrapped prompts.
    select_prompt = _wrap_select_with_cancel_hint(select_prompt)
    text_prompt = _wrap_text_with_cancel_hint(text_prompt)
    choice = select_prompt(
        "Choose a cloud action:",
        (
            "Check AWS auth",
            "Check GCP auth",
            "Create AWS VM",
            "Create GCP VM",
            "Open managed cloud terminal",
            "Bootstrap Duckln runtime on cloud VM",
            "Run a bounded remote command on cloud VM",
            "Show managed resources",
            "Extend keepalive on a managed resource",
            "Cleanup a managed resource",
            "Discover Duckln-tagged AWS resources",
            "Discover Duckln-tagged GCP resources",
            "Cancel",
        ),
    )
    if choice in {None, "Cancel"}:
        display_output("Cloud action cancelled.")
        return
    runner = ControlledCommandRunner(trace=display_output, execution_target="local")
    if choice == "Check AWS auth":
        _handle_cloud_auth_action(
            provider="aws",
            paths=paths,
            runner=runner,
            approve_prompt=approve_prompt,
            select_prompt=select_prompt,
            text_prompt=text_prompt,
            display_output=display_output,
            terminal_interface=terminal_interface,
        )
        return
    if choice == "Check GCP auth":
        _handle_cloud_auth_action(
            provider="gcp",
            paths=paths,
            runner=runner,
            approve_prompt=approve_prompt,
            select_prompt=select_prompt,
            text_prompt=text_prompt,
            display_output=display_output,
            terminal_interface=terminal_interface,
        )
        return
    if choice == "Show managed resources":
        display_output(render_managed_resource_summary(paths.config_dir))
        return
    if choice == "Open managed cloud terminal":
        _handle_cloud_attach_action(
            paths=paths,
            select_prompt=select_prompt,
            display_output=display_output,
            terminal_interface=terminal_interface,
        )
        return
    if choice == "Bootstrap Duckln runtime on cloud VM":
        _handle_cloud_bootstrap_action(
            paths=paths,
            select_prompt=select_prompt,
            approve_prompt=approve_prompt,
            display_output=display_output,
            terminal_interface=terminal_interface,
        )
        return
    if choice == "Run a bounded remote command on cloud VM":
        _handle_cloud_remote_command_action(
            paths=paths,
            select_prompt=select_prompt,
            text_prompt=text_prompt,
            approve_prompt=approve_prompt,
            display_output=display_output,
            terminal_interface=terminal_interface,
        )
        return
    if choice == "Extend keepalive on a managed resource":
        _handle_resource_keepalive_command(
            paths=paths,
            select_prompt=select_prompt,
            text_prompt=text_prompt,
            display_output=display_output,
        )
        return
    if choice == "Cleanup a managed resource":
        _handle_managed_resource_cleanup_command(
            paths=paths,
            select_prompt=select_prompt,
            approve_prompt=approve_prompt,
            display_output=display_output,
        )
        return
    if choice == "Discover Duckln-tagged AWS resources":
        region = _prompt_text_or_default(text_prompt, "AWS region to inspect for Duckln-tagged resources:", "us-east-1")
        resources = discover_tagged_cloud_resources("aws", runner=runner, region_or_zone=region)
        display_output(_render_discovered_cloud_resources(resources, provider_label="AWS"))
        return
    if choice == "Discover Duckln-tagged GCP resources":
        resources = discover_tagged_cloud_resources("gcp", runner=runner)
        display_output(_render_discovered_cloud_resources(resources, provider_label="GCP"))
        return
    if choice == "Create AWS VM":
        _handle_cloud_create_command(
            provider="aws",
            paths=paths,
            select_prompt=select_prompt,
            text_prompt=text_prompt,
            approve_prompt=approve_prompt,
            display_output=display_output,
            terminal_interface=terminal_interface,
        )
        return
    if choice == "Create GCP VM":
        _handle_cloud_create_command(
            provider="gcp",
            paths=paths,
            select_prompt=select_prompt,
            text_prompt=text_prompt,
            approve_prompt=approve_prompt,
            display_output=display_output,
            terminal_interface=terminal_interface,
        )
        return


def _render_cloud_auth_status(status) -> str:
    ready_label = getattr(status, "readiness_label", None) or (
        f"{status.provider.upper()} ready" if getattr(status, "ready", False) else f"{status.provider.upper()} needs setup"
    )
    ready_marker = "●" if getattr(status, "ready", False) else "○"
    lines = [
        f"{ready_marker} {ready_label}",
        f"Provider: {status.provider.upper()}",
        f"CLI: {status.cli_name} {'ready' if status.cli_available else 'missing'}",
        f"Authenticated: {'yes' if status.authenticated else 'no'}",
    ]
    if status.account_label:
        lines.append(f"Account: {status.account_label}")
    if status.project_label:
        lines.append(f"Project: {status.project_label}")
    if status.default_region_or_zone:
        lines.append(f"Default region/zone: {status.default_region_or_zone}")
    lines.append(status.message)
    if getattr(status, "install_hint", None) or getattr(status, "auth_hint", None):
        lines.append("Next steps:")
        if getattr(status, "install_hint", None):
            lines.append(f"- {status.install_hint}")
        if getattr(status, "auth_hint", None):
            lines.append(f"- {status.auth_hint}")
    lines.append(f"Source: [1] {status.source_url}")
    return "\n".join(lines)


def _handle_cloud_auth_action(
    *,
    provider: str,
    paths: ConfigPaths,
    runner: ControlledCommandRunner,
    approve_prompt: Callable[[str], bool] | None,
    select_prompt: Callable[[str, tuple[str, ...]], str | None] | None,
    text_prompt: Callable[[str, str], str | None] | None,
    display_output: Callable[[str], None],
    terminal_interface: object | None = None,
) -> None:
    status = inspect_cloud_auth(provider, runner=runner)
    display_output(_render_cloud_auth_status(status))
    if getattr(status, "authenticated", False):
        _mark_cloud_provider_target(paths, provider=provider, status=status, terminal_interface=terminal_interface)
    if getattr(status, "ready", False):
        display_output(f"● {provider.upper()} ready. Choose what to do next from /cloud.")
        return
    if not status.cli_available:
        if _offer_cloud_cli_install(
            provider=provider,
            runner=runner,
            approve_prompt=approve_prompt,
            select_prompt=select_prompt,
            display_output=display_output,
        ):
            refreshed = inspect_cloud_auth(provider, runner=runner)
            display_output(_render_cloud_auth_status(refreshed))
            status = refreshed
        else:
            return
    if status.cli_available and not getattr(status, "ready", False):
        _continue_cloud_auth_setup(
            provider=provider,
            paths=paths,
            status=status,
            runner=runner,
            select_prompt=select_prompt,
            text_prompt=text_prompt,
            display_output=display_output,
            terminal_interface=terminal_interface,
        )


_CLOUD_REMEDIATION_LABELS: dict[CloudLoopChoice, str] = {
    CloudLoopChoice.FIX_NOW: "Fix now",
    CloudLoopChoice.FIX_LATER: "Fix later",
    CloudLoopChoice.IGNORE: "Ignore",
}


def _surface_cloud_remediation_choice(
    issue: CloudLoopIssue,
    remediation: CloudLoopRemediation | None,
    *,
    display_output: Callable[[str], None],
    select_prompt: Callable[[str, tuple[str, ...]], str | None] | None,
) -> CloudLoopChoice:
    """Open an inline picker asking the user to Fix now / Fix later / Ignore the classified issue."""

    prompt_text = issue.message
    if remediation is not None:
        prompt_text = f"{issue.message} Duckln can run: {remediation.label}."
    display_output(prompt_text)
    if issue.source_url:
        display_output(f"Source: {issue.source_url}")
    if select_prompt is None:
        return CloudLoopChoice.FIX_LATER
    labels = (
        _CLOUD_REMEDIATION_LABELS[CloudLoopChoice.FIX_NOW],
        _CLOUD_REMEDIATION_LABELS[CloudLoopChoice.FIX_LATER],
        _CLOUD_REMEDIATION_LABELS[CloudLoopChoice.IGNORE],
    )
    if remediation is None:
        labels = (
            _CLOUD_REMEDIATION_LABELS[CloudLoopChoice.FIX_LATER],
            _CLOUD_REMEDIATION_LABELS[CloudLoopChoice.IGNORE],
        )
    chosen = select_prompt("What should Duckln do?", labels)
    for key, label in _CLOUD_REMEDIATION_LABELS.items():
        if chosen == label:
            return key
    return CloudLoopChoice.FIX_LATER


def _persist_pending_cloud_remediation(
    issue: CloudLoopIssue,
    *,
    paths: ConfigPaths,
    provider: str,
    project: str | None,
) -> None:
    """Persist a Fix-later remediation so it can be surfaced on the next /cloud turn."""

    write_pending_cloud_remediation(
        paths.config_dir,
        provider=provider,
        kind=issue.kind,
        label=issue.message,
        project=project,
        source_url=issue.source_url,
    )


def _surface_pending_cloud_remediation_on_login(
    *,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
) -> None:
    """If a Fix-later remediation was persisted, remind the user when /cloud reopens."""

    payload = read_pending_cloud_remediation(paths.config_dir)
    if not payload:
        return
    label = payload.get("label") or payload.get("kind")
    provider = str(payload.get("provider") or "").upper()
    display_output(
        f"Reminder: Duckln paused {provider} setup earlier. {label} "
        "Run /cloud and pick 'Fix now' to retry, or /cloud and pick 'Ignore' to dismiss."
    )


def _wrap_runner_result_for_loop(result) -> CloudLoopActionResult:
    """Adapt a duckln CommandResult to the agent-loop ActionResult shape."""

    ok = result is not None and result.exit_code == 0 and not result.timed_out
    return CloudLoopActionResult(
        ok=ok,
        outcome="" if ok else _first_command_error_line(result) if result else "Command failed.",
        raw_stdout=getattr(result, "stdout", "") or "",
        raw_stderr=getattr(result, "stderr", "") or "",
    )


def _run_gcp_zone_configuration_loop(
    *,
    paths: ConfigPaths,
    runner: ControlledCommandRunner,
    cli: str,
    gcloud_cli_path: str,
    project: str,
    default_zone: str,
    select_prompt: Callable[[str, tuple[str, ...]], str | None] | None,
    text_prompt: Callable[[str, str], str | None] | None,
    display_output: Callable[[str], None],
    terminal_interface: object | None,
) -> bool:
    """Discover GCP zones and persist a chosen zone, looping through Fix now / later / ignore."""

    state: dict[str, object] = {"selected_zone": None}

    def _user_auth(command: str) -> None:
        _run_cloud_user_auth_command(command, display_output=display_output, terminal_interface=terminal_interface)

    def _discover_step_run() -> CloudLoopActionResult:
        zones, failure = discover_gcp_zones_detailed(runner=runner, project_id=project)
        if zones:
            choice = _select_or_prompt_value(
                prompt="Choose GCP zone:",
                values=zones,
                text_prompt=text_prompt,
                select_prompt=select_prompt,
                default=default_zone,
                manual_label="Enter zone manually",
            )
            if not choice:
                return CloudLoopActionResult(ok=False, outcome="GCP zone selection cancelled.")
            state["selected_zone"] = choice
            return CloudLoopActionResult(ok=True, outcome=f"Selected GCP zone {choice}.")
        if failure is None:
            return CloudLoopActionResult(
                ok=False,
                outcome="gcloud CLI is not available; cannot discover GCP zones.",
            )
        return _wrap_runner_result_for_loop(failure)

    def _discover_classify(result: CloudLoopActionResult) -> CloudLoopIssue | None:
        return classify_cloud_failure("gcp", result, project=project)

    def _discover_remediation(issue: CloudLoopIssue) -> CloudLoopRemediation | None:
        return resolve_cloud_remediation(
            "gcp",
            issue,
            runner=runner,
            cli_path=gcloud_cli_path,
            project=project,
            run_user_auth_command=_user_auth,
        )

    def _set_step_run() -> CloudLoopActionResult:
        zone = state.get("selected_zone")
        if not zone:
            return CloudLoopActionResult(ok=False, outcome="No GCP zone selected.")
        result = runner.run(f"{cli} config set compute/zone {shlex.quote(str(zone))}")
        if result.exit_code == 0 and not result.timed_out:
            return CloudLoopActionResult(ok=True, outcome=f"GCP zone set to {zone}.")
        return _wrap_runner_result_for_loop(result)

    def _surface(issue: CloudLoopIssue, remediation: CloudLoopRemediation | None) -> CloudLoopChoice:
        return _surface_cloud_remediation_choice(
            issue,
            remediation,
            display_output=display_output,
            select_prompt=select_prompt,
        )

    def _on_defer(issue: CloudLoopIssue) -> None:
        _persist_pending_cloud_remediation(issue, paths=paths, provider="gcp", project=project)

    steps = (
        CloudLoopStep(
            name="gcp_discover_zone",
            run=_discover_step_run,
            classify=_discover_classify,
            resolve_remediation=_discover_remediation,
        ),
        CloudLoopStep(
            name="gcp_set_zone",
            run=_set_step_run,
            classify=_discover_classify,
            resolve_remediation=_discover_remediation,
        ),
    )

    outcome = run_cloud_agent_loop(
        steps=steps,
        surface_choice=_surface,
        on_defer=_on_defer,
        display_output=display_output,
    )
    if outcome.completed:
        clear_pending_cloud_remediation(paths.config_dir)
    return outcome.completed


def _open_browser_url(url: str) -> bool:
    """Best-effort browser open; returns True when webbrowser claims to have opened it."""

    try:
        import webbrowser

        return bool(webbrowser.open(url, new=2))
    except Exception:
        return False


def _run_gcp_api_key_audit_step(
    *,
    paths: ConfigPaths,
    runner: ControlledCommandRunner,
    gcloud_cli_path: str,
    project: str,
    select_prompt: Callable[[str, tuple[str, ...]], str | None] | None,
    display_output: Callable[[str], None],
) -> bool:
    """Run the post-zone audit that flags unrestricted GCP API keys via the agent loop."""

    if not project:
        return True

    audit_state = {"already_surfaced": False, "last_findings": ()}

    def _step_run() -> CloudLoopActionResult:
        if audit_state["already_surfaced"]:
            return CloudLoopActionResult(
                ok=True,
                outcome="API-key audit notice already delivered; restrict the flagged keys in the console.",
            )
        findings, failure = audit_gcp_api_keys(
            runner=runner, gcloud_cli=gcloud_cli_path, project=project,
        )
        audit_state["last_findings"] = findings
        if failure is not None and not findings:
            return _wrap_runner_result_for_loop(failure)
        issue = classify_gcp_api_key_findings(findings, project=project)
        if issue is None:
            return CloudLoopActionResult(ok=True, outcome="No unrestricted GCP API keys found.")
        return CloudLoopActionResult(
            ok=False,
            outcome=issue.message,
            raw_stdout=str(len(findings)),
        )

    def _classify(_result: CloudLoopActionResult) -> CloudLoopIssue | None:
        return classify_gcp_api_key_findings(audit_state["last_findings"], project=project)

    def _resolve(issue: CloudLoopIssue) -> CloudLoopRemediation | None:
        base = resolve_gcp_api_key_remediation(
            issue,
            project=project,
            open_browser=_open_browser_url,
            display_output=display_output,
        )
        if base is None:
            return None

        def _run_then_mark() -> CloudLoopActionResult:
            inner = base.run()
            audit_state["already_surfaced"] = True
            return inner

        return CloudLoopRemediation(
            kind=base.kind,
            label=base.label,
            run=_run_then_mark,
            source_url=base.source_url,
        )

    def _surface(issue: CloudLoopIssue, remediation: CloudLoopRemediation | None) -> CloudLoopChoice:
        return _surface_cloud_remediation_choice(
            issue, remediation, display_output=display_output, select_prompt=select_prompt,
        )

    def _on_defer(issue: CloudLoopIssue) -> None:
        _persist_pending_cloud_remediation(issue, paths=paths, provider="gcp", project=project)

    step = CloudLoopStep(
        name="gcp_audit_api_keys",
        run=_step_run,
        classify=_classify,
        resolve_remediation=_resolve,
    )
    outcome = run_cloud_agent_loop(
        steps=(step,),
        surface_choice=_surface,
        on_defer=_on_defer,
        display_output=display_output,
        max_attempts_per_step=2,
    )
    if outcome.completed:
        clear_pending_cloud_remediation(paths.config_dir)
    return outcome.completed


def _run_aws_region_configuration_loop(
    *,
    paths: ConfigPaths,
    runner: ControlledCommandRunner,
    cli: str,
    aws_cli_path: str,
    default_region: str,
    select_prompt: Callable[[str, tuple[str, ...]], str | None] | None,
    text_prompt: Callable[[str, str], str | None] | None,
    display_output: Callable[[str], None],
    terminal_interface: object | None,
) -> bool:
    """Discover AWS regions and persist a chosen region, looping through Fix now / later / ignore."""

    state: dict[str, object] = {"selected_region": None}

    def _user_auth(command: str) -> None:
        _run_cloud_user_auth_command(command, display_output=display_output, terminal_interface=terminal_interface)

    def _discover_step_run() -> CloudLoopActionResult:
        regions, failure = discover_aws_regions_detailed(runner=runner)
        if regions:
            choice = _select_or_prompt_value(
                prompt="Choose AWS default region:",
                values=regions,
                text_prompt=text_prompt,
                select_prompt=select_prompt,
                default=default_region,
                manual_label="Enter region manually",
            )
            if not choice:
                return CloudLoopActionResult(ok=False, outcome="AWS region selection cancelled.")
            state["selected_region"] = choice
            return CloudLoopActionResult(ok=True, outcome=f"Selected AWS region {choice}.")
        if failure is None:
            return CloudLoopActionResult(
                ok=False,
                outcome="aws CLI is not available; cannot discover AWS regions.",
            )
        return _wrap_runner_result_for_loop(failure)

    def _classify(result: CloudLoopActionResult) -> CloudLoopIssue | None:
        return classify_cloud_failure("aws", result)

    def _remediation(issue: CloudLoopIssue) -> CloudLoopRemediation | None:
        return resolve_cloud_remediation(
            "aws",
            issue,
            runner=runner,
            cli_path=aws_cli_path,
            run_user_auth_command=_user_auth,
        )

    def _set_step_run() -> CloudLoopActionResult:
        region = state.get("selected_region")
        if not region:
            return CloudLoopActionResult(ok=False, outcome="No AWS region selected.")
        result = runner.run(f"{cli} configure set region {shlex.quote(str(region))}")
        if result.exit_code == 0 and not result.timed_out:
            return CloudLoopActionResult(ok=True, outcome=f"AWS default region set to {region}.")
        return _wrap_runner_result_for_loop(result)

    def _surface(issue: CloudLoopIssue, remediation: CloudLoopRemediation | None) -> CloudLoopChoice:
        return _surface_cloud_remediation_choice(
            issue,
            remediation,
            display_output=display_output,
            select_prompt=select_prompt,
        )

    def _on_defer(issue: CloudLoopIssue) -> None:
        _persist_pending_cloud_remediation(issue, paths=paths, provider="aws", project=None)

    steps = (
        CloudLoopStep(
            name="aws_discover_region",
            run=_discover_step_run,
            classify=_classify,
            resolve_remediation=_remediation,
        ),
        CloudLoopStep(
            name="aws_set_region",
            run=_set_step_run,
            classify=_classify,
            resolve_remediation=_remediation,
        ),
    )

    outcome = run_cloud_agent_loop(
        steps=steps,
        surface_choice=_surface,
        on_defer=_on_defer,
        display_output=display_output,
    )
    if outcome.completed:
        clear_pending_cloud_remediation(paths.config_dir)
    return outcome.completed


def _confirm_or_switch_gcp_project(
    *,
    runner: ControlledCommandRunner,
    current_project: str,
    select_prompt: Callable[[str, tuple[str, ...]], str | None] | None,
    text_prompt: Callable[[str, str], str | None] | None,
    display_output: Callable[[str], None],
) -> str:
    """Ask the user whether to keep the current GCP project; on No, run the picker."""

    current = (current_project or "").strip()
    if not current:
        chosen = _select_or_create_gcp_project(
            runner=runner,
            text_prompt=text_prompt,
            select_prompt=select_prompt,
            display_output=display_output,
        )
        return chosen
    if select_prompt is None:
        return current
    answer = select_prompt(
        f"Current GCP project: {current}. Continue with this project?",
        ("Yes, continue", "No, pick another", "Cancel"),
    )
    if answer in {None, "Cancel"}:
        return ""
    if answer == "Yes, continue":
        return current
    chosen = _select_or_create_gcp_project(
        runner=runner,
        text_prompt=text_prompt,
        select_prompt=select_prompt,
        display_output=display_output,
    )
    return chosen


def _continue_cloud_auth_setup(
    *,
    provider: str,
    paths: ConfigPaths,
    status,
    runner: ControlledCommandRunner,
    select_prompt: Callable[[str, tuple[str, ...]], str | None] | None,
    text_prompt: Callable[[str, str], str | None] | None,
    display_output: Callable[[str], None],
    terminal_interface: object | None = None,
) -> bool:
    """Drive official CLI auth/config without collecting secrets in Duckln."""

    provider_label = provider.upper()
    cli = shlex.quote(getattr(status, "cli_path", None) or status.cli_name)
    if not status.cli_available:
        return False
    if provider == "gcp":
        if not status.authenticated:
            if select_prompt is not None:
                choice = select_prompt(
                    "GCP needs login before Duckln can create VMs.",
                    ("Start gcloud auth login", "Cancel"),
                )
                if choice != "Start gcloud auth login":
                    display_output("GCP login cancelled. Duckln stopped the cloud flow.")
                    return False
            command = f"{cli} auth login"
            _run_cloud_user_auth_command(command, display_output=display_output, terminal_interface=terminal_interface)
            return False
        current_project = (getattr(status, "project_label", None) or "").strip()
        confirmed_project = _confirm_or_switch_gcp_project(
            runner=runner,
            current_project=current_project,
            select_prompt=select_prompt,
            text_prompt=text_prompt,
            display_output=display_output,
        )
        if not confirmed_project:
            display_output("GCP project selection cancelled. Duckln stopped the cloud flow.")
            return False
        if confirmed_project != current_project:
            result = runner.run(f"{cli} config set project {shlex.quote(confirmed_project)}")
            if result.exit_code != 0 or result.timed_out:
                display_output(f"Duckln could not set the GCP project. {_first_command_error_line(result)}")
                return False
            status = inspect_cloud_auth(provider, runner=runner)
            display_output(_render_cloud_auth_status(status))
        if not getattr(status, "region_or_zone_configured", False):
            project_id = getattr(status, "project_label", None) or ""
            gcloud_cli_path = getattr(status, "cli_path", None) or status.cli_name
            zone_ok = _run_gcp_zone_configuration_loop(
                paths=paths,
                runner=runner,
                cli=cli,
                gcloud_cli_path=gcloud_cli_path,
                project=project_id,
                default_zone=status.default_region_or_zone or "us-central1-a",
                select_prompt=select_prompt,
                text_prompt=text_prompt,
                display_output=display_output,
                terminal_interface=terminal_interface,
            )
            if not zone_ok:
                return False
        audit_project = (getattr(status, "project_label", None) or confirmed_project or "").strip()
        if audit_project:
            audit_gcloud_cli_path = getattr(status, "cli_path", None) or status.cli_name
            _run_gcp_api_key_audit_step(
                paths=paths,
                runner=runner,
                gcloud_cli_path=audit_gcloud_cli_path,
                project=audit_project,
                select_prompt=select_prompt,
                display_output=display_output,
            )
    elif provider == "aws":
        if not status.authenticated:
            if select_prompt is not None:
                choice = select_prompt(
                    "AWS needs authentication before Duckln can create VMs.",
                    ("Run aws configure", "Run aws sso login", "Cancel"),
                )
                if choice == "Cancel" or choice is None:
                    display_output("AWS login cancelled. Duckln stopped the cloud flow.")
                    return False
                command = f"{cli} configure" if choice == "Run aws configure" else f"{cli} sso login"
            else:
                command = f"{cli} configure"
            _run_cloud_user_auth_command(command, display_output=display_output, terminal_interface=terminal_interface)
            return False
        if not getattr(status, "region_or_zone_configured", False):
            aws_cli_path = getattr(status, "cli_path", None) or status.cli_name
            region_ok = _run_aws_region_configuration_loop(
                paths=paths,
                runner=runner,
                cli=cli,
                aws_cli_path=aws_cli_path,
                default_region=status.default_region_or_zone or "us-east-1",
                select_prompt=select_prompt,
                text_prompt=text_prompt,
                display_output=display_output,
                terminal_interface=terminal_interface,
            )
            if not region_ok:
                return False
    refreshed = inspect_cloud_auth(provider, runner=runner)
    display_output(_render_cloud_auth_status(refreshed))
    if getattr(refreshed, "authenticated", False):
        _mark_cloud_provider_target(paths, provider=provider, status=refreshed, terminal_interface=terminal_interface)
    if getattr(refreshed, "ready", False):
        display_output(f"● {provider_label} ready. Choose what to do next from /cloud.")
        return True
    display_output(f"{provider_label} is not ready yet. Complete the shown provider step, then re-run /cloud.")
    return False


def _run_cloud_user_auth_command(
    command: str,
    *,
    display_output: Callable[[str], None],
    terminal_interface: object | None,
) -> None:
    display_output(f"Duckln will open the official cloud CLI auth flow. Duckln will not collect or store secrets.\nCommand: {command}")
    runner = getattr(terminal_interface, "run_terminal_command", None)
    if callable(runner):
        runner(command=command)
        display_output("Complete the provider login in the terminal/browser, then run /cloud again.")
        return
    display_output(f"Run this in your terminal, then run /cloud again:\n{command}")


def _has_live_managed_resource(config_dir, *, provider: str) -> bool:
    """True only when Duckln tracks a non-terminated resource for the provider."""

    try:
        store = initialize_state_store(config_dir)
        resources = store.list_managed_resources(provider=provider)
    except Exception:
        return False
    for record in resources:
        status = str(getattr(record, "status", "") or "").strip().lower()
        if status and status not in {"terminated", "stopped", "deleted", "removed"}:
            return True
    return False


def _mark_cloud_provider_target(
    paths: ConfigPaths,
    *,
    provider: str,
    status,
    terminal_interface: object | None,
) -> None:
    """Persist provider auth metadata, but only flip the terminal header when actually connected.

    Authentication alone means the user logged in to gcloud/aws — the header must keep saying
    Local until Duckln is actually targeting a live cloud resource (created or attached VM).
    """

    region_or_zone = getattr(status, "default_region_or_zone", None) or None
    connected = _has_live_managed_resource(paths.config_dir, provider=provider)
    if connected:
        write_workflow_state(
            paths.config_dir,
            {
                "active_runtime_execution_target": provider,
                "active_runtime_cloud_vendor": provider.upper(),
                "active_runtime_cloud_region": region_or_zone,
                "active_runtime_cloud_shape": None,
            },
        )
    else:
        write_workflow_state(
            paths.config_dir,
            {
                "active_runtime_cloud_vendor": provider.upper(),
                "active_runtime_cloud_region": region_or_zone,
            },
        )
    updater = getattr(terminal_interface, "update_connection", None)
    if callable(updater):
        if connected:
            updater(
                connection_type=provider,
                cloud_vendor=provider.upper(),
                cloud_region=region_or_zone,
                cloud_shape=None,
            )
        else:
            updater(connection_type="local")


def _pick_cloud_shape_two_tier(
    *,
    provider: str,
    region_or_zone: str,
    runner: ControlledCommandRunner,
    select_prompt: Callable[[str, tuple[str, ...]], str | None] | None,
    display_output: Callable[[str], None],
) -> DiscoveredCloudShape | None:
    """Discover provider shapes and present a category → shape two-tier picker."""

    if select_prompt is None:
        display_output("Interactive selection is required for cloud shape pick.")
        return None
    from duckln.cloud_runtime import _resolve_cloud_cli_path

    cli_name = "aws" if provider == "aws" else "gcloud"
    cli_path = _resolve_cloud_cli_path(cli_name)
    if cli_path is None:
        display_output(f"{cli_name} CLI is not installed; cannot list cloud shapes.")
        return None
    shapes, failure = discover_all_cloud_shapes(
        provider, runner=runner, cli_path=cli_path, region_or_zone=region_or_zone,
    )
    if not shapes:
        if failure is not None:
            display_output(
                f"Duckln could not list {provider.upper()} shapes in {region_or_zone}. "
                f"{_first_command_error_line(failure)}"
            )
        else:
            display_output(
                f"No shapes returned for {provider.upper()} in {region_or_zone}. "
                "Confirm the region/zone exists and your account has compute.machineTypes.list permission."
            )
        return None
    categories = group_shapes_into_categories(shapes)
    category_labels = tuple(category.label for category in categories)
    category_choice = select_prompt(
        f"Choose VM type category for {provider.upper()} in {region_or_zone}:",
        category_labels + ("Show all shapes", "Cancel"),
    )
    if category_choice in {None, "Cancel"}:
        return None
    if category_choice == "Show all shapes":
        present_shapes: tuple[DiscoveredCloudShape, ...] = tuple(
            sorted(shapes, key=lambda s: (s.has_gpu, s.gpu_kind or "", s.cpu_count, s.memory_gb, s.name))
        )
    else:
        match = next((c for c in categories if c.label == category_choice), None)
        if match is None:
            return None
        present_shapes = match.shapes
    shape_labels = tuple(render_shape_label(s) for s in present_shapes)
    label_to_shape = {label: shape for label, shape in zip(shape_labels, present_shapes)}
    chosen = select_prompt(
        f"Choose the {provider.upper()} VM shape in {region_or_zone}:",
        shape_labels + ("Back", "Cancel"),
    )
    if chosen in {None, "Cancel"}:
        return None
    if chosen == "Back":
        return _pick_cloud_shape_two_tier(
            provider=provider,
            region_or_zone=region_or_zone,
            runner=runner,
            select_prompt=select_prompt,
            display_output=display_output,
        )
    return label_to_shape.get(chosen)


def _select_or_create_gcp_project(
    *,
    runner: ControlledCommandRunner,
    text_prompt: Callable[[str, str], str | None] | None,
    select_prompt: Callable[[str, tuple[str, ...]], str | None] | None,
    display_output: Callable[[str], None],
) -> str:
    projects = discover_gcp_projects(runner=runner)
    create_label = "Create new GCP project"
    manual_label = "Enter project id manually"
    if select_prompt is not None:
        choices = tuple(dict.fromkeys(projects)) + (create_label, manual_label, "Cancel")
        selected = select_prompt("Choose GCP project:", choices)
        if selected in {None, "Cancel"}:
            return ""
        if selected not in {create_label, manual_label}:
            return selected
        if selected == manual_label:
            return _prompt_text_or_default(text_prompt, "GCP project id:", "")
    elif projects:
        return projects[0]

    if text_prompt is None:
        display_output("Creating a GCP project needs a project id.")
        return ""
    project_id = _prompt_text_or_default(
        text_prompt,
        "New GCP project id:",
        "",
    )
    if not project_id:
        return ""
    project_name = _prompt_text_or_default(
        text_prompt,
        "GCP project display name:",
        project_id,
    )
    display_output(
        "Duckln will create the GCP project with the official gcloud CLI.\n"
        f"Source: https://cloud.google.com/sdk/gcloud/reference/projects/create\n"
        f"Command Duckln will run: gcloud projects create {project_id}"
    )
    result = create_gcp_project(project_id, name=project_name, runner=runner)
    display_output(result.message)
    if result.ok:
        display_output(f"Source: [1] {result.source_url}")
        return result.project_id
    return ""


def _select_or_prompt_value(
    *,
    prompt: str,
    values: tuple[str, ...],
    text_prompt: Callable[[str, str], str | None] | None,
    select_prompt: Callable[[str, tuple[str, ...]], str | None] | None,
    default: str,
    manual_label: str,
) -> str:
    unique_values = tuple(dict.fromkeys(value for value in values if value))
    if select_prompt is not None and unique_values:
        choices = unique_values + (manual_label, "Cancel")
        selected = select_prompt(prompt, choices)
        if selected in {None, "Cancel"}:
            return ""
        if selected != manual_label:
            return selected
    if text_prompt is None:
        return default
    return (text_prompt(prompt, default) or "").strip()


def _offer_cloud_cli_install(
    *,
    provider: str,
    runner: ControlledCommandRunner,
    approve_prompt: Callable[[str], bool] | None,
    select_prompt: Callable[[str, tuple[str, ...]], str | None] | None,
    display_output: Callable[[str], None],
) -> bool:
    plan = build_cloud_cli_install_plan(provider)
    elevation = " This may ask for your system password." if plan.requires_elevation else ""
    prompt = (
        f"Duckln can install {plan.cli_name} for {plan.provider.upper()} from the official provider package.{elevation}\n"
        f"Source: {plan.source_url}\n"
        f"Command Duckln will run:\n{plan.command}\n\n"
        "Install now?"
    )
    if approve_prompt is not None:
        approved = approve_prompt(prompt)
    elif select_prompt is not None:
        approved = select_prompt(prompt, ("Install now", "Cancel")) == "Install now"
    else:
        approved = False
    if not approved:
        display_output(f"Duckln left {plan.cli_name} uninstalled.")
        return False
    display_output(f"Installing {plan.cli_name} from official {plan.provider.upper()} resources...")
    result = runner.run(plan.command, timeout_seconds=900)
    if result.exit_code == 0 and not result.timed_out:
        display_output(f"{plan.cli_name} install completed. {plan.post_install_auth_hint}")
        return True
    error_line = _first_command_error_line(result)
    display_output(
        f"Duckln could not install {plan.cli_name} automatically. {error_line}\n"
        f"Source: {plan.source_url}"
    )
    return False


def _local_service_installed(service: str) -> bool:
    """Plan 197: is the local service's CLI present on PATH?"""
    svc = (service or "").strip().lower()
    if svc == "multipass":
        return is_multipass_installed()
    return shutil.which(svc) is not None


def _offer_local_service_install(
    *,
    service: str,
    runner: ControlledCommandRunner,
    approve_prompt: Callable[[str], bool] | None,
    select_prompt: Callable[[str, tuple[str, ...]], str | None] | None,
    display_output: Callable[[str], None],
    system_probe: Any | None = None,
) -> bool:
    """Plan 197 F1: Duckln INSTALLS a missing local service (Multipass/Docker) itself on ONE Yes,
    then VERIFIES it worked. Returns True only when the service is confirmed installed afterward.
    On decline / no-clean-install / install failure → a DETAILED F7 step-by-step walkthrough
    (never a one-line hint). System mutation → single consent gate, official source only."""
    os_type = getattr(system_probe, "operating_system", None) or platform.system()
    plan = build_local_service_install_plan(service, os_type)
    name = plan.display_name
    # No clean auto-install for this OS (e.g. Windows) → detailed installer walkthrough.
    if not plan.command:
        display_output(render_remediation_steps(service, os_type, reason="unsupported"))
        return False
    elev = " This may ask for your system password." if plan.requires_elevation else ""
    prompt = (
        f"{name} isn't installed, and I need it for this. I can install it for you now from the "
        f"official source:\n  {plan.command}{elev}\nGo ahead?"
    )
    if approve_prompt is not None:
        approved = approve_prompt(prompt)
    elif select_prompt is not None:
        approved = select_prompt(prompt, ("Yes — install it", "No — I'll do it myself")) == "Yes — install it"
    else:
        approved = False
    if not approved:
        display_output(render_remediation_steps(service, os_type, reason="declined"))
        return False
    display_output(f"Installing {name} from the official source — this can take a minute…")
    try:
        result = runner.run(plan.command, timeout_seconds=900)
        ran_ok = result.exit_code == 0 and not result.timed_out
    except Exception:
        ran_ok = False
    if ran_ok and _local_service_installed(service):
        display_output(f"✓ {name} is installed. Continuing…")
        return True
    # Install ran but didn't take (or failed) → detailed manual steps, not a bare hint.
    display_output(f"I couldn't finish installing {name} automatically. Here's how to do it:")
    display_output(render_remediation_steps(service, os_type, reason="install_failed"))
    return False


def _ensure_service_available(
    *,
    service: str,
    runner: ControlledCommandRunner,
    approve_prompt: Callable[[str], bool] | None,
    select_prompt: Callable[[str, tuple[str, ...]], str | None] | None,
    display_output: Callable[[str], None],
    system_probe: Any | None = None,
) -> bool:
    """Plan 197 F6: the general detect→fix→verify→resume seam. Returns True when the service is
    available (already, or after Duckln installs it on consent). When it's missing, Duckln takes
    the initiative to install it (F1) rather than dead-ending; a caller that gets True continues
    the task. A False return already surfaced detailed remediation steps (F7)."""
    if _local_service_installed(service):
        return True
    return _offer_local_service_install(
        service=service, runner=runner, approve_prompt=approve_prompt,
        select_prompt=select_prompt, display_output=display_output, system_probe=system_probe,
    )


def _first_command_error_line(result: CommandResult) -> str:
    text = f"{result.stderr or ''}\n{result.stdout or ''}"
    for line in text.splitlines():
        clean = line.strip()
        if clean:
            return clean
    if result.timed_out:
        return "The install command timed out."
    if result.exit_code is not None:
        return f"Exit code {result.exit_code}."
    return "No error output was captured."


def _parse_expiry_days(answer: str | None) -> int | None:
    """Extract a positive day count from a free-text expiry answer; None = use default."""

    if not answer:
        return None
    match = re.search(r"\d+", answer)
    if match is None:
        return None
    value = int(match.group(0))
    return value if value > 0 else None


def _handle_loop_command(
    *,
    command: str,
    paths: ConfigPaths,
    text_prompt: Callable[[str, str], str | None] | None,
    select_prompt: Callable[[str, tuple[str, ...]], str | None] | None,
    approve_prompt: Callable[[str], bool] | None,
    display_output: Callable[[str], None],
) -> None:
    from state.store import initialize_state_store

    store = initialize_state_store(paths.config_dir)
    parts = command.split()
    if command == "/loops":
        display_output(render_loops(paths.config_dir))
        return
    if command == "/loop":
        if text_prompt is None:
            display_output("Run `/loop` in interactive mode so Duckln can ask what, how often, and what to do on failure.")
            return
        task = text_prompt(
            "What should Duckln monitor or run automatically?\nExample: restart whisper if it crashes, check AWS cost hourly, or watch this repo for new releases.",
            "",
        ) or ""
        if not task.strip():
            display_output("Loop creation cancelled.")
            return
        cadence = text_prompt("How often should Duckln check?", "every five minutes") or "every five minutes"
        failure_policy = text_prompt("If something goes wrong, should Duckln fix first or only notify?", "fix first then notify if needed") or "fix first then notify if needed"
        expiry_answer = text_prompt(
            "How long should this loop run before I check in with you for review? "
            "(press Enter to accept the per-type default)",
            "",
        )
        expiry_days = _parse_expiry_days(expiry_answer)
        spec = infer_loop_spec(task, cadence, failure_policy, expiry_days=expiry_days)
        confirmation = render_loop_confirmation(spec)
        approved = True
        if select_prompt is not None:
            selected = select_prompt(confirmation, ("Create loop", "Cancel"))
            approved = selected == "Create loop"
        elif approve_prompt is not None:
            approved = approve_prompt(confirmation)
        if not approved:
            display_output("Loop creation cancelled.")
            return
        loop = create_loop_from_answers(
            paths.config_dir,
            task=task,
            cadence=cadence,
            failure_policy=failure_policy,
            expiry_days=expiry_days,
        )
        start_loop_scheduler(paths.config_dir)
        display_output(f"Loop created: {loop.name} — ID: {loop.id}")
        return
    if len(parts) < 3:
        display_output("Usage: /loop pause|resume|delete|history|edit|run <id>")
        return
    action = parts[1].lower()
    loop_id = parts[2]
    loop = store.get_loop(loop_id)
    if loop is None:
        display_output(f"No loop found with id {loop_id}.")
        return
    if action == "pause":
        store.set_loop_active(loop_id, False)
        unschedule_loop(loop_id)
        shutdown_loop_scheduler_if_idle(paths.config_dir)
        display_output(f"Paused {loop_id}: {loop.name}.")
        return
    if action == "resume":
        store.set_loop_active(loop_id, True)
        resumed = store.get_loop(loop_id)
        if resumed is not None:
            schedule_loop(paths.config_dir, resumed)
            start_loop_scheduler(paths.config_dir)
        display_output(f"Resumed {loop_id}: {loop.name}.")
        return
    if action == "delete":
        prompt = f"Delete {loop_id}: {loop.name}? This removes the loop schedule and its saved run history."
        if approve_prompt is not None:
            confirmed = approve_prompt(prompt)
        elif select_prompt is not None:
            confirmed = select_prompt(prompt, ("Delete loop", "Cancel")) == "Delete loop"
        else:
            confirmed = False
        if not confirmed:
            display_output(f"Delete cancelled for {loop_id}.")
            return
        unschedule_loop(loop_id)
        store.delete_loop(loop_id)
        shutdown_loop_scheduler_if_idle(paths.config_dir)
        display_output(f"Deleted {loop_id}: {loop.name}.")
        return
    if action == "history":
        display_output(render_loop_history(paths.config_dir, loop_id))
        return
    if action == "run":
        result = run_loop_now(paths.config_dir, loop_id)
        display_output(f"Loop {loop_id} ran now: {result.status} — {result.summary}")
        return
    if action == "edit":
        if text_prompt is None:
            display_output("Loop edit needs interactive text input.")
            return
        cadence = text_prompt("New schedule in plain English:", "every five minutes") or "every five minutes"
        behavior = text_prompt("New failure behaviour:", "fix first then notify if needed") or "fix first then notify if needed"
        expiry_answer = text_prompt(
            "How many days before the next review? (press Enter to keep the current window)",
            "",
        )
        expiry_days = _parse_expiry_days(expiry_answer)
        renewed_days = expiry_days if expiry_days is not None else loop.expiry_days
        spec = infer_loop_spec(loop.task_description, cadence, behavior, expiry_days=renewed_days)
        store.upsert_loop(
            loop_id=loop.id,
            name=loop.name,
            loop_type=loop.type,
            schedule=spec.schedule,
            task_description=loop.task_description,
            tool_scope=loop.tool_scope,
            os_type=loop.os_type,
            auto_fix=spec.auto_fix,
            max_fix_attempts=loop.max_fix_attempts,
            notify_on=loop.notify_on,
            safety_class=loop.safety_class,
            active=True,
            expires_at=next_expires_at(spec.expiry_days),
            expiry_days=spec.expiry_days,
        )
        edited = store.get_loop(loop.id)
        if edited is not None:
            unschedule_loop(loop.id)
            schedule_loop(paths.config_dir, edited)
            start_loop_scheduler(paths.config_dir)
        display_output(f"Updated {loop.id}: {loop.name} — renewed for {spec.expiry_days} days.")
        return
    display_output("Usage: /loop pause|resume|delete|history|edit|run <id>")


def _handle_cloud_create_command(
    *,
    provider: str,
    paths: ConfigPaths,
    select_prompt: Callable[[str, tuple[str, ...]], str | None],
    text_prompt: Callable[[str, str], str | None] | None,
    approve_prompt: Callable[[str], bool] | None,
    display_output: Callable[[str], None],
    terminal_interface: object | None = None,
) -> None:
    label = "AWS" if provider == "aws" else "GCP"
    # Plan 196 F3: LIST existing managed cloud instances first (read-only — never gated) so
    # the user can ATTACH an existing one instead of only ever creating a new one. Only the
    # CREATE mutation is gated by Plan Mode below (mirrors the /vm F2 restructure).
    try:
        _existing_cloud = tuple(
            r for r in initialize_state_store(paths.config_dir).list_managed_resources(provider=provider)
            if getattr(r, "status", "") not in {"terminated"}
        )
    except Exception:
        _existing_cloud = ()
    if _existing_cloud and select_prompt is not None:
        display_output(
            f"You have {len(_existing_cloud)} existing {label} instance(s): "
            + ", ".join(getattr(r, "display_name", "?") for r in _existing_cloud) + "."
        )
        _cloud_pick = select_prompt(
            f"Use an existing {label} instance, or create a new one?",
            ("Use an existing instance", "Create a new instance", "Cancel"),
        )
        if _cloud_pick is None or _cloud_pick == "Cancel":
            display_output("No cloud change made.")
            return
        if _cloud_pick == "Use an existing instance":
            _handle_cloud_attach_action(
                paths=paths,
                select_prompt=select_prompt,
                display_output=display_output,
                terminal_interface=terminal_interface,
            )
            return
        # else "Create a new instance" → fall through to the gate/create below.
    # Plan 73 Phase A: gate cloud provisioning behind an approved plan when
    # Plan Mode is ON — no direct VM/cloud mutation without approval.
    _cfg = load_app_config(paths)
    if _cfg is not None and getattr(_cfg, "plan_mode_enabled", False):
        from duckln.repo_bringup import gate_mutation_or_draft

        gate_mutation_or_draft(
            intended_steps=(
                (f"Verify {label} CLI is installed", f"{'aws' if provider == 'aws' else 'gcloud'} --version", "version prints"),
                (f"Confirm {label} authentication", f"{'aws sts get-caller-identity' if provider == 'aws' else 'gcloud auth list'}", "authenticated identity shown"),
                (f"Create the {label} VM instance", f"# create {label} instance (region/shape chosen on approval)", "instance reaches running state"),
            ),
            objective=f"Provision a {label} cloud VM",
            repo_slug=None,
            paths=paths,
            current=_cfg,
            display=display_output,
            context_summary=f"cloud provisioning; provider={provider}",
        )
        return
    select_prompt = _wrap_select_with_cancel_hint(select_prompt)
    text_prompt = _wrap_text_with_cancel_hint(text_prompt)
    runner = ControlledCommandRunner(trace=display_output, execution_target="local")
    auth_status = inspect_cloud_auth(provider, runner=runner)
    display_output(_render_cloud_auth_status(auth_status))
    if not auth_status.cli_available:
        installed = _offer_cloud_cli_install(
            provider=provider,
            runner=runner,
            approve_prompt=approve_prompt,
            select_prompt=select_prompt,
            display_output=display_output,
        )
        if not installed:
            return
        auth_status = inspect_cloud_auth(provider, runner=runner)
        display_output(_render_cloud_auth_status(auth_status))
    if not getattr(auth_status, "ready", False):
        if _continue_cloud_auth_setup(
            provider=provider,
            paths=paths,
            status=auth_status,
            runner=runner,
            select_prompt=select_prompt,
            text_prompt=text_prompt,
            display_output=display_output,
            terminal_interface=terminal_interface,
        ):
            auth_status = inspect_cloud_auth(provider, runner=runner)
        else:
            display_output(f"Duckln paused {provider.upper()} VM creation until the provider is ready.")
            return
    if not getattr(auth_status, "ready", False):
        display_output(f"{provider.upper()} is not ready for VM creation yet.")
        return

    default_region_or_zone = auth_status.default_region_or_zone or ("us-east-1" if provider == "aws" else "us-central1-a")
    regions_or_zones = discover_cloud_regions(provider, runner=runner)
    region_or_zone = _select_or_prompt_value(
        prompt=f"Choose {'AWS region' if provider == 'aws' else 'GCP zone'}:",
        values=(default_region_or_zone,) + regions_or_zones,
        text_prompt=text_prompt,
        select_prompt=select_prompt,
        default=default_region_or_zone,
        manual_label=f"Enter {'region' if provider == 'aws' else 'zone'} manually",
    )
    if not region_or_zone:
        display_output("Cloud create cancelled.")
        return

    discovered_shape = _pick_cloud_shape_two_tier(
        provider=provider,
        region_or_zone=region_or_zone,
        runner=runner,
        select_prompt=select_prompt,
        display_output=display_output,
    )
    if discovered_shape is None:
        display_output("Cloud create cancelled.")
        return
    shape = discovered_shape.name
    derived_disk = derived_default_disk_gb(discovered_shape)
    shape_source_url = (
        "https://cloud.google.com/compute/docs/machine-resource"
        if provider == "gcp"
        else "https://docs.aws.amazon.com/ec2/latest/instancetypes/"
    )
    default_name = _next_cloud_resource_name(paths.config_dir, provider)
    display_name = _prompt_text_or_default(
        text_prompt,
        f"Cloud resource name for {provider.upper()}:",
        default_name,
    )
    idle_timeout_minutes = _prompt_positive_int_or_default(
        text_prompt,
        "Idle timeout in minutes before Duckln warns and starts cleanup countdown:",
        30,
    )
    disk_gb = _prompt_positive_int_or_default(
        text_prompt,
        f"Boot disk in GB for {provider.upper()} ({shape}):",
        derived_disk,
    )
    key_pair_name = None
    subnet_id = None
    security_group_ids: tuple[str, ...] = ()
    project_id = auth_status.project_label
    if provider == "aws":
        key_pair_name = _select_or_prompt_value(
            prompt="Choose AWS key pair for SSH access:",
            values=discover_aws_key_pairs(runner=runner, region=region_or_zone),
            text_prompt=text_prompt,
            select_prompt=select_prompt,
            default="",
            manual_label="Enter key pair manually",
        )
        if not key_pair_name:
            display_output("Duckln cancelled AWS creation because a key pair name is required for a usable SSH path.")
            return
        subnet_id = _select_or_prompt_value(
            prompt="Choose AWS subnet, or skip:",
            values=discover_aws_subnets(runner=runner, region=region_or_zone),
            text_prompt=text_prompt,
            select_prompt=select_prompt,
            default="",
            manual_label="Enter subnet manually or leave blank",
        )
        security_groups_csv = _select_or_prompt_value(
            prompt="Choose AWS security group, or skip:",
            values=discover_aws_security_groups(runner=runner, region=region_or_zone),
            text_prompt=text_prompt,
            select_prompt=select_prompt,
            default="",
            manual_label="Enter security group ids manually or leave blank",
        )
        security_group_ids = tuple(item.strip() for item in security_groups_csv.split(",") if item.strip()) if security_groups_csv else ()
    else:
        project_id = auth_status.project_label or _select_or_create_gcp_project(
            runner=runner,
            text_prompt=text_prompt,
            select_prompt=select_prompt,
            display_output=display_output,
        )
        if not project_id:
            display_output("Duckln cancelled GCP creation because no project id was provided.")
            return

    request = CloudLaunchRequest(
        provider=provider,
        display_name=display_name,
        shape=shape,
        cpu_count=discovered_shape.cpu_count,
        memory_gb=int(round(discovered_shape.memory_gb)),
        disk_gb=disk_gb,
        region_or_zone=region_or_zone,
        idle_timeout_minutes=idle_timeout_minutes,
        key_pair_name=key_pair_name or None,
        subnet_id=subnet_id or None,
        security_group_ids=security_group_ids,
        project_id=project_id or None,
    )
    command_preview = (
        f"{create_cloud_resource.__name__} will create {display_name} on {provider.upper()} with {shape} in {region_or_zone}.\n"
        f"Requested resources: CPU={discovered_shape.cpu_count}, RAM={discovered_shape.memory_gb} GB, Disk={disk_gb} GB.\n"
        f"Idle shutdown default: {idle_timeout_minutes} minutes.\n"
        f"Shape reference: {shape_source_url}\n"
        f"Exact launch command:\n"
        f"{_render_cloud_launch_command_preview(request)}"
    )
    display_output(command_preview)
    if approve_prompt is not None and not approve_prompt(f"Do you want Duckln to create {display_name} on {provider.upper()} now?"):
        display_output(f"Duckln cancelled creating {display_name}.")
        return
    # Item: cloud auto-recovery loop. Mirror the VM behaviour — try up to 3 times,
    # surface the failure each round, do a bounded recipe lookup against the cloud CLI
    # output. We don't apply auto-fixes here (cloud CLIs require explicit user policy)
    # but we do retry transient failures (network blips) automatically.
    result = None
    for attempt in range(_VM_AUTO_RECOVERY_MAX_ATTEMPTS):
        if attempt > 0:
            display_output(
                f"Cloud create retry {attempt + 1}/{_VM_AUTO_RECOVERY_MAX_ATTEMPTS} for {display_name}…"
            )
        result = create_cloud_resource(
            request=request,
            paths=paths,
            runner=runner,
            display=display_output,
        )
        if result.ok:
            break
        # Bounded recipe lookup against cloud CLI stderr (npm/pip-style isn't relevant here,
        # but `_VM_FAILURE_RECIPES` includes generic network/permission entries that DO match
        # gcloud / aws cli output).
        haystack_msg = (result.message or "")
        recipe = _lookup_vm_failure_recipe(
            VmProvisionResult(
                ok=False,
                vm_name=display_name,
                message=haystack_msg,
                connection_commands=(),
                failure_type=VmSetupFailureType.UNKNOWN,
            )
        )
        if recipe is not None:
            display_output(f"Cloud auto-recovery: {recipe.description}")
            display_output(f"  Reference: {recipe.docs_url}")
        is_transient = any(
            token in haystack_msg.lower()
            for token in ("timeout", "could not resolve", "network is unreachable", "connection reset")
        )
        if not is_transient:
            break
    display_output(_render_cloud_launch_result(result))
    if result.ok:
        write_workflow_state(
            paths.config_dir,
            {
                "active_repo_key": None,
                "active_repo_name": None,
                "active_issue_kind": None,
                "active_issue_summary": None,
                "active_runtime_status": "running",
                "active_runtime_command": None,
                "active_runtime_command_kind": "cloud_launch",
                "active_runtime_repo_key": None,
                "active_runtime_repo_name": None,
                "active_runtime_cwd": None,
                "active_runtime_pid": None,
                "active_runtime_log_path": None,
                "active_runtime_execution_target": provider,
                "active_runtime_vm_name": None,
                "active_runtime_attach_hint": result.connect_command,
                "active_runtime_logs_hint": None,
                "active_runtime_stop_hint": result.cleanup_command or result.stop_command,
                "active_runtime_stop_command": result.stop_command,
                "active_runtime_docker_name": None,
                "active_runtime_cloud_resource_key": result.resource_key,
                "active_runtime_cloud_vendor": provider.upper(),
                "active_runtime_cloud_region": result.region_or_zone,
                "active_runtime_cloud_shape": result.shape,
            },
        )


def _handle_resource_keepalive_command(
    *,
    paths: ConfigPaths,
    select_prompt: Callable[[str, tuple[str, ...]], str | None],
    text_prompt: Callable[[str, str], str | None] | None,
    display_output: Callable[[str], None],
) -> None:
    store = initialize_state_store(paths.config_dir)
    resources = store.list_managed_resources()
    if not resources:
        display_output("Duckln is not tracking any managed resources right now.")
        return
    selected = select_prompt(
        "Choose a managed resource to keep alive:",
        tuple(f"{record.display_name} [{record.provider}] — {record.status}" for record in resources) + ("Cancel",),
    )
    if selected in {None, "Cancel"}:
        display_output("Keepalive update cancelled.")
        return
    chosen = next((record for record in resources if selected.startswith(f"{record.display_name} [{record.provider}]")), None)
    if chosen is None:
        display_output("Duckln could not resolve that managed resource.")
        return
    extra_minutes = _prompt_positive_int_or_default(text_prompt, "Add how many keepalive minutes?", 30)
    updated = extend_managed_resource_keepalive(paths.config_dir, resource_key=chosen.resource_key, extra_minutes=extra_minutes)
    if updated is None:
        display_output("Duckln could not extend the keepalive timer for that resource.")
        return
    display_output(
        f"Duckln extended keepalive for {updated.display_name} by {extra_minutes} minutes. "
        f"New idle timeout: {updated.idle_timeout_minutes} minutes."
    )


def _handle_managed_resource_cleanup_command(
    *,
    paths: ConfigPaths,
    select_prompt: Callable[[str, tuple[str, ...]], str | None],
    approve_prompt: Callable[[str], bool] | None,
    display_output: Callable[[str], None],
) -> None:
    store = initialize_state_store(paths.config_dir)
    resources = store.list_managed_resources()
    if not resources:
        display_output("Duckln is not tracking any managed resources right now.")
        return
    selected = select_prompt(
        "Choose a managed resource to clean up:",
        tuple(f"{record.display_name} [{record.provider}] — {record.status}" for record in resources) + ("Cancel",),
    )
    if selected in {None, "Cancel"}:
        display_output("Managed resource cleanup cancelled.")
        return
    chosen = next((record for record in resources if selected.startswith(f"{record.display_name} [{record.provider}]")), None)
    if chosen is None:
        display_output("Duckln could not resolve that managed resource.")
        return
    destructive = chosen.provider in {"aws", "gcp"}
    if approve_prompt is not None and not approve_prompt(
        f"Do you want Duckln to {'remove' if destructive else 'stop'} {chosen.display_name} now?"
    ):
        display_output(f"Duckln left {chosen.display_name} unchanged.")
        return
    ok, message = cleanup_managed_resource(
        config_dir=paths.config_dir,
        resource_key=chosen.resource_key,
        runner=ControlledCommandRunner(trace=display_output, execution_target="local"),
        destructive=destructive,
    )
    display_output(message)
    if ok and chosen.provider in {"aws", "gcp"}:
        write_workflow_state(
            paths.config_dir,
            {
                "active_runtime_status": "terminated",
                "active_runtime_stop_hint": None,
                "active_runtime_stop_command": None,
                "active_runtime_cloud_resource_key": None,
                "active_runtime_cloud_vendor": chosen.provider.upper(),
                "active_runtime_cloud_region": chosen.region,
                "active_runtime_cloud_shape": chosen.shape,
            },
        )


def _handle_cloud_attach_action(
    *,
    paths: ConfigPaths,
    select_prompt: Callable[[str, tuple[str, ...]], str | None],
    display_output: Callable[[str], None],
    terminal_interface: object | None,
) -> None:
    record = _select_cloud_managed_resource(paths.config_dir, select_prompt=select_prompt)
    if record is None:
        display_output("Cloud attach cancelled.")
        return
    attached = stage_cloud_terminal_attach(
        record=record,
        terminal_executor=terminal_interface,
        display=display_output,
    )
    if not attached:
        display_output(f"Duckln could not open {record.display_name} in the terminal pane from this session.")
        return
    record_managed_resource_activity(paths.config_dir, resource_key=record.resource_key)
    write_workflow_state(
        paths.config_dir,
        {
            "active_runtime_status": "interactive",
            "active_runtime_command": record.metadata.get("connect_command") if isinstance(record.metadata.get("connect_command"), str) else None,
            "active_runtime_command_kind": "cloud_attach",
            "active_runtime_repo_key": None,
            "active_runtime_repo_name": None,
            "active_runtime_cwd": None,
            "active_runtime_pid": None,
            "active_runtime_log_path": None,
            "active_runtime_execution_target": record.provider,
            "active_runtime_vm_name": None,
            "active_runtime_attach_hint": str(record.metadata.get("connect_command") or "").strip() or None,
            "active_runtime_logs_hint": None,
            "active_runtime_stop_hint": str(record.metadata.get("cleanup_command") or record.metadata.get("stop_command") or "").strip() or None,
            "active_runtime_stop_command": str(record.metadata.get("stop_command") or "").strip() or None,
            "active_runtime_docker_name": None,
            "active_runtime_cloud_resource_key": record.resource_key,
            "active_runtime_cloud_vendor": record.provider.upper(),
            "active_runtime_cloud_region": record.region,
            "active_runtime_cloud_shape": record.shape,
        },
    )
    display_output(f"Supervisor agent opened {record.display_name} in Duckln’s terminal pane.")


def _handle_cloud_bootstrap_action(
    *,
    paths: ConfigPaths,
    select_prompt: Callable[[str, tuple[str, ...]], str | None],
    approve_prompt: Callable[[str], bool] | None,
    display_output: Callable[[str], None],
    terminal_interface: object | None,
) -> None:
    record = _select_cloud_managed_resource(paths.config_dir, select_prompt=select_prompt)
    if record is None:
        display_output("Cloud bootstrap cancelled.")
        return
    bootstrap_command = build_cloud_remote_exec_command(
        record,
        remote_command="mkdir -p ~/.duckln/projects ~/.duckln/memory/sessions ~/.duckln/memory/knowledge ~/.duckln/memory/skills && python3 --version && git --version",
    )
    if not bootstrap_command:
        display_output(f"Duckln does not have a working remote command path for {record.display_name} yet.")
        return
    if approve_prompt is not None and not approve_prompt(f"Do you want Duckln to bootstrap its runtime folders on {record.display_name} now?"):
        display_output(f"Duckln left {record.display_name} unchanged.")
        return
    _run_cloud_wrapped_command(
        paths=paths,
        record=record,
        wrapped_command=bootstrap_command,
        display_output=display_output,
        terminal_interface=terminal_interface,
        summary_prefix=f"Supervisor agent bootstrapped Duckln runtime on {record.display_name}.",
        command_kind="cloud_bootstrap",
    )


def _handle_cloud_remote_command_action(
    *,
    paths: ConfigPaths,
    select_prompt: Callable[[str, tuple[str, ...]], str | None],
    text_prompt: Callable[[str, str], str | None] | None,
    approve_prompt: Callable[[str], bool] | None,
    display_output: Callable[[str], None],
    terminal_interface: object | None,
) -> None:
    record = _select_cloud_managed_resource(paths.config_dir, select_prompt=select_prompt)
    if record is None:
        display_output("Remote command cancelled.")
        return
    remote_cwd = _prompt_text_or_default(text_prompt, "Remote working directory:", "~/.duckln")
    remote_command = _prompt_text_or_default(text_prompt, "Bounded remote command to run:", "")
    if not remote_command:
        display_output("Duckln cancelled the remote command because no command was provided.")
        return
    wrapped_command = build_cloud_remote_exec_command(record, remote_command=remote_command, remote_cwd=remote_cwd)
    if not wrapped_command:
        display_output(f"Duckln does not have a working remote command path for {record.display_name} yet.")
        return
    display_output(
        render_tool_invocation_trace(
            title="cloud remote command review",
            tool_id=f"cloud.{record.provider}_sdk",
            action=f"Run a bounded remote command on {record.display_name}",
            detail_lines=(
                f"Remote working directory: {remote_cwd}",
                f"Remote command: {remote_command}",
                f"Wrapped transport: {wrapped_command}",
            ),
            execution_target=record.provider,
        )
    )
    if approve_prompt is not None and not approve_prompt(f"Do you want Duckln to run that remote command on {record.display_name} now?"):
        display_output(f"Duckln cancelled the remote command for {record.display_name}.")
        return
    _run_cloud_wrapped_command(
        paths=paths,
        record=record,
        wrapped_command=wrapped_command,
        display_output=display_output,
        terminal_interface=terminal_interface,
        summary_prefix=f"Supervisor agent sent the bounded remote command to {record.display_name}.",
        command_kind="cloud_remote_exec",
    )


def _run_cloud_wrapped_command(
    *,
    paths: ConfigPaths,
    record,
    wrapped_command: str,
    display_output: Callable[[str], None],
    terminal_interface: object | None,
    summary_prefix: str,
    command_kind: str,
) -> None:
    started_in_terminal = False
    if terminal_interface is not None and hasattr(terminal_interface, "run_terminal_command"):
        started_in_terminal = bool(terminal_interface.run_terminal_command(command=wrapped_command))
    if started_in_terminal:
        record_managed_resource_activity(paths.config_dir, resource_key=record.resource_key)
        write_workflow_state(
            paths.config_dir,
            {
                "active_runtime_status": "interactive",
                "active_runtime_command": wrapped_command,
                "active_runtime_command_kind": command_kind,
                "active_runtime_repo_key": None,
                "active_runtime_repo_name": None,
                "active_runtime_cwd": None,
                "active_runtime_pid": None,
                "active_runtime_log_path": None,
                "active_runtime_execution_target": record.provider,
                "active_runtime_vm_name": None,
                "active_runtime_attach_hint": str(record.metadata.get("connect_command") or "").strip() or None,
                "active_runtime_logs_hint": None,
                "active_runtime_stop_hint": str(record.metadata.get("cleanup_command") or record.metadata.get("stop_command") or "").strip() or None,
                "active_runtime_stop_command": str(record.metadata.get("stop_command") or "").strip() or None,
                "active_runtime_docker_name": None,
                "active_runtime_cloud_resource_key": record.resource_key,
                "active_runtime_cloud_vendor": record.provider.upper(),
                "active_runtime_cloud_region": record.region,
                "active_runtime_cloud_shape": record.shape,
            },
        )
        display_output(summary_prefix)
        return
    runner = ControlledCommandRunner(trace=display_output, execution_target="local")
    result = runner.run(wrapped_command)
    record_managed_resource_activity(paths.config_dir, resource_key=record.resource_key)
    if result.exit_code == 0 and not result.timed_out:
        display_output(summary_prefix)
    else:
        display_output(f"Duckln could not complete the remote command for {record.display_name} cleanly.")


def _select_cloud_managed_resource(
    config_dir: Path,
    *,
    select_prompt: Callable[[str, tuple[str, ...]], str | None],
):
    resources = tuple(
        record
        for record in initialize_state_store(config_dir).list_managed_resources()
        if record.provider in {"aws", "gcp"} and record.status not in {"terminated"}
    )
    if not resources:
        return None
    labels = tuple(f"{record.display_name} [{record.provider}] — {record.region or 'region'} — {record.status}" for record in resources) + ("Cancel",)
    selected = select_prompt("Choose a managed cloud resource:", labels)
    if selected in {None, "Cancel"}:
        return None
    return next((record for record, label in zip(resources, labels) if label == selected), None)


def _render_cloud_launch_command_preview(request: CloudLaunchRequest) -> str:
    if request.provider == "aws":
        from duckln.cloud_runtime import build_aws_launch_command

        return build_aws_launch_command(request)
    from duckln.cloud_runtime import build_gcp_launch_command

    return build_gcp_launch_command(request)


def _render_cloud_launch_result(result) -> str:
    lines = [result.message]
    if result.connect_command:
        lines.append(f"Connect: {result.connect_command}")
    if result.stop_command:
        lines.append(f"Stop: {result.stop_command}")
    if result.cleanup_command:
        lines.append(f"Cleanup: {result.cleanup_command}")
    if result.source_urls:
        lines.append("Sources: " + "; ".join(f"[{index}] {url}" for index, url in enumerate(result.source_urls, start=1)))
    return "\n".join(lines)


def _render_discovered_cloud_resources(resources, *, provider_label: str) -> str:
    if not resources:
        return f"Duckln did not find any Duckln-tagged {provider_label} resources with the current CLI context."
    lines = [f"Discovered Duckln-tagged {provider_label} resources:"]
    for resource in resources:
        shape = f" • {resource.shape}" if resource.shape else ""
        lines.append(f"- {resource.display_name} [{resource.state}] • {resource.region_or_zone}{shape}")
    return "\n".join(lines)


def _next_cloud_resource_name(config_dir: Path, provider: str) -> str:
    prefix = f"duckln-{provider}"
    existing = {record.display_name for record in initialize_state_store(config_dir).list_managed_resources(provider=provider)}
    if prefix not in existing:
        return prefix
    suffix = 1
    while f"{prefix}-{suffix}" in existing:
        suffix += 1
    return f"{prefix}-{suffix}"


def _prompt_text_or_default(
    text_prompt: Callable[[str, str], str | None] | None,
    message: str,
    default: str,
) -> str:
    if text_prompt is None:
        return default
    return (text_prompt(message, default) or default).strip()


def _prompt_positive_int_or_default(
    text_prompt: Callable[[str, str], str | None] | None,
    message: str,
    default: int,
) -> int:
    raw = _prompt_text_or_default(text_prompt, message, str(default))
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def show_startup_messages(*, display_fn: Callable[[str], None], current: AppConfig, paths: ConfigPaths) -> None:
    """Render startup transcript lines once per session."""

    global _startup_messages_shown
    if _startup_messages_shown:
        return
    _startup_messages_shown = True
    display_fn(_build_session_greeting(current=current, paths=paths))
    # Plan 169 F1: an objective shown in the resume greeting is AWAITING the user's decision,
    # not actively running — mark it so the status footer renders "Awaiting your input" (no
    # spinner) instead of a fake "Working… 30s" before the user answers. Accepting the resume
    # re-activates it (`_update_active_runtime_objective`).
    try:
        wf = _AGENT_CONTEXT_SERVICE.load_workflow_state(config_dir=paths.config_dir)
        if (
            wf is not None
            and not _active_objective_is_stale_for_session(wf, paths.config_dir)
            and getattr(wf, "active_objective_kind", None)
            and getattr(wf, "active_objective_repo_name", None)
            and not getattr(wf, "active_objective_requires_user_decision", False)
        ):
            write_workflow_state(paths.config_dir, {"active_objective_requires_user_decision": True})
    except Exception:
        pass


def _build_session_greeting(*, current: AppConfig, paths: ConfigPaths) -> str:
    alias = current.user_name.strip() or "there"
    snapshot = read_config_snapshot(paths.config_dir)
    last_seen = _parse_timestamp(snapshot.get("session.last_seen_at"))
    last_greeting_id = snapshot.get("session.last_greeting_id", "")
    latest_repo_state = initialize_state_store(paths.config_dir).get_latest_repo_state()
    workflow = _AGENT_CONTEXT_SERVICE.load_workflow_state(config_dir=paths.config_dir)
    active_repo_name = ""
    active_repo_status = ""
    if latest_repo_state is not None:
        active_repo_name = str(latest_repo_state.metadata.get("repo_name") or latest_repo_state.repo_key or "").strip()
        active_repo_status = latest_repo_state.status

    now = datetime.now(timezone.utc)
    greeting_options: list[tuple[str, str]] = []
    workflow_is_current = workflow is not None and not _active_objective_is_stale_for_session(workflow, paths.config_dir)
    if workflow_is_current and workflow is not None and workflow.active_objective_kind and workflow.active_objective_repo_name:
        resume_hint = workflow.active_objective_resume_hint or (
            f"Last time Duckln was {_objective_resume_verb(workflow.active_objective_kind)} "
            f"{workflow.active_objective_repo_name}."
        )
        greeting_options.append(
            (
                "resume-objective",
                f"🐣 {alias}. {resume_hint}\nWant Duckln to continue from there?",
            )
        )
    if active_repo_name and active_repo_status in {"setup", "setting_up", "repairing", "failed", "planned", "in_progress"}:
        target_label = _repo_target_label(latest_repo_state) if latest_repo_state is not None else "tracked target"
        checked = (
            f", last checked {latest_repo_state.updated_at.split('T')[0]}"
            if latest_repo_state is not None and latest_repo_state.updated_at
            else ""
        )
        greeting_options.append(
            (
                "resume-repo",
                f"🐣 {alias}. {active_repo_name} on {target_label}: {active_repo_status}{checked}.",
            )
        )
    if last_seen is not None:
        days_away = (now - last_seen).days
        if days_away >= 3:
            greeting_options.extend(
                (
                    ("returning-minute", f"🐣 Hey {alias} — been a minute. What are we getting into today?"),
                    ("returning-back", f"🐣 {alias}! Good to have you back. What are we running?"),
                    ("returning-returns", f"🐣 {alias} returns. Let's get something running."),
                )
            )
        elif days_away == 0:
            greeting_options.extend(
                (
                    ("same-day-back", f"🐣 Back again {alias} — what's next?"),
                    ("same-day-run", f"🐣 {alias}. Let's run something."),
                )
            )
    current_hour = datetime.now().hour
    if 5 <= current_hour < 12:
        greeting_options.extend(
            (
                ("morning-break", f"🐣 Morning {alias}! Ready to break some repos?"),
                ("morning-setup", f"🐣 Morning {alias}. What are we setting up today?"),
            )
        )
    elif 12 <= current_hour < 17:
        greeting_options.extend(
            (
                ("afternoon-repo", f"🐣 Hey {alias} — got a repo in mind?"),
                ("afternoon-next", f"🐣 Back again {alias} — what's next?"),
            )
        )
    elif 17 <= current_hour < 22:
        greeting_options.extend(
            (
                ("evening-break", f"🐣 Hey {alias} — what are we breaking tonight?"),
                ("evening-good", f"🐣 {alias}! Good to see you. What's on the list?"),
            )
        )
    else:
        greeting_options.extend(
            (
                ("late-running", f"🐣 Still at it {alias}? Let's get this running."),
                ("late-fighting", f"🐣 Late night {alias}. What repo are we fighting tonight?"),
            )
        )
    if not greeting_options:
        greeting_options.append(("default", f"🐣 Hey {alias}. What are we working on today?"))

    filtered = [item for item in greeting_options if item[0] != last_greeting_id] or greeting_options
    greeting_id, greeting = random.choice(filtered)
    session_id = f"session-{int(now.timestamp())}-{random.randint(1000, 9999)}"
    write_config_snapshot(
        paths.config_dir,
        {
            "session.current_id": session_id,
            "session.started_at": now.isoformat(timespec="seconds"),
            "session.last_seen_at": now.isoformat(timespec="seconds"),
            "session.last_greeting_id": greeting_id,
            "session.last_repo_name": active_repo_name,
        },
    )
    return greeting


def _parse_timestamp(raw_value: str | None) -> datetime | None:
    if not raw_value:
        return None
    try:
        parsed = datetime.fromisoformat(raw_value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _render_repo_preflight_summary(
    *,
    repo: RepoCatalogRecord,
    preflight,
) -> str:
    feasibility = preflight.feasibility
    blocks = [paragraph_block(f"You selected {repo.name}.")]
    fit_line = f"{repo.name} looks comfortable on this machine."
    if preflight.fit_status == "workable_but_tight":
        fit_line = f"{repo.name} can work here, but memory will be tight."
    elif preflight.fit_status == "not_recommended":
        fit_line = f"{repo.name} does not look like a strong fit on this machine."
    blocks.append(status_block("Machine fit", fit_line))

    practical_pairs: list[tuple[str, str]] = []
    if feasibility.usable_ram_gib is not None:
        practical_pairs.append(("Usable RAM", f"About {feasibility.usable_ram_gib:.1f} GiB after normal system overhead."))
    if feasibility.failure_modes:
        practical_pairs.append(("Watch-out", ", ".join(feasibility.failure_modes[:2])))
    elif feasibility.compute_path:
        practical_pairs.append(("Practical path", feasibility.compute_path))
    if practical_pairs:
        blocks.append(comparison_block("Practical notes", tuple(practical_pairs[:2])))
    if preflight.local_vm_recommendation:
        blocks.append(action_block("If you want a cleaner next move:", (preflight.local_vm_recommendation,)))
    return join_blocks(*(block for block in blocks[:4] if block))


def _render_repo_requirements_summary(
    *,
    repo: RepoCatalogRecord,
    preflight,
    repo_knowledge,
) -> str:
    feasibility = preflight.feasibility
    profiles = [
        profile
        for profile in (
            repo_knowledge.cpu_profile if repo_knowledge is not None else None,
            repo_knowledge.ram_profile if repo_knowledge is not None else None,
            repo_knowledge.gpu_profile if repo_knowledge is not None else None,
        )
        if profile
    ]
    profile_text = "; ".join(profiles) if profiles else "bounded repo notes"
    blocks = [paragraph_block(f"For this machine, {repo.name} looks more like {profile_text}.")]
    practical_pairs: list[tuple[str, str]] = [("Profile", profile_text)]
    if feasibility.usable_ram_gib is not None:
        practical_pairs.append(("Usable RAM", f"About {feasibility.usable_ram_gib:.1f} GiB after system overhead."))
    if repo_knowledge is not None and repo_knowledge.required_tools:
        blocks.append(action_block("You'll likely want:", tuple(repo_knowledge.required_tools[:4])))
    if feasibility.failure_modes:
        practical_pairs.append(("Watch-out", ", ".join(feasibility.failure_modes[:2])))
    elif preflight.fit_status == "comfortable":
        practical_pairs.append(("Local path", "This looks like a reasonable local first run."))
    if practical_pairs:
        blocks.append(comparison_block("Practical fit", tuple(practical_pairs[:3])))
    if preflight.local_vm_recommendation and len(blocks) < 4:
        blocks.append(action_block("VM option", (preflight.local_vm_recommendation,)))
    return join_blocks(*(block for block in blocks[:4] if block))


def _repo_choice_prompt(*, repo_name: str) -> ConversationChoicePrompt:
    return ConversationChoicePrompt(
        message=f"You selected {repo_name}. What do you want to do next?",
        options=(
            ConversationChoiceOption(
                label="Set it up",
                action_key="set_it_up",
                description="Take the smallest verifiable setup path.",
                recommended=True,
            ),
            ConversationChoiceOption(
                label="Inspect requirements first",
                action_key="inspect_requirements",
                description="See the practical fit and tool needs before making changes.",
            ),
            ConversationChoiceOption(
                label="Cancel",
                action_key="cancel",
                description="Leave everything unchanged.",
            ),
        ),
    )


def _repo_environment_choice_prompt(
    *,
    repo_name: str,
    current_execution_target: str,
    current_vm_name: str | None = None,
) -> ConversationChoicePrompt:
    local_recommended = current_execution_target not in {"vm", "aws", "gcp"}
    vm_label = (
        f"Set up in Ubuntu VM ({current_vm_name})"
        if current_execution_target == "vm" and current_vm_name
        else "Set up in Ubuntu VM"
    )
    cloud_label = (
        f"Set up on cloud VM ({current_execution_target.upper()})"
        if current_execution_target in {"aws", "gcp"}
        else "Set up on cloud VM"
    )
    return ConversationChoicePrompt(
        message=f"Where should Duckln set up {repo_name}?",
        options=(
            ConversationChoiceOption(
                label="Set up on local machine",
                action_key="use_local",
                description="Use the current machine and keep the setup path local.",
                recommended=local_recommended,
            ),
            ConversationChoiceOption(
                label=vm_label,
                action_key="use_vm",
                description="Use an Ubuntu VM for a cleaner Linux-first setup target.",
                recommended=current_execution_target == "vm",
            ),
            ConversationChoiceOption(
                label=cloud_label,
                action_key="use_cloud",
                description="Choose or create a managed cloud VM before Duckln starts setup there.",
                recommended=current_execution_target in {"aws", "gcp"},
            ),
            ConversationChoiceOption(
                label="Set up with Docker on local machine",
                action_key="use_docker",
                description="Keep execution local and let Duckln prefer documented Docker or Compose steps when the repo supports them.",
            ),
            ConversationChoiceOption(
                label="Cancel",
                action_key="cancel",
                description="Stop here without changing anything.",
            ),
        ),
    )


def _repo_warning_choice_prompt(
    *,
    repo_name: str,
    offer_vm: bool = False,
) -> ConversationChoicePrompt:
    options: list[ConversationChoiceOption] = []
    if offer_vm:
        options.append(
            ConversationChoiceOption(
                label="Use a VM instead",
                action_key="use_vm",
                description="Use a cleaner setup target when local memory is tight.",
                recommended=True,
            )
        )
    options.append(
        ConversationChoiceOption(
            label="Pick a lighter repo",
            action_key="pick_lighter_repo",
            description="Pick a smoother first run.",
            recommended=not offer_vm,
        )
    )
    options.extend(
        (
            ConversationChoiceOption(
                label="Continue anyway",
                action_key="continue_anyway",
                description="Keep going with this repo even though the fit looks tight.",
            ),
            ConversationChoiceOption(
                label="Cancel",
                action_key="cancel",
                description="Stop here without changing anything.",
            ),
        )
    )
    return ConversationChoicePrompt(
        message=f"{repo_name} looks rough on this machine. What should Duckln do?",
        options=tuple(options),
    )


def _persist_repo_followup_state(
    *,
    paths: ConfigPaths,
    system_probe,
    repo: RepoCatalogRecord,
    preflight=None,
    choice_prompt: ConversationChoicePrompt | None = None,
    chosen_option: ConversationChoiceOption | None = None,
    completed_step: str | None = None,
    pending_next_action: str | None = None,
    execution_target: str | None = None,
    vm_name: str | None = None,
) -> None:
    snapshot = read_config_snapshot(paths.config_dir)
    latest_state = initialize_state_store(paths.config_dir).get_latest_repo_state()
    workflow = _AGENT_CONTEXT_SERVICE.load_workflow_state(config_dir=paths.config_dir)
    objective_id = _workflow_objective_identity(workflow)
    if objective_id is None:
        objective_action = str(pending_next_action or completed_step or "repo_followup").strip().lower() or "repo_followup"
        objective_anchor = str(repo.repo_url or repo.name).strip()
        if objective_anchor:
            objective_id = f"{objective_action}:{objective_anchor}"
    resolved_execution_target = execution_target or snapshot.get("execution_target", "local") or "local"
    if execution_target is not None:
        resolved_vm_name = vm_name
    else:
        resolved_vm_name = snapshot.get("execution_vm_name")
    write_followup_state(
        paths.config_dir,
        {
            "last_supervisor_decision": "repo_preflight",
            "last_route_family": "workflow_action",
            "last_route_confidence": 0.99,
            "last_response_contract": "workflow_action",
            "active_topic": "slash_runtime_followup",
            "pending_objective_id": objective_id,
            "pending_repo_key": repo.repo_url,
            "pending_repo_name": repo.name,
            "pending_next_action": pending_next_action,
            "pending_offer_objective_id": objective_id if pending_next_action else None,
            "pending_subject_type": "repo",
            "last_detected_system_summary": None if system_probe is None else system_probe.summary(),
            "last_recommendation_fit": None if preflight is None else preflight.fit_status,
            "last_answer_style": "slash_runtime",
            "last_discussed_repo_key": repo.repo_url,
            "last_discussed_repo_name": repo.name,
            "last_choice_prompt": choice_prompt.message if choice_prompt is not None else None,
            "last_chosen_option": chosen_option.action_key if chosen_option is not None else None,
            "last_next_step_offered": pending_next_action,
            "last_completed_step": completed_step,
            "shortlist_repo_names": (repo.name,),
            "shortlist_primary_repo": repo.name,
            "shortlist_secondary_repo": None,
            "last_render_fingerprint": f"slash_runtime|{repo.name}|{completed_step or 'preflight'}",
            "last_slash_runtime_action": completed_step,
            "active_execution_target": resolved_execution_target,
            "active_vm_name": resolved_vm_name,
            "last_plan_path": None if latest_state is None else latest_state.metadata.get("plan_path"),
        },
    )


def _persist_session_execution_target(
    *,
    paths: ConfigPaths,
    execution_target: str,
    vm_name: str | None = None,
    target_metadata: dict[str, object | None] | None = None,
) -> None:
    payload: dict[str, object | None] = {
        "execution_target": execution_target,
        "execution_vm_name": vm_name if execution_target == "vm" else None,
        "active_vm_name": vm_name if execution_target == "vm" else None,
    }
    if target_metadata:
        payload.update(target_metadata)
    write_config_snapshot(paths.config_dir, payload)


def _followup_execution_context(paths: ConfigPaths) -> tuple[str, str | None]:
    followup_state = read_followup_state(paths.config_dir)
    snapshot = read_config_snapshot(paths.config_dir)
    execution_target = (
        str(followup_state.get("active_execution_target") or "").strip()
        or str(snapshot.get("execution_target") or "local").strip()
        or "local"
    )
    vm_name = (
        str(followup_state.get("active_vm_name") or "").strip()
        or str(snapshot.get("execution_vm_name") or "").strip()
        or str(snapshot.get("active_vm_name") or "").strip()
        or None
    )
    return execution_target, vm_name


def _chat_supports_activity(chat: object | None) -> bool:
    return bool(chat is not None and hasattr(chat, "set_activity_text") and hasattr(chat, "clear_activity_text"))


def _chat_supports_objective_status(chat: object | None) -> bool:
    return bool(chat is not None and hasattr(chat, "update_objective_status") and hasattr(chat, "clear_objective_status"))


def _set_chat_activity(chat: object | None, message: str, *, spinner: bool = True) -> None:
    if not _chat_supports_activity(chat):
        return
    try:
        chat.set_activity_text(message, spinner=spinner)
    except Exception:
        return


def _clear_chat_activity(chat: object | None) -> None:
    if not _chat_supports_activity(chat):
        return
    try:
        chat.clear_activity_text()
    except Exception:
        return


def _objective_status_label(value: str | None) -> str:
    normalized = str(value or "").strip().casefold()
    if normalized == "needs_user_decision":
        return "waiting on you"
    if normalized == "active":
        return "can continue"
    if normalized == "verified":
        return "verified"
    if normalized == "complete":
        return "complete"
    if normalized == "failed":
        return "blocked"
    if normalized == "declined":
        return "paused"
    if normalized:
        return normalized.replace("_", " ")
    return "active"


def _objective_target_label(value: str | None) -> str | None:
    normalized = str(value or "").strip().casefold()
    if not normalized:
        return None
    if normalized == "vm":
        return "ubuntu vm"
    return normalized


def _objective_kind_label(value: str | None) -> str:
    """Plan 168 F1: honest, per-kind objective wording so a paused SETUP reads as "setting up",
    not "repo deploy" / "runtime repair". Only a genuine runtime failure says "runtime repair"."""
    normalized = str(value or "").strip().casefold()
    if normalized in {"repo_deploy", "repo_setup", "bringup", "setup"}:
        return "setting up"
    if normalized == "runtime_repair":
        return "runtime repair"
    return normalized.replace("_", " ").strip()


def _objective_resume_verb(value: str | None) -> str:
    """Plan 169 F2: the verb for the resume greeting — a paused SETUP reads "setting up",
    a genuine runtime failure reads "fixing", anything else a neutral "working on"."""
    normalized = str(value or "").strip().casefold()
    if normalized in {"repo_deploy", "repo_setup", "bringup", "setup"}:
        return "setting up"
    if normalized == "runtime_repair":
        return "fixing"
    return "working on"


def _objective_incident_label(value: str | None) -> str | None:
    normalized = str(value or "").strip().casefold()
    if not normalized:
        return None
    mapping = {
        "docker_env_missing": "docker env blocker",
        "missing_command": "missing tool blocker",
        "dependency_install_failure": "dependency blocker",
        "runtime_failure": "runtime blocker",
        "vm_bootstrap_failure": "vm transport blocker",
        "user_input_error": "input issue",
        "app_prompt_active": "app prompt",
    }
    return mapping.get(normalized, normalized.replace("_", " "))


def _objective_phase_label(*, phase: str | None, incident_category: str | None) -> str | None:
    normalized = str(phase or "").strip().casefold()
    incident_label = _objective_incident_label(incident_category)
    if not normalized:
        return None
    if normalized in {"runtime_repair_planning", "runtime_failed"}:
        return f"diagnosing {incident_label}" if incident_label else "diagnosing blocker"
    if normalized == "guidance_only":
        return "waiting for corrected input"
    if normalized == "awaiting_runtime_approval":
        return "waiting to run bounded check"
    if normalized == "awaiting_runtime_docker_env_approval":
        return "waiting to repair docker env"
    if normalized == "runtime_docker_env_repair_running":
        return "applying docker env fix"
    if normalized == "post_docker_env_rerun":
        return "verifying docker env fix"
    if normalized == "awaiting_runtime_prerequisite_approval":
        return "waiting to install prerequisite"
    if normalized == "runtime_prerequisite_repair_running":
        return "installing prerequisite"
    if normalized == "post_prerequisite_rerun":
        return "verifying prerequisite fix"
    if normalized == "awaiting_runtime_dependency_approval":
        return "waiting to repair repo dependencies"
    if normalized == "runtime_dependency_repair_running":
        return "applying repo dependency fix"
    if normalized == "post_dependency_repair_rerun":
        return "verifying repo dependency fix"
    if normalized == "vm_repair_escalation":
        return "waiting on vm transport repair"
    if normalized == "runtime_vm_repair_running":
        return "repairing vm transport"
    if normalized == "runtime_repair_evidence":
        return "reviewing repair evidence"
    if normalized == "repair_handoff":
        return "running bounded repair workflow"
    if normalized == "repair_verified":
        return "repair verified"
    if normalized == "post_repair_rerun":
        return "verifying repaired runtime"
    if normalized == "runtime_verified":
        return "runtime verified"
    if normalized == "repair_declined":
        return "repair paused"
    if normalized == "repair_failed":
        return "repair failed"
    if normalized == "post_repair_runtime_failed":
        return "rerun failed after repair"
    if normalized == "repair_limit_reached":
        return "awaiting escalation"
    return normalized.replace("_", " ")


def _render_chat_objective_status(workflow) -> str | None:
    if workflow is None or not getattr(workflow, "active_objective_kind", None) or not getattr(workflow, "active_objective_repo_name", None):
        return None
    attempt = getattr(workflow, "active_objective_attempt_count", None) or 0
    max_attempts = getattr(workflow, "active_objective_max_attempts", None) or 0
    raw_status = str(getattr(workflow, "active_objective_status", None) or "").strip().casefold()
    requires_user_decision = bool(getattr(workflow, "active_objective_requires_user_decision", False))
    target = _objective_target_label(getattr(workflow, "active_objective_execution_target", None))
    step = _objective_phase_label(
        phase=getattr(workflow, "active_repair_phase", None),
        incident_category=getattr(workflow, "active_incident_category", None),
    )

    # Item C: resolve contradictions. When the underlying status is "failed" but the
    # state machine is also marked as awaiting a user decision, pick the dominant label
    # so the user doesn't see "failed • waiting approval" in the same line. Failed wins
    # on display; "needs your decision" is appended as a single trailing tag.
    if raw_status == "failed":
        primary_status = "failed"
    elif raw_status == "needs_user_decision" or (raw_status == "active" and requires_user_decision):
        primary_status = "waiting on you"
    else:
        primary_status = _objective_status_label(getattr(workflow, "active_objective_status", None))

    parts = [
        str(getattr(workflow, "active_objective_repo_name", "")).strip(),
        _objective_kind_label(getattr(workflow, "active_objective_kind", "")),
    ]
    if step:
        parts.append(step)
    parts.append(primary_status)
    if attempt > 0 and max_attempts > 0:
        parts.append(f"attempt {attempt}/{max_attempts}")
    elif max_attempts > 0 and primary_status != "failed":
        parts.append(f"attempt 0/{max_attempts}")
    if target:
        parts.append(target)
    if primary_status == "failed" and requires_user_decision:
        parts.append("decide next step")
    elif primary_status == "waiting on you":
        pass  # already says it
    elif raw_status == "active":
        parts.append("auto")
    if max_attempts > 0 and attempt >= max_attempts and primary_status != "failed":
        parts.append("escalation needed")
    return " • ".join(part for part in parts if part)


def _phase_to_action_verb(phase_lower: str) -> str:
    """Map a phase label to a leading action verb the user can read at a glance."""

    if not phase_lower:
        return "Working on"
    if "diagnos" in phase_lower or "review" in phase_lower:
        return "Investigating"
    if "install" in phase_lower:
        return "Installing"
    if "download" in phase_lower or "fetch" in phase_lower or "pull" in phase_lower:
        return "Downloading"
    if "clon" in phase_lower:
        return "Cloning"
    if "build" in phase_lower or "compil" in phase_lower:
        return "Building"
    if "test" in phase_lower:
        return "Testing"
    if "verif" in phase_lower:
        return "Verifying"
    if "appl" in phase_lower or "fix" in phase_lower or "repair" in phase_lower:
        return "Repairing"
    if "rerun" in phase_lower or "retry" in phase_lower:
        return "Re-running"
    if "wait" in phase_lower or "approval" in phase_lower:
        return "Waiting"
    if "escalat" in phase_lower or "fail" in phase_lower or "paused" in phase_lower:
        return "Paused on"
    return "Working on"


def _render_chat_objective_activity(workflow) -> tuple[str, bool] | None:
    if workflow is None or not getattr(workflow, "active_objective_kind", None) or not getattr(workflow, "active_objective_repo_name", None):
        return None
    repo_name = str(getattr(workflow, "active_objective_repo_name", "")).strip()
    phase = _objective_phase_label(
        phase=getattr(workflow, "active_repair_phase", None),
        incident_category=getattr(workflow, "active_incident_category", None),
    )
    status = str(getattr(workflow, "active_objective_status", None) or "").strip().casefold()
    requires_user_decision = bool(getattr(workflow, "active_objective_requires_user_decision", False))
    if requires_user_decision:
        phase_lower = str(phase or "").casefold()
        approval_phase = "approval" in phase_lower or "waiting to " in phase_lower
        if phase:
            prefix = "Awaiting approval" if approval_phase else "Awaiting your input"
            return f"{prefix} — {phase} on {repo_name}", False
        return f"Awaiting your input on {repo_name}", False
    if status in {"verified", "complete"}:
        if phase:
            return f"Ready — {phase} on {repo_name}", False
        return f"Ready — {repo_name}", False
    if status == "failed":
        if phase:
            return f"Blocked — {phase} on {repo_name}", False
        return f"Blocked on {repo_name}", False
    if status == "declined":
        if phase:
            return f"Paused — {phase} on {repo_name}", False
        return f"Paused — {repo_name}", False
    _TERMINAL_SUCCESS_PHASES = {"runtime verified", "repair verified"}
    if phase:
        lowered = phase.casefold()
        if lowered in _TERMINAL_SUCCESS_PHASES and status not in {"verified", "complete"}:
            return f"Working on {repo_name}", status == "active"
        verb = _phase_to_action_verb(lowered)
        spinner = not any(token in lowered for token in ("waiting", "fail", "paused", "escalat"))
        return f"{verb} {phase} for {repo_name}", spinner
    return f"Working on {repo_name}", status == "active"


def _workflow_objective_identity(workflow) -> str | None:
    if workflow is None or not getattr(workflow, "active_objective_kind", None):
        return None
    explicit_id = str(getattr(workflow, "active_objective_id", "") or "").strip()
    if explicit_id:
        return explicit_id
    repo_key = str(getattr(workflow, "active_objective_repo_key", "") or "").strip()
    repo_name = str(getattr(workflow, "active_objective_repo_name", "") or "").strip()
    kind = str(getattr(workflow, "active_objective_kind", "") or "").strip()
    if repo_key:
        return f"{kind}:{repo_key}"
    if repo_name:
        return f"{kind}:{repo_name.casefold()}"
    return kind or None


def _active_objective_is_stale_for_session(workflow, config_dir: Path) -> bool:
    """An objective is stale when it was last updated before the current session began."""

    if workflow is None:
        return True
    updated_raw = str(getattr(workflow, "active_objective_updated_at", "") or "").strip()
    if not updated_raw:
        return False
    snapshot = read_config_snapshot(config_dir)
    started_raw = str(snapshot.get("session.started_at") or "").strip()
    if not started_raw:
        return False
    updated_at = _parse_timestamp(updated_raw)
    started_at = _parse_timestamp(started_raw)
    if updated_at is None or started_at is None:
        return False
    return updated_at < started_at


def _sync_chat_objective_status(*, chat: object | None, config_dir: Path) -> None:
    if not _chat_supports_objective_status(chat):
        return
    workflow = _AGENT_CONTEXT_SERVICE.load_workflow_state(config_dir=config_dir)
    if _active_objective_is_stale_for_session(workflow, config_dir):
        try:
            chat.clear_objective_status()
        except Exception:
            pass
        return
    rendered = _render_chat_objective_status(workflow)
    derived_activity = _render_chat_objective_activity(workflow)
    objective_key = _workflow_objective_identity(workflow)
    try:
        if rendered:
            if derived_activity is None:
                chat.update_objective_status(rendered, objective_key=objective_key)
            else:
                activity_message, activity_spinner = derived_activity
                chat.update_objective_status(
                    rendered,
                    activity_message=activity_message,
                    activity_spinner=activity_spinner,
                    objective_key=objective_key,
                )
        else:
            chat.clear_objective_status()
    except Exception:
        return


def _resolve_active_chat_objective_key(config_dir: Path) -> str | None:
    workflow = _AGENT_CONTEXT_SERVICE.load_workflow_state(config_dir=config_dir)
    return _workflow_objective_identity(workflow)


@contextmanager
def _chat_activity_scope(chat: object | None, message: str, *, spinner: bool = True):
    _set_chat_activity(chat, message, spinner=spinner)
    try:
        yield
    finally:
        _clear_chat_activity(chat)


def _activity_message_for_slash_command(command: str) -> str:
    normalized = _normalize_runtime_slash_command(command).strip().lower()
    if normalized == "/":
        return "Opening the command palette"
    # Plan 69 Fix 5: brand Plan Mode commands with the note glyph instead of a
    # generic "Processing …" label (which falsely implied a long-running task).
    if normalized == "/plan" or normalized.startswith("/plan "):
        try:
            from duckln.glyphs import PLAN_NOTE
        except Exception:
            PLAN_NOTE = ""
        prefix = f"{PLAN_NOTE} Plan Mode" if PLAN_NOTE else "Plan Mode"
        if normalized.startswith("/plan edit") or normalized == "/plan reload":
            return f"{prefix} — opening the plan to edit"
        if normalized == "/plan approve":
            return f"{prefix} — running the approved plan"
        if normalized.startswith("/plan"):
            return prefix
        return prefix
    if normalized == "/vm":
        return "Preparing the VM workflow"
    if normalized == "/cloud":
        return "Preparing the cloud workflow"
    if normalized == "/repos":
        return "Opening the repo catalog"
    if normalized == "/repos refresh":
        return "Refreshing the repo catalog"
    if normalized == "/provider":
        return "Updating the provider"
    if normalized == "/model":
        return "Updating the model"
    if normalized == "/mode":
        return "Updating the control mode"
    if normalized == "/config":
        return "Opening configuration"
    if normalized == "/memory clear":
        return "Preparing memory cleanup"
    if normalized == "/healthcheck":
        return "Running healthcheck"
    return f"Processing {command.strip()}"


def _sync_chat_activity_from_output(*, chat: object | None, message: str) -> None:
    if not _chat_supports_activity(chat):
        return
    raw_plain = _ANSI_ESCAPE_PATTERN.sub("", str(message or "")).strip()
    if not raw_plain:
        return
    lines = [line.strip() for line in raw_plain.splitlines() if line.strip()]
    if not lines:
        return
    first_line = lines[0]
    lowered = raw_plain.casefold()
    first_lowered = first_line.casefold()
    if first_line.startswith("Duckln trace: "):
        title = first_line.removeprefix("Duckln trace: ").rstrip(".").strip()
        spinner = not any(token in title.casefold() for token in ("result", "failed", "ready", "verified", "complete"))
        verb = _phase_to_action_verb(title.casefold())
        _set_chat_activity(chat, f"{verb} — {title}", spinner=spinner)
        return
    if "checked current official docs" in first_lowered:
        _set_chat_activity(chat, "Investigating — checking official docs", spinner=True)
        return
    if first_lowered.startswith("supervisor agent ") and any(
        token in first_lowered for token in ("starting ", "verifying ", "opening ", "creating ", "installing ")
    ):
        _set_chat_activity(chat, first_line, spinner=True)
        return
    if "connection ready" in lowered or "updated provider to " in lowered or first_line.startswith("[PASS]") or first_line.startswith("[FAIL]"):
        _clear_chat_activity(chat)


def _build_chat_select_prompt(
    *,
    chat,
    chat_input: Callable[[str], str],
    display: Callable[[str], None],
    objective_key_resolver: Callable[[], str | None] | None = None,
) -> Callable[[str, tuple[str, ...]], str | None]:
    """Render interactive arrow-key menus for slash-command flows."""

    def prompt(message: str, choices: tuple[str, ...]) -> str | None:
        if hasattr(chat, "select_choice") and getattr(chat, "supports_live", False):
            objective_key = objective_key_resolver() if objective_key_resolver is not None else None
            try:
                selected = chat.select_choice(message, choices, objective_key=objective_key)
            except TypeError:
                selected = chat.select_choice(message, choices)
            if selected is not None:
                return selected
        selector = duckln_search_select if "repository" in message.casefold() or len(choices) > 12 else duckln_select
        chat.prepare_selection_overlay()
        try:
            return selector(message, choices)
        finally:
            chat.restore_after_selection()

    return prompt


def _build_chat_text_prompt(
    *,
    chat,
    chat_input: Callable[[str], str],
    display: Callable[[str], None],
    objective_key_resolver: Callable[[], str | None] | None = None,
) -> Callable[[str, str], str | None]:
    """Collect free-form text through the bottom chat composer."""

    def prompt(message: str, default: str = "") -> str | None:
        if hasattr(chat, "prompt_text") and getattr(chat, "supports_live", False):
            objective_key = objective_key_resolver() if objective_key_resolver is not None else None
            try:
                response = chat.prompt_text(
                    message,
                    default=default,
                    objective_key=objective_key,
                )
            except TypeError:
                response = chat.prompt_text(
                    message,
                    default=default,
                )
            if response is None:
                return default or None
            response = response.strip()
            if not response:
                return default or None
            return response
        if default:
            display(f"{message}\nPress Enter to keep: {default}")
        else:
            display(message)
        response = chat.prompt("", input_func=chat_input).strip()
        if not response:
            return default or None
        return response

    return prompt


def _build_chat_secret_prompt(
    *,
    chat,
    chat_input: Callable[[str], str],
    display: Callable[[str], None],
) -> Callable[[str], str | None]:
    """Collect a secret without echoing it back into the conversation transcript."""

    def prompt(message: str) -> str | None:
        # Plan 121: prefer a MASKED pop-out so the API key is hidden and never
        # echoed into the chat. Falls back to the non-echo line prompt when the
        # live TUI overlay isn't available (plain terminal / fallback chat).
        prompt_secret = getattr(chat, "prompt_secret", None)
        if callable(prompt_secret) and getattr(chat, "supports_live", False):
            try:
                secret = prompt_secret(message)
            except Exception:
                secret = None
            if secret is not None:
                secret = secret.strip()
                return secret or None
            # secret is None: overlay unavailable mid-call — fall through to line prompt.
        display(message)
        response = chat.prompt("", input_func=chat_input, record_input=False).strip()
        if not response:
            return None
        return response

    return prompt


def _build_chat_approve_prompt(
    *,
    chat,
    chat_input: Callable[[str], str],
    display: Callable[[str], None],
    objective_key_resolver: Callable[[], str | None] | None = None,
) -> Callable[[str], bool]:
    """Collect yes/no approval through the shared in-chat selector."""

    class ChatApprovalController:
        def __call__(self, message: str) -> bool:
            display(f"Approval requested: {message}")
            if hasattr(chat, "confirm_choice") and getattr(chat, "supports_live", False):
                objective_key = objective_key_resolver() if objective_key_resolver is not None else None
                try:
                    return bool(chat.confirm_choice(message, default=True, objective_key=objective_key))
                except TypeError:
                    return bool(chat.confirm_choice(message, default=True))
            chat.prepare_selection_overlay()
            try:
                return duckln_confirm(message, default=True)
            finally:
                chat.restore_after_selection()

        def approve_dependency_install(self, request: DependencyApprovalRequest) -> DependencyApprovalDecision:
            if hasattr(chat, "approve_dependency_plan") and getattr(chat, "supports_live", False):
                objective_key = objective_key_resolver() if objective_key_resolver is not None else None
                try:
                    return chat.approve_dependency_plan(request, objective_key=objective_key)
                except TypeError:
                    return chat.approve_dependency_plan(request)
            display("\n".join(render_dependency_approval_lines(request)))
            approved = self(
                "\n".join(
                    (
                        request.prompt,
                        f"Command: {request.command}",
                        "Duckln requires explicit approval before dependency installs in every mode.",
                    )
                )
            )
            return DependencyApprovalDecision(
                approved=approved,
                approve_all=approved,
                selected_item_ids=tuple(item.item_id for item in request.items) if approved else (),
            )

    return ChatApprovalController()


def _clear_visible_transcript(*, chat: object | None, display_output: Callable[[str], None]) -> None:
    clear_method = getattr(chat, "clear_transcript", None) if chat is not None else None
    if callable(clear_method):
        clear_method()
        display_output("Cleared.")
        return
    print("\033[2J\033[H", end="")
    display_output("Cleared.")


def main(
    *,
    argv: list[str] | None = None,
    input_func: Callable[[str], str] | None = None,
    display: Callable[[str], None] | None = None,
    client: Any | None = None,
) -> int:
    global _startup_messages_shown
    _startup_messages_shown = False
    args = [] if argv is None else argv
    terminal_input = input_func or build_terminal_input()
    chat_input = input_func or build_chat_input()
    display_output = display or build_terminal_display()
    paths = resolve_config_paths()
    if args[:1] == ["uninstall"]:
        current = load_app_config(paths)
        mode = current.mode if current is not None else ControlMode.HITL
        try:
            run_uninstall_flow(paths, mode=mode, display=display_output)
        except SessionExitRequested:
            display_output("Exiting Duckln.")
        return 0

    initialize_runtime_storage(paths)
    session_probe = probe_system()
    record_system_probe(paths.config_dir, session_probe)
    current = _ensure_active_config(paths, display=display_output, client=client)
    if current is None:
        return 1
    reset_usage_snapshot(config_dir=paths.config_dir)

    session_header = render_session_header(
        provider=current.provider.label,
        model=current.model,
        mode=current.mode.label,
        user_name=current.user_name,
        memory_state=_session_memory_state(paths.config_dir),
        target=_footer_execution_label(paths.config_dir),
        width=shutil.get_terminal_size((80, 20)).columns,
    )
    chat = None
    if display is None:
        try:
            # Plan 69: derive the header connection label the SAME way as the
            # footer (`_footer_execution_label` → `_terminal_connection_context`)
            # so they never disagree. That helper returns "local" for a VM
            # target (Duckln attaches the terminal locally and runs via
            # `multipass exec`) and only reports docker/aws/gcp when a runtime
            # is actively attached — so a stale `active_runtime_execution_target`
            # from a prior session no longer shows "Ubuntu VM" on a fresh start.
            initial_connection_type = (
                _terminal_connection_context(paths.config_dir).connection_type or "local"
            ).strip().lower() or "local"
        except Exception:
            initial_connection_type = "local"
        # Plan 63 Fix 4c: ensure the bundled Symbols Nerd Font is installed
        # in the OS font directory so the connection-status dots, web globe,
        # and `+` dropdown icons render via system font fallback. Idempotent.
        try:
            from duckln.font_setup import ensure_nerd_font_installed
            ensure_nerd_font_installed()
        except Exception:
            pass
        chat = build_chat_interface(
            session_header=session_header,
            user_name=current.user_name,
            initial_connection_type=initial_connection_type,
            config_dir=paths.config_dir,
            ui_mode=getattr(current, "duckln_ui", "auto"),
        )
        if hasattr(chat, "set_footer_text"):
            chat.set_footer_text(_render_runtime_footer(paths.config_dir))
        chat.start()
        _sync_chat_execution_context(chat=chat, config_dir=paths.config_dir)
        _reassert_cloud_terminal_target_from_state(paths=paths, terminal_interface=chat)
        # Plan 63 Fix 4c: surface a one-time VS Code font-setup hint when the
        # user hasn't acknowledged it yet. Acknowledgement is persisted on
        # AppConfig.font_setup_acknowledged so we never nag twice.
        try:
            from duckln.font_setup import is_vscode_terminal, vscode_settings_guidance
            if (
                is_vscode_terminal()
                and not getattr(current, "font_setup_acknowledged", False)
            ):
                chat.display(vscode_settings_guidance())
                try:
                    from dataclasses import replace as _dc_replace
                    current = _dc_replace(current, font_setup_acknowledged=True)
                    save_app_config(current, paths)
                except Exception:
                    pass
        except Exception:
            pass
        raw_chat_display = chat.display

        def _chat_display_with_activity(message: str) -> None:
            _sync_chat_activity_from_output(chat=chat, message=message)
            raw_chat_display(message)

        display_output = _chat_display_with_activity
        objective_key_resolver = lambda: _resolve_active_chat_objective_key(paths.config_dir)
        input_prompt = lambda message: chat.prompt(message, input_func=chat_input)
        select_prompt = _build_chat_select_prompt(
            chat=chat,
            chat_input=chat_input,
            display=display_output,
            objective_key_resolver=objective_key_resolver,
        )
        text_prompt = _build_chat_text_prompt(
            chat=chat,
            chat_input=chat_input,
            display=display_output,
            objective_key_resolver=objective_key_resolver,
        )
        secret_prompt = _build_chat_secret_prompt(chat=chat, chat_input=chat_input, display=display_output)
        approve_prompt = _build_chat_approve_prompt(
            chat=chat,
            chat_input=chat_input,
            display=display_output,
            objective_key_resolver=objective_key_resolver,
        )
    else:
        input_prompt = terminal_input
        display_output(session_header)
        select_prompt = None
        text_prompt = None
        secret_prompt = None
        approve_prompt = None

    _pending_initial_command: str | None = None
    if args[:1] == ["explore"]:
        _pending_initial_command = "/explore"
        if chat is not None:
            try:
                chat._input_queue.put("/explore")
            except Exception:
                pass

    show_startup_messages(display_fn=display_output, current=current, paths=paths)
    enforce_managed_resource_idle_policies(
        config_dir=paths.config_dir,
        runner=ControlledCommandRunner(trace=lambda _line: None, execution_target="local"),
        display=lambda _line: None,
    )
    _refresh_chat_footer(chat=chat, config_dir=paths.config_dir)
    recent_free_text_turns: list[ConversationTurn] = []
    recent_free_text_replies: list[FreeTextReply] = []
    while True:
        enforce_managed_resource_idle_policies(
            config_dir=paths.config_dir,
            runner=ControlledCommandRunner(trace=lambda _line: None, execution_target="local"),
            display=lambda _line: None,
        )
        _drain_terminal_runtime_incidents(
            current=current,
            paths=paths,
            display_output=display_output,
            approve_prompt=approve_prompt,
            terminal_interface=chat,
        )
        if _pending_initial_command is not None and chat is None:
            raw_input = _pending_initial_command
            _pending_initial_command = None
        else:
            _pending_initial_command = None
            try:
                raw_input = input_prompt("duckln> ")
            except EOFError:
                display_output("Exiting Duckln.")
                _write_session_summary_on_exit(paths=paths, current=current)
                if chat is not None:
                    chat.stop()
                return 0
            except KeyboardInterrupt:
                display_output("Exiting Duckln.")
                _write_session_summary_on_exit(paths=paths, current=current)
                if chat is not None:
                    chat.stop()
                return 0

        command = raw_input.strip()
        if not command:
            continue
        if command.lower() in {"exit", "quit", "/exit"}:
            display_output("Exiting Duckln.")
            _write_session_summary_on_exit(paths=paths, current=current)
            if chat is not None:
                chat.stop()
            return 0
        if not command.startswith("/") and command.casefold() in {"clear", "cls"}:
            _clear_visible_transcript(chat=chat, display_output=display_output)
            _refresh_chat_footer(chat=chat, config_dir=paths.config_dir)
            continue
        if not command.startswith("/") and _looks_like_global_stop_command(command):
            _handle_global_stop_command(
                paths=paths,
                display_output=display_output,
                terminal_interface=chat,
            )
            _refresh_chat_footer(chat=chat, config_dir=paths.config_dir)
            continue
        if not command.startswith("/") and _handle_global_browser_link_intent(
            command=command,
            display_output=display_output,
            terminal_interface=chat,
        ):
            _refresh_chat_footer(chat=chat, config_dir=paths.config_dir)
            continue
        if not command.startswith("/"):
            with _chat_activity_scope(chat, "Thinking... checking the active workflow", spinner=True):
                if _handle_pending_runtime_repair_followup(
                    command=command,
                    current=current,
                    paths=paths,
                    display_output=display_output,
                    approve_prompt=approve_prompt,
                    terminal_interface=chat,
                ):
                    _refresh_chat_footer(chat=chat, config_dir=paths.config_dir)
                    continue
                # Plan 174 F6: SAFETY FLOOR — refuse a clearly inappropriate/harmful request up
                # front with a clean dry-British decline; never answer it, never route it anywhere.
                from duckln.conversation_routes.safety import is_inappropriate_request, clean_british_decline
                if is_inappropriate_request(command):
                    display_output(clean_british_decline(len(command)))
                    _refresh_chat_footer(chat=chat, config_dir=paths.config_dir)
                    continue
                # Plan 183 F11: "complete it without asking / do it all" → a consented, scoped
                # switch to auto (HOOTLWO), never a silent HITL violation, never a refusal.
                _elev = _maybe_offer_mode_elevation(
                    command, current=current, paths=paths,
                    display_output=display_output, approve_prompt=approve_prompt,
                )
                if _elev is not None:
                    current = _elev
                    _refresh_chat_footer(chat=chat, config_dir=paths.config_dir)
                    continue
                # Plan 184 F10b: a pasted shell command (e.g. `git clone …`, `npm install`) →
                # reconfirm once → run it as written. Checked BEFORE the repo-URL offer so a real
                # `git clone <url>` runs rather than triggering managed setup.
                if _maybe_run_pasted_command(
                    command, current=current, paths=paths, display_output=display_output,
                    approve_prompt=approve_prompt, chat=chat,
                ):
                    _refresh_chat_footer(chat=chat, config_dir=paths.config_dir)
                    continue
                # Plan 184 F10: a pasted GitHub repo LINK → "set up this repo?" → the existing
                # target picker + bring-up (a URL inside a question is left to the conversation agent).
                with _chat_activity_scope(chat, "Looking at that repo link", spinner=True):
                    _repo_url_setup = _maybe_offer_repo_url_setup(
                        command, current=current, paths=paths, select_prompt=select_prompt,
                        text_prompt=text_prompt, approve_prompt=approve_prompt,
                        display_output=display_output, system_probe=session_probe,
                        client=client, terminal_interface=chat,
                    )
                if _repo_url_setup is not None:
                    current = _repo_url_setup
                    _refresh_chat_footer(chat=chat, config_dir=paths.config_dir)
                    continue
                # Plan 185: capability/resource/"why" queries ("how much RAM does this need / can I
                # run it locally / which target / why a VM") are NOT answered by a hardcoded handler
                # anymore — they flow to the LLM conversation below, which is now GROUNDED in the
                # deterministic machine + repo facts (context_assembler) so the model explains the
                # 'why' in its own words. (Plan 184's deterministic-sentence handler was removed.)
                if _handle_pending_repo_followup(
                    command=command,
                    current=current,
                    paths=paths,
                    system_probe=session_probe,
                    display_output=display_output,
                    approve_prompt=approve_prompt,
                    text_prompt=text_prompt,
                    select_prompt=select_prompt,
                    terminal_interface=chat,
                ):
                    _refresh_chat_footer(chat=chat, config_dir=paths.config_dir)
                    continue
                if _handle_unified_delete_free_text(
                    message=command,
                    paths=paths,
                    display_output=display_output,
                    approve_prompt=approve_prompt,
                ):
                    _refresh_chat_footer(chat=chat, config_dir=paths.config_dir)
                    continue
                if _handle_vm_management_free_text(
                    message=command,
                    current=current,
                    paths=paths,
                    display_output=display_output,
                    select_prompt=select_prompt,
                    approve_prompt=approve_prompt,
                    terminal_interface=chat,
                ):
                    _refresh_chat_footer(chat=chat, config_dir=paths.config_dir)
                    continue
                # Plan 102: auto-route a free-form repo question/action to the repo agent
                # (only when an active repo + a provider exist; `/ask`//`/do` still work).
                if _maybe_autoroute_repo_agent(
                    command=command, current=current, paths=paths,
                    display=display_output, approve=approve_prompt, terminal_interface=chat,
                    recent_turns=tuple(recent_free_text_turns),
                ):
                    _refresh_chat_footer(chat=chat, config_dir=paths.config_dir)
                    continue
            # Plan 119: LLM connection is mandatory for conversation. Gate at the
            # live REPL on the truthful connectivity signal — a connected provider
            # is never blocked; a real disconnect stops here with a helpful message
            # instead of silently routing into a dead provider. (Gated here, not in
            # _respond_to_free_text, so unit tests of the supervisor still run.)
            from duckln.connection_status import ensure_provider_connected as _ensure_connected
            _readiness = _ensure_connected(current)
            if not _readiness.connected:
                display_output(_readiness.message)
                _refresh_chat_footer(chat=chat, config_dir=paths.config_dir)
                continue
            _qa_started_at = time.monotonic()
            with _chat_activity_scope(chat, "Thinking... routing your request", spinner=True):
                reply = _respond_to_free_text(
                    command,
                    current=current,
                    config_dir=paths.config_dir,
                    system_probe=session_probe,
                    recent_turns=tuple(recent_free_text_turns),
                    recent_replies=tuple(recent_free_text_replies),
                    client=client,
                )
            # Plan 80 Fix 8: show "Thought for Ns" before the answer (Claude/Codex
            # style), so a user question reads: their message → Thought for Ns → answer.
            _qa_elapsed = time.monotonic() - _qa_started_at
            if _qa_elapsed >= 1.0:
                from duckln.ui import format_thought_for_seconds as _fmt_thought
                display_output(_fmt_thought(_qa_elapsed))
            display_output(reply.text)
            _refresh_chat_footer(chat=chat, config_dir=paths.config_dir)
            if reply.action == "update_user_alias" and reply.user_alias:
                current = _handle_user_alias_update(
                    current=current,
                    paths=paths,
                    alias=reply.user_alias,
                    chat=chat,
                )
            if reply.action == "run_repo":
                with _chat_activity_scope(chat, "Working... continuing the repo run path", spinner=True):
                    _handle_repo_run_action(
                        reply=reply,
                        current=current,
                        paths=paths,
                        display_output=display_output,
                        approve_prompt=approve_prompt,
                        text_prompt=text_prompt,
                        terminal_interface=chat,
                    )
            if reply.action == "restart_repo":
                with _chat_activity_scope(chat, "Working... restarting the repo", spinner=True):
                    _handle_repo_restart_action(
                        reply=reply,
                        current=current,
                        paths=paths,
                        display_output=display_output,
                        approve_prompt=approve_prompt,
                        text_prompt=text_prompt,
                        terminal_interface=chat,
                    )
            if reply.action == "verify_repo":
                with _chat_activity_scope(chat, "Working... verifying the repo", spinner=True):
                    _handle_repo_verify_action(
                        reply=reply,
                        current=current,
                        paths=paths,
                        display_output=display_output,
                        approve_prompt=approve_prompt,
                        text_prompt=text_prompt,
                        terminal_interface=chat,
                    )
            if reply.action == "stop_repo":
                with _chat_activity_scope(chat, "Working... stopping the repo", spinner=True):
                    _handle_repo_stop_action(
                        reply=reply,
                        current=current,
                        paths=paths,
                        display_output=display_output,
                        approve_prompt=approve_prompt,
                        terminal_interface=chat,
                    )
            if reply.action == "show_repo_logs":
                with _chat_activity_scope(chat, "Working... collecting repo logs", spinner=True):
                    _handle_repo_logs_action(
                        reply=reply,
                        paths=paths,
                        display_output=display_output,
                    )
            if reply.action == "attach_repo":
                with _chat_activity_scope(chat, "Working... opening repo access", spinner=True):
                    _handle_repo_access_action(
                        reply=reply,
                        current=current,
                        paths=paths,
                        display_output=display_output,
                        approve_prompt=approve_prompt,
                        terminal_interface=chat,
                    )
            if reply.action == "remove_repo":
                with _chat_activity_scope(chat, "Working... preparing repo removal", spinner=True):
                    _handle_repo_remove_action(
                        reply=reply,
                        current=current,
                        paths=paths,
                        display_output=display_output,
                        approve_prompt=approve_prompt,
                    )
            _refresh_chat_footer(chat=chat, config_dir=paths.config_dir)
            recent_free_text_turns.append(ConversationTurn(role="user", content=command))
            recent_free_text_turns.append(ConversationTurn(role="assistant", content=reply.text))
            recent_free_text_replies.append(reply)
            if len(recent_free_text_turns) > _FREE_TEXT_REPEAT_WINDOW * 2:
                recent_free_text_turns = recent_free_text_turns[-(_FREE_TEXT_REPEAT_WINDOW * 2):]
            if len(recent_free_text_replies) > _FREE_TEXT_REPEAT_WINDOW:
                recent_free_text_replies = recent_free_text_replies[-_FREE_TEXT_REPEAT_WINDOW:]
            continue

        try:
            with _chat_activity_scope(chat, _activity_message_for_slash_command(command), spinner=True):
                current = handle_session_command(
                    command,
                    current,
                    paths,
                    system_probe=session_probe,
                    select=select_prompt,
                    secret_prompt=secret_prompt,
                    text_prompt=text_prompt,
                    approve=approve_prompt,
                    display=display_output,
                    client=client,
                    terminal_interface=chat,
                )
            if chat is not None and current is not None:
                chat.update_identity(
                    session_header=render_session_header(
                        provider=current.provider.label,
                        model=current.model,
                        mode=current.mode.label,
                        user_name=current.user_name,
                        memory_state=_session_memory_state(paths.config_dir),
                        target=_footer_execution_label(paths.config_dir),
                        width=shutil.get_terminal_size((80, 20)).columns,
                    ),
                    user_name=current.user_name,
                )
                _sync_chat_execution_context(chat=chat, config_dir=paths.config_dir)
                _refresh_chat_footer(chat=chat, config_dir=paths.config_dir)
            recent_free_text_turns.clear()
            recent_free_text_replies.clear()
        except SessionExitRequested:
            display_output("Exiting Duckln.")
            if chat is not None:
                chat.stop()
            return 0
        except Exception as exc:
            display_output(f"Retryable error: {exc}")
            continue

    return 0


def _respond_to_free_text(
    message: str,
    *,
    current: AppConfig | None = None,
    config_dir: Path | None = None,
    system_probe=None,
    recent_turns: tuple[ConversationTurn, ...] = (),
    recent_replies: tuple[FreeTextReply, ...] = (),
    client: Any | None = None,
) -> FreeTextReply:
    """Provide a supervised multi-agent reply for plain-language input."""

    return _CONVERSATION_SUPERVISOR.respond(
        message,
        current=current,
        config_dir=config_dir,
        system_probe=system_probe,
        recent_turns=recent_turns,
        recent_replies=recent_replies,
        client=client,
    )


def _handle_user_alias_update(
    *,
    current: AppConfig,
    paths: ConfigPaths,
    alias: str,
    chat,
) -> AppConfig:
    updated = replace(current, user_name=alias)
    save_app_config(updated, paths)
    write_config_snapshot(
        paths.config_dir,
        {
            "user.alias_changed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "session.last_seen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
    )
    materialize_managed_memory_state(paths.config_dir)
    if chat is not None:
        chat.update_identity(
            session_header=render_session_header(
                provider=updated.provider.label,
                model=updated.model,
                mode=updated.mode.label,
                user_name=updated.user_name,
                memory_state=_session_memory_state(paths.config_dir),
                target=_footer_execution_label(paths.config_dir),
                width=shutil.get_terminal_size((80, 20)).columns,
            ),
            user_name=updated.user_name,
        )
        _sync_chat_execution_context(chat=chat, config_dir=paths.config_dir)
    return updated


def _resolve_repo_from_action_key(config_dir: Path, action_repo_key: str | None) -> RepoCatalogRecord | None:
    tracked_rows = initialize_state_store(config_dir).list_repo_states()
    if action_repo_key:
        lowered = action_repo_key.lower()
        for row in tracked_rows:
            row_repo_url = (row.repo_url or "").lower()
            row_repo_key = (row.repo_key or "").lower()
            row_repo_name = str(row.metadata.get("repo_name") or "").strip().lower()
            if lowered in {row_repo_url, row_repo_key, row_repo_name}:
                return _repo_record_from_state_row(row)

    records = list(load_sorted_local_repo_catalog(config_dir))
    records.extend(
        RepoCatalogRecord(
            name=record.repo_name,
            repo_url=record.repo_url,
            stars=record.stars,
            description=record.description,
            category=record.category,
            framework=record.framework,
            last_updated=record.last_updated,
        )
        for record in initialize_state_store(config_dir).list_recent_custom_repos()
    )
    latest = initialize_state_store(config_dir).get_latest_repo_state()
    if action_repo_key:
        lowered = action_repo_key.lower()
        for record in records:
            if record.repo_url.lower() == lowered or record.name.lower() == lowered:
                return record
    if latest is not None:
        latest_key = (latest.repo_url or latest.repo_key or "").lower()
        for record in records:
            if record.repo_url.lower() == latest_key or record.name.lower() == latest_key:
                return record
        repo_name = str(latest.metadata.get("repo_name") or "").strip()
        if repo_name:
            for record in records:
                if record.name.lower() == repo_name.lower():
                    return record
    if latest is not None:
        return _repo_record_from_state_row(latest)
    return None


def _repo_record_from_state_row(row) -> RepoCatalogRecord:
    repo_name = str(row.metadata.get("repo_name") or "").strip()
    repo_path = str(row.repo_path or row.metadata.get("install_location") or "").strip()
    display_name = repo_name or (Path(repo_path).name if repo_path else row.repo_key)
    description = str(row.metadata.get("description") or row.summary or "Tracked by Duckln.").strip()
    category = str(row.metadata.get("category") or "Custom").strip() or "Custom"
    framework = str(row.metadata.get("framework") or "Unknown").strip() or "Unknown"
    last_updated = str(row.updated_at or "")[:10] or datetime.now(timezone.utc).date().isoformat()
    return RepoCatalogRecord(
        name=display_name,
        repo_url=str(row.repo_key or row.repo_url or display_name),
        stars=int(row.metadata.get("stars") or 0),
        description=description,
        category=category,
        framework=framework,
        last_updated=last_updated,
    )


def _session_vm_name(config_dir: Path) -> str | None:
    snapshot = read_config_snapshot(config_dir)
    vm_name = str(snapshot.get("active_vm_name") or snapshot.get("execution_vm_name") or "").strip()
    return vm_name or None


def _terminal_connection_context(config_dir: Path) -> TerminalConnectionContext:
    """Resolve the terminal pane attachment separately from the desired execution target."""

    workflow = _AGENT_CONTEXT_SERVICE.load_workflow_state(config_dir=config_dir)
    snapshot = read_config_snapshot(config_dir)
    if workflow is not None:
        target = str(workflow.active_runtime_execution_target or "").strip().lower()
        vm_name = str(workflow.active_runtime_vm_name or "").strip() or None
        docker_name = str(workflow.active_runtime_docker_name or "").strip() or None
        cloud_vendor = str(workflow.active_runtime_cloud_vendor or target.upper()).strip() or None
        cloud_region = str(workflow.active_runtime_cloud_region or "").strip() or None
        cloud_shape = str(workflow.active_runtime_cloud_shape or "").strip() or None
        cloud_resource_key = str(workflow.active_runtime_cloud_resource_key or "").strip()
        cloud_attach_hint = str(workflow.active_runtime_attach_hint or "").strip()
        command_kind = str(workflow.active_runtime_command_kind or "").strip().lower()
        runtime_status = str(workflow.active_runtime_status or "").strip().lower()
        terminal_attached = command_kind == "attach" or runtime_status == "interactive"
        if target == "vm":
            return TerminalConnectionContext(connection_type="local")
        if target == "docker" and docker_name and terminal_attached:
            return TerminalConnectionContext(connection_type="docker", docker_name=docker_name)
        if target in {"aws", "gcp"} and terminal_attached and (cloud_resource_key or cloud_attach_hint or cloud_region or cloud_shape):
            return TerminalConnectionContext(
                connection_type=target,
                cloud_vendor=cloud_vendor,
                cloud_region=cloud_region,
                cloud_shape=cloud_shape,
            )

    return TerminalConnectionContext(connection_type="local")


def _footer_execution_label(config_dir: Path) -> str:
    """Target-only label for the footer/header, based on the terminal pane attachment."""

    target = _terminal_connection_context(config_dir).connection_type
    return _execution_target_label(target)


def _execution_target_label(target: str) -> str:
    if target == "vm":
        return "Ubuntu VM"
    if target == "docker":
        return "Docker"
    if target == "aws":
        return "AWS Cloud"
    if target == "gcp":
        return "GCP Cloud"
    return "Local"


def _render_runtime_footer(config_dir: Path) -> str:
    usage = current_usage_snapshot()
    usage_summary = format_usage_footer(
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
        estimated_cost_usd=usage.estimated_cost_usd,
    )
    return render_input_footer(
        execution_label=None,
        usage_summary=usage_summary,
    )


def _refresh_chat_footer(*, chat: object | None, config_dir: Path) -> None:
    if chat is None or not hasattr(chat, "set_footer_text"):
        return
    chat.set_footer_text(_render_runtime_footer(config_dir))


_ORCHESTRATE_MAX_REPAIR_ATTEMPTS = 2


def _lookup_deploy_skill(config_dir: Path, *, repo_name: str, execution_target: str) -> str | None:
    """Item: read a previously-written deploy skill so the orchestrator biases toward
    a verified path (Anthropic guide: skills/memory). Returns the recorded summary or
    None when no skill matches.
    """

    try:
        from agent.memory import SKILLS_DIR_NAME, resolve_agent_memory_paths
    except Exception:
        return None
    try:
        skills_dir = resolve_agent_memory_paths(config_dir).skills_dir
        for candidate in (
            skills_dir / f"deploy-{repo_name}-{execution_target}.md",
            skills_dir / f"deploy-{repo_name.lower()}-{execution_target}.md",
        ):
            if candidate.exists():
                content = candidate.read_text(encoding="utf-8", errors="ignore")
                return content[:600]
    except Exception:
        return None
    return None


def _run_parallel_prep_checks(
    *,
    execution_target: str,
    vm_name: str | None,
    display: Callable[[str], None],
    timeout_seconds: float = 12.0,
) -> dict[str, str]:
    """Item 3: fan-out prep checks before clone+install. Concurrent, bounded, best-effort.

    Returns a dict of check-name → outcome line. We don't fail the deploy on a check error;
    we surface the issue and let the regular bring-up path handle real blockers.
    """

    import concurrent.futures
    import shutil as _shutil

    def _disk_check() -> str:
        try:
            usage = _shutil.disk_usage(str(Path.home()))
            free_gib = usage.free / (1024 ** 3)
            if free_gib < 5.0:
                return f"low host disk space: only {free_gib:.1f} GiB free (recommended: ≥ 5 GiB)"
            return f"host disk free: {free_gib:.1f} GiB"
        except Exception as exc:
            return f"disk check skipped ({exc})"

    def _vm_transport_check() -> str:
        if execution_target != "vm" or not vm_name:
            return "vm transport check: not applicable"
        try:
            runner = ControlledCommandRunner(execution_target="local")
            probe = runner.run(
                f"multipass exec {shlex.quote(vm_name)} -- echo duckln-vm-ready",
                timeout_seconds=8.0,
            )
            if probe.exit_code == 0 and not probe.timed_out:
                return f"vm transport ready: {vm_name}"
            return f"vm transport probe failed (rc={probe.exit_code})"
        except Exception as exc:
            return f"vm transport check skipped ({exc})"

    checks: dict[str, Callable[[], str]] = {
        "disk_space": _disk_check,
        "vm_transport": _vm_transport_check,
    }
    outcomes: dict[str, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(checks)) as pool:
        futures = {name: pool.submit(fn) for name, fn in checks.items()}
        for name, future in futures.items():
            try:
                outcomes[name] = future.result(timeout=timeout_seconds)
            except concurrent.futures.TimeoutError:
                outcomes[name] = f"{name} timed out"
            except Exception as exc:
                outcomes[name] = f"{name} errored: {exc}"
    for name, line in outcomes.items():
        if "low host disk space" in line or "failed" in line:
            display(f"  ⚠ {line}")
    return outcomes


def _offer_skill_approval(
    mode: ControlMode,
    *,
    slug: str,
    title: str,
    summary: str,
    config_dir: "Path",
    approve: "Callable[[str], bool] | None",
    display: "Callable[[str], None]",
) -> bool:
    """Persist a repair skill with mode-appropriate approval.

    HITL/HOTL: show the skill note, prompt yes/no, persist only on yes.
    HOOTLWO: auto-persist and print a one-line confirmation.
    Returns True if the skill was saved.
    """
    if mode is ControlMode.HOOTLWO:
        try:
            write_skill_memory_state(config_dir, slug=slug, title=title, summary=summary)
            display(f"Skill saved: {title}")
            return True
        except Exception:
            return False
    # HITL and HOTL: require explicit user approval
    display(f"\nDuckln learned a repair skill from this session:\n\n# {title}\n\n{summary}\n")
    if approve is None:
        return False
    confirmed = approve("Save this repair skill for future sessions? [yes/no]")
    if confirmed:
        try:
            write_skill_memory_state(config_dir, slug=slug, title=title, summary=summary)
            display(f"Skill saved: {title}")
            return True
        except Exception:
            return False
    return False


def _orchestrate_repo_to_running(
    *,
    repo: RepoCatalogRecord,
    current: AppConfig,
    paths: ConfigPaths,
    approve: Callable[[str], bool] | None,
    display: Callable[[str], None],
    chat: object | None,
    runtime_provider: str,
    execution_target: str,
    vm_name: str | None,
    system_probe: Any | None,
    terminal_interface: object | None,
):
    """Drive a repo from selection to a running state through Duckln's specialist agents.

    Pipeline (each phase shows up as an inline step in the chat):
      1. bring-up: clone + install + verify (via `bring_up_selected_repo`)
      2. run:      start the prepared runtime (via `run_prepared_repo`)
      3. repair:   on a runtime failure, hand off to `_run_runtime_repair_workflow`,
                   then retry step 2. Bounded to `_ORCHESTRATE_MAX_REPAIR_ATTEMPTS`.

    The user sees the same progression even when the underlying calls go through different
    specialists — Duckln does NOT dead-end. Returns the final RepoBringUpResult so callers
    can persist follow-up state.
    """

    # Plan 182 F5: a repo needs a provider + model selected. If none is configured, STOP with a
    # clear "select a provider and model" message + the exact slash hints — never start against a
    # repo with nothing selected. A cheap config-presence check (no network probe in the hot path);
    # a configured-but-unreachable model is caught downstream by the bring-up's honest-stop.
    if current is None or not getattr(current, "provider", None) or not getattr(current, "model", None):
        display(
            "No model is selected. Pick a provider and model first — run `/provider` to choose a "
            "provider and enter a key, then `/model` to pick the model."
        )
        return None

    # Plan 68 Fix 2a: when Plan Mode is on, generate a reviewable plan and stop
    # BEFORE any clone/install/run work. The user reviews via `/plan show` and
    # runs `/plan approve` to begin strict execution. This must short-circuit
    # the whole orchestration, not just the bring-up call.
    if getattr(current, "plan_mode_enabled", False):
        if _chat_supports_thoughts(chat):
            try:
                chat.clear_thoughts()
            except Exception:
                pass
        try:
            return bring_up_selected_repo(
                repo,
                current.mode,
                paths,
                approve=approve,
                display=display,
                runtime_provider=runtime_provider,
                execution_target=execution_target,
                vm_name=vm_name,
                system_probe=system_probe,
                pane_executor=terminal_interface,
                plan_mode_enabled=True,
                emit_thought=lambda t: _emit_thought(chat, t, display=display),
            )
        finally:
            # Plan 78 Fix F: a plan-mode draft always ends in a terminal state
            # (plan shown / blocked / model unreachable / internal bug) — never
            # leave the activity bar spinning afterward.
            _clear_chat_activity(chat)

    def _emit_step(step_id: str, label: str, *, status: str = "running",
                   duration: float | None = None, detail: str | None = None) -> None:
        if chat is not None and hasattr(chat, "display_step"):
            try:
                chat.display_step(step_id, label, status=status, duration_seconds=duration, detail=detail)
            except Exception:
                pass

    def _narrate_in_pane(message: str) -> None:
        """Item A: surface what Duckln is about to do as a comment line in the embedded
        terminal pane, so the user sees Duckln's activity in the same shell session
        they're using to interact with the VM. Falls through silently when no pane.
        """

        if chat is None or not hasattr(chat, "run_terminal_command"):
            return
        try:
            chat.run_terminal_command(command=f"# Duckln: {message}")
        except Exception:
            pass

    started_at = time.monotonic()

    deploy_label = f"Deploy {repo.name} on {execution_target}"
    _emit_step("repo.deploy", deploy_label)
    _narrate_in_pane(f"deploying {repo.name} on {execution_target}")
    _begin_active_deploy_objective(
        paths=paths,
        repo=repo,
        execution_target=execution_target,
        chat=chat,
    )
    # Skill-aware routing (Anthropic guide pattern: persistent memory). When a prior
    # deploy of this repo on this target succeeded, surface that skill so the user
    # knows we have a verified path on file before any work begins.
    prior_skill = _lookup_deploy_skill(
        paths.config_dir, repo_name=repo.name, execution_target=execution_target
    )
    if prior_skill:
        display(f"Found a verified deploy skill for {repo.name} on {execution_target}:\n{prior_skill}")
    # Item: auto-enable internet skill for cloud targets so failure recovery + agent
    # research has network access by default. We surface the flip to the user, never
    # silently. Local/VM targets keep their explicit /internet on/off.
    try:
        from duckln.internet_skill import ensure_internet_for_cloud_target

        ensure_internet_for_cloud_target(
            paths.config_dir,
            execution_target=execution_target,
            display=display,
        )
    except Exception:
        pass

    # Item 3: run independent prep checks in parallel (Anthropic guide pattern #3:
    # parallelization). Currently we fan-out (a) VM transport probe when target=vm and
    # (b) host disk-space probe. Both must finish before clone+install, but neither
    # depends on the other — running serially wastes seconds on each deploy.
    _run_parallel_prep_checks(
        execution_target=execution_target,
        vm_name=vm_name,
        display=display,
    )

    def _advance_deploy_phase(phase: str) -> None:
        """Item 2: advance `active_repair_phase` so the supervisor sees deploy progress."""

        try:
            _update_active_runtime_objective(paths, chat=chat, active_repair_phase=phase)
        except Exception:
            pass

    deploy_run_id = f"repo_deploy:{repo.repo_url}:{int(time.time())}"
    deploy_started_at_iso = _current_timestamp_iso()

    def _record_phase_event(
        phase: str,
        status: str,
        *,
        summary: str,
        stderr_excerpt: str = "",
        recipe_applied: str | None = None,
        retry_count: int = 0,
    ) -> None:
        """Item 1: persist an orchestrator phase event to run_history so the supervisor
        and post-mortem flows can answer "what went wrong last deploy?" without scrubbing
        the chat. Failure events get a short stderr excerpt; success events stay compact.
        """

        try:
            store = initialize_state_store(paths.config_dir)
            metadata: dict[str, object] = {
                "phase": phase,
                "execution_target": execution_target,
                "vm_name": vm_name or "",
                "retry_count": retry_count,
            }
            if stderr_excerpt:
                metadata["stderr_excerpt"] = stderr_excerpt[:300]
            if recipe_applied:
                metadata["recipe_applied"] = recipe_applied[:200]
            store.record_run(
                run_id=f"{deploy_run_id}:{phase}:{status}",
                command_name=f"repo_deploy.{phase}",
                status=status,
                summary=summary[:280],
                mode=str(current.mode.value) if hasattr(current.mode, "value") else str(current.mode),
                repo_key=repo.repo_url,
                started_at=deploy_started_at_iso,
                finished_at=_current_timestamp_iso(),
                metadata=metadata,
            )
        except Exception:
            pass

    # Item 4: graceful Ctrl+C handling. Wrap each phase so KeyboardInterrupt marks the
    # current step as cancelled, persists state, and returns to the caller cleanly.
    def _handle_cancellation(step_id: str, step_label: str, phase: str) -> None:
        _emit_step(step_id, step_label, status="failed", detail="cancelled by user (Ctrl+C)")
        _emit_step("repo.deploy", deploy_label, status="failed", detail=f"cancelled during {phase}")
        _narrate_in_pane(f"cancelled during {phase} for {repo.name}")
        display(f"Duckln cancelled the {phase} step for {repo.name}. State has been preserved; rerun /repos to resume.")
        _clear_chat_activity(chat)
        _clear_active_deploy_objective(paths=paths, chat=chat)
        try:
            _persist_session_execution_target(
                paths=paths,
                execution_target=execution_target,
                vm_name=vm_name,
            )
        except Exception:
            pass

    # Phase 1: clone + install + verify (NOT_INSTALLED → INSTALLED_HEALTHY)
    _advance_deploy_phase("clone_install")
    setup_label = f"Clone and install {repo.name}"
    _emit_step("repo.setup", setup_label)
    _narrate_in_pane(f"cloning + installing {repo.name}")
    _narrate_in_pane(f"NOT_INSTALLED → cloning + installing {repo.name}")
    setup_started = time.monotonic()
    def _on_repair_success(
        failed_cmd: str, error_out: str, recovery_cmd: str,
        repo_family: str, repo_name: str, framework: str,
    ) -> None:
        result = _generate_repair_skill_note(
            failed_command=failed_cmd,
            error_output=error_out,
            recovery_command=recovery_cmd,
            repo_family=repo_family,
            repo_name=repo_name,
            framework=framework,
            config_dir=paths.config_dir,
        )
        if result is not None:
            skill_slug, skill_title, skill_summary = result
            _offer_skill_approval(
                current.mode,
                slug=skill_slug,
                title=skill_title,
                summary=skill_summary,
                config_dir=paths.config_dir,
                approve=approve,
                display=display,
            )

    try:
        bring_up_result = bring_up_selected_repo(
            repo,
            current.mode,
            paths,
            approve=approve,
            display=display,
            runtime_provider=runtime_provider,
            execution_target=execution_target,
            vm_name=vm_name,
            system_probe=system_probe,
            pane_executor=terminal_interface,
            on_repair_success=_on_repair_success,
        )
    except KeyboardInterrupt:
        _handle_cancellation("repo.setup", setup_label, "clone+install")
        return None
    setup_duration = time.monotonic() - setup_started
    if bring_up_result.verification_passed:
        _emit_step("repo.setup", setup_label, status="done", duration=setup_duration)
        _record_phase_event("clone_install", "success", summary=f"Cloned and installed {repo.name}.")
    else:
        failure_label = (
            bring_up_result.failure_type.value.replace("_", " ")
            if bring_up_result.failure_type is not None
            else "verification failed"
        )
        _emit_step("repo.setup", setup_label, status="failed", duration=setup_duration, detail=failure_label)
        _record_phase_event(
            "clone_install",
            "failed",
            summary=f"clone+install failed for {repo.name}: {failure_label}",
            stderr_excerpt=bring_up_result.message or "",
        )
        # Item 4: surface a curated recipe when one matches the bring-up failure signature.
        setup_recipe = lookup_repo_failure_recipe(bring_up_result.message or "")
        if setup_recipe is None:
            # Item 1+3: no curated recipe — try a live DuckDuckGo search so the agent
            # never dead-ends. Gated by /internet ON. We surface intent either way so
            # the user knows why nothing extra appeared (no silent fail).
            try:
                from duckln.internet_skill import (
                    internet_search_summary,
                    is_internet_enabled,
                    render_search_summary,
                )

                if is_internet_enabled(paths.config_dir):
                    short_query = (bring_up_result.message or "").strip()[:140]
                    if short_query:
                        results = internet_search_summary(
                            f"{repo.name} repo setup error: {short_query}",
                            config_dir=paths.config_dir,
                            max_results=3,
                        )
                        if results:
                            display("Duckln searched the web (DuckDuckGo) for guidance:")
                            display(render_search_summary(results))
                        else:
                            from duckln.web_runtime import describe_search_failure
                            display(
                                "Duckln did not find web guidance for this failure. "
                                f"Diagnostic: {describe_search_failure()}"
                            )
                else:
                    display(
                        "No curated recipe matched this failure. Run `/internet on` to let "
                        "Duckln search the official docs for you next time."
                    )
            except Exception:
                pass
        if setup_recipe is not None:
            display(f"Duckln looked this up in official docs:")
            display(f"  • {setup_recipe.description}")
            display(f"  • Reference: {setup_recipe.docs_url}")
            if setup_recipe.fix_command:
                display(f"  • Suggested fix: {setup_recipe.fix_command}")
                # Item 1 (recipe auto-apply): run the curated fix once and re-attempt setup
                # if it succeeds. Bounded to a single auto-attempt so we never loop.
                if setup_recipe.auto_apply:
                    fix_runner = ControlledCommandRunner(
                        trace=display,
                        execution_target=execution_target,
                        vm_name=vm_name,
                        pane_executor=chat,
                    )
                    # Item 2: snapshot the target directory before destructive auto-apply so
                    # we can roll back if the retry doesn't recover. Only checkpoint when the
                    # fix command looks destructive (rm/clean/wipe).
                    checkpoint_path: str | None = None
                    if any(token in setup_recipe.fix_command for token in ("rm -rf", "rm -r ", "rmdir")):
                        checkpoint_dir = paths.config_dir / "checkpoints"
                        try:
                            checkpoint_dir.mkdir(parents=True, exist_ok=True)
                            checkpoint_path = str(checkpoint_dir / f"{repo.name}-{int(time.time())}.tar.gz")
                            project_dir = resolve_managed_project_dir(paths.config_dir, repo)
                            if project_dir.exists():
                                checkpoint_runner = ControlledCommandRunner(trace=display, execution_target="local")
                                checkpoint_cmd = f"tar -czf {shlex.quote(checkpoint_path)} -C {shlex.quote(str(project_dir.parent))} {shlex.quote(project_dir.name)}"
                                ck_result = checkpoint_runner.run(checkpoint_cmd, timeout_seconds=60.0)
                                if ck_result.exit_code == 0 and not ck_result.timed_out:
                                    display(f"Pre-apply checkpoint saved: {checkpoint_path}")
                                else:
                                    checkpoint_path = None  # don't try restore if the snapshot itself failed
                        except Exception:
                            checkpoint_path = None
                    display(f"Auto-applying recipe fix: {setup_recipe.fix_command}")
                    fix_result = fix_runner.run(setup_recipe.fix_command, timeout_seconds=120.0)
                    if fix_result.exit_code == 0 and not fix_result.timed_out:
                        display("Recipe fix applied. Retrying setup once…")
                        retry_started = time.monotonic()
                        try:
                            retry_result = bring_up_selected_repo(
                                repo, current.mode, paths,
                                approve=approve, display=display,
                                runtime_provider=runtime_provider,
                                execution_target=execution_target,
                                vm_name=vm_name,
                                system_probe=system_probe,
                                pane_executor=chat,
                            )
                        except KeyboardInterrupt:
                            _handle_cancellation("repo.setup", setup_label, "clone+install retry")
                            return None
                        retry_duration = time.monotonic() - retry_started
                        if retry_result.verification_passed:
                            _emit_step("repo.setup", setup_label, status="done",
                                       duration=retry_duration, detail="recovered via recipe")
                            bring_up_result = retry_result
                        else:
                            display("Recipe-driven retry still failed; continuing with the existing repair specialist.")
                            # Item 2: roll back to the pre-apply checkpoint so the next repair
                            # specialist sees the original tree, not a half-deleted one.
                            if checkpoint_path:
                                try:
                                    project_dir = resolve_managed_project_dir(paths.config_dir, repo)
                                    restore_runner = ControlledCommandRunner(trace=display, execution_target="local")
                                    restore_cmd = (
                                        f"rm -rf {shlex.quote(str(project_dir))} && "
                                        f"tar -xzf {shlex.quote(checkpoint_path)} -C {shlex.quote(str(project_dir.parent))}"
                                    )
                                    restore_result = restore_runner.run(restore_cmd, timeout_seconds=60.0)
                                    if restore_result.exit_code == 0 and not restore_result.timed_out:
                                        display(f"Restored pre-apply checkpoint: {checkpoint_path}")
                                    else:
                                        display(f"Could not auto-restore checkpoint (manual: tar -xzf {checkpoint_path}).")
                                except Exception:
                                    pass
        # Item 4: when bring-up failed because of a single bad pip/npm package, surface a
        # concrete isolate-and-skip suggestion. We do not auto-skip (that can mask
        # real dep gaps) — we propose the targeted command for the user to apply.
        bad_package = extract_failing_package(bring_up_result.message or "")
        if bad_package is not None:
            manager, pkg = bad_package
            if manager == "pip":
                skip_hint = (
                    f"pip install -r requirements.txt --no-deps "
                    f"--ignore-installed --no-build-isolation --constraint <(grep -v '^{re.escape(pkg)}' requirements.txt)"
                )
            else:
                skip_hint = f"npm install --omit=optional --ignore-scripts (drop dependency '{pkg}' from package.json first)"
            display(
                f"Duckln spotted a single failing {manager} package: '{pkg}'. "
                f"You can isolate it with:\n  {skip_hint}"
            )
        if not bring_up_result.should_offer_repair:
            _emit_step("repo.deploy", deploy_label, status="failed", detail="setup blocked, no auto-repair available")
            display(f"Duckln setup failed for {repo.name}. Duckln stopped the setup flow and is waiting for your next instruction.")
            _clear_chat_activity(chat)
            _clear_active_deploy_objective(paths=paths, chat=chat)
            return bring_up_result
        # Hand off to the repair specialist; it will rerun the runtime when fixed.
        _emit_step("repo.repair", "Repair the setup blocker")
        try:
            workflow = _AGENT_CONTEXT_SERVICE.load_workflow_state(config_dir=paths.config_dir)
            _run_runtime_repair_workflow(
                repo=repo,
                current=current,
                paths=paths,
                display_output=display,
                approve_prompt=approve,
                terminal_interface=terminal_interface,
                run_summary=bring_up_result.message or failure_label,
                workflow=workflow,
            )
            _emit_step("repo.repair", "Repair the setup blocker", status="done")
        except Exception as exc:  # repair workflow failed unexpectedly
            _emit_step("repo.repair", "Repair the setup blocker", status="failed", detail=str(exc)[:80])
            _emit_step("repo.deploy", deploy_label, status="failed", detail="repair workflow raised")
            display(f"Duckln setup failed for {repo.name}. Repair workflow raised an error and Duckln stopped the setup flow.")
            _clear_chat_activity(chat)
            _clear_active_deploy_objective(paths=paths, chat=chat)
            return bring_up_result

    # Phase 2 + 3: run the prepared runtime, with bounded repair retries on failure.
    final_result = bring_up_result
    for attempt in range(_ORCHESTRATE_MAX_REPAIR_ATTEMPTS + 1):
        run_label = f"Run {repo.name}" if attempt == 0 else f"Re-run {repo.name} (attempt {attempt + 1})"
        run_step_id = "repo.run" if attempt == 0 else f"repo.run.retry.{attempt}"
        _advance_deploy_phase("run" if attempt == 0 else f"run_retry_{attempt}")
        _emit_step(run_step_id, run_label)
        _narrate_in_pane(f"running {repo.name} (attempt {attempt + 1})")
        _narrate_in_pane(f"INSTALLED_HEALTHY → running {repo.name} (attempt {attempt + 1})")
        run_started = time.monotonic()
        try:
            run_result = run_prepared_repo(
                repo,
                current.mode,
                paths,
                approve=approve,
                display=display,
                pane_executor=chat,
            )
        except KeyboardInterrupt:
            _handle_cancellation(run_step_id, run_label, f"run attempt {attempt + 1}")
            return final_result
        except Exception as exc:
            _emit_step(run_step_id, run_label, status="failed",
                       duration=time.monotonic() - run_started, detail=str(exc)[:80])
            _emit_step("repo.deploy", deploy_label, status="failed", detail="run raised")
            display(f"Duckln setup/run failed for {repo.name}. Duckln stopped the run flow after an unexpected runtime error.")
            _clear_chat_activity(chat)
            _clear_active_deploy_objective(paths=paths, chat=chat)
            return final_result
        run_duration = time.monotonic() - run_started
        final_result = run_result
        if run_result.verification_passed:
            _emit_step(run_step_id, run_label, status="done", duration=run_duration)
            _emit_step("repo.deploy", deploy_label, status="done")
            _narrate_in_pane(f"{repo.name} is running.")
            _record_phase_event(
                "run",
                "success",
                summary=f"{repo.name} is running.",
                retry_count=attempt,
            )
            _maybe_launch_repo_access(
                repo=repo,
                current=current,
                paths=paths,
                display_output=display,
                approve_prompt=approve,
                terminal_interface=terminal_interface or (chat if chat is not None and hasattr(chat, "run_terminal_command") else None),
            )
            if (
                execution_target == "vm"
                and terminal_interface is None
                and chat is not None
                and hasattr(chat, "run_terminal_command")
            ):
                try:
                    chat.run_terminal_command(command=f"cd ~/.duckln/projects/{shlex.quote(repo.name)}")
                except Exception:
                    pass
            # Item 5: persist a skill entry summarizing what worked, so future deploys
            # for similar repos can reuse the verified path. Writes to ~/.duckln/memory/skills/.
            try:
                skill_summary_lines = [
                    f"Repo: {repo.name} ({repo.framework or 'unknown'})",
                    f"Target: {execution_target}",
                    f"Setup duration: {setup_duration:.1f}s",
                    f"Run attempts before success: {attempt + 1}",
                ]
                if attempt > 0:
                    skill_summary_lines.append("Path included one or more repair retries.")
                cmds = getattr(bring_up_result, "executed_commands", ())
                if cmds:
                    skill_summary_lines.append("## Command sequence")
                    skill_summary_lines.extend(f"  {i + 1}. {c}" for i, c in enumerate(cmds))
                outcome = getattr(bring_up_result, "setup_outcome", None)
                verification_cmd = getattr(outcome, "verification_command", None) if outcome else None
                if verification_cmd:
                    skill_summary_lines.append(f"## Verification\n  {verification_cmd}")
                write_skill_memory_state(
                    paths.config_dir,
                    slug=f"deploy-{repo.name}-{execution_target}",
                    title=f"Verified deploy path: {repo.name} on {execution_target}",
                    summary="\n".join(skill_summary_lines),
                )
            except Exception:
                pass
            display(f"Duckln setup succeeded for {repo.name}. Runtime is active and Duckln stopped the setup flow.")
            # Plan 57 Phase 5: surface usage info from the README so the user knows
            # how to actually use the running repo. Opt-in demo: ask first, then emit a
            # labeled step with the copyable example command if the user wants it.
            _emit_repo_usage_summary_and_offer_demo(
                repo=repo,
                paths=paths,
                display=display,
                approve=approve,
                project_dir=getattr(bring_up_result, "project_dir", None),
            )
            _clear_chat_activity(chat)
            _clear_active_deploy_objective(paths=paths, chat=chat)
            return final_result
        if not getattr(run_result, "should_offer_repair", True):
            _emit_step(
                run_step_id,
                run_label,
                status="failed",
                duration=run_duration,
                detail="awaiting user decision",
            )
            _emit_step("repo.deploy", deploy_label, status="failed", detail="runtime step paused")
            _record_phase_event(
                "run",
                "failed",
                summary=f"Run paused for {repo.name}: awaiting user decision",
                stderr_excerpt=run_result.message or "",
                retry_count=attempt,
            )
            display(f"Duckln paused after setup for {repo.name}. It needs your decision before continuing and has stopped background setup flow.")
            _clear_chat_activity(chat)
            _clear_active_deploy_objective(paths=paths, chat=chat)
            return final_result
        # Run failed — repair and retry up to the bound.
        run_failure_label = (
            run_result.failure_type.value.replace("_", " ")
            if run_result.failure_type is not None
            else "runtime failure"
        )
        _emit_step(run_step_id, run_label, status="failed",
                   duration=run_duration, detail=run_failure_label)
        _record_phase_event(
            "run",
            "failed",
            summary=f"Run attempt {attempt + 1} failed: {run_failure_label}",
            stderr_excerpt=run_result.message or "",
            retry_count=attempt,
        )
        if attempt == _ORCHESTRATE_MAX_REPAIR_ATTEMPTS:
            _emit_step(
                "repo.deploy",
                deploy_label,
                status="failed",
                detail=f"runtime did not stabilize after {_ORCHESTRATE_MAX_REPAIR_ATTEMPTS} repair pass(es)",
            )
            display(
                f"Duckln setup failed for {repo.name}. Runtime did not stabilize after {_ORCHESTRATE_MAX_REPAIR_ATTEMPTS} repair pass(es), and Duckln stopped the setup flow."
            )
            _clear_chat_activity(chat)
            _clear_active_deploy_objective(paths=paths, chat=chat)
            return final_result
        repair_step_id = f"repo.repair.run.{attempt}"
        _advance_deploy_phase(f"repair_attempt_{attempt + 1}")
        _emit_step(repair_step_id, f"Repair runtime issue (attempt {attempt + 1}/{_ORCHESTRATE_MAX_REPAIR_ATTEMPTS})")
        _narrate_in_pane(f"repairing runtime issue (attempt {attempt + 1}/{_ORCHESTRATE_MAX_REPAIR_ATTEMPTS})")
        _narrate_in_pane(f"INSTALLED_BROKEN → repairing runtime issue (attempt {attempt + 1}/{_ORCHESTRATE_MAX_REPAIR_ATTEMPTS})")
        try:
            workflow = _AGENT_CONTEXT_SERVICE.load_workflow_state(config_dir=paths.config_dir)
            _run_runtime_repair_workflow(
                repo=repo,
                current=current,
                paths=paths,
                display_output=display,
                approve_prompt=approve,
                terminal_interface=terminal_interface,
                run_summary=run_result.message or run_failure_label,
                workflow=workflow,
            )
            _emit_step(repair_step_id, "Repair runtime issue", status="done")
        except Exception as exc:
            _emit_step(repair_step_id, "Repair runtime issue", status="failed", detail=str(exc)[:80])
            _emit_step("repo.deploy", deploy_label, status="failed", detail="repair workflow raised")
            display(f"Duckln setup failed for {repo.name}. Runtime repair raised an error and Duckln stopped the setup flow.")
            _clear_chat_activity(chat)
            _clear_active_deploy_objective(paths=paths, chat=chat)
            return final_result
    display(f"Duckln finished the setup flow for {repo.name}.")
    _clear_chat_activity(chat)
    _clear_active_deploy_objective(paths=paths, chat=chat)
    return final_result


def _tracked_repo_origin_kind(config_dir: Path, repo: RepoCatalogRecord) -> str:
    if str(repo.repo_url).startswith("linked://"):
        return "manual_link"
    recent_urls = {record.repo_url for record in initialize_state_store(config_dir).list_recent_custom_repos(limit=32)}
    if repo.repo_url in recent_urls:
        return "custom_github"
    return "catalog"


def _planned_repo_install_location(*, paths: ConfigPaths, repo: RepoCatalogRecord, execution_target: str) -> str:
    managed_name = resolve_managed_project_dir(paths.config_dir, repo).name
    if execution_target in {"vm", "aws", "gcp"}:
        return f"~/.duckln/projects/{managed_name}"
    return str(resolve_managed_project_dir(paths.config_dir, repo).resolve())


def _linked_repo_key(path: Path, *, execution_target: str, vm_name: str | None = None) -> str:
    normalized = path.expanduser().resolve().as_posix()
    if execution_target == "vm" and vm_name:
        return f"linked://vm/{vm_name}{normalized}"
    return f"linked://{execution_target}/{normalized}"


def _tracked_install_location_from_row(row) -> str | None:
    install_location = str(row.metadata.get("install_location") or row.repo_path or "").strip()
    return install_location or None


def _find_repo_state_by_key(config_dir: Path, repo_key: str) -> object | None:
    lowered = repo_key.lower()
    for row in initialize_state_store(config_dir).list_repo_states():
        if lowered in {
            str(row.repo_key or "").strip().lower(),
            str(row.repo_url or "").strip().lower(),
        }:
            return row
    return None


def _upsert_tracked_repo_state(
    *,
    paths: ConfigPaths,
    repo: RepoCatalogRecord,
    repo_key: str | None = None,
    repo_path: Path | None = None,
    status: str,
    summary: str,
    execution_target: str,
    vm_name: str | None,
    managed_by_duckln: bool,
    preserve_status: bool = False,
    metadata: dict[str, object] | None = None,
) -> None:
    store = initialize_state_store(paths.config_dir)
    existing = _find_repo_state_by_key(paths.config_dir, repo_key or repo.repo_url)
    install_location = str(repo_path.resolve()) if repo_path is not None else None
    existing_metadata = dict(existing.metadata) if existing is not None else {}
    payload_metadata = dict(existing_metadata)
    payload_metadata.update(
        {
            "repo_name": repo.name,
            "description": repo.description,
            "category": repo.category,
            "framework": repo.framework,
            "stars": repo.stars,
        }
    )
    if install_location is not None:
        payload_metadata["install_location"] = install_location
        payload_metadata.setdefault("install_root_path", str(Path(install_location).parent))
    elif isinstance(existing_metadata.get("install_location"), str):
        payload_metadata["install_location"] = existing_metadata["install_location"]
    if metadata:
        payload_metadata.update(metadata)
    resolved_status = existing.status if preserve_status and existing is not None and existing.status not in {"selected", "planned"} else status
    store.upsert_repo_state(
        repo_key=repo_key or repo.repo_url,
        repo_url=str(payload_metadata.get("source_repo_url") or repo.repo_url),
        repo_path=install_location or (existing.repo_path if existing is not None else None),
        execution_target=execution_target,
        vm_name=vm_name,
        active_flag=True,
        managed_by_duckln=managed_by_duckln,
        status=resolved_status,
        summary=existing.summary if preserve_status and existing is not None and existing.status not in {"selected", "planned"} else summary,
        metadata=payload_metadata,
    )
    write_workflow_state(
        paths.config_dir,
        {
            "active_repo_key": repo_key or repo.repo_url,
            "active_repo_name": repo.name,
        },
    )


def _track_catalog_repo_selection(
    *,
    paths: ConfigPaths,
    repo: RepoCatalogRecord,
    execution_target: str,
    vm_name: str | None,
) -> None:
    managed_path = resolve_managed_project_dir(paths.config_dir, repo)
    install_path = managed_path if execution_target == "local" else None
    planned_install_location = _planned_repo_install_location(paths=paths, repo=repo, execution_target=execution_target)
    cloud_metadata = _current_cloud_tracking_metadata(paths.config_dir) if execution_target in {"aws", "gcp"} else {}
    _upsert_tracked_repo_state(
        paths=paths,
        repo=repo,
        repo_path=install_path,
        status="selected",
        summary=f"Duckln selected {repo.name} and stored its next setup path.",
        execution_target=execution_target,
        vm_name=vm_name,
        managed_by_duckln=True,
        preserve_status=True,
        metadata={
            "origin_kind": _tracked_repo_origin_kind(paths.config_dir, repo),
            "install_root_kind": "duckln_managed",
            "install_root_path": "~/.duckln/projects" if execution_target in {"vm", "aws", "gcp"} else str((paths.config_dir / "projects").resolve()),
            "planned_install_location": planned_install_location,
            **cloud_metadata,
        },
    )


def _current_cloud_tracking_metadata(config_dir: Path) -> dict[str, object]:
    workflow = _AGENT_CONTEXT_SERVICE.load_workflow_state(config_dir=config_dir)
    if workflow is None:
        return {}
    payload: dict[str, object] = {}
    if workflow.active_runtime_cloud_resource_key:
        payload["cloud_resource_key"] = workflow.active_runtime_cloud_resource_key
    if workflow.active_runtime_cloud_vendor:
        payload["cloud_vendor"] = workflow.active_runtime_cloud_vendor
    if workflow.active_runtime_cloud_region:
        payload["cloud_region"] = workflow.active_runtime_cloud_region
        payload["region"] = workflow.active_runtime_cloud_region
    if workflow.active_runtime_cloud_shape:
        payload["cloud_shape"] = workflow.active_runtime_cloud_shape
    return payload


def _handle_repo_link_command(
    *,
    paths: ConfigPaths,
    system_probe,
    text_prompt: Callable[[str, str], str | None] | None,
    display_output: Callable[[str], None],
) -> None:
    if text_prompt is None:
        display_output("Retryable error: Repo linking needs a text prompt so Duckln can capture the repo path.")
        return

    execution_target = _session_execution_target(paths.config_dir)
    vm_name = _session_vm_name(paths.config_dir)
    is_remote_target = execution_target in {"vm", "aws", "gcp"}
    raw_path = text_prompt("Enter the existing repo path Duckln should track:", "")
    if raw_path is None or not raw_path.strip():
        display_output("Repo link cancelled.")
        return
    candidate_text = raw_path.strip()
    resolved_path: Path | None
    install_location: str
    if is_remote_target:
        install_location = candidate_text
        resolved_path = None
    else:
        candidate_path = Path(candidate_text).expanduser()
        resolved_path = (Path.cwd() / candidate_path).resolve() if not candidate_path.is_absolute() else candidate_path.resolve()
        if not resolved_path.exists() or not resolved_path.is_dir():
            display_output(f"Retryable error: Duckln could not find a repo directory at {resolved_path}.")
            return
        install_location = str(resolved_path)

    repo_url_input = text_prompt(
        "Optional: paste the public repo URL Duckln should remember, or leave it blank:",
        "",
    )
    source_repo_url = (repo_url_input or "").strip() or None
    default_name = (Path(install_location.rstrip("/")).name if install_location.strip() else "repo") or "repo"
    display_name = (text_prompt("Repo name to display in Duckln:", default_name) or default_name).strip() or default_name
    repo_key = (
        _linked_repo_key(resolved_path, execution_target=execution_target, vm_name=vm_name)
        if resolved_path is not None
        else f"linked://{execution_target}/{install_location}"
    )
    linked_repo = RepoCatalogRecord(
        name=display_name,
        repo_url=repo_key,
        stars=0,
        description="User-linked repo tracked by Duckln.",
        category="Custom",
        framework="Unknown",
        last_updated=datetime.now(timezone.utc).date().isoformat(),
    )
    hints = (
        derive_repo_runtime_hints(
            linked_repo,
            resolved_path,
            system_probe=system_probe,
            execution_target=execution_target,
            config_dir=paths.config_dir,
        )
        if resolved_path is not None
        else None
    )
    cloud_metadata = _current_cloud_tracking_metadata(paths.config_dir) if execution_target in {"aws", "gcp"} else {}
    _upsert_tracked_repo_state(
        paths=paths,
        repo=linked_repo,
        repo_key=repo_key,
        repo_path=resolved_path,
        status="linked",
        summary=f"Duckln linked {display_name} from an existing repo path.",
        execution_target=execution_target,
        vm_name=vm_name,
        managed_by_duckln=False,
        metadata={
            "origin_kind": "manual_link",
            "install_root_kind": "user_linked",
            "install_root_path": str(resolved_path.parent) if resolved_path is not None else str(Path(install_location).parent),
            "source_repo_url": source_repo_url,
            "install_location": install_location,
            "run_command": None if hints is None else hints.run_command,
            "verify_command": None if hints is None else hints.verify_command,
            "manual_command": None if hints is None else hints.manual_command,
            "runtime_kind": None if hints is None else hints.runtime_kind,
            "access_hint": (
                f"Use the tracked repo path at {install_location} from the active {execution_target.upper()} target."
                if hints is None
                else hints.access_hint
            ),
            "removal_hint": (
                f"remove the project directory at {install_location}"
                if hints is None
                else hints.removal_hint
            ),
            "detected_files": [] if hints is None else list(hints.detected_files),
            **cloud_metadata,
        },
    )
    if hints is not None and hints.manual_command:
        manual_line = f"Manual run command: `{hints.manual_command}`."
    elif hints is not None and hints.run_command:
        manual_line = f"Manual run command: `{hints.run_command}`."
    elif is_remote_target:
        manual_line = "Duckln linked the remote repo path and will derive runtime commands from repo evidence or future checks."
    else:
        manual_line = "Duckln could not infer a safe manual run command yet."
    access_line = (
        (hints.access_hint if hints is not None else None)
        or (
            f"Duckln recorded this remote repo path on the active {execution_target.upper()} target and can run or inspect it later."
            if is_remote_target
            else "Duckln can still inspect the repo files and derive the next bounded step from this path later."
        )
    )
    display_output(
        join_blocks(
            f"Duckln linked {display_name} at {install_location}.",
            manual_line,
            access_line,
        )
    )


def _handle_repo_run_action(
    *,
    reply: FreeTextReply,
    current: AppConfig,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    approve_prompt: Callable[[str], bool] | None,
    text_prompt: Callable[[str, str], str | None] | None = None,
    terminal_interface: object | None = None,
) -> None:
    _handle_repo_runtime_action(
        reply=reply,
        current=current,
        paths=paths,
        display_output=display_output,
        approve_prompt=approve_prompt,
        text_prompt=text_prompt,
        verification_only=False,
        terminal_interface=terminal_interface,
    )


def _handle_repo_restart_action(
    *,
    reply: FreeTextReply,
    current: AppConfig,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    approve_prompt: Callable[[str], bool] | None,
    text_prompt: Callable[[str, str], str | None] | None = None,
    terminal_interface: object | None = None,
) -> None:
    repo = _resolve_repo_from_action_key(paths.config_dir, reply.action_repo_key)
    if repo is None:
        display_output("Supervisor agent could not resolve that repo from Duckln’s tracked runtime state.")
        return
    workflow = _AGENT_CONTEXT_SERVICE.load_workflow_state(config_dir=paths.config_dir)
    has_live_runtime = bool(
        workflow is not None
        and workflow.active_runtime_status in {"running", "interactive"}
        and workflow.active_runtime_repo_key == repo.repo_url
    )
    display_output(
        render_tool_invocation_trace(
            title="repo restart review",
            tool_id="process.session_runtime",
            action=f"Restart {repo.name} through Duckln's tracked runtime path",
            detail_lines=(
                (
                    "Duckln is tracking a live repo session and will stop it first before re-running."
                    if has_live_runtime
                    else "Duckln is not tracking a live repo session, so restart will fall back to a fresh bounded run."
                ),
                f"Resolved repo: {repo.name}",
            ),
            execution_target=_session_execution_target(paths.config_dir),
        )
    )
    prompt = (
        f"Do you want Duckln to restart {repo.name} now? Duckln will stop the tracked session first and then run it again."
        if has_live_runtime
        else f"Do you want Duckln to restart {repo.name} now? Duckln will fall back to a fresh bounded run."
    )
    if approve_prompt is not None and not approve_prompt(prompt):
        display_output(f"Supervisor agent cancelled restarting {repo.name}.")
        return
    if has_live_runtime:
        stopped = _handle_repo_stop_action(
            reply=FreeTextReply(text="", intent="repo_stop", action="stop_repo", action_repo_key=reply.action_repo_key or repo.repo_url),
            current=current,
            paths=paths,
            display_output=display_output,
            approve_prompt=None,
            terminal_interface=terminal_interface,
        )
        if not stopped:
            display_output(f"Supervisor agent could not complete the restart because Duckln did not stop {repo.name} cleanly.")
            return
    else:
        display_output(f"Duckln is restarting {repo.name} by falling back to a fresh bounded run.")
    _handle_repo_run_action(
        reply=FreeTextReply(text="", intent="repo_run", action="run_repo", action_repo_key=reply.action_repo_key or repo.repo_url),
        current=current,
        paths=paths,
        display_output=display_output,
        approve_prompt=None,
        text_prompt=text_prompt,
        terminal_interface=terminal_interface,
    )


def _handle_repo_verify_action(
    *,
    reply: FreeTextReply,
    current: AppConfig,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    approve_prompt: Callable[[str], bool] | None,
    text_prompt: Callable[[str, str], str | None] | None = None,
    terminal_interface: object | None = None,
) -> None:
    _handle_repo_runtime_action(
        reply=reply,
        current=current,
        paths=paths,
        display_output=display_output,
        approve_prompt=approve_prompt,
        text_prompt=text_prompt,
        verification_only=True,
        terminal_interface=terminal_interface,
    )


def _placeholder_requires_raw_text(placeholder_name: str) -> bool:
    lowered = placeholder_name.casefold()
    return any(token in lowered for token in ("args", "argument", "options", "flags"))


def _placeholder_expects_attached_file(placeholder_name: str) -> bool:
    lowered = placeholder_name.casefold()
    return any(
        token in lowered
        for token in (
            "file",
            "path",
            "audio",
            "image",
            "video",
            "document",
            "doc",
            "input",
            "attachment",
            "transcript",
            "media",
        )
    )


def _materialize_cli_task_command(
    *,
    repo: RepoCatalogRecord,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    text_prompt: Callable[[str, str], str | None] | None,
    terminal_interface: object | None = None,
) -> tuple[str | None, bool]:
    tracked = _AGENT_CONTEXT_SERVICE.load_repo_state_snapshot(config_dir=paths.config_dir, repo=repo)
    if tracked is None or tracked.runtime_kind != "cli_tool":
        return None, False
    run_command = tracked.run_command
    manual_command = tracked.manual_command
    if isinstance(run_command, str) and run_command.strip() and classify_runtime_command(run_command) not in {"verification", "missing"} and not _CLI_PLACEHOLDER_PATTERN.search(run_command):
        return run_command.strip(), False
    template = None
    if isinstance(manual_command, str) and manual_command.strip():
        template = manual_command.strip()
    elif isinstance(run_command, str) and run_command.strip():
        template = run_command.strip()
    if not template:
        return None, False
    placeholders = [match.group(1).strip() for match in _CLI_PLACEHOLDER_PATTERN.finditer(template) if match.group(1).strip()]
    if not placeholders:
        return template, False
    attached_files: list[str] = []
    if terminal_interface is not None and hasattr(terminal_interface, "list_attached_files"):
        try:
            attached_files = [str(item).strip() for item in terminal_interface.list_attached_files() if str(item).strip()]
        except Exception:
            attached_files = []
    if text_prompt is None:
        display_output(
            f"Duckln needs a concrete CLI input or task before it can run {repo.name}, but this session does not currently have a text prompt available."
        )
        return None, True
    target_label = tracked.execution_target.upper() if tracked.execution_target and tracked.execution_target != "local" else "local machine"
    values: dict[str, str] = {}
    consumed_attachments: list[str] = []
    for placeholder in placeholders:
        if _placeholder_expects_attached_file(placeholder) and attached_files:
            chosen = attached_files.pop(0)
            values[placeholder] = chosen
            consumed_attachments.append(chosen)
            if terminal_interface is not None and hasattr(terminal_interface, "consume_attached_file"):
                try:
                    terminal_interface.consume_attached_file(chosen)
                except Exception:
                    pass
            continue
        prompt = (
            f"Enter the value Duckln should use for <{placeholder}> while running {repo.name}. "
            f"If this repo runs on {target_label}, that value must make sense on that target.\n\n"
            f"Command template:\n{template}"
        )
        response = text_prompt(prompt, "")
        if response is None or not response.strip():
            display_output(f"Supervisor agent cancelled running {repo.name} because <{placeholder}> was left empty.")
            return None, True
        values[placeholder] = response.strip()
    materialized = template
    for placeholder in placeholders:
        raw_value = values[placeholder]
        rendered = raw_value if _placeholder_requires_raw_text(placeholder) else shlex.quote(raw_value)
        materialized = materialized.replace(f"<{placeholder}>", rendered, 1)
    display_output(
        render_tool_invocation_trace(
            title="repo task materialization",
            tool_id="process.runtime_materializer",
            action=f"Materialize a concrete CLI task for {repo.name}",
            detail_lines=(
                f"Template: {template}",
                f"Resolved command: {materialized}",
                (
                    "Attached files used: " + ", ".join(consumed_attachments)
                    if consumed_attachments
                    else "No staged attachments were consumed for this task."
                ),
                "Duckln replaced placeholder values before starting the tracked CLI task.",
            ),
            execution_target=_session_execution_target(paths.config_dir),
        )
    )
    return materialized, False


def _normalize_runtime_followup_text(value: str) -> str:
    normalized = str(value or "")
    normalized = re.sub(r"DUCKLN-DONE-[A-Za-z0-9_-]+:\d+", " ", normalized)
    normalized = re.sub(r"\b\d{4}-\d{2}-\d{2}T\d{2}[:_]\d{2}[:_]\d{2}[^\s]*\b", " ", normalized)
    normalized = re.sub(r"/home/[A-Za-z0-9._-]+/\.npm/_logs/[^\s]+", " ", normalized)
    return " ".join(normalized.casefold().split())


def _runtime_incident_fingerprint(*, category: str, summary: str) -> str:
    return f"{str(category or '').strip().casefold()}|{_normalize_runtime_followup_text(summary)}"


def _dedupe_runtime_incidents(incidents: Sequence[dict[str, object]]) -> tuple[dict[str, object], ...]:
    seen: set[str] = set()
    deduped: list[dict[str, object]] = []
    for incident in incidents:
        category = str(incident.get("category") or "unknown_failure").strip() or "unknown_failure"
        summary = str(incident.get("summary") or "").strip()
        if not summary:
            continue
        fingerprint = _runtime_incident_fingerprint(category=category, summary=summary)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        deduped.append(incident)
    return tuple(deduped)


def _looks_like_runtime_repair_request(message: str) -> bool:
    normalized = _normalize_runtime_followup_text(message)
    if normalized in {"yes", "yes please", "please do", "do it", "go ahead", "sure", "ok", "okay", "alright", "all right", "continue", "proceed"}:
        return True
    # Plan 79 Fix 2: explicit "resume the last operation" phrases — after the user
    # starts a model that was unreachable, or wants Duckln to pick up where it left
    # off, these re-enter the pending step (e.g. re-draft the plan) and ask again.
    if normalized in {
        "retry", "try again", "tryagain", "try now", "trynow", "check now",
        "checknow", "check again", "check it now", "resume", "pick up where you left off",
    }:
        return True
    tokens = set(normalized.replace("?", " ").replace(".", " ").split())
    if tokens & {"yes", "sure", "alright", "all", "okay", "ok", "continue", "proceed"} and tokens & {
        "continue",
        "proceed",
        "fix",
        "repair",
        "solve",
        "retry",
    }:
        return True
    if {"please", "ask"} <= tokens or {"ask", "me"} <= tokens:
        return True
    if "approval" in tokens or "approve" in tokens or "prompt" in tokens:
        return True
    return any(
        phrase in normalized
        for phrase in (
            "fix it",
            "repair it",
            "solve it",
            "inspect the blocker",
            "fix the blocker",
            "continue repair",
            "continue fixing",
            "ask for approval",
            "show approval",
            "show the approval",
            "show the prompt",
        )
    )


def _looks_like_runtime_issue_explanation_request(message: str) -> bool:
    normalized = _normalize_runtime_followup_text(message)
    return any(
        phrase in normalized
        for phrase in (
            "what happened",
            "what is wrong",
            "show the blocker",
            "show the error",
            "explain the error",
            "what failed",
            "why did it fail",
        )
    )


def _clear_runtime_repair_followup(paths: ConfigPaths) -> None:
    write_followup_state(
        paths.config_dir,
        {
            "pending_objective_id": None,
            "pending_repo_key": None,
            "pending_repo_name": None,
            "pending_next_action": None,
            "pending_offer_kind": None,
            "pending_offer_label": None,
            "pending_offer_objective_id": None,
            "pending_offer_repo_key": None,
            "pending_offer_repo_name": None,
        },
    )


def _looks_like_global_stop_command(message: str) -> bool:
    normalized = " ".join(str(message or "").casefold().split())
    return normalized in {
        "stop",
        "stop now",
        "cancel",
        "cancel now",
        "abort",
        "abort now",
        "stop everything",
        "stop all",
    }


def _looks_like_browser_link_request(message: str) -> bool:
    normalized = " ".join(str(message or "").casefold().split())
    if not normalized:
        return False
    if not any(token in normalized for token in ("browser", "link", "url", "auth")):
        return False
    return any(
        phrase in normalized
        for phrase in (
            "open",
            "reopen",
            "re-open",
            "repo the link",
            "repon the link",
            "show",
            "copy",
            "paste",
            "not working",
        )
    )


def _handle_global_browser_link_intent(
    *,
    command: str,
    display_output: Callable[[str], None],
    terminal_interface: object | None,
) -> bool:
    if not _looks_like_browser_link_request(command):
        return False
    last_url = None
    getter = getattr(terminal_interface, "last_terminal_url", None) if terminal_interface is not None else None
    if callable(getter):
        try:
            last_url = getter()
        except Exception:
            last_url = None
    if not last_url:
        display_output("I do not see a recent terminal link. Re-run the auth command or paste the URL and I will open it.")
        return True
    normalized = " ".join(str(command or "").casefold().split())
    if any(word in normalized for word in ("copy", "show", "paste")) and not any(word in normalized for word in ("open", "reopen", "browser")):
        display_output(f"Latest terminal link:\n{last_url}")
        return True
    opener = getattr(terminal_interface, "open_last_terminal_url", None) if terminal_interface is not None else None
    opened = False
    if callable(opener):
        try:
            opened = bool(opener())
        except Exception:
            opened = False
    if opened:
        display_output("Opened the latest terminal link in your browser.")
    else:
        display_output(f"I found the latest terminal link, but could not open the browser from here:\n{last_url}")
    return True


def _handle_global_stop_command(
    *,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    terminal_interface: object | None,
) -> bool:
    interrupted = False
    if terminal_interface is not None and hasattr(terminal_interface, "interrupt_terminal"):
        try:
            interrupted = bool(terminal_interface.interrupt_terminal())
        except Exception:
            interrupted = False
    _clear_runtime_repair_followup(paths)
    _clear_active_runtime_objective(paths, chat=terminal_interface)
    _clear_active_deploy_objective(paths, chat=terminal_interface)
    write_workflow_state(
        paths.config_dir,
        {
            "active_issue_kind": "cancelled",
            "active_issue_summary": "Duckln stopped the current action because the user requested stop.",
            "active_runtime_status": "stopped",
            "active_runtime_pid": None,
            "active_runtime_stop_hint": None,
            "active_runtime_stop_command": None,
            "active_repair_phase": "stopped_by_user",
        },
    )
    _clear_chat_activity(terminal_interface)
    display_output(
        "Stopped the current Duckln action."
        + (" Sent Ctrl+C to the terminal pane." if interrupted else " No active terminal process was confirmed.")
    )
    return True


def _persist_runtime_repair_followup(
    *,
    paths: ConfigPaths,
    repo_key: str | None,
    repo_name: str | None,
    objective_id: str | None = None,
) -> None:
    workflow = _AGENT_CONTEXT_SERVICE.load_workflow_state(config_dir=paths.config_dir)
    resolved_objective_id = objective_id or _workflow_objective_identity(workflow)
    if resolved_objective_id is None:
        objective_anchor = str(repo_key or repo_name or "").strip()
        if objective_anchor:
            resolved_objective_id = f"runtime_repair:{objective_anchor}"
    resolved_execution_target = (
        "local"
        if workflow is None or not workflow.active_objective_execution_target
        else workflow.active_objective_execution_target
    )
    resolved_vm_name = (
        None
        if workflow is None
        else str(getattr(workflow, "active_runtime_vm_name", "") or "").strip() or None
    )
    if resolved_execution_target != "vm":
        resolved_vm_name = None
    write_followup_state(
        paths.config_dir,
        {
            "pending_objective_id": resolved_objective_id,
            "pending_repo_key": repo_key,
            "pending_repo_name": repo_name,
            "pending_next_action": "repair_repo_runtime",
            "pending_offer_kind": "repair_repo_runtime",
            "pending_offer_label": "repair the runtime issue",
            "pending_offer_objective_id": resolved_objective_id,
            "pending_offer_repo_key": repo_key,
            "pending_offer_repo_name": repo_name,
            "pending_subject_type": "repo",
            "last_next_step_offered": "repair_repo_runtime",
            "active_execution_target": resolved_execution_target,
            "active_vm_name": resolved_vm_name,
        },
    )


def _current_timestamp_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _begin_active_runtime_objective(
    *,
    paths: ConfigPaths,
    repo: RepoCatalogRecord,
    execution_target: str,
    runtime_command: str | None,
    goal: str,
    resume_hint: str | None = None,
    workflow_context=None,
    chat: object | None = None,
) -> None:
    workflow = _AGENT_CONTEXT_SERVICE.load_workflow_state(config_dir=paths.config_dir)
    objective_id = f"runtime_repair:{repo.repo_url}"
    attempt_count = 0
    started_at = _current_timestamp_iso()
    if (
        workflow is not None
        and workflow.active_objective_kind == "runtime_repair"
        and (workflow.active_objective_repo_key or workflow.repo_key) == repo.repo_url
    ):
        attempt_count = workflow.active_objective_attempt_count or 0
        started_at = workflow.active_objective_started_at or started_at
    context = workflow_context if workflow_context is not None else workflow
    write_workflow_state(
        paths.config_dir,
        {
            "active_repo_key": repo.repo_url,
            "active_repo_name": repo.name,
            "active_objective_id": objective_id,
            "active_objective_kind": "runtime_repair",
            "active_objective_repo_key": repo.repo_url,
            "active_objective_repo_name": repo.name,
            "active_objective_status": "active",
            "active_objective_goal": goal,
            "active_objective_execution_target": execution_target,
            "active_objective_runtime_command": runtime_command,
            "active_objective_last_blocker": None,
            "active_objective_attempt_count": attempt_count,
            "active_objective_max_attempts": _ACTIVE_OBJECTIVE_MAX_REPAIR_ATTEMPTS,
            "active_objective_requires_user_decision": False,
            "active_objective_resume_hint": resume_hint or goal,
            "active_objective_started_at": started_at,
            "active_objective_updated_at": _current_timestamp_iso(),
            "active_repair_phase": None,
            "active_runtime_execution_target": execution_target,
            "active_runtime_repo_key": repo.repo_url,
            "active_runtime_repo_name": repo.name,
            "active_runtime_command": runtime_command,
            "active_runtime_cwd": None if context is None else getattr(context, "active_runtime_cwd", None),
            "active_runtime_vm_name": None if context is None else getattr(context, "active_runtime_vm_name", None),
            "active_runtime_cloud_resource_key": None if context is None else getattr(context, "active_runtime_cloud_resource_key", None),
            "active_runtime_cloud_vendor": None if context is None else getattr(context, "active_runtime_cloud_vendor", None),
            "active_runtime_cloud_region": None if context is None else getattr(context, "active_runtime_cloud_region", None),
            "active_runtime_cloud_shape": None if context is None else getattr(context, "active_runtime_cloud_shape", None),
        },
    )
    _sync_chat_objective_status(chat=chat, config_dir=paths.config_dir)


def _update_active_runtime_objective(
    paths: ConfigPaths,
    chat: object | None = None,
    **values: object | None,
) -> None:
    payload = {"active_objective_updated_at": _current_timestamp_iso()}
    payload.update(values)
    write_workflow_state(paths.config_dir, payload)
    _sync_chat_objective_status(chat=chat, config_dir=paths.config_dir)


def _begin_active_deploy_objective(
    *,
    paths: ConfigPaths,
    repo: RepoCatalogRecord,
    execution_target: str,
    chat: object | None = None,
) -> None:
    """Item 3: mark an active `repo_deploy` objective so the supervisor can answer
    "what's happening?" with deploy-phase context (clone / install / run / repair).
    """

    write_workflow_state(
        paths.config_dir,
        {
            "active_repo_key": repo.repo_url,
            "active_repo_name": repo.name,
            "active_objective_id": f"repo_deploy:{repo.repo_url}",
            "active_objective_kind": "repo_deploy",
            "active_objective_repo_key": repo.repo_url,
            "active_objective_repo_name": repo.name,
            "active_objective_status": "active",
            "active_objective_goal": f"Deploy {repo.name} on {execution_target} and get it running.",
            "active_objective_execution_target": execution_target,
            "active_objective_runtime_command": None,
            "active_objective_last_blocker": None,
            "active_objective_attempt_count": 0,
            "active_objective_max_attempts": _ORCHESTRATE_MAX_REPAIR_ATTEMPTS,
            "active_objective_requires_user_decision": False,
            "active_objective_resume_hint": f"Resume deploying {repo.name}.",
            "active_objective_started_at": _current_timestamp_iso(),
            "active_objective_updated_at": _current_timestamp_iso(),
            "active_repair_phase": "deploy_start",
        },
    )
    _sync_chat_objective_status(chat=chat, config_dir=paths.config_dir)


def _clear_active_deploy_objective(paths: ConfigPaths, *, chat: object | None = None) -> None:
    """Clear the deploy objective on success or cancellation."""

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
            "active_repair_phase": None,
        },
    )
    _sync_chat_objective_status(chat=chat, config_dir=paths.config_dir)


def _clear_active_runtime_objective(paths: ConfigPaths, *, chat: object | None = None) -> None:
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
            "active_approved_install_repo_key": None,
            "active_approved_install_command": None,
            "active_issue_kind": None,
            "active_issue_summary": None,
            "active_incident_category": None,
            "active_incident_summary": None,
            "active_repair_phase": None,
            "active_runtime_repo_key": None,
            "active_runtime_repo_name": None,
            "active_runtime_command": None,
            "active_runtime_cwd": None,
        },
    )
    _sync_chat_objective_status(chat=chat, config_dir=paths.config_dir)


_RUNTIME_REPAIR_TERMINAL_PHASES = {
    "repair_declined",
    "setup_complete",
    "runtime_verified",
    "repair_verified",
    "setup_approval_declined",
    "waiting_on_app_prompt",
}


def _runtime_workflow_blocks_incident_repair(workflow) -> bool:
    """Return True when a workflow is paused/done and terminal output must not restart repair."""

    if workflow is None:
        return False
    phase = str(getattr(workflow, "active_repair_phase", None) or "").strip().casefold()
    status = str(getattr(workflow, "active_objective_status", None) or "").strip().casefold()
    if bool(getattr(workflow, "active_objective_requires_user_decision", False)):
        return True
    if phase in _RUNTIME_REPAIR_TERMINAL_PHASES:
        return True
    if status in {"declined", "complete", "completed", "verified"}:
        return True
    return False


def _mark_runtime_repair_declined(
    *,
    paths: ConfigPaths,
    repo: RepoCatalogRecord,
    run_summary: str,
    incident,
    terminal_interface: object | None,
) -> None:
    """Persist a user's No as a terminal state, not as another pending follow-up."""

    write_workflow_state(
        paths.config_dir,
        {
            "active_repo_key": repo.repo_url,
            "active_repo_name": repo.name,
            "active_issue_kind": "run_issue",
            "active_issue_summary": run_summary,
            "active_incident_category": incident.category,
            "active_incident_summary": incident.summary,
            "active_repair_phase": "repair_declined",
            "active_objective_status": "declined",
            "active_objective_requires_user_decision": False,
            "active_objective_last_blocker": run_summary,
            "active_objective_resume_hint": f"Duckln paused the {repo.name} repair because you said No. Say retry or fix it to reopen.",
            "active_objective_updated_at": _current_timestamp_iso(),
        },
    )
    _clear_runtime_repair_followup(paths)
    _sync_chat_objective_status(chat=terminal_interface, config_dir=paths.config_dir)
    _clear_chat_activity(terminal_interface)


def _is_active_runtime_objective(
    workflow,
    *,
    repo_key: str | None,
) -> bool:
    if workflow is None or repo_key is None:
        return False
    if str(getattr(workflow, "active_objective_kind", "") or "").strip() != "runtime_repair":
        return False
    return (
        str(getattr(workflow, "active_objective_repo_key", "") or "").strip()
        or str(getattr(workflow, "repo_key", "") or "").strip()
    ) == repo_key


def _consume_runtime_objective_attempt(
    *,
    paths: ConfigPaths,
    repo_name: str,
    blocker_summary: str,
    chat: object | None = None,
) -> bool:
    workflow = _AGENT_CONTEXT_SERVICE.load_workflow_state(config_dir=paths.config_dir)
    attempt_count = 1 + (0 if workflow is None or workflow.active_objective_attempt_count is None else workflow.active_objective_attempt_count)
    max_attempts = (
        _ACTIVE_OBJECTIVE_MAX_REPAIR_ATTEMPTS
        if workflow is None or workflow.active_objective_max_attempts is None
        else workflow.active_objective_max_attempts
    )
    exhausted = attempt_count >= max_attempts
    _update_active_runtime_objective(
        paths,
        chat=chat,
        active_objective_attempt_count=attempt_count,
        active_objective_max_attempts=max_attempts,
        active_objective_last_blocker=blocker_summary,
        active_objective_status="needs_user_decision" if exhausted else "active",
        active_objective_requires_user_decision=exhausted,
        active_objective_resume_hint=(
            f"Last time Duckln was fixing {repo_name}. Latest blocker: {blocker_summary}"
            if blocker_summary
            else f"Last time Duckln was fixing {repo_name}."
        ),
    )
    return exhausted


def _runtime_objective_is_preapproved(workflow, *, repo_key: str | None) -> bool:
    return _is_active_runtime_objective(workflow, repo_key=repo_key) and not bool(
        workflow.active_objective_requires_user_decision
    )


def _runtime_install_command_fingerprint(command: str | None) -> str:
    return " ".join(str(command or "").strip().casefold().split())


def _runtime_install_is_preapproved(
    workflow,
    *,
    repo_key: str | None,
    command: str | None,
) -> bool:
    if workflow is None or not repo_key or not command:
        return False
    approved_repo_key = str(getattr(workflow, "active_approved_install_repo_key", "") or "").strip()
    approved_command = _runtime_install_command_fingerprint(
        str(getattr(workflow, "active_approved_install_command", "") or "")
    )
    return approved_repo_key == repo_key and approved_command == _runtime_install_command_fingerprint(command)


def _mark_runtime_install_preapproved(
    *,
    paths: ConfigPaths,
    repo_key: str | None,
    command: str | None,
) -> None:
    if not repo_key or not command:
        return
    write_workflow_state(
        paths.config_dir,
        {
            "active_approved_install_repo_key": repo_key,
            "active_approved_install_command": command,
        },
    )


def _handle_non_repairable_runtime_result(
    *,
    repo: RepoCatalogRecord,
    result_obj,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    terminal_interface: object | None,
    phase: str,
) -> bool:
    if result_obj is None:
        return False
    if getattr(result_obj, "verification_passed", False):
        return False
    if getattr(result_obj, "should_offer_repair", True):
        return False
    blocker = str(getattr(result_obj, "message", "") or "").strip() or (
        f"Duckln paused on {repo.name} because a manual runtime step is still needed."
    )
    write_workflow_state(
        paths.config_dir,
        {
            "active_repo_key": repo.repo_url,
            "active_repo_name": repo.name,
            "active_issue_kind": "run_issue",
            "active_issue_summary": blocker,
            "active_repair_phase": phase,
        },
    )
    _persist_runtime_repair_followup(paths=paths, repo_key=repo.repo_url, repo_name=repo.name)
    _update_active_runtime_objective(
        paths,
        chat=terminal_interface,
        active_objective_status="needs_user_decision",
        active_objective_requires_user_decision=True,
        active_objective_last_blocker=blocker,
        active_objective_resume_hint=f"Last time Duckln was fixing {repo.name}. Latest blocker: {_compact_runtime_summary(blocker)}",
        active_repair_phase=phase,
    )
    if not getattr(result_obj, "message_already_displayed", False):
        display_output(blocker)
    return True


def _render_runtime_objective_timeout(
    *,
    repo_name: str,
    blocker_summary: str,
    max_attempts: int,
) -> str:
    pieces = [
        f"Duckln has already tried {max_attempts} bounded repair step(s) for {repo_name} and it still is not running.",
    ]
    if blocker_summary:
        pieces.append(f"Latest blocker: {blocker_summary}")
    pieces.append(
        "Duckln can try a different repair path next, or escalate to a different execution target like VM or Docker if that fits the repo better."
    )
    return " ".join(pieces)


_RUNTIME_ESCALATION_CHOICE_RETRY = "retry"
_RUNTIME_ESCALATION_CHOICE_SWITCH_LOCAL = "switch_local"
_RUNTIME_ESCALATION_CHOICE_PAUSE = "pause"


def _build_runtime_escalation_choices(
    *,
    repo_name: str,
    max_attempts: int,
    current_target: str,
) -> tuple[tuple[str, str], ...]:
    """Build the inline-chooser options for a repair-limit-reached escalation.

    Returns a tuple of (label, action_key) pairs so the chooser stays terminal-first
    (one chooser entry per actionable next step) while the action key drives the
    state transition without depending on label text."""

    retry_label = f"Retry the repair path (start fresh attempt 1 of {max_attempts})"
    pause_label = f"Pause and let me investigate {repo_name} myself"
    options: list[tuple[str, str]] = [(retry_label, _RUNTIME_ESCALATION_CHOICE_RETRY)]
    if (current_target or "").strip().casefold() != "local":
        options.append((f"Switch {repo_name} to local execution and retry", _RUNTIME_ESCALATION_CHOICE_SWITCH_LOCAL))
    options.append((pause_label, _RUNTIME_ESCALATION_CHOICE_PAUSE))
    return tuple(options)


def _surface_runtime_escalation_choice(
    *,
    repo_name: str,
    blocker_summary: str,
    max_attempts: int,
    current_target: str,
    chat: object | None,
    display_output: Callable[[str], None],
) -> str:
    """Open an inline up/down chooser explaining what Duckln needs the user to decide.

    Falls back to display-only when the live UI is not available so headless tests and
    fallback shells still see the prompt text. Returns the chosen action key."""

    options = _build_runtime_escalation_choices(
        repo_name=repo_name,
        max_attempts=max_attempts,
        current_target=current_target,
    )
    _clear_chat_activity(chat)
    labels = tuple(label for label, _ in options)
    headline = (
        f"Duckln needs your decision on {repo_name}: "
        f"the bounded {max_attempts}-step repair path could not get it running."
    )
    if blocker_summary:
        headline += f" Latest blocker: {blocker_summary}"
    display_output(headline)
    if chat is not None and hasattr(chat, "select_choice") and getattr(chat, "supports_live", False):
        try:
            chosen_label = chat.select_choice(
                f"What should Duckln do next for {repo_name}?",
                labels,
            )
        except TypeError:
            chosen_label = chat.select_choice(
                f"What should Duckln do next for {repo_name}?",
                labels,
            )
        if chosen_label is None:
            return _RUNTIME_ESCALATION_CHOICE_PAUSE
        for label, key in options:
            if label == chosen_label:
                return key
        return _RUNTIME_ESCALATION_CHOICE_PAUSE
    display_output("Type /repos to pick the next step, or set a different execution target with /vm, /aws, or /gcp.")
    return _RUNTIME_ESCALATION_CHOICE_PAUSE


def _apply_runtime_escalation_choice(
    *,
    chosen_action: str,
    repo,
    paths: ConfigPaths,
    execution_target: str,
    display_output: Callable[[str], None],
    terminal_interface: object | None,
) -> None:
    """Persist the user's escalation decision so the next turn picks up the chosen path."""

    if chosen_action == _RUNTIME_ESCALATION_CHOICE_RETRY:
        _update_active_runtime_objective(
            paths,
            chat=terminal_interface,
            active_objective_attempt_count=0,
            active_objective_status="active",
            active_objective_requires_user_decision=False,
            active_repair_phase="awaiting_repair_retry",
        )
        display_output(
            f"Duckln will retry the repair path for {repo.name} on the next runtime check. "
            "Type the run command (or /repos) when you're ready to continue."
        )
        return
    if chosen_action == _RUNTIME_ESCALATION_CHOICE_SWITCH_LOCAL:
        _persist_session_execution_target(paths=paths, execution_target="local", vm_name=None)
        _update_active_runtime_objective(
            paths,
            chat=terminal_interface,
            active_objective_execution_target="local",
            active_objective_attempt_count=0,
            active_objective_status="active",
            active_objective_requires_user_decision=False,
            active_repair_phase="awaiting_repair_retry",
        )
        display_output(
            f"Duckln switched the execution target for {repo.name} to local. "
            "The next run will use the local environment."
        )
        return
    display_output(
        f"Duckln paused {repo.name}. State is preserved — say 'continue' (or /repos) when you've inspected the blocker."
    )


def _runtime_issue_guidance(
    *,
    repo_name: str,
    repo_key: str | None,
    paths: ConfigPaths,
    incident_summary: str,
    incident_category: str,
) -> str | None:
    if incident_category == "app_prompt_active":
        return (
            "Duckln paused automation because the app is waiting for input in the terminal pane. "
            "Answer that prompt there, then tell Duckln to continue."
        )
    if incident_category != "user_input_error":
        return None
    row = _resolve_repo_state_from_action_key(paths.config_dir, repo_key)
    manual_command = None if row is None else str(row.metadata.get("manual_command") or "").strip()
    run_command = None if row is None else str(row.metadata.get("run_command") or "").strip()
    pieces = [
        f"This looks like a command-shape or input issue for {repo_name}, not a broken environment.",
        incident_summary,
    ]
    if manual_command:
        pieces.append(f"Manual use: `{manual_command}`.")
    elif run_command:
        pieces.append(f"Tracked command: `{run_command}`.")
    pieces.append("Give Duckln a concrete file, task, or corrected CLI arguments and I’ll materialize the right run step.")
    return " ".join(piece for piece in pieces if piece)


def _runtime_specialist_guidance(
    *,
    repo_name: str,
    repo_key: str | None,
    paths: ConfigPaths,
    incident,
    runtime_repair_plan,
    execution_target: str,
) -> str | None:
    if runtime_repair_plan.action_key not in {
        "auth_guidance",
        "cloud_auth_guidance",
        "cloud_capacity_escalation",
        "vm_repair_escalation",
    }:
        return None
    row = _resolve_repo_state_from_action_key(paths.config_dir, repo_key)
    metadata = {} if row is None else row.metadata
    pieces = [runtime_repair_plan.reason, incident.summary]
    if runtime_repair_plan.action_key == "auth_guidance":
        missing_auth = tuple(str(item).strip() for item in metadata.get("missing_auth_variables", ()) if str(item).strip())
        if missing_auth:
            pieces.append(f"Auth still needed: {', '.join(missing_auth)}.")
        pieces.append("Duckln will not silently copy or invent credentials. Add the required auth first, then rerun the bounded check.")
    elif runtime_repair_plan.action_key == "cloud_auth_guidance":
        pieces.append(
            f"Duckln needs valid {execution_target.upper() if execution_target in {'aws', 'gcp'} else 'cloud'} auth before it can continue launch or runtime repair."
        )
    elif runtime_repair_plan.action_key == "cloud_capacity_escalation":
        pieces.append("Pick a different approved cloud shape, region, or provider path before retrying the repo bring-up.")
    elif runtime_repair_plan.action_key == "vm_repair_escalation":
        pieces.append("Repair the VM launch or connection path first, then Duckln can continue setup inside that VM.")
    if runtime_repair_plan.verification_hint:
        pieces.append(runtime_repair_plan.verification_hint)
    return " ".join(piece for piece in pieces if piece)


def _runtime_cloud_provider(*, execution_target: str, workflow) -> str | None:
    lowered_target = str(execution_target or "").strip().lower()
    if lowered_target in {"aws", "gcp"}:
        return lowered_target
    if workflow is None:
        return None
    vendor = str(getattr(workflow, "active_runtime_cloud_vendor", "") or "").strip().lower()
    if vendor in {"aws", "gcp"}:
        return vendor
    target = str(getattr(workflow, "active_runtime_execution_target", "") or "").strip().lower()
    if target in {"aws", "gcp"}:
        return target
    return None


def _next_approved_cloud_shape(*, provider: str, current_shape: str | None) -> str | None:
    shortlist = approved_cloud_shapes(provider)
    if not shortlist:
        return None
    normalized_current = str(current_shape or "").strip().lower()
    if not normalized_current:
        return shortlist[0].shape
    for index, item in enumerate(shortlist):
        if item.shape.lower() == normalized_current:
            return shortlist[index + 1].shape if index + 1 < len(shortlist) else None
    return shortlist[0].shape


def _extract_multipass_instance_state(payload_text: str) -> str | None:
    try:
        payload = json.loads(payload_text or "{}")
    except json.JSONDecodeError:
        return None
    info = payload.get("info")
    if not isinstance(info, dict):
        return None
    for item in info.values():
        if isinstance(item, dict):
            for key in ("state", "State", "status", "Status"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return None


def _attempt_runtime_auth_repair(
    *,
    repo: RepoCatalogRecord,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    runtime_repair_plan,
    incident,
    execution_target: str,
) -> tuple[str, str | None]:
    if runtime_repair_plan.action_key != "auth_guidance":
        return "not_applicable", None
    row = _resolve_repo_state_from_action_key(paths.config_dir, repo.repo_url)
    metadata = {} if row is None else row.metadata
    missing_auth = tuple(
        str(item).strip()
        for item in metadata.get("missing_auth_variables", ())
        if str(item).strip()
    )
    auth_requirements = tuple(item for item in metadata.get("auth_requirements", ()) if isinstance(item, dict))
    still_missing = tuple(name for name in missing_auth if not os.environ.get(name))
    provider_names = ", ".join(
        sorted(
            {
                str(item.get("provider") or "external").strip()
                for item in auth_requirements
                if str(item.get("env_var") or "").strip() in still_missing
            }
        )
    )
    display_output(
        render_tool_invocation_trace(
            title="auth repair review",
            tool_id="security.auth_repair",
            action=f"Re-check the required auth state for {repo.name}",
            detail_lines=(
                f"Incident category: {incident.category}",
                (f"Still-missing auth: {', '.join(still_missing)}" if still_missing else "Duckln detected that the required auth variables are now present."),
                (f"Providers involved: {provider_names}" if provider_names else None),
                "Duckln will not invent or silently copy credentials, but it can verify whether the required auth is now present before retrying the repo runtime.",
            ),
            execution_target=execution_target,
        )
    )
    if not still_missing:
        message = f"Supervisor agent detected that the required auth for {repo.name} is now present and will retry the bounded runtime check."
        display_output(message)
        return "ready_to_rerun", message
    pieces = [
        runtime_repair_plan.reason,
        incident.summary,
        f"Auth still needed: {', '.join(still_missing)}.",
    ]
    if provider_names:
        pieces.append(f"Providers involved: {provider_names}.")
    pieces.append("Duckln will not silently copy or invent credentials.")
    pieces.append("Set the required credential first, then Duckln can continue the bounded runtime repair.")
    if runtime_repair_plan.verification_hint:
        pieces.append(runtime_repair_plan.verification_hint)
    message = " ".join(piece for piece in pieces if piece)
    display_output(message)
    return "awaiting_user_action", message


def _attempt_runtime_cloud_repair(
    *,
    repo: RepoCatalogRecord,
    display_output: Callable[[str], None],
    runtime_repair_plan,
    incident,
    execution_target: str,
    workflow,
) -> tuple[str, str | None]:
    if runtime_repair_plan.action_key not in {"cloud_auth_guidance", "cloud_capacity_escalation"}:
        return "not_applicable", None
    provider = _runtime_cloud_provider(execution_target=execution_target, workflow=workflow)
    if provider is None:
        message = f"Duckln could not resolve the active cloud provider for {repo.name}, so it cannot continue the cloud repair path yet."
        display_output(message)
        return "awaiting_user_action", message
    display_output(
        render_tool_invocation_trace(
            title="cloud repair review",
            tool_id="cloud.runtime_repair",
            action=f"Inspect the active {provider.upper()} runtime blocker for {repo.name}",
            detail_lines=(
                f"Incident category: {incident.category}",
                f"Route family: {runtime_repair_plan.route_family}",
                f"Cloud provider: {provider.upper()}",
                "Duckln will re-check bounded cloud auth/capacity state before retrying the runtime path.",
            ),
            execution_target=execution_target,
        )
    )
    auth_status = inspect_cloud_auth(provider, runner=ControlledCommandRunner(trace=display_output, execution_target="local"))
    display_output(_render_cloud_auth_status(auth_status))
    if runtime_repair_plan.action_key == "cloud_auth_guidance":
        if auth_status.cli_available and auth_status.authenticated:
            message = f"Supervisor agent confirmed that {provider.upper()} auth is ready again and will retry the bounded runtime path for {repo.name}."
            display_output(message)
            return "ready_to_rerun", message
        message = (
            f"Duckln still needs valid {provider.upper()} authentication before it can continue {repo.name}. "
            f"CLI state: {auth_status.message} Source: [1] {auth_status.source_url}"
        )
        display_output(message)
        return "awaiting_user_action", message

    shortlist = approved_cloud_shapes(provider)
    shortlist_text = ", ".join(f"{item.shape} ({item.label})" for item in shortlist) or "No approved shortlist recorded."
    next_shape = _next_approved_cloud_shape(
        provider=provider,
        current_shape=None if workflow is None else getattr(workflow, "active_runtime_cloud_shape", None),
    )
    message = (
        f"Duckln confirmed the active {provider.upper()} auth state, but {repo.name} is still blocked by cloud quota or capacity. "
        f"Approved shapes: {shortlist_text}. Choose a different approved cloud target or retry later."
    )
    if next_shape:
        message += f" Next approved fallback shape: {next_shape}."
    display_output(message)
    return "awaiting_user_action", message


def _attempt_runtime_vm_repair(
    *,
    repo: RepoCatalogRecord,
    current: AppConfig,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    approve_prompt: Callable[[str], bool] | None,
    runtime_repair_plan,
    incident,
    execution_target: str,
    workflow,
    chat: object | None = None,
) -> tuple[str, str | None]:
    if runtime_repair_plan.action_key != "vm_repair_escalation":
        return "not_applicable", None
    vm_name = None if workflow is None else str(getattr(workflow, "active_runtime_vm_name", "") or "").strip() or None
    display_output(
        render_tool_invocation_trace(
            title="vm repair review",
            tool_id="vm.runtime_repair",
            action=f"Inspect the active VM path for {repo.name}",
            detail_lines=(
                f"Incident category: {incident.category}",
                (f"Tracked VM: {vm_name}" if vm_name else "Tracked VM: unresolved"),
                "Duckln will verify whether the active Multipass VM exists and can accept bounded commands before retrying the repo runtime.",
            ),
            execution_target=execution_target,
        )
    )
    if not is_multipass_installed():
        # Plan 197 F7: detailed step-by-step remediation (not a one-line hint).
        message = render_remediation_steps(
            "multipass", probe_system().operating_system, reason="not_installed", next_action="say 'continue'"
        )
        display_output(message)
        return "awaiting_user_action", message
    if not vm_name:
        message = f"Duckln could not find the active VM name for {repo.name}. Re-open /vm or select the tracked VM once, then Duckln can continue."
        display_output(message)
        return "awaiting_user_action", message
    runner = ControlledCommandRunner(trace=display_output, execution_target="local")
    vm_names = list_multipass_vm_names(runner=runner)
    if vm_name not in vm_names:
        message = f"Duckln could not find the VM {vm_name}. Recreate it through /vm or point Duckln at the right VM before retrying {repo.name}."
        display_output(message)
        return "awaiting_user_action", message
    list_command = "multipass list --format json"
    list_result = runner.run(list_command)
    if list_result.exit_code != 0 or list_result.timed_out:
        message = build_failure_message(
            "Duckln could not inspect the current Multipass inventory",
            list_result.stderr,
            command=list_command,
            trace=display_output,
            execution_target="local",
        )
        return "failed", message
    info_command = f"multipass info {shlex.quote(vm_name)} --format json"
    info_result = runner.run(info_command)
    instance_state = None if info_result.exit_code != 0 or info_result.timed_out else _extract_multipass_instance_state(info_result.stdout)
    probe_command = f"multipass exec {shlex.quote(vm_name)} -- bash -lc 'echo duckln-vm-ready'"
    probe_result = runner.run(probe_command)
    if probe_result.exit_code == 0 and not probe_result.timed_out:
        message = (
            f"Supervisor agent verified the tracked VM {vm_name}"
            + (f" ({instance_state})" if instance_state else "")
            + f" and will retry the bounded runtime path for {repo.name}."
        )
        display_output(message)
        return "ready_to_rerun", message
    auto_approved = _runtime_objective_is_preapproved(workflow, repo_key=repo.repo_url)
    restart_command = f"multipass restart {shlex.quote(vm_name)}"
    if not auto_approved:
        approved = True if approve_prompt is None else bool(
            approve_prompt(
                f"Duckln can restart the tracked VM {vm_name} and probe it before retrying {repo.name}. Proceed?"
            )
        )
        if not approved:
            message = f"Duckln left VM recovery for {repo.name} paused because VM restart approval is still required."
            return "awaiting_user_action", message
    mode_decision = evaluate_mode_action(
        current.mode,
        assess_command(restart_command),
        is_ai_suggested=True,
        user_approved=True,
    )
    if not mode_decision.allowed:
        message = f"Supervisor agent could not auto-run the bounded VM recovery step for {repo.name} in {current.mode.label}."
        display_output(message)
        return "failed", message
    _update_active_runtime_objective(paths, chat=chat, active_repair_phase="runtime_vm_repair_running")
    restart_result = runner.run(restart_command)
    if restart_result.exit_code != 0 or restart_result.timed_out:
        message = build_failure_message("Duckln could not restart the active VM", restart_result.stderr, command=restart_command, trace=display_output, execution_target="local")
        return "failed", message
    info_result = runner.run(info_command)
    instance_state = None if info_result.exit_code != 0 or info_result.timed_out else _extract_multipass_instance_state(info_result.stdout)
    probe_result = runner.run(probe_command)
    if probe_result.exit_code != 0 or probe_result.timed_out:
        message = build_failure_message("Duckln could not verify the VM runtime path", probe_result.stderr, command=probe_command, trace=display_output, execution_target="local")
        return "failed", message
    message = (
        f"Supervisor agent verified the tracked VM {vm_name}"
        + (f" ({instance_state})" if instance_state else "")
        + f" and will retry the bounded runtime path for {repo.name}."
    )
    display_output(message)
    return "ready_to_rerun", message


def _stack_toolchain_probe(
    *,
    specialist_name: str,
    dependency_command: str | None,
) -> tuple[str, str] | None:
    normalized = " ".join(str(dependency_command or "").lower().split())
    if specialist_name == "node":
        if normalized.startswith("pnpm "):
            return "node", "node --version && pnpm --version"
        if normalized.startswith("yarn "):
            return "node", "node --version && yarn --version"
        return "node", "node --version && npm --version"
    if specialist_name == "rust":
        return "rust", "cargo --version && rustc --version"
    if specialist_name == "go":
        return "go", "go version"
    return None


def _stack_dependency_verification_command(
    *,
    specialist_name: str,
    dependency_command: str | None,
    project_dir: Path | None,
) -> tuple[str, str] | None:
    normalized = " ".join(str(dependency_command or "").lower().split())
    if specialist_name == "node":
        if normalized.startswith("pnpm "):
            return "pnpm dependency contract", "pnpm list --depth 0"
        if normalized.startswith("yarn "):
            return "yarn dependency contract", "yarn list --depth=0"
        return "npm dependency contract", "npm ls --depth=0"
    if specialist_name == "rust":
        if project_dir is not None and (project_dir / "Cargo.lock").exists():
            return "cargo dependency contract", "cargo check --locked"
        return "cargo dependency contract", "cargo check"
    if specialist_name == "go":
        return "go module contract", "go build ./..."
    return None


def _attempt_runtime_stack_verification(
    *,
    repo: RepoCatalogRecord,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    runtime_repair_plan,
    execution_target: str,
    project_dir: Path | None,
    runtime_cwd: str | None,
    workflow,
    chat: object | None = None,
) -> str:
    verification = _stack_dependency_verification_command(
        specialist_name=runtime_repair_plan.specialist_name,
        dependency_command=None if runtime_repair_plan.approval_request is None else runtime_repair_plan.approval_request.command,
        project_dir=project_dir,
    )
    if verification is None:
        return "ready_to_rerun"
    verification_label, verification_command = verification
    wrapped_command = verification_command
    if execution_target in {"vm", "aws", "gcp"}:
        resolved_command, _ = _wrap_command_for_execution_target(
            config_dir=paths.config_dir,
            execution_target=execution_target,
            command=verification_command,
            cwd=runtime_cwd,
            preferred_vm_name=None if workflow is None else workflow.active_runtime_vm_name,
            preferred_cloud_resource_key=None if workflow is None else workflow.active_runtime_cloud_resource_key,
            preferred_cloud_vendor=None if workflow is None else workflow.active_runtime_cloud_vendor,
            preferred_cloud_region=None if workflow is None else workflow.active_runtime_cloud_region,
            preferred_cloud_shape=None if workflow is None else workflow.active_runtime_cloud_shape,
        )
        if not resolved_command:
            return "failed"
        wrapped_command = resolved_command
    display_output(
        render_tool_invocation_trace(
            title="stack dependency verification",
            tool_id=f"{runtime_repair_plan.specialist_name}.verification_contract",
            action=f"Verify the {runtime_repair_plan.specialist_name} dependency contract for {repo.name}",
            detail_lines=(
                f"Specialist: {runtime_repair_plan.specialist_name}",
                f"Verification contract: {verification_label}",
                f"Verification command: {verification_command}",
                "Duckln verifies the stack-specific dependency contract before it reruns the tracked repo command.",
            ),
            execution_target=execution_target,
        )
    )
    _update_active_runtime_objective(paths, chat=chat, active_repair_phase=f"{runtime_repair_plan.specialist_name}_dependency_verification")
    runner = ControlledCommandRunner(trace=display_output, execution_target=execution_target)
    result = runner.run(
        wrapped_command,
        cwd=None if execution_target in {"vm", "aws", "gcp"} else runtime_cwd or (None if project_dir is None else str(project_dir)),
    )
    if result.timed_out or result.exit_code != 0:
        failed_incident = summarize_failure_incident(
            command=verification_command,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        display_output(
            f"Supervisor agent could not verify the {runtime_repair_plan.specialist_name} dependency contract for {repo.name}. {failed_incident.summary}"
        )
        return "failed"
    display_output(
        f"Supervisor agent verified the {runtime_repair_plan.specialist_name} dependency contract for {repo.name} and will rerun the tracked runtime now."
    )
    return "ready_to_rerun"


def _python_verification_contract(
    *,
    runtime_repair_plan,
    incident,
) -> tuple[str, str]:
    dependency_command = None if runtime_repair_plan.approval_request is None else runtime_repair_plan.approval_request.command
    interpreter = ".venv/bin/python"
    normalized_command = " ".join(str(dependency_command or "").split())
    if normalized_command and not normalized_command.startswith(".venv/bin/python"):
        interpreter = "python3"
    package_hint = str(getattr(incident, "package_hint", "") or "").strip()
    if package_hint and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", package_hint):
        return f"python import contract ({package_hint})", f'{interpreter} -c "import {package_hint}"'
    return "python dependency contract", f"{interpreter} -m pip check"


def _attempt_runtime_python_executor(
    *,
    repo: RepoCatalogRecord,
    current: AppConfig,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    approve_prompt: Callable[[str], bool] | None,
    runtime_repair_plan,
    incident,
    execution_target: str,
    project_dir: Path | None,
    runtime_cwd: str | None,
    workflow,
    chat: object | None = None,
) -> str:
    if runtime_repair_plan.action_key != "repo_dependency_repair" or runtime_repair_plan.specialist_name != "python":
        return "not_applicable"
    dependency_status = _attempt_runtime_dependency_repair(
        repo=repo,
        current=current,
        paths=paths,
        display_output=display_output,
        approve_prompt=approve_prompt,
        incident=incident,
        execution_target=execution_target,
        project_dir=project_dir,
        runtime_cwd=runtime_cwd,
        workflow=workflow,
        chat=chat,
    )
    if dependency_status == "awaiting_approval":
        return "awaiting_approval"
    if dependency_status not in {"installed", "not_applicable"}:
        return "failed"
    if dependency_status == "not_applicable":
        return "not_applicable"
    verification_label, verification_command = _python_verification_contract(
        runtime_repair_plan=runtime_repair_plan,
        incident=incident,
    )
    wrapped_command = verification_command
    if execution_target in {"vm", "aws", "gcp"}:
        resolved_command, _ = _wrap_command_for_execution_target(
            config_dir=paths.config_dir,
            execution_target=execution_target,
            command=verification_command,
            cwd=runtime_cwd,
            preferred_vm_name=None if workflow is None else workflow.active_runtime_vm_name,
            preferred_cloud_resource_key=None if workflow is None else workflow.active_runtime_cloud_resource_key,
            preferred_cloud_vendor=None if workflow is None else workflow.active_runtime_cloud_vendor,
            preferred_cloud_region=None if workflow is None else workflow.active_runtime_cloud_region,
            preferred_cloud_shape=None if workflow is None else workflow.active_runtime_cloud_shape,
        )
        if not resolved_command:
            return "failed"
        wrapped_command = resolved_command
    display_output(
        render_tool_invocation_trace(
            title="python dependency verification",
            tool_id="python.verification_contract",
            action=f"Verify the Python dependency contract for {repo.name}",
            detail_lines=(
                "Specialist: python",
                f"Verification contract: {verification_label}",
                f"Verification command: {verification_command}",
                "Duckln verifies the Python dependency contract before it reruns the tracked repo command.",
            ),
            execution_target=execution_target,
        )
    )
    _update_active_runtime_objective(paths, chat=chat, active_repair_phase="python_dependency_verification")
    runner = ControlledCommandRunner(trace=display_output, execution_target=execution_target)
    result = runner.run(
        wrapped_command,
        cwd=None if execution_target in {"vm", "aws", "gcp"} else runtime_cwd or (None if project_dir is None else str(project_dir)),
    )
    if result.timed_out or result.exit_code != 0:
        failed_incident = summarize_failure_incident(
            command=verification_command,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        display_output(
            f"Supervisor agent could not verify the Python dependency contract for {repo.name}. {failed_incident.summary}"
        )
        return "failed"
    display_output(
        f"Supervisor agent verified the Python dependency contract for {repo.name} and will rerun the tracked runtime now."
    )
    return "ready_to_rerun"


def _attempt_runtime_stack_executor(
    *,
    repo: RepoCatalogRecord,
    current: AppConfig,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    approve_prompt: Callable[[str], bool] | None,
    runtime_repair_plan,
    incident,
    execution_target: str,
    project_dir: Path | None,
    runtime_cwd: str | None,
    workflow,
    chat: object | None = None,
) -> str:
    if runtime_repair_plan.action_key != "repo_dependency_repair" or runtime_repair_plan.specialist_name not in {"node", "rust", "go"}:
        return "not_applicable"
    probe = _stack_toolchain_probe(
        specialist_name=runtime_repair_plan.specialist_name,
        dependency_command=None if runtime_repair_plan.approval_request is None else runtime_repair_plan.approval_request.command,
    )
    if probe is None:
        return "not_applicable"
    dependency_name, probe_command = probe
    wrapped_probe = probe_command
    if execution_target in {"vm", "aws", "gcp"}:
        resolved_command, _ = _wrap_command_for_execution_target(
            config_dir=paths.config_dir,
            execution_target=execution_target,
            command=probe_command,
            cwd=runtime_cwd,
            preferred_vm_name=None if workflow is None else workflow.active_runtime_vm_name,
            preferred_cloud_resource_key=None if workflow is None else workflow.active_runtime_cloud_resource_key,
            preferred_cloud_vendor=None if workflow is None else workflow.active_runtime_cloud_vendor,
            preferred_cloud_region=None if workflow is None else workflow.active_runtime_cloud_region,
            preferred_cloud_shape=None if workflow is None else workflow.active_runtime_cloud_shape,
        )
        if not resolved_command:
            return "not_applicable"
        wrapped_probe = resolved_command
    display_output(
        render_tool_invocation_trace(
            title="stack toolchain review",
            tool_id=f"{runtime_repair_plan.specialist_name}.toolchain_probe",
            action=f"Verify the {runtime_repair_plan.specialist_name} toolchain before retrying {repo.name}",
            detail_lines=(
                f"Specialist: {runtime_repair_plan.specialist_name}",
                f"Probe command: {probe_command}",
                "Duckln checks the stack toolchain first so it can repair a missing runtime before retrying repo-scoped dependency work.",
            ),
            execution_target=execution_target,
        )
    )
    runner = ControlledCommandRunner(trace=display_output, execution_target=execution_target)
    result = runner.run(
        wrapped_probe,
        cwd=None if execution_target in {"vm", "aws", "gcp"} else runtime_cwd or (None if project_dir is None else str(project_dir)),
    )
    if not result.timed_out and result.exit_code == 0:
        display_output(
            f"Supervisor agent confirmed the {runtime_repair_plan.specialist_name} toolchain is ready and will continue the repo dependency repair for {repo.name}."
        )
    else:
        plan = _build_named_runtime_prerequisite_plan(
            dependency=dependency_name,
            execution_target=execution_target,
            reason=(
                f"Duckln detected that the {runtime_repair_plan.specialist_name} toolchain itself is missing or broken, "
                f"so it should repair that before retrying the repo dependency path for {repo.name}."
            ),
        )
        if plan is None:
            return "failed"
        toolchain_status = _execute_runtime_prerequisite_install(
            repo=repo,
            current=current,
            paths=paths,
            display_output=display_output,
            approve_prompt=approve_prompt,
            plan=plan,
            execution_target=execution_target,
            project_dir=None if project_dir is None else str(project_dir),
            runtime_cwd=runtime_cwd,
            workflow=workflow,
            chat=chat,
            phase_name=f"{runtime_repair_plan.specialist_name}_toolchain_repair_running",
            review_title="stack prerequisite review",
            review_action=f"Repair the {runtime_repair_plan.specialist_name} toolchain before retrying {repo.name}",
        )
        if toolchain_status != "installed":
            return "awaiting_approval" if toolchain_status == "awaiting_approval" else "failed"
    dependency_status = _attempt_runtime_dependency_repair(
        repo=repo,
        current=current,
        paths=paths,
        display_output=display_output,
        approve_prompt=approve_prompt,
        incident=incident,
        execution_target=execution_target,
        project_dir=project_dir,
        runtime_cwd=runtime_cwd,
        workflow=workflow,
        chat=chat,
    )
    if dependency_status == "awaiting_approval":
        return "awaiting_approval"
    if dependency_status not in {"installed", "not_applicable"}:
        return "failed"
    if dependency_status == "not_applicable":
        return "not_applicable"
    return _attempt_runtime_stack_verification(
        repo=repo,
        paths=paths,
        display_output=display_output,
        runtime_repair_plan=runtime_repair_plan,
        execution_target=execution_target,
        project_dir=project_dir,
        runtime_cwd=runtime_cwd,
        workflow=workflow,
        chat=chat,
    )


def _attempt_runtime_cloud_bootstrap_repair(
    *,
    repo: RepoCatalogRecord,
    current: AppConfig,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    approve_prompt: Callable[[str], bool] | None,
    runtime_repair_plan,
    execution_target: str,
    workflow,
    chat: object | None = None,
) -> str:
    if execution_target not in {"aws", "gcp"}:
        return "not_applicable"
    if runtime_repair_plan.action_key not in {"runtime_prerequisite_repair", "repo_dependency_repair", "escalate"}:
        return "not_applicable"
    resource_key = None if workflow is None else str(getattr(workflow, "active_runtime_cloud_resource_key", "") or "").strip() or None
    if not resource_key:
        return "not_applicable"
    record = resolve_managed_resource(paths.config_dir, resource_key=resource_key)
    if record is None:
        return "not_applicable"
    bootstrap_command = build_cloud_remote_exec_command(
        record,
        remote_command="mkdir -p ~/.duckln/projects ~/.duckln/memory/sessions ~/.duckln/memory/knowledge ~/.duckln/memory/skills && python3 --version && git --version",
    )
    if not bootstrap_command:
        return "not_applicable"
    display_output(
        render_tool_invocation_trace(
            title="cloud runtime bootstrap review",
            tool_id=f"cloud.{record.provider}_bootstrap",
            action=f"Repair the bounded cloud runtime path for {repo.name}",
            detail_lines=(
                f"Managed resource: {record.display_name}",
                f"Provider: {record.provider.upper()}",
                "Duckln will verify the tracked cloud VM can execute bounded commands and has the basic runtime folders before retrying repo repair.",
            ),
            execution_target=execution_target,
        )
    )
    auto_approved = _runtime_objective_is_preapproved(workflow, repo_key=repo.repo_url)
    if not auto_approved:
        approved = True if approve_prompt is None else bool(
            approve_prompt(
                f"Duckln can repair the tracked cloud runtime path for {repo.name} by bootstrapping basic Duckln folders on {record.display_name}. Proceed?"
            )
        )
        if not approved:
            display_output(f"Duckln left the cloud runtime bootstrap for {repo.name} paused because approval is still required.")
            return "awaiting_approval"
    mode_decision = evaluate_mode_action(
        current.mode,
        assess_command(bootstrap_command),
        is_ai_suggested=True,
        user_approved=True,
    )
    if not mode_decision.allowed:
        display_output(f"Supervisor agent could not auto-run the bounded cloud bootstrap for {repo.name} in {current.mode.label}.")
        return "blocked"
    _update_active_runtime_objective(paths, chat=chat, active_repair_phase="runtime_cloud_bootstrap_running")
    runner = ControlledCommandRunner(trace=display_output, execution_target="local")
    result = runner.run(bootstrap_command)
    record_managed_resource_activity(paths.config_dir, resource_key=record.resource_key)
    if result.timed_out or result.exit_code != 0:
        failed_incident = summarize_failure_incident(
            command=bootstrap_command,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        display_output(
            f"Supervisor agent could not finish the cloud runtime bootstrap for {repo.name}. {failed_incident.summary}"
        )
        return "failed"
    display_output(
        f"Supervisor agent repaired the tracked cloud runtime path on {record.display_name} and will continue {repo.name}."
    )
    return "bootstrapped"


def _attempt_runtime_cloud_service_repair(
    *,
    repo: RepoCatalogRecord,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    runtime_repair_plan,
    execution_target: str,
    project_dir: Path | None,
    runtime_cwd: str | None,
    workflow,
    chat: object | None = None,
) -> str:
    if execution_target not in {"aws", "gcp"}:
        return "not_applicable"
    if runtime_repair_plan.route_family != "docker_repair":
        return "not_applicable"
    verification_command = "docker --version && docker compose version"
    if project_dir is not None and any((project_dir / name).exists() for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")):
        verification_command = "docker --version && docker compose version && docker compose config >/dev/null"
    wrapped_command = verification_command
    resolved_command, _ = _wrap_command_for_execution_target(
        config_dir=paths.config_dir,
        execution_target=execution_target,
        command=verification_command,
        cwd=runtime_cwd,
        preferred_vm_name=None if workflow is None else workflow.active_runtime_vm_name,
        preferred_cloud_resource_key=None if workflow is None else workflow.active_runtime_cloud_resource_key,
        preferred_cloud_vendor=None if workflow is None else workflow.active_runtime_cloud_vendor,
        preferred_cloud_region=None if workflow is None else workflow.active_runtime_cloud_region,
        preferred_cloud_shape=None if workflow is None else workflow.active_runtime_cloud_shape,
    )
    if not resolved_command:
        return "not_applicable"
    wrapped_command = resolved_command
    display_output(
        render_tool_invocation_trace(
            title="cloud runtime service review",
            tool_id="cloud.runtime_service_probe",
            action=f"Verify the tracked cloud runtime services for {repo.name}",
            detail_lines=(
                f"Route family: {runtime_repair_plan.route_family}",
                f"Verification command: {verification_command}",
                "Duckln checks the remote Docker/runtime service layer before retrying the repo-scoped repair path.",
            ),
            execution_target=execution_target,
        )
    )
    _update_active_runtime_objective(paths, chat=chat, active_repair_phase="runtime_cloud_service_probe")
    runner = ControlledCommandRunner(trace=display_output, execution_target=execution_target)
    result = runner.run(
        wrapped_command,
        cwd=None if execution_target in {"vm", "aws", "gcp"} else runtime_cwd or (None if project_dir is None else str(project_dir)),
    )
    if result.timed_out or result.exit_code != 0:
        failed_incident = summarize_failure_incident(
            command=verification_command,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        display_output(
            f"Supervisor agent could not verify the tracked cloud runtime services for {repo.name}. {failed_incident.summary}"
        )
        return "failed"
    display_output(
        f"Supervisor agent confirmed the tracked cloud Docker/runtime services are ready for {repo.name} and will continue the repo repair path."
    )
    return "ready"


def _attempt_runtime_specialist_executor(
    *,
    repo: RepoCatalogRecord,
    current: AppConfig,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    approve_prompt: Callable[[str], bool] | None,
    runtime_repair_plan,
    incident,
    execution_target: str,
    workflow,
    chat: object | None = None,
) -> tuple[str, str | None]:
    auth_status, auth_message = _attempt_runtime_auth_repair(
        repo=repo,
        paths=paths,
        display_output=display_output,
        runtime_repair_plan=runtime_repair_plan,
        incident=incident,
        execution_target=execution_target,
    )
    if auth_status != "not_applicable":
        return auth_status, auth_message
    cloud_status, cloud_message = _attempt_runtime_cloud_repair(
        repo=repo,
        display_output=display_output,
        runtime_repair_plan=runtime_repair_plan,
        incident=incident,
        execution_target=execution_target,
        workflow=workflow,
    )
    if cloud_status != "not_applicable":
        return cloud_status, cloud_message
    return _attempt_runtime_vm_repair(
        repo=repo,
        current=current,
        paths=paths,
        display_output=display_output,
        approve_prompt=approve_prompt,
        runtime_repair_plan=runtime_repair_plan,
        incident=incident,
        execution_target=execution_target,
        workflow=workflow,
        chat=chat,
    )


def _build_runtime_prerequisite_plan(
    *,
    incident,
    execution_target: str,
) -> RuntimePrerequisitePlan | None:
    tool = str(getattr(incident, "package_hint", None) or "").strip().lower()
    searchable = " ".join(
        part
        for part in (
            tool,
            str(getattr(incident, "fatal_line", None) or ""),
            " ".join(getattr(incident, "relevant_lines", ()) or ()),
        )
        if part
    )
    searchable = _strip_duckln_synthetic_search_text(_normalize_runtime_followup_text(searchable))
    if not searchable:
        return None

    # EACCES/permission-denied on npm global installs is not a "missing node" prerequisite.
    # Let runtime dependency repair / elevated rerun handle it instead of looping on node install.
    if ("permission denied" in searchable or "eacces" in searchable) and "npm install -g" in searchable:
        return None

    # If the blocker is still "missing run command", do not re-open prerequisite install loops.
    if _MISSING_RUN_COMMAND_MARKER in searchable:
        return None
    missing_markers = (
        "command not found",
        "not found",
        "no such file or directory",
        "module not found",
        "cannot find module",
        "unsupported engine",
        "requires node",
        "requires python",
        "requires go",
        "requires rust",
        "requires cmake",
        "requires ffmpeg",
        "ebadengine",
    )
    looks_missing_prereq = any(marker in searchable for marker in missing_markers)
    if not looks_missing_prereq:
        # Avoid repeatedly proposing prerequisite installs for unrelated runtime failures.
        return None
    node_missing_markers = (
        "node: command not found",
        "npm: command not found",
        "node not found",
        "npm not found",
        "requires node",
        "unsupported engine",
        "ebadengine",
        # Ubuntu/Debian's verbose form: "Command 'node' not found, but can be installed with: ..."
        "command 'node' not found",
        "command 'npm' not found",
    )

    if "ffmpeg" in searchable:
        dependency = "ffmpeg"
    elif any(token in searchable for token in ("cmake",)):
        dependency = "cmake"
    elif any(token in searchable for token in ("cargo", "rustc")):
        dependency = "rust"
    elif any(token in searchable for token in ("go", "golang")):
        dependency = "go"
    elif any(marker in searchable for marker in node_missing_markers):
        dependency = "node"
    elif any(token in searchable for token in ("python", "python3", "pip")):
        dependency = "python"
    else:
        return None

    plan = _build_named_runtime_prerequisite_plan(
        dependency=dependency,
        execution_target=execution_target,
        reason=f"Duckln detected that {dependency} is missing from the runtime environment.",
    )
    if plan is None:
        return None
    raw_error_text = "\n".join(
        part
        for part in (
            str(getattr(incident, "fatal_line", None) or ""),
            "\n".join(getattr(incident, "relevant_lines", ()) or ()),
        )
        if part
    )
    hint = parse_install_hint(raw_error_text)
    if hint is not None:
        _pm, hinted_command = hint
        assessment = assess_command(hinted_command)
        if not assessment.blocked:
            plan = replace(
                plan,
                install_command=hinted_command,
                installer=f"os-hint:{_pm}",
                reason=f"{plan.reason} Duckln using the OS install hint: {hinted_command}",
            )
    # Plan 57 Phase 4: enrich the reason line with README-declared prereqs so the
    # user sees all the related tools that may need installation, not just the one
    # in the immediate stderr. Repo-agnostic: works for any repo whose README
    # declares prerequisites in a Requirements / Setup / Prerequisites section.
    readme_extras = _runtime_readme_declared_extras(
        incident=incident, primary_dependency=dependency
    )
    if readme_extras:
        plan = replace(
            plan,
            reason=f"{plan.reason} README also declares: {', '.join(readme_extras)}.",
        )
    return plan


def _runtime_readme_declared_extras(*, incident, primary_dependency: str) -> tuple[str, ...]:
    """Return README-declared prereqs OTHER than the one already being installed.

    Reads the README of the active runtime repo (if accessible via incident's
    project_dir) and extracts known prerequisites, then filters out the one
    Duckln is already proposing to install. Empty tuple when no README or no
    extras. Safe to call repeatedly — purely informational.
    """
    try:
        from pathlib import Path as _Path
        from duckln.readme_skill import extract_readme_metadata
        project_dir_raw = getattr(incident, "project_dir", None) or getattr(incident, "cwd", None)
        if not project_dir_raw:
            return ()
        proj = _Path(str(project_dir_raw))
        readme_path: _Path | None = None
        for name in ("README.md", "README.rst", "README.txt", "README", "readme.md"):
            candidate = proj / name
            if candidate.exists():
                readme_path = candidate
                break
        if readme_path is None:
            return ()
        text = readme_path.read_text(encoding="utf-8", errors="ignore")
        metadata = extract_readme_metadata(text)
        primary = primary_dependency.strip().lower()
        extras = tuple(
            p.name
            for p in metadata.prerequisites
            if p.name.lower() != primary
        )
        return extras
    except Exception:
        return ()


def _build_named_runtime_prerequisite_plan(
    *,
    dependency: str,
    execution_target: str,
    reason: str,
) -> RuntimePrerequisitePlan | None:
    dependency = dependency.strip().lower()
    node_verify = _node_npm_runtime_verification_command()

    local_platform = sys.platform
    if execution_target in {"vm", "aws", "gcp"}:
        catalog = {
            "ffmpeg": ("sudo apt-get update && sudo apt-get install -y ffmpeg", "apt", "https://ffmpeg.org/documentation.html", "ffmpeg -version"),
            "cmake": ("sudo apt-get update && sudo apt-get install -y cmake build-essential", "apt", "https://cmake.org/documentation/", "cmake --version"),
            "rust": ("sudo apt-get update && sudo apt-get install -y cargo rustc", "apt", "https://www.rust-lang.org/tools/install", "cargo --version"),
            "go": ("sudo apt-get update && sudo apt-get install -y golang-go", "apt", "https://go.dev/doc/install", "go version"),
            "node": (_linux_node_prerequisite_install_command(), "nodesource", "https://github.com/nodesource/distributions", node_verify),
            "python": ("sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip", "apt", "https://docs.python.org/3/using/index.html", "python3 -m pip --version"),
        }
    elif local_platform == "darwin":
        catalog = {
            "ffmpeg": ("brew install ffmpeg", "brew", "https://ffmpeg.org/documentation.html", "ffmpeg -version"),
            "cmake": ("brew install cmake", "brew", "https://cmake.org/documentation/", "cmake --version"),
            "rust": ("brew install rust", "brew", "https://www.rust-lang.org/tools/install", "cargo --version"),
            "go": ("brew install go", "brew", "https://go.dev/doc/install", "go version"),
            "node": ("brew install node", "brew", "https://nodejs.org/en/download", node_verify),
            "python": ("brew install python", "brew", "https://docs.python.org/3/using/index.html", "python -m pip --version"),
        }
    elif local_platform.startswith("linux"):
        catalog = {
            "ffmpeg": ("sudo apt-get update && sudo apt-get install -y ffmpeg", "apt", "https://ffmpeg.org/documentation.html", "ffmpeg -version"),
            "cmake": ("sudo apt-get update && sudo apt-get install -y cmake build-essential", "apt", "https://cmake.org/documentation/", "cmake --version"),
            "rust": ("sudo apt-get update && sudo apt-get install -y cargo rustc", "apt", "https://www.rust-lang.org/tools/install", "cargo --version"),
            "go": ("sudo apt-get update && sudo apt-get install -y golang-go", "apt", "https://go.dev/doc/install", "go version"),
            "node": (_linux_node_prerequisite_install_command(), "nodesource", "https://github.com/nodesource/distributions", node_verify),
            "python": ("sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip", "apt", "https://docs.python.org/3/using/index.html", "python3 -m pip --version"),
        }
    elif local_platform.startswith("win"):
        catalog = {
            "ffmpeg": ("winget install Gyan.FFmpeg", "winget", "https://ffmpeg.org/documentation.html", "ffmpeg -version"),
            "cmake": ("winget install Kitware.CMake", "winget", "https://cmake.org/documentation/", "cmake --version"),
            "rust": ("winget install Rustlang.Rustup", "winget", "https://www.rust-lang.org/tools/install", "cargo --version"),
            "go": ("winget install GoLang.Go", "winget", "https://go.dev/doc/install", "go version"),
            "node": ("winget install OpenJS.NodeJS.LTS", "winget", "https://nodejs.org/en/download", node_verify),
            "python": ("winget install Python.Python.3.12", "winget", "https://docs.python.org/3/using/index.html", "python -m pip --version"),
        }
    else:
        return None

    selected = catalog.get(dependency)
    if selected is None:
        return None
    install_command, installer, source_url, verification_command = selected
    return RuntimePrerequisitePlan(
        dependency=dependency,
        install_command=install_command,
        source_url=source_url,
        installer=installer,
        reason=reason,
        execution_target=execution_target,
        verification_command=verification_command,
    )


def _node_npm_runtime_verification_command() -> str:
    return (
        "node -e \"const cp=require('child_process');"
        "cp.execFileSync('npm',['-v'],{stdio:'ignore'});"
        "process.exit(Number(process.versions.node.split('.')[0])>=20?0:1)\""
    )


def _linux_node_prerequisite_install_command() -> str:
    return (
        "if [ -f /etc/os-release ]; then . /etc/os-release; "
        "case \"${ID:-}\" in "
        "ubuntu|debian) curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt-get install -y nodejs ;; "
        "fedora) sudo dnf install -y nodejs npm ;; "
        "rhel|centos|amzn) sudo yum install -y nodejs npm ;; "
        "*) echo \"Duckln: unsupported Linux distro for automatic Node.js install: ${ID:-unknown}\"; exit 3 ;; "
        "esac; else echo \"Duckln: cannot detect Linux distro for Node.js install\"; exit 3; fi"
    )


def _infer_install_verification_command(command: str) -> str | None:
    """Infer a lightweight verification check for common global install commands."""
    normalized = " ".join(str(command or "").strip().split())
    if not normalized:
        return None
    lowered = normalized.lower()
    if "npm install -g" not in lowered:
        return None
    try:
        tokens = shlex.split(normalized)
    except ValueError:
        return None
    package_token = None
    for index, token in enumerate(tokens):
        if token in {"-g", "--global"} and index + 1 < len(tokens):
            package_token = tokens[index + 1]
            break
    if not package_token:
        return None
    package_name = package_token.split("@", 1)[0].strip()
    if package_name.startswith("@") and "/" in package_name:
        package_name = package_name.rsplit("/", 1)[-1]
    package_name = package_name.strip()
    if not package_name:
        return None
    return f"command -v {shlex.quote(package_name)} >/dev/null 2>&1"


def _run_runtime_verification_command(
    *,
    command: str,
    paths: ConfigPaths,
    execution_target: str,
    runtime_cwd: str | None,
    workflow,
    display_output: Callable[[str], None],
) -> bool:
    verification_command = command
    if execution_target in {"vm", "aws", "gcp"}:
        resolved_verification, _ = _wrap_command_for_execution_target(
            config_dir=paths.config_dir,
            execution_target=execution_target,
            command=verification_command,
            cwd=runtime_cwd,
            preferred_vm_name=None if workflow is None else workflow.active_runtime_vm_name,
            preferred_cloud_resource_key=None if workflow is None else workflow.active_runtime_cloud_resource_key,
            preferred_cloud_vendor=None if workflow is None else workflow.active_runtime_cloud_vendor,
            preferred_cloud_region=None if workflow is None else workflow.active_runtime_cloud_region,
            preferred_cloud_shape=None if workflow is None else workflow.active_runtime_cloud_shape,
        )
        if not resolved_verification:
            return False
        verification_command = resolved_verification
    runner = ControlledCommandRunner(trace=display_output, execution_target=execution_target)
    verification = runner.run(
        verification_command,
        cwd=None if execution_target in {"vm", "aws", "gcp"} else runtime_cwd,
    )
    return not verification.timed_out and verification.exit_code == 0


def _runtime_sudo_noninteractive_available(
    *,
    paths: ConfigPaths,
    execution_target: str,
    runtime_cwd: str | None,
    workflow,
    display_output: Callable[[str], None],
) -> bool:
    sudo_check = "sudo -n true"
    if execution_target in {"vm", "aws", "gcp"}:
        resolved_check, _ = _wrap_command_for_execution_target(
            config_dir=paths.config_dir,
            execution_target=execution_target,
            command=sudo_check,
            cwd=runtime_cwd,
            preferred_vm_name=None if workflow is None else workflow.active_runtime_vm_name,
            preferred_cloud_resource_key=None if workflow is None else workflow.active_runtime_cloud_resource_key,
            preferred_cloud_vendor=None if workflow is None else workflow.active_runtime_cloud_vendor,
            preferred_cloud_region=None if workflow is None else workflow.active_runtime_cloud_region,
            preferred_cloud_shape=None if workflow is None else workflow.active_runtime_cloud_shape,
        )
        if not resolved_check:
            return False
        sudo_check = resolved_check
    runner = ControlledCommandRunner(trace=display_output, execution_target=execution_target)
    result = runner.run(
        sudo_check,
        cwd=None if execution_target in {"vm", "aws", "gcp"} else runtime_cwd,
        timeout_seconds=20.0,
    )
    return (not result.timed_out) and result.exit_code == 0


def _approve_runtime_prerequisite_install(
    *,
    repo_name: str,
    project_dir: str | None,
    plan: RuntimePrerequisitePlan,
    approve_prompt: Callable[[str], bool] | None,
) -> DependencyApprovalDecision:
    request = DependencyApprovalRequest(
        prompt=f"Duckln security review for {repo_name}: install the missing prerequisite {plan.dependency}",
        command=plan.install_command,
        project_dir=project_dir,
        manifest_paths=(),
        source_urls=(plan.source_url,),
        items=(
            DependencyApprovalItem(
                item_id=f"prerequisite:{plan.dependency}",
                dependency=plan.dependency,
                version=None,
                reason=plan.reason,
                source_url=plan.source_url,
                installer=plan.installer,
                risk_note="Check the official source and install command before approving.",
                manifest_path=None,
            ),
        ),
    )
    if approve_prompt is None:
        return DependencyApprovalDecision(approved=False, approve_all=False, selected_item_ids=())
    live_approver = getattr(approve_prompt, "approve_dependency_install", None)
    if callable(live_approver):
        decision = live_approver(request)
        if isinstance(decision, DependencyApprovalDecision):
            return decision
        approved = bool(decision)
        return DependencyApprovalDecision(
            approved=approved,
            approve_all=approved,
            selected_item_ids=(f"prerequisite:{plan.dependency}",) if approved else (),
        )
    approved = bool(
        approve_prompt(
            "\n".join(
                (
                    request.prompt,
                    f"Command: {request.command}",
                    f"Official source: {plan.source_url}",
                    "Duckln will not install system prerequisites silently.",
                    "Approve this prerequisite install?",
                )
            )
        )
    )
    return DependencyApprovalDecision(
        approved=approved,
        approve_all=approved,
        selected_item_ids=(f"prerequisite:{plan.dependency}",) if approved else (),
    )


def _execute_runtime_prerequisite_install(
    *,
    repo: RepoCatalogRecord,
    current: AppConfig,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    approve_prompt: Callable[[str], bool] | None,
    plan: RuntimePrerequisitePlan,
    execution_target: str,
    project_dir: str | None,
    runtime_cwd: str | None,
    workflow,
    chat: object | None = None,
    phase_name: str = "runtime_prerequisite_repair_running",
    review_title: str = "runtime prerequisite review",
    review_action: str | None = None,
) -> str:
    wrapped_command = plan.install_command
    if execution_target in {"vm", "aws", "gcp"}:
        resolved_command, _ = _wrap_command_for_execution_target(
            config_dir=paths.config_dir,
            execution_target=execution_target,
            command=plan.install_command,
            cwd=runtime_cwd,
            preferred_vm_name=None if workflow is None else workflow.active_runtime_vm_name,
            preferred_cloud_resource_key=None if workflow is None else workflow.active_runtime_cloud_resource_key,
            preferred_cloud_vendor=None if workflow is None else workflow.active_runtime_cloud_vendor,
            preferred_cloud_region=None if workflow is None else workflow.active_runtime_cloud_region,
            preferred_cloud_shape=None if workflow is None else workflow.active_runtime_cloud_shape,
        )
        if not resolved_command:
            return "not_applicable"
        wrapped_command = resolved_command
    if plan.verification_command and _run_runtime_verification_command(
        command=plan.verification_command,
        paths=paths,
        execution_target=execution_target,
        runtime_cwd=runtime_cwd,
        workflow=workflow,
        display_output=display_output,
    ):
        display_output(
            f"Supervisor agent verified prerequisite {plan.dependency} is already available for {repo.name}; skipping reinstall."
        )
        return "installed"
    display_output(
        render_tool_invocation_trace(
            title=review_title,
            tool_id="security.prerequisite_review",
            action=review_action or f"Review the missing prerequisite install for {repo.name}",
            detail_lines=(
                f"Missing prerequisite: {plan.dependency}",
                f"Installer path: {plan.install_command}",
                "Duckln will use an official source and still require explicit approval before it mutates the environment.",
            ),
            execution_target=execution_target,
            source_urls=(plan.source_url,),
        )
    )
    decision = _approve_runtime_prerequisite_install(
        repo_name=repo.name,
        project_dir=project_dir,
        plan=replace(plan, install_command=wrapped_command),
        approve_prompt=approve_prompt,
    )
    if _runtime_install_is_preapproved(workflow, repo_key=repo.repo_url, command=wrapped_command):
        decision = DependencyApprovalDecision(
            approved=True,
            approve_all=True,
            selected_item_ids=(f"prerequisite:{plan.dependency}",),
        )
    if not decision.approved:
        display_output(
            f"Supervisor agent paused before repairing {repo.name} because approval is still required for the prerequisite install {plan.dependency}."
        )
        return "awaiting_approval"
    if re.search(r"(^|\s)sudo(\s|$)", wrapped_command) and not _runtime_sudo_noninteractive_available(
        paths=paths,
        execution_target=execution_target,
        runtime_cwd=runtime_cwd,
        workflow=workflow,
        display_output=display_output,
    ):
        display_output(
            "Duckln needs interactive sudo access in this environment. "
            f"Run this command in the terminal pane with your sudo password, then tell Duckln to continue:\n{wrapped_command}"
        )
        return "awaiting_approval"
    mode_decision = evaluate_mode_action(
        current.mode,
        assess_command(wrapped_command),
        is_ai_suggested=True,
        user_approved=True,
    )
    if not mode_decision.allowed:
        display_output(f"Supervisor agent could not auto-run the prerequisite install for {plan.dependency} in {current.mode.label}.")
        return "blocked"
    _mark_runtime_install_preapproved(
        paths=paths,
        repo_key=repo.repo_url,
        command=wrapped_command,
    )
    _update_active_runtime_objective(paths, chat=chat, active_repair_phase=phase_name)
    display_output(f"Duckln installing prerequisite: {wrapped_command}")
    runner = ControlledCommandRunner(trace=display_output, execution_target=execution_target)
    result = runner.run(
        wrapped_command,
        cwd=None if execution_target in {"vm", "aws", "gcp"} else runtime_cwd or project_dir,
    )
    if result.timed_out or result.exit_code != 0:
        failed_incident = summarize_failure_incident(
            command=wrapped_command,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        current_fp = fingerprint_stderr(result.stderr or "")
        if _runtime_install_recently_failed_same(
            repo_url=repo.repo_url,
            install_command=wrapped_command,
            current_stderr_fp=current_fp,
            config_dir=paths.config_dir,
            execution_target=execution_target,
        ):
            display_output(
                f"Duckln tried `{wrapped_command}` twice and got the same error both times. "
                f"Stopping the repair loop. Manual fix needed."
            )
            return "stopped_repeated_failure"
        _record_runtime_install_failure(
            repo_url=repo.repo_url,
            install_command=wrapped_command,
            stderr=result.stderr or "",
            config_dir=paths.config_dir,
            execution_target=execution_target,
            exit_code=int(result.exit_code) if result.exit_code is not None else 1,
        )
        # Plan 61 Fix B: when the failure is a recognizable apt-class problem
        # (sudo password, network, locale, lock file), surface OS-specific
        # guidance to the user before returning. Repo-agnostic — works for
        # any apt-driven install.
        try:
            from duckln.repair_intake import classify_apt_failure, apt_failure_guidance
            apt_category = classify_apt_failure(result.stderr or "")
            guidance = apt_failure_guidance(apt_category)
            if guidance:
                display_output(guidance)
        except Exception:
            pass
        display_output(
            f"Supervisor agent could not finish installing {plan.dependency}. {failed_incident.summary}"
        )
        return "failed"
    if plan.verification_command:
        verification_command = plan.verification_command
        if execution_target in {"vm", "aws", "gcp"}:
            resolved_verification, _ = _wrap_command_for_execution_target(
                config_dir=paths.config_dir,
                execution_target=execution_target,
                command=verification_command,
                cwd=runtime_cwd,
                preferred_vm_name=None if workflow is None else workflow.active_runtime_vm_name,
                preferred_cloud_resource_key=None if workflow is None else workflow.active_runtime_cloud_resource_key,
                preferred_cloud_vendor=None if workflow is None else workflow.active_runtime_cloud_vendor,
                preferred_cloud_region=None if workflow is None else workflow.active_runtime_cloud_region,
                preferred_cloud_shape=None if workflow is None else workflow.active_runtime_cloud_shape,
            )
            if not resolved_verification:
                return "failed"
            verification_command = resolved_verification
        verification = runner.run(
            verification_command,
            cwd=None if execution_target in {"vm", "aws", "gcp"} else runtime_cwd or project_dir,
        )
        if verification.timed_out or verification.exit_code != 0:
            failed_incident = summarize_failure_incident(
                command=verification_command,
                stdout=verification.stdout,
                stderr=verification.stderr,
            )
            current_fp = fingerprint_stderr(verification.stderr or "")
            if _runtime_install_recently_failed_same(
                repo_url=repo.repo_url,
                install_command=wrapped_command,
                current_stderr_fp=current_fp,
            ):
                display_output(
                    f"Duckln tried `{wrapped_command}` twice and the verification still failed identically. "
                    f"Stopping the repair loop."
                )
                return "stopped_repeated_failure"
            _record_runtime_install_failure(
                repo_url=repo.repo_url,
                install_command=wrapped_command,
                stderr=verification.stderr or "",
                config_dir=paths.config_dir,
                execution_target=execution_target,
                exit_code=int(verification.exit_code) if verification.exit_code is not None else 1,
            )
            display_output(
                f"Supervisor agent installed {plan.dependency}, but verification still failed. {failed_incident.summary}"
            )
            return "failed"
    _clear_runtime_install_failure(repo_url=repo.repo_url, install_command=wrapped_command)
    display_output(
        f"Supervisor agent installed the missing prerequisite {plan.dependency} and will re-check {repo.name} now."
    )
    return "installed"


def _approve_runtime_dependency_install(
    *,
    request: DependencyApprovalRequest,
    approve_prompt: Callable[[str], bool] | None,
) -> DependencyApprovalDecision:
    if approve_prompt is None:
        return DependencyApprovalDecision(approved=False, approve_all=False, selected_item_ids=())
    live_approver = getattr(approve_prompt, "approve_dependency_install", None)
    if callable(live_approver):
        decision = live_approver(request)
        if isinstance(decision, DependencyApprovalDecision):
            return decision
        approved = bool(decision)
        return DependencyApprovalDecision(
            approved=approved,
            approve_all=approved,
            selected_item_ids=tuple(item.item_id for item in request.items) if approved else (),
        )
    prompt_lines = [
        request.prompt,
        f"Command: {request.command}",
        "Duckln will not install packages silently.",
    ]
    if request.manifest_paths:
        prompt_lines.append(f"Repo dependency evidence: {', '.join(request.manifest_paths)}")
    if request.source_urls:
        prompt_lines.append(f"Official source: {request.source_urls[0]}")
    prompt_lines.extend(render_dependency_approval_lines(request)[2:8])
    prompt_lines.append("Approve this dependency install?")
    approved = bool(approve_prompt("\n".join(prompt_lines)))
    return DependencyApprovalDecision(
        approved=approved,
        approve_all=approved,
        selected_item_ids=tuple(item.item_id for item in request.items) if approved else (),
    )


def _attempt_runtime_dependency_repair(
    *,
    repo: RepoCatalogRecord,
    current: AppConfig,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    approve_prompt: Callable[[str], bool] | None,
    incident,
    execution_target: str,
    project_dir: Path | None,
    runtime_cwd: str | None,
    workflow,
    chat: object | None = None,
) -> str:
    plan = plan_runtime_repair(
        repo_name=repo.name,
        project_dir=project_dir,
        incident=incident,
    )
    if plan.action_key != "repo_dependency_repair" or plan.approval_request is None:
        return "not_applicable"

    request = plan.approval_request
    wrapped_command = request.command
    if execution_target in {"vm", "aws", "gcp"}:
        resolved_command, _ = _wrap_command_for_execution_target(
            config_dir=paths.config_dir,
            execution_target=execution_target,
            command=request.command,
            cwd=runtime_cwd,
            preferred_vm_name=None if workflow is None else workflow.active_runtime_vm_name,
            preferred_cloud_resource_key=None if workflow is None else workflow.active_runtime_cloud_resource_key,
            preferred_cloud_vendor=None if workflow is None else workflow.active_runtime_cloud_vendor,
            preferred_cloud_region=None if workflow is None else workflow.active_runtime_cloud_region,
            preferred_cloud_shape=None if workflow is None else workflow.active_runtime_cloud_shape,
        )
        if not resolved_command:
            return "not_applicable"
        wrapped_command = resolved_command
    request = replace(request, command=wrapped_command)
    inferred_verification = _infer_install_verification_command(request.command)
    if inferred_verification and _run_runtime_verification_command(
        command=inferred_verification,
        paths=paths,
        execution_target=execution_target,
        runtime_cwd=runtime_cwd,
        workflow=workflow,
        display_output=display_output,
    ):
        display_output(
            f"Supervisor agent verified the requested install command is already satisfied for {repo.name}; skipping duplicate install."
        )
        return "installed"

    detail_lines = [
        f"Repair reason: {plan.reason}",
        f"Planned repair command: {request.command}",
        "Duckln chose the smallest repo-scoped dependency repair from the repo manifests before escalating.",
    ]
    if request.manifest_paths:
        detail_lines.append(f"Repo dependency evidence: {', '.join(request.manifest_paths)}")
    if request.items:
        preview = ", ".join(item.dependency for item in request.items[:4])
        detail_lines.append(f"Dependency preview: {preview}")
    display_output(
        render_tool_invocation_trace(
            title="runtime dependency repair review",
            tool_id="shell.command_runner",
            action=f"Review the smallest repo-scoped dependency repair for {repo.name}",
            detail_lines=tuple(detail_lines),
            execution_target=execution_target,
            source_urls=request.source_urls[:1],
        )
    )

    write_workflow_state(
        paths.config_dir,
        {
            "active_repo_key": repo.repo_url,
            "active_repo_name": repo.name,
            "active_issue_kind": "run_issue",
            "active_issue_summary": f"Duckln paused before repairing {repo.name} because dependency approval is still required.",
            "active_incident_category": incident.category,
            "active_incident_summary": incident.summary,
            "active_repair_phase": "awaiting_runtime_dependency_approval",
        },
    )
    _update_active_runtime_objective(paths, chat=chat, active_repair_phase="awaiting_runtime_dependency_approval")

    decision = _approve_runtime_dependency_install(
        request=request,
        approve_prompt=approve_prompt,
    )
    if _runtime_install_is_preapproved(workflow, repo_key=repo.repo_url, command=request.command):
        decision = DependencyApprovalDecision(
            approved=True,
            approve_all=True,
            selected_item_ids=tuple(item.item_id for item in request.items),
        )
    if not decision.approved:
        _persist_runtime_repair_followup(paths=paths, repo_key=repo.repo_url, repo_name=repo.name)
        display_output(
            f"Supervisor agent paused before repairing {repo.name} because approval is still required for the repo dependency install."
        )
        return "awaiting_approval"

    effective_command = build_approved_dependency_command(
        request=request,
        decision=decision,
    )
    if effective_command is None:
        _persist_runtime_repair_followup(paths=paths, repo_key=repo.repo_url, repo_name=repo.name)
        display_output(
            f"Supervisor agent could not materialize the approved dependency repair command for {repo.name}."
        )
        return "blocked"
    if re.search(r"(^|\s)sudo(\s|$)", effective_command) and not _runtime_sudo_noninteractive_available(
        paths=paths,
        execution_target=execution_target,
        runtime_cwd=runtime_cwd,
        workflow=workflow,
        display_output=display_output,
    ):
        _persist_runtime_repair_followup(paths=paths, repo_key=repo.repo_url, repo_name=repo.name)
        display_output(
            "Duckln needs interactive sudo access in this environment. "
            f"Run this command in the terminal pane with your sudo password, then tell Duckln to continue:\n{effective_command}"
        )
        return "awaiting_approval"

    mode_decision = evaluate_mode_action(
        current.mode,
        assess_command(effective_command),
        is_ai_suggested=True,
        user_approved=True,
    )
    if not mode_decision.allowed:
        _persist_runtime_repair_followup(paths=paths, repo_key=repo.repo_url, repo_name=repo.name)
        display_output(
            f"Supervisor agent could not auto-run the repo dependency repair for {repo.name} in {current.mode.label}."
        )
        return "blocked"
    _mark_runtime_install_preapproved(
        paths=paths,
        repo_key=repo.repo_url,
        command=effective_command,
    )

    write_workflow_state(
        paths.config_dir,
        {
            "active_repair_phase": "runtime_dependency_repair_running",
            "active_issue_summary": f"Duckln is retrying the smallest repo dependency repair for {repo.name}.",
        },
    )
    _update_active_runtime_objective(paths, chat=chat, active_repair_phase="runtime_dependency_repair_running")

    runner = ControlledCommandRunner(trace=display_output, execution_target=execution_target)
    result = runner.run(
        effective_command,
        cwd=None if execution_target in {"vm", "aws", "gcp"} else runtime_cwd or (None if project_dir is None else str(project_dir)),
    )
    if result.timed_out or result.exit_code != 0:
        failed_incident = summarize_failure_incident(
            command=effective_command,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        display_output(
            f"Supervisor agent could not finish the repo dependency repair for {repo.name}. {failed_incident.summary}"
        )
        return "failed"

    display_output(
        f"Supervisor agent repaired the repo dependencies for {repo.name} and will re-check the runtime now."
    )
    return "installed"


def _attempt_runtime_docker_env_repair(
    *,
    repo: RepoCatalogRecord,
    current: AppConfig,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    approve_prompt: Callable[[str], bool] | None,
    runtime_repair_plan,
    execution_target: str,
    project_dir: Path | None,
    runtime_cwd: str | None,
    workflow,
    chat: object | None = None,
) -> str:
    if runtime_repair_plan.action_key != "docker_env_repair" or not runtime_repair_plan.repair_command:
        return "not_applicable"
    if execution_target != "local":
        return "not_applicable"
    detail_lines = [
        f"Repair reason: {runtime_repair_plan.reason}",
        f"Repair command: {runtime_repair_plan.repair_command}",
        "Duckln will create repo-local managed directories for the missing compose environment variables and retry the compose path.",
    ]
    if runtime_repair_plan.repair_env_vars:
        preview = ", ".join(f"{name}={value}" for name, value in runtime_repair_plan.repair_env_vars[:3])
        detail_lines.append(f"Managed env vars: {preview}")
    display_output(
        render_tool_invocation_trace(
            title="docker env repair review",
            tool_id="process.compose_env_repair",
            action=f"Repair the Docker compose environment for {repo.name}",
            detail_lines=tuple(detail_lines),
            execution_target=execution_target,
        )
    )
    auto_approved = _runtime_objective_is_preapproved(workflow, repo_key=repo.repo_url)
    if not auto_approved:
        prompt = (
            f"Duckln found missing Docker compose environment variables for {repo.name}. "
            "Duckln can create managed local directories and retry the compose command now. Proceed?"
        )
        approved = True if approve_prompt is None else bool(approve_prompt(prompt))
        if not approved:
            _persist_runtime_repair_followup(paths=paths, repo_key=repo.repo_url, repo_name=repo.name)
            _update_active_runtime_objective(
                paths,
                chat=chat,
                active_objective_status="needs_user_decision",
                active_objective_requires_user_decision=True,
                active_repair_phase="awaiting_runtime_docker_env_approval",
            )
            return "awaiting_approval"
    for directory in runtime_repair_plan.repair_directories:
        Path(directory).mkdir(parents=True, exist_ok=True)
    mode_decision = evaluate_mode_action(
        current.mode,
        assess_command(runtime_repair_plan.repair_command),
        is_ai_suggested=True,
        user_approved=True,
    )
    if not mode_decision.allowed:
        return "blocked"
    _update_active_runtime_objective(paths, chat=chat, active_repair_phase="runtime_docker_env_repair_running")
    runner = ControlledCommandRunner(trace=display_output, execution_target=execution_target)
    result = runner.run(
        runtime_repair_plan.repair_command,
        cwd=runtime_cwd or (None if project_dir is None else str(project_dir)),
    )
    if result.timed_out or result.exit_code != 0:
        failed_incident = summarize_failure_incident(
            command=runtime_repair_plan.repair_command,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        display_output(
            f"Supervisor agent could not finish the Docker environment repair for {repo.name}. {failed_incident.summary}"
        )
        return "failed"
    display_output(
        f"Supervisor agent repaired the Docker compose environment for {repo.name} and will re-check the runtime now."
    )
    return "installed"


def _attempt_runtime_prerequisite_install(
    *,
    repo: RepoCatalogRecord,
    current: AppConfig,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    approve_prompt: Callable[[str], bool] | None,
    incident,
    execution_target: str,
    project_dir: str | None,
    runtime_cwd: str | None,
    workflow,
    chat: object | None = None,
) -> str:
    plan = _build_runtime_prerequisite_plan(incident=incident, execution_target=execution_target)
    if plan is None:
        return "not_applicable"
    return _execute_runtime_prerequisite_install(
        repo=repo,
        current=current,
        paths=paths,
        display_output=display_output,
        approve_prompt=approve_prompt,
        plan=plan,
        execution_target=execution_target,
        project_dir=project_dir,
        runtime_cwd=runtime_cwd,
        workflow=workflow,
        chat=chat,
    )


_MISSING_RUN_COMMAND_MARKER = "does not have a reliable run command"

_INSTALL_OR_SETUP_COMMAND_MARKERS = (
    " pip install ",
    " python -m pip install ",
    " python3 -m pip install ",
    " npm install ",
    " npm ci ",
    " pnpm install ",
    " pnpm add ",
    " yarn install ",
    " yarn add ",
    " bun install ",
    " apt install ",
    " apt-get install ",
    " dnf install ",
    " yum install ",
    " brew install ",
    " snap install ",
    " cargo build ",
    " cargo fetch ",
    " go mod download ",
    " go build ",
    " docker build ",
    " docker compose build ",
)


def _looks_like_install_or_setup_command(command: str | None) -> bool:
    if not isinstance(command, str) or not command.strip():
        return False
    normalized = f" {' '.join(command.casefold().split())} "
    return any(marker in normalized for marker in _INSTALL_OR_SETUP_COMMAND_MARKERS)


def _effective_runtime_command(
    *,
    runtime_command_override: str | None,
    workflow,
) -> str | None:
    candidates = [
        runtime_command_override,
        None if workflow is None else getattr(workflow, "active_runtime_command", None),
    ]
    for candidate in candidates:
        text = str(candidate or "").strip()
        if not text:
            continue
        if _looks_like_install_or_setup_command(text):
            continue
        if "<" in text and ">" in text:
            continue
        return text
    return None


def _runtime_error_text_for_search(*, failure_summary: str | None, workflow) -> str:
    """Build the concise exact-error text Duckln should send to web search."""

    parts: list[str] = []
    for value in (
        failure_summary,
        None if workflow is None else getattr(workflow, "active_incident_summary", None),
        None if workflow is None else getattr(workflow, "active_issue_summary", None),
        None if workflow is None else getattr(workflow, "active_objective_last_blocker", None),
    ):
        cleaned = _redact_network_identifiers(_ANSI_ESCAPE_PATTERN.sub("", str(value or "")).strip())
        if cleaned and cleaned not in parts:
            parts.append(cleaned)
    generic_markers = (
        _MISSING_RUN_COMMAND_MARKER,
        "duckln did not find a reliable run command",
        "supervisor agent does not have a reliable run command",
        "duckln classified this blocker as",
    )
    prioritized = [_strip_duckln_synthetic_search_text(part) for part in parts]
    prioritized = [
        part
        for part in prioritized
        if part and not any(marker in part.casefold() for marker in generic_markers)
    ]
    if prioritized:
        return "\n".join(prioritized[:4]).strip()

    # If everything is classifier text, salvage a real terminal line.
    import re

    for part in parts:
        match = re.search(r"fatal line:\s*(.+)$", part, flags=re.IGNORECASE)
        if match is not None:
            fatal_line = _ANSI_ESCAPE_PATTERN.sub("", match.group(1)).strip()
            if fatal_line and _strip_duckln_synthetic_search_text(fatal_line):
                return fatal_line
    return "\n".join(parts[:2]).strip()


def _strip_duckln_synthetic_search_text(text: str) -> str:
    synthetic_markers = (
        "duckln classified",
        "specialist route:",
        "toolchain:",
        "duckln checked",
        _MISSING_RUN_COMMAND_MARKER,
        "supervisor agent does not have a reliable run command",
    )
    kept: list[str] = []
    for raw_line in str(text or "").splitlines():
        line = _ANSI_ESCAPE_PATTERN.sub("", raw_line).strip()
        lowered = line.casefold()
        if not line or any(marker in lowered for marker in synthetic_markers):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _redact_network_identifiers(text: str) -> str:
    scrubbed = str(text or "")
    scrubbed = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "[redacted-ipv4]", scrubbed)
    scrubbed = re.sub(r"\b(?:[0-9a-fA-F]{1,4}:){2,}[0-9a-fA-F]{1,4}\b", "[redacted-ipv6]", scrubbed)
    scrubbed = re.sub(r"\b[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}\b", "[redacted-mac]", scrubbed)
    return scrubbed


def _terminal_incident_search_text(incident: dict[str, object], *, fallback_summary: str) -> str:
    """Keep the real terminal error line available for exact-error search."""

    pieces: list[str] = []
    for value in (
        incident.get("fatal_line"),
        *((incident.get("relevant_lines") or ()) if isinstance(incident.get("relevant_lines"), (tuple, list)) else ()),
        fallback_summary,
    ):
        cleaned = _strip_duckln_synthetic_search_text(
            _redact_network_identifiers(_ANSI_ESCAPE_PATTERN.sub("", str(value or "")).strip())
        )
        if cleaned and cleaned not in pieces:
            pieces.append(cleaned)
    return "\n".join(pieces[:5]) or _redact_network_identifiers(fallback_summary)


_RUN_COMMAND_DISCOVERY_WEB_BUDGET_SECONDS = 30


def _surface_package_json_run_command(project_dir: "Path") -> str | None:
    """Return a candidate `npm run <script>` command derived directly from package.json.

    Skips web search entirely for the common Node.js case where the repo
    declares dev/start/serve/preview scripts. Returns None if no package.json
    or no recognized script is present.
    """
    pkg_path = project_dir / "package.json"
    if not pkg_path.exists():
        return None
    try:
        payload = json.loads(pkg_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    scripts = payload.get("scripts")
    if not isinstance(scripts, dict):
        return None
    for candidate in ("dev", "start", "serve", "preview"):
        value = scripts.get(candidate)
        if isinstance(value, str) and value.strip():
            return f"npm run {candidate}"
    return None


def _discover_repo_run_command(
    *,
    repo: RepoCatalogRecord,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    approve_prompt: Callable[[str], bool] | None,
    terminal_interface: object | None,
    execution_target: str,
    failure_summary: str | None = None,
    workflow=None,
) -> None:
    """Handle the case where Duckln has no stored run command for a repo.

    1. Scans the cloned README for candidate entry-point commands.
    2. Surfaces `package.json` scripts directly (no web search) when present.
    3. If still no candidate and a failure exists, searches focused web queries
       inside a 30 s aggregate budget.
    4. Shows OS/VM-specific steps with source [1] and lets the user edit before executing.
    Approved commands are stored as active_runtime_command so future turns use them."""
    project_dir = resolve_managed_project_dir(paths.config_dir, repo)
    readme_candidates = scan_readme_for_run_commands(
        project_dir,
        repo_name=repo.name,
        execution_target=execution_target,
    )
    if not readme_candidates and execution_target != "local":
        readme_candidates = scan_remote_readme_for_run_commands(
            repo.repo_url,
            repo_name=repo.name,
            execution_target=execution_target,
        )
    package_json_candidate = _surface_package_json_run_command(project_dir)
    target_label = "Ubuntu VM" if execution_target == "vm" else execution_target.replace("_", " ").title()
    search_error_text = _runtime_error_text_for_search(failure_summary=failure_summary, workflow=workflow)
    failing_command = _effective_runtime_command(
        runtime_command_override=None,
        workflow=workflow,
    ) or repo.name
    # Two independent decisions:
    #  - exact-error search: trigger whenever there is a real terminal error.
    #    The user wants to know how to *fix* that error, regardless of whether
    #    a run command can already be inferred from README/package.json.
    #  - run-command/install search: only when we have NO local candidate.
    #    If README or package.json already surfaces a run command, the extra
    #    web searches are pure latency.
    have_local_candidate = bool(readme_candidates) or bool(package_json_candidate)
    needs_exact_error_search = bool(search_error_text)
    needs_run_command_search = not have_local_candidate
    discovery_started = time.monotonic()

    def _web_budget_expired() -> bool:
        return time.monotonic() - discovery_started > _RUN_COMMAND_DISCOVERY_WEB_BUDGET_SECONDS

    _empty_evidence = RuntimeRepairEvidence(note=None, source_urls=(), search_query=None)
    exact_error_evidence = (
        gather_runtime_repair_evidence(
            command=failing_command,
            error_text=search_error_text,
            fetch_live=True,
            trace=display_output,
            execution_target=execution_target,
            os_hint=_runtime_search_os_hint(execution_target=execution_target),
        )
        if needs_exact_error_search and not _web_budget_expired()
        else _empty_evidence
    )
    run_command_evidence = (
        search_for_run_command_hint(
            repo.name,
            repo_url=repo.repo_url,
            trace=display_output,
            execution_target=execution_target,
        )
        if needs_run_command_search and not _web_budget_expired()
        else _empty_evidence
    )
    install_evidence = (
        search_for_repo_install_hint(
            repo.name,
            repo_url=repo.repo_url,
            trace=display_output,
            execution_target=execution_target,
            os_hint=_runtime_search_os_hint(execution_target=execution_target),
        )
        if needs_run_command_search and not _web_budget_expired()
        else _empty_evidence
    )
    if (needs_exact_error_search or needs_run_command_search) and _web_budget_expired():
        display_output(
            f"Duckln capped run-command web discovery at {_RUN_COMMAND_DISCOVERY_WEB_BUDGET_SECONDS}s and moved on."
        )
    evidence_notes = tuple(
        note
        for note in (exact_error_evidence.note, run_command_evidence.note, install_evidence.note)
        if note
    )
    evidence_source_urls = tuple(
        dict.fromkeys(
            (
                *exact_error_evidence.source_urls,
                *run_command_evidence.source_urls,
                *install_evidence.source_urls,
            )
        )
    )
    lines: list[str] = [
        f"Duckln has no stored run command for {repo.name}.",
        f"Target environment: {target_label}.",
        "Searching the exact terminal error in DuckDuckGo, then checking README and trusted public sources for the entry point.",
    ]
    if exact_error_evidence.search_query:
        lines.append(f"DuckDuckGo exact-error search query: {exact_error_evidence.search_query}")
    if install_evidence.search_query:
        lines.append(f"Direct install search query: {install_evidence.search_query}")
    if readme_candidates:
        lines.append(f"README candidate: `{readme_candidates[0]}`")
    if evidence_source_urls:
        lines.append("Sources checked: " + ", ".join(evidence_source_urls[:3]))
    display_output(join_blocks(*lines))

    # Determine the top candidate command to offer as editable default.
    top_cmd = readme_candidates[0] if readme_candidates else None
    if _looks_like_install_or_setup_command(top_cmd):
        top_cmd = None
    # Prefer a package.json script when the README didn't yield a clean run command.
    if top_cmd is None and package_json_candidate:
        top_cmd = package_json_candidate
    if top_cmd is None and evidence_source_urls:
        # Try to extract a runtime/start command from evidence notes.
        joined_note = " ".join(evidence_notes)
        for raw_line in joined_note.splitlines():
            candidate = raw_line.strip().strip("`").strip()
            if not candidate:
                continue
            if _looks_like_install_or_setup_command(candidate):
                continue
            if classify_runtime_command(candidate) == "start":
                top_cmd = candidate.rstrip(".,;:")
                break

    has_prompt_text = (
        terminal_interface is not None
        and hasattr(terminal_interface, "prompt_text")
        and getattr(terminal_interface, "supports_live", False)
    )
    has_select_choice = (
        terminal_interface is not None
        and hasattr(terminal_interface, "select_choice")
        and getattr(terminal_interface, "supports_live", False)
    )

    def _store_run_command(chosen_cmd: str) -> bool:
        if _looks_like_install_or_setup_command(chosen_cmd):
            display_output(
                f"Duckln did not store `{chosen_cmd}` because it is an install/setup command, not a runtime start command."
            )
            display_output(
                f"Give Duckln the exact start command for {repo.name} (for example: `python app.py`, `npm run dev`, or the README run command)."
            )
            _pause_run_command_choice()
            return False
        command_kind = classify_runtime_command(chosen_cmd)
        write_workflow_state(
            paths.config_dir,
            {
                "active_repo_key": repo.repo_url,
                "active_repo_name": repo.name,
                "active_runtime_repo_key": repo.repo_url,
                "active_runtime_repo_name": repo.name,
                "active_runtime_command": chosen_cmd,
                "active_runtime_command_kind": command_kind,
                "active_runtime_execution_target": execution_target,
                "active_runtime_cwd": str(project_dir),
                "active_repair_phase": "awaiting_repair_retry",
                "active_objective_requires_user_decision": False,
            },
        )
        _update_active_runtime_objective(paths, chat=terminal_interface, active_repair_phase="awaiting_repair_retry")
        display_output(
            f"Duckln stored `{chosen_cmd}` as the run command for {repo.name}. "
            "Type a run instruction (or /repos) when you're ready to try it."
        )
        return True

    def _pause_run_command_choice() -> None:
        _update_active_runtime_objective(
            paths,
            chat=terminal_interface,
            active_objective_status="needs_user_decision",
            active_objective_requires_user_decision=True,
            active_repair_phase="guidance_only",
        )
        display_output(
            f"Duckln paused on {repo.name}. Give Duckln the exact run command or correction when you're ready, and it will continue from here."
        )

    # Plan 186 F2c: plain, situation-aware guidance — Duckln is set up and just needs to
    # know HOW to start. Clear options, no "post specialist rerun / attempt 0/5" jargon.
    if has_select_choice and top_cmd:
        _clear_chat_activity(terminal_interface)
        run_label = f"Run this: {top_cmd}"
        write_label = "Let me type the run command"
        skip_label = "Skip running for now"
        cancel_label = "Cancel"
        chosen_label = terminal_interface.select_choice(
            f"Good news — {repo.name} is set up. I'm just not sure how to START it. "
            "I found a likely command — what would you like to do?",
            (run_label, write_label, skip_label, cancel_label),
        )
        if chosen_label == run_label:
            _store_run_command(top_cmd)
            return
        if chosen_label == write_label and has_prompt_text:
            edited_cmd = terminal_interface.prompt_text(
                f"Type the command to start {repo.name}:",
                default=top_cmd,
                help_text="Enter the exact command Duckln should run to start this repo.",
            )
            if edited_cmd and edited_cmd.strip():
                _store_run_command(edited_cmd.strip())
                return
        _pause_run_command_choice()
        return
    if has_select_choice and has_prompt_text:
        _clear_chat_activity(terminal_interface)
        write_label = "Let me type the run command"
        keep_looking_label = "Keep looking for how to run it"
        skip_label = "Skip running for now"
        chosen_label = terminal_interface.select_choice(
            f"Good news — {repo.name} is set up. I'm just not sure how to START it "
            "(no run script found, or it looks like a desktop app). What should I do?",
            (write_label, keep_looking_label, skip_label),
        )
        if chosen_label == keep_looking_label:
            write_workflow_state(
                paths.config_dir,
                {
                    "active_repo_key": repo.repo_url,
                    "active_repo_name": repo.name,
                    "active_runtime_repo_key": repo.repo_url,
                    "active_runtime_repo_name": repo.name,
                    "active_issue_kind": "run_issue",
                    "active_issue_summary": search_error_text
                    or f"Duckln still needs a reliable run command for {repo.name}.",
                    "active_repair_phase": "exact_error_research_complete",
                    "active_objective_status": "needs_user_decision",
                    "active_objective_requires_user_decision": True,
                },
            )
            _update_active_runtime_objective(
                paths,
                chat=terminal_interface,
                active_objective_status="needs_user_decision",
                active_objective_requires_user_decision=True,
                active_repair_phase="exact_error_research_complete",
            )
            display_output(
                f"Okay — I'll keep looking for a safe way to start {repo.name}. "
                "I won't run anything until I'm confident, or until you give me the command."
            )
            return
        if chosen_label == write_label:
            edited_cmd = terminal_interface.prompt_text(
                f"Type the command to start {repo.name}:",
                default="",
                help_text="Enter the exact command Duckln should run to start this repo.",
            )
            if edited_cmd and edited_cmd.strip():
                _store_run_command(edited_cmd.strip())
                return
        _pause_run_command_choice()
        return
    if has_prompt_text:
        help_lines = [f"Edit or confirm the run command for {repo.name} on {target_label}."]
        if evidence_source_urls:
            help_lines.append(f"Source: [1] {evidence_source_urls[0]}")
        edited_cmd = terminal_interface.prompt_text(
            f"Write run instructions for {repo.name}:",
            default=top_cmd or "",
            help_text=" ".join(help_lines),
        )
        if edited_cmd and edited_cmd.strip():
            _store_run_command(edited_cmd.strip())
            return
    elif has_select_choice:
        # Fallback for environments without prompt_text: show picker
        _clear_chat_activity(terminal_interface)
        command_options: list[tuple[str, str]] = []
        for cmd in readme_candidates[:3]:
            command_options.append((f"Run with: `{cmd}`", cmd))
        if command_options:
            choice_labels = tuple(label for label, _ in command_options) + (
                f"I'll tell Duckln the correct run command for {repo.name}",
                f"Pause — I'll investigate {repo.name} myself",
            )
            chosen_label = terminal_interface.select_choice(
                f"What is the run command for {repo.name} on {target_label}?",
                choice_labels,
            )
            chosen_cmd = None
            for label, cmd in command_options:
                if label == chosen_label:
                    chosen_cmd = cmd
                    break
            if chosen_cmd:
                write_workflow_state(
                    paths.config_dir,
                    {
                        "active_repo_key": repo.repo_url,
                        "active_repo_name": repo.name,
                        "active_runtime_repo_key": repo.repo_url,
                        "active_runtime_repo_name": repo.name,
                        "active_runtime_command": chosen_cmd,
                        "active_runtime_command_kind": classify_runtime_command(chosen_cmd),
                        "active_runtime_execution_target": execution_target,
                        "active_runtime_cwd": str(project_dir),
                        "active_repair_phase": "awaiting_repair_retry",
                        "active_objective_requires_user_decision": False,
                    },
                )
                _update_active_runtime_objective(paths, chat=terminal_interface, active_repair_phase="awaiting_repair_retry")
                display_output(
                    f"Duckln stored `{chosen_cmd}` as the run command for {repo.name}. "
                    "Type a run instruction when you're ready to try it."
                )
                return
    else:
        # Headless / fallback: suggest the top README candidate if available
        if top_cmd and (approve_prompt is None or approve_prompt(f"Run {repo.name} with: `{top_cmd}`?")):
            write_workflow_state(
                paths.config_dir,
                {
                    "active_repo_key": repo.repo_url,
                    "active_repo_name": repo.name,
                    "active_runtime_repo_key": repo.repo_url,
                    "active_runtime_repo_name": repo.name,
                    "active_runtime_command": top_cmd,
                    "active_runtime_command_kind": classify_runtime_command(top_cmd),
                    "active_runtime_execution_target": execution_target,
                    "active_runtime_cwd": str(project_dir),
                    "active_repair_phase": "awaiting_repair_retry",
                    "active_objective_requires_user_decision": False,
                },
            )
            _update_active_runtime_objective(paths, chat=terminal_interface, active_repair_phase="awaiting_repair_retry")
            display_output(
                f"Duckln stored `{top_cmd}` as the run command for {repo.name}. "
                "Re-run to try it."
            )
            return

    # Nothing resolved — tell the user how to unblock
    _update_active_runtime_objective(
        paths,
        chat=terminal_interface,
        active_objective_status="needs_user_decision",
        active_objective_requires_user_decision=True,
        active_repair_phase="guidance_only",
    )
    display_output(
        f"Duckln paused on {repo.name} — the run command for {target_label} is unknown. "
        "Tell Duckln the exact runtime command (e.g. `openclaw gateway --port 18789 --verbose`) and it will store and run it. "
        + (
            f"Web reference: {evidence_source_urls[0]}"
            if evidence_source_urls
            else "Check the README or project docs for the entry point."
        )
    )


def _run_bounded_runtime_check(
    *,
    repo: RepoCatalogRecord,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    command: str,
    execution_target: str,
    runtime_cwd: str | None,
    workflow,
) -> tuple[bool, str]:
    wrapped_command = command
    if execution_target in {"vm", "aws", "gcp"}:
        resolved_command, _ = _wrap_command_for_execution_target(
            config_dir=paths.config_dir,
            execution_target=execution_target,
            command=command,
            cwd=runtime_cwd,
            preferred_vm_name=None if workflow is None else workflow.active_runtime_vm_name,
            preferred_cloud_resource_key=None if workflow is None else workflow.active_runtime_cloud_resource_key,
            preferred_cloud_vendor=None if workflow is None else workflow.active_runtime_cloud_vendor,
            preferred_cloud_region=None if workflow is None else workflow.active_runtime_cloud_region,
            preferred_cloud_shape=None if workflow is None else workflow.active_runtime_cloud_shape,
        )
        if not resolved_command:
            return False, f"Supervisor agent could not resolve the active {execution_target} target for {repo.name}."
        wrapped_command = resolved_command
    runner = ControlledCommandRunner(trace=display_output, execution_target=execution_target)
    result = runner.run(
        wrapped_command,
        cwd=None if execution_target in {"vm", "aws", "gcp"} else runtime_cwd,
    )
    if not result.timed_out and result.exit_code == 0:
        return True, f"Supervisor agent verified {repo.name} with a bounded live check. Command: {command}."
    incident = summarize_failure_incident(
        command=command,
        stdout=result.stdout,
        stderr=result.stderr,
    )
    combined = (
        result.stderr.strip()
        or result.stdout.strip()
        or str(incident.fatal_line or "").strip()
        or incident.summary
    )
    return False, combined


_RUNTIME_REPAIR_BUDGET_SECONDS = 90
# Total wall-clock cap across ALL incidents processed by _drain_terminal_runtime_incidents
# in a single drain pass. Each _run_runtime_repair_workflow call has its own 90s per-call
# budget, but repeated calls can stack without this outer cap.
_DRAIN_TOTAL_BUDGET_SECONDS = 180


# Module-level dedup cache for runtime prerequisite installs. Plan 55 keeps
# Duckln from looping on the same failing install across orchestration retries.
# Keyed by (repo_url, install_command) -> stderr fingerprint of the last
# failure. Cleared on successful install.
_RUNTIME_PREREQUISITE_FAILED: dict[tuple[str, str], str] = {}


def _record_runtime_install_failure(
    *,
    repo_url: str,
    install_command: str,
    stderr: str,
    config_dir=None,
    execution_target: str | None = None,
    exit_code: int | None = None,
) -> str:
    fp = fingerprint_stderr(stderr)
    _RUNTIME_PREREQUISITE_FAILED[(repo_url, install_command)] = fp
    # Plan 58 Bug D: also persist the failure across sessions so a future
    # Duckln process can consult the log and skip the same failing command.
    if config_dir is not None:
        try:
            from state.access import write_failure_memory_state
            write_failure_memory_state(
                config_dir,
                repo_slug=repo_url,
                command=install_command,
                execution_target=execution_target or "local",
                exit_code=exit_code if exit_code is not None else 1,
                stderr_fingerprint=fp,
            )
        except Exception:
            pass
    return fp


def _runtime_install_recently_failed_same(
    *,
    repo_url: str,
    install_command: str,
    current_stderr_fp: str,
    config_dir=None,
    execution_target: str | None = None,
) -> bool:
    prior = _RUNTIME_PREREQUISITE_FAILED.get((repo_url, install_command))
    if prior is not None and prior == current_stderr_fp and prior != "":
        return True
    # Plan 58 Bug D: also consult the persistent failure log so that a Duckln
    # restart doesn't lose the within-process dedup state.
    if config_dir is not None:
        try:
            from state.access import lookup_recent_failure
            persistent = lookup_recent_failure(
                config_dir,
                repo_slug=repo_url,
                command=install_command,
                execution_target=execution_target or "local",
            )
            if persistent is not None:
                stored_fp = str(persistent.get("stderr_fingerprint", ""))
                if stored_fp and stored_fp == current_stderr_fp:
                    return True
        except Exception:
            pass
    return False


def _clear_runtime_install_failure(*, repo_url: str, install_command: str) -> None:
    _RUNTIME_PREREQUISITE_FAILED.pop((repo_url, install_command), None)


def _repair_model_reachable(config_dir: Path) -> bool:
    """Plan 169 F5: a FAST, provider-agnostic reachability check for the runtime-repair loop.
    No provider configured → unreachable; else a quick `validate_api_key` (the same models/tags
    probe onboarding uses) — its OK reflects connectivity, so a reachable-but-slow model still
    passes (only a genuine CONNECT/auth failure returns False). A probe error → proceed (don't
    block on the probe; the loop's own error handling surfaces it)."""
    try:
        from duckln.ai_client import build_default_llm_client_or_none, get_provider_adapter_for_base_url
        from duckln.config import load_app_config

        # No provider configured here → NOT the F5 scenario (the product always has one after
        # onboarding; this is mainly the test/no-config case). Don't honest-stop; let the flow
        # proceed. F5 targets a CONFIGURED-but-unreachable model (the user's Ollama-down / 429).
        if build_default_llm_client_or_none(config_dir) is None:
            return True
        paths = ConfigPaths(config_dir=Path(config_dir), config_file=Path(config_dir) / "config.json")
        cfg = load_app_config(paths)
        if cfg is None or not getattr(cfg, "provider", None):
            return True
        adapter = get_provider_adapter_for_base_url(cfg.provider, base_url=cfg.base_url)
        return bool(adapter.validate_api_key(cfg.api_key).ok)
    except Exception:
        return True


def _with_runtime_context_hint(message: str) -> str:
    """Plan 169 F5 / Plan 167: append the honest context-overflow hint when relevant."""
    try:
        from duckln.usage_meter import context_overflow_hint

        hint = context_overflow_hint()
    except Exception:
        hint = ""
    return f"{message} {hint}".strip() if hint else message


def _run_runtime_repair_workflow(
    *,
    repo: RepoCatalogRecord,
    current: AppConfig,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    approve_prompt: Callable[[str], bool] | None,
    terminal_interface: object | None,
    run_summary: str,
    runtime_command_override: str | None = None,
    workflow=None,
) -> None:
    # Plan 188 F2c: Plan Mode is always on now, so runtime repair must NOT dead-end behind a
    # (removed) "/plan off". Instead OFFER to fix it in one step — the user's Yes IS the consent
    # to run the repair. No/Cancel leaves the blocker captured; the S0–S4 mode floor still gates
    # any destructive step even after consent.
    if getattr(current, "plan_mode_enabled", False):
        from duckln.interaction import propose_and_confirm

        _sel = getattr(terminal_interface, "select_choice", None)
        _txt = getattr(terminal_interface, "prompt_text", None)
        _clear_chat_activity(terminal_interface)  # stop the spinner while we ask
        decision = propose_and_confirm(
            situation=f"I hit a runtime blocker on {repo.name}: {run_summary}",
            recommendation="attempt the fix now",
            display=display_output,
            select=(lambda q, opts: _sel(q, opts)) if callable(_sel) else None,
            approve=approve_prompt,
            text_prompt=(lambda q, d="": _txt(q, default=d)) if callable(_txt) else None,
            allow_cancel=True,
        )
        if not decision.accepted:
            display_output(
                f"Okay — I'll leave the {repo.name} blocker paused. "
                "Tell me to continue whenever you're ready."
            )
            return
        # Yes → fall through and run the repair workflow below.
    workflow_started_at = time.monotonic()
    # Reconcile workflow's persisted runtime target against the live session
    # target. A prior `/cloud` session may have stamped `gcp` (or another cloud
    # target) into workflow state; if the user is now back on Local, that
    # stale string would leak into search queries ("Google Cloud Linux VM")
    # and produce irrelevant results. We only override when the session has an
    # EXPLICITLY persisted target — never when "local" is just the default
    # fallback, because that would break legitimate cloud runtime flows where
    # the workflow correctly tracks the active cloud resource.
    workflow_target = (
        workflow.active_runtime_execution_target
        if workflow is not None and workflow.active_runtime_execution_target
        else None
    )
    explicit_session_target = read_config_snapshot(paths.config_dir).get("execution_target")
    if (
        workflow_target
        and explicit_session_target
        and workflow_target != explicit_session_target
    ):
        display_output(
            f"Duckln noticed a stale runtime target ({workflow_target}); "
            f"using your current session target ({explicit_session_target})."
        )
        execution_target = explicit_session_target
    else:
        execution_target = workflow_target or _session_execution_target(paths.config_dir) or "local"
    # Clear stale docker-compose commands for Multipass VM — the VM is its own sandbox
    # and docker-compose inside it fails unless Docker is explicitly installed.
    # Cloud targets (aws, gcp) may legitimately use Docker, so only clear for local VMs.
    if execution_target == "vm" and (runtime_command_override or "").strip().startswith("docker"):
        runtime_command_override = None
        write_workflow_state(paths.config_dir, {"active_runtime_command": None, "active_runtime_command_kind": None})
    effective_runtime_command = _effective_runtime_command(
        runtime_command_override=runtime_command_override,
        workflow=workflow,
    )
    if runtime_command_override and effective_runtime_command is None:
        display_output(
            f"Duckln ignored `{runtime_command_override}` for runtime repair because it is not a runnable start command."
        )
        write_workflow_state(
            paths.config_dir,
            {"active_runtime_command": None, "active_runtime_command_kind": None},
        )
    runtime_command_override = effective_runtime_command
    runtime_cwd = (
        workflow.active_runtime_cwd
        if workflow is not None and workflow.active_runtime_cwd
        else str(resolve_managed_project_dir(paths.config_dir, repo))
    )
    project_dir = Path(runtime_cwd) if runtime_cwd else resolve_managed_project_dir(paths.config_dir, repo)
    objective_goal = (
        f"Get {repo.name} running through Duckln's tracked runtime path."
        if not runtime_command_override
        else f"Repair and rerun {repo.name} with `{runtime_command_override}`."
    )
    _begin_active_runtime_objective(
        paths=paths,
        repo=repo,
        execution_target=execution_target,
        runtime_command=runtime_command_override,
        goal=objective_goal,
        resume_hint=f"Last time Duckln was fixing {repo.name}.",
        workflow_context=workflow,
        chat=terminal_interface,
    )
    if _MISSING_RUN_COMMAND_MARKER in (run_summary or ""):
        _discover_repo_run_command(
            repo=repo,
            paths=paths,
            display_output=display_output,
            approve_prompt=approve_prompt,
            terminal_interface=terminal_interface,
            execution_target=execution_target,
            failure_summary=run_summary,
            workflow=workflow,
        )
        return
    incident = summarize_failure_incident(
        command=runtime_command_override or repo.name,
        stderr=run_summary,
    )
    runtime_repair_plan = plan_runtime_repair(
        repo_name=repo.name,
        project_dir=project_dir,
        incident=incident,
    )
    display_output(
        render_tool_invocation_trace(
            title="runtime repair planning",
            tool_id="verification.runtime_repair_planner",
            action=f"Classify the {repo.name} runtime blocker and choose the smallest bounded repair step",
            detail_lines=(
                f"Incident category: {incident.category}",
                f"Route family: {incident.route_family}",
                f"Specialist: {runtime_repair_plan.specialist_name}",
                f"Incident summary: {_compact_runtime_summary(incident.summary)}",
                f"Chosen repair action: {runtime_repair_plan.action_key}",
                f"Reason: {runtime_repair_plan.reason}",
                (
                    f"Verification gate: {runtime_repair_plan.verification_hint}"
                    if runtime_repair_plan.verification_hint
                    else None
                ),
                (
                    "Duckln will cross-check official docs before proceeding."
                    if runtime_repair_plan.requires_official_docs_lookup
                    else None
                ),
            ),
            execution_target=execution_target,
        )
    )
    _update_active_runtime_objective(paths, chat=terminal_interface, active_repair_phase="runtime_repair_planning")
    guidance = _runtime_issue_guidance(
        repo_name=repo.name,
        repo_key=repo.repo_url,
        paths=paths,
        incident_summary=incident.summary,
        incident_category=incident.category,
    )
    if guidance:
        write_workflow_state(
            paths.config_dir,
            {
                "active_repo_key": repo.repo_url,
                "active_repo_name": repo.name,
                "active_issue_kind": "runtime_input_issue",
                "active_issue_summary": guidance,
                "active_incident_category": incident.category,
                "active_incident_summary": incident.summary,
                "active_repair_phase": "guidance_only",
            },
        )
        _update_active_runtime_objective(paths, chat=terminal_interface, active_repair_phase="guidance_only")
        _clear_runtime_repair_followup(paths)
        display_output(guidance)
        return

    # Hard wall-clock budget: stop launching new web tool calls or specialist
    # executors once we've spent more than _RUNTIME_REPAIR_BUDGET_SECONDS on
    # this single repair attempt. The orchestration loop can still try again,
    # but we never silently spin past 90 seconds inside one pass.
    def _budget_elapsed() -> float:
        return time.monotonic() - workflow_started_at

    def _budget_exceeded() -> bool:
        return _budget_elapsed() > _RUNTIME_REPAIR_BUDGET_SECONDS

    if _budget_exceeded():
        elapsed = int(_budget_elapsed())
        timeout_message = (
            f"Duckln spent {elapsed}s on runtime repair without resolving — escalating to the user."
        )
        display_output(timeout_message)
        _update_active_runtime_objective(
            paths,
            chat=terminal_interface,
            active_objective_status="needs_user_decision",
            active_objective_requires_user_decision=True,
            active_repair_phase="repair_timeout",
        )
        return

    if (
        runtime_repair_plan.route_family in {"auth_repair", "cloud_repair", "vm_repair"}
        and runtime_repair_plan.requires_official_docs_lookup
        and not _budget_exceeded()
    ):
        _show_runtime_repair_evidence(
            repo_name=repo.name,
            failing_command=runtime_command_override or repo.name,
            run_summary=run_summary,
            display_output=display_output,
            execution_target=execution_target,
        )

    if _budget_exceeded():
        elapsed = int(_budget_elapsed())
        timeout_message = (
            f"Duckln spent {elapsed}s on runtime repair without resolving — escalating to the user."
        )
        display_output(timeout_message)
        _update_active_runtime_objective(
            paths,
            chat=terminal_interface,
            active_objective_status="needs_user_decision",
            active_objective_requires_user_decision=True,
            active_repair_phase="repair_timeout",
        )
        return

    specialist_status, specialist_message = _attempt_runtime_specialist_executor(
        repo=repo,
        current=current,
        paths=paths,
        display_output=display_output,
        approve_prompt=approve_prompt,
        runtime_repair_plan=runtime_repair_plan,
        incident=incident,
        execution_target=execution_target,
        workflow=workflow,
        chat=terminal_interface,
    )
    if specialist_status == "awaiting_user_action":
        write_workflow_state(
            paths.config_dir,
            {
                "active_repo_key": repo.repo_url,
                "active_repo_name": repo.name,
                "active_issue_kind": "run_issue",
                "active_issue_summary": specialist_message or incident.summary,
                "active_incident_category": incident.category,
                "active_incident_summary": incident.summary,
                "active_repair_phase": runtime_repair_plan.action_key,
            },
        )
        _persist_runtime_repair_followup(paths=paths, repo_key=repo.repo_url, repo_name=repo.name)
        _update_active_runtime_objective(
            paths,
            chat=terminal_interface,
            active_objective_status="needs_user_decision",
            active_objective_requires_user_decision=True,
            active_objective_last_blocker=incident.summary,
            active_objective_resume_hint=f"Last time Duckln was fixing {repo.name}. Latest blocker: {_compact_runtime_summary(incident.summary)}",
            active_repair_phase=runtime_repair_plan.action_key,
        )
        # Surface the same inline chooser used by the `failed` branch so the user can
        # act without typing slash commands (e.g. when VM tools are missing or approval
        # is needed before the repair can continue).
        chosen_action = _surface_runtime_escalation_choice(
            repo_name=repo.name,
            blocker_summary=specialist_message or incident.summary,
            max_attempts=_ACTIVE_OBJECTIVE_MAX_REPAIR_ATTEMPTS,
            current_target=execution_target,
            chat=terminal_interface,
            display_output=display_output,
        )
        _apply_runtime_escalation_choice(
            chosen_action=chosen_action,
            repo=repo,
            paths=paths,
            execution_target=execution_target,
            display_output=display_output,
            terminal_interface=terminal_interface,
        )
        return
    if specialist_status == "failed":
        write_workflow_state(
            paths.config_dir,
            {
                "active_repo_key": repo.repo_url,
                "active_repo_name": repo.name,
                "active_issue_kind": "run_issue",
                "active_issue_summary": specialist_message or incident.summary,
                "active_incident_category": incident.category,
                "active_incident_summary": incident.summary,
                "active_repair_phase": f"{runtime_repair_plan.action_key}_failed",
            },
        )
        _persist_runtime_repair_followup(paths=paths, repo_key=repo.repo_url, repo_name=repo.name)
        _update_active_runtime_objective(
            paths,
            chat=terminal_interface,
            active_objective_status="needs_user_decision",
            active_objective_requires_user_decision=True,
            active_objective_last_blocker=specialist_message or incident.summary,
            active_objective_resume_hint=f"Last time Duckln was fixing {repo.name}. Latest blocker: {_compact_runtime_summary(specialist_message or incident.summary)}",
            active_repair_phase=f"{runtime_repair_plan.action_key}_failed",
        )
        # When a specialist path fails (e.g. vm_repair_escalation), the user previously
        # only saw the footer "Awaiting approval — vm repair escalation failed on <repo>"
        # with no chooser. Surface the same inline up/down picker the limit-reached path
        # uses so the user can retry / switch target / pause without typing slash commands.
        chosen_action = _surface_runtime_escalation_choice(
            repo_name=repo.name,
            blocker_summary=specialist_message or incident.summary,
            max_attempts=_ACTIVE_OBJECTIVE_MAX_REPAIR_ATTEMPTS,
            current_target=execution_target,
            chat=terminal_interface,
            display_output=display_output,
        )
        _apply_runtime_escalation_choice(
            chosen_action=chosen_action,
            repo=repo,
            paths=paths,
            execution_target=execution_target,
            display_output=display_output,
            terminal_interface=terminal_interface,
        )
        return
    if specialist_status == "ready_to_rerun":
        _update_active_runtime_objective(paths, chat=terminal_interface, active_repair_phase="post_specialist_rerun")
        display_output(
            render_tool_invocation_trace(
                title="post-specialist rerun",
                tool_id="process.session_runtime",
                action=f"Re-check {repo.name} after the bounded {runtime_repair_plan.specialist_name} repair step",
                detail_lines=(
                    f"Repo re-check: {repo.name}",
                    f"Specialist path: {runtime_repair_plan.specialist_name}",
                    "Duckln completed the specialist repair step and is re-running the tracked runtime command before escalating.",
                ),
                execution_target=execution_target,
            )
        )
        rerun_result_obj = None
        if runtime_command_override:
            rerun_success, rerun_message = _run_bounded_runtime_check(
                repo=repo,
                paths=paths,
                display_output=display_output,
                command=runtime_command_override,
                execution_target=execution_target,
                runtime_cwd=runtime_cwd,
                workflow=workflow,
            )
        else:
            rerun_result_obj = run_prepared_repo(
                repo,
                current.mode,
                paths,
                approve=approve_prompt,
                display=display_output,
                verification_only=False,
                terminal_executor=terminal_interface,
                runtime_command_override=runtime_command_override,
                runtime_step_preapproved=True,
            )
            rerun_success = rerun_result_obj.verification_passed
            rerun_message = rerun_result_obj.message
        if not getattr(rerun_result_obj, "message_already_displayed", False):
            display_output(rerun_message)
        if _handle_non_repairable_runtime_result(
            repo=repo,
            result_obj=rerun_result_obj,
            paths=paths,
            display_output=display_output,
            terminal_interface=terminal_interface,
            phase="post_specialist_rerun_paused",
        ):
            return
        if _MISSING_RUN_COMMAND_MARKER in (rerun_message or ""):
            _discover_repo_run_command(
                repo=repo,
                paths=paths,
                display_output=display_output,
                approve_prompt=approve_prompt,
                terminal_interface=terminal_interface,
                execution_target=execution_target,
                failure_summary=rerun_message,
                workflow=workflow,
            )
            return
        if rerun_success:
            write_workflow_state(
                paths.config_dir,
                {
                    "active_incident_category": None,
                    "active_incident_summary": None,
                    "active_repair_phase": "runtime_verified",
                },
            )
            _clear_runtime_repair_followup(paths)
            _clear_active_runtime_objective(paths, chat=terminal_interface)
            _maybe_launch_repo_access(
                repo=repo,
                current=current,
                paths=paths,
                display_output=display_output,
                approve_prompt=approve_prompt,
                terminal_interface=terminal_interface,
            )
            return
        run_summary = rerun_message
        incident = summarize_failure_incident(
            command=runtime_command_override or repo.name,
            stderr=run_summary,
        )
        runtime_repair_plan = plan_runtime_repair(
            repo_name=repo.name,
            project_dir=project_dir,
            incident=incident,
        )

    specialist_guidance = _runtime_specialist_guidance(
        repo_name=repo.name,
        repo_key=repo.repo_url,
        paths=paths,
        incident=incident,
        runtime_repair_plan=runtime_repair_plan,
        execution_target=execution_target,
    )
    if specialist_guidance is not None:
        if runtime_repair_plan.requires_official_docs_lookup:
            _show_runtime_repair_evidence(
                repo_name=repo.name,
                failing_command=runtime_command_override or repo.name,
                run_summary=run_summary,
                display_output=display_output,
                execution_target=execution_target,
            )
        write_workflow_state(
            paths.config_dir,
            {
                "active_repo_key": repo.repo_url,
                "active_repo_name": repo.name,
                "active_issue_kind": "run_issue",
                "active_issue_summary": specialist_guidance,
                "active_incident_category": incident.category,
                "active_incident_summary": incident.summary,
                "active_repair_phase": runtime_repair_plan.action_key,
            },
        )
        _persist_runtime_repair_followup(paths=paths, repo_key=repo.repo_url, repo_name=repo.name)
        _update_active_runtime_objective(
            paths,
            chat=terminal_interface,
            active_objective_status="needs_user_decision",
            active_objective_requires_user_decision=True,
            active_objective_last_blocker=incident.summary,
            active_objective_resume_hint=f"Last time Duckln was fixing {repo.name}. Latest blocker: {_compact_runtime_summary(incident.summary)}",
            active_repair_phase=runtime_repair_plan.action_key,
        )
        display_output(specialist_guidance)
        return

    write_workflow_state(
        paths.config_dir,
        {
            "active_incident_category": incident.category,
            "active_incident_summary": incident.summary,
            "active_repair_phase": "runtime_failed",
        },
    )
    _update_active_runtime_objective(paths, chat=terminal_interface, active_repair_phase="runtime_failed")
    if _consume_runtime_objective_attempt(
        paths=paths,
        repo_name=repo.name,
        blocker_summary=incident.summary,
        chat=terminal_interface,
    ):
        timeout_message = _render_runtime_objective_timeout(
            repo_name=repo.name,
            blocker_summary=incident.summary,
            max_attempts=_ACTIVE_OBJECTIVE_MAX_REPAIR_ATTEMPTS,
        )
        write_workflow_state(
            paths.config_dir,
            {
                "active_issue_kind": "run_issue",
                "active_issue_summary": timeout_message,
                "active_repair_phase": "repair_limit_reached",
            },
        )
        _update_active_runtime_objective(
            paths,
            chat=terminal_interface,
            active_repair_phase="repair_limit_reached",
            active_objective_status="needs_user_decision",
            active_objective_requires_user_decision=True,
        )
        _persist_runtime_repair_followup(paths=paths, repo_key=repo.repo_url, repo_name=repo.name)
        display_output(timeout_message)
        chosen_action = _surface_runtime_escalation_choice(
            repo_name=repo.name,
            blocker_summary=incident.summary,
            max_attempts=_ACTIVE_OBJECTIVE_MAX_REPAIR_ATTEMPTS,
            current_target=execution_target,
            chat=terminal_interface,
            display_output=display_output,
        )
        _apply_runtime_escalation_choice(
            chosen_action=chosen_action,
            repo=repo,
            paths=paths,
            execution_target=execution_target,
            display_output=display_output,
            terminal_interface=terminal_interface,
        )
        return
    workflow = _AGENT_CONTEXT_SERVICE.load_workflow_state(config_dir=paths.config_dir)
    docker_env_status = _attempt_runtime_docker_env_repair(
        repo=repo,
        current=current,
        paths=paths,
        display_output=display_output,
        approve_prompt=approve_prompt,
        runtime_repair_plan=runtime_repair_plan,
        execution_target=execution_target,
        project_dir=project_dir,
        runtime_cwd=runtime_cwd,
        workflow=workflow,
        chat=terminal_interface,
    )
    if docker_env_status == "awaiting_approval":
        write_workflow_state(
            paths.config_dir,
            {
                "active_issue_kind": "run_issue",
                "active_issue_summary": f"Duckln paused before repairing {repo.name} because approval is still required.",
                "active_repair_phase": "awaiting_runtime_docker_env_approval",
            },
        )
        _update_active_runtime_objective(
            paths,
            chat=terminal_interface,
            active_repair_phase="awaiting_runtime_docker_env_approval",
            active_objective_status="needs_user_decision",
            active_objective_requires_user_decision=True,
        )
        return
    if docker_env_status == "installed":
        _update_active_runtime_objective(paths, chat=terminal_interface, active_repair_phase="post_docker_env_rerun")
        rerun_result_obj = None
        if runtime_command_override:
            rerun_success, rerun_message = _run_bounded_runtime_check(
                repo=repo,
                paths=paths,
                display_output=display_output,
                command=runtime_command_override,
                execution_target=execution_target,
                runtime_cwd=runtime_cwd,
                workflow=workflow,
            )
        else:
            rerun_result_obj = run_prepared_repo(
                repo,
                current.mode,
                paths,
                approve=approve_prompt,
                display=display_output,
                verification_only=False,
                terminal_executor=terminal_interface,
                runtime_command_override=runtime_command_override,
                runtime_step_preapproved=True,
            )
            rerun_success = rerun_result_obj.verification_passed
            rerun_message = rerun_result_obj.message
        if not getattr(rerun_result_obj, "message_already_displayed", False):
            display_output(rerun_message)
        if _handle_non_repairable_runtime_result(
            repo=repo,
            result_obj=rerun_result_obj,
            paths=paths,
            display_output=display_output,
            terminal_interface=terminal_interface,
            phase="post_docker_env_rerun_paused",
        ):
            return
        if rerun_success:
            write_workflow_state(
                paths.config_dir,
                {
                    "active_incident_category": None,
                    "active_incident_summary": None,
                    "active_repair_phase": "runtime_verified",
                },
            )
            _clear_runtime_repair_followup(paths)
            _clear_active_runtime_objective(paths, chat=terminal_interface)
            _maybe_launch_repo_access(
                repo=repo,
                current=current,
                paths=paths,
                display_output=display_output,
                approve_prompt=approve_prompt,
                terminal_interface=terminal_interface,
            )
            return
        run_summary = rerun_message
        incident = summarize_failure_incident(
            command=runtime_command_override or repo.name,
            stderr=run_summary,
        )
        runtime_repair_plan = plan_runtime_repair(
            repo_name=repo.name,
            project_dir=project_dir,
            incident=incident,
        )
    cloud_bootstrap_status = _attempt_runtime_cloud_bootstrap_repair(
        repo=repo,
        current=current,
        paths=paths,
        display_output=display_output,
        approve_prompt=approve_prompt,
        runtime_repair_plan=runtime_repair_plan,
        execution_target=execution_target,
        workflow=workflow,
        chat=terminal_interface,
    )
    if cloud_bootstrap_status == "awaiting_approval":
        write_workflow_state(
            paths.config_dir,
            {
                "active_issue_kind": "run_issue",
                "active_issue_summary": f"Duckln paused before repairing the cloud runtime path for {repo.name} because approval is still required.",
                "active_repair_phase": "awaiting_runtime_cloud_bootstrap_approval",
            },
        )
        _persist_runtime_repair_followup(paths=paths, repo_key=repo.repo_url, repo_name=repo.name)
        _update_active_runtime_objective(
            paths,
            chat=terminal_interface,
            active_objective_status="needs_user_decision",
            active_objective_requires_user_decision=True,
            active_objective_last_blocker=incident.summary,
            active_objective_resume_hint=f"Last time Duckln was fixing {repo.name}. Latest blocker: {_compact_runtime_summary(incident.summary)}",
            active_repair_phase="awaiting_runtime_cloud_bootstrap_approval",
        )
        return
    if cloud_bootstrap_status == "failed":
        write_workflow_state(
            paths.config_dir,
            {
                "active_issue_kind": "run_issue",
                "active_issue_summary": f"Duckln could not repair the cloud runtime bootstrap path for {repo.name}.",
                "active_repair_phase": "runtime_cloud_bootstrap_failed",
            },
        )
        _persist_runtime_repair_followup(paths=paths, repo_key=repo.repo_url, repo_name=repo.name)
        _update_active_runtime_objective(
            paths,
            chat=terminal_interface,
            active_objective_status="needs_user_decision",
            active_objective_requires_user_decision=True,
            active_objective_last_blocker=incident.summary,
            active_objective_resume_hint=f"Last time Duckln was fixing {repo.name}. Latest blocker: {_compact_runtime_summary(incident.summary)}",
            active_repair_phase="runtime_cloud_bootstrap_failed",
        )
        chosen_action = _surface_runtime_escalation_choice(
            repo_name=repo.name,
            blocker_summary=incident.summary,
            max_attempts=_ACTIVE_OBJECTIVE_MAX_REPAIR_ATTEMPTS,
            current_target=execution_target,
            chat=terminal_interface,
            display_output=display_output,
        )
        _apply_runtime_escalation_choice(
            chosen_action=chosen_action,
            repo=repo,
            paths=paths,
            execution_target=execution_target,
            display_output=display_output,
            terminal_interface=terminal_interface,
        )
        return
    cloud_service_status = _attempt_runtime_cloud_service_repair(
        repo=repo,
        paths=paths,
        display_output=display_output,
        runtime_repair_plan=runtime_repair_plan,
        execution_target=execution_target,
        project_dir=project_dir,
        runtime_cwd=runtime_cwd,
        workflow=workflow,
        chat=terminal_interface,
    )
    if cloud_service_status == "failed":
        write_workflow_state(
            paths.config_dir,
            {
                "active_issue_kind": "run_issue",
                "active_issue_summary": f"Duckln could not repair the tracked cloud runtime services for {repo.name}.",
                "active_repair_phase": "runtime_cloud_service_failed",
            },
        )
        _persist_runtime_repair_followup(paths=paths, repo_key=repo.repo_url, repo_name=repo.name)
        _update_active_runtime_objective(
            paths,
            chat=terminal_interface,
            active_objective_status="needs_user_decision",
            active_objective_requires_user_decision=True,
            active_objective_last_blocker=incident.summary,
            active_objective_resume_hint=f"Last time Duckln was fixing {repo.name}. Latest blocker: {_compact_runtime_summary(incident.summary)}",
            active_repair_phase="runtime_cloud_service_failed",
        )
        chosen_action = _surface_runtime_escalation_choice(
            repo_name=repo.name,
            blocker_summary=incident.summary,
            max_attempts=_ACTIVE_OBJECTIVE_MAX_REPAIR_ATTEMPTS,
            current_target=execution_target,
            chat=terminal_interface,
            display_output=display_output,
        )
        _apply_runtime_escalation_choice(
            chosen_action=chosen_action,
            repo=repo,
            paths=paths,
            execution_target=execution_target,
            display_output=display_output,
            terminal_interface=terminal_interface,
        )
        return
    python_status = _attempt_runtime_python_executor(
        repo=repo,
        current=current,
        paths=paths,
        display_output=display_output,
        approve_prompt=approve_prompt,
        runtime_repair_plan=runtime_repair_plan,
        incident=incident,
        execution_target=execution_target,
        project_dir=project_dir,
        runtime_cwd=runtime_cwd,
        workflow=workflow,
        chat=terminal_interface,
    )
    if python_status == "awaiting_approval":
        write_workflow_state(
            paths.config_dir,
            {
                "active_issue_kind": "run_issue",
                "active_issue_summary": f"Duckln paused before repairing the Python dependency path for {repo.name} because approval is still required.",
                "active_repair_phase": "awaiting_python_dependency_approval",
            },
        )
        _persist_runtime_repair_followup(paths=paths, repo_key=repo.repo_url, repo_name=repo.name)
        _update_active_runtime_objective(
            paths,
            chat=terminal_interface,
            active_objective_status="needs_user_decision",
            active_objective_requires_user_decision=True,
            active_objective_last_blocker=incident.summary,
            active_objective_resume_hint=f"Last time Duckln was fixing {repo.name}. Latest blocker: {_compact_runtime_summary(incident.summary)}",
            active_repair_phase="awaiting_python_dependency_approval",
        )
        return
    if python_status == "failed":
        write_workflow_state(
            paths.config_dir,
            {
                "active_issue_kind": "run_issue",
                "active_issue_summary": f"Duckln could not verify the Python dependency contract for {repo.name}.",
                "active_repair_phase": "python_dependency_failed",
            },
        )
        _persist_runtime_repair_followup(paths=paths, repo_key=repo.repo_url, repo_name=repo.name)
        _update_active_runtime_objective(
            paths,
            chat=terminal_interface,
            active_objective_status="needs_user_decision",
            active_objective_requires_user_decision=True,
            active_objective_last_blocker=incident.summary,
            active_objective_resume_hint=f"Last time Duckln was fixing {repo.name}. Latest blocker: {_compact_runtime_summary(incident.summary)}",
            active_repair_phase="python_dependency_failed",
        )
        chosen_action = _surface_runtime_escalation_choice(
            repo_name=repo.name,
            blocker_summary=incident.summary,
            max_attempts=_ACTIVE_OBJECTIVE_MAX_REPAIR_ATTEMPTS,
            current_target=execution_target,
            chat=terminal_interface,
            display_output=display_output,
        )
        _apply_runtime_escalation_choice(
            chosen_action=chosen_action,
            repo=repo,
            paths=paths,
            execution_target=execution_target,
            display_output=display_output,
            terminal_interface=terminal_interface,
        )
        return
    if python_status == "ready_to_rerun":
        _update_active_runtime_objective(paths, chat=terminal_interface, active_repair_phase="post_python_rerun")
        display_output(
            render_tool_invocation_trace(
                title="post-python rerun",
                tool_id="process.session_runtime",
                action=f"Re-check {repo.name} after the bounded Python repair path",
                detail_lines=(
                    f"Repo re-check: {repo.name}",
                    "Specialist path: python",
                    "Duckln completed the Python dependency contract and is re-running the tracked runtime command before escalating.",
                ),
                execution_target=execution_target,
            )
        )
        rerun_success, rerun_message = _run_bounded_runtime_check(
            repo=repo,
            paths=paths,
            display_output=display_output,
            command=runtime_command_override or repo.name,
            execution_target=execution_target,
            runtime_cwd=runtime_cwd,
            workflow=workflow,
        )
        display_output(rerun_message)
        if rerun_success:
            write_workflow_state(
                paths.config_dir,
                {
                    "active_incident_category": None,
                    "active_incident_summary": None,
                    "active_repair_phase": "runtime_verified",
                },
            )
            _clear_runtime_repair_followup(paths)
            _clear_active_runtime_objective(paths, chat=terminal_interface)
            _maybe_launch_repo_access(
                repo=repo,
                current=current,
                paths=paths,
                display_output=display_output,
                approve_prompt=approve_prompt,
                terminal_interface=terminal_interface,
            )
            return
        run_summary = rerun_message
        incident = summarize_failure_incident(
            command=runtime_command_override or repo.name,
            stderr=run_summary,
        )
        runtime_repair_plan = plan_runtime_repair(
            repo_name=repo.name,
            project_dir=project_dir,
            incident=incident,
        )
    stack_status = _attempt_runtime_stack_executor(
        repo=repo,
        current=current,
        paths=paths,
        display_output=display_output,
        approve_prompt=approve_prompt,
        runtime_repair_plan=runtime_repair_plan,
        incident=incident,
        execution_target=execution_target,
        project_dir=project_dir,
        runtime_cwd=runtime_cwd,
        workflow=workflow,
        chat=terminal_interface,
    )
    if stack_status == "awaiting_approval":
        write_workflow_state(
            paths.config_dir,
            {
                "active_issue_kind": "run_issue",
                "active_issue_summary": f"Duckln paused before repairing the stack toolchain for {repo.name} because approval is still required.",
                "active_repair_phase": "awaiting_stack_toolchain_approval",
            },
        )
        _persist_runtime_repair_followup(paths=paths, repo_key=repo.repo_url, repo_name=repo.name)
        _update_active_runtime_objective(
            paths,
            chat=terminal_interface,
            active_objective_status="needs_user_decision",
            active_objective_requires_user_decision=True,
            active_objective_last_blocker=incident.summary,
            active_objective_resume_hint=f"Last time Duckln was fixing {repo.name}. Latest blocker: {_compact_runtime_summary(incident.summary)}",
            active_repair_phase="awaiting_stack_toolchain_approval",
        )
        return
    if stack_status == "failed":
        write_workflow_state(
            paths.config_dir,
            {
                "active_issue_kind": "run_issue",
                "active_issue_summary": f"Duckln could not repair the stack toolchain for {repo.name}.",
                "active_repair_phase": "stack_toolchain_failed",
            },
        )
        _persist_runtime_repair_followup(paths=paths, repo_key=repo.repo_url, repo_name=repo.name)
        _update_active_runtime_objective(
            paths,
            chat=terminal_interface,
            active_objective_status="needs_user_decision",
            active_objective_requires_user_decision=True,
            active_objective_last_blocker=incident.summary,
            active_objective_resume_hint=f"Last time Duckln was fixing {repo.name}. Latest blocker: {_compact_runtime_summary(incident.summary)}",
            active_repair_phase="stack_toolchain_failed",
        )
        chosen_action = _surface_runtime_escalation_choice(
            repo_name=repo.name,
            blocker_summary=incident.summary,
            max_attempts=_ACTIVE_OBJECTIVE_MAX_REPAIR_ATTEMPTS,
            current_target=execution_target,
            chat=terminal_interface,
            display_output=display_output,
        )
        _apply_runtime_escalation_choice(
            chosen_action=chosen_action,
            repo=repo,
            paths=paths,
            execution_target=execution_target,
            display_output=display_output,
            terminal_interface=terminal_interface,
        )
        return
    if stack_status == "ready_to_rerun":
        _update_active_runtime_objective(paths, chat=terminal_interface, active_repair_phase="post_stack_rerun")
        display_output(
            render_tool_invocation_trace(
                title="post-stack rerun",
                tool_id="process.session_runtime",
                action=f"Re-check {repo.name} after the bounded {runtime_repair_plan.specialist_name} repair path",
                detail_lines=(
                    f"Repo re-check: {repo.name}",
                    f"Specialist path: {runtime_repair_plan.specialist_name}",
                    "Duckln completed the stack-specific repair contract and is re-running the tracked runtime command before escalating.",
                ),
                execution_target=execution_target,
            )
        )
        rerun_success, rerun_message = _run_bounded_runtime_check(
            repo=repo,
            paths=paths,
            display_output=display_output,
            command=runtime_command_override or repo.name,
            execution_target=execution_target,
            runtime_cwd=runtime_cwd,
            workflow=workflow,
        )
        display_output(rerun_message)
        if rerun_success:
            write_workflow_state(
                paths.config_dir,
                {
                    "active_incident_category": None,
                    "active_incident_summary": None,
                    "active_repair_phase": "runtime_verified",
                },
            )
            _clear_runtime_repair_followup(paths)
            _clear_active_runtime_objective(paths, chat=terminal_interface)
            _maybe_launch_repo_access(
                repo=repo,
                current=current,
                paths=paths,
                display_output=display_output,
                approve_prompt=approve_prompt,
                terminal_interface=terminal_interface,
            )
            return
        run_summary = rerun_message
        incident = summarize_failure_incident(
            command=runtime_command_override or repo.name,
            stderr=run_summary,
        )
        runtime_repair_plan = plan_runtime_repair(
            repo_name=repo.name,
            project_dir=project_dir,
            incident=incident,
        )
    prerequisite_status = "not_applicable"
    if _budget_exceeded():
        elapsed = int(_budget_elapsed())
        display_output(
            f"Duckln spent {elapsed}s on runtime repair — skipping prerequisite install to stay within budget."
        )
        return
    if runtime_repair_plan.action_key != "repo_dependency_repair":
        prerequisite_status = _attempt_runtime_prerequisite_install(
            repo=repo,
            current=current,
            paths=paths,
            display_output=display_output,
            approve_prompt=approve_prompt,
            incident=incident,
            execution_target=execution_target,
            project_dir=str(project_dir),
            runtime_cwd=runtime_cwd,
            workflow=workflow,
            chat=terminal_interface,
        )
    if prerequisite_status == "declined":
        write_workflow_state(
            paths.config_dir,
            {
                "active_repo_key": repo.repo_url,
                "active_repo_name": repo.name,
                "active_issue_kind": "run_issue",
                "active_issue_summary": run_summary,
                "active_repair_phase": "repair_declined",
            },
        )
        _clear_runtime_repair_followup(paths)
        return
    if prerequisite_status == "awaiting_approval":
        write_workflow_state(
            paths.config_dir,
            {
                "active_repo_key": repo.repo_url,
                "active_repo_name": repo.name,
                "active_issue_kind": "run_issue",
                "active_issue_summary": f"Duckln paused before repairing {repo.name} because approval is still required.",
                "active_repair_phase": "awaiting_runtime_prerequisite_approval",
            },
        )
        _persist_runtime_repair_followup(paths=paths, repo_key=repo.repo_url, repo_name=repo.name)
        _update_active_runtime_objective(
            paths,
            chat=terminal_interface,
            active_objective_status="needs_user_decision",
            active_objective_requires_user_decision=True,
            active_objective_last_blocker=incident.summary,
            active_objective_resume_hint=f"Last time Duckln was fixing {repo.name}. Latest blocker: {_compact_runtime_summary(incident.summary)}",
            active_repair_phase="awaiting_runtime_prerequisite_approval",
        )
        return
    if prerequisite_status in {"failed", "stopped_repeated_failure"}:
        phase = (
            "runtime_prerequisite_repair_loop_stopped"
            if prerequisite_status == "stopped_repeated_failure"
            else "runtime_prerequisite_repair_failed"
        )
        write_workflow_state(
            paths.config_dir,
            {
                "active_repo_key": repo.repo_url,
                "active_repo_name": repo.name,
                "active_issue_kind": "run_issue",
                "active_issue_summary": f"Duckln could not verify the prerequisite repair for {repo.name}.",
                "active_repair_phase": phase,
            },
        )
        _persist_runtime_repair_followup(paths=paths, repo_key=repo.repo_url, repo_name=repo.name)
        _update_active_runtime_objective(
            paths,
            chat=terminal_interface,
            active_objective_status="needs_user_decision",
            active_objective_requires_user_decision=True,
            active_objective_last_blocker=incident.summary,
            active_objective_resume_hint=f"Last time Duckln was fixing {repo.name}. Latest blocker: {_compact_runtime_summary(incident.summary)}",
            active_repair_phase=phase,
        )
        return
    if prerequisite_status == "installed":
        _update_active_runtime_objective(paths, chat=terminal_interface, active_repair_phase="post_prerequisite_rerun")
        display_output(
            render_tool_invocation_trace(
                title="post-prerequisite rerun",
                tool_id="process.session_runtime",
                action=f"Re-check {repo.name} after prerequisite installation",
                detail_lines=(
                    f"Repo re-check: {repo.name}",
                    "Duckln installed the missing prerequisite and is re-running the tracked command before broader repair.",
                ),
                execution_target=execution_target,
            )
        )
        rerun_result_obj = None
        if runtime_command_override:
            rerun_success, rerun_message = _run_bounded_runtime_check(
                repo=repo,
                paths=paths,
                display_output=display_output,
                command=runtime_command_override,
                execution_target=execution_target,
                runtime_cwd=runtime_cwd,
                workflow=workflow,
            )
        else:
            rerun_result_obj = run_prepared_repo(
                repo,
                current.mode,
                paths,
                approve=approve_prompt,
                display=display_output,
                verification_only=False,
                terminal_executor=terminal_interface,
                runtime_command_override=runtime_command_override,
                runtime_step_preapproved=True,
            )
            rerun_success = rerun_result_obj.verification_passed
            rerun_message = rerun_result_obj.message
        if not getattr(rerun_result_obj, "message_already_displayed", False):
            display_output(rerun_message)
        if _handle_non_repairable_runtime_result(
            repo=repo,
            result_obj=rerun_result_obj,
            paths=paths,
            display_output=display_output,
            terminal_interface=terminal_interface,
            phase="post_prerequisite_rerun_paused",
        ):
            return
        if rerun_success:
            write_workflow_state(
                paths.config_dir,
                {
                    "active_incident_category": None,
                    "active_incident_summary": None,
                    "active_repair_phase": "runtime_verified",
                },
            )
            _clear_runtime_repair_followup(paths)
            _clear_active_runtime_objective(paths, chat=terminal_interface)
            _maybe_launch_repo_access(
                repo=repo,
                current=current,
                paths=paths,
                display_output=display_output,
                approve_prompt=approve_prompt,
                terminal_interface=terminal_interface,
            )
            return
        run_summary = rerun_message
        incident = summarize_failure_incident(
            command=runtime_command_override or repo.name,
            stderr=run_summary,
        )
        runtime_repair_plan = plan_runtime_repair(
            repo_name=repo.name,
            project_dir=project_dir,
            incident=incident,
        )

    dependency_repair_status = _attempt_runtime_dependency_repair(
        repo=repo,
        current=current,
        paths=paths,
        display_output=display_output,
        approve_prompt=approve_prompt,
        incident=incident,
        execution_target=execution_target,
        project_dir=project_dir,
        runtime_cwd=runtime_cwd,
        workflow=workflow,
        chat=terminal_interface,
    )
    if dependency_repair_status == "awaiting_approval":
        _update_active_runtime_objective(
            paths,
            chat=terminal_interface,
            active_objective_status="needs_user_decision",
            active_objective_requires_user_decision=True,
            active_objective_last_blocker=incident.summary,
            active_objective_resume_hint=f"Last time Duckln was fixing {repo.name}. Latest blocker: {_compact_runtime_summary(incident.summary)}",
            active_repair_phase="awaiting_runtime_dependency_approval",
        )
        return
    if dependency_repair_status == "installed":
        _update_active_runtime_objective(paths, chat=terminal_interface, active_repair_phase="post_dependency_repair_rerun")
        display_output(
            render_tool_invocation_trace(
                title="post-dependency-repair rerun",
                tool_id="process.session_runtime",
                action=f"Re-check {repo.name} after repo dependency repair",
                detail_lines=(
                    f"Repo re-check: {repo.name}",
                    "Duckln repaired the smallest manifest-backed dependency path and is re-running the runtime command before escalating.",
                ),
                execution_target=execution_target,
            )
        )
        rerun_result_obj = None
        if runtime_command_override:
            rerun_success, rerun_message = _run_bounded_runtime_check(
                repo=repo,
                paths=paths,
                display_output=display_output,
                command=runtime_command_override,
                execution_target=execution_target,
                runtime_cwd=runtime_cwd,
                workflow=workflow,
            )
        else:
            rerun_result_obj = run_prepared_repo(
                repo,
                current.mode,
                paths,
                approve=approve_prompt,
                display=display_output,
                verification_only=False,
                terminal_executor=terminal_interface,
                runtime_command_override=runtime_command_override,
                runtime_step_preapproved=True,
            )
            rerun_success = rerun_result_obj.verification_passed
            rerun_message = rerun_result_obj.message
        if not getattr(rerun_result_obj, "message_already_displayed", False):
            display_output(rerun_message)
        if _handle_non_repairable_runtime_result(
            repo=repo,
            result_obj=rerun_result_obj,
            paths=paths,
            display_output=display_output,
            terminal_interface=terminal_interface,
            phase="post_dependency_rerun_paused",
        ):
            return
        if rerun_success:
            write_workflow_state(
                paths.config_dir,
                {
                    "active_incident_category": None,
                    "active_incident_summary": None,
                    "active_repair_phase": "runtime_verified",
                },
            )
            _clear_runtime_repair_followup(paths)
            _clear_active_runtime_objective(paths, chat=terminal_interface)
            _maybe_launch_repo_access(
                repo=repo,
                current=current,
                paths=paths,
                display_output=display_output,
                approve_prompt=approve_prompt,
                terminal_interface=terminal_interface,
            )
            return
        run_summary = rerun_message
        incident = summarize_failure_incident(
            command=runtime_command_override or repo.name,
            stderr=run_summary,
        )

    repair_evidence = _show_runtime_repair_evidence(
        repo_name=repo.name,
        failing_command=runtime_command_override or repo.name,
        run_summary=run_summary,
        display_output=display_output,
        execution_target=execution_target,
    )
    _update_active_runtime_objective(paths, chat=terminal_interface, active_repair_phase="runtime_repair_evidence")
    repair_context_lines = [
        f"Duckln found a run issue for {repo.name}.",
        "Repair may include bounded environment or dependency fixes, and Duckln will still ask before any package install.",
    ]
    if repair_evidence.search_query:
        repair_context_lines.append(f"Search query: {repair_evidence.search_query}")
    plan_text = "\n".join(
        _runtime_repair_approval_plan_lines(
            repo_name=repo.name,
            runtime_repair_plan=runtime_repair_plan,
            repair_evidence=repair_evidence,
        )
    )
    display_output(join_blocks("\n".join(repair_context_lines), plan_text))
    proposed_action = runtime_repair_plan.repair_command or runtime_repair_plan.action_key
    blocker_line = _compact_runtime_summary(incident.summary)[:120]
    approval_prompt_text = (
        f"Duckln wants to run: `{proposed_action}`\n"
        f"Reason: {blocker_line}\n"
        f"Approve repair of {repo.name}? [y/n]"
    )
    should_repair = True if approve_prompt is None else approve_prompt(approval_prompt_text)
    if _runtime_objective_is_preapproved(workflow, repo_key=repo.repo_url):
        should_repair = True
    if not should_repair:
        _mark_runtime_repair_declined(
            paths=paths,
            repo=repo,
            run_summary=run_summary,
            incident=incident,
            terminal_interface=terminal_interface,
        )
        display_output(f"Duckln paused the {repo.name} repair because you said No. Say retry or fix it to reopen this blocker.")
        return

    display_output(
        render_tool_invocation_trace(
            title="runtime repair handoff",
            tool_id="subagents.dispatch",
            action=f"Hand off {repo.name} to the bounded repair workflow",
            detail_lines=(
                f"Repo with blocker: {repo.name}",
                f"Latest run summary: {_compact_runtime_summary(run_summary)}",
                "Duckln will reuse the repo bring-up workflow for repair.",
                (
                    "Duckln already surfaced official-doc or bounded web evidence for this blocker."
                    if repair_evidence.note
                    else "Duckln is proceeding with repo-grounded repair without extra web evidence."
                ),
                "Any dependency install during repair will still require explicit approval with repo evidence and official references.",
            ),
            execution_target=execution_target,
            source_urls=repair_evidence.source_urls,
            search_query=repair_evidence.search_query,
        )
    )
    write_workflow_state(
        paths.config_dir,
        {
            "active_incident_category": incident.category,
            "active_incident_summary": incident.summary,
            "active_repair_phase": "repair_handoff",
        },
    )
    _update_active_runtime_objective(paths, chat=terminal_interface, active_repair_phase="repair_handoff")
    context_service = AgentContextService()
    context_service.record_repair_learning(
        config_dir=paths.config_dir,
        repo=repo,
        summary=run_summary,
        signal="failure",
        metadata={"stage": "run"},
    )
    repair_result = bring_up_selected_repo(
        repo,
        current.mode,
        paths,
        approve=approve_prompt,
        display=display_output,
        runtime_provider=current.provider.value,
        execution_target=execution_target,
        vm_name=_session_vm_name(paths.config_dir) if execution_target == "vm" else None,
    )
    display_output(repair_result.message)
    context_service.record_repair_learning(
        config_dir=paths.config_dir,
        repo=repo,
        summary=repair_result.message,
        signal="verified_success" if repair_result.verification_passed else "verified_failure",
        metadata={"stage": "repair"},
    )
    if repair_result.verification_passed:
        write_workflow_state(
            paths.config_dir,
            {
                "active_incident_category": None,
                "active_incident_summary": None,
                "active_repair_phase": "repair_verified",
            },
        )
        _update_active_runtime_objective(paths, chat=terminal_interface, active_repair_phase="repair_verified")
        display_output(
            render_tool_invocation_trace(
                title="post-repair rerun",
                tool_id="process.session_runtime",
                action=f"Re-run {repo.name} after bounded repair verification",
                detail_lines=(
                    f"Repo repaired: {repo.name}",
                    "Duckln verified the repair path and is now re-running the stored runtime command.",
                    "If the runtime still fails, Duckln will keep the blocker in tracked workflow state instead of pretending success.",
                ),
                execution_target=execution_target,
            )
        )
        _update_active_runtime_objective(paths, chat=terminal_interface, active_repair_phase="post_repair_rerun")
        rerun_result_obj = None
        if runtime_command_override:
            rerun_success, rerun_message = _run_bounded_runtime_check(
                repo=repo,
                paths=paths,
                display_output=display_output,
                command=runtime_command_override,
                execution_target=execution_target,
                runtime_cwd=runtime_cwd,
                workflow=workflow,
            )
        else:
            rerun_result_obj = run_prepared_repo(
                repo,
                current.mode,
                paths,
                approve=approve_prompt,
                display=display_output,
                verification_only=False,
                terminal_executor=terminal_interface,
                runtime_command_override=runtime_command_override,
                runtime_step_preapproved=True,
            )
            rerun_success = rerun_result_obj.verification_passed
            rerun_message = rerun_result_obj.message
        if not getattr(rerun_result_obj, "message_already_displayed", False):
            display_output(rerun_message)
        if _handle_non_repairable_runtime_result(
            repo=repo,
            result_obj=rerun_result_obj,
            paths=paths,
            display_output=display_output,
            terminal_interface=terminal_interface,
            phase="post_repair_rerun_paused",
        ):
            return
        if rerun_success:
            write_workflow_state(
                paths.config_dir,
                {
                    "active_incident_category": None,
                    "active_incident_summary": None,
                    "active_repair_phase": "runtime_verified",
                },
            )
            _clear_runtime_repair_followup(paths)
            _clear_active_runtime_objective(paths, chat=terminal_interface)
            _maybe_launch_repo_access(
                repo=repo,
                current=current,
                paths=paths,
                display_output=display_output,
                approve_prompt=approve_prompt,
                terminal_interface=terminal_interface,
            )
        else:
            _persist_runtime_repair_followup(paths=paths, repo_key=repo.repo_url, repo_name=repo.name)
            write_workflow_state(
                paths.config_dir,
                {
                    "active_repo_key": repo.repo_url,
                    "active_repo_name": repo.name,
                    "active_issue_kind": "run_issue",
                    "active_issue_summary": rerun_message,
                    "active_repair_phase": "post_repair_runtime_failed",
                },
            )
            _update_active_runtime_objective(
                paths,
                chat=terminal_interface,
                active_objective_status="needs_user_decision",
                active_objective_requires_user_decision=True,
                active_objective_last_blocker=rerun_message,
                active_objective_resume_hint=f"Last time Duckln was fixing {repo.name}. Latest blocker: {_compact_runtime_summary(rerun_message)}",
                active_repair_phase="post_repair_runtime_failed",
            )
    else:
        _persist_runtime_repair_followup(paths=paths, repo_key=repo.repo_url, repo_name=repo.name)
        write_workflow_state(
            paths.config_dir,
            {
                "active_repo_key": repo.repo_url,
                "active_repo_name": repo.name,
                "active_issue_kind": "run_issue",
                "active_issue_summary": repair_result.message,
                "active_repair_phase": "repair_failed",
            },
        )
        _update_active_runtime_objective(
            paths,
            chat=terminal_interface,
            active_objective_status="needs_user_decision",
            active_objective_requires_user_decision=True,
            active_objective_last_blocker=repair_result.message,
            active_objective_resume_hint=f"Last time Duckln was fixing {repo.name}. Latest blocker: {_compact_runtime_summary(repair_result.message)}",
            active_repair_phase="repair_failed",
        )


def _drain_terminal_runtime_incidents(
    *,
    current: AppConfig,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    approve_prompt: Callable[[str], bool] | None,
    terminal_interface: object | None,
) -> None:
    if terminal_interface is None or not hasattr(terminal_interface, "consume_terminal_incidents"):
        return
    # Plan 74 fix: when Plan Mode is ON, Duckln must NOT auto-triage/repair
    # terminal incidents (the "Working… triaging the runtime blocker" loop).
    # A failed approved-plan step already routes through the visible
    # attribution → amendment flow. Drain the queue so it doesn't pile up, then
    # return without launching the repair workflow.
    if getattr(current, "plan_mode_enabled", False):
        try:
            terminal_interface.consume_terminal_incidents()
        except Exception:
            pass
        return
    workflow = _AGENT_CONTEXT_SERVICE.load_workflow_state(config_dir=paths.config_dir)
    if workflow is None or _runtime_workflow_blocks_incident_repair(workflow):
        return
    try:
        incidents = terminal_interface.consume_terminal_incidents()
    except Exception:
        return
    if not incidents:
        return
    repo_key = workflow.active_runtime_repo_key or workflow.repo_key
    repo_name = workflow.active_runtime_repo_name or workflow.repo_name
    if not repo_name:
        return
    followup_state = read_followup_state(paths.config_dir)
    active_incident_fingerprint = None
    if workflow.active_incident_category and workflow.active_incident_summary:
        active_incident_fingerprint = _runtime_incident_fingerprint(
            category=workflow.active_incident_category,
            summary=workflow.active_incident_summary,
        )
    repair_offer_pending = (
        str(followup_state.get("pending_next_action") or "").strip() == "repair_repo_runtime"
        or str(followup_state.get("pending_offer_kind") or "").strip() == "repair_repo_runtime"
    )
    drain_started_at = time.monotonic()
    for incident in _dedupe_runtime_incidents(incidents):
        drain_elapsed = time.monotonic() - drain_started_at
        if drain_elapsed > _DRAIN_TOTAL_BUDGET_SECONDS:
            display_output(
                f"Duckln spent {drain_elapsed:.0f}s total on runtime repair across all incidents — stopping. "
                f"Manual action needed."
            )
            break
        category = str(incident.get("category") or "unknown_failure").strip() or "unknown_failure"
        summary = str(incident.get("summary") or "").strip()
        if not summary:
            continue
        incident_search_text = _terminal_incident_search_text(incident, fallback_summary=summary)
        incident_fingerprint = _runtime_incident_fingerprint(category=category, summary=summary)
        if active_incident_fingerprint == incident_fingerprint:
            continue
        guidance = _runtime_issue_guidance(
            repo_name=repo_name,
            repo_key=repo_key,
            paths=paths,
            incident_summary=summary,
            incident_category=category,
        )
        display_output(
            render_tool_invocation_trace(
                title="terminal incident intake",
                tool_id="process.terminal_incident_watch",
                action=f"Capture a runtime blocker for {repo_name} from the terminal pane",
                detail_lines=(
                    f"Incident category: {category}",
                    f"Incident summary: {_compact_runtime_summary(summary)}",
                    "Duckln is converting terminal output into bounded follow-up state instead of ignoring it.",
                ),
                execution_target=workflow.active_runtime_execution_target or _session_execution_target(paths.config_dir),
            )
        )
        workflow_updates = {
            "active_repo_key": repo_key,
            "active_repo_name": repo_name,
            "active_issue_kind": "runtime_input_issue" if guidance else "run_issue",
            "active_issue_summary": guidance or summary,
            "active_incident_category": category,
            "active_incident_summary": incident_search_text,
            "active_repair_phase": (
                "waiting_on_app_prompt"
                if category == "app_prompt_active"
                else ("guidance_only" if guidance else "runtime_failed")
            ),
        }
        if category == "app_prompt_active":
            workflow_updates.update(
                {
                    "active_objective_status": "needs_user_decision",
                    "active_objective_requires_user_decision": True,
                }
            )
        write_workflow_state(paths.config_dir, workflow_updates)
        _sync_chat_objective_status(chat=terminal_interface, config_dir=paths.config_dir)
        if guidance:
            _clear_runtime_repair_followup(paths)
            repair_offer_pending = False
            active_incident_fingerprint = incident_fingerprint
            display_output(guidance)
            continue
        if _runtime_objective_is_preapproved(workflow, repo_key=repo_key):
            repo = _resolve_repo_from_action_key(paths.config_dir, repo_key)
            if repo is not None:
                _set_chat_activity(
                    terminal_interface,
                    f"Thinking... continuing repair for {repo.name}",
                    spinner=True,
                )
                try:
                    _run_runtime_repair_workflow(
                        repo=repo,
                        current=current,
                        paths=paths,
                        display_output=display_output,
                        approve_prompt=approve_prompt,
                        terminal_interface=terminal_interface,
                        run_summary=summary,
                        runtime_command_override=workflow.active_runtime_command,
                        workflow=workflow,
                    )
                finally:
                    _clear_chat_activity(terminal_interface)
                workflow = _AGENT_CONTEXT_SERVICE.load_workflow_state(config_dir=paths.config_dir)
                repair_offer_pending = False
                active_incident_fingerprint = incident_fingerprint
                continue
        _persist_runtime_repair_followup(paths=paths, repo_key=repo_key, repo_name=repo_name)
        repair_offer_pending = True
        active_incident_fingerprint = incident_fingerprint
        observed_command = _effective_runtime_command(
            runtime_command_override=None,
            workflow=workflow,
        ) or repo_name
        _show_runtime_repair_evidence(
            repo_name=repo_name,
            failing_command=observed_command,
            run_summary=summary,
            display_output=display_output,
            execution_target=workflow.active_runtime_execution_target or _session_execution_target(paths.config_dir),
        )
        _set_chat_activity(
            terminal_interface,
            f"Awaiting approval... {repo_name} repair is paused",
            spinner=False,
        )
        display_output(
            f"Duckln captured a live runtime blocker for {repo_name}. Say yes, fix it, or repair it and Duckln will continue with a bounded repair path. Duckln is paused until you approve."
        )


def _handle_pending_runtime_repair_followup(
    *,
    command: str,
    current: AppConfig,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    approve_prompt: Callable[[str], bool] | None,
    terminal_interface: object | None,
) -> bool:
    followup_state = read_followup_state(paths.config_dir)
    pending_next_action = str(followup_state.get("pending_next_action") or "").strip()
    pending_offer_kind = str(followup_state.get("pending_offer_kind") or "").strip()
    workflow = _AGENT_CONTEXT_SERVICE.load_workflow_state(config_dir=paths.config_dir)
    workflow_objective_id = _workflow_objective_identity(workflow)
    followup_objective_id = (
        str(followup_state.get("pending_offer_objective_id") or "").strip()
        or str(followup_state.get("pending_objective_id") or "").strip()
        or None
    )
    if workflow_objective_id and followup_objective_id and workflow_objective_id != followup_objective_id:
        workflow = None
    active_issue_kind = "" if workflow is None else str(workflow.active_issue_kind or "").strip()
    active_repair_phase = "" if workflow is None else str(workflow.active_repair_phase or "").strip()
    explicit_repair_request = _looks_like_runtime_repair_request(command)
    if active_repair_phase == "repair_declined" and not explicit_repair_request:
        if _looks_like_runtime_issue_explanation_request(command):
            display_output((None if workflow is None else workflow.active_issue_summary) or "Duckln paused this repair because you said No.")
            return True
        return False
    runtime_repair_active = (
        pending_next_action == "repair_repo_runtime"
        or pending_offer_kind == "repair_repo_runtime"
        or active_issue_kind in {"run_issue", "verify_issue", "runtime_input_issue"}
        or active_repair_phase
        in {
            "runtime_failed",
            "repair_handoff",
            "repair_failed",
            "post_repair_runtime_failed",
            "awaiting_runtime_prerequisite_approval",
            "runtime_dependency_repair_running",
        }
    )
    if not runtime_repair_active:
        return False
    repo_key = None if workflow is None else workflow.active_runtime_repo_key or workflow.repo_key
    repo_name = None if workflow is None else workflow.active_runtime_repo_name or workflow.repo_name
    if repo_key is None:
        repo_key = str(followup_state.get("pending_repo_key") or "").strip() or None
    if repo_name is None:
        repo_name = str(followup_state.get("pending_repo_name") or "").strip() or None
    if _looks_like_runtime_issue_explanation_request(command):
        display_output((None if workflow is None else workflow.active_issue_summary) or "Duckln is tracking a runtime blocker but does not yet have a compact summary.")
        return True
    if not explicit_repair_request:
        return False
    if repo_key is None:
        display_output("Duckln lost the active runtime context for that blocker, so name the repo once and I’ll re-anchor the repair path.")
        _clear_runtime_repair_followup(paths)
        return True
    repo = _resolve_repo_from_action_key(paths.config_dir, repo_key)
    if repo is None:
        display_output("Duckln could not resolve the repo for the pending runtime repair request.")
        _clear_runtime_repair_followup(paths)
        return True
    # Plan 169 F5: the user just accepted the resume, but the repair needs the model. If it's
    # not reachable, tell them NOW and stop — never set the spinner + spin the minutes-long
    # loop pretending to work (honest-stop, mirroring the bring-up flow). Gated at the
    # user-facing resume entry so the shared deterministic repair workflow is unaffected.
    if not _repair_model_reachable(paths.config_dir):
        from duckln.plan_mode import model_unreachable_message

        provider = read_config_snapshot(paths.config_dir).get("provider")
        display_output(_with_runtime_context_hint(model_unreachable_message(provider)))
        display_output(
            f"Duckln can't reach the model the {repo.name} repair needs — switch/fix the "
            "provider (e.g. `/provider`), then say continue again. Nothing is running."
        )
        _clear_chat_activity(terminal_interface)
        return True
    _begin_active_runtime_objective(
        paths=paths,
        repo=repo,
        execution_target=(
            str(followup_state.get("active_execution_target") or "").strip()
            or ("local" if workflow is None or not workflow.active_objective_execution_target else workflow.active_objective_execution_target)
        ),
        runtime_command=None if workflow is None else workflow.active_runtime_command,
        goal=f"Get {repo.name} running through Duckln's tracked runtime path.",
        resume_hint=f"Last time Duckln was fixing {repo.name}.",
        chat=terminal_interface,
    )
    _update_active_runtime_objective(
        paths,
        chat=terminal_interface,
        active_objective_status="active",
        active_objective_requires_user_decision=False,
    )
    display_output(f"Duckln is continuing the bounded runtime repair path for {repo_name or repo.name}.")
    _run_runtime_repair_workflow(
        repo=repo,
        current=current,
        paths=paths,
        display_output=display_output,
        approve_prompt=approve_prompt,
        terminal_interface=terminal_interface,
        run_summary=(
            "Duckln captured a runtime blocker in the terminal pane."
            if workflow is None or not workflow.active_issue_summary
            else workflow.active_issue_summary
        ),
        runtime_command_override=None if workflow is None else workflow.active_runtime_command,
        workflow=workflow,
    )
    return True


def _handle_pending_repo_followup(
    *,
    command: str,
    current: AppConfig,
    paths: ConfigPaths,
    system_probe,
    display_output: Callable[[str], None],
    approve_prompt: Callable[[str], bool] | None,
    text_prompt: Callable[[str, str], str | None] | None,
    select_prompt: Callable[[str, tuple[str, ...]], str | None] | None,
    terminal_interface: object | None,
) -> bool:
    followup_state = read_followup_state(paths.config_dir)
    pending_next_action = str(followup_state.get("pending_next_action") or "").strip().lower()
    if pending_next_action not in {"set_up_repo", "run_repo", "verify_repo", "stop_repo"}:
        return False
    # Plan 195 (Dialogue_state_design.md §5/§6): a reply to the pending action resolves against a
    # CLOSED set (affirm / negate) FIRST — "yes lets run this" / "sure" / "run it" execute it, "no"
    # cancels — so it's never re-classified into a fresh clarify loop.
    if _is_negation(command):
        write_followup_state(paths.config_dir, {
            "pending_next_action": None, "pending_offer_kind": None, "pending_offer_id": None,
            "pending_offer_thread_id": None, "pending_repo_key": None, "pending_repo_name": None,
        })
        display_output("Okay, I'll hold off. Tell me what you'd like instead.")
        return True
    if not (_looks_like_runtime_repair_request(command) or _is_affirmation(command)):
        return False

    repo_key = str(followup_state.get("pending_repo_key") or "").strip() or None
    repo_name = str(followup_state.get("pending_repo_name") or "").strip() or None
    if repo_key is None:
        display_output("Duckln lost the active repo context for that pending step, so name the repo once and I’ll re-anchor it.")
        return True
    repo = _resolve_repo_from_action_key(paths.config_dir, repo_key)
    if repo is None:
        display_output("Duckln could not resolve the repo tied to the pending workflow step.")
        return True
    execution_target, vm_name = _followup_execution_context(paths)
    # Plan 195: consume the expectation once we commit to executing, so an affirm can't re-fire the
    # same pending action on a later turn (loop-breaker + expire-after-use); reset the clarify cap.
    write_followup_state(paths.config_dir, {
        "pending_next_action": None, "pending_offer_kind": None, "pending_offer_id": None,
        "pending_offer_thread_id": None, "clarify_count": None, "last_choice_prompt": None,
    })

    if pending_next_action == "set_up_repo":
        completed_step = str(followup_state.get("last_completed_step") or "").strip().lower()
        if completed_step == "vm_create_failed":
            display_output(f"Duckln is continuing the VM setup path for {repo_name or repo.name}.")
            create_result = create_multipass_vm(
                paths,
                text_prompt=text_prompt,
                display=display_output,
                approve_prompt=approve_prompt,
                system_probe=system_probe,
            )
            if create_result is None or not create_result.ok:
                return True
            configure_existing_multipass_vm(
                create_result.vm_name,
                paths,
                select=select_prompt,
                display=display_output,
            )
            execution_target = "vm"
            vm_name = create_result.vm_name
            _persist_session_execution_target(paths=paths, execution_target=execution_target, vm_name=vm_name)
            bring_up_selected_repo(
                repo,
                current.mode,
                paths,
                approve=approve_prompt,
                display=display_output,
                runtime_provider=current.provider.value,
                execution_target=execution_target,
                vm_name=vm_name,
                system_probe=system_probe,
            )
            _persist_repo_followup_state(
                paths=paths,
                system_probe=system_probe,
                repo=repo,
                completed_step="use_vm",
                pending_next_action="run_repo",
                execution_target=execution_target,
                vm_name=vm_name,
            )
            return True
        # Plan 79 Fix 2: resume in the SAME mode the operation was started in.
        # In Plan Mode that means re-DRAFTING the plan (e.g. now that the model is
        # reachable) and letting the user approve again — not a legacy direct run.
        plan_mode_on = bool(getattr(current, "plan_mode_enabled", False))
        display_output(f"Duckln is picking up where it left off for {repo_name or repo.name}.")
        bring_up_selected_repo(
            repo,
            current.mode,
            paths,
            approve=approve_prompt,
            display=display_output,
            runtime_provider=current.provider.value,
            execution_target=execution_target,
            vm_name=vm_name,
            system_probe=system_probe,
            pane_executor=terminal_interface,
            plan_mode_enabled=plan_mode_on,
            emit_thought=(
                (lambda t: _emit_thought(terminal_interface, t, display=display_output))
                if plan_mode_on else None
            ),
        )
        _persist_repo_followup_state(
            paths=paths,
            system_probe=system_probe,
            repo=repo,
            completed_step="set_up_repo",
            pending_next_action="set_up_repo" if plan_mode_on else "run_repo",
            execution_target=execution_target,
            vm_name=vm_name,
        )
        return True

    synthetic_reply = FreeTextReply(
        text="",
        intent={
            "run_repo": "repo_run",
            "verify_repo": "repo_verify",
            "stop_repo": "repo_stop",
        }[pending_next_action],
        action={
            "run_repo": "run_repo",
            "verify_repo": "verify_repo",
            "stop_repo": "stop_repo",
        }[pending_next_action],
        action_repo_key=repo_key,
    )
    display_output(f"Duckln is continuing the pending {pending_next_action.replace('_', ' ')} step for {repo_name or repo.name}.")
    if pending_next_action == "run_repo":
        _handle_repo_run_action(
            reply=synthetic_reply,
            current=current,
            paths=paths,
            display_output=display_output,
            approve_prompt=approve_prompt,
            text_prompt=text_prompt,
            terminal_interface=terminal_interface,
        )
        return True
    if pending_next_action == "verify_repo":
        _handle_repo_verify_action(
            reply=synthetic_reply,
            current=current,
            paths=paths,
            display_output=display_output,
            approve_prompt=approve_prompt,
            text_prompt=text_prompt,
            terminal_interface=terminal_interface,
        )
        return True
    _handle_repo_stop_action(
        reply=synthetic_reply,
        current=current,
        paths=paths,
        display_output=display_output,
        approve_prompt=approve_prompt,
        terminal_interface=terminal_interface,
    )
    return True


def _handle_repo_runtime_action(
    *,
    reply: FreeTextReply,
    current: AppConfig,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    approve_prompt: Callable[[str], bool] | None,
    text_prompt: Callable[[str, str], str | None] | None,
    verification_only: bool,
    terminal_interface: object | None = None,
) -> None:
    repo = _resolve_repo_from_action_key(paths.config_dir, reply.action_repo_key)
    if repo is None:
        display_output("Supervisor agent could not resolve that repo from Duckln’s tracked setup state.")
        return
    workflow = _AGENT_CONTEXT_SERVICE.load_workflow_state(config_dir=paths.config_dir)
    display_output(
        render_tool_invocation_trace(
            title="repo runtime review",
            tool_id="verification.repo_checks" if verification_only else "process.session_runtime",
            action=(
                f"Verify {repo.name} with a bounded live check"
                if verification_only
                else f"Run {repo.name} through Duckln's tracked runtime path"
            ),
            detail_lines=_repo_runtime_review_items(
                config_dir=paths.config_dir,
                repo_key=reply.action_repo_key,
                repo_name=repo.name,
                verification_only=verification_only,
            ),
            execution_target=_session_execution_target(paths.config_dir),
        )
    )

    runtime_command_override = None
    if not verification_only:
        runtime_command_override, materialization_cancelled = _materialize_cli_task_command(
            repo=repo,
            paths=paths,
            display_output=display_output,
            text_prompt=text_prompt,
            terminal_interface=terminal_interface,
        )
        if materialization_cancelled:
            return

    prompt = (
        f"Do you want Duckln to verify {repo.name} with a bounded live check now?"
        if verification_only
        else (
            f"Run {repo.name}: {runtime_command_override}"
            if runtime_command_override
            else f"Do you want Duckln to run {repo.name} now?"
        )
    )
    preapproved = _runtime_objective_is_preapproved(workflow, repo_key=repo.repo_url)
    should_run = True if preapproved or approve_prompt is None else approve_prompt(prompt)
    if not should_run:
        cancelled_text = (
            f"Supervisor agent cancelled verifying {repo.name}."
            if verification_only
            else f"Supervisor agent cancelled running {repo.name}."
        )
        display_output(cancelled_text)
        write_workflow_state(
            paths.config_dir,
            {
                "active_repo_key": repo.repo_url,
                "active_repo_name": repo.name,
                "active_issue_kind": "run_issue",
                "active_issue_summary": (
                    f"Duckln is still waiting on the next bounded verification step for {repo.name}."
                    if verification_only
                    else f"Duckln is still waiting on the next bounded run step for {repo.name}."
                ),
                "active_repair_phase": "awaiting_runtime_approval",
            },
        )
        _begin_active_runtime_objective(
            paths=paths,
            repo=repo,
            execution_target=_session_execution_target(paths.config_dir),
            runtime_command=runtime_command_override,
            goal=(
                f"Verify {repo.name} with a bounded live check."
                if verification_only
                else f"Get {repo.name} running through Duckln's tracked runtime path."
            ),
            resume_hint=f"Last time Duckln was fixing {repo.name}.",
            chat=terminal_interface,
        )
        _update_active_runtime_objective(
            paths,
            chat=terminal_interface,
            active_objective_status="needs_user_decision",
            active_objective_requires_user_decision=True,
            active_repair_phase="awaiting_runtime_approval",
        )
        return
    _begin_active_runtime_objective(
        paths=paths,
        repo=repo,
        execution_target=_session_execution_target(paths.config_dir),
        runtime_command=runtime_command_override,
        goal=(
            f"Verify {repo.name} with a bounded live check."
            if verification_only
            else f"Get {repo.name} running through Duckln's tracked runtime path."
        ),
        resume_hint=f"Last time Duckln was fixing {repo.name}.",
        chat=terminal_interface,
    )

    run_result = run_prepared_repo(
        repo,
        current.mode,
        paths,
        approve=approve_prompt,
        display=display_output,
        verification_only=verification_only,
        terminal_executor=terminal_interface,
        runtime_command_override=runtime_command_override,
        runtime_step_preapproved=True,
    )
    _sync_chat_execution_context(chat=terminal_interface, config_dir=paths.config_dir)
    if getattr(run_result, "message_already_displayed", False) is not True:
        display_output(run_result.message)
    if run_result.verification_passed:
        write_workflow_state(
            paths.config_dir,
            {
                "active_incident_category": None,
                "active_incident_summary": None,
                "active_repair_phase": "runtime_verified",
            },
        )
        _clear_active_runtime_objective(paths, chat=terminal_interface)
        if not verification_only:
            _maybe_launch_repo_access(
                repo=repo,
                current=current,
                paths=paths,
                display_output=display_output,
                approve_prompt=approve_prompt,
                terminal_interface=terminal_interface,
            )
        return

    if not getattr(run_result, "should_offer_repair", True):
        _handle_non_repairable_runtime_result(
            repo=repo,
            result_obj=run_result,
            paths=paths,
            display_output=display_output,
            terminal_interface=terminal_interface,
            phase="runtime_manual_step_required",
        )
        return
    _run_runtime_repair_workflow(
        repo=repo,
        current=current,
        paths=paths,
        display_output=display_output,
        approve_prompt=approve_prompt,
        terminal_interface=terminal_interface,
        run_summary=run_result.message,
        runtime_command_override=runtime_command_override,
    )


def _remember_current_context(*, current: AppConfig, paths: ConfigPaths) -> str:
    store = initialize_state_store(paths.config_dir)
    latest_repo = store.get_latest_repo_state()
    repo_text = "No active repo is currently tracked."
    if latest_repo is not None:
        repo_name = str(latest_repo.metadata.get("repo_name") or "").strip() or (latest_repo.repo_path or latest_repo.repo_key or "repo")
        repo_text = f"Latest repo: {repo_name} ({latest_repo.status})."
    system_bits: list[str] = []
    snapshot = store.read_config_values(prefix="system.")
    ram_gib = snapshot.get("system.ram_gib")
    cpu_cores = snapshot.get("system.cpu_logical_cores")
    gpu_summary = snapshot.get("system.gpu_summary")
    if cpu_cores:
        system_bits.append(f"{cpu_cores} CPU")
    if ram_gib:
        system_bits.append(f"{ram_gib} GiB RAM")
    if gpu_summary:
        system_bits.append(gpu_summary)
    hardware_text = f" Hardware: {', '.join(system_bits)}." if system_bits else ""
    summary = (
        f"Mode {current.mode.label}; provider {current.provider.label}; model {current.model}; "
        f"{repo_text}{hardware_text}"
    )
    write_session_summary_state(paths.config_dir, session_id="remember-latest", summary=summary)
    return f"Supervisor agent remembered the current context. {summary}"


def _resolve_repo_state_from_action_key(config_dir: Path, action_repo_key: str | None):
    rows = initialize_state_store(config_dir).list_repo_states()
    if action_repo_key:
        lowered = action_repo_key.lower()
        for row in rows:
            repo_name = str(row.metadata.get("repo_name") or "").strip().lower()
            repo_url = str(row.repo_url or row.repo_key or "").strip().lower()
            repo_key = str(row.repo_key or "").strip().lower()
            if lowered in {repo_name, repo_url, repo_key}:
                return row
    return rows[0] if rows else None


def _repo_display_name(row) -> str:
    return str(row.metadata.get("repo_name") or row.repo_path or row.repo_key or "repo").strip()


def _repo_target_label(row) -> str:
    if row.execution_target == "vm":
        vm_name = row.vm_name or str(row.metadata.get("vm_name") or row.metadata.get("execution_vm_name") or "").strip()
        return f"VM {vm_name}" if vm_name else "VM"
    if row.execution_target == "docker":
        docker_name = str(
            row.metadata.get("docker_name")
            or row.metadata.get("container_name")
            or row.metadata.get("docker_container")
            or row.metadata.get("last_docker_name")
            or ""
        ).strip()
        return f"Docker {docker_name}" if docker_name else "Docker"
    if row.execution_target in {"aws", "gcp"}:
        provider = row.execution_target.upper()
        resource_name = str(
            row.metadata.get("cloud_resource_name")
            or row.metadata.get("cloud_instance_name")
            or row.metadata.get("instance_name")
            or row.metadata.get("cloud_resource_key")
            or ""
        ).strip()
        region = str(row.metadata.get("cloud_region") or row.metadata.get("region") or "").strip()
        details = " ".join(part for part in (resource_name, region) if part)
        return f"{provider} {details}" if details else f"{provider} remote"
    return "local machine"


def _render_tracked_repos_summary(config_dir: Path, *, target_filter: str | None = None) -> str:
    rows = initialize_state_store(config_dir).list_repo_states()
    filter_text = str(target_filter or "").strip()
    if filter_text:
        needle = filter_text.casefold()
        rows = tuple(
            row
            for row in rows
            if needle in _repo_target_label(row).casefold()
            or needle == str(row.vm_name or "").casefold()
            or needle == str(row.metadata.get("docker_name") or row.metadata.get("container_name") or "").casefold()
            or needle == str(row.metadata.get("cloud_resource_name") or row.metadata.get("instance_name") or "").casefold()
        )
    if not rows:
        return f"Duckln is not tracking any repos on {filter_text} yet." if filter_text else "Duckln is not tracking any repos yet."
    title = f"Tracked repos on {filter_text}" if filter_text else "Tracked repos"
    return join_blocks(
        paragraph_block(
            f"Duckln is currently tracking these repos on {filter_text}:"
            if filter_text
            else "Duckln is currently tracking these repos:"
        ),
        comparison_block(
            title,
            tuple(
                (
                    _repo_display_name(row),
                    " • ".join(
                        part
                        for part in (
                            row.status,
                            _repo_target_label(row),
                            f"created {row.created_at.split('T')[0]}" if row.created_at else "",
                            f"verified {row.last_verified_at.split('T')[0]}" if row.last_verified_at else "",
                        )
                        if part
                    ),
                )
                for row in rows[:8]
            ),
        ),
    )


def _render_active_repo_summary(config_dir: Path) -> str:
    row = initialize_state_store(config_dir).get_latest_repo_state()
    if row is None:
        return "Duckln does not currently have a selected repo context or a live repo session."
    path = str(row.metadata.get("install_location") or row.repo_path or "").strip()
    path_line = f" Recorded path: {path}." if path else ""
    if row.status in {"running", "interactive"}:
        return f"Live repo session is {_repo_display_name(row)} with status {row.status} on {_repo_target_label(row)}.{path_line}"
    return f"Current selected repo context is {_repo_display_name(row)} with status {row.status} on {_repo_target_label(row)}.{path_line}"


def _render_skills_summary(config_dir: Path) -> str:
    """Read-only renderer for /skills — list materialized skill notes from SQLite memory.

    Surfaces the agent skills the user (or auto-bootstrappers) have registered, so the
    user can see what extras Duckln has on hand (internet search, deploy recipes, etc.)
    without poking around the filesystem."""

    try:
        store = initialize_state_store(config_dir)
        records = tuple(record for record in store.list_managed_memory_records() if record.memory_kind == "skill")
    except Exception as exc:
        return f"Could not read skills memory: {exc}"
    record_by_path = {record.relative_path: record for record in records}
    try:
        skills_dir = resolve_agent_memory_paths(config_dir).skills_dir
        file_paths = tuple(sorted(skills_dir.glob("*.md"))) if skills_dir.exists() else ()
    except Exception:
        file_paths = ()
    file_only = tuple(path for path in file_paths if f"skills/{path.name}" not in record_by_path)
    builtin_skills = (
        ("Repo README bring-up", "Built in • reads README/manifests before setup"),
        ("Debug / Recovery", "Built in • classifies setup failures and bounded next actions"),
        ("VM / Docker target routing", "Built in • keeps repo actions scoped to local, VM, Docker, or cloud"),
    )
    total_count = len(records) + len(file_only) + len(builtin_skills)
    if total_count == 0:
        return (
            "No skills materialized yet. Skills get registered automatically as Duckln "
            "uses them — e.g. enable internet search with /internet on."
        )
    lines = [f"Duckln skills ({total_count} available):"]
    # Plan 73 Phase C: separate auto-LEARNED skills (from verified/failed runs)
    # from manual/memory skills, and show their family·os·target key + source.
    learned: list[str] = []
    other_records: list = []
    for record in records:
        slug = record.relative_path.split("/")[-1].removesuffix(".md")
        if slug.startswith("setup-"):
            key = slug[len("setup-"):]
            if key.endswith("-avoid"):
                learned.append(f"  • {key[:-len('-avoid')]} — ⚠ known-failure (learned; avoid this path) [{slug}]")
            else:
                learned.append(f"  • {key} — ✓ verified-run (learned; injected into future plans) [{slug}]")
        else:
            other_records.append(record)
    if learned:
        lines.append("Learned from runs:")
        lines.extend(learned)
        lines.append("Built-in & manual:")
    for title, suffix in builtin_skills:
        lines.append(f"  • {title} ({suffix})")
    for record in other_records:
        title = (record.title or record.relative_path.split("/")[-1].removesuffix(".md")).strip()
        updated = (record.updated_at or "").split("T")[0]
        suffix = f" (memory skill • updated {updated})" if updated else " (memory skill)"
        lines.append(f"  • {title}{suffix}")
    for path in file_only:
        title = path.stem.replace("-", " ").replace("_", " ").strip().title() or path.name
        try:
            first_line = path.read_text(encoding="utf-8").splitlines()[0].strip()
        except Exception:
            first_line = ""
        if first_line.startswith("#"):
            title = first_line.lstrip("#").strip() or title
        lines.append(f"  • {title}")
    return "\n".join(lines)


def _render_skill_detail(config_dir: Path, slug: str) -> str:
    """Plan 73 Phase C: render a single skill note for `/skills show <slug>`."""
    from agent.memory import resolve_agent_memory_paths

    safe = slug.strip()
    try:
        skills_dir = resolve_agent_memory_paths(config_dir).skills_dir
        path = skills_dir / f"{safe}.md"
        if not path.exists():
            return f"No skill named '{safe}'. Run `/skills` to list available skills."
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except Exception as exc:
        return f"Could not read skill '{safe}': {exc}"


def _clear_learned_skills(config_dir: Path, *, approve: Callable[[str], bool] | None) -> str:
    """Plan 73 Phase C: wipe auto-learned (`setup-…`) skills with confirmation."""
    from agent.memory import resolve_agent_memory_paths

    try:
        skills_dir = resolve_agent_memory_paths(config_dir).skills_dir
        learned = [p for p in skills_dir.glob("setup-*.md")] if skills_dir.exists() else []
    except Exception as exc:
        return f"Could not access skills: {exc}"
    if not learned:
        return "No learned skills to clear."
    if approve is not None and not approve(f"Delete {len(learned)} learned skill(s)? [y/n]"):
        return "Cancelled — no skills deleted."
    deleted = 0
    for p in learned:
        try:
            p.unlink()
            deleted += 1
        except OSError:
            pass
    return f"Cleared {deleted} learned skill(s). Future runs will re-learn from scratch."


def _render_learning_status(config_dir: Path) -> str:
    """Plan 73 Phase C: `/learn` — show the self-learning loop status for the
    active repo: the verified skill (if any) that the next plan will inject,
    plus recent reflections."""
    from agent.memory import resolve_agent_memory_paths
    from duckln.plan_mode import find_matching_skill, plan_skill_signature

    lines = ["Self-learning loop status (Run → Reflect → Extract → Loop):"]
    row = initialize_state_store(config_dir).get_latest_repo_state()
    target = _session_execution_target(config_dir)
    if row is not None:
        # Best-effort family from stored metadata; OS from this host.
        family = str(row.metadata.get("repo_family") or "unknown")
        import platform as _pf

        sig = plan_skill_signature(repo_family=family, os_name=_pf.system(), execution_target=target)
        matched = find_matching_skill(
            config_dir, repo_family=family, os_name=_pf.system(), execution_target=target
        )
        lines.append(f"  Active repo: {_repo_display_name(row)} (family={family}, target={target})")
        lines.append(f"  Signature: {sig}")
        if matched:
            lines.append(f"  ✓ Next plan will INJECT verified skill: {matched}")
        else:
            lines.append("  No verified skill yet for this signature — the first successful run will create one.")
    else:
        lines.append("  No active repo selected. Run `/repos` to pick one.")
    # Recent reflections (session summaries named reflection-*).
    try:
        sessions_dir = resolve_agent_memory_paths(config_dir).sessions_dir
        refs = sorted(sessions_dir.glob("reflection-*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:3] if sessions_dir.exists() else []
        if refs:
            lines.append("  Recent reflections:")
            for p in refs:
                first = ""
                try:
                    body = p.read_text(encoding="utf-8", errors="replace")
                    first = next((ln.strip() for ln in body.splitlines() if ln.strip() and not ln.startswith("#")), "")
                except Exception:
                    first = ""
                lines.append(f"    • {first[:120]}")
    except Exception:
        pass
    return "\n".join(lines)


# Plan 187 F3 / Plan 188 F1: a "which/last/previous repo were we working on / ran" question is a
# SESSION-MEMORY question — answer it DIRECTLY from state (fast, clean, no repo_qa loop + no
# tool-name leak). ROBUST structural match (not a brittle phrase list): a REPO + a RECENCY marker
# + a first-person SESSION subject, and NOT a future/recommendation phrasing. High-precision
# (Plan 174): fires only on this narrow past-tense class ("which repo we ran last", "what did we
# run last", "the last repo we worked on"), never on "which repo should I set up next".
_PREV_REPO_EXCLUDE = ("recommend", "should i", "should we", "which one should", "set up a", "new repo", "next repo")
# Plan 196 F9: users say "project"/"app" interchangeably with "repo". Safe to broaden
# because `_maybe_answer_previous_repo` requires ALL THREE of this + recency + a first-person
# subject, so "how do I build an app" (no recency) never matches.
_PREV_REPO_RE = re.compile(r"\b(repo(sitor(y|ies))?|projects?|apps?)\b", re.IGNORECASE)
_PREV_REPO_RECENCY_RE = re.compile(
    r"\b(last|previous(ly)?|before|earlier|recent(ly)?|ago|working on|worked on|were we|did we|have we)\b",
    re.IGNORECASE,
)
_PREV_REPO_SUBJECT_RE = re.compile(r"\b(we|i|us|our)\b|\bdid we\b|\bwere we\b|\bhave we\b", re.IGNORECASE)


# Plan 195 (Dialogue_state_design.md): a SMALL closed-set reply classifier — affirm / negate — so a
# reply to a pending proposal ("yes lets run this", "sure", "go ahead", "run it") is matched against
# yes/no, NOT re-classified as a standalone message with no repo signal. This is the core of dialogue
# -state follow-through; it works for every phrasing because the set is closed.
_AFFIRM_EXACT = {
    "yes", "yeah", "yea", "yep", "yup", "ya", "y", "sure", "ok", "okay", "k", "kk", "alright",
    "all right", "absolutely", "definitely", "please", "yes please", "please do", "go ahead",
    "go for it", "do it", "lets go", "let's go", "sounds good", "sure thing", "yes go",
    "run it", "lets run it", "let's run it", "lets run this", "let's run this", "yes lets run this",
    "yes let's run this", "yes run it", "of course", "yeah do it", "yep do it",
}
_AFFIRM_START = ("yes ", "yeah ", "yep ", "sure ", "ok ", "okay ", "alright ", "absolutely ",
                 "definitely ", "please ", "yea ", "yup ")
_AFFIRM_CONTAINS = ("go ahead", "do it", "run it", "lets run", "let's run", "sounds good",
                    "go for it", "please do")
_NEGATE_EXACT = {"no", "nope", "nah", "naw", "not now", "no thanks", "no thank you", "cancel",
                 "stop", "dont", "don't", "negative", "not that", "not that one", "leave it"}
_NEGATE_START = ("no ", "nope ", "nah ", "not ", "cancel ", "stop ", "don't ", "dont ")


def _norm_reply(message: str) -> str:
    return " ".join(str(message or "").strip().lower().replace("’", "'").split()).rstrip("!.?")


def _is_affirmation(message: str) -> bool:
    m = _norm_reply(message)
    if not m:
        return False
    if m in _AFFIRM_EXACT or m.startswith(_AFFIRM_START):
        return True
    return any(p in m for p in _AFFIRM_CONTAINS)


def _is_negation(message: str) -> bool:
    m = _norm_reply(message)
    if not m:
        return False
    return m in _NEGATE_EXACT or m.startswith(_NEGATE_START)


# A run/setup/resume-ish phrasing in the recall ask ("can you RUN the last repo we worked on").
_RUN_ISH_RE = re.compile(r"\b(run|start|launch|boot|set ?up|setup|deploy|resume|continue|spin ?up|get .* running|fire up)\b", re.IGNORECASE)


def _write_run_expectation(config_dir: Path, row) -> None:
    """Plan 195 (§4 WRITE): register a `confirm_action: run <repo>` expectation so the next affirm
    ('yes lets run this') executes it via the existing pending-followup handler — not a fresh clarify."""
    import uuid
    from datetime import datetime, timedelta, timezone

    try:
        key = str(getattr(row, "repo_key", "") or getattr(row, "repo_url", "") or getattr(row, "metadata", {}).get("repo_name", "") if getattr(row, "metadata", None) else getattr(row, "repo_key", "")).strip()
    except Exception:
        key = ""
    key = key or str(getattr(row, "repo_url", "") or "").strip()
    name = _repo_display_name(row)
    tid = uuid.uuid4().hex[:16]
    write_followup_state(config_dir, {
        "pending_next_action": "run_repo",
        "pending_offer_kind": "run_repo",
        "pending_repo_key": key or name,
        "pending_repo_name": name,
        "pending_offer_id": tid,
        "pending_offer_thread_id": tid,
        "active_thread_id": tid,
        "pending_offer_expires_at": (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat(),
    })


def _maybe_answer_previous_repo(command: str, config_dir: Path, display: Callable[[str], None]) -> bool:
    """Answer 'which repo were we working on / did we run last?' directly from session state.
    Returns True when handled; False (no match) → normal routing continues."""
    m = str(command or "").strip().lower()
    if not m or any(x in m for x in _PREV_REPO_EXCLUDE):
        return False
    if not (_PREV_REPO_RE.search(m) and _PREV_REPO_RECENCY_RE.search(m) and _PREV_REPO_SUBJECT_RE.search(m)):
        return False
    try:
        row = initialize_state_store(config_dir).get_latest_repo_state()
    except Exception:
        row = None
    if row is None:
        display("I don't have a previous repo on record for this session — pick one with `/repos`.")
        return True
    name = _repo_display_name(row)
    url = str(getattr(row, "repo_url", "") or getattr(row, "repo_key", "") or "").strip()
    # Plan 195 (§4/§8): when the ask is RUN/setup/resume-ish, PROPOSE resuming it and WRITE a
    # confirm_action expectation — so "yes lets run this" executes the run (via the pending-followup
    # handler) instead of re-classifying into a fresh clarify loop.
    if _RUN_ISH_RE.search(m):
        try:
            _write_run_expectation(config_dir, row)
        except Exception:
            pass
        display(f"We were previously working on {name}" + (f" ({url})" if url else "")
                + f" — want me to run {name}? (yes / no)")
        return True
    display(f"We were previously working on {name}" + (f" ({url})." if url else "."))
    return True


# Plan 189 F1: high-precision SESSION-META questions answered directly from state (status /
# which VM/target), so they never spin the repo_qa loop and don't depend on the model.
_META_STATUS_RE = re.compile(
    r"\b(status|progress)\b.*\b(setup|set up|build|deploy|install|repo|it|things)\b"
    r"|\b(did|has|is|have)\b.*\b(setup|set up|build|install|it|the repo)\b.*\b(finish|finished|done|ready|complete|completed)\b"
    r"|\bwhere are we\b|\bhow far along\b",
    re.IGNORECASE,
)
_META_TARGET_RE = re.compile(
    r"\b(which|what)\s+(vm|target|machine)\b"
    r"|\bam i (on|using|running on)\b"
    r"|\bwhere(?:'s| is)?\s+(?:it|the repo|this)\s+running\b",
    re.IGNORECASE,
)

# Plan 193 F2 (SELECT + answer-from-slice): a context-dependent question about the LAST blocker.
# These resolve against the WRITTEN incident (active_incident_*), not a generic reply.
_INCIDENT_QUESTION_RE = re.compile(
    r"\bwhat(?:'?s| is| was)?\s+(?:the\s+)?(issue|problem|error|blocker|matter|wrong|holding)\b"
    r"|\bwhy\s+(?:did|does|is|was|isn'?t|didn'?t|won'?t|can'?t)\b.*\b(fail|failing|failed|work|working|start|broke|broken|stuck|block)\b"
    r"|\bwhat\s+(went wrong|happened|failed|broke|is failing|is broken|is the hold ?up)\b"
    r"|\bwhat'?s\s+(blocking|wrong|the hold ?up|going on|stopping)\b"
    r"|\bis\s+(?:it|that|the (?:setup|build|repo|vm))\s+(fixed|resolved|working now|ok now)\b",
    re.IGNORECASE,
)


def _maybe_answer_incident_question(command: str, config_dir: Path, display: Callable[[str], None]) -> bool:
    """Plan 193 F2: answer "what is the issue / why did it fail / what happened" FROM the written
    incident scratchpad (SELECT the incident slice, respond from it). Returns False (defer) when no
    incident is captured — never a generic 'name the repo' reply."""
    m = str(command or "").strip().lower()
    if not m or not _INCIDENT_QUESTION_RE.search(m):
        return False
    try:
        from state.access import read_workflow_state

        wf = read_workflow_state(config_dir)
    except Exception:
        return False
    summary = str(wf.get("active_incident_summary") or wf.get("active_issue_summary") or "").strip()
    if not summary:
        return False  # nothing captured → defer to the LLM (Tier 3) rather than guess
    resolved = bool(wf.get("active_incident_resolved"))
    category = str(wf.get("active_incident_category") or "")
    # SELECT + RESPOND: state the real blocker + the most useful next step (grounded, concise).
    next_step = ""
    if "vm" in category:
        next_step = " Restart it with `/vm`, or check `multipass list`."
    elif category in ("disk_crunch", "resource"):
        next_step = " Free space or resize via `/resources` / `/vm`."
    if resolved:
        display(f"That blocker is resolved now — earlier: {summary}")
    else:
        display(f"The last blocker: {summary}{next_step}")
    return True


def _maybe_answer_session_meta(command: str, config_dir: Path, display: Callable[[str], None]) -> bool:
    """Plan 189 F1: answer a SESSION-STATE question (status / which VM / which repo) directly from
    state (model-independent, never repo_qa). Superset of `_maybe_answer_previous_repo`."""
    m = str(command or "").strip().lower()
    if not m:
        return False
    if _META_STATUS_RE.search(m) and not m.startswith(("how ", "why ")):
        try:
            display(_render_active_deploy_status_summary(config_dir))
            return True
        except Exception:
            return False
    if _META_TARGET_RE.search(m):
        try:
            from duckln.repo_bringup import _active_vm_name, _active_container_name

            target = _session_execution_target(config_dir)
            name = (_active_vm_name(config_dir) if target == "vm"
                    else _active_container_name(config_dir) if target in ("docker", "container")
                    else "")
            where = target + (f" ({name})" if name else "")
            display(f"You're currently on the {where} target.")
            return True
        except Exception:
            return False
    return _maybe_answer_previous_repo(command, config_dir, display)


# Plan 196 F6: high-precision NL INVENTORY intents, answered from REAL state (never the LLM,
# never fabricated). Docker first (most specific), then VMs, then session repos. The repo
# pattern requires a session-lifecycle marker (we/our/set up/…) so a bare "how many repos"
# still falls through to the DUCKLN-SELF catalog answer, not this session-repo list.
_INV_DOCKER_RE = re.compile(
    r"\bdocker\b[^?]*\b(image|images|container|containers)\b"
    r"|\b(how many|list|show|any)\b[^?]*\b(docker\s+)?(image|images|container|containers)\b"
    r"|\b(image|images|container|containers)\b[^?]*\b(active|running|in use|do i have|do we have)\b",
    re.IGNORECASE,
)
_INV_VM_RE = re.compile(
    r"\b(list|show|which|what|how many|do i have|do we have)\b[^?]*\bvm(s)?\b"
    r"|\bvm(s)?\b[^?]*\b(list|do i have|do we have|are (there|running|up)|running)\b"
    r"|\b(list|show|which|what|how many)\b[^?]*\b(machines|instances)\b",
    re.IGNORECASE,
)
_INV_REPO_RE = re.compile(
    r"\b(list|show|how many|which|what)\b[^?]*\brepos?\b[^?]*\b(set up|tracked|worked|ran|we|our|i)\b"
    r"|\b(we|i|our)\b[^?]*\b(set up|worked on|ran|tracked)\b[^?]*\brepos?\b"
    # Plan 198 F4: "custom repo list" / "show me all custom(er) repos" / "list my repos" — the
    # user's added/tracked repos (distinct from the DUCKLN catalog handled by duckln-self).
    r"|\b(custom|customer)\s+repos?\b"
    r"|\b(list|show)\b[^?]*\bcustom(er)?\b[^?]*\brepos?\b"
    r"|\b(list|show)\b[^?]*\b(all|my)\b[^?]*\brepos?\b",
    re.IGNORECASE,
)
# Plan 196 F14: a LOOSE pre-filter — only pay for the LLM inventory classifier when the message
# is plausibly about infra/state or past work, so greetings never trigger it.
_INV_LOOSE_RE = re.compile(
    r"\b(vm|vms|docker|container|containers|image|images|machine|machines|instance|instances)\b"
    r"|\b(what|which|list|show|how many)\b[^?]*\b(we|i|our|my)\b[^?]*\b(build|building|built|work|working|worked|set up|made|running|doing)\b",
    re.IGNORECASE,
)


def _answer_last_project(config_dir: Path, display: Callable[[str], None]) -> bool:
    try:
        row = initialize_state_store(config_dir).get_latest_repo_state()
    except Exception:
        row = None
    if row is None:
        display("I don't have a previous repo on record for this session — pick one with `/repos`.")
        return True
    name = _repo_display_name(row)
    url = str(getattr(row, "repo_url", "") or getattr(row, "repo_key", "") or "").strip()
    display(f"We were previously working on {name}" + (f" ({url})." if url else "."))
    return True


def _inventory_vm_answer(config_dir: Path) -> str:
    vms = list_multipass_vms_or_none()
    if vms is None:
        # Plan 197 F5: distinguish not-installed (point to the /vm install offer) from a
        # transient reach failure — never a dead "couldn't reach".
        if not is_multipass_installed():
            return "Multipass isn't installed — run `/vm` and I'll offer to install it for you, then I can list your VMs."
        return "I couldn't reach Multipass just now (it may be starting) — run `/vm` and I'll get it going, then list your VMs."
    if not vms:
        return "You don't have any Multipass VMs right now. Create one with `/vm`."
    repos_by_vm: dict[str, list[str]] = {}
    try:
        for r in initialize_state_store(config_dir).list_repo_states():
            if r.execution_target == "vm" and r.vm_name:
                repos_by_vm.setdefault(r.vm_name, []).append(_repo_display_name(r))
    except Exception:
        repos_by_vm = {}
    parts = []
    for v in vms:
        repos = repos_by_vm.get(v)
        parts.append(f"{v} ({', '.join(sorted(set(repos)))})" if repos else f"{v} (no repo tracked)")
    return f"You have {len(vms)} VM(s): " + "; ".join(parts) + "."


def _inventory_docker_answer(config_dir: Path) -> str:
    from duckln.docker_inventory import list_docker_images, list_docker_containers

    imgs = list_docker_images()
    if not imgs.ok:
        # Plan 197 F5: distinguish not-installed (offer to install) from daemon-down.
        if shutil.which("docker") is None:
            return "Docker isn't installed — say 'install docker' or set up a repo on Docker and I'll offer to install it, then I can list your images/containers."
        return "Docker is installed but the engine isn't running — open Docker Desktop (or start the docker service), then ask me again and I'll list your images/containers."
    running = list_docker_containers(running_only=True)
    all_c = list_docker_containers(running_only=False)
    n_img = len(imgs.images)
    n_run = len(running.containers) if running.ok else 0
    proj_by_container: dict[str, str] = {}
    try:
        for r in initialize_state_store(config_dir).list_repo_states():
            cname = str(r.metadata.get("container_name") or "").strip()
            if cname:
                proj_by_container[cname] = _repo_display_name(r)
    except Exception:
        proj_by_container = {}
    lines = [f"{n_img} Docker image(s); {n_run} container(s) running (in use)."]
    if all_c.ok and all_c.containers:
        for c in all_c.containers:
            proj = proj_by_container.get(c.name)
            tag = f" — {proj}" if proj else " — (no project tracked)"
            state = "running" if c.running else "stopped"
            label = c.name or (c.container_id[:12] if c.container_id else "?")
            lines.append(f"  • {label} [{c.image}] ({state}){tag}")
    return "\n".join(lines)


def _inventory_repo_answer(config_dir: Path) -> str:
    # Plan 198 F4: list BOTH the added custom repos (your GitHub URLs) and the tracked/set-up
    # repos — grounded in real state, never a clarify or a repo_qa read of the active repo.
    store = None
    try:
        store = initialize_state_store(config_dir)
    except Exception:
        store = None
    tracked = []
    try:
        tracked = list(store.list_repo_states()) if store else []
    except Exception:
        tracked = []
    custom = []
    try:
        custom = list(store.list_recent_custom_repos()) if store else []
    except Exception:
        custom = []
    lines: list[str] = []
    if custom:
        names = ", ".join(
            str(getattr(c, "repo_name", "") or getattr(c, "repo_url", "")).strip() for c in custom
            if str(getattr(c, "repo_name", "") or getattr(c, "repo_url", "")).strip()
        )
        if names:
            lines.append(f"Custom repos you added: {names}.")
    if tracked:
        parts = "; ".join(f"{_repo_display_name(r)} ({r.execution_target or 'local'})" for r in tracked)
        lines.append(f"Repos you've set up/worked on ({len(tracked)}): {parts}.")
    if not lines:
        return "I don't have any custom or tracked repos yet — add one with `/repos` (or paste a GitHub URL)."
    return "\n".join(lines)


def _maybe_answer_inventory_question(command: str, config_dir: Path, display: Callable[[str], None]) -> bool:
    """Plan 196 F6: answer a VM/docker/repo INVENTORY question from real state. Returns True
    when handled; False → normal routing continues (and F14 handles the paraphrase tail)."""
    m = str(command or "").strip().lower()
    if not m:
        return False
    # Plan 199 F5: a RECENCY question about the repo ("what was the LAST repo we ran?") asks for
    # the SINGLE most-recent repo — defer to the previous-repo answerer, don't list them all. The
    # Plan-198 F4 broadening of `_INV_REPO_RE` (what … repo … we/ran) otherwise hijacks it.
    if (_PREV_REPO_RE.search(m) and _PREV_REPO_RECENCY_RE.search(m)
            and _PREV_REPO_SUBJECT_RE.search(m) and not any(x in m for x in _PREV_REPO_EXCLUDE)):
        return _maybe_answer_previous_repo(command, config_dir, display)
    try:
        if _INV_DOCKER_RE.search(m):
            display(_inventory_docker_answer(config_dir))
            return True
        if _INV_VM_RE.search(m):
            display(_inventory_vm_answer(config_dir))
            return True
        if _INV_REPO_RE.search(m):
            display(_inventory_repo_answer(config_dir))
            return True
    except Exception:
        return False
    # Plan 196 F14: the paraphrase tail. Only pay for the LLM when the message is plausibly
    # about infra/state or past work (loose pre-filter) — never on greetings. A CONFIDENT
    # verdict routes to the same grounded answerer; below threshold / 'none' → fall through
    # (the normal cascade + safe clarify handle it), never a wrong answer.
    if not _INV_LOOSE_RE.search(m):
        return False
    try:
        from duckln.ai_client import build_default_llm_client_or_none
        from duckln.conversation_routes.llm_intent import (
            classify_inventory_intent, INVENTORY_CONFIDENCE_THRESHOLD,
        )

        _client = build_default_llm_client_or_none(config_dir)
        if _client is None:
            return False
        _intent, _conf = classify_inventory_intent(command, llm_client=_client)
        if _conf < INVENTORY_CONFIDENCE_THRESHOLD or _intent == "none":
            return False
        if _intent == "inventory_docker":
            display(_inventory_docker_answer(config_dir))
            return True
        if _intent == "inventory_vm":
            display(_inventory_vm_answer(config_dir))
            return True
        if _intent == "inventory_repo":
            display(_inventory_repo_answer(config_dir))
            return True
        if _intent == "last_project":
            return _answer_last_project(config_dir, display)
    except Exception:
        return False
    return False


# Plan 190 F1: questions about DUCKLN ITSELF (its repo catalog, capabilities, identity, internet
# state) answered from REAL facts — never a generic "I'm just an AI, no repo access" LLM disclaimer,
# and never a repo_action side-effect. Deterministic → correct on ANY model.
_SELF_REF_RE = re.compile(r"\b(you|your|yours|duckln)\b", re.IGNORECASE)
_SELF_LIBRARY_RE = re.compile(
    r"\bhow many repos?\b"
    r"|\brepo (library|catalog)\b"
    r"|\b(list|show)\b.{0,14}\b(your repos?|the repos?|repos? you)\b"
    r"|\brepos? (you|duckln) (have|has|support|can set up|can help|access)\b"
    r"|\bwhat repos?\b|\bdo you have (a )?repo\b|\brepos? you have access\b|\bhave access to\b.*\brepos?\b",
    re.IGNORECASE,
)
_SELF_INTERNET_RE = re.compile(r"\b(internet|web access|browse the web|access the web|go online)\b", re.IGNORECASE)
_SELF_IDENTITY_RE = re.compile(
    r"\bwho are you\b|\bwhat('?s| is)? your name\b|\bwhat is duckln\b|\bwhat model are you\b"
    r"|\bwhat are you\b|\bhow do you work\b|\bare you (an ai|a bot|connected to github|a language model|real)\b",
    re.IGNORECASE,
)
_SELF_CAPABILITY_RE = re.compile(
    r"\bwhat can you (do|help)\b|\bwhat are your (capabilities|abilities)\b|\bwhat can you help me with\b"
    r"|\b(do|can) you (support|use|run|execute|clone|set ?up|deploy|handle|access)\b",
    re.IGNORECASE,
)


def _duckln_repo_library_answer(config_dir: Path) -> str:
    recs = ()
    try:
        recs = load_sorted_local_repo_catalog(config_dir)
    except Exception:
        recs = ()
    if not recs:
        try:
            from state.repo_catalog import load_bundled_repo_catalog

            recs = load_bundled_repo_catalog()
        except Exception:
            recs = ()
    n = len(recs)
    names = ", ".join(r.name for r in recs[:5])
    tracked = 0
    try:
        tracked = len(initialize_state_store(config_dir).list_repo_states())
    except Exception:
        tracked = 0
    out = (f"I have a curated library of {n} repos you can set up — browse them with `/repos`."
           + (f" A few: {names}." if names else "")
           + " You can also paste any GitHub URL and I'll add it.")
    if tracked:
        out += f" {tracked} repo(s) are already tracked in this session."
    return out


def _duckln_capabilities_answer() -> str:
    return (
        "I set up, run, and fix code repositories. I can clone, install, and run a repo locally, on "
        "an Ubuntu VM, in Docker, or in the cloud (including GPU). I run commands under a safety gate "
        "(S0–S4) and your control mode, keep a curated repo catalog (browse with `/repos`), and can add "
        "any GitHub repo you point me at. Ask me to set up a repo, fix a blocker, or check your machine."
    )


def _duckln_identity_answer(current) -> str:
    prov = getattr(getattr(current, "provider", None), "label", "") or ""
    if not isinstance(prov, str):
        prov = str(prov)
    model = getattr(current, "model", "") or "a model"
    mode = str(getattr(getattr(current, "mode", None), "value", "") or "").upper()
    return (
        f"I'm Duckln — a terminal repo manager that sets up, runs, and fixes code repositories. "
        f"Right now I'm running {model}" + (f" via {prov}" if prov else "")
        + (f", in {mode} mode" if mode else "")
        + ". I keep a curated repo catalog (`/repos`) and can work locally, on a VM, in Docker, or in the cloud."
    )


def _maybe_answer_duckln_self(command: str, current, config_dir: Path, display: Callable[[str], None]) -> bool:
    """Plan 190 F1: answer a question about DUCKLN ITSELF from real facts. Returns True when handled."""
    m = str(command or "").strip().lower()
    if not m:
        return False
    if _SELF_LIBRARY_RE.search(m):
        display(_duckln_repo_library_answer(config_dir))
        return True
    # The remaining self-answers must clearly be ABOUT Duckln (mention you/your/duckln).
    if not _SELF_REF_RE.search(m):
        return False
    if _SELF_INTERNET_RE.search(m):
        on = False
        try:
            from duckln.internet_skill import is_internet_enabled

            on = is_internet_enabled(config_dir)
        except Exception:
            on = False
        display("Yes — my internet skill is ON, so I can search the web and cite sources."
                if on else
                "It's off right now. Turn it on with `/internet on` and I'll fetch cited, up-to-date sources.")
        return True
    if _SELF_CAPABILITY_RE.search(m):
        display(_duckln_capabilities_answer())
        return True
    if _SELF_IDENTITY_RE.search(m):
        display(_duckln_identity_answer(current))
        return True
    return False


# Plan 190 F2b: repo RECOMMENDATION / DISCOVERY — route to a GROUNDED answer from Duckln's catalog,
# never `repo_action` (which would ACT on the active repo) or `repo_question` (which would read it).
# When the ask is ambiguous, ask exactly ONE clarifying question (aware, not a frustrating loop).
# (The actual GitHub-trending search / top-5-ML fetch is a later phase — here we answer from the
# existing catalog.)
_RECO_RE = re.compile(
    r"\brecommend\b|\bsuggest\b|\bwhich repo (should|do you|to)\b|\bbest repos?\b"
    r"|\btop \d+ .*repos?\b|\bfind me (a|some) .*repos?\b|\ba repo to (try|play|start)\b"
    r"|\bwhat should i build\b|\bcool repos?\b|\bgood repos? to\b|\brepos? to (try|start with|play with)\b"
    r"|\bany (cool|good|interesting) repos?\b",
    re.IGNORECASE,
)
_RECO_DOMAINS = {
    "machine learning / LLMs": ("ml", "machine learning", " ai ", "llm", "language model", "training", "transformer", "neural"),
    "image generation": ("image", "diffusion", "stable diffusion", "comfyui", "picture", "art", "text-to-image"),
    "audio / speech": ("audio", "whisper", "speech", "voice", "music", "transcri"),
    "web apps": ("web", "website", "frontend", "front-end", "backend", "back-end", "fullstack", "react", "next", "node", "django", "flask"),
    "chatbots / agents": ("chatbot", "chat bot", "assistant", "agent", "conversation"),
}


def _domain_from_text(text: str) -> str | None:
    """Map a free-text answer (or a picked option label) to one of the catalog domains."""
    low = str(text or "").strip().lower()
    if not low:
        return None
    for label, kws in _RECO_DOMAINS.items():
        if label.lower() in low or low in label.lower() or any(k.strip() in low for k in kws):
            return label
    return None


def _recommend_for_domain(domain: str, config_dir: Path, display: Callable[[str], None]) -> None:
    """Plan 190 F2b: present the top catalog repos for a resolved domain (grounded)."""
    recs = ()
    try:
        recs = load_sorted_local_repo_catalog(config_dir)
    except Exception:
        recs = ()
    if not recs:
        try:
            from state.repo_catalog import load_bundled_repo_catalog

            recs = load_bundled_repo_catalog()
        except Exception:
            recs = ()
    kws = _RECO_DOMAINS.get(domain, ())
    def _hit(r):
        blob = f"{r.name} {getattr(r, 'category', '')} {getattr(r, 'framework', '')} {getattr(r, 'description', '')}".lower()
        return any(k.strip() in blob for k in kws)
    matches = [r for r in recs if _hit(r)][:3] or list(recs[:3])
    if not matches:
        display("Browse my catalog with `/repos` — I can set up any of them (or paste a GitHub URL).")
        return
    lines = "\n".join(f"- {r.name} — {(getattr(r, 'description', '') or '').strip()[:90]}" for r in matches)
    display(
        f"From my catalog, for {domain}:\n{lines}\n"
        "Want me to set one up? Say the name, or run `/repos` to see more."
    )


def _resolve_recommendation_from_answer(answer: str, config_dir: Path, display: Callable[[str], None]) -> None:
    """Turn a clarify answer (a picked domain or free 'Other' text) into a grounded recommendation."""
    domain = _domain_from_text(answer)
    if domain is not None:
        _recommend_for_domain(domain, config_dir, display)
    else:
        # Free-text focus we don't have a domain bucket for → be honest, point at the catalog.
        display(
            f"I don't have a dedicated bucket for “{answer.strip()[:60]}” in my curated catalog yet — "
            "browse everything with `/repos`, or paste a GitHub URL and I'll set it up."
        )


def _persist_pending_clarify(config_dir: Path, *, question: str, options: tuple[str, ...]) -> None:
    """Plan 191 F2: remember a pending recommendation clarify so the NEXT typed message can be
    reasoned CONTINUE-vs-NEW (used on the non-overlay/text path)."""
    import uuid
    from datetime import datetime, timedelta, timezone

    tid = uuid.uuid4().hex[:16]
    write_followup_state(config_dir, {
        "pending_offer_kind": "clarify",
        "pending_question_type": "recommend",
        "pending_offer_label": question,
        "pending_clarification_options": list(options),
        "pending_offer_thread_id": tid,
        "active_thread_id": tid,
        "pending_offer_expires_at": (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
    })


def _clear_pending_clarify(config_dir: Path) -> None:
    write_followup_state(config_dir, {
        "pending_offer_kind": None, "pending_question_type": None, "pending_offer_label": None,
        "pending_clarification_options": None, "pending_offer_thread_id": None,
        "active_thread_id": None, "pending_offer_expires_at": None,
    })


_RECO_DOMAIN_OPTIONS = tuple(_RECO_DOMAINS.keys())


def _maybe_resume_pending_clarify(command: str, config_dir: Path, display: Callable[[str], None], *, current) -> bool:
    """Plan 191 F2: when a recommendation clarify is pending and the user TYPES a message, decide
    CONTINUE (answer it → recommend) vs NEW (clear it → route fresh). Returns True only on CONTINUE."""
    try:
        st = read_followup_state(config_dir)
    except Exception:
        return False
    if str(st.get("pending_offer_kind") or "") != "clarify":
        return False
    # Honor expiry — a stale pending clarify is dropped, not resumed.
    exp = str(st.get("pending_offer_expires_at") or "")
    if exp:
        try:
            from datetime import datetime, timezone

            if datetime.fromisoformat(exp) < datetime.now(timezone.utc):
                _clear_pending_clarify(config_dir)
                return False
        except Exception:
            pass
    question = str(st.get("pending_offer_label") or "")
    options = tuple(str(o) for o in (st.get("pending_clarification_options") or ()))
    from duckln.clarify import decide_continue_or_new
    from duckln.ai_client import build_default_llm_client_or_none

    llm_client = build_default_llm_client_or_none(config_dir) if getattr(current, "model", None) else None
    decision = decide_continue_or_new(message=command, pending_question=question, options=options, llm_client=llm_client)
    _clear_pending_clarify(config_dir)  # a single-shot gate either way — never a loop
    if decision == "continue":
        _resolve_recommendation_from_answer(command, config_dir, display)
        return True
    return False  # NEW → fall through to normal routing


def _maybe_answer_recommendation(
    command: str,
    config_dir: Path,
    display: Callable[[str], None],
    *,
    channel: object | None = None,
    current: object | None = None,
) -> bool:
    """Plan 190 F2b + Plan 191: answer a repo-recommendation ask from the catalog. When the ask
    already names a focus, recommend straight; otherwise run the ADAPTIVE clarify engine (LLM
    decides how many clever questions — up to 4 — with radio options + Other + Esc), grounded in
    the catalog domains, and record the reasoning to logical-thinking.md."""
    m = str(command or "").strip().lower()
    if not m or not _RECO_RE.search(m):
        return False

    # Fast path: the ask already names a domain → recommend without asking.
    domain = _domain_from_text(m)
    if domain is not None:
        _recommend_for_domain(domain, config_dir, display)
        return True

    from duckln.clarify import ClarifyFacts, run_clarification
    from duckln.ai_client import build_default_llm_client_or_none

    llm_client = build_default_llm_client_or_none(config_dir) if getattr(current, "model", None) else None
    facts = ClarifyFacts(topic="what you want to build or run", options=_RECO_DOMAIN_OPTIONS)
    result = run_clarification(ask=command, facts=facts, channel=channel, config_dir=config_dir,
                               llm_client=llm_client, display=display)
    if result.cancelled:
        display("No worries — browse my catalog any time with `/repos`, or ask me again whenever.")
        return True
    if result.pending_question is not None:
        # Non-overlay path: ask as text + remember it so the next message is reasoned continue-vs-new.
        q, opts = result.pending_question
        numbered = "  ".join(f"{i + 1}) {o}" for i, o in enumerate(opts))
        display(f"{q}\n{numbered}\nTell me which (or your own focus).")
        _persist_pending_clarify(config_dir, question=q, options=opts)
        return True
    if result.resolved and result.value:
        _resolve_recommendation_from_answer(result.value, config_dir, display)
        return True
    # Resolved with no value (nothing grounded) → honest catalog pointer.
    display("Tell me a focus (ML/LLMs, web, image generation, audio, a chatbot…) and I'll pick from my catalog — or run `/repos`.")
    return True


# Plan 189 F4: answer a general TECHNICAL/coding/cloud/GPU question CLEARLY (not the dry persona)
# and WITH CITATIONS (build trust): cite web sources when /internet is on, else answer from general
# knowledge with an honest note + a ONE-TIME offer to turn the internet on.
_TECH_SYSTEM_CLEAR = (
    # Plan 190 F3: identity guard — Duckln must never disclaim being a real tool.
    "You are Duckln — a REAL developer tool that sets up, runs, and fixes code repositories (you have "
    "a curated repo catalog via `/repos` and can clone, install, and run repos locally, on a VM, in "
    "Docker, or in the cloud including GPU). NEVER claim to be a disconnected language model with no "
    "repo access or no capabilities.\n"
    "Answer the user's technical / programming / systems / cloud / GPU question CLEARLY and correctly "
    "in plain, easy-to-understand language: a one-line direct answer first, then a few concise bullet "
    "points (and a short example when useful). Cite a source URL when one is provided. Be accurate; if "
    "unsure, say so plainly. Do NOT be sarcastic or witty — be clear and genuinely helpful.\n"
    # Plan 190: a one-shot example so the model knows the BEST way to present an answer.
    "Example — imitate this SHAPE, not the content:\n"
    "Q: How does a hash map work?\n"
    "A: A hash map stores key→value pairs with average O(1) lookup.\n"
    "- It hashes the key to an index in an array bucket.\n"
    "- Collisions (two keys landing in the same bucket) are handled by chaining or open addressing.\n"
    "- It resizes as it fills so lookups stay fast.\n"
    "(Cite a source URL here when one is provided.)"
)


def _technical_source_lines(sources) -> str:
    return "\n".join(f"- {s.url}" for s in (sources or ()) if getattr(s, "url", "").strip())


def _answer_technical_question(*, command, current, paths, display, terminal_interface) -> bool:
    """Generate a clear, cited technical answer. Returns True when answered; False → fall through."""
    from duckln.ai_client import build_default_llm_client_or_none
    from duckln.internet_skill import is_internet_enabled, internet_search_summary

    client = build_default_llm_client_or_none(paths.config_dir)
    if client is None:
        return False  # no model → let the conversation supervisor honest-stop

    def _generate(with_web: bool):
        sources = ()
        if with_web:
            try:
                sources = internet_search_summary(command, config_dir=paths.config_dir, max_results=3)
            except Exception:
                sources = ()
        system = _TECH_SYSTEM_CLEAR
        if sources:
            src_block = "\n".join(
                f"- {getattr(s, 'title', '').strip()} ({s.url.strip()}): {getattr(s, 'snippet', '').strip()[:200]}"
                for s in sources if getattr(s, "url", "").strip()
            )
            system = _TECH_SYSTEM_CLEAR + (
                "\nUse these web sources and CITE the ones you rely on by their URL in your answer:\n" + src_block
            )
        try:
            return str(client(system_prompt=system, user_message=command) or "").strip(), sources
        except Exception:
            return "", sources

    try:
        internet_on = is_internet_enabled(paths.config_dir)
    except Exception:
        internet_on = False

    answer, sources = _generate(internet_on)
    if not answer:
        return False
    display(answer)
    if internet_on:
        srcs = _technical_source_lines(sources)
        if srcs:
            display("Sources:\n" + srcs)
    else:
        display("(Answered from general knowledge — turn on `/internet` for cited, up-to-date sources.)")
        _maybe_offer_internet_once(paths=paths, display=display, terminal_interface=terminal_interface, generate=_generate)
    return True


def _maybe_offer_internet_once(*, paths, display, terminal_interface, generate) -> None:
    """Plan 189 F4: the FIRST time an offline technical answer is given, offer (Yes/No) to turn the
    internet on for cited answers. Asks ONLY ONCE (persisted flag) — no nagging afterwards."""
    try:
        from state.access import read_config_snapshot, write_config_snapshot

        if read_config_snapshot(paths.config_dir).get("internet_offer_shown"):
            return
        write_config_snapshot(paths.config_dir, {"internet_offer_shown": True})
    except Exception:
        return
    from duckln.interaction import propose_and_confirm

    _sel = getattr(terminal_interface, "select_choice", None)
    decision = propose_and_confirm(
        situation="For the best, source-cited answers, Duckln can use the internet.",
        recommendation="turn internet on now (you can toggle it anytime with `/internet`)",
        display=display,
        select=(lambda q, opts: _sel(q, opts)) if callable(_sel) else None,
        accept_label="Yes — turn it on",
    )
    if decision.accepted:
        try:
            from duckln.internet_skill import set_internet_enabled

            set_internet_enabled(paths.config_dir, True)
            display("Internet is on. Re-answering with sources…")
            answer, sources = generate(True)
            if answer:
                display(answer)
                srcs = _technical_source_lines(sources)
                if srcs:
                    display("Sources:\n" + srcs)
        except Exception:
            pass


# Plan 192 Tier 3: a small deterministic detector for STATUS-shaped, context-dependent questions
# that carry no repo keyword but tie to work already underway ("is it ready?", "when will this be
# done?"). These are the doc's second Tier-3 trigger (alongside a repo signal).
_STATUS_QUESTION_RE = re.compile(
    r"\b(is it (ready|done|working|up|running|finished|complete)|are we (done|there yet|ready)"
    r"|when (will|is) (this|it|that) (be )?(done|ready|finished)|how (long|much longer)"
    r"|is (the|it) (setup|build|install|deploy) (done|ready|finished)|are you (done|finished)"
    r"|hows? (the|it) (setup|build|going)|whats? the (status|progress))\b",
    re.IGNORECASE,
)


def _looks_like_status_question(normalized_compact: str) -> bool:
    return bool(_STATUS_QUESTION_RE.search(str(normalized_compact or "")))


def _conversation_context_str(recent_turns: tuple) -> str:
    """Build the doc's `{{conversation_context}}` — the last few turns, labeled who spoke."""
    lines = []
    for turn in tuple(recent_turns)[-6:]:
        role = str(getattr(turn, "role", "") or "user")
        content = str(getattr(turn, "content", "") or "").strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _surface_repo_recall(repo, config_dir, display: Callable[[str], None]) -> None:
    """Plan 194 F6 (long-term SELECT-on-return): when a repo is (re-)selected, surface a one-line
    recall of what Duckln remembers about it (needs-venv, pkg manager, prior fixes, prefs). Silent
    when nothing is remembered — so a returning repo visibly brings its profile."""
    try:
        from duckln.conversation_routes.provider_support import build_repo_memory_recall

        recall = build_repo_memory_recall(config_dir, repo)
        if recall:
            display(recall)
    except Exception:
        pass


def _incident_context_slice(config_dir) -> str:
    """Plan 193 F2 (SELECT): the incident + recent-events slice for the context-dependent path —
    bounded, only what's relevant (context rot: less, chosen well). Empty when nothing's captured."""
    try:
        from state.access import read_workflow_state

        wf = read_workflow_state(config_dir)
    except Exception:
        return ""
    parts = []
    incident = str(wf.get("active_incident_summary") or "").strip()
    if incident and not wf.get("active_incident_resolved"):
        parts.append(f"Active incident: {incident}")
    events = wf.get("recent_events")
    if isinstance(events, list) and events:
        parts.append("Recent events: " + " | ".join(str(e) for e in events[-3:]))
    return ("\n" + "\n".join(parts)) if parts else ""


def _record_routing_decision(config_dir, *, tier: int, message: str, intent: str, confidence: float, rationale: str) -> None:
    """Plan 192 F5: record each Tier-3/Tier-4 routing decision to the runtime logical-thinking.md
    (surface='routing') so routing is observable/auditable. Redacted, best-effort."""
    try:
        from duckln.recovery import append_thinking_log

        append_thinking_log(
            config_dir, repo_slug="intent-routing", title="Intent routing",
            surface="routing",
            lines=[
                f"Message: {str(message).strip()[:200]}",
                f"Tier {tier}: intent={intent} confidence={confidence:.2f}",
                f"Why: {str(rationale).strip()[:200]}",
            ],
        )
    except Exception:
        pass


def _maybe_autoroute_repo_agent(
    *,
    command: str,
    current: AppConfig,
    paths: ConfigPaths,
    display: Callable[[str], None],
    approve: Callable[[str], bool] | None,
    terminal_interface: object | None,
    recent_turns: tuple = (),
) -> bool:
    """Plan 102: when the user types a free-form repo question/action AND there's an
    active repo AND a configured provider, route it to the repo agent (same as `/ask`
    or `/do`). Returns True when it handled the message. Conservative: no active repo
    or no provider → returns False so normal chat is unaffected."""
    from duckln.repo_agent import classify_repo_agent_intent

    # Plan 191 F2: if a recommendation clarify is pending, decide whether this typed message
    # CONTINUES it (answers the question) or is a NEW request — before any other routing.
    if _maybe_resume_pending_clarify(command, paths.config_dir, display, current=current):
        return True
    # Plan 193 F2 (SELECT + answer-from-slice): "what is the issue / why did it fail / what
    # happened" → answer from the WRITTEN incident, before any classifier, so it's never a generic
    # "name the repo" reply.
    if _maybe_answer_incident_question(command, paths.config_dir, display):
        return True
    # Plan 196 F6: answer a natural-language INVENTORY question (list VMs + their repos, how
    # many docker images / containers active, list docker images and their project, our repos)
    # from REAL state — before session_meta (which answers only the SINGULAR current target).
    if _maybe_answer_inventory_question(command, paths.config_dir, display):
        return True
    # Plan 187 F3 / Plan 189 F1: answer a session-state question (status / which VM / which repo)
    # directly from state, so it never spins the repo_qa loop.
    if _maybe_answer_session_meta(command, paths.config_dir, display):
        return True
    # Plan 190 F1: answer a question about DUCKLN ITSELF (catalog / capabilities / identity /
    # internet) from real facts — before any classifier — so it's never a generic "I'm an AI"
    # disclaimer or a repo_action side-effect.
    if _maybe_answer_duckln_self(command, current, paths.config_dir, display):
        return True
    # Plan 190 F2b / Plan 191: a repo-recommendation ask → a grounded catalog answer, using the
    # ADAPTIVE clarify engine when ambiguous — never `repo_action` (which would act on the repo).
    if _maybe_answer_recommendation(command, paths.config_dir, display,
                                    channel=terminal_interface, current=current):
        return True

    intent = classify_repo_agent_intent(command)
    # Plan 193 F7b (perf, doc §6): a message with NO repo signal and no context-dependent question
    # is conversation — route it there DETERMINISTICALLY without paying an LLM classifier round-trip
    # (that was the greeting "Thought for 4-10s"). Only pay the classifier for the uncertain middle
    # (a repo signal, or a status/incident/context question).
    try:
        from duckln.conversation_routes.normalizer import has_repo_signal as _hrs, normalize_compact_message as _ncm

        _norm0 = _ncm(command)
        _worth_classifying = bool(_hrs(_norm0) or _looks_like_status_question(_norm0) or _INCIDENT_QUESTION_RE.search(_norm0))
    except Exception:
        _worth_classifying = True  # defensive: if the check fails, keep prior behavior
    # Plan 174 F2 / Plan 189 F2+F4: cascade CATCH-ALL. When deterministic routing is ambiguous
    # (intent '') and a model is configured, ask the LLM to classify into repo_question /
    # repo_action / technical / social. A repo verdict (+ active repo) → repo_qa/action; a
    # `technical` verdict → a clear, cited technical answer (no repo needed); `social`/unclear →
    # the conversation supervisor (safe default), never a forced repo_qa loop.
    if intent == "" and getattr(current, "model", None) and _worth_classifying:
        try:
            from duckln.conversation_routes.llm_intent import classify_repo_relevance
            from duckln.ai_client import build_default_llm_client_or_none
            _route = classify_repo_relevance(command, llm_client=build_default_llm_client_or_none(paths.config_dir))
        except Exception:
            _route = ""
        try:
            _has_active_repo = initialize_state_store(paths.config_dir).get_latest_repo_state() is not None
        except Exception:
            _has_active_repo = False
        if _route == "technical":
            if _answer_technical_question(
                command=command, current=current, paths=paths,
                display=display, terminal_interface=terminal_interface,
            ):
                return True
            # couldn't produce an answer → fall through to the conversation supervisor
        elif _has_active_repo and _route == "repo_question":
            intent = "ask"
        elif _has_active_repo and _route == "repo_action":
            intent = "do"
        # `social`, or a repo verdict with no active repo → fall through to conversation

    # Plan 192 Tiers 3–4: the UNCERTAIN MIDDLE. When the deterministic + repo-relevance tiers did
    # not resolve to ask/do, and the message either carries a repo SIGNAL or is a context-dependent
    # STATUS question, run the doc's context-aware classifier (repo_task/status/conversation/
    # ambiguous + confidence). Act at confidence ≥ 0.6; below it, or "ambiguous", ask ONE clarifying
    # question on the RIGHT axis ("a repo job, or just chatting?") — never the repo-only clarifier.
    if intent == "" and getattr(current, "model", None):
        try:
            from duckln.conversation_routes.normalizer import has_repo_signal, normalize_compact_message
            _norm = normalize_compact_message(command)
        except Exception:
            _norm = str(command or "").strip().lower()
            has_repo_signal = lambda _t: False  # noqa: E731 — defensive fallback
        try:
            _has_active_repo  # from the block above
        except NameError:
            try:
                _has_active_repo = initialize_state_store(paths.config_dir).get_latest_repo_state() is not None
            except Exception:
                _has_active_repo = False
        # Plan 193 F7b: fire Tier 3 for the uncertain middle — a repo signal, a status question, OR
        # a context-dependent incident question (when no incident was written to answer directly).
        if has_repo_signal(_norm) or _looks_like_status_question(_norm) or _INCIDENT_QUESTION_RE.search(_norm):
            try:
                from duckln.conversation_routes.llm_intent import (
                    TIER3_CONFIDENCE_THRESHOLD, classify_intent_with_context, phrase_axis_clarification,
                )
                from duckln.ai_client import build_default_llm_client_or_none

                _client = build_default_llm_client_or_none(paths.config_dir)
                # Plan 193 F2/F3: SELECT the incident/recent-events slice into the classifier context
                # (the doc's intent-with-context), so "what is the issue?" reasons over what happened.
                _ctx = _conversation_context_str(recent_turns) + _incident_context_slice(paths.config_dir)
                _t3, _conf, _why = classify_intent_with_context(command, _ctx, llm_client=_client)
                _record_routing_decision(paths.config_dir, tier=3, message=command, intent=_t3, confidence=_conf, rationale=_why)
                if _conf >= TIER3_CONFIDENCE_THRESHOLD and _t3 != "ambiguous":
                    if _t3 == "status":
                        if _maybe_answer_session_meta(command, paths.config_dir, display):
                            return True
                        # no concrete status to report → fall through to conversation
                    elif _t3 == "repo_task" and _has_active_repo:
                        from duckln.repo_agent import classify_repo_agent_intent as _cri
                        intent = _cri(command) or "ask"
                    elif _t3 == "conversation":
                        return False  # Tier 2 default — the conversation supervisor
                else:
                    # Tier 4 + Plan 195 §6 loop-breakers: cap clarifies at 2 per thread and NEVER
                    # re-ask the same question — after that, ACT on the best interpretation with a
                    # stated assumption, so we never loop like the screenshot.
                    _fs = read_followup_state(paths.config_dir)
                    _cc = int(_fs.get("clarify_count") or 0)
                    _axis_q = phrase_axis_clarification(command, _ctx, llm_client=_client)
                    _last_q = str(_fs.get("last_choice_prompt") or "")
                    if _cc >= 2 or (_axis_q and _axis_q == _last_q):
                        write_followup_state(paths.config_dir, {"clarify_count": None, "last_choice_prompt": None})
                        if _has_active_repo:
                            from duckln.repo_agent import classify_repo_agent_intent as _cri
                            intent = _cri(command) or "ask"
                            display("I'll take this as a question about the active repo — say 'stop' if that's not it.")
                            # fall through with intent set (no clarify)
                        else:
                            return False  # conversation supervisor — no repo to assume
                    else:
                        write_followup_state(paths.config_dir, {"clarify_count": _cc + 1, "last_choice_prompt": _axis_q})
                        display(_axis_q)
                        _record_routing_decision(paths.config_dir, tier=4, message=command, intent="clarify",
                                                 confidence=_conf, rationale="ambiguous/low-confidence → right-axis clarify")
                        return True
            except Exception:
                pass  # any failure → fall through to the conversation supervisor (safe default)

    if intent not in ("ask", "do", "reasoning", "recheck"):
        return False
    # Plan 145 F7: "show your reasoning / why did you do that" opens the maintained reasoning
    # log — no slash, no active repo required (you can ask to see Duckln's thinking anytime).
    if intent == "reasoning":
        opened = False
        try:
            opener = getattr(terminal_interface, "open_last_reasoning_file", None)
            opened = bool(opener and opener())
        except Exception:
            opened = False
        if not opened:
            try:
                from duckln.recovery import thinking_log_path
                _p = thinking_log_path(paths.config_dir)
                display(f"My reasoning is logged here — open it to read the full chain: {_p}"
                        if _p.exists() else "I don't have a reasoning log yet — it's written as I plan and recover.")
            except Exception:
                display("I don't have a reasoning log yet — it's written as I plan and recover.")
        return True
    if not getattr(current, "model", None):
        return False
    try:
        if initialize_state_store(paths.config_dir).get_latest_repo_state() is None:
            return False
    except Exception:
        return False
    if intent == "ask":
        _handle_ask_command(
            command=f"/ask {command}", current=current, paths=paths,
            display=display, approve=approve, terminal_interface=terminal_interface,
        )
    else:
        # 'do' and 'recheck' (a critique → re-investigate + act) both route to the action agent.
        _handle_do_command(
            command=f"/do {command}", current=current, paths=paths,
            display=display, approve=approve, terminal_interface=terminal_interface,
        )
    return True


def _resolve_repo_agent_target(row, repo_name: str, execution_target: str, config_dir: Path, *, verb: str):
    """Plan 97: resolve the repo workspace dir + remote name for the repo agent on ANY
    target. Returns (project_dir, vm_name, gate_message). gate_message is non-empty when
    the agent can't run (and should be shown instead). Local needs a real local clone;
    vm/container/cloud use the in-target path + the active remote name."""
    raw_path = str(row.metadata.get("install_location") or row.repo_path or "").strip()
    if execution_target in ("vm", "container", "aws", "gcp"):
        if not raw_path:
            return None, None, f"Duckln doesn't have a path for {repo_name} on '{execution_target}' yet — set it up there first."
        from duckln.repo_bringup import _active_container_name, _active_vm_name

        name = _active_container_name(config_dir) if execution_target == "container" else _active_vm_name(config_dir)
        if execution_target in ("vm", "container") and not name:
            return None, None, f"No active {execution_target} resolved. Use `/vm` or set it up, then retry."
        return Path(raw_path), name, ""
    # local
    project_dir = Path(raw_path) if raw_path else None
    if project_dir is None or not project_dir.exists():
        return None, None, f"Duckln has no local clone of {repo_name} to {verb}. Set it up locally first."
    return project_dir, None, ""


def _reasoning_context_hints(config_dir: Path, question: str) -> tuple[str, ...]:
    """Plan 146 A2: when the user asks about Duckln's OWN solution/reasoning (not the repo
    code), surface its maintained reasoning log (`logical-thinking.md`) + the active plan as
    context, so the repo Q&A agent can ANSWER/DEFEND its reasoning — and the user can
    challenge it the way the internal supervisor (145 F3) does. Read-only + redacted; returns
    () for ordinary code questions so it doesn't bloat them."""
    q = (question or "").lower()
    cues = (
        "reasoning", "why did you", "why didn't you", "why didnt you", "your thinking",
        "your logic", "the solution", "your solution", "your fix", "your plan", "your approach",
        "your decision", "are you sure", "recheck", "re-check", "that's wrong", "thats wrong",
        "reconsider", "justify", "how did you decide", "what did you do",
    )
    if not any(c in q for c in cues):
        return ()
    from duckln.diagnostics import redact_sensitive_data

    out: list[str] = []
    try:
        from duckln.recovery import thinking_log_path

        p = thinking_log_path(config_dir)
        if p.exists():
            tail = p.read_text(encoding="utf-8")[-3000:]
            out.append("Duckln's OWN recent reasoning log (logical-thinking.md):\n" + redact_sensitive_data(tail))
    except Exception:
        pass
    try:
        from state.access import read_pending_plan

        pend = read_pending_plan(config_dir)
        if pend:
            steps = pend.get("steps") or []
            titles = "; ".join(str(s.get("title", "")) for s in steps[:15] if isinstance(s, dict))
            out.append(redact_sensitive_data(f"Active plan (status={pend.get('status', '')}): {titles}"))
    except Exception:
        pass
    return tuple(out)


def _handle_ask_command(
    *,
    command: str,
    current: AppConfig,
    paths: ConfigPaths,
    display: Callable[[str], None],
    approve: Callable[[str], bool] | None,
    terminal_interface: object | None,
) -> None:
    """Plan 91: `/ask <question>` — run the repo-scoped tool-use agent over the active
    repo's actual files and print a grounded answer. Read-only (Phase 1)."""
    question = command[len("/ask"):].strip()
    if not question:
        # Plan 145 F7: never tell the user to type a slash command — just ask naturally.
        display("What would you like to know about the repo? (e.g. \"where is the entry point?\")")
        return
    from duckln.connection_status import ensure_provider_connected
    readiness = ensure_provider_connected(current)
    if not readiness.connected:
        display(readiness.message)
        return
    store = initialize_state_store(paths.config_dir)
    row = store.get_latest_repo_state()
    if row is None:
        display("No active repo yet. Select one with `/repos` first, then `/ask`.")
        return
    repo_name = _repo_display_name(row)
    execution_target = _session_execution_target(paths.config_dir)
    project_dir, vm_name, gate_msg = _resolve_repo_agent_target(row, repo_name, execution_target, paths.config_dir, verb="read")
    if gate_msg:
        display(gate_msg)
        return

    from duckln.repo_agent import run_repo_agent
    from state.access import read_repo_memory_ranked, read_tool_policy, write_repo_memory

    repo_slug = str(row.repo_path or repo_name)
    tool_policy = read_tool_policy(paths.config_dir)
    # Plan 101: recall the most RELEVANT prior findings about this repo, not just recent.
    prior = read_repo_memory_ranked(paths.config_dir, repo_slug, query=question, top_k=3)
    hints = tuple(f"Earlier Q: {m.get('q','')} → {m.get('a','')[:300]}" for m in prior)
    # Plan 146 A2: when the user asks about Duckln's OWN solution/reasoning (not the code),
    # surface its maintained reasoning log + the active plan so the agent can answer/defend it.
    hints = hints + _reasoning_context_hints(paths.config_dir, question)
    emit_thought = lambda t: _emit_thought(terminal_interface, t, display=display)
    display(f"Reading {repo_name} to answer your question…")
    outcome = run_repo_agent(
        question=question,
        config_dir=paths.config_dir,
        repo_name=repo_name,
        project_dir=project_dir,
        execution_target=execution_target,
        mode=current.mode,
        display=None,
        approve=approve,
        emit_thought=emit_thought,
        chat=terminal_interface,
        tool_policy=tool_policy,
        extra_hints=hints,
        vm_name=vm_name,
    )
    display(outcome.answer)
    # Plan 95: remember this Q&A about the repo for future conversations (redacted).
    try:
        write_repo_memory(
            paths.config_dir, repo_slug=repo_slug,
            question=redact_sensitive_data(question), answer=redact_sensitive_data(outcome.answer),
        )
    except Exception:
        pass


def _handle_do_command(
    *,
    command: str,
    current: AppConfig,
    paths: ConfigPaths,
    display: Callable[[str], None],
    approve: Callable[[str], bool] | None,
    terminal_interface: object | None,
) -> None:
    """Plan 93/94: `/do <task>` — perform an action on the active repo (edit/fix/run),
    delegating Explorer→Engineer subagents (isolated contexts), HITL-gated."""
    task = command[len("/do"):].strip()
    if not task:
        # Plan 145 F7: never tell the user to type a slash command — just ask naturally.
        display("What would you like me to do on the repo? (e.g. \"add type hints to utils.py and run the tests\")")
        return
    from duckln.connection_status import ensure_provider_connected
    readiness = ensure_provider_connected(current)
    if not readiness.connected:
        display(readiness.message)
        return
    store = initialize_state_store(paths.config_dir)
    row = store.get_latest_repo_state()
    if row is None:
        display("No active repo yet. Select one with `/repos` first, then `/do`.")
        return
    repo_name = _repo_display_name(row)
    execution_target = _session_execution_target(paths.config_dir)
    project_dir, vm_name, gate_msg = _resolve_repo_agent_target(row, repo_name, execution_target, paths.config_dir, verb="act on")
    if gate_msg:
        display(gate_msg)
        return

    from duckln.repo_agent import run_repo_task
    from state.access import read_tool_policy, write_repo_memory

    tool_policy = read_tool_policy(paths.config_dir)
    emit_thought = lambda t: _emit_thought(terminal_interface, t, display=display)
    display(f"Working on {repo_name}: {task}")
    outcome = run_repo_task(
        task=task,
        config_dir=paths.config_dir,
        repo_name=repo_name,
        project_dir=project_dir,
        execution_target=execution_target,
        mode=current.mode,
        approve=approve,
        emit_thought=emit_thought,
        chat=terminal_interface,
        tool_policy=tool_policy,
        vm_name=vm_name,
    )
    display(outcome.answer)
    try:
        write_repo_memory(
            paths.config_dir, repo_slug=str(row.repo_path or repo_name),
            question=redact_sensitive_data("[task] " + task), answer=redact_sensitive_data(outcome.answer),
        )
    except Exception:
        pass


def _handle_policy_command(*, command: str, paths: ConfigPaths, display: Callable[[str], None]) -> None:
    """Plan 95: `/policy` — configure per-tool HITL (allow/deny/default), persisted."""
    from state.access import read_tool_policy, write_tool_policy

    parts = command.split()
    if len(parts) <= 1 or parts[1] in ("show", "list"):
        pol = read_tool_policy(paths.config_dir)
        if not pol:
            display("No per-tool overrides set. Use `/policy allow <tool>` or `/policy deny <tool>` (e.g. fs.write, repo.run, git.run).")
        else:
            display("Tool policy overrides:\n" + "\n".join(f"  • {t}: {d}" for t, d in sorted(pol.items())))
        return
    action = parts[1].lower()
    if action in ("allow", "deny", "default") and len(parts) >= 3:
        tool = parts[2]
        write_tool_policy(paths.config_dir, tool=tool, decision=action)
        display(f"Tool policy: `{tool}` → {action}.")
        return
    display("Usage: /policy show | /policy allow <tool> | /policy deny <tool> | /policy default <tool>")


def _handle_resources_command(*, command: str, paths: ConfigPaths, display: Callable[[str], None]) -> None:
    """Plan 111: `/resources` — show the active target's RAM/disk/CPU, flag any crunch,
    and the per-target resource-change log."""
    from duckln import resource_manager as rm
    from duckln.repo_bringup import _active_container_name, _active_vm_name, composite_target_label
    from state.access import read_resource_log

    et = _session_execution_target(paths.config_dir)
    vm_name = _active_container_name(paths.config_dir) if et == "container" else _active_vm_name(paths.config_dir)
    target_label = composite_target_label(et, vm_name)
    snap = rm.probe_target_resources(config_dir=paths.config_dir, execution_target=et, vm_name=vm_name)
    if not snap.known:
        display(f"Couldn't read resources for {target_label} (target unreachable?).")
    else:
        display(
            f"Resources for {target_label}:\n"
            f"  • RAM: {snap.ram_mb // 1024 or '<1'} GB (swap: {'on' if snap.swap_on else 'off'})\n"
            f"  • Disk: {snap.disk_free_mb // 1024} GB free of {snap.disk_total_mb // 1024} GB ({snap.disk_used_pct}% used)\n"
            f"  • CPU: {snap.cpu_cores} cores"
        )
        if snap.disk_used_pct >= 90:
            display(f"  ⚠ Disk is {snap.disk_used_pct}% full — a heavy build may fail; `/resources` can grow it on approval.")
    log = read_resource_log(paths.config_dir, target_label)
    if log:
        import datetime as _dt
        display("Resource log (recent):")
        for e in log[-8:]:
            ts = _dt.datetime.fromtimestamp(e.get("t", 0)).strftime("%Y-%m-%d %H:%M")
            display(f"  · {ts} — {e.get('event','')[:160]}")


def _handle_prefs_command(*, command: str, paths: ConfigPaths, display: Callable[[str], None]) -> None:
    """Plan 95: `/prefs` — view/set persisted user preferences."""
    from state.access import read_user_preferences, write_user_preference

    parts = command.split(maxsplit=2)
    if len(parts) <= 1 or parts[1] in ("show", "list"):
        prefs = read_user_preferences(paths.config_dir)
        if not prefs:
            display("No preferences set. Use `/prefs set <key> <value>` (e.g. `/prefs set answer_style concise`).")
        else:
            display("Preferences:\n" + "\n".join(f"  • {k} = {v}" for k, v in sorted(prefs.items())))
        return
    if parts[1] == "set" and len(parts) == 3 and " " in parts[2]:
        key, _, value = parts[2].partition(" ")
        write_user_preference(paths.config_dir, key=key.strip(), value=value.strip())
        display(f"Preference set: {key.strip()} = {value.strip()}.")
        return
    display("Usage: /prefs show | /prefs set <key> <value>")


def _render_tools_summary(config_dir: Path) -> str:
    """Read-only renderer for /tools — show the active Duckln tool contract."""

    execution_target = _session_execution_target(config_dir)
    rendered = render_tools_manifest(policy=ToolVisibilityPolicy(execution_target=execution_target))
    if not rendered.entries:
        return f"Duckln has no tools active for target {execution_target}."
    lines = [f"Duckln tools for {execution_target} ({len(rendered.entries)} active):"]
    for entry in rendered.entries:
        approval = "approval" if entry.approval_required else "auto-safe"
        lines.append(
            f"  • {entry.tool_id} — {entry.label}; {entry.execution_domain}; {entry.safety_class.value}; {approval}"
        )
    return "\n".join(lines)


def _render_mcp_summary(config_dir: Path) -> str:
    """Read-only renderer for /mcp — show MCP-backed capabilities when configured."""

    execution_target = _session_execution_target(config_dir)
    rendered = render_tools_manifest(policy=ToolVisibilityPolicy(execution_target=execution_target))
    mcp_entries = tuple(entry for entry in rendered.entries if entry.provider_type == "mcp")
    if not mcp_entries:
        return (
            "No MCP servers are active in Duckln's tool contract yet. MCP is for external "
            "servers/connectors that expose tools, resources, or prompts."
        )
    lines = [f"Duckln MCP capabilities for {execution_target}:"]
    for entry in mcp_entries:
        lines.append(f"  • {entry.tool_id} — {entry.label}; {entry.capability_class}")
    return "\n".join(lines)


def _classify_extension_request(text: str) -> tuple[str, str]:
    """Classify a natural-language extension request as skill, tool, or MCP."""

    lowered = str(text or "").casefold()
    if any(token in lowered for token in ("mcp", "server", "connector", "resource", "prompt server")):
        return "mcp", "This sounds like an MCP connector: an external server that exposes tools/resources/prompts."
    if any(token in lowered for token in ("run", "execute", "api", "cli", "command", "sdk", "call", "fetch", "write", "create")):
        return "tool", "This sounds like a tool: Duckln would execute or call it to do work."
    return "skill", "This sounds like a skill: reusable instructions or a playbook Duckln should follow."


_AGENT_ROLE_LABELS = {
    "SUPERVISOR": "Supervisor",
    "REPO_AGENT": "Repo agent",
    "ERROR_AGENT": "Error agent",
    "WEB_READER": "Web reader",
}


def _render_agent_routing_status(config_dir: Path, current: AppConfig) -> str:
    """Plan 179 C3: a `/status` table of which model powers each agent node (+ host memory/GPU)."""
    from duckln.ai_client import AGENT_ROLES, read_active_routing

    routing = read_active_routing(config_dir)
    global_model = getattr(current, "model", "") or "(none)"
    provider = getattr(getattr(current, "provider", None), "value", "") or str(getattr(current, "provider", "") or "")
    lines = [
        "⚙️  Agent model routing  (use /models to change)",
        f"  Provider : {provider}",
        f"  Mode     : {'Specialized' if routing else 'Unified'}",
        "",
    ]
    for role in AGENT_ROLES:
        assigned = routing.get(role)
        shown = assigned if assigned else f"{global_model}  (unified)"
        lines.append(f"  {_AGENT_ROLE_LABELS.get(role, role):<11}→ {shown}")
    # Plan 181: the Web reader model distills long fetched pages into a compact digest
    # (token saving) before the main agent reasons; unset → it uses the global model.
    lines += ["", "  Web reader distills long fetched pages (point it at a cheap model to save tokens)."]
    try:
        from duckln.resource_manager import probe_host_resources

        snap = probe_host_resources()
        bits = []
        if getattr(snap, "ram_mb", 0):
            bits.append(f"RAM ~{snap.ram_mb // 1024} GB")
        if getattr(snap, "gpu_present", False):
            bits.append(f"GPU {getattr(snap, 'gpu_name', '') or 'present'}"
                        + (f" ({snap.vram_mb // 1024} GB)" if getattr(snap, "vram_mb", 0) else ""))
        if bits:
            lines += ["", "  Host     : " + " · ".join(bits)]
    except Exception:
        pass
    return "\n".join(lines)


def _handle_agent_routing_command(
    current: AppConfig,
    paths: ConfigPaths,
    *,
    select: Callable[[str, tuple[str, ...]], str | None] | None,
    text_prompt: Callable[[str, str], str | None] | None,
    display: Callable[[str], None],
) -> AppConfig:
    """Plan 179 C3: the two-tier model configuration — Unified (one model for all agents) vs
    Specialized (assign a distinct model id per agent node). Persisted to `active_routing`."""
    from duckln.ai_client import AGENT_ROLES, read_active_routing, write_active_routing

    if select is None:
        display(_render_agent_routing_status(paths.config_dir, current))
        return current
    mode = select(
        "Model configuration mode:",
        ("Unified — one model for all agents", "Specialized — assign a model per agent"),
    )
    if mode is None:
        return current
    if mode.startswith("Unified"):
        write_active_routing(paths.config_dir, {})
        display(f"Unified Mode — every agent uses the global model ({getattr(current, 'model', '') or '(none)'}).")
        return current
    routing = dict(read_active_routing(paths.config_dir))
    global_model = getattr(current, "model", "") or ""
    # Plan 180 F3: offer the provider's LIVE model list (arrow-key select) when it can be
    # fetched; fall back to text entry of a model id (offline / fetch failure).
    model_ids: tuple[str, ...] = ()
    try:
        from duckln.config import _model_selection_ids, _sorted_models

        adapter = get_provider_adapter_for_base_url(current.provider, base_url=current.base_url)
        validation = adapter.validate_api_key(current.api_key)
        if getattr(validation, "ok", False):
            model_ids = _model_selection_ids(current.provider, _sorted_models(validation.models))
    except Exception:
        model_ids = ()
    _KEEP = "↩ keep current"
    if model_ids is None:
        model_ids = ()
    for role in AGENT_ROLES:
        default = routing.get(role, global_model)
        label = _AGENT_ROLE_LABELS.get(role, role)
        if model_ids:
            options = (f"{_KEEP} ({default or 'global'})",) + tuple(m for m in model_ids if m != default)
            choice = select(f"Model for {label}:", options)
            if choice is None or choice.startswith(_KEEP):
                continue
            routing[role] = choice
        elif text_prompt is not None:
            entered = text_prompt(
                f"Model id for {label} (Enter = {default or 'global'}):", default or "",
            )
            entered = (entered or "").strip()
            if entered:
                routing[role] = entered
        else:
            display("Specialized Mode needs interactive input; assign per-agent models with /models in the TUI.")
            return current
    write_active_routing(paths.config_dir, routing)
    display(_render_agent_routing_status(paths.config_dir, current))
    return current


def _draft_extension_config(kind: str, description: str) -> dict:
    """Plan 146 B4: DRAFT a registerable tool/MCP config from a natural-language description
    (the detect→DRAFT step before the user confirms→register). Returns a config dict the
    confirm step persists; for `mcp` it's a stdio-connector skeleton, for `tool` a shell-tool
    skeleton. Repo-agnostic; the user fills the command before it goes live."""
    slug = re.sub(r"[^a-z0-9]+", "-", description.casefold()).strip("-")[:48] or kind
    if kind == "mcp":
        return {
            "name": slug, "provider_type": "mcp", "transport": "stdio",
            "command": "", "args": [], "env": {}, "description": description, "status": "draft",
        }
    return {
        "name": slug, "provider_type": "shell", "command": "", "args": [],
        "description": description, "status": "draft",
    }


def _handle_extension_add_command(
    *,
    command: str,
    config_dir: Path,
    text_prompt: Callable[[str, str], str | None] | None,
    select_prompt: Callable[[str, tuple[str, ...]], str | None] | None,
    display_output: Callable[[str], None],
) -> None:
    """Capture a skill/tool/MCP proposal without mutating core manifests."""

    default_kind = "skill" if command.startswith("/skill") else "tool"
    inline = command.split(" ", 2)[2].strip() if len(command.split(" ", 2)) >= 3 else ""
    description = inline
    if not description and text_prompt is not None:
        description = text_prompt(
            "Describe what this skill/tool should help Duckln do.",
            "",
        ) or ""
    description = " ".join(description.split())
    if not description:
        display_output(f"Tell Duckln what the {default_kind} should do, e.g. `{command} read Terraform plans safely`.")
        return
    recommended_kind, reason = _classify_extension_request(description)
    if command.startswith("/skill") and recommended_kind == "tool":
        reason += " You used /skill add, but Duckln will store it as a proposal until you confirm the category."
    if command.startswith("/tools") and recommended_kind == "skill":
        reason += " You used /tools add, but Duckln will store it as a proposal until you confirm the category."
    display_output(reason)
    choices = ("Continue with this category", "Change category", "Schedule later", "Cancel")
    choice = select_prompt("What should Duckln do with this proposal?", choices) if select_prompt is not None else choices[0]
    if choice is None or choice == "Cancel":
        display_output("Proposal cancelled.")
        return
    final_kind = recommended_kind
    scheduled = False
    if choice == "Change category" and select_prompt is not None:
        selected_kind = select_prompt("Choose the category:", ("skill", "tool", "mcp", "Cancel"))
        if selected_kind is None or selected_kind == "Cancel":
            display_output("Proposal cancelled.")
            return
        final_kind = selected_kind
    elif choice == "Schedule later":
        scheduled = True
    slug = re.sub(r"[^a-z0-9]+", "-", description.casefold()).strip("-")[:48] or final_kind
    status = "scheduled" if scheduled else "pending_review"
    # Plan 146 B4: for a tool/MCP, DRAFT a registerable config now (detect→draft→confirm→
    # register) so the proposal is actionable, not just a note. The user fills the command
    # and confirms before it goes live in the tool contract.
    import json as _json
    draft = _draft_extension_config(final_kind, description) if final_kind in ("tool", "mcp") else None
    _blocks = [
        paragraph_block(f"Kind: {final_kind}"),
        paragraph_block(f"Status: {status}"),
        paragraph_block(f"Request: {description}"),
    ]
    if draft is not None:
        _blocks.append(paragraph_block("Drafted config (fill `command`, then confirm to register):"))
        _blocks.append(paragraph_block(_json.dumps(draft, indent=2)))
    else:
        _blocks.append(paragraph_block("No executable tool or core manifest was changed."))
    initialize_state_store(config_dir).upsert_managed_memory(
        memory_key=f"extension-proposal:{final_kind}:{slug}",
        memory_kind=f"pending_{final_kind}",
        relative_path=f"proposals/{final_kind}-{slug}.md",
        title=f"Pending {final_kind}: {description[:60]}",
        content=join_blocks(*_blocks),
        metadata={"kind": final_kind, "status": status, "source_command": command,
                  **({"draft_config": _json.dumps(draft)} if draft is not None else {})},
    )
    _suffix = " A registerable config was drafted — fill its `command` and confirm to add it." if draft is not None else ""
    display_output(f"Stored {status} {final_kind} proposal.{_suffix} Duckln will not change tools or skills until you approve the next step.")

    # Plan 155 F8: detect → draft → CONFIRM → REGISTER. A drafted tool/MCP can now actually
    # be registered into the tool contract (no longer a proposal-only dead-end) so the agent
    # sees + can call it. User confirms once; nothing is registered without that confirm.
    if draft is not None and not scheduled and final_kind in ("tool", "mcp") and select_prompt is not None:
        register_choice = select_prompt(
            f"Register this {final_kind} now so the agent can use it? (You can fill its command later.)",
            ("Register now", "Keep as proposal only"),
        )
        if register_choice == "Register now":
            tool_id = f"{final_kind}.{slug}"
            initialize_state_store(config_dir).upsert_managed_memory(
                memory_key=f"registered-{final_kind}:{slug}",
                memory_kind=f"registered_{final_kind}",
                relative_path=f"tools/{final_kind}-{slug}.json",
                title=f"Registered {final_kind}: {description[:60]}",
                content=_json.dumps({
                    "tool_id": tool_id, "label": description[:48] or tool_id,
                    "description": description, "draft_config": draft,
                }),
                metadata={"kind": final_kind, "status": "registered", "tool_id": tool_id},
            )
            # Re-materialize the agent-visible tools manifest so the new tool is live.
            try:
                from state.access import materialize_managed_memory_state
                materialize_managed_memory_state(config_dir)
            except Exception:
                pass
            display_output(
                f"Registered {final_kind} '{tool_id}' — it's now in the tool contract and visible to the agent. "
                "Fill its command when ready; Duckln will ask before running it (approval required)."
            )

    # Plan 178 F4: a SKILL (procedural knowledge, text) is safe to register directly — once
    # registered it is LOADED into the agent's context (registered_skill_hints) and actually
    # consulted, closing the "pull → use" gap. User confirms once.
    if final_kind == "skill" and not scheduled and select_prompt is not None:
        register_choice = select_prompt(
            "Register this skill now so the agent consults it on relevant tasks?",
            ("Register now", "Keep as proposal only"),
        )
        if register_choice == "Register now":
            initialize_state_store(config_dir).upsert_managed_memory(
                memory_key=f"registered-skill:{slug}",
                memory_kind="registered_skill",
                relative_path=f"skills/{slug}.md",
                title=description[:60],
                content=description,
                metadata={"kind": "skill", "status": "registered"},
            )
            try:
                from state.access import materialize_managed_memory_state
                materialize_managed_memory_state(config_dir)
            except Exception:
                pass
            display_output(
                f"Registered skill '{slug}' — Duckln will load it into the agent's context on "
                "relevant tasks (it's knowledge, so nothing executes)."
            )


def _render_repo_history_summary(config_dir: Path, *, limit: int = 10) -> str:
    """Item: show last N run_history rows for the active repo (deploy + run + repair)."""

    try:
        store = initialize_state_store(config_dir)
        latest = store.get_latest_repo_state()
        if latest is None:
            return "No active repo on record yet — pick one with /repos."
        repo_key = latest.repo_key or latest.repo_url or ""
        rows = []
        try:
            rows = list(store.list_run_history(repo_key=repo_key, limit=limit))
        except AttributeError:
            with store._connect() as conn:  # fallback if helper missing
                cursor = conn.execute(
                    "SELECT run_id, command_name, status, summary, finished_at FROM run_history "
                    "WHERE repo_key = ? ORDER BY started_at DESC LIMIT ?",
                    (repo_key, limit),
                )
                rows = list(cursor.fetchall())
        if not rows:
            return f"No deploy / run history yet for {latest.repo_url}."
        repo_label = str(latest.metadata.get("repo_name") or latest.repo_url or "active repo").strip()
        lines = [f"Last {min(limit, len(rows))} events for {repo_label}:"]
        digest = _summarize_run_history_rows(rows)
        if digest:
            lines.append(f"  Pattern: {digest}")
        for row in rows[:limit]:
            command = getattr(row, "command_name", row[1] if not hasattr(row, "command_name") else "")
            status = getattr(row, "status", row[2] if not hasattr(row, "status") else "")
            summary = getattr(row, "summary", row[3] if not hasattr(row, "summary") else "")
            finished = getattr(row, "finished_at", row[4] if not hasattr(row, "finished_at") else "")
            timestamp = (finished or "").split("T")[0] if finished else ""
            lines.append(f"  • [{status:<8}] {command} — {summary}{(' (' + timestamp + ')') if timestamp else ''}")
        return "\n".join(lines)
    except Exception as exc:
        return f"Could not read deploy history: {exc}"


def _summarize_run_history_rows(rows) -> str:
    """Aggregate counts across the last N run_history rows.

    Surfaces the most-common command and the most-common failure status so the user
    can spot drift at a glance without reading every event line. Returns "" when
    there's nothing useful to highlight (single row, all unique)."""
    if not rows or len(rows) < 2:
        return ""
    from collections import Counter

    cmd_counts: Counter = Counter()
    fail_counts: Counter = Counter()
    success_total = 0
    failure_total = 0
    for row in rows:
        command = getattr(row, "command_name", row[1] if not hasattr(row, "command_name") else "")
        status = getattr(row, "status", row[2] if not hasattr(row, "status") else "")
        cmd = (command or "").strip()
        st = (status or "").strip().casefold()
        if cmd:
            cmd_counts[cmd] += 1
        if st in {"success", "ok", "passed", "verified"}:
            success_total += 1
        elif st in {"failed", "error", "timeout", "blocked"}:
            failure_total += 1
            if cmd:
                fail_counts[cmd] += 1
    pieces: list[str] = []
    if cmd_counts:
        most_run, count = cmd_counts.most_common(1)[0]
        if count >= 2:
            pieces.append(f"most run = {most_run} ({count}×)")
    if failure_total > 0 and (success_total + failure_total) > 0:
        rate = int(round(failure_total * 100 / (success_total + failure_total)))
        pieces.append(f"failure rate = {rate}%")
        if fail_counts:
            worst, worst_count = fail_counts.most_common(1)[0]
            if worst_count >= 2:
                pieces.append(f"most-failing = {worst}")
    return ", ".join(pieces)


def _render_active_deploy_status_summary(config_dir: Path) -> str:
    """Item 3: surface the live repo_deploy objective (phase, repo, target, duration)."""

    workflow = _AGENT_CONTEXT_SERVICE.load_workflow_state(config_dir=config_dir)
    if workflow is None:
        return "No active Duckln deploy or repair objective right now."
    kind = str(getattr(workflow, "active_objective_kind", "") or "").strip()
    if not kind:
        return "No active Duckln deploy or repair objective right now."
    repo_name = str(getattr(workflow, "active_objective_repo_name", "") or "").strip() or "(unknown repo)"
    phase = str(getattr(workflow, "active_repair_phase", "") or "").strip() or "(no phase recorded)"
    target = str(getattr(workflow, "active_objective_execution_target", "") or "").strip() or "(unknown target)"
    status = str(getattr(workflow, "active_objective_status", "") or "").strip() or "(unknown status)"
    started_raw = str(getattr(workflow, "active_objective_started_at", "") or "").strip()
    duration_part = ""
    if started_raw:
        started_at = _parse_timestamp(started_raw)
        if started_at is not None:
            elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
            mins, secs = divmod(int(elapsed), 60)
            duration_part = f" • {mins}m{secs:02d}s elapsed"
    blocker = str(getattr(workflow, "active_objective_last_blocker", "") or "").strip()
    blocker_part = f"\nLast blocker: {blocker}" if blocker else ""
    kind_label = kind.replace("_", " ")
    return (
        f"Active {kind_label} objective\n"
        f"  • repo: {repo_name}\n"
        f"  • target: {target}\n"
        f"  • phase: {phase}\n"
        f"  • status: {status}{duration_part}"
        f"{blocker_part}"
    )


def _render_live_repo_sessions_summary(config_dir: Path) -> str:
    sessions = _AGENT_CONTEXT_SERVICE.load_live_repo_sessions_snapshot(config_dir=config_dir)
    if not sessions:
        return "Duckln is not currently tracking any live repo sessions."
    return join_blocks(
        paragraph_block("Duckln is currently tracking these live repo sessions:"),
        comparison_block(
            "Live repo sessions",
            tuple(
                (
                    session.repo_name,
                    f"{session.status} • {_live_session_target_label(session)}",
                )
                for session in sessions[:8]
            ),
        ),
    )


def _render_active_repo_path(config_dir: Path) -> str:
    row = initialize_state_store(config_dir).get_latest_repo_state()
    if row is None:
        return "Duckln does not currently have an active repo path recorded."
    path = str(row.metadata.get("install_location") or row.repo_path or "").strip()
    if not path:
        return f"Duckln does not currently have a stored path for {_repo_display_name(row)}."
    return f"{_repo_display_name(row)} is tracked at {path} on {_repo_target_label(row)}."


def _live_session_target_label(session) -> str:
    target = str(getattr(session, "execution_target", "") or "local").strip().lower()
    if target == "vm":
        vm_name = str(getattr(session, "vm_name", "") or "").strip()
        return f"VM {vm_name}" if vm_name else "VM"
    if target == "docker":
        docker_name = str(getattr(session, "docker_name", "") or "").strip()
        return f"Docker {docker_name}" if docker_name else "Docker"
    if target in {"aws", "gcp"}:
        vendor = str(getattr(session, "cloud_vendor", "") or target.upper()).strip()
        region = str(getattr(session, "cloud_region", "") or "").strip()
        return f"{vendor} {region}".strip()
    return "local machine"


def _tail_log_excerpt(log_path: Path, *, limit: int = 20) -> str:
    if not log_path.exists():
        return "Duckln does not have a live log file for that repo session yet."
    lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    excerpt = lines[-limit:]
    if not excerpt:
        return f"Duckln has not captured any log lines in {log_path} yet."
    return join_blocks(
        paragraph_block(f"Latest runtime log excerpt from {log_path}:"),
        bullet_block(tuple(excerpt)),
    )


def _remove_repo_from_target(*, config_dir: Path, row, runner: ControlledCommandRunner | None = None) -> tuple[bool, str]:
    path_text = str(row.metadata.get("install_location") or row.repo_path or "").strip()
    if not path_text:
        return True, "Duckln cleared the tracked repo state. No stored repo path was recorded."
    if row.execution_target == "docker":
        runner_instance = runner or ControlledCommandRunner()
        docker_name = str(row.metadata.get("docker_name") or row.metadata.get("last_docker_name") or "").strip() or None
        stop_command = str(row.metadata.get("stop_command") or "").strip() or None
        if not stop_command and docker_name:
            managed = resolve_managed_resource(config_dir, resource_key=f"docker:{docker_name}")
            if managed is not None:
                stop_command = str(managed.metadata.get("stop_command") or "").strip() or None
        if not stop_command:
            workflow = _AGENT_CONTEXT_SERVICE.load_workflow_state(config_dir=config_dir)
            if workflow is not None and workflow.active_runtime_repo_key == row.repo_url:
                stop_command = workflow.active_runtime_stop_command
        if stop_command:
            stop_result = runner_instance.run(stop_command, cwd=path_text if Path(path_text).exists() else None)
            if stop_result.exit_code != 0 or stop_result.timed_out:
                label = docker_name or "the tracked Docker runtime"
                return False, f"Duckln could not stop {label} before removing the repo state."
        path = Path(path_text)
        if path.exists():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            return True, f"Duckln stopped the tracked Docker runtime and removed the repo path at {path_text}."
        return True, f"Duckln stopped the tracked Docker runtime and cleared the repo state. The path {path_text} was already gone."
    if row.execution_target == "vm":
        if not row.vm_name:
            return False, "Duckln cannot remove that VM repo yet because the tracked VM name is missing."
        runner_instance = runner or ControlledCommandRunner()
        command = f"multipass exec {shlex.quote(row.vm_name)} -- rm -rf {shlex.quote(path_text)}"
        result = runner_instance.run(command)
        if result.exit_code != 0 or result.timed_out:
            return False, f"Duckln could not remove the VM repo path at {path_text}."
        return True, f"Duckln removed the VM repo path at {path_text}."
    if row.execution_target in {"aws", "gcp"}:
        workflow = _AGENT_CONTEXT_SERVICE.load_workflow_state(config_dir=config_dir)
        resource_key = str(row.metadata.get("cloud_resource_key") or "").strip() or (
            workflow.active_runtime_cloud_resource_key if workflow is not None else None
        )
        if not resource_key:
            return False, f"Duckln cannot remove that {row.execution_target.upper()} repo yet because the tracked cloud resource is missing."
        record = resolve_managed_resource(config_dir, resource_key=resource_key)
        if record is None:
            return False, f"Duckln cannot remove that {row.execution_target.upper()} repo yet because the tracked cloud resource could not be resolved."
        wrapped_command = build_cloud_remote_exec_command(
            record,
            remote_command=f"rm -rf {shlex.quote(path_text)}",
        )
        if not wrapped_command:
            return False, f"Duckln cannot remove that {row.execution_target.upper()} repo yet because the remote execution path is unavailable."
        runner_instance = runner or ControlledCommandRunner()
        result = runner_instance.run(wrapped_command)
        if result.exit_code != 0 or result.timed_out:
            return False, f"Duckln could not remove the {row.execution_target.upper()} repo path at {path_text}."
        return True, f"Duckln removed the {row.execution_target.upper()} repo path at {path_text}."
    path = Path(path_text)
    if not path.exists():
        return True, f"Duckln cleared the tracked repo state. The path {path_text} was already gone."
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    return True, f"Duckln removed the repo path at {path_text}."


def _handle_repo_remove_action(
    *,
    reply: FreeTextReply,
    current: AppConfig,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    approve_prompt: Callable[[str], bool] | None,
    runner: ControlledCommandRunner | None = None,
) -> None:
    row = _resolve_repo_state_from_action_key(paths.config_dir, reply.action_repo_key)
    if row is None:
        display_output("Duckln could not resolve that tracked repo for removal.")
        return

    repo_name = _repo_display_name(row)
    path_text = str(row.metadata.get("install_location") or row.repo_path or "unknown path").strip()
    target_label = _repo_target_label(row)
    write_workflow_state(
        paths.config_dir,
        {
            "pending_destructive_action": "remove_repo",
            "pending_destructive_repo_key": row.repo_key,
            "pending_destructive_repo_name": repo_name,
            "pending_destructive_path": path_text,
            "pending_destructive_target": target_label,
            "last_confirmation_outcome": None,
        },
    )
    if approve_prompt is None:
        display_output(f"Duckln is waiting for confirmation before removing {repo_name} from {target_label}.")
        return
    confirmed = approve_prompt(
        f"Do you want Duckln to remove {repo_name} from {target_label}? Path: {path_text}"
    )
    if not confirmed:
        write_workflow_state(
            paths.config_dir,
            {
                "pending_destructive_action": None,
                "pending_destructive_repo_key": None,
                "pending_destructive_repo_name": None,
                "pending_destructive_path": None,
                "pending_destructive_target": None,
                "last_confirmation_outcome": "cancelled",
            },
        )
        display_output(f"Duckln cancelled removing {repo_name}.")
        return

    removed, message = _remove_repo_from_target(config_dir=paths.config_dir, row=row, runner=runner)
    display_output(message)
    if not removed:
        write_workflow_state(
            paths.config_dir,
            {
                "pending_destructive_action": None,
                "pending_destructive_repo_key": None,
                "pending_destructive_repo_name": None,
                "pending_destructive_path": None,
                "pending_destructive_target": None,
                "last_confirmation_outcome": "failed",
            },
        )
        return
    initialize_state_store(paths.config_dir).clear_project_state(repo_key=row.repo_key)
    write_workflow_state(
        paths.config_dir,
        {
            "pending_destructive_action": None,
            "pending_destructive_repo_key": None,
            "pending_destructive_repo_name": None,
            "pending_destructive_path": None,
            "pending_destructive_target": None,
            "last_confirmation_outcome": "confirmed",
            "active_repo_key": None,
            "active_repo_name": None,
            "active_issue_kind": None,
            "active_issue_summary": None,
            "active_runtime_status": None,
            "active_runtime_command": None,
            "active_runtime_command_kind": None,
            "active_runtime_run_approved": None,
            "active_runtime_repo_key": None,
            "active_runtime_repo_name": None,
            "active_runtime_cwd": None,
            "active_runtime_pid": None,
            "active_runtime_log_path": None,
            "active_runtime_execution_target": None,
            "active_runtime_vm_name": None,
            "active_runtime_attach_hint": None,
            "active_runtime_logs_hint": None,
            "active_runtime_stop_hint": None,
            "active_runtime_stop_command": None,
            "active_runtime_docker_name": None,
            "active_runtime_cloud_resource_key": None,
            "active_runtime_cloud_vendor": None,
            "active_runtime_cloud_region": None,
            "active_runtime_cloud_shape": None,
        },
    )
    display_output(f"Duckln also cleared the tracked state for {repo_name}.")


def _handle_repo_stop_action(
    *,
    reply: FreeTextReply,
    current: AppConfig,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    approve_prompt: Callable[[str], bool] | None,
    runner: ControlledCommandRunner | None = None,
    terminal_interface: object | None = None,
) -> bool:
    del current
    repo = _resolve_repo_from_action_key(paths.config_dir, reply.action_repo_key)
    workflow = _AGENT_CONTEXT_SERVICE.load_workflow_state(config_dir=paths.config_dir)
    if repo is None or workflow is None:
        display_output("Duckln could not resolve a tracked runtime session to stop.")
        return False
    if workflow.active_runtime_status not in {"running", "interactive"} or workflow.active_runtime_repo_key != repo.repo_url:
        display_output(f"Duckln is not currently tracking a live runtime session for {repo.name}.")
        return False
    if approve_prompt is not None and not approve_prompt(f"Do you want Duckln to stop {repo.name} now?"):
        display_output(f"Duckln left {repo.name} running.")
        return False
    if workflow.active_runtime_stop_command:
        runner_instance = runner or ControlledCommandRunner(trace=display_output, execution_target=_session_execution_target(paths.config_dir))
        stop_result = runner_instance.run(
            workflow.active_runtime_stop_command,
            cwd=workflow.active_runtime_cwd or None,
        )
        stopped = stop_result.exit_code == 0 and not stop_result.timed_out
    elif terminal_interface is not None and hasattr(terminal_interface, "interrupt_terminal") and workflow.active_runtime_pid is None:
        stopped = bool(terminal_interface.interrupt_terminal())
    else:
        if workflow.active_runtime_pid is None:
            display_output(f"Duckln does not have a tracked PID for {repo.name}, so it cannot stop that session reliably yet.")
            return False
        runner_instance = runner or ControlledCommandRunner(trace=display_output, execution_target=_session_execution_target(paths.config_dir))
        stopped = runner_instance.stop_background(workflow.active_runtime_pid)
    if not stopped:
        display_output(f"Duckln could not stop the tracked {repo.name} runtime session cleanly.")
        return False
    row = _resolve_repo_state_from_action_key(paths.config_dir, repo.repo_url)
    if row is not None:
        metadata = dict(row.metadata)
        metadata.update(
            {
                "active": True,
                "runtime_pid": None,
                "last_run_result": "stopped",
                "last_blocker": None,
            }
        )
        initialize_state_store(paths.config_dir).upsert_repo_state(
            repo_key=row.repo_key,
            repo_url=row.repo_url,
            repo_path=row.repo_path,
            execution_target=row.execution_target,
            vm_name=row.vm_name,
            active_flag=True,
            managed_by_duckln=row.managed_by_duckln,
            status="ready",
            summary=f"Supervisor agent stopped {repo.name}.",
            metadata=metadata,
        )
    if workflow.active_runtime_docker_name:
        initialize_state_store(paths.config_dir).upsert_managed_resource(
            resource_key=f"docker:{workflow.active_runtime_docker_name}",
            resource_kind="docker_runtime",
            provider="docker",
            display_name=workflow.active_runtime_docker_name,
            execution_target="docker",
            install_root=workflow.active_runtime_cwd,
            status="stopped",
            last_activity_at=datetime.now(timezone.utc).isoformat(),
            metadata={"stop_command": workflow.active_runtime_stop_command},
        )
    write_workflow_state(
        paths.config_dir,
        {
            "active_repo_key": repo.repo_url,
            "active_repo_name": repo.name,
            "active_issue_kind": None,
            "active_issue_summary": None,
            "active_runtime_status": "stopped",
            "active_runtime_command": workflow.active_runtime_command,
            "active_runtime_command_kind": workflow.active_runtime_command_kind,
            "active_runtime_repo_key": workflow.active_runtime_repo_key,
            "active_runtime_repo_name": workflow.active_runtime_repo_name,
            "active_runtime_cwd": workflow.active_runtime_cwd,
            "active_runtime_pid": None,
            "active_runtime_log_path": workflow.active_runtime_log_path,
            "active_runtime_execution_target": workflow.active_runtime_execution_target,
            "active_runtime_vm_name": workflow.active_runtime_vm_name,
            "active_runtime_attach_hint": workflow.active_runtime_attach_hint,
            "active_runtime_logs_hint": workflow.active_runtime_logs_hint,
            "active_runtime_stop_hint": None,
            "active_runtime_stop_command": None,
            "active_runtime_docker_name": None,
            "active_runtime_cloud_resource_key": workflow.active_runtime_cloud_resource_key,
            "active_runtime_cloud_vendor": workflow.active_runtime_cloud_vendor,
            "active_runtime_cloud_region": workflow.active_runtime_cloud_region,
            "active_runtime_cloud_shape": workflow.active_runtime_cloud_shape,
        },
    )
    _sync_chat_execution_context(chat=terminal_interface, config_dir=paths.config_dir)
    display_output(f"Supervisor agent stopped {repo.name} successfully.")
    return True


def _handle_repo_logs_action(
    *,
    reply: FreeTextReply,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
) -> None:
    repo = _resolve_repo_from_action_key(paths.config_dir, reply.action_repo_key)
    workflow = _AGENT_CONTEXT_SERVICE.load_workflow_state(config_dir=paths.config_dir)
    if repo is None or workflow is None:
        display_output("Duckln could not resolve runtime logs for that repo.")
        return
    log_path = workflow.active_runtime_log_path
    if workflow.active_runtime_repo_key != repo.repo_url or not log_path:
        display_output(f"Duckln does not currently have a live runtime log path recorded for {repo.name}.")
        return
    display_output(_tail_log_excerpt(Path(log_path)))


def _handle_repo_access_action(
    *,
    reply: FreeTextReply,
    current: AppConfig,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    approve_prompt: Callable[[str], bool] | None,
    terminal_interface: object | None = None,
    skip_confirmation: bool = False,
) -> None:
    repo = _resolve_repo_from_action_key(paths.config_dir, reply.action_repo_key)
    row = _resolve_repo_state_from_action_key(paths.config_dir, reply.action_repo_key)
    workflow = _AGENT_CONTEXT_SERVICE.load_workflow_state(config_dir=paths.config_dir)
    if repo is None and row is None:
        display_output("Duckln could not resolve a tracked repo access path for that request.")
        return
    repo_name = repo.name if repo is not None else _repo_display_name(row)
    repo_key = repo.repo_url if repo is not None else row.repo_url or row.repo_key
    workflow_matches_repo = bool(
        workflow is not None
        and repo_key is not None
        and workflow.active_runtime_repo_key == repo_key
    )
    access_hint = None
    if row is not None:
        raw_access_hint = row.metadata.get("access_hint")
        if isinstance(raw_access_hint, str) and raw_access_hint.strip():
            access_hint = raw_access_hint.strip()
    if access_hint is None and workflow_matches_repo and workflow is not None:
        access_hint = workflow.active_runtime_attach_hint

    if workflow_matches_repo and workflow is not None and workflow.active_runtime_status == "interactive":
        display_output(f"Duckln already has {repo_name} open in the terminal pane.")
        return

    url = _extract_url_from_text(access_hint or "")
    if url:
        _open_repo_access_url(
            repo_name=repo_name,
            url=url,
            current=current,
            paths=paths,
            display_output=display_output,
            approve_prompt=approve_prompt,
            terminal_interface=terminal_interface,
        )
        return
    attach_command, attach_cwd, attach_target_label = _build_repo_attach_command(
        config_dir=paths.config_dir,
        row=row,
        workflow=workflow,
        repo_key=repo_key,
        repo_name=repo_name,
    )
    if attach_command and terminal_interface is not None and hasattr(terminal_interface, "run_terminal_command"):
        details = [
            f"Resolved repo: {repo_name}",
            f"Attach target: {attach_target_label}",
            f"Attach command: {attach_command}",
        ]
        if attach_cwd:
            details.append(f"Working directory: {attach_cwd}")
        if workflow_matches_repo and workflow is not None and workflow.active_runtime_status == "running":
            details.append("Opening this attach path will replace the current terminal-pane session for that repo.")
        display_output(
            render_tool_invocation_trace(
                title="repo access review",
                tool_id="process.session_runtime",
                action=f"Open {repo_name} in Duckln's terminal pane",
                detail_lines=tuple(details),
                execution_target=_session_execution_target(paths.config_dir),
            )
        )
        prompt = (
            f"Do you want Duckln to open {repo_name} in the terminal pane now? This will replace the current terminal session for that repo."
            if workflow_matches_repo and workflow is not None and workflow.active_runtime_status == "running"
            else f"Do you want Duckln to open {repo_name} in the terminal pane now?"
        )
        if (not skip_confirmation) and approve_prompt is not None and not approve_prompt(prompt):
            display_output(f"Supervisor agent left {repo_name} unchanged.")
            return
        started = bool(terminal_interface.run_terminal_command(command=attach_command, cwd=attach_cwd))
        if not started:
            display_output(f"Supervisor agent could not open {repo_name} in Duckln’s terminal pane from this session.")
            return
        if row is not None and row.execution_target in {"aws", "gcp"}:
            resource_key = str(row.metadata.get("cloud_resource_key") or "").strip() or (
                workflow.active_runtime_cloud_resource_key if workflow_matches_repo and workflow is not None else None
            )
            if resource_key:
                record_managed_resource_activity(paths.config_dir, resource_key=resource_key)
        write_workflow_state(
            paths.config_dir,
            {
                "active_repo_key": repo_key,
                "active_repo_name": repo_name,
                "active_issue_kind": None,
                "active_issue_summary": None,
                "active_runtime_status": "interactive",
                "active_runtime_command": attach_command,
                "active_runtime_command_kind": "attach",
                "active_runtime_repo_key": repo_key,
                "active_runtime_repo_name": repo_name,
                "active_runtime_cwd": (
                    attach_cwd
                    or (workflow.active_runtime_cwd if workflow_matches_repo and workflow is not None else None)
                    or (str(row.metadata.get("install_location") or row.repo_path or "").strip() if row is not None else None)
                ),
                "active_runtime_pid": None,
                "active_runtime_log_path": workflow.active_runtime_log_path if workflow_matches_repo and workflow is not None else None,
                "active_runtime_execution_target": row.execution_target if row is not None else workflow.active_runtime_execution_target if workflow is not None else None,
                "active_runtime_vm_name": row.vm_name if row is not None else workflow.active_runtime_vm_name if workflow is not None else None,
                "active_runtime_attach_hint": access_hint,
                "active_runtime_logs_hint": workflow.active_runtime_logs_hint if workflow_matches_repo and workflow is not None else None,
                "active_runtime_stop_hint": workflow.active_runtime_stop_hint if workflow_matches_repo and workflow is not None else None,
                "active_runtime_stop_command": workflow.active_runtime_stop_command if workflow_matches_repo and workflow is not None else None,
                "active_runtime_docker_name": (
                    str(row.metadata.get("docker_name") or "").strip() if row is not None else None
                ) or (workflow.active_runtime_docker_name if workflow is not None else None),
                "active_runtime_cloud_resource_key": (
                    str(row.metadata.get("cloud_resource_key") or "").strip() if row is not None else None
                ) or (workflow.active_runtime_cloud_resource_key if workflow is not None else None),
                "active_runtime_cloud_vendor": (
                    str(row.metadata.get("cloud_vendor") or "").strip() if row is not None else None
                ) or (workflow.active_runtime_cloud_vendor if workflow is not None else None),
                "active_runtime_cloud_region": (
                    str(row.metadata.get("cloud_region") or "").strip() if row is not None else None
                ) or (workflow.active_runtime_cloud_region if workflow is not None else None),
                "active_runtime_cloud_shape": workflow.active_runtime_cloud_shape if workflow is not None else None,
            },
        )
        _sync_chat_execution_context(chat=terminal_interface, config_dir=paths.config_dir)
        display_output(f"Supervisor agent opened {repo_name} in Duckln’s terminal pane on {attach_target_label}.")
        return

    if access_hint:
        display_output(access_hint)
        return
    display_output(f"Duckln does not have a stronger tracked attach path for {repo_name} yet.")


def _handle_repo_remove_command(
    *,
    current: AppConfig,
    paths: ConfigPaths,
    select_prompt: Callable[[str, tuple[str, ...]], str | None] | None,
    approve_prompt: Callable[[str], bool] | None,
    display_output: Callable[[str], None],
) -> None:
    rows = initialize_state_store(paths.config_dir).list_repo_states()
    if not rows:
        display_output("Duckln is not tracking any repos to remove.")
        return
    if len(rows) == 1 or select_prompt is None:
        selected_row = rows[0]
    else:
        choices = tuple(f"{_repo_display_name(row)} — {row.status} — {_repo_target_label(row)}" for row in rows) + ("Cancel",)
        selected = select_prompt("Choose a tracked repo to remove:", choices)
        if selected in {None, "Cancel"}:
            display_output("Repo removal cancelled.")
            return
        selected_row = rows[choices.index(selected)]
    _handle_repo_remove_action(
        reply=FreeTextReply(text="", intent="repo_removal", action="remove_repo", action_repo_key=selected_row.repo_key),
        current=current,
        paths=paths,
        display_output=display_output,
        approve_prompt=approve_prompt,
    )


def _emit_repo_usage_summary_and_offer_demo(
    *,
    repo: RepoCatalogRecord,
    paths: ConfigPaths,
    display: Callable[[str], None],
    approve: Callable[[str], bool] | None,
    project_dir: object | None,
) -> None:
    """Plan 57 Phase 5: after a successful run, show the README's usage summary,
    and offer to demo the first example command. Repo-agnostic — works for any
    repo whose README has a Usage / Quick Start / Getting Started section.
    Silent no-op when README lacks usable info — never spams the user."""
    if project_dir is None:
        return
    try:
        from pathlib import Path as _Path
        from duckln.readme_skill import extract_readme_metadata
        readme_path: _Path | None = None
        proj = _Path(str(project_dir))
        for name in ("README.md", "README.rst", "README.txt", "README", "readme.md"):
            candidate = proj / name
            if candidate.exists():
                readme_path = candidate
                break
        if readme_path is None:
            return
        readme_text = readme_path.read_text(encoding="utf-8", errors="ignore")
        metadata = extract_readme_metadata(readme_text)
        if not metadata.usage_summary and not metadata.usage_example:
            return
        if metadata.usage_summary:
            display(f"Usage: {metadata.usage_summary}")
        if metadata.usage_example and approve is not None:
            wants_demo = approve(
                f"Want Duckln to run an example to show how to use {repo.name}? "
                f"Example: `{metadata.usage_example}` [y/n]"
            )
            if wants_demo:
                display(f"▶ Example to try: `{metadata.usage_example}`")
                display(f"  Copy/paste it into the terminal — Duckln has left the shell ready for you.")
        # Persist usage info to the existing skill record so future invocations
        # can surface it without re-reading the README.
        try:
            from state.access import write_skill_memory_state
            usage_lines: list[str] = []
            if metadata.usage_summary:
                usage_lines.append(f"## Usage summary\n{metadata.usage_summary}")
            if metadata.usage_example:
                usage_lines.append(f"## Example\n  {metadata.usage_example}")
            if usage_lines:
                write_skill_memory_state(
                    paths.config_dir,
                    slug=f"usage-{repo.name}",
                    title=f"How to use {repo.name}",
                    summary="\n\n".join(usage_lines),
                )
        except Exception:
            pass
    except Exception:
        # Never let usage display break the success flow.
        return


def _maybe_launch_repo_access(
    *,
    repo: RepoCatalogRecord,
    current: AppConfig,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    approve_prompt: Callable[[str], bool] | None,
    terminal_interface: object | None = None,
) -> None:
    row = _resolve_repo_state_from_action_key(paths.config_dir, repo.repo_url)
    workflow = _AGENT_CONTEXT_SERVICE.load_workflow_state(config_dir=paths.config_dir)
    if row is None and workflow is None:
        return
    repo_key = repo.repo_url
    workflow_matches_repo = bool(
        workflow is not None
        and workflow.active_runtime_repo_key == repo_key
    )
    access_hint = None
    if row is not None:
        raw_access_hint = row.metadata.get("access_hint")
        if isinstance(raw_access_hint, str) and raw_access_hint.strip():
            access_hint = raw_access_hint.strip()
    if access_hint is None and workflow_matches_repo and workflow is not None:
        access_hint = workflow.active_runtime_attach_hint

    preview_supported = bool(terminal_interface is not None and hasattr(terminal_interface, "open_web_preview"))
    url = _extract_url_from_text(access_hint or "")
    runtime_transport = ""
    if row is not None:
        runtime_transport = str(row.metadata.get("runtime_transport") or "").strip().lower()

    if terminal_interface is not None and workflow_matches_repo and workflow is not None:
        if workflow.active_runtime_status == "interactive":
            display_output(f"Supervisor agent kept {repo.name} open in Duckln’s terminal pane.")
            return
        if runtime_transport == "terminal_pane" and workflow.active_runtime_status == "running":
            display_output(f"Supervisor agent kept {repo.name} live in Duckln’s terminal pane so you can continue there.")
            return
    if terminal_interface is not None and runtime_transport == "terminal_pane" and row is not None and row.status == "running":
        display_output(f"Supervisor agent kept {repo.name} live in Duckln’s terminal pane so you can continue there.")
        return

    if not url and terminal_interface is None:
        display_output(f"Duckln setup completed for {repo.name}. Repo is ready and Duckln stopped the active setup flow.")
        return

    if url and not preview_supported:
        _open_repo_access_url(
            repo_name=repo.name,
            url=url,
            current=current,
            paths=paths,
            display_output=display_output,
            approve_prompt=approve_prompt,
            terminal_interface=terminal_interface,
        )
        return

    if url and preview_supported:
        opened_preview = bool(terminal_interface.open_web_preview(url=url, title_hint=repo.name))
        if opened_preview:
            display_output(f"Supervisor agent opened {repo.name} in Duckln’s managed browser window at {url}.")
            return

    if terminal_interface is not None:
        _handle_repo_access_action(
            reply=FreeTextReply(
                text="",
                intent="repo_access",
                action="attach_repo",
                action_repo_key=repo.repo_url,
            ),
            current=current,
            paths=paths,
            display_output=display_output,
            approve_prompt=approve_prompt,
            terminal_interface=terminal_interface,
            skip_confirmation=True,
        )
        return

    if not url:
        return
    _open_repo_access_url(
        repo_name=repo.name,
        url=url,
        current=current,
        paths=paths,
        display_output=display_output,
        approve_prompt=approve_prompt,
        terminal_interface=terminal_interface,
    )


def _build_repo_attach_command(
    *,
    config_dir: Path,
    row,
    workflow,
    repo_key: str | None,
    repo_name: str,
) -> tuple[str | None, str | None, str]:
    target_label = "local machine"
    install_location = str(row.metadata.get("install_location") or row.repo_path or "").strip() if row is not None else ""
    if row is None:
        if workflow is not None and workflow.active_runtime_cwd:
            command = _local_shell_attach_command(workflow.active_runtime_cwd)
            return command, workflow.active_runtime_cwd, target_label
        return None, None, target_label
    if row.execution_target == "vm":
        target_label = f"VM {row.vm_name}" if row.vm_name else "the active VM"
        if not row.vm_name or not install_location:
            return None, None, target_label
        # Plan 151 F2: cd into the VM-SIDE repo dir (`$HOME/.duckln/projects/<name>`), NOT the
        # stored LOCAL `install_location` (`/Users/.../.duckln/...`) — that Mac path doesn't
        # exist on the VM. Use `"$HOME/..."` (double-quoted) so bash expands it inside `bash -lc`
        # AND after the wrapper is stripped for an already-in-VM pane (Plan 151 F1); a quoted
        # `~` would NOT expand.
        _vm_name_dir = Path(install_location).name or re.sub(r"[^A-Za-z0-9._-]+", "-", repo_name).strip("-")
        shell_body = f'cd "$HOME/.duckln/projects/{_vm_name_dir}" && exec /bin/bash -l'
        return f"multipass exec {shlex.quote(row.vm_name)} -- bash -lc {shlex.quote(shell_body)}", None, target_label
    if row.execution_target == "docker":
        docker_name = str(row.metadata.get("docker_name") or row.metadata.get("last_docker_name") or "").strip() or (
            workflow.active_runtime_docker_name if workflow is not None and workflow.active_runtime_repo_key == repo_key else None
        )
        target_label = f"Docker {docker_name}" if docker_name else "Docker"
        if not docker_name:
            return None, None, target_label
        if not install_location:
            install_location = "/"
        bash_attach = f"docker exec -it {shlex.quote(docker_name)} /bin/bash -lc {shlex.quote(f'cd {shlex.quote(install_location)} && exec /bin/bash -l')}"
        sh_attach = f"docker exec -it {shlex.quote(docker_name)} /bin/sh -lc {shlex.quote(f'cd {shlex.quote(install_location)} && exec /bin/sh -l')}"
        return f"{bash_attach} || {sh_attach}", None, target_label
    if row.execution_target in {"aws", "gcp"}:
        provider = str(row.metadata.get("cloud_vendor") or row.execution_target.upper()).strip() or row.execution_target.upper()
        region = str(row.metadata.get("cloud_region") or "").strip()
        target_label = f"{provider} {region}".strip()
        resource_key = str(row.metadata.get("cloud_resource_key") or "").strip() or (
            workflow.active_runtime_cloud_resource_key if workflow is not None and workflow.active_runtime_repo_key == repo_key else None
        )
        if not resource_key:
            return None, None, target_label
        record = resolve_managed_resource(config_dir, resource_key=resource_key)
        if record is None:
            return None, None, target_label
        remote_command = "exec /bin/bash -l"
        command = build_cloud_remote_exec_command(
            record,
            remote_command=remote_command,
            remote_cwd=install_location or None,
        )
        return command, None, target_label
    return _local_shell_attach_command(install_location or str(row.repo_path or "").strip()), install_location or None, target_label


def _local_shell_attach_command(cwd: str | None) -> str | None:
    if not cwd:
        return None
    shell_body = f"cd {shlex.quote(cwd)} && exec " + '"${SHELL:-/bin/bash}" -l'
    return f"bash -lc {shlex.quote(shell_body)}"


def _open_repo_access_url(
    *,
    repo_name: str,
    url: str,
    current: AppConfig,
    paths: ConfigPaths,
    display_output: Callable[[str], None],
    approve_prompt: Callable[[str], bool] | None,
    terminal_interface: object | None = None,
) -> None:
    preview_supported = bool(terminal_interface is not None and hasattr(terminal_interface, "open_web_preview"))
    prompt_text = (
        f"Duckln can open the repo endpoint for {repo_name} inside Duckln’s managed browser window. Do you want Duckln to do that?"
        if preview_supported
        else f"Duckln can launch/open the repo endpoint for {repo_name}. Do you want Duckln to do that?"
    )
    should_launch = True if approve_prompt is None else approve_prompt(prompt_text)
    if not should_launch:
        display_output(f"Supervisor agent left {repo_name} ready. Access it at {url}.")
        return
    if preview_supported and bool(terminal_interface.open_web_preview(url=url, title_hint=repo_name)):
        display_output(f"Supervisor agent opened {repo_name} in Duckln’s managed browser window at {url}.")
        return
    open_command = _platform_open_command(url)
    decision = evaluate_mode_action(
        current.mode,
        assess_command(open_command),
        is_ai_suggested=True,
        user_approved=(approve_prompt is not None),
    )
    if not decision.allowed:
        display_output(f"Supervisor agent left {repo_name} ready. Access it at {url}.")
        return
    result = ControlledCommandRunner(
        trace=display_output,
        execution_target=_session_execution_target(paths.config_dir),
    ).run(open_command)
    if result.exit_code == 0 and not result.timed_out:
        display_output(f"Supervisor agent opened {repo_name} for you at {url}.")
    else:
        display_output(f"Supervisor agent left {repo_name} ready. Open {url} manually if needed.")


def _repo_runtime_review_items(
    *,
    config_dir: Path,
    repo_key: str | None,
    repo_name: str,
    verification_only: bool,
) -> tuple[str, ...]:
    row = _resolve_repo_state_from_action_key(config_dir, repo_key)
    items = [
        (
            f"Requested action: bounded verification for {repo_name}"
            if verification_only
            else f"Requested action: run {repo_name}"
        )
    ]
    if row is not None:
        path_text = str(row.metadata.get("install_location") or row.repo_path or "").strip()
        if path_text:
            items.append(f"Tracked repo path: {path_text}")
        items.append(f"Tracked execution target: {_repo_target_label(row)}")
        run_command = row.metadata.get("run_command")
        if isinstance(run_command, str) and run_command.strip():
            items.append(f"Stored runtime command: {run_command.strip()}")
    items.append("Duckln will use the tracked repo state and keep the action bounded.")
    return tuple(items)


def _compact_runtime_summary(message: str, *, limit: int = 220) -> str:
    compact = " ".join(message.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _runtime_search_os_hint(*, execution_target: str) -> str:
    if execution_target == "vm":
        return "Ubuntu Linux VM"
    if execution_target == "aws":
        return "AWS Linux VM"
    if execution_target == "gcp":
        return "Google Cloud Linux VM"
    return platform.system() or "Local OS"


def _show_runtime_repair_evidence(
    *,
    repo_name: str,
    failing_command: str,
    run_summary: str,
    display_output: Callable[[str], None],
    execution_target: str,
) -> RuntimeRepairEvidence:
    """Show bounded repair evidence before Duckln asks to repair a runtime issue."""

    safe_run_summary = redact_sensitive_data(run_summary)
    safe_failing_command = redact_sensitive_data(failing_command)
    evidence = gather_runtime_repair_evidence(
        command=safe_failing_command,
        error_text=safe_run_summary,
        fetch_live=True,
        trace=display_output,
        execution_target=execution_target,
        os_hint=_runtime_search_os_hint(execution_target=execution_target),
    )
    if evidence.note is None and not evidence.search_query and not evidence.source_urls:
        return evidence
    display_output(
        render_tool_invocation_trace(
            title="runtime repair evidence",
            tool_id="web.documentation_lookup",
            action=f"Cross-check the latest {repo_name} blocker against official docs or a bounded web result",
            detail_lines=(
                f"Repo with blocker: {repo_name}",
                f"Captured failing command: {safe_failing_command}",
                f"Failure summary: {_compact_runtime_summary(safe_run_summary)}",
                f"Runtime context: {_runtime_search_os_hint(execution_target=execution_target)}",
                "Duckln is surfacing this evidence before asking for repair approval.",
            ),
            execution_target=execution_target,
            source_urls=evidence.source_urls,
            search_query=evidence.search_query,
        )
    )
    if evidence.search_query:
        display_output(f"DuckDuckGo exact-error search query: {evidence.search_query}")
    if evidence.note:
        display_output(evidence.note)
    return evidence


def _runtime_repair_approval_plan_lines(
    *,
    repo_name: str,
    runtime_repair_plan,
    repair_evidence: RuntimeRepairEvidence,
) -> tuple[str, ...]:
    lines = [
        f"Duckln repair plan for {repo_name}:",
        "1. Use the captured terminal error, not raw noisy logs.",
        "2. Cross-check trusted docs or the bounded source shown above.",
        "3. Show the exact command before changing the environment.",
    ]
    if runtime_repair_plan is not None and getattr(runtime_repair_plan, "repair_command", None):
        lines.append(f"Command Duckln may run after approval: {runtime_repair_plan.repair_command}")
    else:
        lines.append("Command Duckln may run after approval: none yet — Duckln will inspect the repo first and ask again before any mutation.")
    lines.append("4. Apply only the approved step, then rerun the smallest verification command.")
    if repair_evidence.source_urls:
        lines.append("Trusted source: " + ", ".join(repair_evidence.source_urls[:2]))
    if repair_evidence.search_query:
        lines.append(f"Search query used if official docs were not enough: {repair_evidence.search_query}")
    return tuple(lines)


def _extract_url_from_text(value: str) -> str | None:
    import re

    match = re.search(r"https?://[^\s)]+", value)
    return None if match is None else match.group(0)


def _platform_open_command(url: str) -> str:
    if sys.platform == "darwin":
        return f"open '{url}'"
    if sys.platform.startswith("win"):
        return f"start {url}"
    return f"xdg-open '{url}'"


def _ensure_active_config(
    paths: ConfigPaths,
    *,
    display: Callable[[str], None],
    client: Any | None = None,
) -> AppConfig | None:
    current = load_app_config(paths)
    if current is not None:
        try:
            if not current.safety_accepted_at or not current.onboarding_complete:
                ensure_first_run_preferences(paths, display=display)
                current = load_app_config(paths) or current
        except SessionExitRequested:
            display("Exiting Duckln.")
            return None
        except OnboardingError as exc:
            display(f"Retryable error: {exc}")
            return None
        if current.provider is Provider.OLLAMA:
            _check_ollama_runtime_on_session_start(current, display=display, client=client)
        return current

    try:
        ensure_first_run_preferences(paths, display=display)
        display("Starting provider and mode setup...")
        onboarding = run_onboarding(
            paths,
            display=display,
            render_banner=lambda: render_banner(width=shutil.get_terminal_size((80, 20)).columns),
            client=client,
        )
    except SessionExitRequested:
        display("Exiting Duckln.")
        return None
    except OnboardingError as exc:
        display(f"Retryable error: {exc}")
        return None
    return onboarding.config


def _check_ollama_runtime_on_session_start(
    current: AppConfig,
    *,
    display: Callable[[str], None],
    client: Any | None,
) -> None:
    adapter = get_provider_adapter_for_base_url(current.provider, base_url=current.base_url)
    for attempt in range(1, OLLAMA_SESSION_START_MAX_ATTEMPTS + 1):
        display(
            f"Checking local Ollama runtime at {adapter.models_url()} "
            f"(attempt {attempt}/{OLLAMA_SESSION_START_MAX_ATTEMPTS})..."
        )
        validation = adapter.validate_api_key(current.api_key, client=client)
        if validation.ok:
            display(validation.message)
            return
        display(f"{validation.message} Run `ollama serve`, then Duckln will retry detection.")
        if attempt < OLLAMA_SESSION_START_MAX_ATTEMPTS:
            time.sleep(OLLAMA_SESSION_START_RETRY_SECONDS)


def _write_session_summary_on_exit(*, paths: ConfigPaths, current: AppConfig | None) -> None:
    """Persist a high-signal session summary to ~/.duckln/memory/sessions/ on graceful exit.

    Captures: provider/model/mode + active repo + active workflow phase. The store helper
    handles SQLite + filesystem materialization. Best-effort — never raises, since exit
    paths must remain robust."""
    try:
        from datetime import datetime, timezone

        if current is None:
            return
        workflow = _AGENT_CONTEXT_SERVICE.load_workflow_state(config_dir=paths.config_dir)
        active_repo = (getattr(workflow, "active_repo_name", None) or getattr(workflow, "active_objective_repo_name", None) or "").strip()
        active_phase = (getattr(workflow, "active_repair_phase", None) or "").strip()
        active_target = (getattr(workflow, "active_objective_execution_target", None) or _session_execution_target(paths.config_dir) or "local").strip()
        pieces = [
            f"Mode {current.mode.label}; provider {current.provider.label}; model {current.model}.",
            f"Execution target: {active_target}.",
        ]
        if active_repo:
            phase_text = f" (phase: {active_phase})" if active_phase else ""
            pieces.append(f"Active repo on exit: {active_repo}{phase_text}.")
        else:
            pieces.append("No active repo on exit.")
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        pieces.append(f"Recorded at {ts}.")
        write_session_summary_state(
            paths.config_dir,
            session_id="exit-latest",
            summary=" ".join(pieces),
        )
    except Exception:
        # Never block exit on a memory write — silent best-effort by design.
        pass


def _session_memory_state(config_dir: Path) -> str:
    config_file = config_dir / "config.json"
    onboarding_complete = False
    if config_file.exists():
        try:
            onboarding_complete = bool(json.loads(config_file.read_text(encoding="utf-8")).get("onboarding_complete", False))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            onboarding_complete = False
    if not onboarding_complete and not config_file.exists():
        return "setup pending"
    memory_paths = resolve_agent_memory_paths(config_dir)
    if not (
        memory_paths.memory_root.is_dir()
        and memory_paths.skills_dir.is_dir()
        and memory_paths.knowledge_dir.is_dir()
        and memory_paths.sessions_dir.is_dir()
        and memory_paths.agents_file.is_file()
    ):
        return "not initialized"
    if not memory_paths.agents_file.read_text(encoding="utf-8").strip():
        return "not initialized"
    return "ready"


def _session_execution_target(config_dir: Path) -> str:
    return read_config_snapshot(config_dir).get("execution_target", "local") or "local"


def _sync_chat_execution_context(*, chat: object | None, config_dir: Path) -> None:
    if chat is None or not hasattr(chat, "update_connection"):
        return
    context = _terminal_connection_context(config_dir)
    chat.update_connection(
        connection_type=context.connection_type,
        vm_name=context.vm_name,
        docker_name=context.docker_name,
        cloud_vendor=context.cloud_vendor,
        cloud_region=context.cloud_region,
        cloud_shape=context.cloud_shape,
    )
    _sync_chat_objective_status(chat=chat, config_dir=config_dir)
    _refresh_chat_footer(chat=chat, config_dir=config_dir)


if __name__ == "__main__":
    raise SystemExit(main(argv=sys.argv[1:]))
