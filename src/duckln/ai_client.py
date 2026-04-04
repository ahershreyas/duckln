"""Provider adapter interfaces and implementations for Duckln."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import shutil
from typing import Any, Protocol

from duckln.logging_utils import log_provider_failure


DEFAULT_TIMEOUT_SECONDS = 10.0
OLLAMA_TIMEOUT_SECONDS = 2.0
OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434"
ANTHROPIC_VERSION = "2023-06-01"


class Provider(str, Enum):
    """Supported Duckln providers for first-run onboarding."""

    OPENROUTER = "openrouter"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"

    @property
    def label(self) -> str:
        labels = {
            Provider.OPENROUTER: "OpenRouter",
            Provider.OPENAI: "OpenAI",
            Provider.ANTHROPIC: "Anthropic",
            Provider.OLLAMA: "Ollama",
        }
        return labels[self]

    @property
    def api_key_name(self) -> str:
        names = {
            Provider.OPENROUTER: "OpenRouter API key",
            Provider.OPENAI: "OpenAI API key",
            Provider.ANTHROPIC: "Anthropic API key",
            Provider.OLLAMA: "Ollama local runtime",
        }
        return names[self]

    @property
    def requires_api_key(self) -> bool:
        return self is not Provider.OLLAMA


@dataclass(frozen=True)
class ProviderModel:
    """A provider model available for selection during onboarding."""

    id: str
    display_name: str


@dataclass(frozen=True)
class ProviderValidationResult:
    """Result of validating provider connectivity or a chosen model."""

    ok: bool
    message: str
    models: tuple[ProviderModel, ...] = ()


@dataclass(frozen=True)
class ProviderRequestError(ValueError):
    """Bounded provider request failure with separate public/log messages."""

    public_message: str
    log_message: str
    status_code: int | None = None


class HttpResponse(Protocol):
    """Minimal response contract used by provider adapters."""

    status_code: int
    text: str

    def json(self) -> Any:
        """Return a parsed JSON response."""


class HttpClient(Protocol):
    """Minimal HTTP client contract used by provider adapters."""

    def get(self, url: str, *, headers: dict[str, str], timeout: float) -> HttpResponse:
        """Perform a GET request."""


class ProviderAdapter:
    """Shared behavior for provider onboarding adapters."""

    provider: Provider
    base_url: str
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    def build_headers(self, api_key: str | None) -> dict[str, str]:
        raise NotImplementedError

    def parse_models(self, payload: dict[str, Any]) -> tuple[ProviderModel, ...]:
        raise NotImplementedError

    def models_url(self) -> str:
        return f"{self.base_url}/models"

    def list_models(
        self,
        api_key: str | None,
        *,
        client: HttpClient | None = None,
        model_id: str | None = None,
    ) -> tuple[ProviderModel, ...]:
        try:
            response = _perform_get(
                self.models_url(),
                headers=self.build_headers(api_key),
                client=client,
                timeout_seconds=self.timeout_seconds,
            )
        except Exception as exc:
            public_message = f"Failed to connect to {self.provider.label}. Please retry."
            log_message = (
                f"{public_message} "
                f"url={self.models_url()} timeout={self.timeout_seconds}s "
                f"error={type(exc).__name__}: {exc}"
            )
            log_provider_failure(
                provider=self.provider.value,
                model=model_id,
                status_code=None,
                message=log_message,
            )
            raise ProviderRequestError(public_message, log_message) from exc

        if response.status_code >= 400:
            message = (
                f"Invalid {self.provider.api_key_name.lower()} or provider error "
                f"(HTTP {response.status_code}). Please retry."
            )
            log_provider_failure(
                provider=self.provider.value,
                model=model_id,
                status_code=response.status_code,
                message=message,
            )
            raise ProviderRequestError(message, message, status_code=response.status_code)

        try:
            payload = response.json()
        except Exception as exc:
            public_message = f"{self.provider.label} returned an unreadable response. Please retry."
            log_message = (
                f"{public_message} "
                f"url={self.models_url()} status={response.status_code} "
                f"error={type(exc).__name__}: {exc}"
            )
            log_provider_failure(
                provider=self.provider.value,
                model=model_id,
                status_code=response.status_code,
                message=log_message,
            )
            raise ProviderRequestError(public_message, log_message, status_code=response.status_code) from exc
        models = self.parse_models(payload)
        if not models:
            message = f"{self.provider.label} returned an empty model list. Please retry."
            log_provider_failure(
                provider=self.provider.value,
                model=model_id,
                status_code=response.status_code,
                message=message,
            )
            raise ProviderRequestError(message, message, status_code=response.status_code)
        return models

    def validate_api_key(
        self,
        api_key: str | None,
        *,
        client: HttpClient | None = None,
    ) -> ProviderValidationResult:
        if self.provider.requires_api_key and not (api_key or "").strip():
            return ProviderValidationResult(ok=False, message=f"{self.provider.api_key_name} is required.")

        try:
            models = self.list_models(api_key, client=client)
        except ProviderRequestError as exc:
            return ProviderValidationResult(ok=False, message=exc.public_message)
        except ValueError as exc:
            return ProviderValidationResult(ok=False, message=str(exc))

        return ProviderValidationResult(
            ok=True,
            message=f"{self.provider.label} connection verified.",
            models=models,
        )

    def validate_model(
        self,
        api_key: str | None,
        model_id: str,
        *,
        client: HttpClient | None = None,
        models: tuple[ProviderModel, ...] | None = None,
    ) -> ProviderValidationResult:
        if not model_id.strip():
            return ProviderValidationResult(ok=False, message="A model selection is required.")

        available_models = models or self.list_models(api_key, client=client, model_id=model_id)
        model_ids = {model.id for model in available_models}
        if model_id not in model_ids:
            log_provider_failure(
                provider=self.provider.value,
                model=model_id,
                status_code=None,
                message=f"{model_id} is not available for {self.provider.label}.",
            )
            return ProviderValidationResult(
                ok=False,
                message=f"{model_id} is not available for {self.provider.label}.",
                models=available_models,
            )

        return ProviderValidationResult(
            ok=True,
            message=f"{model_id} is available for {self.provider.label}.",
            models=available_models,
        )

    def pull_model(
        self,
        api_key: str | None,
        model_id: str,
        *,
        client: HttpClient | None = None,
    ) -> ProviderValidationResult:
        return ProviderValidationResult(ok=False, message=f"Pulling a new model is not supported for {self.provider.label}.")


class OpenRouterAdapter(ProviderAdapter):
    """Provider adapter for OpenRouter."""

    provider = Provider.OPENROUTER
    base_url = "https://openrouter.ai/api/v1"

    def build_headers(self, api_key: str | None) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key or ''}",
            "Accept": "application/json",
        }

    def parse_models(self, payload: dict[str, Any]) -> tuple[ProviderModel, ...]:
        return tuple(
            ProviderModel(
                id=item["id"],
                display_name=item.get("name") or item["id"],
            )
            for item in payload.get("data", [])
            if item.get("id")
        )


class OpenAIAdapter(ProviderAdapter):
    """Provider adapter for OpenAI."""

    provider = Provider.OPENAI
    base_url = "https://api.openai.com/v1"

    def build_headers(self, api_key: str | None) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key or ''}",
            "Accept": "application/json",
        }

    def parse_models(self, payload: dict[str, Any]) -> tuple[ProviderModel, ...]:
        return tuple(
            ProviderModel(id=item["id"], display_name=item["id"])
            for item in payload.get("data", [])
            if item.get("id")
        )


class AnthropicAdapter(ProviderAdapter):
    """Provider adapter for Anthropic."""

    provider = Provider.ANTHROPIC
    base_url = "https://api.anthropic.com/v1"

    def build_headers(self, api_key: str | None) -> dict[str, str]:
        return {
            "x-api-key": api_key or "",
            "anthropic-version": ANTHROPIC_VERSION,
            "Accept": "application/json",
        }

    def parse_models(self, payload: dict[str, Any]) -> tuple[ProviderModel, ...]:
        return tuple(
            ProviderModel(
                id=item["id"],
                display_name=item.get("display_name") or item["id"],
            )
            for item in payload.get("data", [])
            if item.get("id")
        )


class OllamaAdapter(ProviderAdapter):
    """Provider adapter for local Ollama."""

    provider = Provider.OLLAMA
    timeout_seconds = OLLAMA_TIMEOUT_SECONDS

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = normalize_ollama_base_url(base_url)

    def build_headers(self, api_key: str | None) -> dict[str, str]:
        return {
            "Accept": "application/json",
        }

    def models_url(self) -> str:
        return f"{self.base_url}/api/tags"

    def list_models(
        self,
        api_key: str | None,
        *,
        client: HttpClient | None = None,
        model_id: str | None = None,
    ) -> tuple[ProviderModel, ...]:
        try:
            response = _perform_get(
                self.models_url(),
                headers=self.build_headers(None),
                client=client,
                timeout_seconds=self.timeout_seconds,
            )
        except Exception as exc:
            public_message = f"Failed to connect to {self.provider.label}. Please retry."
            log_message = (
                f"{public_message} "
                    f"url={self.models_url()} timeout={self.timeout_seconds}s "
                f"error={type(exc).__name__}: {exc}"
            )
            log_provider_failure(
                provider=self.provider.value,
                model=model_id,
                status_code=None,
                message=log_message,
            )
            raise ProviderRequestError(public_message, log_message) from exc

        if response.status_code >= 400:
            message = f"Ollama returned HTTP {response.status_code}. Please retry."
            log_provider_failure(
                provider=self.provider.value,
                model=model_id,
                status_code=response.status_code,
                message=message,
            )
            raise ProviderRequestError(message, message, status_code=response.status_code)

        return self.parse_models(response.json())

    def parse_models(self, payload: dict[str, Any]) -> tuple[ProviderModel, ...]:
        return tuple(
            ProviderModel(
                id=item["name"],
                display_name=item["name"],
            )
            for item in payload.get("models", [])
            if item.get("name")
        )

    def validate_api_key(
        self,
        api_key: str | None,
        *,
        client: HttpClient | None = None,
    ) -> ProviderValidationResult:
        try:
            models = self.list_models(None, client=client)
        except ProviderRequestError:
            if shutil.which("ollama") is not None:
                return ProviderValidationResult(
                    ok=False,
                    message=(
                        f"Ollama is installed but not running at {self.models_url()}. "
                        "Start it with `ollama serve`, then retry detection."
                    ),
                )
            return ProviderValidationResult(
                ok=False,
                message=(
                    f"Ollama is not installed or not reachable at {self.models_url()}. "
                    f"{_ollama_install_guidance()}"
                ),
            )
        return ProviderValidationResult(
            ok=True,
            message=f"Ollama is reachable at {self.models_url()}.",
            models=models,
        )

    def pull_model(
        self,
        api_key: str | None,
        model_id: str,
        *,
        client: HttpClient | None = None,
    ) -> ProviderValidationResult:
        if not model_id.strip():
            return ProviderValidationResult(ok=False, message="An Ollama model name is required.")

        try:
            response = _perform_post(
                f"{self.base_url}/api/pull",
                headers=self.build_headers(None),
                json_payload={"name": model_id, "stream": False},
                client=client,
                timeout_seconds=self.timeout_seconds,
            )
        except Exception as exc:
            return ProviderValidationResult(
                ok=False,
                message=f"Failed to request ollama pull {model_id}. Please retry. ({type(exc).__name__}: {exc})",
            )

        if response.status_code >= 400:
            return ProviderValidationResult(
                ok=False,
                message=f"Ollama pull failed for {model_id} (HTTP {response.status_code}).",
            )

        refreshed = self.validate_api_key(None, client=client)
        if refreshed.ok and model_id not in {model.id for model in refreshed.models}:
            return ProviderValidationResult(
                ok=False,
                message=f"Ollama accepted pull for {model_id}, but the model is not listed locally yet. Retry detection.",
                models=refreshed.models,
            )
        return ProviderValidationResult(
            ok=refreshed.ok,
            message=f"Ollama model {model_id} is ready locally." if refreshed.ok else refreshed.message,
            models=refreshed.models,
        )


def get_provider_adapter(provider: Provider) -> ProviderAdapter:
    """Return the adapter for a configured provider."""

    return get_provider_adapter_for_base_url(provider, base_url=None)


def get_provider_adapter_for_base_url(
    provider: Provider,
    *,
    base_url: str | None,
) -> ProviderAdapter:
    """Return a provider adapter, preserving custom Ollama base URLs."""

    adapters: dict[Provider, ProviderAdapter] = {
        Provider.OPENROUTER: OpenRouterAdapter(),
        Provider.OPENAI: OpenAIAdapter(),
        Provider.ANTHROPIC: AnthropicAdapter(),
        Provider.OLLAMA: OllamaAdapter(base_url=base_url),
    }
    return adapters[provider]


def normalize_ollama_base_url(base_url: str | None) -> str:
    """Normalize a user-entered Ollama URL to a stable host root."""

    normalized = (base_url or OLLAMA_DEFAULT_BASE_URL).strip().rstrip("/")
    if normalized.endswith("/api/tags"):
        normalized = normalized[: -len("/api/tags")]
    elif normalized.endswith("/api"):
        normalized = normalized[: -len("/api")]
    return normalized or OLLAMA_DEFAULT_BASE_URL


def _perform_get(
    url: str,
    *,
    headers: dict[str, str],
    client: HttpClient | None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> HttpResponse:
    if client is not None:
        return client.get(url, headers=headers, timeout=timeout_seconds)

    try:
        import httpx
    except ModuleNotFoundError as exc:
        raise RuntimeError("httpx is required for live provider validation.") from exc

    with httpx.Client() as http_client:
        return http_client.get(url, headers=headers, timeout=timeout_seconds)


def _perform_post(
    url: str,
    *,
    headers: dict[str, str],
    json_payload: dict[str, Any],
    client: HttpClient | None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> HttpResponse:
    if client is not None and hasattr(client, "post"):
        return client.post(url, headers=headers, json=json_payload, timeout=timeout_seconds)

    try:
        import httpx
    except ModuleNotFoundError as exc:
        raise RuntimeError("httpx is required for live provider validation.") from exc

    with httpx.Client() as http_client:
        return http_client.post(url, headers=headers, json=json_payload, timeout=timeout_seconds)


def _ollama_install_guidance() -> str:
    import platform

    system_name = platform.system()
    if system_name == "Darwin":
        return "Install Ollama from https://ollama.com/download and start it with `ollama serve`."
    if system_name == "Linux":
        return "Install Ollama from https://ollama.com/download/linux and start it with `ollama serve`."
    if system_name == "Windows":
        return "Install Ollama from https://ollama.com/download/windows and start the Ollama app."
    return "Install Ollama from https://ollama.com/download and start the local Ollama service."
