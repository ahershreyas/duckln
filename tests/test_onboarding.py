"""Tests for shared config schema and first-run onboarding flow."""

from __future__ import annotations

from dataclasses import dataclass
import tempfile
import unittest

from duckln.ai_client import Provider
from duckln.config import (
    AppConfig,
    OnboardingError,
    deserialize_app_config,
    load_app_config,
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


class OnboardingFlowTest(unittest.TestCase):
    def test_shared_config_schema_round_trips(self) -> None:
        config = AppConfig(
            provider=Provider.OPENROUTER,
            model="openrouter/auto",
            api_key="secret-key",
            mode=ControlMode.HITL,
        )

        payload = serialize_app_config(config)

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
            self.assertIn("Entered API key: router-key", displayed)
            self.assertEqual(
                (
                    ("Select your AI provider:", ("OpenRouter", "OpenAI", "Anthropic")),
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
