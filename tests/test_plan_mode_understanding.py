"""Plan 67: Stage-1 repo understanding tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from duckln.plan_mode import (
    RepoUnderstanding,
    _classify_family_from_files,
    gather_repo_understanding,
)


class RepoUnderstandingTest(unittest.TestCase):
    def _make_repo(self, contents: dict[str, str]) -> Path:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        for name, body in contents.items():
            (root / name).write_text(body, encoding="utf-8")
        return root

    def test_understanding_populated_from_node_repo(self) -> None:
        repo = self._make_repo({
            "package.json": '{"name":"x","engines":{"node":">=20"}}',
            "README.md": "# X\nA Node CLI.\n\nUse `npm install` then `npm run dev`.",
        })
        u = gather_repo_understanding(
            objective="Set up x",
            project_dir=repo,
            repo_slug="owner/x",
            config_dir=None,
            os_name="Darwin",
            arch="arm64",
            execution_target="local",
            control_mode="hotl",
            detected_runtimes=("node",),
        )
        self.assertEqual(u.repo_family, "node_typescript")
        self.assertIn("package.json", u.detected_files)
        self.assertIn("README.md", u.detected_files)
        self.assertIn("npm install", u.readme_excerpt)
        self.assertFalse(u.needs_clarification)
        self.assertEqual(u.detected_runtimes, ("node",))

    def test_missing_readme_unknown_family_triggers_clarification(self) -> None:
        repo = self._make_repo({"random.txt": "hello"})
        u = gather_repo_understanding(
            objective="run this",
            project_dir=repo,
            repo_slug="owner/y",
            config_dir=None,
        )
        self.assertEqual(u.repo_family, "unknown")
        self.assertTrue(u.needs_clarification)
        self.assertIsNotNone(u.clarification_seed)

    def test_classify_family_from_pyproject(self) -> None:
        self.assertEqual(
            _classify_family_from_files(("pyproject.toml",), ""),
            "python",
        )

    def test_classify_family_from_cargo(self) -> None:
        self.assertEqual(
            _classify_family_from_files(("Cargo.toml",), ""),
            "rust",
        )

    def test_failure_memory_loaded_from_config_dir(self) -> None:
        repo = self._make_repo({"package.json": "{}"})
        config_td = tempfile.TemporaryDirectory()
        self.addCleanup(config_td.cleanup)
        config_dir = Path(config_td.name)
        failures = config_dir / "memory" / "failures"
        failures.mkdir(parents=True)
        (failures / "owner_x.md").write_text(
            "## Failure: 2026-05-19\nnpm install timed out\nCause: registry rate-limit\n",
            encoding="utf-8",
        )
        u = gather_repo_understanding(
            objective="Set up x",
            project_dir=repo,
            repo_slug="owner/x",
            config_dir=config_dir,
        )
        self.assertTrue(any("npm install timed out" in s or "registry" in s for s in u.recent_failures))


if __name__ == "__main__":
    unittest.main()
