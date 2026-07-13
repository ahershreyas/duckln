"""Plan 165 — Ollama live-check HTTP 500: surface the real error body, classify it, and
auto-fall-back to /api/chat on a generate-5xx (test the endpoint hypothesis without assuming)."""

from __future__ import annotations

import json
import unittest

from duckln.ai_client import (
    OllamaAdapter,
    ProviderRequestError,
    _extract_error_body,
    _ollama_error_hint,
)


class _Resp:
    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self._body = body
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        return self._body if isinstance(self._body, dict) else json.loads(self._body)


class F1ErrorBodySurfaced(unittest.TestCase):
    def test_extract_error_field(self):
        body = _extract_error_body(_Resp(500, {"error": "model requires more system memory (5.0 GiB) than is available (3.1 GiB)"}))
        self.assertIn("more system memory", body)

    def test_extract_falls_back_to_text(self):
        self.assertIn("boom", _extract_error_body(_Resp(500, "boom plaintext")))

    def test_base_generate_reply_includes_body(self):
        # An OpenRouter-style adapter surfacing a 500 body (via the shared base path).
        from duckln.ai_client import OpenRouterAdapter

        class _Client:
            def post(self, url, headers=None, json=None, timeout=None):
                return _Resp(500, {"error": {"message": "context length exceeded"}})

        with self.assertRaises(ProviderRequestError) as ctx:
            OpenRouterAdapter().generate_reply("k", model_id="m", system_prompt="s", user_message="u", client=_Client())
        self.assertIn("context length exceeded", ctx.exception.public_message)


class F2ActionableHint(unittest.TestCase):
    def test_oom_hint(self):
        self.assertIn("smaller model", _ollama_error_hint("model requires more system memory", "m"))

    def test_not_found_hint(self):
        self.assertIn("ollama pull llama3.2", _ollama_error_hint("model 'llama3.2' not found, try pulling it first", "llama3.2"))

    def test_unknown_no_hint(self):
        self.assertEqual("", _ollama_error_hint("some other error", "m"))


class F3ChatFallback(unittest.TestCase):
    def test_generate_500_falls_back_to_chat(self):
        class _Client:
            def post(self, url, headers=None, json=None, timeout=None):
                if url.endswith("/api/generate"):
                    return _Resp(500, {"error": "template error"})
                return _Resp(200, {"message": {"content": "hi from chat"}})

        adapter = OllamaAdapter(base_url="http://localhost:11434")
        out = adapter.generate_reply(None, model_id="m", system_prompt="s", user_message="u", client=_Client())
        self.assertEqual("hi from chat", out)
        self.assertFalse(adapter._use_chat)  # flag reset after the fallback

    def test_both_fail_surfaces_original_body(self):
        class _Client:
            def post(self, url, headers=None, json=None, timeout=None):
                return _Resp(500, {"error": "model requires more system memory"})

        adapter = OllamaAdapter(base_url="http://localhost:11434")
        with self.assertRaises(ProviderRequestError) as ctx:
            adapter.generate_reply(None, model_id="m", system_prompt="s", user_message="u", client=_Client())
        msg = ctx.exception.public_message
        self.assertIn("system memory", msg)     # F1 body
        self.assertIn("smaller model", msg)      # F2 hint

    def test_no_fallback_on_4xx(self):
        # A 404 (model-not-found) is not an endpoint problem — no chat retry; hint surfaces.
        calls = {"chat": 0}

        class _Client:
            def post(self, url, headers=None, json=None, timeout=None):
                if url.endswith("/api/chat"):
                    calls["chat"] += 1
                return _Resp(404, {"error": "model 'm' not found, try pulling it first"})

        adapter = OllamaAdapter(base_url="http://localhost:11434")
        with self.assertRaises(ProviderRequestError) as ctx:
            adapter.generate_reply(None, model_id="m", system_prompt="s", user_message="u", client=_Client())
        self.assertEqual(0, calls["chat"])  # no endpoint fallback on a 4xx
        self.assertIn("ollama pull m", ctx.exception.public_message)

    def test_default_uses_generate_endpoint(self):
        adapter = OllamaAdapter(base_url="http://localhost:11434")
        self.assertTrue(adapter.conversation_url().endswith("/api/generate"))
        payload = adapter.build_conversation_payload(
            model_id="m", system_prompt="s", user_message="u", recent_turns=(),
        )
        self.assertIn("prompt", payload)  # default generate shape unchanged


if __name__ == "__main__":
    unittest.main()
