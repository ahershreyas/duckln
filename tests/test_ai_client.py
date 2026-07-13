"""Phase 7 alignment for provider adapter validation.

Req: R1
Plan: 1, 2
Tasks:
- Integration tests for provider adapters
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from duckln.ai_client import (
    ANTHROPIC_VERSION,
    AnthropicAdapter,
    OllamaAdapter,
    OpenAIAdapter,
    OpenRouterAdapter,
    Provider,
    generate_provider_reply,
    get_provider_adapter,
    get_provider_adapter_for_base_url,
)
from duckln.usage_meter import reset_usage_snapshot
from state.store import initialize_state_store


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
        self.calls: list[tuple[str, dict[str, str], float]] = []

    def get(self, url: str, *, headers: dict[str, str], timeout: float) -> FakeResponse:
        self.calls.append((url, headers, timeout))
        return self.responses[url]

    def post(self, url: str, *, headers: dict[str, str], json: dict, timeout: float) -> FakeResponse:
        self.calls.append((url, headers, timeout))
        return self.responses[url]


class RaisingHttpClient:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls: list[tuple[str, dict[str, str], float]] = []

    def get(self, url: str, *, headers: dict[str, str], timeout: float) -> FakeResponse:
        self.calls.append((url, headers, timeout))
        raise self.error


class ProviderAdaptersReqR1Plan1Plan2Test(unittest.TestCase):
    """Covers adapter selection, auth headers, model listing, and validation flow."""

    def _parse_log_payload(self, entry: str) -> dict:
        return json.loads(entry[entry.find("{"):])

    def test_r1_plan1_get_provider_adapter_returns_expected_adapter(self) -> None:
        self.assertIsInstance(get_provider_adapter(Provider.OPENROUTER), OpenRouterAdapter)
        self.assertIsInstance(get_provider_adapter(Provider.OPENAI), OpenAIAdapter)
        self.assertIsInstance(get_provider_adapter(Provider.ANTHROPIC), AnthropicAdapter)
        self.assertIsInstance(get_provider_adapter(Provider.OLLAMA), OllamaAdapter)

    def test_r1_plan1_openrouter_lists_models_and_uses_bearer_auth(self) -> None:
        adapter = OpenRouterAdapter()
        client = FakeHttpClient(
            {
                adapter.models_url(): FakeResponse(
                    status_code=200,
                    payload={
                        "data": [
                            {"id": "openrouter/auto", "name": "Auto"},
                            {"id": "openai/gpt-4o-mini"},
                        ]
                    },
                )
            }
        )

        models = adapter.list_models("router-key", client=client)

        self.assertEqual(("openrouter/auto", "openai/gpt-4o-mini"), tuple(model.id for model in models))
        self.assertEqual(adapter.models_url(), client.calls[0][0])
        self.assertEqual(10.0, client.calls[0][2])
        self.assertEqual("Bearer router-key", client.calls[0][1]["Authorization"])
        self.assertEqual("application/json", client.calls[0][1]["Accept"])

    def test_r1_plan2_openai_validation_returns_models(self) -> None:
        adapter = OpenAIAdapter()
        client = FakeHttpClient(
            {
                adapter.models_url(): FakeResponse(
                    status_code=200,
                    payload={"data": [{"id": "gpt-4o-mini"}, {"id": "gpt-4.1-mini"}]},
                )
            }
        )

        result = adapter.validate_api_key("openai-key", client=client)

        self.assertTrue(result.ok)
        self.assertEqual(("gpt-4o-mini", "gpt-4.1-mini"), tuple(model.id for model in result.models))

    def test_r1_plan1_anthropic_validation_uses_required_headers(self) -> None:
        adapter = AnthropicAdapter()
        client = FakeHttpClient(
            {
                adapter.models_url(): FakeResponse(
                    status_code=200,
                    payload={"data": [{"id": "claude-3-5-haiku", "display_name": "Claude 3.5 Haiku"}]},
                )
            }
        )

        result = adapter.validate_api_key("anthropic-key", client=client)

        self.assertTrue(result.ok)
        self.assertEqual("anthropic-key", client.calls[0][1]["x-api-key"])
        self.assertEqual(ANTHROPIC_VERSION, client.calls[0][1]["anthropic-version"])

    def test_r1_plan2_validate_model_rejects_unknown_choice(self) -> None:
        adapter = OpenAIAdapter()
        client = FakeHttpClient(
            {
                adapter.models_url(): FakeResponse(
                    status_code=200,
                    payload={"data": [{"id": "gpt-4o-mini"}]},
                )
            }
        )

        result = adapter.validate_model("openai-key", "gpt-unknown", client=client)

        self.assertFalse(result.ok)
        self.assertIn("not available", result.message)

    def test_r1_plan2_http_error_is_reported_cleanly(self) -> None:
        adapter = OpenRouterAdapter()
        client = FakeHttpClient(
            {
                adapter.models_url(): FakeResponse(
                    status_code=401,
                    payload={},
                    text="Unauthorized",
                )
            }
        )

        result = adapter.validate_api_key("bad-key", client=client)

        self.assertFalse(result.ok)
        self.assertIn("HTTP 401", result.message)

    def test_r1_plan2_provider_failure_is_logged_without_secrets(self) -> None:
        adapter = OpenAIAdapter()
        client = FakeHttpClient(
            {
                adapter.models_url(): FakeResponse(
                    status_code=401,
                    payload={},
                    text="Unauthorized token=ghp_secretvalue12345",
                )
            }
        )

        with self.assertLogs("duckln", level=logging.ERROR) as captured:
            result = adapter.validate_api_key("sk-secret-12345678", client=client)

        payload = self._parse_log_payload(captured.output[0])
        self.assertFalse(result.ok)
        self.assertEqual("provider_failure", payload["event"])
        self.assertNotIn("sk-secret-12345678", captured.output[0])

    def test_openrouter_connection_failure_logs_specific_request_details_but_returns_concise_error(self) -> None:
        adapter = OpenRouterAdapter()
        client = RaisingHttpClient(OSError("DNS lookup failed for openrouter.ai"))

        with self.assertLogs("duckln", level=logging.ERROR) as captured:
            result = adapter.validate_api_key("router-key", client=client)

        payload = self._parse_log_payload(captured.output[0])
        self.assertFalse(result.ok)
        self.assertEqual("Failed to connect to OpenRouter. Please retry.", result.message)
        self.assertEqual("provider_failure", payload["event"])
        self.assertIn("url=https://openrouter.ai/api/v1/models", payload["message"])
        self.assertIn("timeout=10.0s", payload["message"])
        self.assertIn("OSError", payload["message"])
        self.assertIn("DNS lookup failed", payload["message"])
        self.assertIsNone(payload["metadata"]["status_code"])

    def test_openrouter_validate_api_key_returns_models_on_successful_response(self) -> None:
        adapter = OpenRouterAdapter()
        client = FakeHttpClient(
            {
                adapter.models_url(): FakeResponse(
                    status_code=200,
                    payload={
                        "data": [
                            {"id": "openrouter/auto", "name": "Auto"},
                            {"id": "openai/gpt-4o-mini", "name": "GPT-4o Mini"},
                        ]
                    },
                )
            }
        )

        result = adapter.validate_api_key("router-key", client=client)

        self.assertTrue(result.ok)
        self.assertEqual(("openrouter/auto", "openai/gpt-4o-mini"), tuple(model.id for model in result.models))

    def test_generate_provider_reply_uses_openrouter_chat_completion_endpoint(self) -> None:
        client = FakeHttpClient(
            {
                "https://openrouter.ai/api/v1/chat/completions": FakeResponse(
                    status_code=200,
                    payload={
                        "usage": {"prompt_tokens": 30, "completion_tokens": 12, "total_tokens": 42},
                        "choices": [
                            {
                                "message": {
                                    "content": "I’m ready to help with the next setup step."
                                }
                            }
                        ]
                    },
                )
            }
        )

        reply = generate_provider_reply(
            Provider.OPENROUTER,
            model_id="openai/gpt-4o-mini",
            api_key="router-key",
            base_url=None,
            system_prompt="You are Duckln.",
            user_message="hello",
            recent_turns=(("user", "hi"),),
            client=client,
        )

        self.assertEqual("I’m ready to help with the next setup step.", reply)
        self.assertEqual("https://openrouter.ai/api/v1/chat/completions", client.calls[0][0])
        self.assertEqual("Bearer router-key", client.calls[0][1]["Authorization"])

    def test_generate_provider_reply_records_usage_snapshot_when_config_dir_is_provided(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            reset_usage_snapshot()
            client = FakeHttpClient(
                {
                    "https://openrouter.ai/api/v1/chat/completions": FakeResponse(
                        status_code=200,
                        payload={
                            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                            "choices": [{"message": {"content": "ready"}}],
                        },
                    )
                }
            )

            reply = generate_provider_reply(
                Provider.OPENROUTER,
                model_id="openai/gpt-4o-mini",
                api_key="router-key",
                base_url=None,
                system_prompt="You are Duckln.",
                user_message="hello",
                client=client,
                config_dir=Path(temp_dir),
            )

            usage = initialize_state_store(Path(temp_dir)).get_usage_snapshot()
            self.assertEqual("ready", reply)
            assert usage is not None
            self.assertEqual(15, usage.total_tokens)

    def test_r1_plan1_plan2_openrouter_full_validation_flow_succeeds(self) -> None:
        adapter = OpenRouterAdapter()
        client = FakeHttpClient(
            {
                adapter.models_url(): FakeResponse(
                    status_code=200,
                    payload={"data": [{"id": "openrouter/auto", "name": "Auto"}]},
                )
            }
        )

        api_key_result = adapter.validate_api_key("router-key", client=client)
        model_result = adapter.validate_model(
            "router-key",
            "openrouter/auto",
            client=client,
            models=api_key_result.models,
        )

        self.assertTrue(api_key_result.ok)
        self.assertEqual(("openrouter/auto",), tuple(model.id for model in api_key_result.models))
        self.assertTrue(model_result.ok)
        self.assertEqual("Bearer router-key", client.calls[0][1]["Authorization"])

    def test_r1_plan1_plan2_openai_full_validation_flow_succeeds(self) -> None:
        adapter = OpenAIAdapter()
        client = FakeHttpClient(
            {
                adapter.models_url(): FakeResponse(
                    status_code=200,
                    payload={"data": [{"id": "gpt-4o-mini"}, {"id": "gpt-4.1-mini"}]},
                )
            }
        )

        api_key_result = adapter.validate_api_key("openai-key", client=client)
        model_result = adapter.validate_model(
            "openai-key",
            "gpt-4o-mini",
            client=client,
            models=api_key_result.models,
        )

        self.assertTrue(api_key_result.ok)
        self.assertTrue(model_result.ok)
        self.assertEqual("Bearer openai-key", client.calls[0][1]["Authorization"])

    def test_r1_plan1_plan2_anthropic_full_validation_flow_succeeds(self) -> None:
        adapter = AnthropicAdapter()
        client = FakeHttpClient(
            {
                adapter.models_url(): FakeResponse(
                    status_code=200,
                    payload={"data": [{"id": "claude-3-5-haiku", "display_name": "Claude 3.5 Haiku"}]},
                )
            }
        )

        api_key_result = adapter.validate_api_key("anthropic-key", client=client)
        model_result = adapter.validate_model(
            "anthropic-key",
            "claude-3-5-haiku",
            client=client,
            models=api_key_result.models,
        )

        self.assertTrue(api_key_result.ok)
        self.assertTrue(model_result.ok)
        self.assertEqual("anthropic-key", client.calls[0][1]["x-api-key"])

    def test_ollama_validation_lists_local_models_without_api_key(self) -> None:
        adapter = OllamaAdapter()
        client = FakeHttpClient(
            {
                adapter.models_url(): FakeResponse(
                    status_code=200,
                    payload={"models": [{"name": "llama3.2:latest"}, {"name": "qwen2.5:7b"}]},
                )
            }
        )

        api_key_result = adapter.validate_api_key(None, client=client)
        model_result = adapter.validate_model(None, "llama3.2:latest", client=client, models=api_key_result.models)

        self.assertTrue(api_key_result.ok)
        self.assertEqual(("llama3.2:latest", "qwen2.5:7b"), tuple(model.id for model in api_key_result.models))
        self.assertTrue(model_result.ok)
        self.assertEqual("http://localhost:11434/api/tags", client.calls[0][0])
        self.assertNotIn("Authorization", client.calls[0][1])

    def test_ollama_adapter_uses_custom_base_url_and_normalizes_api_tags_suffix(self) -> None:
        adapter = get_provider_adapter_for_base_url(
            Provider.OLLAMA,
            base_url="http://localhost:11555/api/tags",
        )
        client = FakeHttpClient(
            {
                "http://localhost:11555/api/tags": FakeResponse(
                    status_code=200,
                    payload={"models": [{"name": "llama3.2:latest"}]},
                )
            }
        )

        result = adapter.validate_api_key(None, client=client)

        self.assertTrue(result.ok)
        self.assertEqual("Ollama is reachable at http://localhost:11555/api/tags.", result.message)
        self.assertEqual("http://localhost:11555/api/tags", client.calls[0][0])

    def test_ollama_validation_distinguishes_installed_but_not_running(self) -> None:
        adapter = OllamaAdapter()
        client = RaisingHttpClient(OSError("connection refused"))

        with patch("duckln.ai_client.shutil.which", return_value="/usr/local/bin/ollama"):
            result = adapter.validate_api_key(None, client=client)

        self.assertFalse(result.ok)
        self.assertEqual(
            "Ollama is installed but not running at http://localhost:11434/api/tags. "
            "Start it with `ollama serve`, then retry detection.",
            result.message,
        )

    def test_ollama_pull_model_posts_model_name_and_refreshes_tags(self) -> None:
        adapter = OllamaAdapter()
        client = FakeHttpClient(
            {
                "http://localhost:11434/api/tags": FakeResponse(
                    status_code=200,
                    payload={"models": [{"name": "mistral:latest"}]},
                ),
                "http://localhost:11434/api/pull": FakeResponse(
                    status_code=200,
                    payload={"status": "success"},
                ),
            }
        )

        result = adapter.pull_model(None, "mistral:latest", client=client)

        self.assertTrue(result.ok)
        self.assertEqual("Ollama model mistral:latest is ready locally.", result.message)
        self.assertEqual("http://localhost:11434/api/pull", client.calls[0][0])


class ConversationTimeoutPlan69Test(unittest.TestCase):
    """Plan 69 Fix 1: conversation POST uses the generous conversation timeout
    (not the fast model-list timeout) and retries once on a transport error."""

    def test_ollama_conversation_uses_conversation_timeout(self) -> None:
        from duckln.ai_client import OLLAMA_CONVERSATION_TIMEOUT_SECONDS

        adapter = OllamaAdapter()
        url = adapter.conversation_url()
        client = FakeHttpClient({url: FakeResponse(status_code=200, payload={"response": "hello"})})
        text = adapter.generate_reply(
            None, model_id="gemma2:2b", system_prompt="sys", user_message="hi", client=client,
        )
        self.assertEqual(text, "hello")
        # The POST must use the long conversation timeout, NOT the 2s tags timeout.
        self.assertEqual(client.calls[0][2], OLLAMA_CONVERSATION_TIMEOUT_SECONDS)
        self.assertNotEqual(client.calls[0][2], adapter.timeout_seconds)

    def test_cloud_conversation_uses_conversation_timeout(self) -> None:
        from duckln.ai_client import CONVERSATION_TIMEOUT_SECONDS, OpenAIAdapter

        adapter = OpenAIAdapter()
        url = adapter.conversation_url()
        client = FakeHttpClient({
            url: FakeResponse(
                status_code=200,
                payload={"choices": [{"message": {"content": "ok"}}]},
            )
        })
        adapter.generate_reply(
            "key", model_id="gpt-4o-mini", system_prompt="s", user_message="u", client=client,
        )
        self.assertEqual(client.calls[0][2], CONVERSATION_TIMEOUT_SECONDS)

    def test_conversation_retries_once_on_transport_error(self) -> None:
        adapter = OllamaAdapter()
        url = adapter.conversation_url()

        class FlakyClient:
            def __init__(self):
                self.calls = 0

            def post(self, u, *, headers, json, timeout):
                self.calls += 1
                if self.calls == 1:
                    raise TimeoutError("cold load")
                return FakeResponse(status_code=200, payload={"response": "recovered"})

        client = FlakyClient()
        text = adapter.generate_reply(
            None, model_id="gemma2:2b", system_prompt="s", user_message="u", client=client,
        )
        self.assertEqual(text, "recovered")
        self.assertEqual(client.calls, 2)


if __name__ == "__main__":
    unittest.main()
