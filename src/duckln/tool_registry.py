"""Authoritative runtime tool registry and agent-visible tools manifest helpers."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Literal

from duckln.runtime_governance import DEFAULT_IDLE_SHUTDOWN_MINUTES, approved_cloud_shapes, render_cloud_guardrail_lines
from duckln.safety import SafetyClass


ToolProviderType = Literal["builtin", "shell", "mcp", "sdk"]
ExecutionDomain = Literal["local", "vm", "both"]
CapabilityClass = Literal["inspection", "planning", "setup", "verification", "runtime", "cloud", "filesystem", "session", "research"]
Audience = Literal["supervisor", "specialist", "both"]


@dataclass(frozen=True)
class ToolRegistryEntry:
    """Runtime tool declaration mirrored into the agent-visible tools manifest."""

    tool_id: str
    label: str
    description: str
    provider_type: ToolProviderType
    execution_domain: ExecutionDomain
    capability_class: CapabilityClass
    safety_class: SafetyClass
    approval_required: bool
    audience: Audience
    visible_to_agent: bool = True
    restrictions: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolVisibilityPolicy:
    """Filtering view for the tools manifest."""

    execution_target: str = "local"
    include_supervisor_only: bool = True
    include_hidden: bool = False
    allowed_tool_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RenderedToolsManifest:
    """Rendered manifest plus the filtered entries used to build it."""

    content: str
    entries: tuple[ToolRegistryEntry, ...]


def default_tool_registry_entries() -> tuple[ToolRegistryEntry, ...]:
    """Return the built-in Duckln runtime tools exposed to agents."""

    return (
        ToolRegistryEntry(
            tool_id="shell.command_runner",
            label="Controlled shell runner",
            description="Run one bounded shell command when Duckln has repo or system evidence for the action and the mode policy allows execution.",
            provider_type="shell",
            execution_domain="both",
            capability_class="setup",
            safety_class=SafetyClass.S1,
            approval_required=True,
            audience="both",
            restrictions=(
                "Shell usage is bounded by Duckln safety classes and mode policy.",
                "Destructive commands remain blocked or approval-gated.",
                "Dependency installs must surface repo evidence and official reference sources before approval.",
            ),
        ),
        ToolRegistryEntry(
            tool_id="filesystem.read",
            label="Filesystem read",
            description="Read the smallest useful repo files, config files, logs, and runtime artifacts from the active workspace.",
            provider_type="builtin",
            execution_domain="both",
            capability_class="filesystem",
            safety_class=SafetyClass.S0,
            approval_required=False,
            audience="both",
            restrictions=(
                "Prefer local repo evidence before reaching for remote sources.",
                "Read only the smallest useful files for the current task.",
            ),
        ),
        ToolRegistryEntry(
            tool_id="filesystem.search",
            label="Filesystem search",
            description="Search the active workspace for setup files, command hints, logs, and implementation details before choosing a runtime action.",
            provider_type="builtin",
            execution_domain="both",
            capability_class="filesystem",
            safety_class=SafetyClass.S0,
            approval_required=False,
            audience="both",
            restrictions=(
                "Prefer targeted search over broad scans.",
                "Use search to ground actions in repo evidence.",
            ),
        ),
        ToolRegistryEntry(
            tool_id="filesystem.write_patch",
            label="Filesystem patch/write",
            description="Create or update bounded project files only when a code or config change is the verified next step.",
            provider_type="builtin",
            execution_domain="both",
            capability_class="filesystem",
            safety_class=SafetyClass.S2,
            approval_required=True,
            audience="both",
            restrictions=(
                "Do not mutate unrelated files.",
                "Explain the intended change before applying it.",
            ),
        ),
        ToolRegistryEntry(
            tool_id="repo.file_inspection",
            label="Repo file inspection",
            description="Inspect README, manifests, Docker files, and entrypoint hints before setup, repair, or launch planning.",
            provider_type="builtin",
            execution_domain="both",
            capability_class="inspection",
            safety_class=SafetyClass.S0,
            approval_required=False,
            audience="both",
        ),
        ToolRegistryEntry(
            tool_id="process.session_runtime",
            label="Process and runtime sessions",
            description="Track, start, interrupt, and inspect Duckln-managed repo runtime sessions that have a concrete run command and target.",
            provider_type="builtin",
            execution_domain="both",
            capability_class="session",
            safety_class=SafetyClass.S1,
            approval_required=True,
            audience="both",
            restrictions=(
                "Only stop or restart sessions Duckln is actively tracking.",
                "Always show the user what runtime action Duckln is taking.",
            ),
        ),
        ToolRegistryEntry(
            tool_id="runtime.terminal_pane",
            label="Interactive terminal pane",
            description="Use the live terminal pane for interactive repo commands, shell sessions, log streaming, and user-visible execution.",
            provider_type="builtin",
            execution_domain="both",
            capability_class="runtime",
            safety_class=SafetyClass.S1,
            approval_required=False,
            audience="both",
            restrictions=(
                "Prefer the terminal pane for interactive repo work.",
                "Do not hide terminal activity from the user.",
            ),
        ),
        ToolRegistryEntry(
            tool_id="docker.local_engine",
            label="Local Docker engine",
            description="Run and inspect local Docker containers or compose projects only when the repo evidence points to a Docker-based path.",
            provider_type="shell",
            execution_domain="local",
            capability_class="runtime",
            safety_class=SafetyClass.S2,
            approval_required=True,
            audience="both",
            restrictions=(
                "Use Docker only when the repo evidence points to Dockerfile or docker compose flows.",
                "Record container or compose project names so Duckln can stop them later.",
                "Show the user the exact Docker command before execution.",
            ),
        ),
        ToolRegistryEntry(
            tool_id="system.local_probe",
            label="Local system probe",
            description="Inspect host OS, CPU, RAM, disk, Python, and accelerator availability to ground setup and fit decisions.",
            provider_type="builtin",
            execution_domain="local",
            capability_class="inspection",
            safety_class=SafetyClass.S0,
            approval_required=False,
            audience="both",
        ),
        ToolRegistryEntry(
            tool_id="state.sqlite_runtime",
            label="SQLite runtime state",
            description="Read and persist concise repo, VM, workflow, command, and routing metadata through Duckln's SQLite state store.",
            provider_type="builtin",
            execution_domain="both",
            capability_class="session",
            safety_class=SafetyClass.S0,
            approval_required=False,
            audience="both",
            restrictions=(
                "SQLite is the source of truth for Duckln runtime state.",
                "Filesystem memory is a materialized view, not the primary source.",
            ),
        ),
        ToolRegistryEntry(
            tool_id="catalog.repo_access",
            label="Repo catalog access",
            description="Load curated repos, public custom GitHub repos, and recent custom repo records before repo selection or attach flows.",
            provider_type="builtin",
            execution_domain="both",
            capability_class="inspection",
            safety_class=SafetyClass.S0,
            approval_required=False,
            audience="both",
        ),
        ToolRegistryEntry(
            tool_id="web.documentation_lookup",
            label="Web and trusted public source lookup",
            description="Fetch official docs, repo metadata, GitHub issues, and other trusted public sources only when the task genuinely depends on fresh network information.",
            provider_type="sdk",
            execution_domain="both",
            capability_class="research",
            safety_class=SafetyClass.S0,
            approval_required=True,
            audience="both",
            restrictions=(
                "Use local repo evidence first.",
                "Use web access only when the task needs fresh remote information such as install docs, API references, public repo metadata, or known public issue discussions.",
                "Show the user which source Duckln checked and do not hide web lookup activity.",
            ),
        ),
        ToolRegistryEntry(
            tool_id="repo.knowledge_resolver",
            label="Repo knowledge resolver",
            description="Summarize setup files, runtime hints, stack signals, and practical repo requirements into a bounded plan.",
            provider_type="builtin",
            execution_domain="both",
            capability_class="planning",
            safety_class=SafetyClass.S0,
            approval_required=False,
            audience="both",
        ),
        ToolRegistryEntry(
            tool_id="session.history",
            label="Session history and summaries",
            description="Read concise session summaries, recent workflow state, and bounded runtime history for continuity without replaying the whole transcript.",
            provider_type="builtin",
            execution_domain="both",
            capability_class="session",
            safety_class=SafetyClass.S0,
            approval_required=False,
            audience="both",
            restrictions=(
                "Use summaries, not raw transcript dumps.",
                "Do not let stale session memory outrank the current user request.",
            ),
        ),
        ToolRegistryEntry(
            tool_id="cloud.resource_registry",
            label="Cloud and managed resource registry",
            description="Track Duckln-managed VMs, Docker resources, and cloud resources with ownership tags, install roots, and idle policies.",
            provider_type="builtin",
            execution_domain="both",
            capability_class="cloud",
            safety_class=SafetyClass.S0,
            approval_required=False,
            audience="both",
            restrictions=(
                "Every managed resource must carry Duckln ownership metadata.",
                f"Idle shutdown defaults to {DEFAULT_IDLE_SHUTDOWN_MINUTES} minutes unless the user overrides it.",
                "Cleanup must be able to enumerate only Duckln-owned resources.",
            ),
        ),
        ToolRegistryEntry(
            tool_id="cloud.aws_sdk",
            label="AWS SDK workflow",
            description="Perform bounded AWS resource actions with authenticated tooling, approved shapes, ownership tags, and explicit approval.",
            provider_type="sdk",
            execution_domain="both",
            capability_class="cloud",
            safety_class=SafetyClass.S3,
            approval_required=True,
            audience="supervisor",
            restrictions=(
                "Use only the approved instance shortlist.",
                "Never create billable resources without explicit user approval.",
                "Tag every Duckln-created AWS resource for cleanup and audit.",
            ),
        ),
        ToolRegistryEntry(
            tool_id="cloud.gcp_sdk",
            label="GCP SDK workflow",
            description="Perform bounded GCP resource actions with authenticated tooling, approved shapes, ownership tags, and explicit approval.",
            provider_type="sdk",
            execution_domain="both",
            capability_class="cloud",
            safety_class=SafetyClass.S3,
            approval_required=True,
            audience="supervisor",
            restrictions=(
                "Use only the approved instance shortlist.",
                "Never create billable resources without explicit user approval.",
                "Tag every Duckln-created GCP resource for cleanup and audit.",
            ),
        ),
        ToolRegistryEntry(
            tool_id="subagents.dispatch",
            label="Specialist subagent dispatch",
            description="Route bounded setup, debug, VM, cloud, and provider work to the right Duckln specialist contract.",
            provider_type="builtin",
            execution_domain="both",
            capability_class="session",
            safety_class=SafetyClass.S0,
            approval_required=False,
            audience="supervisor",
            restrictions=(
                "Keep specialist scope bounded to the current task.",
                "Summarize specialist handoffs clearly to the user.",
            ),
        ),
        ToolRegistryEntry(
            tool_id="vm.multipass_provision",
            label="Multipass Ubuntu VM provisioning",
            description="Create Ubuntu VMs through Canonical Multipass when isolation is the safer execution target than the local host.",
            provider_type="builtin",
            execution_domain="local",
            capability_class="runtime",
            safety_class=SafetyClass.S3,
            approval_required=True,
            audience="supervisor",
            restrictions=(
                "Multipass is Duckln's approved Ubuntu VM path.",
                "Do not invent alternate Ubuntu VM provisioning flows.",
            ),
        ),
        ToolRegistryEntry(
            tool_id="vm.runtime_configure",
            label="VM runtime configuration",
            description="Prepare Duckln runtime folders and foundational tooling inside the selected Multipass VM without copying local secrets.",
            provider_type="builtin",
            execution_domain="local",
            capability_class="setup",
            safety_class=SafetyClass.S3,
            approval_required=True,
            audience="supervisor",
            restrictions=(
                "Host MPS/CUDA does not carry into the VM by default.",
                "Do not transfer local provider credentials or memory into the VM automatically.",
            ),
        ),
        ToolRegistryEntry(
            tool_id="verification.repo_checks",
            label="Verification helpers",
            description="Run bounded smoke checks, health probes, and post-fix verification commands before Duckln claims success.",
            provider_type="builtin",
            execution_domain="both",
            capability_class="verification",
            safety_class=SafetyClass.S1,
            approval_required=False,
            audience="both",
        ),
    )


def _tool_use_when(tool_id: str) -> tuple[str, ...]:
    mapping = {
        "shell.command_runner": (
            "Use after Duckln has file, workflow, or runtime evidence for a concrete command.",
            "Use for one bounded setup, verification, or repair step.",
        ),
        "filesystem.read": (
            "Use to read README, manifests, lockfiles, logs, or config before deciding.",
        ),
        "filesystem.search": (
            "Use to locate entrypoints, dependency files, Docker assets, and runtime hints.",
        ),
        "filesystem.write_patch": (
            "Use when the verified next step is a small code or config change.",
        ),
        "repo.file_inspection": (
            "Use at the start of bring-up and again before a cross-stack repair decision.",
        ),
        "process.session_runtime": (
            "Use when Duckln has a concrete repo run command and wants to track a live runtime session.",
        ),
        "runtime.terminal_pane": (
            "Use for interactive commands, log-following, or visible runtime execution.",
        ),
        "docker.local_engine": (
            "Use when the repo contains Dockerfile, docker compose files, or documented Docker steps.",
        ),
        "system.local_probe": (
            "Use before machine-fit, accelerator, or local-vs-VM decisions.",
        ),
        "state.sqlite_runtime": (
            "Use to read or persist concise workflow, routing, resource, and repo state.",
        ),
        "catalog.repo_access": (
            "Use for curated repo browsing, tracked custom repo recall, and public GitHub repo linking.",
        ),
        "web.documentation_lookup": (
            "Use when the answer depends on fresh official docs, trusted public issue discussions, API references, or current remote metadata.",
        ),
        "repo.knowledge_resolver": (
            "Use after inspection to compress repo evidence into a stack-aware bounded plan.",
        ),
        "session.history": (
            "Use for compact continuity when Duckln needs the recent bounded thread or workflow summary.",
        ),
        "cloud.resource_registry": (
            "Use to enumerate Duckln-owned resources, install roots, idle timers, and cleanup scope.",
        ),
        "cloud.aws_sdk": (
            "Use only after the user selected AWS and explicitly approved a bounded cloud action.",
        ),
        "cloud.gcp_sdk": (
            "Use only after the user selected GCP and explicitly approved a bounded cloud action.",
        ),
        "subagents.dispatch": (
            "Use when the task fits one specialist better than the general supervisor.",
        ),
        "vm.multipass_provision": (
            "Use when the user chose Ubuntu VM execution and approved VM creation.",
        ),
        "vm.runtime_configure": (
            "Use after VM creation when the user agreed to prepare Duckln inside the VM.",
        ),
        "verification.repo_checks": (
            "Use after setup, repair, or runtime changes to verify the outcome before claiming success.",
        ),
    }
    return mapping.get(tool_id, ())


def _tool_avoid_when(tool_id: str) -> tuple[str, ...]:
    mapping = {
        "shell.command_runner": (
            "Do not use for destructive or speculative shotgun debugging commands.",
            "Do not use when a filesystem read or search would answer the question safely.",
        ),
        "filesystem.read": (
            "Do not read large unrelated files or full transcripts by default.",
        ),
        "filesystem.search": (
            "Do not broad-scan the whole workspace when a narrow pattern is enough.",
        ),
        "filesystem.write_patch": (
            "Do not mutate unrelated files or make speculative edits before verification.",
        ),
        "process.session_runtime": (
            "Do not pretend a verification command is a long-running service.",
        ),
        "runtime.terminal_pane": (
            "Do not hide execution from the user or run background work silently.",
        ),
        "docker.local_engine": (
            "Do not use for repos with a documented non-Docker path unless the user asked for Docker.",
        ),
        "web.documentation_lookup": (
            "Do not use when local repo evidence already answers the question.",
        ),
        "cloud.aws_sdk": (
            "Do not create billable resources without explicit approval and an approved shape.",
        ),
        "cloud.gcp_sdk": (
            "Do not create billable resources without explicit approval and an approved shape.",
        ),
        "vm.runtime_configure": (
            "Do not copy host credentials, API keys, or Duckln memory into the VM automatically.",
        ),
        "verification.repo_checks": (
            "Do not skip verification after a claimed fix.",
        ),
    }
    return mapping.get(tool_id, ())


def _tool_examples(tool_id: str) -> tuple[str, ...]:
    mapping = {
        "shell.command_runner": (
            "Example: run `.venv/bin/python -m pip install -r requirements.txt` after approval.",
            "Example: run `npm run dev` only after Duckln has identified the Node entrypoint.",
        ),
        "filesystem.read": (
            "Example: read `README.md` and `pyproject.toml` before planning setup.",
        ),
        "filesystem.search": (
            "Example: search for `uvicorn`, `streamlit`, `gradio`, or `docker compose` hints.",
        ),
        "filesystem.write_patch": (
            "Example: patch a missing config key or startup script after the blocker is verified.",
        ),
        "process.session_runtime": (
            "Example: start a tracked FastAPI or Streamlit process and remember its stop hint.",
        ),
        "runtime.terminal_pane": (
            "Example: stage the repo's real run command in the right pane so the user can see logs live.",
        ),
        "docker.local_engine": (
            "Example: run `docker compose up` when the repo's documented path is compose-based.",
        ),
        "system.local_probe": (
            "Example: inspect RAM and accelerator support before choosing local vs VM.",
        ),
        "state.sqlite_runtime": (
            "Example: persist the active repo, runtime status, routing decision, and cleanup hints.",
        ),
        "catalog.repo_access": (
            "Example: show curated repos plus recent custom GitHub repos in `/repos`.",
        ),
        "web.documentation_lookup": (
            "Example: fetch an official install page or trusted GitHub issue Duckln cites before approval.",
        ),
        "repo.knowledge_resolver": (
            "Example: infer Python vs Node vs Rust setup from README and manifest evidence.",
        ),
        "session.history": (
            "Example: load the bounded summary of the last repo failure before replying.",
        ),
        "cloud.resource_registry": (
            "Example: list all Duckln-tagged resources for cleanup.",
        ),
        "cloud.aws_sdk": (
            "Example: propose a tagged AWS launch using only the approved instance shortlist.",
        ),
        "cloud.gcp_sdk": (
            "Example: propose a tagged GCP launch using only the approved instance shortlist.",
        ),
        "subagents.dispatch": (
            "Example: route an audio repo failure to the audio specialist instead of the generic supervisor.",
        ),
        "vm.multipass_provision": (
            "Example: create a named Ubuntu VM with user-approved CPU and memory values.",
        ),
        "vm.runtime_configure": (
            "Example: install Duckln runtime basics inside the selected VM after user approval.",
        ),
        "verification.repo_checks": (
            "Example: rerun the repo smoke check after a dependency or config fix.",
        ),
    }
    return mapping.get(tool_id, ())


def registered_extension_entries(config_dir) -> tuple[ToolRegistryEntry, ...]:
    """Plan 155 F8: load user-registered tool/MCP extensions (persisted by `/tools add` →
    confirm) so the agent's manifest INCLUDES them and can call them — closing the old
    proposal-only dead-end. Best-effort: a missing store / bad record is skipped."""
    if config_dir is None:
        return ()
    try:
        import json as _json
        from pathlib import Path as _Path
        from state.store import initialize_state_store
        records = initialize_state_store(_Path(config_dir)).list_managed_memory_records()
    except Exception:
        return ()
    out: list[ToolRegistryEntry] = []
    for rec in records:
        kind = getattr(rec, "memory_kind", "")
        if kind not in ("registered_tool", "registered_mcp"):
            continue
        try:
            cfg = _json.loads(getattr(rec, "content", "") or "{}")
        except Exception:
            cfg = {}
        tool_id = str(cfg.get("tool_id") or getattr(rec, "memory_key", "")).strip()
        if not tool_id:
            continue
        out.append(ToolRegistryEntry(
            tool_id=tool_id,
            label=str(cfg.get("label") or tool_id),
            description=str(cfg.get("description") or ""),
            provider_type=("mcp" if kind == "registered_mcp" else "shell"),
            execution_domain="both",
            capability_class="research",
            safety_class=SafetyClass.S2,
            approval_required=True,
            audience="both",
            visible_to_agent=True,
        ))
    return tuple(out)


def tool_registry_entries(config_dir=None) -> tuple[ToolRegistryEntry, ...]:
    """Plan 155 F8: the built-in tools PLUS any user-registered extensions for this config."""
    return default_tool_registry_entries() + registered_extension_entries(config_dir)


def filter_tool_registry_entries(
    entries: tuple[ToolRegistryEntry, ...] | None = None,
    *,
    policy: ToolVisibilityPolicy | None = None,
) -> tuple[ToolRegistryEntry, ...]:
    """Filter tool entries for a given execution-target-aware visibility policy."""

    selected = entries or default_tool_registry_entries()
    active = policy or ToolVisibilityPolicy()
    visible: list[ToolRegistryEntry] = []
    for entry in selected:
        if not active.include_hidden and not entry.visible_to_agent:
            continue
        if active.allowed_tool_ids and entry.tool_id not in active.allowed_tool_ids:
            continue
        if entry.execution_domain == "local" and active.execution_target == "vm":
            continue
        if entry.execution_domain == "vm" and active.execution_target == "local":
            continue
        if entry.audience == "supervisor" and not active.include_supervisor_only:
            continue
        visible.append(entry)
    return tuple(visible)


def render_tools_manifest(
    entries: tuple[ToolRegistryEntry, ...] | None = None,
    *,
    policy: ToolVisibilityPolicy | None = None,
) -> RenderedToolsManifest:
    """Render the filtered tool registry as agent-visible JSON."""

    filtered = filter_tool_registry_entries(entries, policy=policy)
    payload = {
        "version": 2,
        "approved_vm_tool": "canonical-multipass",
        "execution_target": (policy.execution_target if policy is not None else "local"),
        "tool_count": len(filtered),
        "tool_order": [entry.tool_id for entry in filtered],
        "usage_rules": {
            "prefer_local_evidence_first": True,
            "announce_tool_use_before_action": True,
            "show_agent_handoffs_to_user": True,
            "use_web_only_when_task_needs_fresh_remote_information": True,
            "show_cited_sources_when_web_is_used": True,
        },
        "trace_contract": {
            "shared_duckln_trace": True,
            "include_tool_id_and_action": True,
            "show_web_source_to_user": True,
            "show_web_query_when_broad_search_is_used": True,
        },
        "cloud_guardrails": {
            "idle_shutdown_minutes_default": DEFAULT_IDLE_SHUTDOWN_MINUTES,
            "ownership_tags_required": True,
            "guardrail_lines": list(render_cloud_guardrail_lines()),
            "approved_shapes": [
                {
                    "provider": shape.provider,
                    "shape": shape.shape,
                    "label": shape.label,
                    "notes": shape.notes,
                }
                for shape in approved_cloud_shapes()
            ],
        },
        "tools": [
            {
                "id": entry.tool_id,
                "label": entry.label,
                "description": entry.description,
                "use_when": list(_tool_use_when(entry.tool_id)),
                "avoid_when": list(_tool_avoid_when(entry.tool_id)),
                "examples": list(_tool_examples(entry.tool_id)),
                "provider_type": entry.provider_type,
                "execution_domain": entry.execution_domain,
                "capability_class": entry.capability_class,
                "safety_class": entry.safety_class.value,
                "approval_required": entry.approval_required,
                "audience": entry.audience,
                "restrictions": list(entry.restrictions),
            }
            for entry in filtered
        ],
    }
    return RenderedToolsManifest(
        content=json.dumps(payload, indent=2, sort_keys=False) + "\n",
        entries=filtered,
    )


def active_tool_order(
    entries: tuple[ToolRegistryEntry, ...] | None = None,
    *,
    policy: ToolVisibilityPolicy | None = None,
) -> tuple[str, ...]:
    """Return the ordered active tool ids for the current execution target."""

    return tuple(entry.tool_id for entry in filter_tool_registry_entries(entries, policy=policy))


def validate_tools_manifest(content: str) -> tuple[str, ...]:
    """Validate the compact tools.json shape used by Duckln memory."""

    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        return (f"tools.json is not valid JSON: {exc.msg}.",)
    if not isinstance(payload, dict):
        return ("tools.json must contain a JSON object.",)
    tools = payload.get("tools")
    if not isinstance(tools, list):
        return ("tools.json must include a 'tools' array.",)
    errors: list[str] = []
    for index, item in enumerate(tools):
        if not isinstance(item, dict):
            errors.append(f"tools[{index}] must be an object.")
            continue
        for key in ("id", "label", "description", "provider_type", "execution_domain", "capability_class", "safety_class"):
            if not str(item.get(key) or "").strip():
                errors.append(f"tools[{index}] is missing '{key}'.")
    return tuple(errors)


def tool_policy_summary(*, execution_target: str = "local") -> str:
    """Return a compact prompt-friendly summary of the active tool policy."""

    manifest = render_tools_manifest(policy=ToolVisibilityPolicy(execution_target=execution_target))
    labels = ", ".join(entry.label for entry in manifest.entries[:6])
    order = ", ".join(manifest.content and json.loads(manifest.content)["tool_order"][:5])
    return (
        f"Use only tools declared in tools.json for the active {execution_target} execution target. "
        f"Current visible tools include: {labels}. "
        f"Current tool order starts with: {order}. "
        "Prefer local repo and runtime evidence first, announce what tool Duckln is using before acting, "
        "follow the shared Duckln trace contract, surface specialist or agent handoffs to the user, "
        "show web sources and bounded search queries when used, and use web access only when the task truly needs fresh remote information. "
        "Multipass is the approved Ubuntu VM path. Docker must stay user-visible in the terminal pane. "
        f"Cloud resources must stay on Duckln's approved shortlist and default to {DEFAULT_IDLE_SHUTDOWN_MINUTES}-minute idle shutdown."
    )
