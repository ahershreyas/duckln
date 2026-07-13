"""Plan 158 — the model-unreachable hint is provider-aware (no `ollama serve` for a cloud API)."""

from __future__ import annotations

import unittest

from duckln.plan_mode import MODEL_UNREACHABLE_MESSAGE, model_unreachable_message


class ProviderAwareUnreachableMessage(unittest.TestCase):
    def test_openrouter_has_no_ollama_and_mentions_api_key_and_429(self):
        msg = model_unreachable_message("openrouter")
        self.assertNotIn("ollama", msg.lower())
        self.assertIn("OpenRouter", msg)
        self.assertIn("API key", msg)
        self.assertIn("429", msg)

    def test_openai_and_anthropic_are_cloud_phrased(self):
        for prov in ("openai", "anthropic", "google"):
            msg = model_unreachable_message(prov)
            self.assertNotIn("ollama", msg.lower())
            self.assertIn("API key", msg)

    def test_ollama_keeps_serve_hint(self):
        msg = model_unreachable_message("ollama")
        self.assertIn("ollama serve", msg)

    def test_unknown_provider_is_neutral(self):
        self.assertEqual(model_unreachable_message(""), MODEL_UNREACHABLE_MESSAGE)
        self.assertEqual(model_unreachable_message(None), MODEL_UNREACHABLE_MESSAGE)

    def test_neutral_default_has_no_ollama(self):
        self.assertNotIn("ollama", MODEL_UNREACHABLE_MESSAGE.lower())


if __name__ == "__main__":
    unittest.main()
