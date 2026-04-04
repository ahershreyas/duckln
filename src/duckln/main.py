"""Duckln CLI entrypoint."""

from __future__ import annotations

from dataclasses import dataclass
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable

from agent.memory import resolve_agent_memory_paths
from duckln.ai_client import Provider, get_provider_adapter_for_base_url
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
    update_runtime_mode,
    update_runtime_model,
    update_runtime_provider,
)
from duckln.diagnostics import run_healthcheck
from duckln.modes import ControlMode
from duckln.repo_bringup import bring_up_selected_repo
from duckln.repos import open_repo_catalog
from duckln.ui import build_terminal_display, build_terminal_input, render_banner, render_session_header
from duckln.uninstall import run_uninstall_flow
from duckln.vm import configure_existing_multipass_vm, create_multipass_vm
from agent.probe import probe_system
from state.access import clear_memory_scope, read_config_snapshot, record_system_probe
from state.repo_catalog import refresh_local_repo_catalog


OLLAMA_SESSION_START_MAX_ATTEMPTS = 3
OLLAMA_SESSION_START_RETRY_SECONDS = 0.1


@dataclass(frozen=True)
class SlashCommandDescriptor:
    """Slash command metadata for the runtime command palette."""

    command: str
    description: str

    @property
    def choice_label(self) -> str:
        return f"{self.command} — {self.description}"


def get_slash_command_descriptors() -> tuple[SlashCommandDescriptor, ...]:
    """Return the supported runtime slash commands."""

    return (
        SlashCommandDescriptor("/help", "Show the available slash commands."),
        SlashCommandDescriptor("/mode", "Change how much Duckln can do for you."),
        SlashCommandDescriptor("/provider", "Change provider, enter a new key, and pick a model."),
        SlashCommandDescriptor("/model", "Pick a different model for the current provider."),
        SlashCommandDescriptor("/config", "Open the configuration menu."),
        SlashCommandDescriptor("/repos", "Browse the cached repo catalog and select a repository."),
        SlashCommandDescriptor("/repos refresh", "Refresh the cached repo catalog from GitHub."),
        SlashCommandDescriptor("/memory clear", "Clear Duckln memory with confirmation."),
        SlashCommandDescriptor("/vm", "Create an Ubuntu VM with Multipass."),
        SlashCommandDescriptor("/healthcheck", "Validate Python, dependencies, and provider connectivity."),
    )


def handle_session_command(
    command: str,
    current: AppConfig,
    paths: ConfigPaths,
    *,
    select: Callable[[str, tuple[str, ...]], str | None] | None = None,
    secret_prompt: Callable[[str], str | None] | None = None,
    text_prompt: Callable[[str, str], str | None] | None = None,
    approve: Callable[[str], bool] | None = None,
    display: Callable[[str], None] = print,
    client: Any | None = None,
) -> AppConfig:
    """Handle a runtime slash command without losing the last good config."""

    initialize_runtime_storage(paths)

    if command == "/":
        return open_command_palette(
            current,
            paths,
            select=select,
            secret_prompt=secret_prompt,
            text_prompt=text_prompt,
            display=display,
            client=client,
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
    if command == "/repos":
        try:
            selected_repo = open_repo_catalog(paths, select=select)
        except ValueError as exc:
            display(f"Retryable error: {exc}")
            return current

        if selected_repo is None:
            display("No repository selected.")
            return current

        display(f"Selected repository: {selected_repo.name} ({selected_repo.repo_url})")
        display("Reading repo files and preparing a bounded setup path...")
        bring_up_selected_repo(
            selected_repo,
            current.mode,
            paths,
            approve=approve,
            display=display,
            runtime_provider=current.provider.value,
            execution_target=_session_execution_target(paths.config_dir),
        )
        return current
    if command == "/vm":
        try:
            create_result = create_multipass_vm(
                paths,
                text_prompt=text_prompt,
                display=display,
            )
            if create_result is not None and create_result.ok:
                configure_existing_multipass_vm(
                    create_result.vm_name,
                    paths,
                    select=select,
                    display=display,
                )
        except ValueError as exc:
            display(f"Retryable error: {exc}")
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

    display(f"Retryable error: Unknown slash command {command}.")
    return current


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
) -> AppConfig:
    """Open the runtime slash command palette."""

    if select is None:
        from duckln.config import _default_select_prompt

        select_prompt = _default_select_prompt
    else:
        select_prompt = select

    choices = tuple(descriptor.choice_label for descriptor in get_slash_command_descriptors()) + ("Back", "Exit")
    selected = select_prompt("Duckln command palette:", choices)
    if selected is None or selected == "Back":
        display("No slash command selected.")
        return current
    if selected == "Exit":
        raise SessionExitRequested("Exit requested from command palette.")

    for descriptor in get_slash_command_descriptors():
        if selected == descriptor.choice_label:
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
            )

    display(f"Retryable error: Unknown palette selection {selected}.")
    return current


def _display_help(*, display: Callable[[str], None]) -> None:
    for descriptor in get_slash_command_descriptors():
        display(descriptor.choice_label)


def main(
    *,
    argv: list[str] | None = None,
    input_func: Callable[[str], str] | None = None,
    display: Callable[[str], None] | None = None,
    client: Any | None = None,
) -> int:
    args = [] if argv is None else argv
    input_prompt = input_func or build_terminal_input()
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
    record_system_probe(paths.config_dir, probe_system())
    current = _ensure_active_config(paths, display=display_output, client=client)
    if current is None:
        return 1

    display_output(
        render_session_header(
            provider=current.provider.label,
            model=current.model,
            mode=current.mode.label,
            user_name=current.user_name,
            memory_state=_session_memory_state(paths.config_dir),
            width=shutil.get_terminal_size((80, 20)).columns,
        )
    )
    display_output("Duckln is ready. Type /help to explore commands.")
    while True:
        try:
            raw_input = input_prompt("duckln> ")
        except EOFError:
            display_output("Exiting Duckln.")
            return 0
        except KeyboardInterrupt:
            display_output("Exiting Duckln.")
            return 0

        command = raw_input.strip()
        if not command:
            continue
        if command.lower() in {"exit", "quit", "/exit"}:
            display_output("Exiting Duckln.")
            return 0
        if not command.startswith("/"):
            display_output("Use / for commands. Type exit to quit.")
            continue

        try:
            current = handle_session_command(
                command,
                current,
                paths,
                display=display_output,
                client=client,
            )
        except SessionExitRequested:
            display_output("Exiting Duckln.")
            return 0

    return 0


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


if __name__ == "__main__":
    raise SystemExit(main(argv=sys.argv[1:]))
