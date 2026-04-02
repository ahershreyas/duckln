"""Duckln CLI entrypoint."""

from __future__ import annotations

from dataclasses import dataclass
import shutil
from typing import Any, Callable

from duckln.config import (
    _resolve_agent_contract_source,
    AppConfig,
    ConfigPaths,
    OnboardingError,
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
from duckln.repo_bringup import bring_up_selected_repo
from duckln.repos import open_repo_catalog
from duckln.ui import render_banner
from duckln.vm import configure_existing_multipass_vm, create_multipass_vm
from agent.probe import probe_system
from state.access import clear_memory_scope, record_system_probe
from state.repo_catalog import refresh_local_repo_catalog


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
    secret_prompt: Callable[[str], str] | None = None,
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
            display=display,
            client=client,
        )
    if command == "/model":
        return update_runtime_model(
            current,
            paths,
            select=select,
            display=display,
            client=client,
        )
    if command == "/config":
        return open_runtime_config_menu(
            current,
            paths,
            select=select,
            secret_prompt=secret_prompt,
            display=display,
            client=client,
        )
    if command == "/repos refresh":
        try:
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
        bring_up_selected_repo(
            selected_repo,
            current.mode,
            paths,
            approve=approve,
            display=display,
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
            _handle_memory_clear_command(paths, select=select, display=display)
        except ValueError as exc:
            display(f"Retryable error: {exc}")
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
) -> None:
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
        return
    if selected_scope == "Cancel and return":
        return

    scope_key, summary = scope_descriptions[selected_scope]
    display(summary)
    confirmed = select_prompt("Are you sure?", ("Yes", "No"))
    if confirmed != "Yes":
        display("No memory was cleared.")
        return

    result = clear_memory_scope(
        paths.config_dir,
        scope=scope_key,
        contract_source=_resolve_agent_contract_source(),
    )
    display(result.summary)


def open_command_palette(
    current: AppConfig,
    paths: ConfigPaths,
    *,
    select: Callable[[str, tuple[str, ...]], str | None] | None = None,
    secret_prompt: Callable[[str], str] | None = None,
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

    choices = tuple(descriptor.choice_label for descriptor in get_slash_command_descriptors())
    selected = select_prompt("Duckln command palette:", choices)
    if selected is None:
        display("No slash command selected.")
        return current

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
    input_func: Callable[[str], str] = input,
    display: Callable[[str], None] = print,
    client: Any | None = None,
) -> int:
    paths = resolve_config_paths()
    initialize_runtime_storage(paths)
    record_system_probe(paths.config_dir, probe_system())
    current = _ensure_active_config(paths, display=display, client=client)
    if current is None:
        return 1

    display("Duckln is ready. Type /help to explore commands.")
    while True:
        try:
            raw_input = input_func("duckln> ")
        except EOFError:
            display("Exiting Duckln.")
            return 0
        except KeyboardInterrupt:
            display("Exiting Duckln.")
            return 0

        command = raw_input.strip()
        if not command:
            continue
        if command.lower() in {"exit", "quit"}:
            display("Exiting Duckln.")
            return 0
        if not command.startswith("/"):
            display("Use / for commands. Type exit to quit.")
            continue

        current = handle_session_command(
            command,
            current,
            paths,
            display=display,
            client=client,
        )

    return 0


def _ensure_active_config(
    paths: ConfigPaths,
    *,
    display: Callable[[str], None],
    client: Any | None = None,
) -> AppConfig | None:
    current = load_app_config(paths)
    if current is not None:
        return current

    try:
        onboarding = run_onboarding(
            paths,
            display=display,
            render_banner=lambda: render_banner(width=shutil.get_terminal_size((80, 20)).columns),
            client=client,
        )
    except OnboardingError as exc:
        display(f"Retryable error: {exc}")
        return None
    return onboarding.config


if __name__ == "__main__":
    raise SystemExit(main())
