"""Tests: _infer_start_command_from_files covers Python/Rust/Go/C++ (Plan 55, REQ-RUNTIME-NOLOOP-6)."""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from duckln.repo_bringup import _infer_start_command_from_files


class RunCommandInferenceTest(unittest.TestCase):
    def test_pyproject_pep621_scripts_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "pyproject.toml").write_text(
                "[project]\nname = \"demo\"\n\n[project.scripts]\nmyapp = \"demo:main\"\n",
                encoding="utf-8",
            )
            result = _infer_start_command_from_files(project_dir)
            self.assertEqual(result, "myapp")

    def test_pyproject_poetry_scripts_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "pyproject.toml").write_text(
                "[tool.poetry]\nname = \"demo\"\n\n[tool.poetry.scripts]\ndemo-cli = \"demo:cli\"\n",
                encoding="utf-8",
            )
            result = _infer_start_command_from_files(project_dir)
            self.assertEqual(result, "demo-cli")

    def test_go_single_cmd_subdir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "go.mod").write_text("module example.com/foo\n", encoding="utf-8")
            cmd_dir = project_dir / "cmd" / "server"
            cmd_dir.mkdir(parents=True)
            (cmd_dir / "main.go").write_text("package main\n", encoding="utf-8")
            result = _infer_start_command_from_files(project_dir)
            self.assertEqual(result, "go run ./cmd/server")

    def test_go_multiple_cmd_subdirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "go.mod").write_text("module example.com/foo\n", encoding="utf-8")
            (project_dir / "cmd" / "server").mkdir(parents=True)
            (project_dir / "cmd" / "worker").mkdir(parents=True)
            result = _infer_start_command_from_files(project_dir)
            self.assertEqual(result, "go run ./cmd/...")

    def test_go_no_cmd_dir_falls_back_to_dot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "go.mod").write_text("module example.com/foo\n", encoding="utf-8")
            result = _infer_start_command_from_files(project_dir)
            self.assertEqual(result, "go run .")

    def test_cargo_toml_returns_cargo_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "Cargo.toml").write_text(
                "[package]\nname = \"demo\"\nversion = \"0.1.0\"\nedition = \"2021\"\n",
                encoding="utf-8",
            )
            self.assertEqual(_infer_start_command_from_files(project_dir), "cargo run")

    def test_cmake_with_build_executable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "CMakeLists.txt").write_text("project(demo)\n", encoding="utf-8")
            build_dir = project_dir / "build"
            build_dir.mkdir()
            exe = build_dir / "demo"
            exe.write_text("#!/bin/sh\necho demo\n", encoding="utf-8")
            os.chmod(exe, os.stat(exe).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            result = _infer_start_command_from_files(project_dir)
            self.assertEqual(result, "./build/demo")

    def test_no_marker_files_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "README.md").write_text("hi", encoding="utf-8")
            self.assertIsNone(_infer_start_command_from_files(project_dir))

    def test_pyproject_with_no_scripts_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "pyproject.toml").write_text(
                "[project]\nname = \"demo\"\n",
                encoding="utf-8",
            )
            self.assertIsNone(_infer_start_command_from_files(project_dir))


if __name__ == "__main__":
    unittest.main()
