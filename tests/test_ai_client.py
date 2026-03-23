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
import unittest

from duckln.ai_client import (
    ANTHROPIC_VERSION,
    AnthropicAdapter,
    OpenAIAdapter,
    OpenRouterAdapter,
    Provider,
    get_provider_adapter,
)


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


class ProviderAdaptersReqR1Plan1Plan2Test(unittest.TestCase):
    """Covers adapter selection, auth headers, model listing, and validation flow."""

    def _parse_log_payload(self, entry: str) -> dict:
        return json.loads(entry[entry.find("{"):])

    def test_r1_plan1_get_provider_adapter_returns_expected_adapter(self) -> None:
        self.assertIsInstance(get_provider_adapter(Provider.OPENROUTER), OpenRouterAdapter)
        self.assertIsInstance(get_provider_adapter(Provider.OPENAI), OpenAIAdapter)
        self.assertIsInstance(get_provider_adapter(Provider.ANTHROPIC), AnthropicAdapter)

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
        self.assertEqual("Bearer router-key", client.calls[0][1]["Authorization"])

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


if __name__ == "__main__":
    unittest.main()
