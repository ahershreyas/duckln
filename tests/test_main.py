"""Tests for slash commands and the runtime command palette."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from duckln.ai_client import Provider
from duckln.config import (
    AppConfig,
    OnboardingResult,
    SessionExitRequested,
    load_app_config,
    resolve_config_paths,
    save_app_config,
)
from duckln.main import _session_memory_state, get_slash_command_descriptors, handle_session_command, main
from duckln.modes import ControlMode
from duckln.repo_bringup import infer_repo_setup_plan, resolve_managed_project_dir
from duckln.repos import CANCEL_REPO_SELECTION, format_repo_catalog_choice, open_repo_catalog
from state.access import initialize_managed_memory_state, write_config_snapshot
from state.repo_catalog import RepoCatalogRecord, RepoCatalogRefreshResult, resolve_local_repo_catalog_cache_path
from agent.probe import GpuProbeState, SystemProbe


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

    def post(self, url: str, *, headers: dict[str, str], json: dict, timeout: float) -> FakeResponse:
        response = self.responses[url]
        if isinstance(response, dict):
            return response[json["name"]]
        return response


class FakePullProcess:
    def __init__(self, lines: tuple[str, ...] = ("pulling manifest\n", "success\n"), exit_code: int = 0) -> None:
        self.stdout = iter(lines)
        self._exit_code = exit_code

    def wait(self) -> int:
        return self._exit_code


class SlashCommandTest(unittest.TestCase):
    def test_command_palette_lists_available_commands(self) -> None:
        commands = get_slash_command_descriptors()

        self.assertEqual(
            ("/help", "/mode", "/provider", "/model", "/config", "/repos", "/repos refresh", "/memory clear", "/vm", "/healthcheck"),
            tuple(item.command for item in commands),
        )
        self.assertTrue(all("—" in item.choice_label for item in commands))

    def test_help_command_prints_supported_commands_from_metadata(self) -> None:
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
                "/help",
                current,
                paths,
                display=displayed.append,
            )

            self.assertEqual(current, updated)
            self.assertEqual(
                tuple(descriptor.choice_label for descriptor in get_slash_command_descriptors()),
                tuple(displayed),
            )

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

    def test_provider_command_cancel_returns_current_config_without_saving(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENROUTER,
                model="openrouter/auto",
                api_key="working-key",
                mode=ControlMode.HOTL,
            )
            displayed: list[str] = []

            updated = handle_session_command(
                "/provider",
                current,
                paths,
                select=lambda prompt, choices: "Back",
                secret_prompt=lambda prompt: self.fail("Cancel should not ask for a new key"),
                display=displayed.append,
            )

            self.assertEqual(current, updated)
            self.assertIsNone(load_app_config(paths))
            self.assertEqual(["Provider update cancelled."], displayed)

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

            displayed: list[str] = []

            updated = handle_session_command(
                "/model",
                current,
                paths,
                select=lambda prompt, choices: choices[0],
                display=displayed.append,
                client=client,
            )

            self.assertEqual("a-model", updated.model)
            self.assertEqual(updated, load_app_config(paths))
            self.assertIn("OpenRouter connection verified.", displayed)
            self.assertIn("OpenRouter is ready with model a-model.", displayed)

    def test_model_command_cancel_returns_current_config_without_saving(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENROUTER,
                model="openrouter/auto",
                api_key="router-key",
                mode=ControlMode.HITL,
            )
            displayed: list[str] = []
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
                select=lambda prompt, choices: "Cancel",
                display=displayed.append,
                client=client,
            )

            self.assertEqual(current, updated)
            self.assertIsNone(load_app_config(paths))
            self.assertEqual(["OpenRouter connection verified.", "Model update cancelled."], displayed)

    def test_provider_command_supports_ollama_without_api_key_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )
            displayed: list[str] = []
            selections = iter(("Ollama", "llama3.2:latest"))
            client = FakeHttpClient(
                {
                    "http://localhost:11434/api/tags": FakeResponse(
                        status_code=200,
                        payload={"models": [{"name": "llama3.2:latest"}]},
                    )
                }
            )

            updated = handle_session_command(
                "/provider",
                current,
                paths,
                select=lambda prompt, choices: next(selections),
                secret_prompt=lambda prompt: self.fail("Ollama should not prompt for an API key"),
                display=displayed.append,
                client=client,
            )

            self.assertEqual(Provider.OLLAMA, updated.provider)
            self.assertEqual("llama3.2:latest", updated.model)
            self.assertIsNone(updated.api_key)
            self.assertEqual("http://localhost:11434", updated.base_url)
            self.assertIn("Ollama is reachable at http://localhost:11434/api/tags.", displayed)
            self.assertIn("Ollama connection ready.", displayed)

    def test_provider_command_can_switch_to_custom_ollama_base_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )
            displayed: list[str] = []
            selections = iter(("Ollama", "Use a custom Ollama base URL", "llama3.2:latest"))

            class CustomBaseUrlClient:
                def get(self, url: str, *, headers: dict[str, str], timeout: float) -> FakeResponse:
                    if url == "http://localhost:11434/api/tags":
                        raise OSError("connection refused")
                    if url == "http://localhost:11555/api/tags":
                        return FakeResponse(
                            status_code=200,
                            payload={"models": [{"name": "llama3.2:latest"}]},
                        )
                    raise AssertionError(f"Unexpected URL {url}")

            updated = handle_session_command(
                "/provider",
                current,
                paths,
                select=lambda prompt, choices: next(selections),
                secret_prompt=lambda prompt: self.fail("Ollama should not prompt for an API key"),
                text_prompt=lambda prompt, default="": "http://localhost:11555/api/tags",
                display=displayed.append,
                client=CustomBaseUrlClient(),
            )

            self.assertEqual(Provider.OLLAMA, updated.provider)
            self.assertEqual("llama3.2:latest", updated.model)
            self.assertIsNone(updated.api_key)
            self.assertEqual("http://localhost:11555", updated.base_url)
            self.assertIn("Checking local Ollama runtime at http://localhost:11555/api/tags.", displayed)
            self.assertEqual(updated, load_app_config(paths))

    def test_provider_command_can_start_ollama_in_hotl_after_user_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            displayed: list[str] = []
            selections = iter(("Ollama", "Start Ollama now", "llama3.2:latest"))
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
                updated = handle_session_command(
                    "/provider",
                    current,
                    paths,
                    select=lambda prompt, choices: next(selections),
                    secret_prompt=lambda prompt: self.fail("Ollama should not prompt for an API key"),
                    display=displayed.append,
                    client=RecoveringOllamaClient(),
                )

            self.assertEqual(Provider.OLLAMA, updated.provider)
            self.assertEqual("llama3.2:latest", updated.model)
            self.assertEqual([["ollama", "serve"]], process_calls)
            self.assertIn("Starting Ollama with `ollama serve`...", displayed)
            self.assertIn("Ollama connection ready.", displayed)

    def test_model_command_reuses_ollama_pull_flow_and_preserves_null_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OLLAMA,
                model="llama3.2:latest",
                api_key=None,
                base_url="http://localhost:11434",
                mode=ControlMode.HITL,
            )
            displayed: list[str] = []
            selections = iter(("Enter a model name manually",))
            pull_commands: list[list[str]] = []

            class PullableOllamaClient:
                def __init__(self) -> None:
                    self.models = ["llama3.2:latest"]

                def get(self, url: str, *, headers: dict[str, str], timeout: float) -> FakeResponse:
                    return FakeResponse(
                        status_code=200,
                        payload={"models": [{"name": model_name} for model_name in self.models]},
                    )

            client = PullableOllamaClient()

            def fake_popen(command, stdout=None, stderr=None, text=True, bufsize=1):
                pull_commands.append(list(command))
                client.models.append(command[-1])
                return FakePullProcess()

            with patch("duckln.config.subprocess.Popen", side_effect=fake_popen):
                updated = handle_session_command(
                    "/model",
                    current,
                    paths,
                    select=lambda prompt, choices: next(selections),
                    text_prompt=lambda prompt, default="": "mistral:latest",
                    display=displayed.append,
                    client=client,
                )

            self.assertEqual(Provider.OLLAMA, updated.provider)
            self.assertEqual("mistral:latest", updated.model)
            self.assertIsNone(updated.api_key)
            self.assertEqual("http://localhost:11434", updated.base_url)
            self.assertEqual([["ollama", "pull", "mistral:latest"]], pull_commands)
            self.assertIn("Running `ollama pull mistral:latest`...", displayed)
            self.assertIn("pulling manifest", displayed)
            self.assertIn("Ollama is ready with model mistral:latest.", displayed)
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
                select=lambda prompt, choices: choices[1],
                display=displayed.append,
            )

            self.assertEqual(current, updated)
            self.assertTrue(any("Selected repository: alpha (https://example.com/alpha)" == message for message in displayed))

    def test_repos_command_returns_cleanly_when_repo_selection_is_cancelled(self) -> None:
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
                select=lambda prompt, choices: CANCEL_REPO_SELECTION,
                display=displayed.append,
            )

            self.assertEqual(current, updated)
            self.assertEqual(["No repository selected."], displayed)

    def test_repos_refresh_command_reports_success_without_changing_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )
            displayed: list[str] = []

            with patch("duckln.main.refresh_local_repo_catalog") as refresh_catalog:
                refresh_catalog.return_value = RepoCatalogRefreshResult(
                    ok=True,
                    message="Repo catalog refreshed: 14 repositories cached.",
                    records=(),
                )

                updated = handle_session_command(
                    "/repos refresh",
                    current,
                    paths,
                    display=displayed.append,
                )

            self.assertEqual(current, updated)
            refresh_catalog.assert_called_once_with(paths.config_dir)
            self.assertEqual(
                ["Refreshing the cached repo catalog...", "Repo catalog refreshed: 14 repositories cached."],
                displayed,
            )

    def test_repos_refresh_command_reports_retryable_error_without_changing_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )
            displayed: list[str] = []

            with patch("duckln.main.refresh_local_repo_catalog") as refresh_catalog:
                refresh_catalog.return_value = RepoCatalogRefreshResult(
                    ok=False,
                    message="Repo catalog refresh failed: GitHub topic fetch failed for 'llm' (HTTP 503).",
                    records=(),
                )

                updated = handle_session_command(
                    "/repos refresh",
                    current,
                    paths,
                    display=displayed.append,
                )

            self.assertEqual(current, updated)
            refresh_catalog.assert_called_once_with(paths.config_dir)
            self.assertEqual(
                [
                    "Refreshing the cached repo catalog...",
                    "Retryable error: Repo catalog refresh failed: GitHub topic fetch failed for 'llm' (HTTP 503).",
                ],
                displayed,
            )

    def test_vm_command_guides_install_without_changing_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )
            displayed: list[str] = []

            from unittest.mock import patch

            with patch("duckln.main.create_multipass_vm") as create_vm:
                create_vm.side_effect = lambda paths, text_prompt=None, display=print: display("Multipass is not installed.")
                updated = handle_session_command(
                    "/vm",
                    current,
                    paths,
                    text_prompt=lambda prompt, default: default,
                    display=displayed.append,
                )

            self.assertEqual(current, updated)
            self.assertIn("Multipass is not installed.", displayed)

    def test_vm_command_runs_configure_step_after_successful_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )

            from unittest.mock import patch
            from duckln.vm import VmProvisionResult

            with (
                patch("duckln.main.create_multipass_vm") as create_vm,
                patch("duckln.main.configure_existing_multipass_vm") as configure_vm,
            ):
                create_vm.return_value = VmProvisionResult(
                    ok=True,
                    vm_name="duckln-vm",
                    message="Ubuntu VM created and ready.",
                    connection_commands=("multipass shell duckln-vm",),
                )

                updated = handle_session_command(
                    "/vm",
                    current,
                    paths,
                    select=lambda prompt, choices: "No",
                    text_prompt=lambda prompt, default: default,
                    display=lambda message: None,
                )

            self.assertEqual(current, updated)
            configure_vm.assert_called_once()
            args, kwargs = configure_vm.call_args
            self.assertEqual(("duckln-vm", paths), args)
            self.assertEqual("No", kwargs["select"]("prompt", ("Yes", "No")))

    def test_memory_clear_command_cancels_without_changes_when_confirmation_is_declined(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )
            displayed: list[str] = []
            selections = iter(("Clear session history only", "No"))

            updated = handle_session_command(
                "/memory clear",
                current,
                paths,
                select=lambda prompt, choices: next(selections),
                display=displayed.append,
            )

            self.assertEqual(current, updated)
            self.assertIn("Delete session history and saved session summaries only.", displayed)
            self.assertIn("No memory was cleared.", displayed)

    def test_memory_clear_command_returns_immediately_for_cancel_option(self) -> None:
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
                "/memory clear",
                current,
                paths,
                select=lambda prompt, choices: "Cancel and return",
                display=displayed.append,
            )

            self.assertEqual(current, updated)
            self.assertEqual([], displayed)

    def test_memory_clear_command_runs_selected_scope_after_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )
            displayed: list[str] = []
            selections = iter(("Clear everything and reset to factory", "Yes"))

            with self.assertRaises(SessionExitRequested):
                handle_session_command(
                    "/memory clear",
                    current,
                    paths,
                    select=lambda prompt, choices: next(selections),
                    display=displayed.append,
                )

            self.assertIn("Delete Duckln state and reset managed memory to its default contract.", displayed)
            self.assertIn("Cleared Duckln state and reset managed memory.", displayed)
            self.assertIsNone(load_app_config(paths))

    def test_main_runtime_loop_routes_slash_commands_until_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )
            save_app_config(current, paths)
            displayed: list[str] = []
            user_inputs = iter(("/", "/memory clear", "exit"))

            with (
                patch("duckln.main.resolve_config_paths", return_value=paths),
                patch(
                    "duckln.main.probe_system",
                    return_value=SystemProbe(
                        operating_system="Linux",
                        architecture="x86_64",
                        cpu_logical_cores=8,
                        ram_bytes=16 * 1024**3,
                        disk_free_bytes=100 * 1024**3,
                        python_version="3.11.8",
                        gpu=GpuProbeState(
                            backend="cpu",
                            summary="No CUDA-capable GPU detected.",
                            cuda_capable=False,
                            cuda_available=False,
                            mps_capable=False,
                            mps_available=False,
                        ),
                    ),
                ),
                patch("duckln.main.record_system_probe"),
                patch("duckln.main.handle_session_command", side_effect=lambda command, current, paths, **kwargs: current) as handle_command,
            ):
                exit_code = main(
                    input_func=lambda prompt: next(user_inputs),
                    display=displayed.append,
                )

            self.assertEqual(0, exit_code)
            self.assertEqual(["/", "/memory clear"], [call.args[0] for call in handle_command.call_args_list])
            self.assertIn("Duckln is ready. Type /help to explore commands.", displayed)
            self.assertIn("Exiting Duckln.", displayed)
            self.assertTrue(any("provider : OpenAI" in re.sub(r"\x1b\[[0-9;]*m", "", message) for message in displayed))

    def test_main_accepts_slash_exit_and_prints_session_header(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            save_app_config(
                AppConfig(
                    provider=Provider.OPENAI,
                    model="gpt-4o-mini",
                    api_key="openai-key",
                    mode=ControlMode.HOTL,
                    user_name="Shreyas",
                    onboarding_complete=True,
                    safety_accepted_at="2026-04-04T00:00:00+00:00",
                    preferred_mode=ControlMode.HOTL,
                ),
                paths,
            )
            displayed: list[str] = []

            with (
                patch("duckln.main.resolve_config_paths", return_value=paths),
                patch("duckln.main.probe_system", return_value=_probe()),
                patch("duckln.main.record_system_probe"),
            ):
                exit_code = main(
                    argv=[],
                    input_func=lambda prompt: "/exit",
                    display=displayed.append,
                )

            self.assertEqual(0, exit_code)
            joined = re.sub(r"\x1b\[[0-9;]*m", "", "\n".join(displayed))
            self.assertIn("◆ Duckln", joined)
            self.assertIn("provider : OpenAI", joined)
            self.assertIn("model    : gpt-4o-mini", joined)
            self.assertIn("mode     : HOTL", joined)
            self.assertIn("user     : Shreyas", joined)
            self.assertIn("/provider to change", joined)
            self.assertIn("Exiting Duckln.", displayed)

    def test_main_runs_onboarding_before_entering_loop_when_config_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            configured = AppConfig(
                provider=Provider.OPENROUTER,
                model="openrouter/auto",
                api_key="router-key",
                mode=ControlMode.HOTL,
            )

            with (
                patch("duckln.main.resolve_config_paths", return_value=paths),
                patch(
                    "duckln.main.probe_system",
                    return_value=SystemProbe(
                        operating_system="Linux",
                        architecture="x86_64",
                        cpu_logical_cores=8,
                        ram_bytes=16 * 1024**3,
                        disk_free_bytes=100 * 1024**3,
                        python_version="3.11.8",
                        gpu=GpuProbeState(
                            backend="cpu",
                            summary="No CUDA-capable GPU detected.",
                            cuda_capable=False,
                            cuda_available=False,
                            mps_capable=False,
                            mps_available=False,
                        ),
                    ),
                ),
                patch("duckln.main.record_system_probe"),
                patch("duckln.main.ensure_first_run_preferences"),
                patch(
                    "duckln.main.run_onboarding",
                    return_value=OnboardingResult(config=configured, saved_to=paths.config_file),
                ) as run_onboarding_mock,
            ):
                exit_code = main(
                    input_func=lambda prompt: "exit",
                    display=lambda message: None,
                )

            self.assertEqual(0, exit_code)
            run_onboarding_mock.assert_called_once()

    def test_main_checks_ollama_runtime_on_session_start_and_retries_with_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            save_app_config(
                AppConfig(
                    provider=Provider.OLLAMA,
                    model="llama3.2:latest",
                    api_key=None,
                    base_url="http://localhost:11434",
                    mode=ControlMode.HITL,
                ),
                paths,
            )
            displayed: list[str] = []

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

            with (
                patch("duckln.main.resolve_config_paths", return_value=paths),
                patch(
                    "duckln.main.probe_system",
                    return_value=SystemProbe(
                        operating_system="Linux",
                        architecture="x86_64",
                        cpu_logical_cores=8,
                        ram_bytes=16 * 1024**3,
                        disk_free_bytes=100 * 1024**3,
                        python_version="3.11.8",
                        gpu=GpuProbeState(
                            backend="cpu",
                            summary="No CUDA-capable GPU detected.",
                            cuda_capable=False,
                            cuda_available=False,
                            mps_capable=False,
                            mps_available=False,
                        ),
                    ),
                ),
                patch("duckln.main.record_system_probe"),
                patch("duckln.main.time.sleep"),
            ):
                exit_code = main(
                    input_func=lambda prompt: "exit",
                    display=displayed.append,
                    client=RecoveringOllamaClient(),
                )

            self.assertEqual(0, exit_code)
            self.assertIn(
                "Checking local Ollama runtime at http://localhost:11434/api/tags (attempt 1/3)...",
                displayed,
            )
            self.assertTrue(any("Run `ollama serve`, then Duckln will retry detection." in message for message in displayed))
            self.assertIn(
                "Checking local Ollama runtime at http://localhost:11434/api/tags (attempt 2/3)...",
                displayed,
            )
            self.assertIn("Ollama is reachable at http://localhost:11434/api/tags.", displayed)

    def test_repos_command_passes_current_provider_and_execution_target_to_bringup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OLLAMA,
                model="llama3.2:latest",
                api_key=None,
                base_url="http://localhost:11434",
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
            write_config_snapshot(paths.config_dir, {"execution_target": "vm"})

            with patch("duckln.main.bring_up_selected_repo") as bringup_mock:
                updated = handle_session_command(
                    "/repos",
                    current,
                    paths,
                    select=lambda prompt, choices: choices[1],
                    display=lambda message: None,
                )

            self.assertEqual(current, updated)
            self.assertEqual("ollama", bringup_mock.call_args.kwargs["runtime_provider"])
            self.assertEqual("vm", bringup_mock.call_args.kwargs["execution_target"])

    def test_session_memory_state_requires_materialized_memory_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            save_app_config(
                AppConfig(
                    provider=Provider.OPENAI,
                    model="gpt-4o-mini",
                    api_key="openai-key",
                    mode=ControlMode.HITL,
                    user_name="Shreyas",
                    safety_accepted_at="2026-04-04T00:00:00+00:00",
                    onboarding_complete=True,
                ),
                paths,
            )
            (config_dir / "memory" / "AGENTS.md").unlink()

            self.assertEqual("not initialized", _session_memory_state(config_dir))

            initialize_managed_memory_state(config_dir)

            self.assertEqual("ready", _session_memory_state(config_dir))


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

        self.assertIn("duck", label)
        self.assertIn("123", label)
        self.assertIn("LLM", label)
        self.assertIn("Python", label)
        self.assertTrue("..." in label or "…" in label)

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

            selected = open_repo_catalog(paths, select=lambda prompt, choices: choices[1])

            self.assertIsNotNone(selected)
            assert selected is not None
            self.assertEqual("top", selected.name)
            self.assertEqual("https://example.com/top", selected.repo_url)

    def test_open_repo_catalog_returns_none_for_explicit_cancel_choice(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-03-23",
                        "repos": [
                            {
                                "name": "top",
                                "repo_url": "https://example.com/top",
                                "stars": 10,
                                "description": "Top repo",
                                "category": "LLM",
                                "framework": "Python",
                                "last_updated": "2026-03-21",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            selected = open_repo_catalog(paths, select=lambda prompt, choices: CANCEL_REPO_SELECTION)

            self.assertIsNone(selected)

    def test_repos_command_in_hitl_suggests_clone_without_mutating_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-03-23",
                        "repos": [
                            {
                                "name": "alpha",
                                "repo_url": "https://example.com/alpha",
                                "stars": 50,
                                "description": "Alpha repository for tests",
                                "category": "LLM",
                                "framework": "Python",
                                "last_updated": "2026-03-22",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            displayed: list[str] = []

            updated = handle_session_command(
                "/repos",
                current,
                paths,
                select=lambda prompt, choices: choices[1],
                display=displayed.append,
            )

            self.assertEqual(current, updated)
            self.assertTrue(any("Clone repository" in message or "suggested only" in message for message in displayed))


if __name__ == "__main__":
    unittest.main()


def _probe() -> SystemProbe:
    return SystemProbe(
        operating_system="Linux",
        architecture="x86_64",
        cpu_logical_cores=8,
        ram_bytes=16 * 1024**3,
        disk_free_bytes=100 * 1024**3,
        python_version="3.11.8",
        gpu=GpuProbeState(
            backend="cpu",
            summary="No CUDA-capable GPU detected.",
            cuda_capable=False,
            cuda_available=False,
            mps_capable=False,
            mps_available=False,
        ),
    )
