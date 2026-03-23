"""Tests for Duckln config path helpers and raw config persistence."""

from __future__ import annotations

import tempfile
import unittest

from duckln.config import (
    APP_DIR_NAME,
    CONFIG_FILE_NAME,
    ENV_CONFIG_DIR,
    ENV_CONFIG_FILE,
    ENV_HOME,
    ENV_XDG_CONFIG_HOME,
    get_env_defaults,
    load_raw_config,
    resolve_config_paths,
    save_raw_config,
)


class ConfigHelpersTest(unittest.TestCase):
    def test_explicit_file_override_wins(self) -> None:
        paths = resolve_config_paths({ENV_CONFIG_FILE: "/tmp/custom-duckln.json"})

        self.assertEqual("/tmp", str(paths.config_dir))
        self.assertEqual("/tmp/custom-duckln.json", str(paths.config_file))

    def test_explicit_dir_override_beats_xdg_and_home(self) -> None:
        paths = resolve_config_paths(
            {
                ENV_CONFIG_DIR: "/tmp/duckln-config",
                ENV_XDG_CONFIG_HOME: "/tmp/xdg",
                ENV_HOME: "/tmp/home",
            }
        )

        self.assertEqual("/tmp/duckln-config", str(paths.config_dir))
        self.assertEqual(f"/tmp/duckln-config/{CONFIG_FILE_NAME}", str(paths.config_file))

    def test_xdg_default_is_used_before_home(self) -> None:
        paths = resolve_config_paths(
            {
                ENV_XDG_CONFIG_HOME: "/tmp/xdg",
                ENV_HOME: "/tmp/home",
            }
        )

        self.assertEqual(f"/tmp/xdg/{APP_DIR_NAME}", str(paths.config_dir))
        self.assertEqual(f"/tmp/xdg/{APP_DIR_NAME}/{CONFIG_FILE_NAME}", str(paths.config_file))

    def test_home_fallback_uses_hidden_duckln_dir(self) -> None:
        paths = resolve_config_paths({ENV_HOME: "/tmp/home"})

        self.assertEqual("/tmp/home/.duckln", str(paths.config_dir))
        self.assertEqual("/tmp/home/.duckln/config.json", str(paths.config_file))

    def test_missing_home_and_overrides_raises(self) -> None:
        with self.assertRaises(ValueError):
            resolve_config_paths({})

    def test_env_defaults_report_known_keys(self) -> None:
        defaults = get_env_defaults({ENV_HOME: "/tmp/home"})

        self.assertEqual("", defaults[ENV_CONFIG_DIR])
        self.assertEqual("", defaults[ENV_CONFIG_FILE])
        self.assertEqual("", defaults[ENV_XDG_CONFIG_HOME])
        self.assertEqual("/tmp/home", defaults[ENV_HOME])

    def test_save_and_load_raw_config_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({ENV_CONFIG_DIR: temp_dir})
            payload = {"provider": "openrouter", "mode": "hitl"}

            save_raw_config(payload, paths)

            self.assertEqual(payload, load_raw_config(paths))


if __name__ == "__main__":
    unittest.main()
