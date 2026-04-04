"""Tests for shared config schema and first-run onboarding flow."""

from __future__ import annotations

from dataclasses import dataclass
import tempfile
import unittest
from unittest.mock import patch

from duckln.ai_client import Provider
from duckln.config import (
    AppConfig,
    OnboardingError,
    SessionExitRequested,
    deserialize_app_config,
    ensure_first_run_preferences,
    load_app_config,
    load_user_preferences,
    resolve_config_paths,
    run_onboarding,
    save_app_config,
    serialize_app_config,
)
from duckln.modes import ControlMode


@dataclass
class FakeResponse:
    status_code: int
    payload: dict
    text: str = ""

    def json(self) -> dict:
        return self.payload


class FakeHttpClient:
    def __init__(self, responses: dict[str, FakeResponse]) -> None:
        self.responses = responses

    def get(self, url: str, *, headers: dict[str, str], timeout: float) -> FakeResponse:
        response = self.responses[url]
        if isinstance(response, dict):
            auth_header = headers.get("Authorization") or headers.get("x-api-key") or ""
            return response[auth_header]
        return response


class FakePullProcess:
    def __init__(self, lines: tuple[str, ...] = ("pulling manifest\n", "success\n"), exit_code: int = 0) -> None:
        self.stdout = iter(lines)
        self._exit_code = exit_code

    def wait(self) -> int:
        return self._exit_code

    def post(self, url: str, *, headers: dict[str, str], json: dict, timeout: float) -> FakeResponse:
        response = self.responses[url]
        if isinstance(response, dict):
            return response[json["name"]]
        return response


class OnboardingFlowTest(unittest.TestCase):
    def test_first_run_trust_preferences_are_collected_and_persisted_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            displayed: list[str] = []
            selections = iter(("I understand and want to continue",))

            preferences = ensure_first_run_preferences(
                paths,
                select=lambda prompt, choices: next(selections),
                text_prompt=lambda prompt, default="": "Shreyas",
                display=displayed.append,
            )

            self.assertEqual("Shreyas", preferences.user_name)
            self.assertTrue(preferences.safety_accepted_at)
            self.assertFalse(preferences.onboarding_complete)
            self.assertEqual(preferences, load_user_preferences(paths))
            self.assertTrue(any("Safety & Permissions" in message for message in displayed))

    def test_save_app_config_preserves_user_preferences_and_marks_onboarding_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            ensure_first_run_preferences(
                paths,
                select=lambda prompt, choices: "I understand and want to continue",
                text_prompt=lambda prompt, default="": "Sam",
                display=lambda message: None,
            )

            result = run_onboarding(
                paths,
                select=lambda prompt, choices, choices_iter=iter(("OpenAI", "Use this key", "gpt-4o-mini", "HOTL")): next(choices_iter),
                secret_prompt=lambda prompt: "openai-key",
                display=lambda message: None,
                render_banner=lambda: "DUCKLN",
                client=FakeHttpClient(
                    {
                        "https://api.openai.com/v1/models": FakeResponse(
                            status_code=200,
                            payload={"data": [{"id": "gpt-4o-mini"}]},
                        )
                    }
                ),
            )

            loaded = load_app_config(paths)
            assert loaded is not None
            self.assertEqual("Sam", result.config.user_name)
            self.assertEqual("Sam", loaded.user_name)
            self.assertTrue(loaded.onboarding_complete)
            self.assertEqual(ControlMode.HOTL, loaded.preferred_mode)

    def test_shared_config_schema_round_trips(self) -> None:
        config = AppConfig(
            provider=Provider.OPENROUTER,
            model="openrouter/auto",
            api_key="secret-key",
            mode=ControlMode.HITL,
        )

        payload = serialize_app_config(config)

        self.assertEqual(config, deserialize_app_config(payload))

    def test_ollama_config_schema_serializes_null_api_key_and_base_url(self) -> None:
        config = AppConfig(
            provider=Provider.OLLAMA,
            model="llama3.2:latest",
            api_key=None,
            mode=ControlMode.HITL,
            base_url="http://localhost:11434",
        )

        payload = serialize_app_config(config)

        self.assertEqual(
            {
                "provider": "ollama",
                "model": "llama3.2:latest",
                "api_key": None,
                "mode": "hitl",
                "base_url": "http://localhost:11434",
                "user_name": "there",
                "safety_accepted_at": "1970-01-01T00:00:00+00:00",
                "onboarding_complete": True,
                "preferred_mode": None,
            },
            payload,
        )
        self.assertEqual(config, deserialize_app_config(payload))

    def test_save_and_load_typed_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            config = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )

            save_app_config(config, paths)

            self.assertEqual(config, load_app_config(paths))

    def test_onboarding_validates_and_saves_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            prompts: list[tuple[str, tuple[str, ...]]] = []
            displayed: list[str] = []
            selections = iter(("OpenRouter", "Use this key", "a-model", "HOTL"))
            client = FakeHttpClient(
                {
                    "https://openrouter.ai/api/v1/models": FakeResponse(
                        status_code=200,
                        payload={
                            "data": [
                                {"id": "z-model", "name": "Zulu"},
                                {"id": "a-model", "name": "Alpha"},
                            ]
                        },
                    )
                }
            )

            def select(prompt: str, choices: tuple[str, ...]) -> str:
                prompts.append((prompt, choices))
                return next(selections)

            result = run_onboarding(
                paths,
                select=select,
                secret_prompt=lambda prompt: "router-key",
                display=displayed.append,
                render_banner=lambda: "DUCKLN",
                client=client,
            )

            self.assertEqual(Provider.OPENROUTER, result.config.provider)
            self.assertEqual("a-model", result.config.model)
            self.assertEqual(ControlMode.HOTL, result.config.mode)
            self.assertEqual(result.config, load_app_config(paths))
            self.assertEqual("DUCKLN", displayed[0])
            self.assertIn("API key received.", displayed)
            self.assertNotIn("router-key", "\n".join(displayed))
            self.assertIn("OpenRouter connection verified.", displayed)
            self.assertIn("OpenRouter is ready with model a-model.", displayed)
            self.assertEqual(
                (
                    ("Select your AI provider:", ("OpenRouter", "OpenAI", "Anthropic", "Ollama")),
                    ("Use this API key?", ("Use this key", "Re-enter API key")),
                    ("Select a model:", ("a-model", "z-model")),
                    (
                        "Select your control mode:",
                        (
                            "HITL — Human-in-the-Loop: AI explains and suggests only; you run commands yourself.",
                            "HOTL — Human-on-the-Loop: AI suggests exact commands; you approve before execution.",
                            "HOOTLWO — Human-out-of-the-Loop with Oversight: AI can auto-run safe whitelisted fixes; use a sandbox or VM.",
                        ),
                    ),
                ),
                tuple(prompts),
            )

    def test_onboarding_supports_ollama_without_prompting_for_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            displayed: list[str] = []
            selections = iter(("Ollama", "llama3.2:latest", "HITL"))
            client = FakeHttpClient(
                {
                    "http://localhost:11434/api/tags": FakeResponse(
                        status_code=200,
                        payload={"models": [{"name": "llama3.2:latest"}]},
                    )
                }
            )

            result = run_onboarding(
                paths,
                select=lambda prompt, choices: next(selections),
                secret_prompt=lambda prompt: self.fail("Ollama should not ask for an API key"),
                display=displayed.append,
                render_banner=lambda: "DUCKLN",
                client=client,
            )

            self.assertEqual(Provider.OLLAMA, result.config.provider)
            self.assertIsNone(result.config.api_key)
            self.assertEqual("http://localhost:11434", result.config.base_url)
            self.assertEqual("llama3.2:latest", result.config.model)
            self.assertIn("Checking local Ollama runtime at http://localhost:11434/api/tags.", displayed)
            self.assertIn("Ollama is reachable at http://localhost:11434/api/tags.", displayed)

    def test_onboarding_can_retry_unreachable_ollama_then_return_to_provider_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            displayed: list[str] = []
            selections = iter(
                (
                    "Ollama",
                    "Retry Ollama detection",
                    "Back to provider selection",
                    "OpenAI",
                    "Use this key",
                    "gpt-4o-mini",
                    "HITL",
                )
            )
            client = FakeHttpClient(
                {
                    "https://api.openai.com/v1/models": FakeResponse(
                        status_code=200,
                        payload={"data": [{"id": "gpt-4o-mini"}]},
                    )
                }
            )

            class FailingThenOpenAIClient(FakeHttpClient):
                def get(self, url: str, *, headers: dict[str, str], timeout: float) -> FakeResponse:
                    if url == "http://localhost:11434/api/tags":
                        raise OSError("connection refused")
                    return super().get(url, headers=headers, timeout=timeout)

            result = run_onboarding(
                paths,
                select=lambda prompt, choices: next(selections),
                secret_prompt=lambda prompt: "openai-key",
                display=displayed.append,
                render_banner=lambda: "DUCKLN",
                client=FailingThenOpenAIClient(client.responses),
            )

            self.assertEqual(Provider.OPENAI, result.config.provider)
            self.assertEqual("openai-key", result.config.api_key)
            self.assertTrue(
                any(
                    "Ollama is not installed or not reachable at http://localhost:11434/api/tags." in message
                    or "Ollama is installed but not running at http://localhost:11434/api/tags." in message
                    for message in displayed
                )
            )

    def test_runtime_provider_update_cancels_cleanly_when_secret_entry_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            from duckln.config import update_runtime_provider

            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENROUTER,
                model="openrouter/auto",
                api_key="router-key",
                mode=ControlMode.HITL,
            )
            displayed: list[str] = []

            updated = update_runtime_provider(
                current,
                paths,
                select=lambda prompt, choices: "OpenAI",
                secret_prompt=lambda prompt: None,
                display=displayed.append,
            )

            self.assertEqual(current, updated)
            self.assertEqual(["Provider update cancelled."], displayed)

    def test_onboarding_can_use_custom_ollama_base_url_after_default_url_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            displayed: list[str] = []
            selections = iter(
                (
                    "Ollama",
                    "Use a custom Ollama base URL",
                    "custom-model:latest",
                    "HITL",
                )
            )

            class CustomBaseUrlClient:
                def get(self, url: str, *, headers: dict[str, str], timeout: float) -> FakeResponse:
                    if url == "http://localhost:11434/api/tags":
                        raise OSError("connection refused")
                    if url == "http://localhost:11555/api/tags":
                        return FakeResponse(
                            status_code=200,
                            payload={"models": [{"name": "custom-model:latest"}]},
                        )
                    raise AssertionError(f"Unexpected URL {url}")

            result = run_onboarding(
                paths,
                select=lambda prompt, choices: next(selections),
                secret_prompt=lambda prompt: self.fail("Ollama should not ask for an API key"),
                text_prompt=lambda prompt, default="": "http://localhost:11555",
                display=displayed.append,
                render_banner=lambda: "DUCKLN",
                client=CustomBaseUrlClient(),
            )

            self.assertEqual(Provider.OLLAMA, result.config.provider)
            self.assertEqual("custom-model:latest", result.config.model)
            self.assertEqual("http://localhost:11555", result.config.base_url)
            self.assertIsNone(result.config.api_key)
            self.assertIn("Checking local Ollama runtime at http://localhost:11555/api/tags.", displayed)

    def test_onboarding_can_start_ollama_runtime_when_user_chooses_start_now(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            displayed: list[str] = []
            selections = iter(("Ollama", "Start Ollama now", "llama3.2:latest", "HITL"))
            process_calls: list[list[str]] = []

            class RecoveringOllamaClient:
                def __init__(self) -> None:
                    self.calls = 0

                def get(self, url: str, *, headers: dict[str, str], timeout: float) -> FakeResponse:
                    self.calls += 1
                    if self.calls == 1:
                        raise OSError("connection refused")
                    return FakeResponse(
                        status_code=200,
                        payload={"models": [{"name": "llama3.2:latest"}]},
                    )

            def fake_popen(command, stdout=None, stderr=None, text=True, bufsize=1):
                process_calls.append(list(command))
                return FakePullProcess(lines=())

            with (
                patch("duckln.ai_client.shutil.which", return_value="/usr/local/bin/ollama"),
                patch("duckln.config.shutil.which", return_value="/usr/local/bin/ollama"),
                patch("duckln.config.subprocess.Popen", side_effect=fake_popen),
                patch("duckln.config.time.sleep"),
            ):
                result = run_onboarding(
                    paths,
                    select=lambda prompt, choices: next(selections),
                    secret_prompt=lambda prompt: self.fail("Ollama should not ask for an API key"),
                    display=displayed.append,
                    render_banner=lambda: "DUCKLN",
                    client=RecoveringOllamaClient(),
                )

            self.assertEqual("llama3.2:latest", result.config.model)
            self.assertEqual([["ollama", "serve"]], process_calls)
            self.assertIn("Starting Ollama with `ollama serve`...", displayed)
            self.assertIn("Ollama is reachable at http://localhost:11434/api/tags.", displayed)

    def test_onboarding_can_pull_new_ollama_model_from_selection_menu(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            displayed: list[str] = []
            selections = iter(("Ollama", "Pull a new model", "HITL"))
            case = self

            class PullableOllamaClient:
                def __init__(self) -> None:
                    self.models = ["llama3.2:latest"]

                def get(self, url: str, *, headers: dict[str, str], timeout: float) -> FakeResponse:
                    case.assertEqual("http://localhost:11434/api/tags", url)
                    return FakeResponse(
                        status_code=200,
                        payload={"models": [{"name": model_name} for model_name in self.models]},
                    )

            client = PullableOllamaClient()
            popen_calls: list[list[str]] = []

            def fake_popen(command, stdout=None, stderr=None, text=True, bufsize=1):
                popen_calls.append(list(command))
                client.models.append(command[-1])
                return FakePullProcess()

            with patch("duckln.config.subprocess.Popen", side_effect=fake_popen):
                result = run_onboarding(
                    paths,
                    select=lambda prompt, choices: next(selections),
                    secret_prompt=lambda prompt: self.fail("Ollama should not ask for an API key"),
                    text_prompt=lambda prompt, default="": "mistral:latest",
                    display=displayed.append,
                    render_banner=lambda: "DUCKLN",
                    client=client,
                )

            self.assertEqual(Provider.OLLAMA, result.config.provider)
            self.assertEqual("mistral:latest", result.config.model)
            self.assertIsNone(result.config.api_key)
            self.assertEqual("http://localhost:11434", result.config.base_url)
            self.assertEqual([["ollama", "pull", "mistral:latest"]], popen_calls)
            self.assertIn("Running `ollama pull mistral:latest`...", displayed)
            self.assertIn("pulling manifest", displayed)
            self.assertIn("Ollama is reachable at http://localhost:11434/api/tags.", displayed)

    def test_onboarding_ollama_model_menu_shows_ram_filtered_recommendations_and_manual_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            prompts: list[tuple[str, tuple[str, ...]]] = []
            displayed: list[str] = []
            selections = iter(("Ollama", "llama3.2:3b [recommended]", "HITL"))

            class PullableOllamaClient:
                def __init__(self) -> None:
                    self.models = ["llama3.2:latest"]

                def get(self, url: str, *, headers: dict[str, str], timeout: float) -> FakeResponse:
                    return FakeResponse(
                        status_code=200,
                        payload={"models": [{"name": model_name} for model_name in self.models]},
                    )

            client = PullableOllamaClient()

            def select(prompt: str, choices: tuple[str, ...]) -> str:
                prompts.append((prompt, choices))
                return next(selections)

            def fake_popen(command, stdout=None, stderr=None, text=True, bufsize=1):
                client.models.append(command[-1])
                return FakePullProcess()

            with (
                patch("duckln.config._detect_system_ram_gib", return_value=16),
                patch("duckln.config.subprocess.Popen", side_effect=fake_popen),
            ):
                result = run_onboarding(
                    paths,
                    select=select,
                    secret_prompt=lambda prompt: self.fail("Ollama should not ask for an API key"),
                    display=displayed.append,
                    render_banner=lambda: "DUCKLN",
                    client=client,
                )

            self.assertEqual("llama3.2:3b", result.config.model)
            self.assertIn(
                (
                    "Select a model:",
                    (
                        "llama3.2:latest",
                        "llama3.2:3b [recommended]",
                        "qwen2.5:3b [recommended]",
                        "phi3:mini [recommended]",
                        "Pull a new model",
                        "Enter a model name manually",
                    ),
                ),
                prompts,
            )

    def test_onboarding_ollama_model_menu_supports_manual_model_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            selections = iter(("Ollama", "Enter a model name manually", "HITL"))

            class PullableOllamaClient:
                def __init__(self) -> None:
                    self.models: list[str] = []

                def get(self, url: str, *, headers: dict[str, str], timeout: float) -> FakeResponse:
                    return FakeResponse(
                        status_code=200,
                        payload={"models": [{"name": model_name} for model_name in self.models]},
                    )

            client = PullableOllamaClient()

            def fake_popen(command, stdout=None, stderr=None, text=True, bufsize=1):
                client.models.append(command[-1])
                return FakePullProcess()

            with patch("duckln.config.subprocess.Popen", side_effect=fake_popen):
                result = run_onboarding(
                    paths,
                    select=lambda prompt, choices: next(selections),
                    secret_prompt=lambda prompt: self.fail("Ollama should not ask for an API key"),
                    text_prompt=lambda prompt, default="": "custom-model:latest",
                    display=lambda message: None,
                    render_banner=lambda: "DUCKLN",
                    client=client,
                )

            self.assertEqual("custom-model:latest", result.config.model)

    def test_hootlwo_selection_displays_safety_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            displayed: list[str] = []
            selections = iter(("Anthropic", "Use this key", "claude-3-5-haiku", "HOOTLWO"))
            client = FakeHttpClient(
                {
                    "https://api.anthropic.com/v1/models": FakeResponse(
                        status_code=200,
                        payload={"data": [{"id": "claude-3-5-haiku", "display_name": "Claude 3.5 Haiku"}]},
                    )
                }
            )

            run_onboarding(
                paths,
                select=lambda prompt, choices: next(selections),
                secret_prompt=lambda prompt: "anthropic-key",
                display=displayed.append,
                render_banner=lambda: "DUCKLN",
                client=client,
            )

            self.assertTrue(any("sandbox or VM" in message for message in displayed))

    def test_failed_validation_can_retry_without_saving_invalid_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            displayed: list[str] = []
            selections = iter(
                (
                    "OpenAI",
                    "Use this key",
                    "Retry API key",
                    "Use this key",
                    "gpt-4o-mini",
                    "HITL",
                )
            )
            secret_inputs = iter(("bad-key", "good-key"))
            client = FakeHttpClient(
                {
                    "https://api.openai.com/v1/models": {
                        "Bearer bad-key": FakeResponse(
                            status_code=401,
                            payload={},
                            text="Unauthorized",
                        ),
                        "Bearer good-key": FakeResponse(
                            status_code=200,
                            payload={"data": [{"id": "gpt-4o-mini"}]},
                        ),
                    }
                }
            )

            result = run_onboarding(
                paths,
                select=lambda prompt, choices: next(selections),
                secret_prompt=lambda prompt: next(secret_inputs),
                display=displayed.append,
                render_banner=lambda: "DUCKLN",
                client=client,
            )

            self.assertEqual("good-key", result.config.api_key)
            self.assertEqual(result.config, load_app_config(paths))
            self.assertTrue(any("Retryable error:" in message for message in displayed))

    def test_empty_model_list_can_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            displayed: list[str] = []
            selections = iter(
                (
                    "OpenRouter",
                    "Use this key",
                    "Retry API key",
                    "Use this key",
                    "openrouter/auto",
                    "HOTL",
                )
            )
            secret_inputs = iter(("empty-key", "good-key"))
            client = FakeHttpClient(
                {
                    "https://openrouter.ai/api/v1/models": {
                        "Bearer empty-key": FakeResponse(
                            status_code=200,
                            payload={"data": []},
                        ),
                        "Bearer good-key": FakeResponse(
                            status_code=200,
                            payload={"data": [{"id": "openrouter/auto", "name": "Auto"}]},
                        ),
                    }
                }
            )

            result = run_onboarding(
                paths,
                select=lambda prompt, choices: next(selections),
                secret_prompt=lambda prompt: next(secret_inputs),
                display=displayed.append,
                render_banner=lambda: "DUCKLN",
                client=client,
            )

            self.assertEqual("openrouter/auto", result.config.model)
            self.assertTrue(any("empty model list" in message for message in displayed))

    def test_failed_connection_surfaces_retryable_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            displayed: list[str] = []
            selections = iter(("Anthropic", "Use this key", "Cancel onboarding"))

            class FailingHttpClient:
                def get(self, url: str, *, headers: dict[str, str], timeout: float) -> FakeResponse:
                    raise OSError("network down")

            with self.assertRaises(OnboardingError):
                run_onboarding(
                    paths,
                    select=lambda prompt, choices: next(selections),
                    secret_prompt=lambda prompt: "anthropic-key",
                    display=displayed.append,
                    render_banner=lambda: "DUCKLN",
                    client=FailingHttpClient(),
                )

            self.assertFalse(paths.config_file.exists())
            self.assertTrue(any("Failed to connect" in message for message in displayed))


if __name__ == "__main__":
    unittest.main()
