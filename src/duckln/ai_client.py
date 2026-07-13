"""Provider adapter interfaces and implementations for Duckln."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import shutil
import time
from typing import Any, Protocol

from duckln.logging_utils import log_provider_failure
from duckln.usage_meter import record_provider_usage


DEFAULT_TIMEOUT_SECONDS = 10.0
OLLAMA_TIMEOUT_SECONDS = 2.0
# Plan 69: conversation/generation needs a far larger ceiling than the
# model-list GET. A 2s (Ollama) / 10s (cloud) timeout is fine for listing
# models but times out real completions — local models cold-load into memory
# and generate JSON from large prompts. These are used ONLY for the
# conversation POST, never for the fast tags/models GET.
CONVERSATION_TIMEOUT_SECONDS = 120.0
OLLAMA_CONVERSATION_TIMEOUT_SECONDS = 300.0
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
    # Plan 167 F3: the model's real context window when the provider's model list exposes it
    # (e.g. OpenRouter `context_length`); 0 = unknown → fall back to the family-table guess.
    context_length: int = 0


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

    def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any], timeout: float) -> HttpResponse:
        """Perform a POST request."""


class ProviderAdapter:
    """Shared behavior for provider onboarding adapters."""

    provider: Provider
    base_url: str
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    # Plan 69: generous timeout used ONLY for the conversation POST.
    conversation_timeout_seconds: float = CONVERSATION_TIMEOUT_SECONDS

    def build_headers(self, api_key: str | None) -> dict[str, str]:
        raise NotImplementedError

    def parse_models(self, payload: dict[str, Any]) -> tuple[ProviderModel, ...]:
        raise NotImplementedError

    def models_url(self) -> str:
        return f"{self.base_url}/models"

    def conversation_url(self) -> str | None:
        return None

    def build_conversation_payload(
        self,
        *,
        model_id: str,
        system_prompt: str,
        user_message: str,
        recent_turns: tuple[tuple[str, str], ...],
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def parse_conversation_text(self, payload: dict[str, Any]) -> str:
        raise NotImplementedError

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

    def _error_hint(self, body: str, model_id: str) -> str:
        """Plan 165 F2: a provider-specific, actionable hint derived from the error body.
        Base = none; OllamaAdapter overrides it (OOM / model-not-found)."""
        return ""

    def generate_reply(
        self,
        api_key: str | None,
        *,
        model_id: str,
        system_prompt: str,
        user_message: str,
        recent_turns: tuple[tuple[str, str], ...] = (),
        client: HttpClient | None = None,
        config_dir=None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> str:
        conversation_url = self.conversation_url()
        if conversation_url is None:
            raise ProviderRequestError(
                f"Live conversation is not supported for {self.provider.label}.",
                f"conversation unsupported for provider={self.provider.value}",
            )

        json_payload = self.build_conversation_payload(
            model_id=model_id,
            system_prompt=system_prompt,
            user_message=user_message,
            recent_turns=recent_turns,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )
        headers = self.build_headers(api_key)
        # Plan 69: use the generous conversation timeout (not the fast
        # model-list timeout), and retry once on a transport error — local
        # models frequently time out on the first call while cold-loading.
        # Plan 122: retry on a transport error AND on a transient HTTP 429/503
        # (rate-limit / overloaded) with bounded backoff — so a momentary limit
        # doesn't turn into "no usable fix". Backoff is capped so the UI never hangs.
        response = None
        last_exc: Exception | None = None
        for attempt in range(_CONVERSATION_MAX_ATTEMPTS):
            try:
                response = _perform_post(
                    conversation_url,
                    headers=headers,
                    json_payload=json_payload,
                    client=client,
                    timeout_seconds=self.conversation_timeout_seconds,
                )
            except Exception as exc:
                last_exc = exc
                response = None
                if attempt < _CONVERSATION_MAX_ATTEMPTS - 1:
                    time.sleep(_backoff_seconds(attempt))
                    continue
                break
            if response.status_code in _RATE_LIMIT_STATUSES and attempt < _CONVERSATION_MAX_ATTEMPTS - 1:
                time.sleep(_retry_after_seconds(response) or _backoff_seconds(attempt))
                continue
            break
        if response is None:
            public_message = f"Failed to get a reply from {self.provider.label}. Please retry."
            log_message = (
                f"{public_message} "
                f"url={conversation_url} timeout={self.conversation_timeout_seconds}s "
                f"error={type(last_exc).__name__}: {last_exc}"
            )
            log_provider_failure(
                provider=self.provider.value,
                model=model_id,
                status_code=None,
                message=log_message,
            )
            raise ProviderRequestError(public_message, log_message) from last_exc

        if response.status_code in _RATE_LIMIT_STATUSES:
            message = (
                f"{self.provider.label} is rate-limited or over quota (HTTP {response.status_code}) — "
                "wait and retry, or check your plan/billing."
            )
            log_provider_failure(
                provider=self.provider.value,
                model=model_id,
                status_code=response.status_code,
                message=message,
            )
            raise ProviderRequestError(message, message, status_code=response.status_code)

        if response.status_code >= 400:
            # Plan 165 F1: surface the provider's REAL error body (Ollama/OpenAI carry the
            # actual cause — model-not-found / out-of-memory / template) instead of a generic
            # "please retry". F2: a provider-specific actionable hint when we recognize it.
            body = _extract_error_body(response)
            hint = self._error_hint(body, model_id)
            tail = f": {body}" if body else ". Please retry."
            message = f"{self.provider.label} could not generate a reply (HTTP {response.status_code}){tail}"
            if hint:
                message = f"{message} {hint}"
            log_provider_failure(
                provider=self.provider.value,
                model=model_id,
                status_code=response.status_code,
                message=message,
            )
            raise ProviderRequestError(message, message, status_code=response.status_code)

        try:
            payload = response.json()
            text = self.parse_conversation_text(payload).strip()
            record_provider_usage(
                provider=self.provider.value,
                model=model_id,
                payload=payload,
                config_dir=config_dir,
            )
        except Exception as exc:
            public_message = f"{self.provider.label} returned an unreadable conversation response. Please retry."
            log_message = (
                f"{public_message} "
                f"url={conversation_url} status={response.status_code} "
                f"error={type(exc).__name__}: {exc}"
            )
            log_provider_failure(
                provider=self.provider.value,
                model=model_id,
                status_code=response.status_code,
                message=log_message,
            )
            raise ProviderRequestError(public_message, log_message, status_code=response.status_code) from exc

        if not text:
            message = f"{self.provider.label} returned an empty conversation response. Please retry."
            log_provider_failure(
                provider=self.provider.value,
                model=model_id,
                status_code=response.status_code,
                message=message,
            )
            raise ProviderRequestError(message, message, status_code=response.status_code)
        return text


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
                context_length=_safe_context_length(item.get("context_length")),
            )
            for item in payload.get("data", [])
            if item.get("id")
        )

    def conversation_url(self) -> str:
        return f"{self.base_url}/chat/completions"

    def build_conversation_payload(
        self,
        *,
        model_id: str,
        system_prompt: str,
        user_message: str,
        recent_turns: tuple[tuple[str, str], ...],
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> dict[str, Any]:
        return _build_openai_style_conversation_payload(
            model_id=model_id,
            system_prompt=system_prompt,
            user_message=user_message,
            recent_turns=recent_turns,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )

    def parse_conversation_text(self, payload: dict[str, Any]) -> str:
        return _parse_openai_style_conversation_text(payload)


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

    def conversation_url(self) -> str:
        return f"{self.base_url}/chat/completions"

    def build_conversation_payload(
        self,
        *,
        model_id: str,
        system_prompt: str,
        user_message: str,
        recent_turns: tuple[tuple[str, str], ...],
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> dict[str, Any]:
        return _build_openai_style_conversation_payload(
            model_id=model_id,
            system_prompt=system_prompt,
            user_message=user_message,
            recent_turns=recent_turns,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )

    def parse_conversation_text(self, payload: dict[str, Any]) -> str:
        return _parse_openai_style_conversation_text(payload)


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

    def conversation_url(self) -> str:
        return f"{self.base_url}/messages"

    def build_conversation_payload(
        self,
        *,
        model_id: str,
        system_prompt: str,
        user_message: str,
        recent_turns: tuple[tuple[str, str], ...],
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> dict[str, Any]:
        messages = [
            {"role": role, "content": content}
            for role, content in recent_turns
            if role in {"user", "assistant"}
        ]
        messages.append({"role": "user", "content": user_message})
        # Plan 119: Anthropic has no response_format; a larger budget + the
        # strict JSON prompt + parse-side truncation repair carry json_mode.
        return {
            "model": model_id,
            "system": system_prompt,
            "max_tokens": max_tokens or 220,
            "messages": messages,
        }

    def parse_conversation_text(self, payload: dict[str, Any]) -> str:
        blocks = payload.get("content", [])
        parts = [block.get("text", "") for block in blocks if block.get("type") == "text"]
        return "".join(parts).strip()


class OllamaAdapter(ProviderAdapter):
    """Provider adapter for local Ollama."""

    provider = Provider.OLLAMA
    timeout_seconds = OLLAMA_TIMEOUT_SECONDS
    conversation_timeout_seconds = OLLAMA_CONVERSATION_TIMEOUT_SECONDS

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = normalize_ollama_base_url(base_url)
        # Plan 165 F3: default to /api/generate; flipped to /api/chat only for the one-shot
        # fallback when a generate call returns a 5xx (what `ollama run` uses).
        self._use_chat = False

    def build_headers(self, api_key: str | None) -> dict[str, str]:
        return {
            "Accept": "application/json",
        }

    def _error_hint(self, body: str, model_id: str) -> str:
        return _ollama_error_hint(body, model_id)

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

    def conversation_url(self) -> str:
        # Plan 165 F3: /api/chat only during the one-shot fallback; /api/generate by default.
        return f"{self.base_url}/api/chat" if self._use_chat else f"{self.base_url}/api/generate"

    def build_conversation_payload(
        self,
        *,
        model_id: str,
        system_prompt: str,
        user_message: str,
        recent_turns: tuple[tuple[str, str], ...],
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> dict[str, Any]:
        if self._use_chat:
            # Plan 165 F3: the messages shape `ollama run` uses (/api/chat).
            messages: list[dict[str, str]] = []
            if system_prompt.strip():
                messages.append({"role": "system", "content": system_prompt})
            for role, content in recent_turns:
                if role in {"user", "assistant"}:
                    messages.append({"role": role, "content": content})
            messages.append({"role": "user", "content": user_message})
            payload: dict[str, Any] = {"model": model_id, "messages": messages, "stream": False}
            if json_mode:
                payload["format"] = "json"
            if max_tokens:
                payload["options"] = {"num_predict": int(max_tokens)}
            return payload
        transcript = []
        for role, content in recent_turns:
            if role not in {"user", "assistant"}:
                continue
            transcript.append(f"{role.title()}: {content}")
        transcript.append(f"User: {user_message}")
        prompt = f"{system_prompt}\n\n" + "\n".join(transcript) + "\nAssistant:"
        payload = {"model": model_id, "prompt": prompt, "stream": False}
        if json_mode:
            # Plan 119: Ollama's native JSON-constrained decoding.
            payload["format"] = "json"
        if max_tokens:
            payload["options"] = {"num_predict": int(max_tokens)}
        return payload

    def parse_conversation_text(self, payload: dict[str, Any]) -> str:
        # Plan 165 F3: /api/chat returns {"message": {"content": ...}}; /api/generate returns
        # {"response": ...}. Read chat first, fall back to generate so either shape parses.
        message = payload.get("message")
        if isinstance(message, dict) and message.get("content"):
            return str(message["content"]).strip()
        return str(payload.get("response", "")).strip()

    def generate_reply(
        self,
        api_key: str | None,
        *,
        model_id: str,
        system_prompt: str,
        user_message: str,
        recent_turns: tuple[tuple[str, str], ...] = (),
        client: HttpClient | None = None,
        config_dir=None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> str:
        """Plan 165 F3: try /api/generate; on a 5xx server error retry ONCE via /api/chat
        (the endpoint `ollama run` uses). If chat succeeds, return it; if it also fails,
        surface the ORIGINAL generate error (which carries the real body + hint, F1/F2)."""
        kwargs = dict(
            model_id=model_id, system_prompt=system_prompt, user_message=user_message,
            recent_turns=recent_turns, client=client, config_dir=config_dir,
            max_tokens=max_tokens, json_mode=json_mode,
        )
        try:
            return super().generate_reply(api_key, **kwargs)
        except ProviderRequestError as exc:
            status = getattr(exc, "status_code", None)
            # Only a genuine server-side generate error is worth the chat fallback; a 4xx
            # (e.g. model-not-found) or transport error won't be fixed by switching endpoint.
            if self._use_chat or not status or status < 500 or status in _RATE_LIMIT_STATUSES:
                raise
            self._use_chat = True
            try:
                return super().generate_reply(api_key, **kwargs)
            except ProviderRequestError:
                raise exc  # surface the original generate error (real body + hint)
            finally:
                self._use_chat = False

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


# Plan 164 F2: best-effort context-window sizes per model FAMILY (substring match on the
# model id). A conservative default for unknown ids; a user override (config key
# `model_context_tokens`) always wins. Honest — these are heuristics, not guarantees.
_MODEL_CONTEXT_WINDOWS: tuple[tuple[str, int], ...] = (
    ("gpt-4o", 128_000), ("gpt-4.1", 128_000), ("o1", 128_000), ("o3", 128_000), ("o4", 128_000),
    ("gpt-4-turbo", 128_000), ("gpt-4", 8_192), ("gpt-3.5", 16_385),
    ("claude", 200_000),
    ("llama-3.1", 128_000), ("llama3.1", 128_000), ("llama-3.2", 128_000), ("llama-3.3", 128_000),
    ("llama-3", 8_192), ("llama3", 8_192),
    ("qwen2.5", 32_768), ("qwen-2.5", 32_768), ("qwen3", 32_768),
    ("deepseek", 64_000), ("mistral", 32_768), ("mixtral", 32_768),
    ("gemma-2", 8_192), ("gemma2", 8_192), ("gemma-3", 8_192), ("gemma-4", 8_192), ("gemma", 8_192),
    ("phi-3", 128_000), ("phi3", 128_000),
)
_DEFAULT_CONTEXT_WINDOW = 8_192


def _safe_context_length(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def persist_model_context_window(config_dir, models, model_id: str) -> None:
    """Plan 167 F3: when the selected model's real context window is known (from the
    provider's model list), persist it as `model_context_tokens` so `ctx %` uses the
    actual context, not the family-table guess. No-op when unknown."""
    if config_dir is None or not model_id:
        return
    window = 0
    for model in models or ():
        if getattr(model, "id", None) == model_id:
            window = _safe_context_length(getattr(model, "context_length", 0))
            break
    try:
        from state.access import write_config_snapshot

        # Write the known window, or clear a stale override (empty) so a model switch to one
        # with an unknown window falls back to the family-table guess rather than the old value.
        write_config_snapshot(config_dir, {"model_context_tokens": str(window) if window > 0 else ""})
    except Exception:
        pass


def model_context_window(model_id: str, *, config_dir=None) -> int:
    """Plan 164 F2: best-effort context-window size (tokens) for a model id. A user override
    (`model_context_tokens` in the config snapshot) wins; else a family-substring lookup; else
    a conservative default. 0 is never returned (so the UI always has a denominator)."""
    if config_dir is not None:
        try:
            from state.access import read_config_snapshot

            override = read_config_snapshot(config_dir).get("model_context_tokens")
            if override:
                value = int(str(override).strip())
                if value > 0:
                    return value
        except Exception:
            pass
    lowered = (model_id or "").lower()
    for needle, window in _MODEL_CONTEXT_WINDOWS:
        if needle in lowered:
            return window
    return _DEFAULT_CONTEXT_WINDOW


# --- Plan 179 Part B: Runtime Capability Adapter -----------------------------
# Decide, per MODEL CLASS (not per provider), how to elicit structured reasoning:
#   - cloud_reasoning: a capable model that follows up on truncated context and emits
#     valid structured output natively — rely on `response_format`/the provider budget,
#     do NOT force pseudo-XML or the strict "don't guess" enforcement.
#   - local: a small/local model — append the strict `<thought>` directive + the
#     smart-window "don't guess" footer so it spends compute on internal logic and
#     refuses to patch from a truncated snippet (Plan 179 Part A4).

CLOUD_REASONING = "cloud_reasoning"
LOCAL_MODEL = "local"

# Appended to a reasoning agent's system prompt ONLY for the LOCAL class.
LOCAL_REASONING_DIRECTIVE = (
    "\n\n[LOCAL MODEL DIRECTIVE]\n"
    "Before your final answer, think step by step INSIDE a single "
    "<thought>...</thought> block — symptom, what you inspected, the root cause and its "
    "mechanism, the smallest correct fix and why — then emit your action/JSON payload "
    "AFTER the closing </thought> tag. Always close the tag.\n"
    "When you are shown a truncated file snippet, you are STRICTLY FORBIDDEN from guessing "
    "a patch if the target function or variable definition is cut off — call the read tool "
    "for the next line range first."
)


def model_capability_class(model_id: str, *, config_dir=None) -> str:
    """Plan 179 B1: return ``CLOUD_REASONING`` or ``LOCAL_MODEL`` for a model id.

    Reuses the Plan-156 behavior-probe cache (via ``recovery.model_is_reasoning_capable``)
    and its name-heuristic fallback — a probed-capable model is ``cloud_reasoning``; anything
    else (probed-weak or an unknown small/local id) is ``local``."""
    try:
        from duckln.recovery import model_is_reasoning_capable

        capable = model_is_reasoning_capable(model_id or "", config_dir=config_dir)
    except Exception:
        capable = False
    return CLOUD_REASONING if capable else LOCAL_MODEL


def adapt_reasoning_prompt(system_prompt: str, model_id: str, *, config_dir=None) -> str:
    """Plan 179 B2: the centralized prompt switch for reasoning agents.

    A ``cloud_reasoning`` model gets the prompt unchanged (it produces a `<thought>` block +
    valid structured output natively from the spec body). A ``local`` model has the strict
    ``LOCAL_REASONING_DIRECTIVE`` appended so it burns local compute on the structured `<thought>`
    chain and never guesses a patch from a truncated snippet."""
    base = system_prompt or ""
    if model_capability_class(model_id, config_dir=config_dir) == LOCAL_MODEL:
        return base + LOCAL_REASONING_DIRECTIVE
    return base


def generate_provider_reply(
    provider: Provider,
    *,
    model_id: str,
    api_key: str | None,
    base_url: str | None,
    system_prompt: str,
    user_message: str,
    recent_turns: tuple[tuple[str, str], ...] = (),
    client: HttpClient | None = None,
    config_dir=None,
    max_tokens: int | None = None,
    json_mode: bool = False,
) -> str:
    """Generate one bounded conversation reply through the configured provider."""

    # Plan 164 F3: record the ESTIMATED prompt size + the model's context window BEFORE the
    # call, so the UI shows "↑~N tokens · ctx ~X%" for every caller and the size survives even
    # if the call fails/overflows. Best-effort; never blocks the call.
    try:
        from duckln.harness.context_budget import estimate_tokens as _estimate_tokens
        from duckln.usage_meter import record_call_estimate as _record_call_estimate

        _est = _estimate_tokens(f"{system_prompt}\n{user_message}")
        _record_call_estimate(
            estimated_prompt_tokens=_est,
            context_window=model_context_window(model_id, config_dir=config_dir),
        )
    except Exception:
        pass

    adapter = get_provider_adapter_for_base_url(provider, base_url=base_url)
    return adapter.generate_reply(
        api_key,
        model_id=model_id,
        system_prompt=system_prompt,
        user_message=user_message,
        recent_turns=recent_turns,
        client=client,
        config_dir=config_dir,
        max_tokens=max_tokens,
        json_mode=json_mode,
    )


def build_default_llm_client_or_none(config_dir):
    """Plan 65 Phase 6: build an ``LLMClient`` callable wrapping the
    user-configured provider, or ``None`` when no provider is configured.

    The returned callable matches the harness's ``LLMClient`` protocol
    (kwargs: ``system_prompt`` + ``user_message`` → str). Suitable for
    feeding ``run_agent`` and ``run_coordinator``.
    """
    try:
        from duckln.config import ConfigPaths, load_app_config
        from pathlib import Path as _Path
        paths = ConfigPaths(
            config_dir=_Path(config_dir),
            config_file=_Path(config_dir) / "config.json",
        )
        current = load_app_config(paths)
    except Exception:
        return None
    if current is None or not getattr(current, "model", None):
        return None

    def _client(
        *,
        system_prompt: str,
        user_message: str,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> str:
        # Plan 119: forward optional structured-output controls so callers like
        # fix-attribution can request JSON mode + a larger token budget.
        return generate_provider_reply(
            current.provider,
            model_id=current.model,
            api_key=current.api_key,
            base_url=current.base_url,
            system_prompt=system_prompt,
            user_message=user_message,
            config_dir=config_dir,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )

    return _client


# --- Plan 179 Part C: per-agent model routing (active_routing) ----------------

# The agent ROLES the two-tier config can assign distinct models to (the spec's nodes).
# The agent ROLES the two-tier config assigns distinct models to. Plan 181 gave WEB_READER a
# real consumer (the web-reader step that distills a fetched page in web.fetch / web-evidence),
# so it routes a genuine model — point it at a cheap/fast model to save tokens on long pages
# while the main agents keep a stronger model.
AGENT_ROLES: tuple[str, ...] = ("SUPERVISOR", "REPO_AGENT", "ERROR_AGENT", "WEB_READER")


def read_active_routing(config_dir) -> dict[str, str]:
    """Plan 179 C1: the `active_routing` map (ROLE → model id) from the config snapshot.
    Empty = Unified Mode (every role uses the single global model)."""
    try:
        import json as _json
        from state.access import read_config_snapshot

        raw = read_config_snapshot(config_dir).get("active_routing") or "{}"
        parsed = _json.loads(raw)
        return {str(k).upper(): str(v) for k, v in parsed.items() if str(v).strip()}
    except Exception:
        return {}


def write_active_routing(config_dir, routing: dict[str, str]) -> None:
    """Persist the `active_routing` map. An empty/cleared map = Unified Mode."""
    import json as _json
    from state.access import write_config_snapshot

    clean = {str(k).upper(): str(v).strip() for k, v in (routing or {}).items() if str(v).strip()}
    write_config_snapshot(config_dir, {"active_routing": _json.dumps(clean)})


def build_llm_client_for_role(config_dir, role: str):
    """Plan 179 C2: build an ``LLMClient`` for a specific agent ROLE.

    When `active_routing` assigns the role a model, build a client for THAT model (same
    provider/api_key/base_url as the global config); otherwise fall back to the global client
    (Unified Mode — zero behavior change). Returns None when no provider is configured."""
    role_model = read_active_routing(config_dir).get(str(role or "").upper(), "").strip()
    if not role_model:
        return build_default_llm_client_or_none(config_dir)
    try:
        from pathlib import Path as _Path

        from duckln.config import ConfigPaths, load_app_config

        paths = ConfigPaths(config_dir=_Path(config_dir), config_file=_Path(config_dir) / "config.json")
        current = load_app_config(paths)
    except Exception:
        current = None
    if current is None or not getattr(current, "provider", None):
        return build_default_llm_client_or_none(config_dir)

    def _client(*, system_prompt: str, user_message: str, max_tokens: int | None = None, json_mode: bool = False) -> str:
        return generate_provider_reply(
            current.provider,
            model_id=role_model,
            api_key=current.api_key,
            base_url=current.base_url,
            system_prompt=system_prompt,
            user_message=user_message,
            config_dir=config_dir,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )

    return _client


def normalize_ollama_base_url(base_url: str | None) -> str:
    """Normalize a user-entered Ollama URL to a stable host root."""

    normalized = (base_url or OLLAMA_DEFAULT_BASE_URL).strip().rstrip("/")
    if normalized.endswith("/api/tags"):
        normalized = normalized[: -len("/api/tags")]
    elif normalized.endswith("/api"):
        normalized = normalized[: -len("/api")]
    return normalized or OLLAMA_DEFAULT_BASE_URL


# Plan 122: bounded retry on transient rate-limit / overload responses.
_RATE_LIMIT_STATUSES = frozenset({429, 503})
_CONVERSATION_MAX_ATTEMPTS = 4
_BACKOFF_CAP_SECONDS = 5.0


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff (0.5s, 1s, 2s, …) capped so the UI never hangs."""
    return min(_BACKOFF_CAP_SECONDS, 0.5 * (2 ** attempt))


def _retry_after_seconds(response) -> float:
    """Honor a numeric `Retry-After` header, capped; 0 when absent/unparseable."""
    try:
        headers = getattr(response, "headers", None) or {}
        raw = headers.get("Retry-After") or headers.get("retry-after")
        if raw is None:
            return 0.0
        return min(_BACKOFF_CAP_SECONDS, float(str(raw).strip()))
    except (TypeError, ValueError):
        return 0.0


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


def _extract_error_body(response) -> str:
    """Plan 165 F1: the provider's REAL error text from a ≥400 response — `error`/`message`
    (Ollama/OpenAI shapes) else the raw body — redacted + capped. Empty when unreadable."""
    from duckln.diagnostics import redact_sensitive_data

    raw = ""
    try:
        data = response.json()
        if isinstance(data, dict):
            err = data.get("error") or data.get("message")
            if isinstance(err, dict):
                err = err.get("message") or err.get("error")
            raw = str(err or "")
    except Exception:
        raw = ""
    if not raw:
        try:
            raw = (getattr(response, "text", "") or "").strip()
        except Exception:
            raw = ""
    if not raw:
        return ""
    try:
        return redact_sensitive_data(raw)[:300]
    except Exception:
        return raw[:300]


def _ollama_error_hint(body: str, model_id: str) -> str:
    """Plan 165 F2: turn the two common Ollama error bodies into an actionable next step."""
    low = (body or "").lower()
    if "more system memory" in low or "out of memory" in low or "cudamalloc" in low:
        return "This model needs more RAM than is free right now — try a smaller model (e.g. `ollama pull llama3.2:1b`) or close other apps."
    if "not found" in low or "try pulling" in low or "no such model" in low:
        return f"Ollama doesn't have `{model_id}` installed — run `ollama pull {model_id}` first."
    return ""


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


def _build_openai_style_conversation_payload(
    *,
    model_id: str,
    system_prompt: str,
    user_message: str,
    recent_turns: tuple[tuple[str, str], ...],
    max_tokens: int | None = None,
    json_mode: bool = False,
) -> dict[str, Any]:
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(
        {"role": role, "content": content}
        for role, content in recent_turns
        if role in {"user", "assistant"}
    )
    messages.append({"role": "user", "content": user_message})
    payload: dict[str, Any] = {
        "model": model_id,
        "messages": messages,
        "temperature": 0.3,
        # Plan 119: callers needing a complete structured answer (e.g. fix
        # attribution) raise this so the JSON can't truncate mid-object.
        "max_tokens": max_tokens or 220,
    }
    if json_mode:
        # OpenAI / OpenRouter JSON mode — forces a single valid JSON object and
        # suppresses reasoning-model prose preambles.
        payload["response_format"] = {"type": "json_object"}
    return payload


def _parse_openai_style_conversation_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices", [])
    if not choices:
        return ""
    content = choices[0].get("message", {}).get("content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "".join(parts).strip()
    return str(content).strip()


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
