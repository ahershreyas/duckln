"""Plan 182 — consistent LIVE connection test on every provider/model change, a header that's
green only when the model truly answered (no fake green), a clear "select a provider and model"
guard before running a repo, a curated Ollama pull list, and an ollama-serve readiness wait."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from duckln import config as cfgmod
from duckln import connection_status as cs
from duckln.config import _select_ollama_model_to_pull, OLLAMA_PULL_TYPE_CUSTOM_CHOICE


class F1ModelLiveTest(unittest.TestCase):
    def test_model_not_switched_when_live_reply_fails(self):
        from duckln.ai_client import Provider, ProviderModel

        current = SimpleNamespace(
            provider=Provider.OPENAI, model="old-model", api_key="k", base_url=None,
        )
        fake_adapter = MagicMock()
        fake_adapter.provider = Provider.OPENAI
        fake_adapter.base_url = None
        fake_adapter.validate_api_key.return_value = SimpleNamespace(
            ok=True, models=(ProviderModel(id="new-model", display_name="new-model"),), message="ok",
        )
        fake_adapter.validate_model.return_value = SimpleNamespace(ok=True, message="ok")
        displayed: list[str] = []
        with TemporaryDirectory() as d:
            paths = SimpleNamespace(config_dir=Path(d))
            with patch("duckln.config.get_provider_adapter_for_base_url", return_value=fake_adapter), \
                 patch("duckln.config.verify_live_reply", return_value=False) as live:
                result = cfgmod.update_runtime_model(
                    current, paths,
                    select=lambda _p, _c: "new-model",
                    text_prompt=lambda _p, _d="": "",
                    display=displayed.append,
                    client=None,
                )
            live.assert_called_once()  # the live test ran
            self.assertEqual(result.model, "old-model")  # NOT switched
            self.assertTrue(any("did not return a live reply" in m for m in displayed))


class F4HeaderTruthfulGreen(unittest.TestCase):
    def setUp(self):
        cs.clear_cache()

    def _patches(self):
        fake_sock = MagicMock()
        fake_sock.__enter__ = lambda s: s
        fake_sock.__exit__ = lambda s, *a: None
        fake_adapter = MagicMock()
        fake_adapter.validate_api_key.return_value = SimpleNamespace(ok=True)
        return fake_sock, fake_adapter

    def test_green_only_with_verified_marker(self):
        fake_sock, fake_adapter = self._patches()
        cfg = SimpleNamespace(
            provider=SimpleNamespace(value="openai"), model="gpt-4o",
            base_url="https://api.openai.com/v1", api_key="k",
        )
        with TemporaryDirectory() as d, \
             patch("socket.create_connection", return_value=fake_sock), \
             patch("duckln.ai_client.get_provider_adapter_for_base_url", return_value=fake_adapter):
            cd = Path(d)
            # Reachable but NOT yet live-verified → orange (no fake green).
            self.assertEqual(cs.probe_provider_status(cfg, config_dir=cd), "orange")
            cs.clear_cache()
            # After a verified live reply for this exact (provider, model) → green.
            cs.record_live_verified(cd, provider=cfg.provider, model="gpt-4o")
            self.assertEqual(cs.probe_provider_status(cfg, config_dir=cd), "green")
            cs.clear_cache()
            # A different model is not covered by the marker → orange again.
            cfg2 = SimpleNamespace(provider=cfg.provider, model="gpt-4o-mini", base_url=cfg.base_url, api_key="k")
            self.assertEqual(cs.probe_provider_status(cfg2, config_dir=cd), "orange")

    def test_no_config_dir_keeps_reachability_signal(self):
        fake_sock, fake_adapter = self._patches()
        cfg = SimpleNamespace(
            provider=SimpleNamespace(value="openai"), model="gpt-4o",
            base_url="https://api.openai.com/v1", api_key="k",
        )
        with patch("socket.create_connection", return_value=fake_sock), \
             patch("duckln.ai_client.get_provider_adapter_for_base_url", return_value=fake_adapter):
            self.assertEqual(cs.probe_provider_status(cfg), "green")  # backward-compatible


class F5RepoRunGuard(unittest.TestCase):
    def test_no_model_returns_select_message(self):
        r = cs.ensure_provider_connected(SimpleNamespace(provider="openai", model=""))
        self.assertFalse(r.connected)
        self.assertIn("/provider", r.message)
        self.assertIn("/model", r.message)

    def test_reachable_orange_is_allowed(self):
        with patch("duckln.connection_status.probe_provider_status", return_value="orange"):
            r = cs.ensure_provider_connected(SimpleNamespace(provider="openai", model="m"))
        self.assertTrue(r.connected)

    def test_red_is_blocked_with_hints(self):
        with patch("duckln.connection_status.probe_provider_status", return_value="red"):
            r = cs.ensure_provider_connected(SimpleNamespace(provider="openai", model="m"))
        self.assertFalse(r.connected)
        self.assertIn("/provider", r.message)


class F2OllamaPullList(unittest.TestCase):
    def test_picks_from_curated_list(self):
        picked = _select_ollama_model_to_pull(
            lambda _p, opts: opts[1], lambda _p, _d="": "", installed_ids=(), ram_gib=8,
        )
        self.assertTrue(picked)
        self.assertNotIn("[recommended", picked)  # returns the id, not the label

    def test_type_custom_falls_back_to_text(self):
        picked = _select_ollama_model_to_pull(
            lambda _p, _opts: OLLAMA_PULL_TYPE_CUSTOM_CHOICE,
            lambda _p, _d="": "some-exotic:tag", installed_ids=(), ram_gib=8,
        )
        self.assertEqual(picked, "some-exotic:tag")

    def test_cancel_returns_empty(self):
        picked = _select_ollama_model_to_pull(
            lambda _p, _opts: None, lambda _p, _d="": "", installed_ids=(), ram_gib=8,
        )
        self.assertEqual(picked, "")

    def test_excludes_installed(self):
        installed = ("llama3.2:1b",)
        captured = {}

        def _select(_prompt, opts):
            captured["opts"] = opts
            return OLLAMA_PULL_TYPE_CUSTOM_CHOICE

        _select_ollama_model_to_pull(_select, lambda _p, _d="": "x", installed_ids=installed, ram_gib=8)
        self.assertFalse(any("llama3.2:1b " in o for o in captured["opts"]))


class F3OllamaReadinessWait(unittest.TestCase):
    def test_waits_until_adapter_reports_ready(self):
        displayed: list[str] = []
        fake_adapter = MagicMock()
        fake_adapter.validate_api_key.return_value = SimpleNamespace(ok=True)
        with patch("duckln.config.evaluate_mode_action", return_value=SimpleNamespace(allowed=True, reason="")), \
             patch("duckln.config.subprocess.Popen", return_value=MagicMock()):
            cfgmod._start_ollama_runtime(
                mode=None, display=displayed.append, adapter=fake_adapter, readiness_timeout=2.0,
            )
        self.assertTrue(any("Ollama is up" in m for m in displayed))


if __name__ == "__main__":
    unittest.main()
