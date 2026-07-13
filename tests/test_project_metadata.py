"""Tests for Python project metadata and dependency declarations."""

from __future__ import annotations

from pathlib import Path
import unittest

try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        tomllib = None


REPO_ROOT = Path(__file__).resolve().parents[1]


class ProjectMetadataTest(unittest.TestCase):
    def test_pyproject_declares_expected_runtime_metadata(self) -> None:
        pyproject_text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")

        if tomllib is None:
            self.assertIn('name = "duckln"', pyproject_text)
            self.assertIn('requires-python = ">=3.11"', pyproject_text)
            self.assertIn('"httpx>=0.27,<1.0"', pyproject_text)
            self.assertIn('"InquirerPy>=0.3.4,<1.0"', pyproject_text)
            self.assertIn('"textual>=0.75,<1.0"', pyproject_text)
            self.assertIn('"textual-terminal>=0.3,<1.0"', pyproject_text)
            self.assertIn('"apscheduler>=3.10.4"', pyproject_text)
            self.assertIn('duckln = "duckln.main:main"', pyproject_text)
            return

        payload = tomllib.loads(pyproject_text)

        self.assertEqual("duckln", payload["project"]["name"])
        self.assertEqual(">=3.11", payload["project"]["requires-python"])
        self.assertEqual(
            [
                "httpx>=0.27,<1.0",
                "InquirerPy>=0.3.4,<1.0",
                "prompt_toolkit>=3.0.43,<4.0",
                "rich>=13.7,<14.0",
                "textual>=0.75,<1.0",
                "textual-terminal>=0.3,<1.0",
                "ptyprocess>=0.7,<1.0",
                "pywinpty>=2.0,<3.0",
                "pyte>=0.8,<1.0",
                "pywebview>=6.2,<7.0",
                "apscheduler>=3.10.4",
                "SQLAlchemy>=1.4,<3.0",
                "pync>=2.0.3",
                "win10toast>=0.9",
            ],
            payload["project"]["dependencies"],
        )
        self.assertEqual("duckln.main:main", payload["project"]["scripts"]["duckln"])


if __name__ == "__main__":
    unittest.main()
