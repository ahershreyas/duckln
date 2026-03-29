"""Provider adapter interfaces and implementations for Duckln."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from duckln.logging_utils import log_provider_failure


DEFAULT_TIMEOUT_SECONDS = 10.0
ANTHROPIC_VERSION = "2023-06-01"


class Provider(str, Enum):
    """Supported Duckln providers for first-run onboarding."""

    OPENROUTER = "openrouter"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"

    @property
    def label(self) -> str:
        labels = {
            Provider.OPENROUTER: "OpenRouter",
            Provider.OPENAI: "OpenAI",
            Provider.ANTHROPIC: "Anthropic",
        }
        return labels[self]

    @property
    def api_key_name(self) -> str:
        names = {
            Provider.OPENROUTER: "OpenRouter API key",
            Provider.OPENAI: "OpenAI API key",
            Provider.ANTHROPIC: "Anthropic API key",
        }
        return names[self]


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

    def build_headers(self, api_key: str) -> dict[str, str]:
        raise NotImplementedError

    def parse_models(self, payload: dict[str, Any]) -> tuple[ProviderModel, ...]:
        raise NotImplementedError

    def models_url(self) -> str:
        return f"{self.base_url}/models"

    def list_models(
        self,
        api_key: str,
        *,
        client: HttpClient | None = None,
        model_id: str | None = None,
    ) -> tuple[ProviderModel, ...]:
        try:
            response = _perform_get(
                self.models_url(),
                headers=self.build_headers(api_key),
                client=client,
            )
        except Exception as exc:
            public_message = f"Failed to connect to {self.provider.label}. Please retry."
            log_message = (
                f"{public_message} "
                f"url={self.models_url()} timeout={DEFAULT_TIMEOUT_SECONDS}s "
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
        api_key: str,
        *,
        client: HttpClient | None = None,
    ) -> ProviderValidationResult:
        if not api_key.strip():
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
        api_key: str,
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


class OpenRouterAdapter(ProviderAdapter):
    """Provider adapter for OpenRouter."""

    provider = Provider.OPENROUTER
    base_url = "https://openrouter.ai/api/v1"

    def build_headers(self, api_key: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key}",
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

    def build_headers(self, api_key: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key}",
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

    def build_headers(self, api_key: str) -> dict[str, str]:
        return {
            "x-api-key": api_key,
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


def get_provider_adapter(provider: Provider) -> ProviderAdapter:
    """Return the adapter for a configured provider."""

    adapters: dict[Provider, ProviderAdapter] = {
        Provider.OPENROUTER: OpenRouterAdapter(),
        Provider.OPENAI: OpenAIAdapter(),
        Provider.ANTHROPIC: AnthropicAdapter(),
    }
    return adapters[provider]


def _perform_get(
    url: str,
    *,
    headers: dict[str, str],
    client: HttpClient | None,
) -> HttpResponse:
    if client is not None:
        return client.get(url, headers=headers, timeout=DEFAULT_TIMEOUT_SECONDS)

    try:
        import httpx
    except ModuleNotFoundError as exc:
        raise RuntimeError("httpx is required for live provider validation.") from exc

    with httpx.Client() as http_client:
        return http_client.get(url, headers=headers, timeout=DEFAULT_TIMEOUT_SECONDS)
