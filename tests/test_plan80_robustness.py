from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from duckln.diagnostics import match_deterministic_fix, ErrorCategory
from duckln.repo_bringup import (
    _detect_required_go_version,
    _detect_required_node_version,
    _detect_required_python_version,
    _detect_required_rust_version,
    _framework_node_requirement,
    _tool_versions_map,
    _xy_below,
    resolve_runtime_project_dir,
)
from state.access import read_common_lessons, write_common_lesson
from state.repo_catalog import RepoCatalogRecord


def _repo(url=""):
    return RepoCatalogRecord("demo", url, 1, "d", "Custom", "x", "2026-04-01")


def _write_local(cfg, repo, name, content):
    proj = Path(resolve_runtime_project_dir(cfg, repo, execution_target="local"))
    proj.mkdir(parents=True, exist_ok=True)
    (proj / name).write_text(content, encoding="utf-8")


class TestFrameworkNodeRequirement(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(_framework_node_requirement({"devDependencies": {"vite": "^7.0.0"}}), "20")
        self.assertEqual(_framework_node_requirement({"devDependencies": {"vite": "^5.0.0"}}), "18")
        self.assertEqual(_framework_node_requirement({"dependencies": {"next": "15.1.0"}}), "20")
        self.assertEqual(_framework_node_requirement({"dependencies": {"@angular/cli": "^19"}}), "20")
        self.assertIsNone(_framework_node_requirement({"dependencies": {"react": "18"}}))

    def test_vite_repo_without_engines_resolves_via_framework(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, repo = Path(tmp), _repo()
            _write_local(cfg, repo, "package.json", '{"devDependencies": {"vite": "^7.0.4"}}')
            v = _detect_required_node_version(config_dir=cfg, repo=repo, detected_files=("package.json",))
            self.assertEqual(v, "20")


class TestGenericRequirements(unittest.TestCase):
    def test_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, repo = Path(tmp), _repo()
            _write_local(cfg, repo, "pyproject.toml", '[project]\nrequires-python = ">=3.11"\n')
            self.assertEqual(_detect_required_python_version(config_dir=cfg, repo=repo, detected_files=("pyproject.toml",)), "3.11")

    def test_go(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, repo = Path(tmp), _repo()
            _write_local(cfg, repo, "go.mod", "module x\n\ngo 1.22\n")
            self.assertEqual(_detect_required_go_version(config_dir=cfg, repo=repo, detected_files=("go.mod",)), "1.22")

    def test_rust(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, repo = Path(tmp), _repo()
            _write_local(cfg, repo, "Cargo.toml", '[package]\nrust-version = "1.75"\n')
            self.assertEqual(_detect_required_rust_version(config_dir=cfg, repo=repo, detected_files=("cargo.toml",)), "1.75")

    def test_tool_versions(self):
        self.assertEqual(_tool_versions_map("node 20.11.0\npython 3.11.5")["python"], "3.11.5")

    def test_xy_below(self):
        self.assertTrue(_xy_below("3.10", "3.11"))
        self.assertFalse(_xy_below("3.12", "3.11"))
        self.assertTrue(_xy_below(None, "3.11"))


class TestDeterministicFixes(unittest.TestCase):
    def test_node_engine(self):
        f = match_deterministic_fix(stderr="Vite requires Node.js version 20.19+", execution_target="vm")
        self.assertEqual(f.category, ErrorCategory.ENGINE_MISMATCH)
        self.assertIn("setup_20.x", f.fix_command)

    def test_command_not_found(self):
        f = match_deterministic_fix(stderr="sh: 1: pnpm: command not found", execution_target="vm")
        self.assertEqual(f.fix_command, "npm install -g pnpm")

    def test_port_in_use(self):
        f = match_deterministic_fix(stderr="Error: listen EADDRINUSE: address already in use :::3000")
        self.assertIn("3000", f.fix_command)

    def test_missing_module(self):
        f = match_deterministic_fix(stderr="ModuleNotFoundError: No module named 'flask'")
        self.assertEqual(f.fix_command, "python3 -m pip install flask")

    def test_unmatched(self):
        self.assertIsNone(match_deterministic_fix(stderr="some unrelated error"))


class TestCommonLessons(unittest.TestCase):
    def test_round_trip_and_dedup(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_common_lesson(tmp, signature="engine_mismatch", lesson="vite needs node 20", fix_command="install node 20")
            write_common_lesson(tmp, signature="engine_mismatch", lesson="vite needs node 20", fix_command="install node 20")
            lessons = read_common_lessons(tmp)
            self.assertIn("engine_mismatch", lessons)
            self.assertEqual(lessons["engine_mismatch"]["fix"], "install node 20")


class TestWebRepairLive(unittest.TestCase):
    def test_browses_each_url_and_uses_page_content(self):
        import duckln.web_runtime as wr

        calls = {"trace": [], "fetched": []}

        class _Result:
            def __init__(self, url, title, excerpt):
                self.url, self.title, self.excerpt = url, title, excerpt

        class _Summary:
            def __init__(self, title, excerpt, steps):
                self.title, self.excerpt, self.steps = title, excerpt, steps

        orig_search = wr.search_runtime_issue_results
        orig_fetch = wr._fetch_web_reference_summary
        wr.search_runtime_issue_results = lambda **kw: (
            _Result("https://example.com/a", "A", "snippet a"),
            _Result("https://example.com/b", "B", "snippet b"),
        )
        wr._fetch_web_reference_summary = lambda label, url: (
            calls["fetched"].append(url)
            or _Summary("Fix B", "Upgrade Node to 20 via NodeSource. " * 20, ("run nodesource",))
        ) if "b" in url else _Summary("A", "short", ())
        try:
            evidence = wr.gather_repair_fix(
                command="npm run dev", error_text="vite requires node 20",
                trace=lambda m: calls["trace"].append(m), max_pages=3,
            )
        finally:
            wr.search_runtime_issue_results = orig_search
            wr._fetch_web_reference_summary = orig_fetch

        self.assertTrue(any("Browsing https://example.com/a" in m for m in calls["trace"]))
        self.assertTrue(calls["fetched"])  # real page content fetched, not just snippet
        self.assertIn("NodeSource", evidence or "")


if __name__ == "__main__":
    unittest.main()
