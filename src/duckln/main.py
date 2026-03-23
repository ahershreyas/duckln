"""Duckln CLI entrypoint."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from duckln.config import (
    AppConfig,
    ConfigPaths,
    initialize_runtime_storage,
    open_runtime_config_menu,
    update_runtime_mode,
    update_runtime_model,
    update_runtime_provider,
)
from duckln.diagnostics import run_healthcheck
from duckln.repos import open_repo_catalog
from state.access import record_system_probe


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
        SlashCommandDescriptor("/mode", "Change how much Duckln can do for you."),
        SlashCommandDescriptor("/provider", "Change provider, enter a new key, and pick a model."),
        SlashCommandDescriptor("/model", "Pick a different model for the current provider."),
        SlashCommandDescriptor("/config", "Open the configuration menu."),
        SlashCommandDescriptor("/repos", "Browse the cached repo catalog and select a repository."),
        SlashCommandDescriptor("/healthcheck", "Validate Python, dependencies, and provider connectivity."),
    )


def handle_session_command(
    command: str,
    current: AppConfig,
    paths: ConfigPaths,
    *,
    select: Callable[[str, tuple[str, ...]], str | None] | None = None,
    secret_prompt: Callable[[str], str] | None = None,
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
            display=display,
            client=client,
        )
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


def open_command_palette(
    current: AppConfig,
    paths: ConfigPaths,
    *,
    select: Callable[[str, tuple[str, ...]], str | None] | None = None,
    secret_prompt: Callable[[str], str] | None = None,
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
                display=display,
                client=client,
            )

    display(f"Retryable error: Unknown palette selection {selected}.")
    return current


def main() -> int:
    from duckln.config import resolve_config_paths

    paths = resolve_config_paths()
    initialize_runtime_storage(paths)
    record_system_probe(paths.config_dir, probe_system())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
