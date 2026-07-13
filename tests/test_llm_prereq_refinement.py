"""Tests: LLM refinement of heuristic prereqs with 24h cache (Plan 59 Fix 3)."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from duckln.repo_bringup import (
    _llm_refine_readme_prereqs,
    _parse_llm_refinement_json,
    _apply_refinement_to_heuristic,
    _read_llm_refinement_cache,
    _write_llm_refinement_cache,
)
from duckln.readme_skill import ReadmePrerequisite, _KNOWN_PREREQUISITES


def _heuristic_node_only() -> tuple[ReadmePrerequisite, ...]:
    spec = _KNOWN_PREREQUISITES["node"]
    return (
        ReadmePrerequisite(
            name="node",
            version_constraint=">=20",
            probe_command=spec[0],
            install_apt=spec[1],
            install_brew=spec[2],
            install_fallback=spec[3],
            install_winget=spec[4] if len(spec) > 4 else None,
            source_line="Node.js 20+",
        ),
    )


class ParseRefinementJsonTest(unittest.TestCase):
    def test_valid_json_parsed(self) -> None:
        raw = '{"additions":[{"name":"git","reason":"clone"}],"removals":[],"confidence":0.9}'
        additions, removals, confidence = _parse_llm_refinement_json(raw)
        self.assertEqual(additions, ["git"])
        self.assertEqual(removals, [])
        self.assertAlmostEqual(confidence, 0.9)

    def test_malformed_json_returns_zero_confidence(self) -> None:
        additions, removals, confidence = _parse_llm_refinement_json("not json")
        self.assertEqual(additions, [])
        self.assertEqual(removals, [])
        self.assertEqual(confidence, 0.0)

    def test_tool_outside_allowlist_ignored(self) -> None:
        raw = '{"additions":[{"name":"bazel","reason":"x"}],"removals":[],"confidence":0.9}'
        additions, _, _ = _parse_llm_refinement_json(raw)
        # bazel is not in _LLM_REFINEMENT_ALLOWLIST.
        self.assertNotIn("bazel", additions)
        self.assertEqual(additions, [])

    def test_json_with_markdown_fence_parsed(self) -> None:
        raw = '```json\n{"additions":[{"name":"git","reason":"x"}],"removals":[],"confidence":0.8}\n```'
        additions, _, confidence = _parse_llm_refinement_json(raw)
        self.assertEqual(additions, ["git"])
        self.assertAlmostEqual(confidence, 0.8)


class ApplyRefinementTest(unittest.TestCase):
    def test_addition_appends_known_tool(self) -> None:
        heuristic = _heuristic_node_only()
        refined = _apply_refinement_to_heuristic(
            heuristic, {"additions": ["git"], "removals": []}
        )
        names = {p.name for p in refined}
        self.assertIn("node", names)
        self.assertIn("git", names)

    def test_removal_drops_named_tool(self) -> None:
        heuristic = _heuristic_node_only()
        refined = _apply_refinement_to_heuristic(
            heuristic, {"additions": [], "removals": ["node"]}
        )
        self.assertEqual(len(refined), 0)

    def test_addition_of_unknown_tool_skipped(self) -> None:
        heuristic = _heuristic_node_only()
        refined = _apply_refinement_to_heuristic(
            heuristic, {"additions": ["totally_unknown_tool"], "removals": []}
        )
        names = {p.name for p in refined}
        self.assertNotIn("totally_unknown_tool", names)


class LlmRefineNoConfigTest(unittest.TestCase):
    def test_no_config_dir_returns_none(self) -> None:
        """Without config_dir, refinement is skipped (returns None → use heuristic)."""
        result = _llm_refine_readme_prereqs(
            readme_text="# Test",
            heuristic_prereqs=_heuristic_node_only(),
            repo_name="Test",
            repo_url="https://example.com/test",
            execution_target="local",
            config_dir=None,
        )
        self.assertIsNone(result)

    def test_empty_heuristic_returns_none(self) -> None:
        """Empty heuristic list — nothing to refine."""
        with tempfile.TemporaryDirectory() as tmp:
            result = _llm_refine_readme_prereqs(
                readme_text="# Test",
                heuristic_prereqs=(),
                repo_name="Test",
                repo_url="https://example.com/test",
                execution_target="local",
                config_dir=Path(tmp),
            )
            self.assertIsNone(result)


class LlmRefineCacheTest(unittest.TestCase):
    def test_cache_written_and_read_within_24h(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            refinement = {"additions": ["git"], "removals": []}
            _write_llm_refinement_cache(
                config_dir, repo_url="https://example.com/repo", refinement=refinement
            )
            cached = _read_llm_refinement_cache(
                config_dir, repo_url="https://example.com/repo"
            )
            self.assertIsNotNone(cached)
            self.assertEqual(cached.get("additions"), ["git"])

    def test_cache_stale_after_24h(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            refinement = {"additions": ["git"], "removals": []}
            _write_llm_refinement_cache(
                config_dir, repo_url="https://example.com/repo", refinement=refinement
            )
            # Backdate the cached_at field.
            from agent.memory import AGENT_MEMORY_DIR_NAME, SKILLS_DIR_NAME
            from duckln.repo_bringup import _url_slug
            old_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - (48 * 3600)))
            cache_file = (
                config_dir / AGENT_MEMORY_DIR_NAME / SKILLS_DIR_NAME
                / f"llm-prereq-refine-{_url_slug('https://example.com/repo')}.md"
            )
            import re as _re
            text = cache_file.read_text(encoding="utf-8")
            text = _re.sub(r'"cached_at":\s*"[^"]+"', f'"cached_at": "{old_iso}"', text)
            cache_file.write_text(text, encoding="utf-8")
            cached = _read_llm_refinement_cache(
                config_dir, repo_url="https://example.com/repo"
            )
            self.assertIsNone(cached, "Cache older than 24h must be ignored")


if __name__ == "__main__":
    unittest.main()
