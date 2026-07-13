"""Tests: prereq preflight picks the package manager from execution target, not local host (Plan 58 Bug A)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from duckln.repo_bringup import (
    NodeTypescriptRepoSetupSpecialist,
    RepoBringUpInspection,
    RepoFamily,
)
from state.repo_catalog import RepoCatalogRecord
from agent.probe import GpuProbeState, SystemProbe


JUSTHIREME_README = """
# JustHireMe

## Requirements

- Node.js 20+
- Python 3.13+
- Rust stable
- uv latest
"""


def _probe(os_name: str) -> SystemProbe:
    return SystemProbe(
        operating_system=os_name,
        architecture="arm64",
        cpu_logical_cores=8,
        ram_bytes=16 * (1024 ** 3),
        disk_free_bytes=200 * (1024 ** 3),
        python_version="3.11.6",
        gpu=GpuProbeState(
            backend="cpu",
            summary="No GPU",
            cuda_capable=False,
            cuda_available=False,
            mps_capable=False,
            mps_available=False,
        ),
    )


def _make_inspection(project_dir: Path, *, os_name: str, execution_target: str) -> RepoBringUpInspection:
    repo = RepoCatalogRecord(
        name="TestRepo",
        repo_url="https://example.com/TestRepo",
        stars=10,
        description="Test",
        category="web",
        framework="TypeScript",
        last_updated="2026-05-01",
    )
    (project_dir / "README.md").write_text(JUSTHIREME_README, encoding="utf-8")
    (project_dir / "package.json").write_text('{"name":"t","scripts":{"dev":"vite"}}', encoding="utf-8")
    return RepoBringUpInspection(
        repo=repo,
        project_dir=project_dir,
        detected_files=("package.json", "README.md"),
        readme_excerpt=JUSTHIREME_README[:500],
        system_probe=_probe(os_name),
        execution_target=execution_target,
    )


class TargetAwareOSTest(unittest.TestCase):
    def test_vm_target_with_darwin_host_uses_apt(self) -> None:
        """Mac host + Ubuntu VM target → install commands must use apt, never brew."""
        with tempfile.TemporaryDirectory() as tmp:
            inspection = _make_inspection(Path(tmp), os_name="Darwin", execution_target="vm")
            specialist = NodeTypescriptRepoSetupSpecialist()
            with patch("state.access.lookup_recent_failure", return_value=None):
                plan = specialist.build_plan(inspection, RepoFamily.NODE_TYPESCRIPT)

            prereq_commands = [s.command for s in plan.steps if s.source == "readme-prereq"]
            self.assertTrue(prereq_commands, "Expected at least one prereq step")
            for cmd in prereq_commands:
                self.assertFalse(
                    cmd.startswith("brew"),
                    f"VM target must not use brew; got: {cmd}",
                )
            apt_commands = [c for c in prereq_commands if "apt install" in c]
            self.assertTrue(apt_commands, f"Expected apt install commands; got: {prereq_commands}")

    def test_local_target_with_darwin_host_uses_brew(self) -> None:
        """Mac host + local target → brew is the correct package manager."""
        with tempfile.TemporaryDirectory() as tmp:
            inspection = _make_inspection(Path(tmp), os_name="Darwin", execution_target="local")
            specialist = NodeTypescriptRepoSetupSpecialist()
            with patch("state.access.lookup_recent_failure", return_value=None):
                plan = specialist.build_plan(inspection, RepoFamily.NODE_TYPESCRIPT)

            prereq_commands = [s.command for s in plan.steps if s.source == "readme-prereq"]
            self.assertTrue(prereq_commands)
            brew_commands = [c for c in prereq_commands if c.startswith("brew install")]
            self.assertTrue(
                brew_commands,
                f"Local Darwin should use brew; got: {prereq_commands}",
            )

    def test_local_target_with_linux_host_uses_apt(self) -> None:
        """Linux host + local target → apt."""
        with tempfile.TemporaryDirectory() as tmp:
            inspection = _make_inspection(Path(tmp), os_name="Linux", execution_target="local")
            specialist = NodeTypescriptRepoSetupSpecialist()
            with patch("state.access.lookup_recent_failure", return_value=None):
                plan = specialist.build_plan(inspection, RepoFamily.NODE_TYPESCRIPT)

            prereq_commands = [s.command for s in plan.steps if s.source == "readme-prereq"]
            self.assertTrue(prereq_commands)
            for cmd in prereq_commands:
                self.assertFalse(cmd.startswith("brew"))

    def test_aws_target_uses_apt(self) -> None:
        """AWS cloud target → apt (treats AWS VMs as Ubuntu-like)."""
        with tempfile.TemporaryDirectory() as tmp:
            inspection = _make_inspection(Path(tmp), os_name="Darwin", execution_target="aws")
            specialist = NodeTypescriptRepoSetupSpecialist()
            with patch("state.access.lookup_recent_failure", return_value=None):
                plan = specialist.build_plan(inspection, RepoFamily.NODE_TYPESCRIPT)

            prereq_commands = [s.command for s in plan.steps if s.source == "readme-prereq"]
            for cmd in prereq_commands:
                self.assertFalse(cmd.startswith("brew"), f"AWS target must not use brew; got: {cmd}")


if __name__ == "__main__":
    unittest.main()
