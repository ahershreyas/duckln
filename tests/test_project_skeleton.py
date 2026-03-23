"""Project skeleton checks for the initial Duckln setup task."""

from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "src" / "duckln"


class ProjectSkeletonTest(unittest.TestCase):
    def test_expected_package_modules_exist(self) -> None:
        expected_files = {
            "__init__.py",
            "ai_client.py",
            "config.py",
            "diagnostics.py",
            "logging_utils.py",
            "main.py",
            "modes.py",
            "prompts.py",
            "safety.py",
            "shell.py",
            "ui.py",
        }

        self.assertTrue(PACKAGE_ROOT.is_dir())
        self.assertTrue(expected_files.issubset({path.name for path in PACKAGE_ROOT.iterdir()}))


if __name__ == "__main__":
    unittest.main()
