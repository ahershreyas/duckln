"""Configuration loading, onboarding, and persistence helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from duckln.ai_client import (
    OLLAMA_DEFAULT_BASE_URL,
    Provider,
    ProviderAdapter,
    ProviderModel,
    get_provider_adapter,
    get_provider_adapter_for_base_url,
    normalize_ollama_base_url,
)
from duckln.modes import ControlMode, evaluate_mode_action, get_mode_descriptors, parse_mode
from duckln.safety import assess_command
from state.access import initialize_managed_memory_state, read_config_snapshot, write_config_snapshot
from state.repo_catalog import initialize_local_repo_catalog_cache
from state.store import initialize_state_store


APP_DIR_NAME = "duckln"
CONFIG_FILE_NAME = "config.json"
ENV_CONFIG_DIR = "DUCKLN_CONFIG_DIR"
ENV_CONFIG_FILE = "DUCKLN_CONFIG_FILE"
ENV_HOME = "HOME"
ENV_XDG_CONFIG_HOME = "XDG_CONFIG_HOME"
RUNTIME_PROVIDER_CANCEL_CHOICES = ("Back", "Cancel", "Exit")
RUNTIME_MODEL_CANCEL_CHOICES = ("Back", "Cancel", "Exit")
ONBOARDING_SAFETY_ACCEPT_CHOICE = "I understand and want to continue"
ONBOARDING_SAFETY_DECLINE_CHOICE = "Exit"
OLLAMA_PULL_MODEL_CHOICE = "Pull a new model"
OLLAMA_ENTER_MODEL_CHOICE = "Enter a model name manually"
OLLAMA_START_RUNTIME_CHOICE = "Start Ollama now"
OLLAMA_CUSTOM_BASE_URL_CHOICE = "Use a custom Ollama base URL"
OLLAMA_DETECTION_MAX_ATTEMPTS = 3
OLLAMA_STARTUP_GRACE_SECONDS = 0.2
OLLAMA_RECOMMENDED_MODELS_BY_RAM_GIB = (
    (32, ("llama3.1:8b", "qwen2.5:7b", "mistral:7b")),
    (16, ("llama3.2:3b", "qwen2.5:3b", "phi3:mini")),
    (0, ("llama3.2:1b", "qwen2.5:1.5b", "gemma2:2b")),
)
CONFIG_STATE_KEYS = (
    "provider",
    "model",
    "mode",
    "base_url",
    "user_name",
    "safety_accepted_at",
    "onboarding_complete",
    "preferred_mode",
    "execution_target",
    "execution_vm_name",
)


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
    mode: ControlMode
    api_key: str | None
    base_url: str | None = None
    user_name: str = "there"
    safety_accepted_at: str | None = "1970-01-01T00:00:00+00:00"
    onboarding_complete: bool = True
    preferred_mode: ControlMode | None = None


@dataclass(frozen=True)
class OnboardingResult:
    """Successful onboarding output."""

    config: AppConfig
    saved_to: Path


class OnboardingError(ValueError):
    """Raised when onboarding cannot complete safely."""


class ProviderSelectionRestart(OnboardingError):
    """Raised when the user asks to return to provider selection."""


class SessionExitRequested(OnboardingError):
    """Raised when the user explicitly chooses Exit in a menu."""


@dataclass(frozen=True)
class UserPreferences:
    """Persisted trust and session preferences."""

    user_name: str = "there"
    safety_accepted_at: str | None = None
    onboarding_complete: bool = False
    preferred_mode: ControlMode = ControlMode.HITL


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
    """Load a raw config payload, reconciling SQLite state over file-backed secrets."""

    initialize_runtime_storage(paths)
    file_payload = _read_raw_config_file(paths)
    state_payload = _load_state_config_payload(paths)
    if not state_payload:
        if file_payload:
            write_config_snapshot(paths.config_dir, _state_snapshot_from_payload(file_payload))
        return file_payload

    reconciled_payload = {
        **file_payload,
        **state_payload,
    }
    if reconciled_payload != file_payload:
        _write_raw_config_file(reconciled_payload, paths)
    return reconciled_payload


def save_raw_config(payload: dict[str, Any], paths: ConfigPaths) -> Path:
    """Persist a raw config payload and synchronize the SQLite-backed non-secret snapshot."""

    initialize_runtime_storage(paths)
    _write_raw_config_file(payload, paths)
    state_snapshot = _state_snapshot_from_payload(payload)
    stale_keys = tuple(key for key in CONFIG_STATE_KEYS if key not in state_snapshot)
    if stale_keys:
        initialize_state_store(paths.config_dir).delete_config_values(stale_keys)
    write_config_snapshot(paths.config_dir, state_snapshot)
    return paths.config_file


def serialize_app_config(config: AppConfig) -> dict[str, Any]:
    """Convert app config into a JSON-safe payload."""

    payload = {
        "provider": config.provider.value,
        "model": config.model,
        "api_key": config.api_key,
        "mode": config.mode.value,
        "user_name": config.user_name,
        "safety_accepted_at": config.safety_accepted_at,
        "onboarding_complete": config.onboarding_complete,
        "preferred_mode": None if config.preferred_mode is None else config.preferred_mode.value,
    }
    if config.base_url is not None:
        payload["base_url"] = config.base_url
    return payload


def deserialize_app_config(payload: dict[str, Any]) -> AppConfig:
    """Create typed app config from a persisted payload."""

    required_keys = {"provider", "model", "api_key", "mode"}
    missing = required_keys.difference(payload)
    if missing:
        raise ValueError(f"Missing config fields: {', '.join(sorted(missing))}")

    provider = Provider(payload["provider"])
    base_url = payload.get("base_url")
    if base_url is None and provider is Provider.OLLAMA:
        base_url = OLLAMA_DEFAULT_BASE_URL
    return AppConfig(
        provider=provider,
        model=str(payload["model"]),
        mode=parse_mode(str(payload["mode"])),
        api_key=None if payload["api_key"] is None else str(payload["api_key"]),
        base_url=None if base_url is None else str(base_url),
        user_name=_normalize_user_name(str(payload.get("user_name") or "there")),
        safety_accepted_at=(
            None
            if payload.get("safety_accepted_at") is None
            else str(payload.get("safety_accepted_at"))
        ),
        onboarding_complete=_parse_optional_bool(payload.get("onboarding_complete")),
        preferred_mode=(
            None
            if payload.get("preferred_mode") is None
            else parse_mode(str(payload.get("preferred_mode")))
        ),
    )


def load_app_config(paths: ConfigPaths) -> AppConfig | None:
    """Load typed app config if it exists."""

    payload = load_raw_config(paths)
    if not payload:
        return None
    required_keys = {"provider", "model", "api_key", "mode"}
    if required_keys.difference(payload):
        return None
    return deserialize_app_config(payload)


def save_app_config(config: AppConfig, paths: ConfigPaths) -> Path:
    """Persist typed app config."""

    existing_payload = load_raw_config(paths)
    payload = {
        **existing_payload,
        **serialize_app_config(config),
    }
    if config.base_url is None:
        payload.pop("base_url", None)
    return save_raw_config(payload, paths)


def load_user_preferences(paths: ConfigPaths) -> UserPreferences:
    """Load persisted non-secret user preferences from config storage."""

    payload = load_raw_config(paths)
    return UserPreferences(
        user_name=_normalize_user_name(str(payload.get("user_name") or "there")),
        safety_accepted_at=(
            None
            if payload.get("safety_accepted_at") is None
            else str(payload.get("safety_accepted_at"))
        ),
        onboarding_complete=_parse_optional_bool(payload.get("onboarding_complete")),
        preferred_mode=parse_mode(str(payload.get("preferred_mode") or payload.get("mode") or ControlMode.HITL.value)),
    )


def save_user_preferences(preferences: UserPreferences, paths: ConfigPaths) -> Path:
    """Persist user preferences without requiring provider config to exist yet."""

    payload = {
        **load_raw_config(paths),
        "user_name": _normalize_user_name(preferences.user_name),
        "safety_accepted_at": preferences.safety_accepted_at,
        "onboarding_complete": preferences.onboarding_complete,
        "preferred_mode": preferences.preferred_mode.value,
    }
    return save_raw_config(payload, paths)


def ensure_first_run_preferences(
    paths: ConfigPaths,
    *,
    select: Callable[[str, tuple[str, ...]], str | None] | None = None,
    text_prompt: Callable[[str, str], str | None] | None = None,
    display: Callable[[str], None],
) -> UserPreferences:
    """Collect safety consent and user name once, then persist preferences."""

    preferences = load_user_preferences(paths)
    if preferences.safety_accepted_at and preferences.user_name and preferences.onboarding_complete:
        return preferences

    select_prompt = select or _default_select_prompt
    text_input = text_prompt or _default_text_prompt

    if not preferences.safety_accepted_at:
        display(
            "\n".join(
                (
                    "┌────────────────────────────────────────────────────────────┐",
                    "│ ◆ Safety & Permissions                                    │",
                    "│ Duckln can run terminal commands, read project files,      │",
                    "│ and install dependencies on your machine.                  │",
                    "│ HOTL asks before execution; HOOTLWO runs safe commands.    │",
                    "│ Destructive commands are blocked.                          │",
                    "└────────────────────────────────────────────────────────────┘",
                )
            )
        )
        consent = select_prompt(
            "Safety check:",
            (ONBOARDING_SAFETY_ACCEPT_CHOICE, ONBOARDING_SAFETY_DECLINE_CHOICE),
        )
        if consent is None or consent.strip() == ONBOARDING_SAFETY_DECLINE_CHOICE:
            raise SessionExitRequested("Safety acceptance was declined.")
        preferences = UserPreferences(
            user_name=preferences.user_name,
            safety_accepted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            onboarding_complete=preferences.onboarding_complete,
            preferred_mode=preferences.preferred_mode,
        )
        save_user_preferences(preferences, paths)

    if preferences.user_name == "there":
        user_name = (text_input("What would you like me to call you?", "") or "").strip()
        if not user_name:
            raise OnboardingError("Name setup was cancelled.")
        preferences = UserPreferences(
            user_name=_normalize_user_name(user_name),
            safety_accepted_at=preferences.safety_accepted_at,
            onboarding_complete=preferences.onboarding_complete,
            preferred_mode=preferences.preferred_mode,
        )
        save_user_preferences(preferences, paths)

    return preferences


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
    secret_prompt: Callable[[str], str | None] | None = None,
    text_prompt: Callable[[str, str], str | None] | None = None,
    display: Callable[[str], None],
    render_banner: Callable[[], str],
    client: Any | None = None,
) -> OnboardingResult:
    """Run first-launch onboarding and persist config only after validation succeeds."""

    select_prompt = select or _default_select_prompt
    secret_input = secret_prompt or _default_secret_prompt
    text_input = text_prompt or _default_text_prompt

    display(render_banner())
    preferences = load_user_preferences(paths)

    while True:
        provider = _select_provider(select_prompt)
        adapter = get_provider_adapter(provider)
        try:
            api_key, models, base_url = _collect_validated_api_key(
                adapter=adapter,
                select=select_prompt,
                secret_prompt=secret_input,
                text_prompt=text_input,
                display=display,
                mode=None,
                client=client,
            )
        except ProviderSelectionRestart:
            continue
        break

    model, models = _select_model(
        select_prompt,
        provider,
        _sorted_models(models),
        text_prompt=text_input,
        adapter=adapter,
        api_key=api_key,
        display=display,
        client=client,
    )
    model_validation = adapter.validate_model(
        api_key,
        model,
        client=client,
        models=models,
    )
    if not model_validation.ok:
        raise OnboardingError(f"Retryable error: {model_validation.message}")
    display(f"{provider.label} is ready with model {model}.")

    mode = _select_mode(select_prompt)
    if mode is ControlMode.HOOTLWO:
        display("Warning: HOOTLWO only auto-runs safe whitelisted commands. Use a sandbox or VM.")

    config = AppConfig(
        provider=provider,
        model=model,
        mode=mode,
        api_key=api_key,
        base_url=_provider_base_url(provider, base_url=base_url),
        user_name=preferences.user_name,
        safety_accepted_at=preferences.safety_accepted_at,
        onboarding_complete=True,
        preferred_mode=mode,
    )
    saved_to = save_app_config(config, paths)
    display(f"Duckln is configured and ready to use with {provider.label}.")
    return OnboardingResult(config=config, saved_to=saved_to)


def _select_provider(
    select: Callable[[str, tuple[str, ...]], str | None],
    *,
    include_cancel: bool = False,
) -> Provider:
    choices = available_provider_choices()
    if include_cancel:
        choices = choices + RUNTIME_PROVIDER_CANCEL_CHOICES
    choice = select("Select your AI provider:", choices)
    if choice is None or choice.strip() in {"Back", "Cancel"}:
        raise OnboardingError("Provider selection was cancelled.")
    if choice.strip() == "Exit":
        raise SessionExitRequested("Exit requested from provider selection.")
    choice = choice.strip()
    for provider in Provider:
        if choice.lower() in {provider.value, provider.label.lower()}:
            return provider
    raise OnboardingError(f"Unsupported provider selection: {choice}")


def _select_model(
    select: Callable[[str, tuple[str, ...]], str | None],
    provider: Provider,
    models: tuple[ProviderModel, ...],
    *,
    include_cancel: bool = False,
    text_prompt: Callable[[str, str], str | None] | None = None,
    adapter: ProviderAdapter | None = None,
    api_key: str | None = None,
    display: Callable[[str], None] | None = None,
    client: Any | None = None,
) -> tuple[str, tuple[ProviderModel, ...]]:
    if provider is Provider.OLLAMA:
        return _select_ollama_model(
            select,
            models,
            include_cancel=include_cancel,
            text_prompt=text_prompt,
            adapter=adapter,
            api_key=api_key,
            display=display,
            client=client,
        )

    if not models:
        raise OnboardingError(f"{provider.label} returned no models to choose from.")

    choices = tuple(model.id for model in models)
    if include_cancel:
        choices = choices + RUNTIME_MODEL_CANCEL_CHOICES
    choice = select("Select a model:", choices)
    if choice is None or choice.strip() in {"Back", "Cancel"}:
        raise OnboardingError("Model selection was cancelled.")
    if choice.strip() == "Exit":
        raise SessionExitRequested("Exit requested from model selection.")
    choice = choice.strip()
    if choice not in choices:
        raise OnboardingError(f"Unsupported model selection: {choice}")
    return choice, models


def _select_mode(
    select: Callable[[str, tuple[str, ...]], str | None],
    *,
    include_cancel: bool = False,
) -> ControlMode:
    choices = available_mode_choices()
    if include_cancel:
        choices = choices + ("Back", "Cancel", "Exit")
    choice = select("Select your control mode:", choices)
    if choice is None or choice.strip() in {"Back", "Cancel"}:
        raise OnboardingError("Mode selection was cancelled.")
    if choice.strip() == "Exit":
        raise SessionExitRequested("Exit requested from mode selection.")
    choice = choice.strip()
    for descriptor in get_mode_descriptors():
        if choice == descriptor.choice_label or choice.upper() == descriptor.title:
            return descriptor.mode
    raise OnboardingError(f"Unsupported mode selection: {choice}")


def _collect_validated_api_key(
    *,
    adapter: ProviderAdapter,
    select: Callable[[str, tuple[str, ...]], str | None],
    secret_prompt: Callable[[str], str | None],
    text_prompt: Callable[[str, str], str | None],
    display: Callable[[str], None],
    mode: ControlMode | None,
    client: Any | None,
) -> tuple[str | None, tuple[ProviderModel, ...], str | None]:
    ollama_attempts = 0
    while True:
        if adapter.provider.requires_api_key:
            entered_api_key = secret_prompt(f"Enter your {adapter.provider.api_key_name}: ")
            if entered_api_key is None:
                raise OnboardingError("API key entry was cancelled.")
            api_key = entered_api_key.strip()
            display("API key received.")

            confirmation = select(
                "Use this API key?",
                ("Use this key", "Re-enter API key"),
            )
            if confirmation is None:
                raise OnboardingError("API key confirmation was cancelled.")
            confirmation = confirmation.strip()
            if confirmation == "Re-enter API key":
                continue
        else:
            api_key = None
            ollama_attempts += 1
            display(f"Checking local Ollama runtime at {adapter.models_url()}.")

        validation = adapter.validate_api_key(api_key, client=client)
        if validation.ok:
            display(validation.message)
            return api_key, validation.models, _provider_base_url(adapter.provider, base_url=adapter.base_url)

        display(f"Retryable error: {validation.message}")
        if adapter.provider is Provider.OLLAMA:
            if ollama_attempts >= OLLAMA_DETECTION_MAX_ATTEMPTS:
                raise ProviderSelectionRestart(validation.message)
            if shutil.which("ollama") is not None and mode is not ControlMode.HITL:
                ollama_choices = (
                    OLLAMA_START_RUNTIME_CHOICE,
                    "Retry Ollama detection",
                    OLLAMA_CUSTOM_BASE_URL_CHOICE,
                    "Back to provider selection",
                    "Cancel",
                    "Exit",
                )
            else:
                if shutil.which("ollama") is not None and mode is ControlMode.HITL:
                    display("Run `ollama serve` in another terminal, then retry detection.")
                ollama_choices = (
                    "Retry Ollama detection",
                    OLLAMA_CUSTOM_BASE_URL_CHOICE,
                    "Back to provider selection",
                    "Cancel",
                    "Exit",
                )
            retry_choice = select(
                "Ollama is not reachable. What would you like to do?",
                ollama_choices,
            )
            if retry_choice is None:
                raise OnboardingError("Provider validation failed: Ollama detection was cancelled.")
            retry_choice = retry_choice.strip()
            if retry_choice == "Exit":
                raise SessionExitRequested("Exit requested from Ollama setup.")
            if retry_choice == OLLAMA_START_RUNTIME_CHOICE:
                _start_ollama_runtime(mode=mode, display=display)
                continue
            if retry_choice == "Retry Ollama detection":
                continue
            if retry_choice == OLLAMA_CUSTOM_BASE_URL_CHOICE:
                custom_base_url = text_prompt("Enter Ollama base URL:", adapter.base_url)
                if custom_base_url is None or not custom_base_url.strip():
                    raise OnboardingError("Provider validation failed: custom Ollama URL entry was cancelled.")
                adapter = get_provider_adapter_for_base_url(
                    Provider.OLLAMA,
                    base_url=normalize_ollama_base_url(custom_base_url),
                )
                continue
            if retry_choice == "Back to provider selection":
                raise ProviderSelectionRestart(validation.message)
            raise OnboardingError("Provider validation failed: Ollama detection was cancelled.")

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


def _provider_base_url(provider: Provider, *, base_url: str | None = None) -> str | None:
    if provider is Provider.OLLAMA:
        return normalize_ollama_base_url(base_url)
    return None


def _start_ollama_runtime(
    *,
    mode: ControlMode | None,
    display: Callable[[str], None],
) -> None:
    command = "ollama serve"
    effective_mode = mode or ControlMode.HOTL
    decision = evaluate_mode_action(
        effective_mode,
        assess_command(command),
        is_ai_suggested=True,
        user_approved=True,
    )
    if not decision.allowed:
        display(decision.reason)
        display("Run `ollama serve` in another terminal, then retry detection.")
        return

    display("Starting Ollama with `ollama serve`...")
    try:
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        display(f"Retryable error: Failed to start Ollama automatically. Run `ollama serve` manually and retry. ({exc})")
        return
    time.sleep(OLLAMA_STARTUP_GRACE_SECONDS)


def _select_ollama_model(
    select: Callable[[str, tuple[str, ...]], str | None],
    models: tuple[ProviderModel, ...],
    *,
    include_cancel: bool,
    text_prompt: Callable[[str, str], str | None] | None,
    adapter: ProviderAdapter | None,
    api_key: str | None,
    display: Callable[[str], None] | None,
    client: Any | None,
) -> tuple[str, tuple[ProviderModel, ...]]:
    text_input = text_prompt or _default_text_prompt
    local_model_ids = tuple(model.id for model in models)
    recommended_model_ids = _recommended_ollama_models(_detect_system_ram_gib())
    recommendation_choices = tuple(
        f"{model_id} [recommended]"
        for model_id in recommended_model_ids
        if model_id not in local_model_ids
    )
    choices = local_model_ids + recommendation_choices + (OLLAMA_PULL_MODEL_CHOICE, OLLAMA_ENTER_MODEL_CHOICE)
    if include_cancel:
        choices = choices + RUNTIME_MODEL_CANCEL_CHOICES

    choice = select("Select a model:", choices)
    if choice is None or choice.strip() in RUNTIME_MODEL_CANCEL_CHOICES:
        raise OnboardingError("Model selection was cancelled.")
    choice = choice.strip()

    if choice.endswith(" [recommended]"):
        requested_model = choice.removesuffix(" [recommended]").strip()
        refreshed_models = _pull_and_refresh_ollama_model(
            requested_model,
            adapter=adapter,
            api_key=api_key,
            display=display,
            client=client,
        )
        return requested_model, refreshed_models

    if choice == OLLAMA_ENTER_MODEL_CHOICE:
        requested_model = (text_input("Enter an Ollama model name:", "") or "").strip()
        if not requested_model:
            raise OnboardingError("Model selection was cancelled.")
        if requested_model in local_model_ids:
            return requested_model, models
        refreshed_models = _pull_and_refresh_ollama_model(
            requested_model,
            adapter=adapter,
            api_key=api_key,
            display=display,
            client=client,
        )
        return requested_model, refreshed_models

    if choice == OLLAMA_PULL_MODEL_CHOICE:
        requested_model = (text_input("Enter an Ollama model to pull:", "") or "").strip()
        if not requested_model:
            raise OnboardingError("Model selection was cancelled.")
        refreshed_models = _pull_and_refresh_ollama_model(
            requested_model,
            adapter=adapter,
            api_key=api_key,
            display=display,
            client=client,
        )
        return requested_model, refreshed_models

    if choice not in local_model_ids:
        raise OnboardingError(f"Unsupported model selection: {choice}")
    return choice, models


def _pull_and_refresh_ollama_model(
    requested_model: str,
    *,
    adapter: ProviderAdapter | None,
    api_key: str | None,
    display: Callable[[str], None] | None,
    client: Any | None,
) -> tuple[ProviderModel, ...]:
    if adapter is None or display is None:
        raise OnboardingError("Pulling a new model is unavailable here.")

    _run_ollama_pull_subprocess(requested_model, display=display)
    validation = adapter.validate_api_key(api_key, client=client)
    if not validation.ok:
        raise OnboardingError(validation.message)
    display(validation.message)
    if requested_model not in {model.id for model in validation.models}:
        raise OnboardingError(f"Ollama model {requested_model} is not listed locally after pull.")
    return _sorted_models(validation.models)


def _run_ollama_pull_subprocess(
    model_name: str,
    *,
    display: Callable[[str], None],
) -> None:
    display(f"Running `ollama pull {model_name}`...")
    try:
        process = subprocess.Popen(
            ["ollama", "pull", model_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        raise OnboardingError(f"Failed to run `ollama pull {model_name}`: {exc}") from exc

    assert process.stdout is not None
    for line in process.stdout:
        if line.strip():
            display(line.rstrip())
    exit_code = process.wait()
    if exit_code != 0:
        raise OnboardingError(f"`ollama pull {model_name}` failed with exit code {exit_code}.")


def _recommended_ollama_models(ram_gib: int | None) -> tuple[str, ...]:
    available_ram = 0 if ram_gib is None else ram_gib
    for min_ram_gib, model_ids in OLLAMA_RECOMMENDED_MODELS_BY_RAM_GIB:
        if available_ram >= min_ram_gib:
            return model_ids
    return OLLAMA_RECOMMENDED_MODELS_BY_RAM_GIB[-1][1]


def _detect_system_ram_gib() -> int | None:
    if not hasattr(os, "sysconf"):
        return None
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        page_count = int(os.sysconf("SC_PHYS_PAGES"))
    except (OSError, ValueError, TypeError):
        return None
    if page_size <= 0 or page_count <= 0:
        return None
    return int((page_size * page_count) / (1024**3))


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
        mode = _select_mode(select_prompt, include_cancel=True)
    except SessionExitRequested:
        raise
    except OnboardingError as exc:
        display(f"Retryable error: {exc}")
        return current

    updated = AppConfig(
        provider=current.provider,
        model=current.model,
        mode=mode,
        api_key=current.api_key,
        base_url=current.base_url,
        user_name=current.user_name,
        safety_accepted_at=current.safety_accepted_at,
        onboarding_complete=current.onboarding_complete,
        preferred_mode=mode,
    )
    save_app_config(updated, paths)
    display(f"Updated mode to {mode.label}.")
    return updated


def update_runtime_provider(
    current: AppConfig,
    paths: ConfigPaths,
    *,
    select: Callable[[str, tuple[str, ...]], str | None] | None = None,
    secret_prompt: Callable[[str], str | None] | None = None,
    text_prompt: Callable[[str, str], str | None] | None = None,
    display: Callable[[str], None],
    client: Any | None = None,
) -> AppConfig:
    """Update the active provider, API key, and model safely."""

    select_prompt = select or _default_select_prompt
    secret_input = secret_prompt or _default_secret_prompt
    text_input = text_prompt or _default_text_prompt

    try:
        while True:
            provider = _select_provider(select_prompt, include_cancel=True)
            adapter = get_provider_adapter_for_base_url(
                provider,
                base_url=current.base_url if provider is Provider.OLLAMA and current.provider is Provider.OLLAMA else None,
            )
            try:
                api_key, models, base_url = _collect_validated_api_key(
                    adapter=adapter,
                    select=select_prompt,
                    secret_prompt=secret_input,
                    text_prompt=text_input,
                    display=display,
                    mode=current.mode,
                    client=client,
                )
            except ProviderSelectionRestart:
                continue
            break
        model, models = _select_model(
            select_prompt,
            provider,
            _sorted_models(models),
            include_cancel=True,
            text_prompt=text_input,
            adapter=adapter,
            api_key=api_key,
            display=display,
            client=client,
        )
    except SessionExitRequested:
        raise
    except OnboardingError as exc:
        if "cancelled" in str(exc).lower():
            display("Provider update cancelled.")
            return current
        display(f"Retryable error: {exc}")
        return current

    validation = get_provider_adapter_for_base_url(provider, base_url=base_url).validate_model(
        api_key,
        model,
        client=client,
        models=models,
    )
    if not validation.ok:
        display(f"Retryable error: {validation.message}")
        return current
    display(f"{provider.label} connection ready.")

    updated = AppConfig(
        provider=provider,
        model=model,
        mode=current.mode,
        api_key=api_key,
        base_url=_provider_base_url(provider, base_url=base_url),
        user_name=current.user_name,
        safety_accepted_at=current.safety_accepted_at,
        onboarding_complete=current.onboarding_complete,
        preferred_mode=current.preferred_mode or current.mode,
    )
    save_app_config(updated, paths)
    display(f"Updated provider to {provider.label} with model {model}.")
    return updated


def update_runtime_model(
    current: AppConfig,
    paths: ConfigPaths,
    *,
    select: Callable[[str, tuple[str, ...]], str | None] | None = None,
    text_prompt: Callable[[str, str], str | None] | None = None,
    display: Callable[[str], None],
    client: Any | None = None,
) -> AppConfig:
    """Update the active model safely for the current provider."""

    select_prompt = select or _default_select_prompt
    text_input = text_prompt or _default_text_prompt
    adapter = get_provider_adapter_for_base_url(current.provider, base_url=current.base_url)
    validation = adapter.validate_api_key(current.api_key, client=client)
    if not validation.ok:
        display(f"Retryable error: {validation.message}")
        return current
    display(validation.message)

    try:
        model, models = _select_model(
            select_prompt,
            current.provider,
            _sorted_models(validation.models),
            include_cancel=True,
            text_prompt=text_input,
            adapter=adapter,
            api_key=current.api_key,
            display=display,
            client=client,
        )
    except SessionExitRequested:
        raise
    except OnboardingError as exc:
        if "cancelled" in str(exc).lower():
            display("Model update cancelled.")
            return current
        display(f"Retryable error: {exc}")
        return current

    model_validation = adapter.validate_model(
        current.api_key,
        model,
        client=client,
        models=models,
    )
    if not model_validation.ok:
        display(f"Retryable error: {model_validation.message}")
        return current
    display(f"{current.provider.label} is ready with model {model}.")

    updated = AppConfig(
        provider=current.provider,
        model=model,
        mode=current.mode,
        api_key=current.api_key,
        base_url=current.base_url,
        user_name=current.user_name,
        safety_accepted_at=current.safety_accepted_at,
        onboarding_complete=current.onboarding_complete,
        preferred_mode=current.preferred_mode or current.mode,
    )
    save_app_config(updated, paths)
    display(f"Updated model to {model}.")
    return updated


def open_runtime_config_menu(
    current: AppConfig,
    paths: ConfigPaths,
    *,
    select: Callable[[str, tuple[str, ...]], str | None] | None = None,
    secret_prompt: Callable[[str], str | None] | None = None,
    text_prompt: Callable[[str, str], str | None] | None = None,
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
            "Back",
            "Exit",
        ),
    )
    if choice is None or choice == "Back":
        display("No configuration change selected.")
        return current
    if choice == "Exit":
        raise SessionExitRequested("Exit requested from configuration menu.")
    if choice.startswith("/provider"):
        return update_runtime_provider(
            current,
            paths,
            select=select_prompt,
            secret_prompt=secret_prompt,
            text_prompt=text_prompt,
            display=display,
            client=client,
        )
    if choice.startswith("/model"):
        return update_runtime_model(
            current,
            paths,
            select=select_prompt,
            text_prompt=text_prompt,
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


def _default_secret_prompt(prompt: str) -> str | None:
    try:
        from InquirerPy import inquirer
    except ModuleNotFoundError as exc:
        raise RuntimeError("InquirerPy is required for interactive onboarding.") from exc

    try:
        return inquirer.secret(message=prompt).execute()
    except KeyboardInterrupt:
        return None


def _default_text_prompt(prompt: str, default: str = "") -> str | None:
    try:
        from InquirerPy import inquirer
    except ModuleNotFoundError as exc:
        raise RuntimeError("InquirerPy is required for interactive prompts.") from exc

    try:
        return inquirer.text(message=prompt, default=default).execute()
    except KeyboardInterrupt:
        return None


def _normalize_user_name(name: str) -> str:
    normalized = " ".join(name.split())
    return normalized or "there"


def _read_raw_config_file(paths: ConfigPaths) -> dict[str, Any]:
    if not paths.config_file.exists():
        return {}
    return json.loads(paths.config_file.read_text(encoding="utf-8"))


def _write_raw_config_file(payload: dict[str, Any], paths: ConfigPaths) -> None:
    paths.config_file.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _load_state_config_payload(paths: ConfigPaths) -> dict[str, Any]:
    state_values = read_config_snapshot(paths.config_dir)
    payload: dict[str, Any] = {}
    for key in CONFIG_STATE_KEYS:
        if key not in state_values:
            continue
        if key == "onboarding_complete":
            payload[key] = _parse_optional_bool(state_values[key])
        elif key == "preferred_mode" and not state_values[key]:
            payload[key] = None
        else:
            payload[key] = state_values[key]
    return payload


def _state_snapshot_from_payload(payload: dict[str, Any]) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for key in CONFIG_STATE_KEYS:
        if key not in payload or payload[key] is None:
            continue
        value = payload[key]
        if key == "onboarding_complete":
            snapshot[key] = str(_parse_optional_bool(value)).lower()
        else:
            snapshot[key] = str(value)
    return snapshot


def _parse_optional_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
