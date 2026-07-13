"""Plan 119 — real-agent fixes: truthful provider status + mandatory LLM gate +
reliable fix-attribution (JSON mode / retry / truncation repair) + polyglot build
ordering + Ollama pull progress collapsing."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from duckln import connection_status as cs
from duckln.ai_client import _build_openai_style_conversation_payload
from duckln.diagnostics import ErrorCategory, match_deterministic_fix
from duckln.plan_mode import (
    _attribution_llm_call,
    _repair_truncated_json,
    parse_plan_json,
)
from duckln.repo_bringup import _secondary_python_setup_steps
from duckln.textual_ui import _is_ollama_pull_noise, _transient_activity_update


# --- P119.1: truthful provider status ----------------------------------------


class TruthfulProviderStatus(unittest.TestCase):
    def setUp(self):
        cs.clear_cache()

    def _cfg(self):
        cfg = MagicMock()
        cfg.provider = "openai"
        cfg.model = "gpt-5-mini"  # a model NOT in the listed set
        cfg.base_url = "https://api.openai.com/v1"
        cfg.api_key = "test-key"
        return cfg

    def test_green_when_connectivity_ok_even_if_model_not_listed(self):
        # The old behaviour returned RED when the model id wasn't enumerated; now
        # the dot reflects validate_api_key (true connectivity) → GREEN.
        sock = MagicMock(); sock.__enter__ = lambda s: s; sock.__exit__ = lambda s, *a: None
        adapter = MagicMock()
        adapter.validate_api_key.return_value = SimpleNamespace(ok=True)
        adapter.validate_model.return_value = SimpleNamespace(ok=False)  # would be RED before
        with patch("socket.create_connection", return_value=sock), \
             patch("duckln.ai_client.get_provider_adapter_for_base_url", return_value=adapter):
            self.assertEqual(cs.probe_provider_status(self._cfg(), timeout=0.1), "green")
        adapter.validate_api_key.assert_called_once()

    def test_green_for_default_url_provider_with_no_config_base_url(self):
        # Plan 121: OpenAI/OpenRouter/Anthropic store base_url=None; the dot must
        # fall back to the adapter's URL for the reachability check, not return RED.
        cfg = MagicMock()
        cfg.provider = "openai"; cfg.model = "gpt-5"; cfg.base_url = None; cfg.api_key = "sk-proj-x"
        sock = MagicMock(); sock.__enter__ = lambda s: s; sock.__exit__ = lambda s, *a: None
        adapter = MagicMock()
        adapter.base_url = "https://api.openai.com/v1"
        adapter.validate_api_key.return_value = SimpleNamespace(ok=True)
        with patch("socket.create_connection", return_value=sock), \
             patch("duckln.ai_client.get_provider_adapter_for_base_url", return_value=adapter):
            self.assertEqual(cs.probe_provider_status(cfg, timeout=0.1), "green")
        adapter.validate_api_key.assert_called_once()  # the host check no longer short-circuits

    def test_red_when_connectivity_fails(self):
        sock = MagicMock(); sock.__enter__ = lambda s: s; sock.__exit__ = lambda s, *a: None
        adapter = MagicMock()
        adapter.validate_api_key.return_value = SimpleNamespace(ok=False)
        with patch("socket.create_connection", return_value=sock), \
             patch("duckln.ai_client.get_provider_adapter_for_base_url", return_value=adapter):
            self.assertEqual(cs.probe_provider_status(self._cfg(), timeout=0.1), "red")


# --- P119.2: mandatory-but-correct gate --------------------------------------


class ProviderGate(unittest.TestCase):
    def setUp(self):
        cs.clear_cache()

    def test_no_config_blocks_with_message(self):
        r = cs.ensure_provider_connected(None)
        self.assertFalse(r.connected)
        # Plan 182 F5: clear "select a provider and model" message with the slash hints.
        self.assertIn("/provider", r.message)
        self.assertIn("/model", r.message)

    def test_connected_never_blocks(self):
        cfg = SimpleNamespace(provider="openai", model="m")
        with patch("duckln.connection_status.probe_provider_status", return_value="green"):
            r = cs.ensure_provider_connected(cfg)
        self.assertTrue(r.connected)
        self.assertEqual(r.message, "")

    def test_disconnected_blocks_with_actionable_message(self):
        cfg = SimpleNamespace(provider="openai", model="m")
        with patch("duckln.connection_status.probe_provider_status", return_value="red"):
            r = cs.ensure_provider_connected(cfg)
        self.assertFalse(r.connected)
        self.assertIn("/healthcheck", r.message)


# --- P119.3: reliable fix-attribution ----------------------------------------


class TruncationRepair(unittest.TestCase):
    def test_fenced_json_parses(self):
        out = parse_plan_json('```json\n{"cause": "x", "fix": null}\n```')
        self.assertEqual(out["cause"], "x")

    def test_prose_preamble_then_json(self):
        out = parse_plan_json('We need to produce JSON. {"cause":"e","fix":{"command":"python -m venv .venv"}}')
        self.assertEqual(out["fix"]["command"], "python -m venv .venv")

    def test_truncated_object_recovers(self):
        # The screenshots' cut-off response — no closing brace/quote.
        raw = '```json\n{\n "cause": "venv missing", "fix": null, "rationale": "A new user must set up the env'
        out = parse_plan_json(raw)
        self.assertEqual(out["cause"], "venv missing")
        self.assertIsNone(out["fix"])

    def test_repair_returns_none_for_garbage(self):
        self.assertIsNone(_repair_truncated_json("no json here at all"))


class AttributionCall(unittest.TestCase):
    def test_uses_json_mode_and_large_budget(self):
        seen = {}

        def client(*, system_prompt, user_message, max_tokens=None, json_mode=False):
            seen["max_tokens"] = max_tokens
            seen["json_mode"] = json_mode
            return '{"cause":"c","fix":null}'

        out = _attribution_llm_call(client, system="S", user_message="U")
        self.assertEqual(out["cause"], "c")
        self.assertTrue(seen["json_mode"])
        self.assertGreaterEqual(seen["max_tokens"], 512)

    def test_retries_once_with_strict_suffix_on_parse_failure(self):
        calls = []

        def client(*, system_prompt, user_message, max_tokens=None, json_mode=False):
            calls.append(user_message)
            if len(calls) == 1:
                return "I cannot answer in JSON, sorry."  # unparseable
            return '{"cause":"recovered","fix":null}'

        out = _attribution_llm_call(client, system="S", user_message="U")
        self.assertEqual(out["cause"], "recovered")
        self.assertEqual(len(calls), 2)
        self.assertIn("ONLY", calls[1])  # strict reprompt appended

    def test_degrades_to_plain_client(self):
        # A fake that only accepts the 2 base kwargs must still work.
        def client(*, system_prompt, user_message):
            return '{"cause":"plain","fix":null}'

        out = _attribution_llm_call(client, system="S", user_message="U")
        self.assertEqual(out["cause"], "plain")


class JsonModePayload(unittest.TestCase):
    def test_openai_payload_sets_response_format_and_budget(self):
        p = _build_openai_style_conversation_payload(
            model_id="gpt", system_prompt="s", user_message="u",
            recent_turns=(), max_tokens=1024, json_mode=True,
        )
        self.assertEqual(p["response_format"], {"type": "json_object"})
        self.assertEqual(p["max_tokens"], 1024)

    def test_openai_payload_default_no_json_mode(self):
        p = _build_openai_style_conversation_payload(
            model_id="gpt", system_prompt="s", user_message="u", recent_turns=(),
        )
        self.assertNotIn("response_format", p)
        self.assertEqual(p["max_tokens"], 220)


# --- P119.4: polyglot build ordering -----------------------------------------


class DeterministicVenvFix(unittest.TestCase):
    def test_missing_venv_maps_to_create_venv(self):
        f = match_deterministic_fix(
            stderr="Error: Python virtual environment not found: /home/u/proj/backend/.venv/bin/python",
            execution_target="vm",
        )
        self.assertIsNotNone(f)
        self.assertEqual(f.category, ErrorCategory.MISSING_VENV)
        self.assertIn('cd "/home/u/proj/backend"', f.fix_command)
        self.assertIn("python3 -m venv", f.fix_command)
        self.assertIn("requirements.txt", f.fix_command)

    def test_no_match_for_unrelated_error(self):
        f = match_deterministic_fix(stderr="TypeError: undefined is not a function", execution_target="local")
        if f is not None:
            self.assertNotEqual(f.category, ErrorCategory.MISSING_VENV)


class SecondaryEcosystemOrdering(unittest.TestCase):
    def test_target_side_step_emitted_without_local_clone(self):
        # Plan 120: no host filesystem dependency — the step detects the backend at
        # runtime on the TARGET, so a VM/container/cloud repo is covered.
        steps = _secondary_python_setup_steps(("package.json",), has_prebuild=True)
        self.assertTrue(steps)
        title, cmd, verify = steps[0]
        self.assertIn("python3 -m venv .venv", cmd)
        self.assertIn("for d in */", cmd)                 # scans subdirs on the target
        self.assertIn(".duckln-deps-ok", cmd)             # Plan 127 completion-marker guard
        self.assertIn("requirements.txt", cmd)

    def test_no_node_means_no_steps(self):
        self.assertEqual(_secondary_python_setup_steps(("requirements.txt",), has_prebuild=True), ())

    def test_no_prebuild_means_no_steps(self):
        self.assertEqual(_secondary_python_setup_steps(("package.json",), has_prebuild=False), ())


# --- P119.5: Ollama pull progress collapsing ---------------------------------


class OllamaProgress(unittest.TestCase):
    def test_pulling_manifest_with_spinner_collapses(self):
        self.assertEqual(_transient_activity_update("pulling manifest")[0], "Ollama pull — pulling manifest")
        self.assertIsNotNone(_transient_activity_update("pulling manifest ⠴ 24s"))

    def test_percent_line_renders_bar(self):
        out = _transient_activity_update("pulling abc123... 45% 1.2 GB/2.0 GB")
        self.assertIn("45%", out[0])
        self.assertIn("[", out[0])

    def test_hash_line_without_percent_collapses(self):
        self.assertEqual(_transient_activity_update("pulling abc123def")[0], "Ollama pull — downloading…")

    def test_bare_noise_detected(self):
        self.assertTrue(_is_ollama_pull_noise("24s"))
        self.assertTrue(_is_ollama_pull_noise("2.0 GB"))
        self.assertTrue(_is_ollama_pull_noise("⠴"))
        self.assertFalse(_is_ollama_pull_noise("Installing dependencies"))


if __name__ == "__main__":
    unittest.main()
