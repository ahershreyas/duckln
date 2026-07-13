"""Filesystem-shaped subagent contract helpers for Duckln specialists."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


PLAYBOOK_DIR = Path(__file__).resolve().parents[1] / "agent" / "playbooks"


@dataclass(frozen=True)
class SubagentContractRecord:
    """Visible contract for one Duckln specialist agent."""

    slug: str
    title: str
    relative_path: str
    content: str


@dataclass(frozen=True)
class SubagentRuntimeDescriptor:
    """Runtime-facing descriptor for one specialist agent."""

    slug: str
    title: str
    specialist_name: str
    route_scope: str
    execution_targets: tuple[str, ...]
    playbook_name: str
    repo_families: tuple[str, ...] = ()
    signal_files: tuple[str, ...] = ()
    allowed_tool_ids: tuple[str, ...] = ()


def default_subagent_contracts() -> tuple[SubagentContractRecord, ...]:
    """Build the default filesystem-visible specialist contracts.

    Each specialist gets three files: AGENTS.md (rules + signal files + allowed tools),
    SOUL.md (tone and disposition), TOOLS.md (full tool list scoped to the specialist).
    The contract content is generated from the runtime descriptors so the supervisor
    can read `memory/subagents/<slug>/AGENTS.md` and see what each specialist looks for
    without reading source code."""

    definitions = (
        ("python_setup", "Python setup specialist", "python.md", "Use repo-local Python and venv-first setup."),
        ("node_typescript", "Node / TypeScript specialist", "node_typescript.md", "Handle JS package managers and service startup carefully."),
        ("cpp_native", "C++ / native specialist", "cpp_native.md", "Handle compiler and native dependency setup cautiously."),
        ("go_native", "Go specialist", "go_native.md", "Handle Go module resolution and bounded build or run verification."),
        ("rust", "Rust specialist", "rust.md", "Handle cargo-based crate setup, fetch, build, and bounded run verification."),
        ("audio", "Audio specialist", "audio.md", "Handle ffmpeg, speech, and audio runtime dependencies."),
        ("diffusion", "Diffusion specialist", "diffusion.md", "Handle heavier image/model workflows and bounded hardware checks."),
        ("vm_environment", "VM / environment specialist", "vm.md", "Prefer Canonical Multipass for Ubuntu VM provisioning and configuration."),
        ("provider_routing", "Provider routing specialist", "provider.md", "Handle provider and runtime-specific routing concerns."),
        ("debug_recovery", "Debug / Recovery specialist", "debug_recovery.md", "Classify failed setup attempts and return bounded recovery actions."),
    )
    descriptors_by_slug = {desc.slug: desc for desc in default_subagent_runtime_descriptors()}
    contracts: list[SubagentContractRecord] = []
    for slug, title, playbook_name, one_line_scope in definitions:
        descriptor = descriptors_by_slug.get(slug)
        contracts.extend(
            (
                SubagentContractRecord(
                    slug=slug,
                    title=f"{title} Rules",
                    relative_path=f"subagents/{slug}/AGENTS.md",
                    content=_render_subagent_contract(
                        title=title,
                        one_line_scope=one_line_scope,
                        playbook_name=playbook_name,
                        descriptor=descriptor,
                    ),
                ),
                SubagentContractRecord(
                    slug=slug,
                    title=f"{title} Soul",
                    relative_path=f"subagents/{slug}/SOUL.md",
                    content=_render_subagent_soul(title=title),
                ),
                SubagentContractRecord(
                    slug=slug,
                    title=f"{title} Tools",
                    relative_path=f"subagents/{slug}/TOOLS.md",
                    content=_render_subagent_tools(title=title, descriptor=descriptor),
                ),
            )
        )
    return tuple(contracts)


def default_subagent_runtime_descriptors() -> tuple[SubagentRuntimeDescriptor, ...]:
    """Return the runtime descriptors that mirror the filesystem contracts."""

    return (
        SubagentRuntimeDescriptor(
            "python_setup",
            "Python setup specialist",
            "python",
            "python-first repo setup",
            ("local", "vm"),
            "python.md",
            repo_families=("python",),
            allowed_tool_ids=(
                "shell.command_runner",
                "filesystem.read",
                "filesystem.search",
                "filesystem.write_patch",
                "process.session_runtime",
                "runtime.terminal_pane",
                "repo.file_inspection",
                "repo.knowledge_resolver",
                "state.sqlite_runtime",
                "session.history",
                "web.documentation_lookup",
                "verification.repo_checks",
                "system.local_probe",
            ),
        ),
        SubagentRuntimeDescriptor(
            "node_typescript",
            "Node / TypeScript specialist",
            "node-typescript",
            "node and service setup",
            ("local", "vm"),
            "node_typescript.md",
            repo_families=("node_typescript",),
            signal_files=("package.json", "pnpm-lock.yaml", "yarn.lock", "package-lock.json"),
            allowed_tool_ids=(
                "shell.command_runner",
                "filesystem.read",
                "filesystem.search",
                "filesystem.write_patch",
                "process.session_runtime",
                "runtime.terminal_pane",
                "repo.file_inspection",
                "repo.knowledge_resolver",
                "state.sqlite_runtime",
                "session.history",
                "web.documentation_lookup",
                "verification.repo_checks",
                "system.local_probe",
            ),
        ),
        SubagentRuntimeDescriptor(
            "cpp_native",
            "C++ / native specialist",
            "cpp-native",
            "native and compiler-heavy setup",
            ("local", "vm"),
            "cpp_native.md",
            repo_families=("cpp_native",),
            signal_files=("CMakeLists.txt", "Cargo.toml", "configure"),
            allowed_tool_ids=(
                "shell.command_runner",
                "filesystem.read",
                "filesystem.search",
                "filesystem.write_patch",
                "process.session_runtime",
                "runtime.terminal_pane",
                "repo.file_inspection",
                "repo.knowledge_resolver",
                "state.sqlite_runtime",
                "session.history",
                "web.documentation_lookup",
                "verification.repo_checks",
                "system.local_probe",
            ),
        ),
        SubagentRuntimeDescriptor(
            "go_native",
            "Go specialist",
            "go-native",
            "go module and binary setup",
            ("local", "vm"),
            "go_native.md",
            repo_families=("go_native",),
            signal_files=("go.mod", "main.go"),
            allowed_tool_ids=(
                "shell.command_runner",
                "filesystem.read",
                "filesystem.search",
                "filesystem.write_patch",
                "process.session_runtime",
                "runtime.terminal_pane",
                "repo.file_inspection",
                "repo.knowledge_resolver",
                "state.sqlite_runtime",
                "session.history",
                "web.documentation_lookup",
                "verification.repo_checks",
                "system.local_probe",
            ),
        ),
        SubagentRuntimeDescriptor(
            "rust",
            "Rust specialist",
            "rust",
            "cargo-based crate setup, build, and run verification",
            ("local", "vm"),
            "rust.md",
            repo_families=("rust",),
            signal_files=("Cargo.toml", "rust-toolchain.toml", "rust-toolchain"),
            allowed_tool_ids=(
                "shell.command_runner",
                "filesystem.read",
                "filesystem.search",
                "filesystem.write_patch",
                "process.session_runtime",
                "runtime.terminal_pane",
                "repo.file_inspection",
                "repo.knowledge_resolver",
                "state.sqlite_runtime",
                "session.history",
                "web.documentation_lookup",
                "verification.repo_checks",
                "system.local_probe",
            ),
        ),
        SubagentRuntimeDescriptor(
            "audio",
            "Audio specialist",
            "audio",
            "speech and audio runtimes",
            ("local", "vm"),
            "audio.md",
            repo_families=("audio",),
            allowed_tool_ids=(
                "shell.command_runner",
                "filesystem.read",
                "filesystem.search",
                "filesystem.write_patch",
                "process.session_runtime",
                "runtime.terminal_pane",
                "repo.file_inspection",
                "repo.knowledge_resolver",
                "state.sqlite_runtime",
                "session.history",
                "web.documentation_lookup",
                "verification.repo_checks",
                "system.local_probe",
            ),
        ),
        SubagentRuntimeDescriptor(
            "diffusion",
            "Diffusion specialist",
            "diffusion-heavy",
            "heavier model/image workflows",
            ("local",),
            "diffusion.md",
            repo_families=("diffusion_heavy",),
            allowed_tool_ids=(
                "shell.command_runner",
                "filesystem.read",
                "filesystem.search",
                "filesystem.write_patch",
                "process.session_runtime",
                "runtime.terminal_pane",
                "repo.file_inspection",
                "repo.knowledge_resolver",
                "state.sqlite_runtime",
                "session.history",
                "web.documentation_lookup",
                "verification.repo_checks",
                "system.local_probe",
            ),
        ),
        SubagentRuntimeDescriptor(
            "vm_environment",
            "VM / environment specialist",
            "vm-environment",
            "ubuntu vm provisioning and isolation",
            ("vm",),
            "vm.md",
            repo_families=("vm_environment",),
            allowed_tool_ids=(
                "filesystem.read",
                "filesystem.search",
                "process.session_runtime",
                "runtime.terminal_pane",
                "repo.file_inspection",
                "repo.knowledge_resolver",
                "state.sqlite_runtime",
                "session.history",
                "web.documentation_lookup",
                "verification.repo_checks",
            ),
        ),
        SubagentRuntimeDescriptor(
            "provider_routing",
            "Provider routing specialist",
            "provider-routing",
            "provider/runtime specific setup",
            ("local", "vm"),
            "provider.md",
            repo_families=("provider_routing",),
            allowed_tool_ids=(
                "filesystem.read",
                "filesystem.search",
                "repo.file_inspection",
                "repo.knowledge_resolver",
                "catalog.repo_access",
                "state.sqlite_runtime",
                "session.history",
                "web.documentation_lookup",
                "verification.repo_checks",
                "system.local_probe",
            ),
        ),
        SubagentRuntimeDescriptor(
            "debug_recovery",
            "Debug / Recovery specialist",
            "debug-recovery",
            "failure classification and bounded recovery",
            ("local", "vm"),
            "debug_recovery.md",
            allowed_tool_ids=(
                "shell.command_runner",
                "filesystem.read",
                "filesystem.search",
                "filesystem.write_patch",
                "process.session_runtime",
                "runtime.terminal_pane",
                "repo.file_inspection",
                "repo.knowledge_resolver",
                "state.sqlite_runtime",
                "session.history",
                "web.documentation_lookup",
                "verification.repo_checks",
                "system.local_probe",
            ),
        ),
        # Plan 156 P3: the GENERIC engineer — handles ANY stack with no family specialist
        # (Java/Maven/Gradle, Scala, Elixir, an odd native build) by READING its real build
        # files and REASONING the setup. Broad tools; the LLM reasoning pass carries the
        # specifics. This is what makes "any repo" true beyond the named families.
        SubagentRuntimeDescriptor(
            "generalist",
            "Generalist setup engineer",
            "generalist",
            "any-stack setup by reading build files + reasoning (no family specialist)",
            ("local", "vm"),
            "generic.md",
            allowed_tool_ids=(
                "shell.command_runner",
                "filesystem.read",
                "filesystem.search",
                "filesystem.write_patch",
                "process.session_runtime",
                "runtime.terminal_pane",
                "repo.file_inspection",
                "repo.knowledge_resolver",
                "state.sqlite_runtime",
                "session.history",
                "web.documentation_lookup",
                "verification.repo_checks",
                "system.local_probe",
            ),
        ),
    )


def validate_subagent_contract(content: str) -> tuple[str, ...]:
    """Validate the minimal shape of a subagent contract file."""

    required_sections = (
        "## Scope",
        "## Allowed Tools",
        "## Execution Targets",
        "## Planning",
        "## Safety",
        "## Escalation",
        "## Verification",
    )
    errors = [f"Missing section {section}." for section in required_sections if section not in content]
    if not content.strip().startswith("# "):
        errors.append("Subagent contract must start with a title heading.")
    return tuple(errors)


def _render_subagent_contract(
    *,
    title: str,
    one_line_scope: str,
    playbook_name: str,
    descriptor: SubagentRuntimeDescriptor | None = None,
) -> str:
    playbook_excerpt = _playbook_excerpt(playbook_name)
    signal_section = _render_signal_files_section(descriptor)
    repo_family_section = _render_repo_families_section(descriptor)
    execution_targets_section = _render_execution_targets_section(descriptor)
    allowed_tools_section = _render_allowed_tools_section(descriptor)
    return (
        f"# {title}\n\n"
        "## Scope\n"
        f"{one_line_scope}\n\n"
        f"{repo_family_section}"
        f"{signal_section}"
        "## Allowed Tools\n"
        f"{allowed_tools_section}"
        "- Use only tools declared in Duckln tools.json.\n"
        "- Read repo files before acting.\n"
        "- Announce the tool family Duckln is about to use when it matters to the user.\n"
        "- Use web lookup only when local evidence is not enough and the task truly needs fresh remote information.\n"
        "- Use Multipass only when Ubuntu VM isolation is the chosen path.\n\n"
        "## Execution Targets\n"
        f"{execution_targets_section}"
        "- Respect the active execution target.\n"
        "- Stay in VM once the repo thread is running there unless the user changes it.\n"
        "- Do not assume host GPU/MPS acceleration exists inside the VM.\n\n"
        "## Planning\n"
        "- Update or rely on Duckln's current TODO.md before making repo changes.\n"
        "- Keep changes bounded to the active repo task.\n\n"
        "## Safety\n"
        "- Read before acting.\n"
        "- Plan before changing.\n"
        "- Do not deploy or mutate broadly without explicit authorization.\n"
        "- Never invent unsupported provisioning methods.\n\n"
        "## Escalation\n"
        "- Escalate unclear, risky, or out-of-domain work back to the supervisor.\n"
        "- Prefer clarity over verbosity when handing control back.\n\n"
        "## User Visibility\n"
        "- Tell the user what Duckln is doing before repo-changing or runtime actions.\n"
        "- Surface specialist handoffs or agent-to-agent coordination in plain language.\n\n"
        "## Verification\n"
        "- End with a bounded verification step.\n"
        "- Never claim success without evidence.\n\n"
        "## Playbook Reference\n"
        f"Reference playbook: `{playbook_name}`.\n\n"
        f"{playbook_excerpt}"
    )


def _render_signal_files_section(descriptor: SubagentRuntimeDescriptor | None) -> str:
    """If the specialist watches specific repo signals, surface them so the supervisor
    can route deterministically without re-reading source."""
    if descriptor is None or not descriptor.signal_files:
        return ""
    bullets = "\n".join(f"- {fname}" for fname in descriptor.signal_files)
    return f"## Signal Files\n{bullets}\n\n"


def _render_repo_families_section(descriptor: SubagentRuntimeDescriptor | None) -> str:
    if descriptor is None or not descriptor.repo_families:
        return ""
    bullets = "\n".join(f"- {family}" for family in descriptor.repo_families)
    return f"## Repo Families\n{bullets}\n\n"


def _render_execution_targets_section(descriptor: SubagentRuntimeDescriptor | None) -> str:
    if descriptor is None or not descriptor.execution_targets:
        return ""
    targets = ", ".join(descriptor.execution_targets)
    return f"- Active execution target scope: {targets}.\n"


def _render_allowed_tools_section(descriptor: SubagentRuntimeDescriptor | None) -> str:
    if descriptor is None or not descriptor.allowed_tool_ids:
        return ""
    bullets = "\n".join(f"- `{tool_id}`" for tool_id in descriptor.allowed_tool_ids)
    return f"{bullets}\n"


def _playbook_excerpt(playbook_name: str) -> str:
    playbook_path = PLAYBOOK_DIR / playbook_name
    if not playbook_path.exists():
        return "Playbook excerpt unavailable."
    text = playbook_path.read_text(encoding="utf-8").strip()
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines[:12])


def registered_skill_hints(config_dir, *, task_text: str = "", limit: int = 3) -> tuple[str, ...]:
    """Plan 178 F4: load the user's PULLED/learned SKILLS so the agent actually CONSULTS them.

    Reads the skill-memory records (`memory_kind` 'skill' or 'registered_skill') and returns
    the task-matched ones as hint strings the caller injects into the agent's context. Matched
    by keyword overlap with `task_text`; when nothing matches, still surfaces the top few so a
    freshly-pulled skill is used at least once. Best-effort — a missing store is a no-op."""
    import re as _re

    try:
        from pathlib import Path as _P
        from state.store import initialize_state_store

        records = initialize_state_store(_P(config_dir)).list_managed_memory_records()
    except Exception:
        return ()
    task_words = {w for w in _re.findall(r"[a-z0-9]+", (task_text or "").lower()) if len(w) > 3}
    scored: list[tuple[int, str]] = []
    for rec in records:
        if getattr(rec, "memory_kind", "") not in ("skill", "registered_skill"):
            continue
        title = str(getattr(rec, "title", "") or "").strip()
        content = str(getattr(rec, "content", "") or "").strip()
        if not (title or content):
            continue
        blob = f"{title} {content}".lower()
        score = sum(1 for w in task_words if w in blob) if task_words else 0
        scored.append((score, f"User skill — {title or 'untitled'}:\n{content[:800]}"))
    scored.sort(key=lambda item: item[0], reverse=True)
    return tuple(hint for _score, hint in scored[: max(0, limit)])


def _render_subagent_soul(*, title: str) -> str:
    return (
        f"# {title} Soul\n\n"
        "- Sound calm, practical, and specific.\n"
        "- Stay concise and repo-grounded.\n"
        "- Prefer the next useful diagnostic or action over long explanation.\n"
        "- Hand work back cleanly when the supervisor should decide.\n"
    )


def _render_subagent_tools(*, title: str, descriptor: SubagentRuntimeDescriptor | None = None) -> str:
    tool_list_section = ""
    if descriptor is not None and descriptor.allowed_tool_ids:
        bullets = "\n".join(f"- `{tool_id}`" for tool_id in descriptor.allowed_tool_ids)
        tool_list_section = f"## Allowed tool ids\n{bullets}\n\n"
    return (
        f"# {title} Tools\n\n"
        f"{tool_list_section}"
        "## Rules\n"
        "- Use only tools allowed by Duckln tools.json and the active execution target.\n"
        "- Read the repo before making changes.\n"
        "- Prefer filesystem read/search before web lookup.\n"
        "- Use web lookup only for genuinely fresh remote information.\n"
        "- Tell the user when Duckln is using shell, process, web, or specialist handoff capabilities.\n"
        "- Use Multipass only for the Ubuntu VM path.\n"
        "- Do not invent alternative provisioning or hidden tool behavior.\n"
    )
