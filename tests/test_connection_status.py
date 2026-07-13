"""Tests: connection_status probes and dot rendering (Plan 62)."""

from __future__ import annotations

import socket
import unittest
from unittest.mock import MagicMock, patch

from duckln import connection_status
from duckln.connection_status import (
    clear_cache,
    dot_for,
    probe_internet_status,
    probe_provider_status,
    probe_target_status,
    startup_offline_notice,
)


class DotForTest(unittest.TestCase):
    def test_green_dot_has_green_markup(self) -> None:
        self.assertIn("green", dot_for("green"))
        self.assertIn("●", dot_for("green"))

    def test_orange_dot_has_orange_markup(self) -> None:
        self.assertIn("orange", dot_for("orange").lower())
        self.assertIn("●", dot_for("orange"))

    def test_red_dot_has_red_markup(self) -> None:
        self.assertIn("red", dot_for("red"))
        self.assertIn("●", dot_for("red"))

    def test_plain_strips_markup(self) -> None:
        plain = dot_for("green", plain=True)
        self.assertNotIn("[", plain)
        self.assertEqual(plain, "●")


class ProbeInternetStatusTest(unittest.TestCase):
    def setUp(self) -> None:
        clear_cache()

    def test_returns_green_when_connection_succeeds(self) -> None:
        fake_sock = MagicMock()
        fake_sock.__enter__ = lambda self: self
        fake_sock.__exit__ = lambda self, *a: None
        with patch("socket.create_connection", return_value=fake_sock):
            self.assertEqual(probe_internet_status(timeout=0.1), "green")

    def test_returns_red_when_all_connections_fail(self) -> None:
        with patch("socket.create_connection", side_effect=OSError("network unreachable")):
            self.assertEqual(probe_internet_status(timeout=0.1), "red")

    def test_returns_red_on_timeout(self) -> None:
        with patch("socket.create_connection", side_effect=socket.timeout()):
            self.assertEqual(probe_internet_status(timeout=0.1), "red")

    def test_cached_result_skips_second_probe(self) -> None:
        with patch("socket.create_connection") as mock_conn:
            fake_sock = MagicMock()
            fake_sock.__enter__ = lambda self: self
            fake_sock.__exit__ = lambda self, *a: None
            mock_conn.return_value = fake_sock
            probe_internet_status(timeout=0.1)
            probe_internet_status(timeout=0.1)
            self.assertEqual(mock_conn.call_count, 1)


class ProbeProviderStatusTest(unittest.TestCase):
    def setUp(self) -> None:
        clear_cache()

    def test_none_config_returns_red(self) -> None:
        self.assertEqual(probe_provider_status(None), "red")

    def test_unreachable_host_returns_red(self) -> None:
        cfg = MagicMock()
        cfg.provider = "openai"
        cfg.model = "gpt-4o"
        cfg.base_url = "https://nonexistent.invalid:443"
        cfg.api_key = "x"
        with patch("socket.create_connection", side_effect=OSError("nxdomain")):
            self.assertEqual(probe_provider_status(cfg, timeout=0.1), "red")

    def test_reachable_host_and_valid_model_returns_green(self) -> None:
        cfg = MagicMock()
        cfg.provider = "openai"
        cfg.model = "gpt-4o"
        cfg.base_url = "https://api.openai.com/v1"
        cfg.api_key = "test-key"

        fake_sock = MagicMock()
        fake_sock.__enter__ = lambda self: self
        fake_sock.__exit__ = lambda self, *a: None
        fake_adapter = MagicMock()
        fake_adapter.validate_model = MagicMock(return_value=True)

        with patch("socket.create_connection", return_value=fake_sock), \
             patch("duckln.ai_client.get_provider_adapter_for_base_url", return_value=fake_adapter):
            self.assertEqual(probe_provider_status(cfg, timeout=0.1), "green")


class ProbeTargetStatusTest(unittest.TestCase):
    def setUp(self) -> None:
        clear_cache()

    def test_local_always_green(self) -> None:
        self.assertEqual(probe_target_status("local"), "green")
        self.assertEqual(probe_target_status(""), "green")  # defaults to local

    def test_unknown_target_returns_red(self) -> None:
        self.assertEqual(probe_target_status("totally_unknown"), "red")

    def test_aws_without_cli_returns_red(self) -> None:
        with patch("shutil.which", return_value=None):
            self.assertEqual(probe_target_status("aws"), "red")

    def test_gcp_without_cli_returns_red(self) -> None:
        with patch("shutil.which", return_value=None):
            self.assertEqual(probe_target_status("gcp"), "red")


class StartupOfflineNoticeTest(unittest.TestCase):
    def setUp(self) -> None:
        clear_cache()

    def test_none_when_no_config(self) -> None:
        self.assertIsNone(startup_offline_notice(config=None))

    @staticmethod
    def _cache_key(cfg) -> str:
        # Plan 121: mirror probe_provider_status' cache key (now includes api-key
        # presence + base_url so a credential change busts a stale result).
        # Plan 182 F4: the key gained a trailing config_dir marker ('' when config_dir is None,
        # which is how startup_offline_notice calls the probe).
        return (
            f"provider:{cfg.provider}:{cfg.model}"
            f":{bool(getattr(cfg, 'api_key', None))}:{getattr(cfg, 'base_url', None) or ''}:"
        )

    def test_red_status_returns_message_with_provider_name(self) -> None:
        cfg = MagicMock()
        cfg.provider = MagicMock(value="openai")
        cfg.base_url = "https://api.openai.com/v1"
        # Cache a red result so we don't actually probe.
        connection_status._cache_put(self._cache_key(cfg), "red")
        notice = startup_offline_notice(config=cfg)
        self.assertIsNotNone(notice)
        # Plan 186 F2a: plain, guiding wording that names the provider (no "offline" jargon).
        self.assertIn("openai", notice.lower())
        self.assertIn("can't reach", notice.lower())

    def test_green_status_returns_none(self) -> None:
        cfg = MagicMock()
        cfg.provider = MagicMock(value="openai")
        cfg.base_url = "https://api.openai.com/v1"
        connection_status._cache_put(self._cache_key(cfg), "green")
        notice = startup_offline_notice(config=cfg)
        self.assertIsNone(notice)


if __name__ == "__main__":
    unittest.main()
