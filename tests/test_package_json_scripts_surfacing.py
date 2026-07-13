"""Tests: _surface_package_json_run_command picks scripts without web search."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from duckln.main import _surface_package_json_run_command


class SurfacePackageJsonScriptsTest(unittest.TestCase):
    def test_dev_script_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "package.json").write_text(
                json.dumps({"scripts": {"dev": "vite", "start": "node server.js"}}),
                encoding="utf-8",
            )
            self.assertEqual(_surface_package_json_run_command(project_dir), "npm run dev")

    def test_start_when_no_dev(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "package.json").write_text(
                json.dumps({"scripts": {"start": "node server.js", "test": "jest"}}),
                encoding="utf-8",
            )
            self.assertEqual(_surface_package_json_run_command(project_dir), "npm run start")

    def test_serve_when_no_dev_or_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "package.json").write_text(
                json.dumps({"scripts": {"serve": "http-server"}}),
                encoding="utf-8",
            )
            self.assertEqual(_surface_package_json_run_command(project_dir), "npm run serve")

    def test_preview_when_no_higher_priority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "package.json").write_text(
                json.dumps({"scripts": {"preview": "vite preview"}}),
                encoding="utf-8",
            )
            self.assertEqual(_surface_package_json_run_command(project_dir), "npm run preview")

    def test_no_package_json_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(_surface_package_json_run_command(Path(tmp)))

    def test_no_scripts_field_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "package.json").write_text(
                json.dumps({"name": "x", "version": "1.0.0"}),
                encoding="utf-8",
            )
            self.assertIsNone(_surface_package_json_run_command(project_dir))

    def test_unknown_scripts_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "package.json").write_text(
                json.dumps({"scripts": {"build": "webpack", "test": "jest"}}),
                encoding="utf-8",
            )
            # None of dev/start/serve/preview are present.
            self.assertIsNone(_surface_package_json_run_command(project_dir))

    def test_malformed_json_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "package.json").write_text("{not valid json", encoding="utf-8")
            self.assertIsNone(_surface_package_json_run_command(project_dir))


if __name__ == "__main__":
    unittest.main()
