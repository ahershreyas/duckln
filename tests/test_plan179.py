"""Plan 179 — (A) smart file-read windows, (B) the runtime capability adapter,
(C) per-agent model routing + /status."""

from __future__ import annotations

import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from duckln.harness.tools import (
    AgentContext,
    _fs_read_file_handler,
    _read_ceiling_for,
    _window_text,
)


def _ctx(config_dir, project_dir=None):
    return AgentContext(
        agent_name="t", mode=None, config_dir=Path(config_dir),
        project_dir=Path(project_dir) if project_dir else None, execution_target="local",
    )


class APartCeilings(unittest.TestCase):
    def test_ceilings_by_type(self):
        self.assertEqual(_read_ceiling_for("x.py"), 600)
        self.assertEqual(_read_ceiling_for("server.ts"), 600)
        self.assertEqual(_read_ceiling_for("build.log"), 150)
        self.assertEqual(_read_ceiling_for("notes.txt"), 150)
        self.assertEqual(_read_ceiling_for("package.json"), 150)
        self.assertEqual(_read_ceiling_for("README.md"), 150)
        self.assertEqual(_read_ceiling_for("data.bin"), 400)


class APartWindowing(unittest.TestCase):
    def setUp(self):
        self.ctx = _ctx("/nonexistent")
        self.big = "\n".join(f"line{i}" for i in range(1, 2001))

    def test_default_window_truncates_with_marker(self):
        out = _window_text(self.big, ceiling=600, ctx=self.ctx)
        self.assertTrue(out["truncated"])
        self.assertEqual((out["start_line"], out["end_line"]), (1, 600))
        self.assertIn("SYSTEM WARNING", out["text"])
        self.assertIn("start_line=601", out["text"])
        self.assertEqual(out["lines_total"], 2000)

    def test_focus_centers(self):
        out = _window_text(self.big, ceiling=600, ctx=self.ctx, focus_line=450)
        self.assertLessEqual(out["start_line"], 450)
        self.assertGreaterEqual(out["end_line"], 450)
        self.assertIn("targeted line 450", out["text"])

    def test_pagination(self):
        out = _window_text(self.big, ceiling=600, ctx=self.ctx, start_line=700, end_line=720)
        self.assertEqual((out["start_line"], out["end_line"]), (700, 720))

    def test_small_file_full_no_marker(self):
        out = _window_text("a\nb\nc", ceiling=600, ctx=self.ctx)
        self.assertFalse(out["truncated"])
        self.assertNotIn("SYSTEM WARNING", out["text"])

    def test_handler_never_raises_on_big_file(self):
        with TemporaryDirectory() as d:
            f = Path(d) / "huge.py"
            f.write_text("\n".join(f"x={i}" for i in range(5000)), encoding="utf-8")
            res = _fs_read_file_handler({"path": "huge.py"}, _ctx(d, d))
            self.assertTrue(res.ok)
            self.assertIn("SYSTEM WARNING", res.payload["text"])


class APartLocalFooter(unittest.TestCase):
    def test_footer_only_for_local_model(self):
        from duckln.harness.tools import _local_read_directive
        from state.access import write_config_snapshot

        with TemporaryDirectory() as d:
            write_config_snapshot(d, {"model": "qwen2.5:1.5b"})
            self.assertIn("LOCAL DIRECTIVE", _local_read_directive(_ctx(d)))
        with TemporaryDirectory() as d2:
            write_config_snapshot(d2, {"model": "claude-opus-4"})
            self.assertEqual(_local_read_directive(_ctx(d2)), "")


class BPartCapabilityAdapter(unittest.TestCase):
    def test_capability_class(self):
        from duckln.ai_client import CLOUD_REASONING, LOCAL_MODEL, model_capability_class

        self.assertEqual(model_capability_class("claude-opus-4"), CLOUD_REASONING)
        self.assertEqual(model_capability_class("qwen2.5:1.5b"), LOCAL_MODEL)

    def test_adapt_prompt(self):
        from duckln.ai_client import adapt_reasoning_prompt

        self.assertIn("LOCAL MODEL DIRECTIVE", adapt_reasoning_prompt("base", "qwen2.5:1.5b"))
        self.assertEqual(adapt_reasoning_prompt("base", "claude-opus-4"), "base")


class CPartRouting(unittest.TestCase):
    def test_read_write_routing(self):
        from duckln.ai_client import read_active_routing, write_active_routing

        with TemporaryDirectory() as d:
            self.assertEqual(read_active_routing(d), {})  # Unified default
            write_active_routing(d, {"supervisor": "claude-opus-4", "REPO_AGENT": "qwen:7b", "x": ""})
            self.assertEqual(read_active_routing(d), {"SUPERVISOR": "claude-opus-4", "REPO_AGENT": "qwen:7b"})

    def test_unset_role_falls_back_to_global(self):
        from duckln.ai_client import build_llm_client_for_role

        with TemporaryDirectory() as d:
            # No provider configured → global builder returns None (Unified fallback path).
            self.assertIsNone(build_llm_client_for_role(d, "SUPERVISOR"))

    def test_status_render_lists_roles(self):
        from duckln.main import _render_agent_routing_status

        with TemporaryDirectory() as d:
            cfg = types.SimpleNamespace(model="m1", provider=types.SimpleNamespace(value="ollama"))
            text = _render_agent_routing_status(Path(d), cfg)
            # Plan 181: WEB_READER is back as a routable role (it now has a real consumer).
            for label in ("Supervisor", "Repo agent", "Error agent", "Web reader"):
                self.assertIn(label, text)
            self.assertIn("Unified", text)

    def test_unified_command_clears_routing(self):
        from duckln.ai_client import read_active_routing, write_active_routing
        from duckln.main import _handle_agent_routing_command

        with TemporaryDirectory() as d:
            write_active_routing(d, {"SUPERVISOR": "x"})
            cfg = types.SimpleNamespace(model="m1", provider=types.SimpleNamespace(value="ollama"))
            paths = types.SimpleNamespace(config_dir=Path(d))
            _handle_agent_routing_command(
                cfg, paths,
                select=lambda _m, _o: "Unified — one model for all agents",
                text_prompt=None, display=lambda _t: None,
            )
            self.assertEqual(read_active_routing(d), {})


if __name__ == "__main__":
    unittest.main()
