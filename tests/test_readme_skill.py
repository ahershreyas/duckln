"""Tests: README skill — enriched extraction + prereq preflight (Plan 57 Phase 1, 1B, 2)."""

from __future__ import annotations

import unittest

from duckln.readme_skill import (
    ReadmePrerequisite,
    extract_readme_metadata,
    detect_package_manager,
    build_prerequisite_preflight_steps,
)


JUSTHIREME_README = """
# JustHireMe

Local-first AI job intelligence.

## Requirements

| Tool | Version |
| --- | --- |
| Node.js | 20+ |
| Python | 3.13+ |
| Rust | stable |
| uv | latest stable |

## Quick Start

To preview the frontend:

```bash
git clone https://github.com/example/JustHireMe.git
cd JustHireMe
npm install
npm run dev
```

This starts the Vite frontend.

## Usage

Once running, the app window opens automatically. Tray icon → Settings.
"""


VLLM_README = """
# vLLM

A fast and easy-to-use library for LLM inference.

## Getting Started

Install vLLM with uv (recommended) or pip:

```bash
uv pip install vllm
```

Visit our documentation to learn more.

## Quickstart

Run an inference server:

```bash
vllm serve meta-llama/Llama-3.1-8B
```
"""


PYTHON_DJANGO_README = """
# Awesome Django App

A simple Django web app.

## Prerequisites

- Python 3.11 or higher
- pip

## Installation

```
pip install -r requirements.txt
```

## Usage

Start the dev server:

```
python manage.py runserver
```

Then visit http://localhost:8000.
"""


EMPTY_README = ""

NO_PREREQS_README = """
# Simple Project

Just a static HTML page. Open index.html in your browser.
"""


class ReadmeMetadataExtractionTest(unittest.TestCase):
    def test_justhireme_extracts_all_four_prereqs(self) -> None:
        meta = extract_readme_metadata(JUSTHIREME_README)
        names = {p.name for p in meta.prerequisites}
        self.assertIn("node", names)
        self.assertIn("python", names)
        self.assertIn("rust", names)
        self.assertIn("uv", names)

    def test_justhireme_extracts_version_constraint(self) -> None:
        meta = extract_readme_metadata(JUSTHIREME_README)
        node_prereq = next((p for p in meta.prerequisites if p.name == "node"), None)
        self.assertIsNotNone(node_prereq)
        # Node 20+ should produce a >=20 constraint.
        self.assertIsNotNone(node_prereq.version_constraint)
        self.assertIn("20", node_prereq.version_constraint or "")

    def test_vllm_extracts_uv_prereq(self) -> None:
        meta = extract_readme_metadata(VLLM_README)
        names = {p.name for p in meta.prerequisites}
        self.assertIn("uv", names)

    def test_django_extracts_python_prereq(self) -> None:
        meta = extract_readme_metadata(PYTHON_DJANGO_README)
        names = {p.name for p in meta.prerequisites}
        self.assertIn("python", names)

    def test_empty_readme_returns_no_prereqs(self) -> None:
        meta = extract_readme_metadata(EMPTY_README)
        self.assertEqual(meta.prerequisites, ())
        self.assertEqual(meta.usage_summary, "")

    def test_no_prereqs_readme_returns_empty_list(self) -> None:
        meta = extract_readme_metadata(NO_PREREQS_README)
        self.assertEqual(meta.prerequisites, ())

    def test_usage_summary_extracted_from_quick_start_or_usage(self) -> None:
        meta = extract_readme_metadata(JUSTHIREME_README)
        self.assertTrue(meta.usage_summary)
        # Should mention something from the Usage section.
        self.assertIn("app", meta.usage_summary.lower())

    def test_usage_example_is_first_code_block_in_usage_section(self) -> None:
        meta = extract_readme_metadata(PYTHON_DJANGO_README)
        self.assertIn("manage.py", meta.usage_example)

    def test_confidence_increases_with_extracted_fields(self) -> None:
        rich = extract_readme_metadata(JUSTHIREME_README)
        poor = extract_readme_metadata(NO_PREREQS_README)
        self.assertGreater(rich.confidence, poor.confidence)


class PackageManagerDetectionTest(unittest.TestCase):
    def test_linux_maps_to_apt(self) -> None:
        self.assertEqual(detect_package_manager("Linux"), "apt")

    def test_darwin_maps_to_brew(self) -> None:
        self.assertEqual(detect_package_manager("Darwin"), "brew")

    def test_windows_returns_winget(self) -> None:
        # Plan 61 Fix A: Windows now maps to winget (previously None).
        self.assertEqual(detect_package_manager("Windows"), "winget")

    def test_empty_returns_none(self) -> None:
        self.assertIsNone(detect_package_manager(""))


class PrerequisiteInstallCommandTest(unittest.TestCase):
    def test_node_on_apt(self) -> None:
        prereq = ReadmePrerequisite(
            name="node", version_constraint=">=20", probe_command="node --version",
            install_apt="nodejs", install_brew="node", install_fallback=None,
        )
        self.assertEqual(prereq.install_command_for(package_manager="apt"), "sudo apt install -y nodejs")

    def test_node_on_brew(self) -> None:
        prereq = ReadmePrerequisite(
            name="node", version_constraint=">=20", probe_command="node --version",
            install_apt="nodejs", install_brew="node", install_fallback=None,
        )
        self.assertEqual(prereq.install_command_for(package_manager="brew"), "brew install node")

    def test_uv_on_apt_falls_back_to_curl(self) -> None:
        prereq = ReadmePrerequisite(
            name="uv", version_constraint=None, probe_command="uv --version",
            install_apt=None, install_brew="uv",
            install_fallback="curl -LsSf https://astral.sh/uv/install.sh | sh",
        )
        # uv has no apt package — should fall back to the curl installer on Linux.
        self.assertEqual(prereq.install_command_for(package_manager="apt"), "curl -LsSf https://astral.sh/uv/install.sh | sh")

    def test_no_install_path_returns_none(self) -> None:
        prereq = ReadmePrerequisite(
            name="obscure_tool", version_constraint=None, probe_command="obscure_tool --version",
            install_apt=None, install_brew=None, install_fallback=None,
        )
        self.assertIsNone(prereq.install_command_for(package_manager="apt"))


class PrerequisitePreflightStepsTest(unittest.TestCase):
    def test_justhireme_on_linux_produces_four_preflight_triples(self) -> None:
        meta = extract_readme_metadata(JUSTHIREME_README)
        triples = build_prerequisite_preflight_steps(
            meta.prerequisites, system_probe_os="Linux",
        )
        # We expect at minimum node, python, rust, uv (4 prereqs).
        self.assertGreaterEqual(len(triples), 4)
        # Each triple is (purpose, probe, install_command).
        for purpose, probe, install in triples:
            self.assertTrue(purpose)
            self.assertTrue(probe)
            self.assertTrue(install)

    def test_apt_install_command_used_on_linux(self) -> None:
        meta = extract_readme_metadata(JUSTHIREME_README)
        triples = build_prerequisite_preflight_steps(
            meta.prerequisites, system_probe_os="Linux",
        )
        installs = [t[2] for t in triples]
        # Node on Linux should use apt.
        node_install = next((cmd for cmd in installs if "nodejs" in cmd), None)
        self.assertIsNotNone(node_install)
        self.assertIn("apt install", node_install)

    def test_empty_prereqs_returns_empty_tuple(self) -> None:
        triples = build_prerequisite_preflight_steps((), system_probe_os="Linux")
        self.assertEqual(triples, ())


if __name__ == "__main__":
    unittest.main()
