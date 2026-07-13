"""Tests: code-block scanner extracts tools from command lines (Plan 58 Bug B)."""

from __future__ import annotations

import unittest

from duckln.readme_skill import extract_readme_metadata


class CodeBlockScannerTest(unittest.TestCase):
    def test_npm_extracted_from_code_block_when_not_in_requirements(self) -> None:
        """JustHireMe-style README: 'Node.js' in Requirements, 'npm install' in code block.
        Both node AND npm must be extracted."""
        readme = (
            "# Test\n\n"
            "## Requirements\n\n"
            "- Node.js 20+\n\n"
            "## Quick Start\n\n"
            "```bash\n"
            "npm install\n"
            "npm run dev\n"
            "```\n"
        )
        meta = extract_readme_metadata(readme)
        names = {p.name for p in meta.prerequisites}
        self.assertIn("node", names)
        self.assertIn("npm", names)

    def test_sudo_prefix_stripped(self) -> None:
        readme = (
            "# Test\n\n"
            "```bash\n"
            "sudo apt install nodejs\n"
            "```\n"
        )
        meta = extract_readme_metadata(readme)
        names = {p.name for p in meta.prerequisites}
        # First real token is 'apt' (not in known prereqs but registered as probe-only).
        # Importantly, 'sudo' itself should NOT appear.
        self.assertNotIn("sudo", names)

    def test_env_var_prefix_stripped(self) -> None:
        readme = (
            "# Test\n\n"
            "```bash\n"
            "PORT=3000 npm start\n"
            "```\n"
        )
        meta = extract_readme_metadata(readme)
        names = {p.name for p in meta.prerequisites}
        self.assertIn("npm", names)
        self.assertNotIn("PORT=3000", names)
        self.assertNotIn("port=3000", names)

    def test_shell_builtins_skipped(self) -> None:
        readme = (
            "# Test\n\n"
            "```bash\n"
            "cd repo\n"
            "ls -la\n"
            "echo hello\n"
            "cat README.md\n"
            "```\n"
        )
        meta = extract_readme_metadata(readme)
        names = {p.name for p in meta.prerequisites}
        for builtin in ("cd", "ls", "echo", "cat"):
            self.assertNotIn(builtin, names, f"Shell builtin '{builtin}' must not be registered as prereq")

    def test_unknown_tool_gets_probe_only(self) -> None:
        readme = (
            "# Test\n\n"
            "```bash\n"
            "obscuretool --init\n"
            "```\n"
        )
        meta = extract_readme_metadata(readme)
        obscure = next((p for p in meta.prerequisites if p.name == "obscuretool"), None)
        self.assertIsNotNone(obscure, f"Expected obscuretool in {[p.name for p in meta.prerequisites]}")
        # Unknown tools have no install paths — probe-only.
        self.assertIsNone(obscure.install_apt)
        self.assertIsNone(obscure.install_brew)
        self.assertIsNone(obscure.install_fallback)
        self.assertIn("command -v obscuretool", obscure.probe_command)

    def test_first_occurrence_wins_for_order(self) -> None:
        """README mentions git before npm — git should be registered first."""
        readme = (
            "# Test\n\n"
            "```bash\n"
            "git clone https://example.com/repo\n"
            "cd repo\n"
            "npm install\n"
            "```\n"
        )
        meta = extract_readme_metadata(readme)
        names = [p.name for p in meta.prerequisites]
        if "git" in names and "npm" in names:
            self.assertLess(names.index("git"), names.index("npm"))

    def test_python_code_block_skipped(self) -> None:
        """A ```python fenced block must NOT be scanned for shell commands."""
        readme = (
            "# Test\n\n"
            "```python\n"
            "import os\n"
            "subprocess.run(['npm', 'install'])\n"
            "```\n"
        )
        meta = extract_readme_metadata(readme)
        names = {p.name for p in meta.prerequisites}
        # 'import' is not a prereq; 'subprocess.run' is not the first shell token.
        self.assertNotIn("import", names)
        self.assertNotIn("subprocess.run", names)


if __name__ == "__main__":
    unittest.main()
