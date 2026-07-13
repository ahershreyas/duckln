"""Plan 121 — truthful dot cache-busting, real round-trip verification, and the
masked-secret prompt wiring."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from duckln import connection_status as cs
from duckln.ai_client import Provider
from duckln.config import verify_live_reply
from duckln.main import _build_chat_secret_prompt


# --- Real round-trip verification --------------------------------------------


class VerifyLiveReply(unittest.TestCase):
    def test_shows_real_reply_on_success(self):
        msgs = []
        with patch("duckln.ai_client.generate_provider_reply", return_value="Got it — connection works.") as gen:
            ok = verify_live_reply(
                provider=Provider.OPENAI, model="gpt-5", api_key="k", base_url=None, display=msgs.append,
            )
        self.assertTrue(ok)
        self.assertTrue(any("replied:" in m and "connection works" in m for m in msgs))
        # bounded budget — proof not a long generation
        self.assertEqual(gen.call_args.kwargs.get("max_tokens"), 60)

    def test_returns_false_and_shows_error_on_failure(self):
        msgs = []
        with patch("duckln.ai_client.generate_provider_reply", side_effect=RuntimeError("401 unauthorized")):
            ok = verify_live_reply(
                provider=Provider.OPENAI, model="gpt-5", api_key="bad", base_url=None, display=msgs.append,
            )
        self.assertFalse(ok)
        self.assertTrue(any("did not reply" in m and "401" in m for m in msgs))

    def test_empty_reply_is_failure(self):
        msgs = []
        with patch("duckln.ai_client.generate_provider_reply", return_value="   "):
            ok = verify_live_reply(
                provider=Provider.OLLAMA, model="qwen", api_key=None, base_url="http://localhost:11434", display=msgs.append,
            )
        self.assertFalse(ok)
        self.assertTrue(any("empty reply" in m for m in msgs))

    def test_never_prints_the_key(self):
        msgs = []
        with patch("duckln.ai_client.generate_provider_reply", return_value="ok"):
            verify_live_reply(provider=Provider.OPENAI, model="gpt-5", api_key="sk-proj-SECRET", base_url=None, display=msgs.append)
        self.assertFalse(any("SECRET" in m for m in msgs))


# --- Cache busts when credentials change -------------------------------------


class CacheKeyBustsOnCredentialChange(unittest.TestCase):
    def setUp(self):
        cs.clear_cache()

    def test_key_presence_changes_cache_key(self):
        # Same provider+model but no key vs key-present must not share a cached value.
        no_key = SimpleNamespace(provider="openai", model="gpt-5", base_url=None, api_key=None)
        with_key = SimpleNamespace(provider="openai", model="gpt-5", base_url=None, api_key="k")
        sock = type("S", (), {"__enter__": lambda s: s, "__exit__": lambda s, *a: None})()
        adapter_red = SimpleNamespace(base_url="https://api.openai.com/v1", validate_api_key=lambda *a, **k: SimpleNamespace(ok=False))
        adapter_green = SimpleNamespace(base_url="https://api.openai.com/v1", validate_api_key=lambda *a, **k: SimpleNamespace(ok=True))
        with patch("socket.create_connection", return_value=sock):
            with patch("duckln.ai_client.get_provider_adapter_for_base_url", return_value=adapter_red):
                self.assertEqual(cs.probe_provider_status(no_key, timeout=0.1), "red")
            with patch("duckln.ai_client.get_provider_adapter_for_base_url", return_value=adapter_green):
                # Different cache key (api_key now present) → fresh probe → green, not stale red.
                self.assertEqual(cs.probe_provider_status(with_key, timeout=0.1), "green")


# --- Masked secret prompt wiring ---------------------------------------------


class MaskedSecretPrompt(unittest.TestCase):
    def test_uses_prompt_secret_when_live(self):
        captured = {}

        class FakeChat:
            supports_live = True

            def prompt_secret(self, message, **kwargs):
                captured["message"] = message
                return "  sk-proj-abc  "

            def prompt(self, *a, **k):  # must NOT be used in the live masked path
                captured["fell_back"] = True
                return ""

        prompt = _build_chat_secret_prompt(chat=FakeChat(), chat_input=lambda *_: "", display=lambda *_: None)
        result = prompt("Enter your OpenAI API key:")
        self.assertEqual(result, "sk-proj-abc")        # stripped
        self.assertIn("API key", captured["message"])
        self.assertNotIn("fell_back", captured)         # masked path used, no echo fallback

    def test_falls_back_when_not_live(self):
        used = {}

        class FakeChat:
            supports_live = False

            def prompt(self, *a, record_input=True, **k):
                used["record_input"] = record_input
                return "typed-key"

        prompt = _build_chat_secret_prompt(chat=FakeChat(), chat_input=lambda *_: "typed-key", display=lambda *_: None)
        result = prompt("Enter your key:")
        self.assertEqual(result, "typed-key")
        self.assertFalse(used["record_input"])          # fallback still never records into history


if __name__ == "__main__":
    unittest.main()
