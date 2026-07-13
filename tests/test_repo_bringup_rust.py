"""Tests for the new RepoFamily.RUST routing and RustRepoSetupSpecialist."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent.probe import GpuProbeState, SystemProbe
from duckln.repo_bringup import (
    RepoBringUpSupervisor,
    RepoFamily,
    RustRepoSetupSpecialist,
    classify_repo_family,
    inspect_repo_for_bringup,
)
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


def _repo() -> RepoCatalogRecord:
    return RepoCatalogRecord(
        name="rust-hello",
        repo_url="https://example.com/rust-hello",
        stars=42,
        description="hello world in Rust",
        category="tool",
        framework="Rust",
        last_updated="2026-05-01",
    )


class ClassifyRepoFamilyRustTest(unittest.TestCase):
    def test_cargo_toml_routes_to_rust(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "Cargo.toml").write_text("[package]\nname=\"hello\"\n", encoding="utf-8")
            (project_dir / "README.md").write_text("# hello\n", encoding="utf-8")
            inspection = inspect_repo_for_bringup(_repo(), project_dir, system_probe=_probe())
        self.assertEqual(RepoFamily.RUST, classify_repo_family(inspection))

    def test_rust_toolchain_file_routes_to_rust(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "Cargo.toml").write_text("[package]\nname=\"hello\"\n", encoding="utf-8")
            (project_dir / "rust-toolchain.toml").write_text("[toolchain]\nchannel=\"stable\"\n", encoding="utf-8")
            (project_dir / "README.md").write_text("# hello\n", encoding="utf-8")
            inspection = inspect_repo_for_bringup(_repo(), project_dir, system_probe=_probe())
        self.assertEqual(RepoFamily.RUST, classify_repo_family(inspection))


class RustSpecialistStepsTest(unittest.TestCase):
    def test_plans_verify_fetch_build_for_cargo_repo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "Cargo.toml").write_text("[package]\nname=\"hello\"\n", encoding="utf-8")
            (project_dir / "README.md").write_text("# hello\n", encoding="utf-8")
            inspection = inspect_repo_for_bringup(_repo(), project_dir, system_probe=_probe())

        steps = RustRepoSetupSpecialist().infer_steps(inspection, RepoFamily.RUST)
        commands = [step.command for step in steps]

        self.assertIn("cargo --version", commands)
        self.assertIn("cargo fetch", commands)
        self.assertIn("cargo build --release", commands)

    def test_supervisor_selects_rust_specialist_for_rust_family(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "Cargo.toml").write_text("[package]\nname=\"hello\"\n", encoding="utf-8")
            (project_dir / "README.md").write_text("# hello\n", encoding="utf-8")
            inspection = inspect_repo_for_bringup(_repo(), project_dir, system_probe=_probe())

        specialist = RepoBringUpSupervisor()._select_specialist(inspection, RepoFamily.RUST)
        self.assertEqual("rust", specialist.specialist_name)


if __name__ == "__main__":
    unittest.main()
