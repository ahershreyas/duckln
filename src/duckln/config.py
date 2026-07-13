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
from duckln.selections import duckln_select
from duckln.safety import assess_command
from duckln.ui import render_first_run_safety_panel
from state.access import initialize_managed_memory_state, materialize_managed_memory_state, read_config_snapshot, write_config_snapshot
from state.repo_catalog import initialize_local_repo_catalog_cache
from state.store import initialize_state_store


APP_DIR_NAME = "duckln"
CONFIG_FILE_NAME = "config.json"
ENV_CONFIG_DIR = "DUCKLN_CONFIG_DIR"
ENV_CONFIG_FILE = "DUCKLN_CONFIG_FILE"
ENV_HOME = "HOME"
ENV_XDG_CONFIG_HOME = "XDG_CONFIG_HOME"
RUNTIME_PROVIDER_CANCEL_CHOICES = ("Cancel",)
RUNTIME_MODEL_CANCEL_CHOICES = ("Cancel",)
ONBOARDING_SAFETY_ACCEPT_CHOICE = "I understand and want to continue"
ONBOARDING_SAFETY_DECLINE_CHOICE = "Cancel"
OLLAMA_PULL_MODEL_CHOICE = "Pull a new model"
OLLAMA_ENTER_MODEL_CHOICE = "Enter a model name manually"
OLLAMA_REMOVE_MODEL_CHOICE = "Remove an installed model…"
OLLAMA_START_RUNTIME_CHOICE = "Start Ollama now"
OLLAMA_CUSTOM_BASE_URL_CHOICE = "Use a custom Ollama base URL"
OLLAMA_DETECTION_MAX_ATTEMPTS = 3
OLLAMA_STARTUP_GRACE_SECONDS = 0.2
OLLAMA_RECOMMENDED_MODELS_BY_RAM_GIB = (
    (32, ("llama3.1:8b", "qwen2.5:7b", "mistral:7b")),
    (16, ("llama3.2:3b", "qwen2.5:3b", "phi3:mini")),
    (0, ("llama3.2:1b", "qwen2.5:1.5b", "gemma2:2b")),
)
# Plan 182 F2: a curated shortlist of popular pullable Ollama models so "Pull a model" is a
# pick-list, not a blank text box. (Ollama has no API to enumerate its full registry, so this
# is a maintained shortlist; the "type an exact name" fallback covers anything not listed.)
OLLAMA_POPULAR_MODELS = (
    ("llama3.2:3b", "Meta Llama 3.2 — small, fast (~2 GB)"),
    ("llama3.1:8b", "Meta Llama 3.1 — capable general (~4.7 GB)"),
    ("qwen2.5-coder:7b", "Qwen2.5 Coder — strong for code (~4.7 GB)"),
    ("qwen2.5:7b", "Qwen2.5 — general (~4.7 GB)"),
    ("deepseek-r1:7b", "DeepSeek-R1 — reasoning (~4.7 GB)"),
    ("deepseek-r1:8b", "DeepSeek-R1 8B — reasoning (~5 GB)"),
    ("mistral:7b", "Mistral 7B — general (~4.1 GB)"),
    ("phi3.5", "Microsoft Phi-3.5 — small, capable (~2.2 GB)"),
    ("gemma2:9b", "Google Gemma 2 — general (~5.4 GB)"),
    ("codellama:7b", "Code Llama — code (~3.8 GB)"),
    ("llama3.2:1b", "Meta Llama 3.2 1B — tiny (~1.3 GB)"),
)
OLLAMA_PULL_TYPE_CUSTOM_CHOICE = "Type an exact model name…"
OPENROUTER_RECOMMENDED_MODEL_IDS = (
    "anthropic/claude-sonnet-4-6",
    "anthropic/claude-opus-4-6",
    "openai/gpt-4o",
    "openai/gpt-4o-mini",
    "openai/o3-mini",
    "google/gemma-3-27b-it:free",
    "google/gemma-3-12b-it:free",
    "meta-llama/llama-3.3-70b-instruct",
    "meta-llama/llama-3.1-8b-instruct:free",
    "mistralai/mistral-small-3.1-24b-instruct:free",
    "qwen/qwen3-235b-a22b",
    "qwen/qwen3-30b-a3b:free",
    "deepseek/deepseek-r1",
    "deepseek/deepseek-v3",
    "nvidia/llama-3.1-nemotron-ultra-253b-v1:free",
    "microsoft/phi-4-reasoning-plus:free",
    "moonshotai/kimi-k2:free",
)
MAX_PROVIDER_MODEL_CHOICES = 40
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
    "failure_window_hours",
    "font_setup_acknowledged",
    "plan_mode_enabled",
    "plan_precheck",
    "duckln_ui",
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
    # Plan 61 Fix C: tunable freshness window for persistent failure memory.
    # Default 24h; user can change via `/failures window <hours>`.
    failure_window_hours: float = 24.0
    # Plan 63 Fix 4c: True once the user has dismissed (or acted on) the
    # one-time VS Code font-setup guidance message. Persisted so we never
    # nag twice.
    font_setup_acknowledged: bool = False
    # Plan 67: when True, multi-step actions (repo bring-up, runtime
    # repair, multi-step conversation requests) generate a structured
    # plan first, render it, and wait for `/plan approve`. Orthogonal
    # to ControlMode — the two flags compose.
    # Plan 188: Plan Mode is ALWAYS ON (no OFF) for the LIVE app — a loaded config is
    # always coerced on (see deserialize). The dataclass field default stays False so
    # programmatic/internal construction (and the separate `bring_up_selected_repo`
    # `plan_mode_enabled` PARAMETER) can still build a plan-off config for direct execution.
    plan_mode_enabled: bool = False
    # Plan 76: whether Duckln runs a read-only (S0) pre-check of the target
    # before drafting a plan. "ask" (default) → prompt Yes/No each time;
    # "on" → always pre-check; "off" → never.
    plan_precheck: str = "ask"
    duckln_ui: str = "auto"  # Plan 80 Fix 4: auto | inline | full


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


def _model_selection_ids(provider: Provider, models: tuple[ProviderModel, ...]) -> tuple[str, ...]:
    """Return model ids suitable for interactive selection."""

    model_ids = tuple(model.id for model in models)
    return model_ids


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
    try:
        from duckln.loop_runtime import start_loop_scheduler

        start_loop_scheduler(paths.config_dir)
    except Exception:
        pass
    _sweep_run_history_ttl(paths.config_dir)


_RUN_HISTORY_TTL_DAYS = 30


def _sweep_run_history_ttl(config_dir) -> int:
    """Item: bound the size of run_history. Drops rows older than TTL on each session
    start. Best-effort — schema differences across versions don't fail startup.
    """

    from datetime import datetime, timedelta, timezone

    cutoff = (datetime.now(timezone.utc) - timedelta(days=_RUN_HISTORY_TTL_DAYS)).isoformat(timespec="seconds")
    try:
        store = initialize_state_store(config_dir)
        with store._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM run_history WHERE started_at < ?",
                (cutoff,),
            )
            return cursor.rowcount or 0
    except Exception:
        return 0


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
    materialize_managed_memory_state(paths.config_dir)
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
        "failure_window_hours": float(config.failure_window_hours),
        "font_setup_acknowledged": bool(config.font_setup_acknowledged),
        "plan_mode_enabled": bool(config.plan_mode_enabled),
        "plan_precheck": config.plan_precheck,
        "duckln_ui": config.duckln_ui,
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
        failure_window_hours=_parse_failure_window_hours(payload.get("failure_window_hours")),
        font_setup_acknowledged=_parse_optional_bool(payload.get("font_setup_acknowledged")),
        # Plan 72 Phase 4: Plan Mode defaults ON when the key is absent (legacy configs). The
        # schema round-trip stays FAITHFUL here; the LIVE always-on coercion (Plan 188) happens
        # in load_app_config, so serialize/deserialize is not lossy.
        plan_mode_enabled=_parse_optional_bool_default_true(payload.get("plan_mode_enabled")),
        plan_precheck=_parse_plan_precheck(payload.get("plan_precheck")),
        duckln_ui=_parse_duckln_ui(payload.get("duckln_ui")),
    )


def _parse_failure_window_hours(raw) -> float:
    """Plan 61 Fix C: parse failure_window_hours with safe defaults.

    Bounds: 0.5 to 168 (one week). Default 24.0."""
    if raw is None:
        return 24.0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 24.0
    if value < 0.5:
        return 0.5
    if value > 168.0:
        return 168.0
    return value


def load_app_config(paths: ConfigPaths) -> AppConfig | None:
    """Load typed app config if it exists."""

    payload = load_raw_config(paths)
    if not payload:
        return None
    required_keys = {"provider", "model", "api_key", "mode"}
    if required_keys.difference(payload):
        return None
    cfg = deserialize_app_config(payload)
    # Plan 188: Plan Mode is ALWAYS ON in the running app — coerce any legacy stored `false` to
    # on so the pre-check + plan always run for a setup/deploy. (Serialize/deserialize stay
    # faithful; this coercion is only at the live-load boundary.)
    if not cfg.plan_mode_enabled:
        from dataclasses import replace as _dc_replace

        cfg = _dc_replace(cfg, plan_mode_enabled=True)
    return cfg


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
        display(render_first_run_safety_panel())
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
        display(
            "🐣 Hey there! I'm Duckln.\n\n"
            "I'm your terminal companion for running AI/ML projects locally.\n\n"
            "Point me at any repo and I'll handle the setup, fix the errors,\n\n"
            "and get it running — while keeping you in control.\n\n"
            "Before we start — What should I call you?"
        )
        user_name = (text_input("", "") or "").strip()
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
    # Plan 121: prove the connection with a real round-trip and SHOW the reply —
    # only treat it as ready when the model actually answers.
    if not verify_live_reply(
        provider=provider, model=model, api_key=api_key,
        base_url=_provider_base_url(provider, base_url=base_url), display=display, client=client,
    ):
        raise OnboardingError(f"Retryable error: {provider.label} accepted the key but did not return a live reply.")
    try:
        from duckln.connection_status import record_live_verified

        record_live_verified(paths.config_dir, provider=provider, model=model)
    except Exception:
        pass
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
        plan_mode_enabled=True,  # Plan 72 Phase 4: Plan Mode defaults ON for new users.
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
    if choice is None or choice.strip() == "Cancel":
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

    choices = _model_selection_ids(provider, models)
    if include_cancel:
        choices = RUNTIME_MODEL_CANCEL_CHOICES + choices
    choice = select("Select a model:", choices)
    if choice is None or choice.strip() == "Cancel":
        raise OnboardingError("Model selection was cancelled.")
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
        choices = choices + ("Cancel",)
    choice = select("Select your control mode:", choices)
    if choice is None or choice.strip() == "Cancel":
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
                _start_ollama_runtime(mode=mode, display=display, adapter=adapter, client=client)
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


def verify_live_reply(
    *,
    provider: Provider,
    model: str,
    api_key: str | None,
    base_url: str | None,
    display: Callable[[str], None],
    client=None,
) -> bool:
    """Plan 121: prove the connection with a REAL round-trip — send a tiny message
    to the model and SHOW its reply to the user. Returns True on a non-empty reply,
    False (with the real error shown) otherwise. Never prints the API key."""
    from duckln.ai_client import generate_provider_reply

    display(f"Sending a test message to {provider.label} to confirm the connection...")
    try:
        reply = generate_provider_reply(
            provider,
            model_id=model,
            api_key=api_key,
            base_url=base_url,
            system_prompt="You are a connectivity check for the Duckln CLI. Answer in one short sentence.",
            user_message="Reply with one short sentence confirming you received this message.",
            client=client,
            max_tokens=60,
        )
    except Exception as exc:  # noqa: BLE001 — surface the real provider error
        display(f"Live check failed — {provider.label} did not reply: {exc}")
        return False
    reply = (reply or "").strip()
    if not reply:
        display(f"Live check failed — {provider.label} returned an empty reply.")
        return False
    display(f'Live check — {provider.label} replied: "{reply}"')
    return True


def _start_ollama_runtime(
    *,
    mode: ControlMode | None,
    display: Callable[[str], None],
    adapter: ProviderAdapter | None = None,
    client: Any | None = None,
    readiness_timeout: float = 6.0,
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
    # Plan 182 F3: WAIT for the daemon to actually bind before returning, so the caller's
    # immediate re-validate doesn't loop on "installed but not running" (the screenshot bug).
    if adapter is not None:
        deadline = time.monotonic() + max(0.5, readiness_timeout)
        while time.monotonic() < deadline:
            time.sleep(0.5)
            try:
                if getattr(adapter.validate_api_key(None, client=client), "ok", False):
                    display("Ollama is up.")
                    return
            except Exception:
                pass
    else:
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
    # Plan 186 F1b: loop so "Remove an installed model…" refreshes the list and re-prompts,
    # while every other choice returns the selected model.
    while True:
        local_model_ids = tuple(model.id for model in models)
        recommended_model_ids = _recommended_ollama_models(_detect_system_ram_gib())
        recommendation_choices = tuple(
            f"{model_id} [recommended]"
            for model_id in recommended_model_ids
            if model_id not in local_model_ids
        )
        choices = local_model_ids + recommendation_choices + (OLLAMA_PULL_MODEL_CHOICE, OLLAMA_ENTER_MODEL_CHOICE)
        # Only offer removal when something is actually installed.
        if local_model_ids:
            choices = choices + (OLLAMA_REMOVE_MODEL_CHOICE,)
        if include_cancel:
            choices = RUNTIME_MODEL_CANCEL_CHOICES + choices

        choice = select("Select a model:", choices)
        if choice is None or choice.strip() in RUNTIME_MODEL_CANCEL_CHOICES:
            raise OnboardingError("Model selection was cancelled.")
        choice = choice.strip()

        if choice == OLLAMA_REMOVE_MODEL_CHOICE:
            models = _remove_ollama_model(
                select, models, adapter=adapter, api_key=api_key, display=display, client=client,
            )
            continue  # re-present the refreshed menu
        return _resolve_ollama_model_choice(
            choice, models, local_model_ids,
            select=select, text_input=text_input,
            adapter=adapter, api_key=api_key, display=display, client=client,
        )


def _resolve_ollama_model_choice(
    choice: str,
    models: tuple[ProviderModel, ...],
    local_model_ids: tuple[str, ...],
    *,
    select: Callable[[str, tuple[str, ...]], str | None],
    text_input: Callable[[str, str], str | None],
    adapter: ProviderAdapter | None,
    api_key: str | None,
    display: Callable[[str], None] | None,
    client: Any | None,
) -> tuple[str, tuple[ProviderModel, ...]]:
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
        # Plan 182 F2: present a curated pick-list (RAM-recommended + popular) instead of a
        # blank text box; "Type an exact name…" remains for an unlisted tag.
        requested_model = _select_ollama_model_to_pull(
            select, text_input, local_model_ids, _detect_system_ram_gib(),
        )
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


def _select_ollama_model_to_pull(
    select: Callable[[str, tuple[str, ...]], str | None],
    text_input: Callable[[str, str], str | None],
    installed_ids: tuple[str, ...],
    ram_gib: int | None,
) -> str:
    """Plan 182 F2: a curated pick-list of pullable Ollama models (RAM-recommended first, then
    popular), excluding already-installed, plus a 'type an exact name' fallback. Returns the
    chosen/typed model id, or '' when cancelled."""
    seen = set(installed_ids)
    options: list[str] = []
    label_to_id: dict[str, str] = {}
    for model_id in _recommended_ollama_models(ram_gib):
        if model_id in seen:
            continue
        seen.add(model_id)
        label = f"{model_id}  [recommended for your RAM]"
        options.append(label)
        label_to_id[label] = model_id
    for model_id, desc in OLLAMA_POPULAR_MODELS:
        if model_id in seen:
            continue
        seen.add(model_id)
        label = f"{model_id}  — {desc}"
        options.append(label)
        label_to_id[label] = model_id
    options.append(OLLAMA_PULL_TYPE_CUSTOM_CHOICE)
    choice = select("Pick a model to pull (or type an exact name):", tuple(options))
    if choice is None:
        return ""
    choice = choice.strip()
    if choice == OLLAMA_PULL_TYPE_CUSTOM_CHOICE:
        return (text_input("Enter an Ollama model to pull:", "") or "").strip()
    return label_to_id.get(choice, "")


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


def _ollama_line_has_percent(line: str) -> bool:
    """True when a line carries a ``<digits>%`` token — a real download-progress line
    (as opposed to a phase word like ``pulling manifest`` / ``verifying``)."""
    for index, char in enumerate(line):
        if char == "%" and index > 0 and line[index - 1].isdigit():
            return True
    return False


def _run_ollama_pull_subprocess(
    model_name: str,
    *,
    display: Callable[[str], None],
) -> None:
    # Plan 186 F1a: show ONE clean progress bar, not a wall of stacked lines.
    # `ollama pull` streams phase words (`pulling manifest`, a bare `pulling`,
    # `verifying`, `writing manifest`, `removing`) plus periodic download-progress
    # lines. We forward ONLY the download-progress lines (a `NN%` token) — the TUI
    # collapses those into the single activity bar — and swallow the phase noise so
    # it never stacks in chat. A single confirmation is emitted on success.
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
    for raw in process.stdout:
        line = raw.rstrip()
        if not line:
            continue
        if _ollama_line_has_percent(line):
            display(line)
        # else: swallow the phase/noise line so it doesn't stack in chat.
    exit_code = process.wait()
    if exit_code != 0:
        raise OnboardingError(f"`ollama pull {model_name}` failed with exit code {exit_code}.")
    display(f"✓ {model_name} downloaded and ready.")


def _run_ollama_rm_subprocess(model_name: str, *, display: Callable[[str], None]) -> None:
    """Plan 186 F1b: remove an installed Ollama model (`ollama rm <model>`) to free disk."""
    try:
        result = subprocess.run(
            ["ollama", "rm", model_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except OSError as exc:
        raise OnboardingError(f"Failed to run `ollama rm {model_name}`: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stdout or "").strip()
        raise OnboardingError(
            f"`ollama rm {model_name}` failed"
            + (f": {detail}" if detail else f" with exit code {result.returncode}.")
        )
    display(f"✓ Removed {model_name}.")


def _remove_ollama_model(
    select: Callable[[str, tuple[str, ...]], str | None],
    models: tuple[ProviderModel, ...],
    *,
    adapter: ProviderAdapter | None,
    api_key: str | None,
    display: Callable[[str], None] | None,
    client: Any | None,
) -> tuple[ProviderModel, ...]:
    """Plan 186 F1b: pick an installed model, confirm, and `ollama rm` it — then refresh the list.
    Returns the refreshed models (unchanged when cancelled or on failure)."""
    emit = display or (lambda _message: None)
    installed_ids = tuple(model.id for model in models)
    if not installed_ids:
        emit("No installed Ollama models to remove.")
        return models
    target = select("Remove which installed model?", RUNTIME_MODEL_CANCEL_CHOICES + installed_ids)
    if target is None or target.strip() in RUNTIME_MODEL_CANCEL_CHOICES:
        emit("Model removal cancelled.")
        return models
    target = target.strip()
    if target not in installed_ids:
        emit("Model removal cancelled.")
        return models
    confirm = select(
        f"Remove {target}? This deletes it from disk (you can re-pull it later).",
        ("Yes, remove it", "Cancel"),
    )
    if (confirm or "").strip() != "Yes, remove it":
        emit("Model removal cancelled.")
        return models
    _run_ollama_rm_subprocess(target, display=emit)
    # Refresh the installed-model list so the menu reflects the removal.
    if adapter is not None:
        try:
            validation = adapter.validate_api_key(api_key, client=client)
            if getattr(validation, "ok", False):
                return _sorted_models(validation.models)
        except Exception:
            pass
    return tuple(model for model in models if model.id != target)


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
        failure_window_hours=current.failure_window_hours,
        font_setup_acknowledged=current.font_setup_acknowledged,
        plan_mode_enabled=current.plan_mode_enabled,
        plan_precheck=current.plan_precheck,
        duckln_ui=current.duckln_ui,
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
    # Plan 121: confirm the connection with a real round-trip and SHOW the reply —
    # don't apply the change unless the model actually answers.
    if not verify_live_reply(
        provider=provider, model=model, api_key=api_key,
        base_url=base_url, display=display, client=client,
    ):
        display(f"{provider.label} accepted the key but did not return a live reply — keeping your current provider.")
        return current
    try:
        from duckln.connection_status import record_live_verified

        record_live_verified(paths.config_dir, provider=provider, model=model)
    except Exception:
        pass
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
        failure_window_hours=current.failure_window_hours,
        font_setup_acknowledged=current.font_setup_acknowledged,
        plan_mode_enabled=current.plan_mode_enabled,
        plan_precheck=current.plan_precheck,
        duckln_ui=current.duckln_ui,
    )
    save_app_config(updated, paths)
    # Plan 167 F3: persist the model's real context window (from the provider's model list)
    # so the token counter's `ctx %` uses the actual context, not the family-table guess.
    try:
        from duckln.ai_client import persist_model_context_window

        persist_model_context_window(paths.config_dir, models, model)
    except Exception:
        pass
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
    # Plan 182 F1: prove the model actually GENERATES with a quick test message — the same live
    # round-trip onboarding and /provider do — not just that it's LISTED (validate_model is only a
    # metadata check). Provider/location-agnostic (local Ollama / remote / any cloud). Only report
    # "ready" when the model truly answers.
    if not verify_live_reply(
        provider=current.provider,
        model=model,
        api_key=current.api_key,
        base_url=_provider_base_url(current.provider, base_url=current.base_url),
        display=display,
        client=client,
    ):
        display(
            f"Retryable error: {current.provider.label} accepted '{model}' but it did not return a "
            "live reply — keeping the current model."
        )
        return current
    # Plan 182 F4: record that this model truly answered, so the header shows real green.
    try:
        from duckln.connection_status import record_live_verified

        record_live_verified(paths.config_dir, provider=current.provider, model=model)
    except Exception:
        pass
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
        failure_window_hours=current.failure_window_hours,
        font_setup_acknowledged=current.font_setup_acknowledged,
        plan_mode_enabled=current.plan_mode_enabled,
        plan_precheck=current.plan_precheck,
        duckln_ui=current.duckln_ui,
    )
    save_app_config(updated, paths)
    # Plan 167 F3: persist the chosen model's real context window for the token counter.
    try:
        from duckln.ai_client import persist_model_context_window

        persist_model_context_window(paths.config_dir, models, model)
    except Exception:
        pass
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
            "Cancel",
        ),
    )
    if choice is None or choice == "Cancel":
        display("No configuration change selected.")
        return current
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
        return duckln_select(prompt, choices)
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
        elif key == "plan_mode_enabled":
            payload[key] = _parse_optional_bool(state_values[key])
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
        elif key == "plan_mode_enabled":
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


def _parse_plan_precheck(value: Any) -> str:
    """Plan 76: normalize plan_precheck to one of ask|on|off (default ask)."""
    v = str(value or "").strip().lower()
    return v if v in {"ask", "on", "off"} else "ask"


def _parse_duckln_ui(value: Any) -> str:
    """Plan 80 Fix 4: normalize duckln_ui to one of auto|inline|full (default auto)."""
    v = str(value or "").strip().lower()
    return v if v in {"auto", "inline", "full"} else "auto"


def _parse_optional_bool_default_true(value: Any) -> bool:
    """Like `_parse_optional_bool` but an ABSENT value (None) resolves to True.

    Used for `plan_mode_enabled` so fresh/legacy configs default Plan Mode ON
    while an explicitly stored ``false`` is still honoured."""
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
