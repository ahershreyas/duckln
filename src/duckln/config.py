"""Configuration loading, onboarding, and persistence helpers."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Callable

from duckln.ai_client import Provider, ProviderAdapter, ProviderModel, get_provider_adapter
from duckln.modes import ControlMode, get_mode_descriptors, parse_mode
from state.access import initialize_managed_memory_state, write_config_snapshot
from state.repo_catalog import initialize_local_repo_catalog_cache
from state.store import initialize_state_store


APP_DIR_NAME = "duckln"
CONFIG_FILE_NAME = "config.json"
ENV_CONFIG_DIR = "DUCKLN_CONFIG_DIR"
ENV_CONFIG_FILE = "DUCKLN_CONFIG_FILE"
ENV_HOME = "HOME"
ENV_XDG_CONFIG_HOME = "XDG_CONFIG_HOME"


@dataclass(frozen=True)
class ConfigPaths:
    """Resolved filesystem locations for Duckln configuration."""

    config_dir: Path
    config_file: Path


@dataclass(frozen=True)
class AppConfig:
    """Shared persisted Duckln configuration."""

    provider: Provider
    model: str
    api_key: str
    mode: ControlMode


@dataclass(frozen=True)
class OnboardingResult:
    """Successful onboarding output."""

    config: AppConfig
    saved_to: Path


class OnboardingError(ValueError):
    """Raised when onboarding cannot complete safely."""


def get_env_defaults(environ: dict[str, str] | None = None) -> dict[str, str]:
    """Return the path-related environment defaults Duckln respects."""

    env = os.environ if environ is None else environ
    return {
        ENV_CONFIG_DIR: env.get(ENV_CONFIG_DIR, ""),
        ENV_CONFIG_FILE: env.get(ENV_CONFIG_FILE, ""),
        ENV_XDG_CONFIG_HOME: env.get(ENV_XDG_CONFIG_HOME, ""),
        ENV_HOME: env.get(ENV_HOME, ""),
    }


def resolve_config_paths(environ: dict[str, str] | None = None) -> ConfigPaths:
    """Resolve config paths from explicit overrides, XDG, or the home directory."""

    env = os.environ if environ is None else environ

    explicit_file = env.get(ENV_CONFIG_FILE)
    if explicit_file:
        config_file = Path(explicit_file).expanduser()
        return ConfigPaths(config_dir=config_file.parent, config_file=config_file)

    explicit_dir = env.get(ENV_CONFIG_DIR)
    if explicit_dir:
        config_dir = Path(explicit_dir).expanduser()
        return ConfigPaths(config_dir=config_dir, config_file=config_dir / CONFIG_FILE_NAME)

    xdg_config_home = env.get(ENV_XDG_CONFIG_HOME)
    if xdg_config_home:
        config_dir = Path(xdg_config_home).expanduser() / APP_DIR_NAME
        return ConfigPaths(config_dir=config_dir, config_file=config_dir / CONFIG_FILE_NAME)

    home = env.get(ENV_HOME)
    if not home:
        raise ValueError("Duckln requires HOME or an explicit config path override.")

    config_dir = Path(home).expanduser() / f".{APP_DIR_NAME}"
    return ConfigPaths(config_dir=config_dir, config_file=config_dir / CONFIG_FILE_NAME)


def ensure_config_dir(paths: ConfigPaths) -> Path:
    """Create the resolved config directory if it does not already exist."""

    paths.config_dir.mkdir(parents=True, exist_ok=True)
    return paths.config_dir


def initialize_runtime_storage(paths: ConfigPaths) -> None:
    """Create the minimal Duckln runtime storage foundation."""

    ensure_config_dir(paths)
    initialize_state_store(paths.config_dir)
    initialize_managed_memory_state(paths.config_dir, contract_source=_resolve_agent_contract_source())
    initialize_local_repo_catalog_cache(paths.config_dir)


def load_raw_config(paths: ConfigPaths) -> dict[str, Any]:
    """Load a raw JSON config payload if present."""

    initialize_runtime_storage(paths)
    if not paths.config_file.exists():
        return {}
    return json.loads(paths.config_file.read_text(encoding="utf-8"))


def save_raw_config(payload: dict[str, Any], paths: ConfigPaths) -> Path:
    """Persist a raw JSON config payload to the resolved config path."""

    initialize_runtime_storage(paths)
    paths.config_file.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return paths.config_file


def serialize_app_config(config: AppConfig) -> dict[str, Any]:
    """Convert app config into a JSON-safe payload."""

    return {
        "provider": config.provider.value,
        "model": config.model,
        "api_key": config.api_key,
        "mode": config.mode.value,
    }


def deserialize_app_config(payload: dict[str, Any]) -> AppConfig:
    """Create typed app config from a persisted payload."""

    required_keys = {"provider", "model", "api_key", "mode"}
    missing = required_keys.difference(payload)
    if missing:
        raise ValueError(f"Missing config fields: {', '.join(sorted(missing))}")

    return AppConfig(
        provider=Provider(payload["provider"]),
        model=str(payload["model"]),
        api_key=str(payload["api_key"]),
        mode=parse_mode(str(payload["mode"])),
    )


def load_app_config(paths: ConfigPaths) -> AppConfig | None:
    """Load typed app config if it exists."""

    payload = load_raw_config(paths)
    if not payload:
        return None
    return deserialize_app_config(payload)


def save_app_config(config: AppConfig, paths: ConfigPaths) -> Path:
    """Persist typed app config."""

    saved_to = save_raw_config(serialize_app_config(config), paths)
    write_config_snapshot(
        paths.config_dir,
        {
            "provider": config.provider.value,
            "model": config.model,
            "mode": config.mode.value,
        },
    )
    return saved_to


def _resolve_agent_contract_source() -> Path | None:
    repo_root = Path(__file__).resolve().parents[2]
    for candidate_name in ("AGENTS.md", "Agent.md"):
        candidate = repo_root / candidate_name
        if candidate.exists():
            return candidate
    return None


def available_provider_choices() -> tuple[str, ...]:
    """Return onboarding provider labels in display order."""

    return tuple(provider.label for provider in Provider)


def available_mode_choices() -> tuple[str, ...]:
    """Return onboarding mode labels in display order."""

    return tuple(descriptor.choice_label for descriptor in get_mode_descriptors())


def run_onboarding(
    paths: ConfigPaths,
    *,
    select: Callable[[str, tuple[str, ...]], str | None] | None = None,
    secret_prompt: Callable[[str], str] | None = None,
    display: Callable[[str], None],
    render_banner: Callable[[], str],
    client: Any | None = None,
) -> OnboardingResult:
    """Run first-launch onboarding and persist config only after validation succeeds."""

    select_prompt = select or _default_select_prompt
    secret_input = secret_prompt or _default_secret_prompt

    display(render_banner())

    provider = _select_provider(select_prompt)
    adapter = get_provider_adapter(provider)
    api_key, models = _collect_validated_api_key(
        adapter=adapter,
        select=select_prompt,
        secret_prompt=secret_input,
        display=display,
        client=client,
    )

    model = _select_model(select_prompt, provider, _sorted_models(models))
    model_validation = adapter.validate_model(
        api_key,
        model,
        client=client,
        models=models,
    )
    if not model_validation.ok:
        raise OnboardingError(f"Retryable error: {model_validation.message}")

    mode = _select_mode(select_prompt)
    if mode is ControlMode.HOOTLWO:
        display("Warning: HOOTLWO only auto-runs safe whitelisted commands. Use a sandbox or VM.")

    config = AppConfig(
        provider=provider,
        model=model,
        api_key=api_key,
        mode=mode,
    )
    saved_to = save_app_config(config, paths)
    display(f"Duckln is configured and ready to use with {provider.label}.")
    return OnboardingResult(config=config, saved_to=saved_to)


def _select_provider(
    select: Callable[[str, tuple[str, ...]], str | None],
) -> Provider:
    choice = select("Select your AI provider:", available_provider_choices())
    if choice is None:
        raise OnboardingError("Provider selection was cancelled.")
    choice = choice.strip()
    for provider in Provider:
        if choice.lower() in {provider.value, provider.label.lower()}:
            return provider
    raise OnboardingError(f"Unsupported provider selection: {choice}")


def _select_model(
    select: Callable[[str, tuple[str, ...]], str | None],
    provider: Provider,
    models: tuple[ProviderModel, ...],
) -> str:
    if not models:
        raise OnboardingError(f"{provider.label} returned no models to choose from.")

    choices = tuple(model.id for model in models)
    choice = select("Select a model:", choices)
    if choice is None:
        raise OnboardingError("Model selection was cancelled.")
    choice = choice.strip()
    if choice not in choices:
        raise OnboardingError(f"Unsupported model selection: {choice}")
    return choice


def _select_mode(
    select: Callable[[str, tuple[str, ...]], str | None],
) -> ControlMode:
    choice = select("Select your control mode:", available_mode_choices())
    if choice is None:
        raise OnboardingError("Mode selection was cancelled.")
    choice = choice.strip()
    for descriptor in get_mode_descriptors():
        if choice == descriptor.choice_label or choice.upper() == descriptor.title:
            return descriptor.mode
    raise OnboardingError(f"Unsupported mode selection: {choice}")


def _collect_validated_api_key(
    *,
    adapter: ProviderAdapter,
    select: Callable[[str, tuple[str, ...]], str | None],
    secret_prompt: Callable[[str], str],
    display: Callable[[str], None],
    client: Any | None,
) -> tuple[str, tuple[ProviderModel, ...]]:
    while True:
        api_key = secret_prompt(f"Enter your {adapter.provider.api_key_name}: ").strip()
        display(f"Entered API key: {api_key}")

        confirmation = select(
            "Use this API key?",
            ("Use this key", "Re-enter API key"),
        )
        if confirmation is None:
            raise OnboardingError("API key confirmation was cancelled.")
        confirmation = confirmation.strip()
        if confirmation == "Re-enter API key":
            continue

        validation = adapter.validate_api_key(api_key, client=client)
        if validation.ok:
            return api_key, validation.models

        display(f"Retryable error: {validation.message}")
        retry_choice = select(
            "Validation failed. What would you like to do?",
            ("Retry API key", "Cancel onboarding"),
        )
        if retry_choice is None:
            raise OnboardingError(f"Provider validation failed: {validation.message}")
        retry_choice = retry_choice.strip()
        if retry_choice != "Retry API key":
            raise OnboardingError(f"Provider validation failed: {validation.message}")


def _sorted_models(models: tuple[ProviderModel, ...]) -> tuple[ProviderModel, ...]:
    return tuple(sorted(models, key=lambda model: (model.display_name.lower(), model.id.lower())))


def update_runtime_mode(
    current: AppConfig,
    paths: ConfigPaths,
    *,
    select: Callable[[str, tuple[str, ...]], str | None] | None = None,
    display: Callable[[str], None],
) -> AppConfig:
    """Update the active mode safely and persist it."""

    select_prompt = select or _default_select_prompt

    try:
        mode = _select_mode(select_prompt)
    except OnboardingError as exc:
        display(f"Retryable error: {exc}")
        return current

    updated = AppConfig(
        provider=current.provider,
        model=current.model,
        api_key=current.api_key,
        mode=mode,
    )
    save_app_config(updated, paths)
    display(f"Updated mode to {mode.label}.")
    return updated


def update_runtime_provider(
    current: AppConfig,
    paths: ConfigPaths,
    *,
    select: Callable[[str, tuple[str, ...]], str | None] | None = None,
    secret_prompt: Callable[[str], str] | None = None,
    display: Callable[[str], None],
    client: Any | None = None,
) -> AppConfig:
    """Update the active provider, API key, and model safely."""

    select_prompt = select or _default_select_prompt
    secret_input = secret_prompt or _default_secret_prompt

    try:
        provider = _select_provider(select_prompt)
        adapter = get_provider_adapter(provider)
        api_key, models = _collect_validated_api_key(
            adapter=adapter,
            select=select_prompt,
            secret_prompt=secret_input,
            display=display,
            client=client,
        )
        model = _select_model(select_prompt, provider, _sorted_models(models))
    except OnboardingError as exc:
        display(f"Retryable error: {exc}")
        return current

    validation = get_provider_adapter(provider).validate_model(
        api_key,
        model,
        client=client,
        models=models,
    )
    if not validation.ok:
        display(f"Retryable error: {validation.message}")
        return current

    updated = AppConfig(
        provider=provider,
        model=model,
        api_key=api_key,
        mode=current.mode,
    )
    save_app_config(updated, paths)
    display(f"Updated provider to {provider.label} with model {model}.")
    return updated


def update_runtime_model(
    current: AppConfig,
    paths: ConfigPaths,
    *,
    select: Callable[[str, tuple[str, ...]], str | None] | None = None,
    display: Callable[[str], None],
    client: Any | None = None,
) -> AppConfig:
    """Update the active model safely for the current provider."""

    select_prompt = select or _default_select_prompt
    adapter = get_provider_adapter(current.provider)
    validation = adapter.validate_api_key(current.api_key, client=client)
    if not validation.ok:
        display(f"Retryable error: {validation.message}")
        return current

    try:
        model = _select_model(select_prompt, current.provider, _sorted_models(validation.models))
    except OnboardingError as exc:
        display(f"Retryable error: {exc}")
        return current

    model_validation = adapter.validate_model(
        current.api_key,
        model,
        client=client,
        models=validation.models,
    )
    if not model_validation.ok:
        display(f"Retryable error: {model_validation.message}")
        return current

    updated = AppConfig(
        provider=current.provider,
        model=model,
        api_key=current.api_key,
        mode=current.mode,
    )
    save_app_config(updated, paths)
    display(f"Updated model to {model}.")
    return updated


def open_runtime_config_menu(
    current: AppConfig,
    paths: ConfigPaths,
    *,
    select: Callable[[str, tuple[str, ...]], str | None] | None = None,
    secret_prompt: Callable[[str], str] | None = None,
    display: Callable[[str], None],
    client: Any | None = None,
) -> AppConfig:
    """Open a configuration menu for safe runtime updates."""

    select_prompt = select or _default_select_prompt
    choice = select_prompt(
        "Configuration menu:",
        (
            "/provider — Change the AI provider, API key, and model.",
            "/model — Change the selected model for the current provider.",
            "/mode — Change how much Duckln can do for you.",
        ),
    )
    if choice is None:
        display("No configuration change selected.")
        return current
    if choice.startswith("/provider"):
        return update_runtime_provider(
            current,
            paths,
            select=select_prompt,
            secret_prompt=secret_prompt,
            display=display,
            client=client,
        )
    if choice.startswith("/model"):
        return update_runtime_model(
            current,
            paths,
            select=select_prompt,
            display=display,
            client=client,
        )
    return update_runtime_mode(
        current,
        paths,
        select=select_prompt,
        display=display,
    )


def _default_select_prompt(prompt: str, choices: tuple[str, ...]) -> str | None:
    try:
        from InquirerPy import inquirer
    except ModuleNotFoundError as exc:
        raise RuntimeError("InquirerPy is required for interactive onboarding.") from exc

    try:
        return inquirer.select(
            message=prompt,
            choices=list(choices),
            cycle=False,
        ).execute()
    except KeyboardInterrupt:
        return None


def _default_secret_prompt(prompt: str) -> str:
    try:
        from InquirerPy import inquirer
    except ModuleNotFoundError as exc:
        raise RuntimeError("InquirerPy is required for interactive onboarding.") from exc

    return inquirer.secret(message=prompt).execute()


def _default_text_prompt(prompt: str, default: str = "") -> str | None:
    try:
        from InquirerPy import inquirer
    except ModuleNotFoundError as exc:
        raise RuntimeError("InquirerPy is required for interactive prompts.") from exc

    try:
        return inquirer.text(message=prompt, default=default).execute()
    except KeyboardInterrupt:
        return None
