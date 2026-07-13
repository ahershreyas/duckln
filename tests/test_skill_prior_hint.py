"""Tests for prior skill hint loading in inspect_repo_for_bringup."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent.probe import GpuProbeState, SystemProbe
from duckln.repo_bringup import inspect_repo_for_bringup
from state.repo_catalog import RepoCatalogRecord


def _probe() -> SystemProbe:
    return SystemProbe(
        operating_system="Linux",
        architecture="x86_64",
        cpu_logical_cores=8,
        ram_bytes=16 * 1024**3,
        disk_free_bytes=100 * 1024**3,
        python_version="3.11.8",
        gpu=GpuProbeState(
            backend="cpu",
            summary="No CUDA-capable GPU detected.",
            cuda_capable=False,
            cuda_available=False,
            mps_capable=False,
            mps_available=False,
        ),
    )


def _node_repo() -> RepoCatalogRecord:
    return RepoCatalogRecord(
        name="JustHireMe",
        repo_url="https://example.com/JustHireMe",
        stars=10,
        description="Node app",
        category="web",
        framework="TypeScript",
        last_updated="2026-05-01",
    )


def _rust_repo() -> RepoCatalogRecord:
    return RepoCatalogRecord(
        name="myapp",
        repo_url="https://example.com/myapp",
        stars=5,
        description="Rust CLI",
        category="cli",
        framework="Rust",
        last_updated="2026-05-01",
    )


class PriorSkillHintLoadingTest(unittest.TestCase):
    def test_matching_skill_loaded_as_prior_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            project_dir = config_dir / "project"
            project_dir.mkdir()
            (project_dir / "package.json").write_text('{"name":"jh"}', encoding="utf-8")

            # Write a skill whose slug contains the "node-typescript" family token
            skills_dir = config_dir / "memory" / "skills"
            skills_dir.mkdir(parents=True)
            skill_content = "# Repair: ENOENT\n\nRun npm cache clean first."
            (skills_dir / "repair-node-typescript-enoent.md").write_text(
                skill_content, encoding="utf-8"
            )

            inspection = inspect_repo_for_bringup(
                _node_repo(),
                project_dir,
                system_probe=_probe(),
                config_dir=config_dir,
            )
        self.assertIsNotNone(inspection.prior_skill_hint)
        self.assertIn("npm cache clean", inspection.prior_skill_hint or "")

    def test_no_matching_skill_yields_none_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            project_dir = config_dir / "project"
            project_dir.mkdir()
            (project_dir / "package.json").write_text('{"name":"jh"}', encoding="utf-8")

            # Skill exists for python, not node
            skills_dir = config_dir / "memory" / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "repair-python-venv.md").write_text(
                "# Python venv repair", encoding="utf-8"
            )

            inspection = inspect_repo_for_bringup(
                _node_repo(),
                project_dir,
                system_probe=_probe(),
                config_dir=config_dir,
            )
        self.assertIsNone(inspection.prior_skill_hint)

    def test_no_skills_dir_yields_none_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            project_dir = config_dir / "project"
            project_dir.mkdir()
            (project_dir / "Cargo.toml").write_text(
                '[package]\nname = "myapp"\nversion = "0.1.0"', encoding="utf-8"
            )

            inspection = inspect_repo_for_bringup(
                _rust_repo(),
                project_dir,
                system_probe=_probe(),
                config_dir=config_dir,
            )
        self.assertIsNone(inspection.prior_skill_hint)

    def test_no_config_dir_yields_none_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "package.json").write_text('{"name":"jh"}', encoding="utf-8")

            inspection = inspect_repo_for_bringup(
                _node_repo(),
                project_dir,
                system_probe=_probe(),
                config_dir=None,
            )
        self.assertIsNone(inspection.prior_skill_hint)

    def test_rust_skill_loaded_for_rust_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            project_dir = config_dir / "project"
            project_dir.mkdir()
            (project_dir / "Cargo.toml").write_text(
                '[package]\nname = "myapp"\nversion = "0.1.0"', encoding="utf-8"
            )

            skills_dir = config_dir / "memory" / "skills"
            skills_dir.mkdir(parents=True)
            rust_skill = "# Repair: rustup missing\n\nRun rustup update stable."
            (skills_dir / "repair-rust-toolchain.md").write_text(
                rust_skill, encoding="utf-8"
            )

            inspection = inspect_repo_for_bringup(
                _rust_repo(),
                project_dir,
                system_probe=_probe(),
                config_dir=config_dir,
            )
        self.assertIsNotNone(inspection.prior_skill_hint)
        self.assertIn("rustup update", inspection.prior_skill_hint or "")


if __name__ == "__main__":
    unittest.main()
