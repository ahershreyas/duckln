"""Tests: bundled Nerd Font auto-install + VS Code detection (Plan 63 Fix 4b)."""

from __future__ import annotations

import os
import platform
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from duckln.font_setup import (
    bundled_font_path,
    bundled_license_path,
    ensure_nerd_font_installed,
    is_vscode_terminal,
    target_font_path,
    vscode_settings_guidance,
    FONT_FILENAME,
)


class BundledAssetsTest(unittest.TestCase):
    def test_bundled_font_file_exists_in_package(self) -> None:
        path = bundled_font_path()
        self.assertTrue(path.exists(), f"Expected {path} to exist in the package")
        self.assertGreater(path.stat().st_size, 100_000, "Font file looks suspiciously small")

    def test_bundled_license_file_exists(self) -> None:
        self.assertTrue(bundled_license_path().exists())


class TargetFontPathTest(unittest.TestCase):
    def test_macos_path(self) -> None:
        with patch("platform.system", return_value="Darwin"):
            path = target_font_path()
            self.assertIsNotNone(path)
            self.assertIn("Library/Fonts", str(path))
            self.assertTrue(str(path).endswith(FONT_FILENAME))

    def test_linux_path(self) -> None:
        with patch("platform.system", return_value="Linux"):
            path = target_font_path()
            self.assertIsNotNone(path)
            self.assertIn(".local/share/fonts", str(path))

    def test_windows_path(self) -> None:
        with patch("platform.system", return_value="Windows"), \
             patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\test\AppData\Local"}, clear=False):
            path = target_font_path()
            self.assertIsNotNone(path)
            self.assertIn("Microsoft", str(path))
            self.assertIn("Fonts", str(path))

    def test_unsupported_os_returns_none(self) -> None:
        with patch("platform.system", return_value="Plan9"):
            self.assertIsNone(target_font_path())


class EnsureInstalledTest(unittest.TestCase):
    def test_idempotent_when_font_already_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            font_target = tmp_path / FONT_FILENAME
            # Pre-populate the target with the same content as the bundled source.
            font_target.write_bytes(bundled_font_path().read_bytes())
            with patch("duckln.font_setup.target_font_path", return_value=font_target):
                installed, msg = ensure_nerd_font_installed()
            self.assertFalse(installed)
            self.assertIn("already", msg.lower())

    def test_installs_when_target_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            font_target = Path(tmp) / "Fonts" / FONT_FILENAME
            with patch("duckln.font_setup.target_font_path", return_value=font_target):
                installed, msg = ensure_nerd_font_installed()
            self.assertTrue(installed)
            self.assertTrue(font_target.exists())
            self.assertIn("Installed", msg)

    def test_unsupported_os_no_install(self) -> None:
        with patch("duckln.font_setup.target_font_path", return_value=None):
            installed, msg = ensure_nerd_font_installed()
        self.assertFalse(installed)
        self.assertIn("Unsupported", msg)


class VsCodeDetectionTest(unittest.TestCase):
    def test_detects_vscode_via_term_program(self) -> None:
        with patch.dict(os.environ, {"TERM_PROGRAM": "vscode"}, clear=False):
            self.assertTrue(is_vscode_terminal())

    def test_detects_vscode_via_vscode_pid(self) -> None:
        with patch.dict(os.environ, {"VSCODE_PID": "1234"}, clear=False):
            self.assertTrue(is_vscode_terminal())

    def test_returns_false_without_vscode_hints(self) -> None:
        # Strip both env vars regardless of test runner inheritance.
        env = {k: v for k, v in os.environ.items() if k not in {"TERM_PROGRAM", "VSCODE_PID"}}
        with patch.dict(os.environ, env, clear=True):
            self.assertFalse(is_vscode_terminal())

    def test_guidance_mentions_settings_json(self) -> None:
        msg = vscode_settings_guidance()
        self.assertIn("settings.json", msg)
        self.assertIn("Nerd Font", msg)


if __name__ == "__main__":
    unittest.main()
