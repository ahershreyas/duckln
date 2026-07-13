"""Regression tests for the Debug/Recovery classifier and recovery-input plumbing.

The bug: when a command's stderr was empty, the prior code fell back to feeding
Duckln's own narrative summary ("Specialist route: rust. Toolchain: cargo. Fatal
line: …") into the failure classifier. The classifier then substring-matched
"rust"/"cargo" and rerouted Node repos to the Rust specialist in a loop.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent.probe import GpuProbeState, SystemProbe
from duckln.repo_bringup import (
    DebugRecoverySpecialist,
    FailureType,
    RepoBringUpStep,
    RepoFamily,
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


def _node_repo() -> RepoCatalogRecord:
    return RepoCatalogRecord(
        name="JustHireMe",
        repo_url="https://example.com/JustHireMe",
        stars=10,
        description="Node web app",
        category="web",
        framework="TypeScript",
        last_updated="2026-05-01",
    )


class RecoveryClassifierNarrativeIsolationTest(unittest.TestCase):
    """The classifier must ignore Duckln narrative tokens like 'rust' and 'cargo'."""

    def _node_inspection(self, project_dir: Path):
        (project_dir / "README.md").write_text("# JustHireMe\n", encoding="utf-8")
        (project_dir / "package.json").write_text("{\"name\":\"jh\"}", encoding="utf-8")
        return inspect_repo_for_bringup(_node_repo(), project_dir, system_probe=_probe())

    def test_narrative_with_rust_cargo_tokens_does_not_match_compiler_failure(self) -> None:
        narrative = (
            "Duckln classified this blocker as rust dependency failure. "
            "Specialist route: rust. Toolchain: cargo. "
            "Fatal line: json || test -e go.mod || t"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            specialist = DebugRecoverySpecialist()
            inspection = self._node_inspection(Path(temp_dir))
            failure_type = specialist._classify_failure(
                inspection=inspection,
                repo_family=RepoFamily.NODE_TYPESCRIPT,
                failed_command="npm ci",
                verification_failure="install failed",
                error_output=narrative,
                low_confidence=False,
            )
        # Without scrubbing, the substring "cargo: command not found" would match
        # MISSING_COMPILER_BUILD_TOOLS. After scrubbing, the narrative line is
        # discarded entirely and the classifier must fall back to a non-Rust class.
        self.assertNotEqual(FailureType.MISSING_COMPILER_BUILD_TOOLS, failure_type)

    def test_real_stderr_with_legitimate_cargo_signal_still_classifies_correctly(self) -> None:
        # Make sure scrubbing the narrative does not hide real cargo errors.
        real_stderr = "bash: cargo: command not found"
        with tempfile.TemporaryDirectory() as temp_dir:
            specialist = DebugRecoverySpecialist()
            inspection = self._node_inspection(Path(temp_dir))
            failure_type = specialist._classify_failure(
                inspection=inspection,
                repo_family=RepoFamily.RUST,
                failed_command="cargo build",
                verification_failure="cargo build failed",
                error_output=real_stderr,
                low_confidence=False,
            )
        self.assertEqual(FailureType.MISSING_COMPILER_BUILD_TOOLS, failure_type)


if __name__ == "__main__":
    unittest.main()
