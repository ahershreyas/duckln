"""Tests: runtime prerequisite plan includes README extras in its reason (Plan 57 Phase 4)."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from duckln.main import _build_runtime_prerequisite_plan


JUSTHIREME_README = """
# JustHireMe

## Requirements

- Node.js 20+
- Python 3.13+
- Rust stable
- uv latest

## Quick Start

```bash
npm install
```
"""


@dataclass
class _FakeIncident:
    fatal_line: str
    relevant_lines: tuple[str, ...] = ()
    package_hint: str | None = None
    project_dir: str = ""


class RuntimeRepairReadmeExtrasTest(unittest.TestCase):
    def test_node_install_reason_mentions_python_rust_uv_when_readme_lists_them(self) -> None:
        """When the user is on JustHireMe-shaped repo and node is missing,
        the runtime prereq plan's reason line must mention python/rust/uv too."""
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "README.md").write_text(JUSTHIREME_README, encoding="utf-8")

            incident = _FakeIncident(
                fatal_line="Command 'node' not found, but can be installed with: sudo apt install nodejs",
                relevant_lines=(),
                package_hint=None,
                project_dir=str(project_dir),
            )
            plan = _build_runtime_prerequisite_plan(incident=incident, execution_target="local")

            self.assertIsNotNone(plan)
            assert plan is not None
            self.assertEqual(plan.dependency, "node")
            # The reason line must mention at least one of the README extras.
            reason_lower = plan.reason.lower()
            self.assertIn("readme also declares", reason_lower)
            extras_mentioned = sum(1 for k in ("python", "rust", "uv") if k in reason_lower)
            self.assertGreaterEqual(
                extras_mentioned,
                1,
                f"Expected README extras in reason; got: {plan.reason!r}",
            )

    def test_no_project_dir_does_not_error(self) -> None:
        """When incident has no project_dir, plan should still be returned (no crash)."""
        incident = _FakeIncident(
            fatal_line="node: command not found",
            relevant_lines=(),
            package_hint=None,
            project_dir="",
        )
        plan = _build_runtime_prerequisite_plan(incident=incident, execution_target="local")
        self.assertIsNotNone(plan)

    def test_no_readme_does_not_add_extras_clause(self) -> None:
        """When project_dir has no README, the reason must not include 'README also declares'."""
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            incident = _FakeIncident(
                fatal_line="node: command not found",
                relevant_lines=(),
                package_hint=None,
                project_dir=str(project_dir),
            )
            plan = _build_runtime_prerequisite_plan(incident=incident, execution_target="local")
            self.assertIsNotNone(plan)
            assert plan is not None
            self.assertNotIn("README also declares", plan.reason)


if __name__ == "__main__":
    unittest.main()
