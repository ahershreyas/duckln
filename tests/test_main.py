"""Tests for slash commands and the runtime command palette."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import tempfile
import unittest

from duckln.ai_client import Provider
from duckln.config import AppConfig, load_app_config, resolve_config_paths
from duckln.main import get_slash_command_descriptors, handle_session_command
from duckln.modes import ControlMode
from duckln.repos import format_repo_catalog_choice, open_repo_catalog
from state.repo_catalog import RepoCatalogRecord, resolve_local_repo_catalog_cache_path


@dataclass
class FakeResponse:
    status_code: int
    payload: dict
    text: str = ""

    def json(self) -> dict:
        return self.payload


class FakeHttpClient:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses

    def get(self, url: str, *, headers: dict[str, str], timeout: float) -> FakeResponse:
        response = self.responses[url]
        if isinstance(response, dict):
            auth_header = headers.get("Authorization") or headers.get("x-api-key") or ""
            return response[auth_header]
        return response


class SlashCommandTest(unittest.TestCase):
    def test_command_palette_lists_available_commands(self) -> None:
        commands = get_slash_command_descriptors()

        self.assertEqual(("/mode", "/provider", "/model", "/config", "/repos", "/healthcheck"), tuple(item.command for item in commands))
        self.assertTrue(all("—" in item.choice_label for item in commands))

    def test_mode_command_updates_saved_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )
            displayed: list[str] = []

            updated = handle_session_command(
                "/mode",
                current,
                paths,
                select=lambda prompt, choices: choices[1],
                display=displayed.append,
            )

            self.assertEqual(ControlMode.HOTL, updated.mode)
            self.assertEqual(updated, load_app_config(paths))
            self.assertTrue(any("Updated mode" in message for message in displayed))

    def test_provider_command_preserves_previous_config_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENROUTER,
                model="openrouter/auto",
                api_key="working-key",
                mode=ControlMode.HOTL,
            )
            displayed: list[str] = []
            selections = iter(("OpenAI", "Use this key", "Cancel onboarding"))
            client = FakeHttpClient(
                {
                    "https://api.openai.com/v1/models": {
                        "Bearer bad-key": FakeResponse(status_code=401, payload={}, text="Unauthorized"),
                    }
                }
            )

            updated = handle_session_command(
                "/provider",
                current,
                paths,
                select=lambda prompt, choices: next(selections),
                secret_prompt=lambda prompt: "bad-key",
                display=displayed.append,
                client=client,
            )

            self.assertEqual(current, updated)
            self.assertIsNone(load_app_config(paths))
            self.assertTrue(any("Retryable error:" in message for message in displayed))

    def test_model_command_updates_model_after_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENROUTER,
                model="openrouter/auto",
                api_key="router-key",
                mode=ControlMode.HITL,
            )
            client = FakeHttpClient(
                {
                    "https://openrouter.ai/api/v1/models": FakeResponse(
                        status_code=200,
                        payload={"data": [{"id": "z-model", "name": "Zulu"}, {"id": "a-model", "name": "Alpha"}]},
                    )
                }
            )

            updated = handle_session_command(
                "/model",
                current,
                paths,
                select=lambda prompt, choices: choices[0],
                display=lambda message: None,
                client=client,
            )

            self.assertEqual("a-model", updated.model)
            self.assertEqual(updated, load_app_config(paths))

    def test_config_menu_routes_to_provider_flow(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENROUTER,
                model="openrouter/auto",
                api_key="router-key",
                mode=ControlMode.HITL,
            )
            selections = iter(
                (
                    "/provider — Change the AI provider, API key, and model.",
                    "Anthropic",
                    "Use this key",
                    "claude-3-5-haiku",
                )
            )
            client = FakeHttpClient(
                {
                    "https://api.anthropic.com/v1/models": FakeResponse(
                        status_code=200,
                        payload={"data": [{"id": "claude-3-5-haiku", "display_name": "Claude 3.5 Haiku"}]},
                    )
                }
            )

            updated = handle_session_command(
                "/config",
                current,
                paths,
                select=lambda prompt, choices: next(selections),
                secret_prompt=lambda prompt: "anthropic-key",
                display=lambda message: None,
                client=client,
            )

            self.assertEqual(Provider.ANTHROPIC, updated.provider)
            self.assertEqual("claude-3-5-haiku", updated.model)
            self.assertEqual(updated, load_app_config(paths))

    def test_slash_palette_executes_selected_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )
            selections = iter(
                (
                    "/mode — Change how much Duckln can do for you.",
                    "HOTL — Human-on-the-Loop: AI suggests exact commands; you approve before execution.",
                )
            )

            updated = handle_session_command(
                "/",
                current,
                paths,
                select=lambda prompt, choices: next(selections),
                display=lambda message: None,
            )

            self.assertEqual(ControlMode.HOTL, updated.mode)
            self.assertEqual(updated, load_app_config(paths))

    def test_healthcheck_command_reports_status_without_changing_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )
            displayed: list[str] = []
            client = FakeHttpClient(
                {
                    "https://api.openai.com/v1/models": FakeResponse(
                        status_code=200,
                        payload={"data": [{"id": "gpt-4o-mini"}]},
                    )
                }
            )

            updated = handle_session_command(
                "/healthcheck",
                current,
                paths,
                display=displayed.append,
                client=client,
            )

            self.assertEqual(current, updated)
            self.assertTrue(any("[PASS]" in message or "[FAIL]" in message for message in displayed))

    def test_repos_command_displays_selected_cached_repo_without_changing_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )
            repo_record = {
                "name": "alpha",
                "repo_url": "https://example.com/alpha",
                "stars": 50,
                "description": "Alpha repository for tests",
                "category": "LLM",
                "framework": "Python",
                "last_updated": "2026-03-22",
            }
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps({"last_updated": "2026-03-23", "repos": [repo_record]}) + "\n", encoding="utf-8")
            displayed: list[str] = []

            updated = handle_session_command(
                "/repos",
                current,
                paths,
                select=lambda prompt, choices: choices[0],
                display=displayed.append,
            )

            self.assertEqual(current, updated)
            self.assertTrue(any("Selected repository: alpha (https://example.com/alpha)" == message for message in displayed))


class RepoSelectionTest(unittest.TestCase):
    def test_format_repo_catalog_choice_is_clean_and_readable(self) -> None:
        label = format_repo_catalog_choice(
            RepoCatalogRecord(
                name="duck",
                repo_url="https://example.com/duck",
                stars=123,
                description="A very long description that should be shortened for the dropdown label so it stays readable.",
                category="LLM",
                framework="Python",
                last_updated="2026-03-23",
            )
        )

        self.assertIn("duck | 123 stars |", label)
        self.assertIn("| LLM | Python", label)
        self.assertIn("...", label)

    def test_open_repo_catalog_returns_selected_repo_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            records = [
                {
                    "name": "low",
                    "repo_url": "https://example.com/low",
                    "stars": 5,
                    "description": "Low repo",
                    "category": "Vision",
                    "framework": "Rust",
                    "last_updated": "2026-03-20",
                },
                {
                    "name": "top",
                    "repo_url": "https://example.com/top",
                    "stars": 10,
                    "description": "Top repo",
                    "category": "LLM",
                    "framework": "Python",
                    "last_updated": "2026-03-21",
                },
            ]
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps({"last_updated": "2026-03-23", "repos": records}) + "\n", encoding="utf-8")

            selected = open_repo_catalog(paths, select=lambda prompt, choices: choices[0])

            self.assertIsNotNone(selected)
            assert selected is not None
            self.assertEqual("top", selected.name)
            self.assertEqual("https://example.com/top", selected.repo_url)


if __name__ == "__main__":
    unittest.main()
