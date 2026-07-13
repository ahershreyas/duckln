"""Tests: build_plan now injects README prereq preflight steps (Plan 57 Phase 2)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from duckln.repo_bringup import (
    NodeTypescriptRepoSetupSpecialist,
    RepoBringUpInspection,
    RepoFamily,
)
from state.repo_catalog import RepoCatalogRecord
from agent.probe import probe_system


JUSTHIREME_README = """
# JustHireMe

A Tauri desktop app with React + Python backend.

## Requirements

| Tool | Version |
| --- | --- |
| Node.js | 20+ |
| Python | 3.13+ |
| Rust | stable |
| uv | latest stable |

## Quick Start

```bash
npm install
npm run dev
```
"""


def _make_inspection(project_dir: Path, readme_text: str) -> RepoBringUpInspection:
    repo = RepoCatalogRecord(
        name="TestRepo",
        repo_url="https://example.com/TestRepo",
        stars=10,
        description="Test",
        category="web",
        framework="TypeScript",
        last_updated="2026-05-01",
    )
    (project_dir / "README.md").write_text(readme_text, encoding="utf-8")
    (project_dir / "package.json").write_text('{"name":"t","scripts":{"dev":"vite"}}', encoding="utf-8")
    probe = probe_system()
    return RepoBringUpInspection(
        repo=repo,
        project_dir=project_dir,
        detected_files=("package.json", "README.md"),
        readme_excerpt=readme_text[:500],
        system_probe=probe,
        execution_target="local",
    )


class BuildPlanInjectsPrereqsTest(unittest.TestCase):
    def test_node_specialist_prepends_prereq_preflight_steps(self) -> None:
        """For a JustHireMe-style README, build_plan must include prereq steps before install/run."""
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            inspection = _make_inspection(project_dir, JUSTHIREME_README)
            specialist = NodeTypescriptRepoSetupSpecialist()
            plan = specialist.build_plan(inspection, RepoFamily.NODE_TYPESCRIPT)

            sources = [step.source for step in plan.steps]
            self.assertIn(
                "readme-prereq",
                sources,
                f"Expected at least one 'readme-prereq' step in plan; got sources: {sources}",
            )

    def test_prereq_steps_come_before_install_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            inspection = _make_inspection(project_dir, JUSTHIREME_README)
            specialist = NodeTypescriptRepoSetupSpecialist()
            plan = specialist.build_plan(inspection, RepoFamily.NODE_TYPESCRIPT)

            sources = [step.source for step in plan.steps]
            try:
                first_prereq = sources.index("readme-prereq")
            except ValueError:
                self.fail(f"No 'readme-prereq' step found; sources: {sources}")
            # No 'readme' (install/run) step may come BEFORE the first prereq step.
            for i, src in enumerate(sources[:first_prereq]):
                self.assertNotIn(
                    src,
                    {"readme", "readme-llm", "inferred"},
                    f"Step at index {i} ({src}) appears before prereq preflight",
                )

    def test_no_readme_no_prereq_steps(self) -> None:
        """When README has no prereqs section, build_plan returns plan without prereq steps."""
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            # Minimal README with no Requirements section.
            inspection = _make_inspection(project_dir, "# T\n\nA test repo.\n")
            specialist = NodeTypescriptRepoSetupSpecialist()
            plan = specialist.build_plan(inspection, RepoFamily.NODE_TYPESCRIPT)

            sources = [step.source for step in plan.steps]
            self.assertNotIn("readme-prereq", sources)

    def test_each_prereq_step_has_probe_as_verification_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            inspection = _make_inspection(project_dir, JUSTHIREME_README)
            specialist = NodeTypescriptRepoSetupSpecialist()
            plan = specialist.build_plan(inspection, RepoFamily.NODE_TYPESCRIPT)

            prereq_steps = [s for s in plan.steps if s.source == "readme-prereq"]
            self.assertGreater(len(prereq_steps), 0)
            for step in prereq_steps:
                # Each prereq step must have a verification command (the probe).
                self.assertIsNotNone(step.verification_command)
                self.assertTrue(step.verification_command.strip())


if __name__ == "__main__":
    unittest.main()
