"""Plan 133 — five live-run fixes: F2 resume state, F3 build-scratch reclaim,
F5 dot recolor on failure/interrupt, F6 maintained logical-thinking.md. (F1 custom
resize wiring + F4 live-investigate + F7b cards are verified live/by import.)"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from duckln.resource_manager import reclaim_commands
from duckln.recovery import append_thinking_log, thinking_log_path
import duckln.repo_bringup as rb


class BuildScratchReclaim(unittest.TestCase):
    def test_reclaim_clears_pyinstaller_scratch_not_repo(self):
        cmds = reclaim_commands(execution_target="vm")
        joined = " ".join(cmds)
        self.assertIn(".codex-temp-sidecar", joined)
        self.assertIn("build_cache", joined)
        # never the repo / deps / user data
        self.assertNotIn("node_modules", joined)
        self.assertNotIn(".venv", joined)
        self.assertTrue(all("|| true" in c for c in cmds))


class ThinkingLogFile(unittest.TestCase):
    def test_creates_then_appends_maintained_file(self):
        with tempfile.TemporaryDirectory() as t:
            p1 = append_thinking_log(t, repo_slug="r", title="Planning brief", lines=["stack: tauri+python"], surface="plan")
            p2 = append_thinking_log(t, repo_slug="r", title="Recovering: npm run x", lines=["decision: skip"], surface="recover")
            self.assertEqual(p1, p2)
            self.assertEqual(p1, thinking_log_path(t))
            txt = Path(p1).read_text()
            self.assertTrue(txt.startswith("# Duckln"))          # created with header
            self.assertIn("Planning brief", txt)                  # episode 1 appended
            self.assertIn("Recovering: npm run x", txt)           # episode 2 appended (not overwritten)
            self.assertIn("· plan", txt)
            self.assertIn("· recover", txt)

    def test_redacts_secrets(self):
        with tempfile.TemporaryDirectory() as t:
            p = append_thinking_log(t, repo_slug="r", title="t", lines=["token sk-proj-ABCDEF1234567890SECRET"], surface="plan")
            self.assertNotIn("sk-proj-ABCDEF1234567890SECRET", Path(p).read_text())


class ResumeState(unittest.TestCase):
    def test_done_steps_roundtrip_and_clear(self):
        from state.store import initialize_state_store
        with tempfile.TemporaryDirectory() as t:
            cfg = Path(t)
            initialize_state_store(cfg)
            self.assertEqual(rb._load_done_steps(cfg, "repo"), set())
            done: set[str] = set()
            rb._mark_step_done(cfg, "repo", "npm ci", done)
            rb._mark_step_done(cfg, "repo", "npm run build:sidecar", done)
            self.assertEqual(rb._load_done_steps(cfg, "repo"), {"npm ci", "npm run build:sidecar"})
            rb._clear_done_steps(cfg, "repo")
            self.assertEqual(rb._load_done_steps(cfg, "repo"), set())


class InterruptEmission(unittest.TestCase):
    """F7a: a step cancelled mid-flight emits a tracked 'Tool interrupted' line (red dot)
    and re-raises, instead of silently propagating."""

    def test_keyboard_interrupt_emits_tracked_line_and_reraises(self):
        import tempfile
        from duckln.ai_client import Provider
        from duckln.config import AppConfig, resolve_config_paths, save_app_config
        from duckln.modes import ControlMode
        from duckln.plan_mode import PlanRecord, PlanStep
        from state.access import write_config_snapshot

        class _Interrupting:
            def run(self, command, *a, **k):
                raise KeyboardInterrupt()

        steps = (
            PlanStep(index=1, title="Install deps", description="", command="npm install",
                     safety_class="S0", verification="exit 0", rationale="", estimated_seconds=1, target="local"),
        )
        plan = PlanRecord(
            plan_id="p1", objective="x", context_summary="family=node_typescript",
            steps=steps, risks=(), rollback="", estimated_seconds=1,
            created_at="2026-05-28T00:00:00Z", status="approved", repo_slug="owner/demo",
            mode_at_creation="hootlwo",
        )
        with tempfile.TemporaryDirectory() as tdname:
            td = type("T", (), {"name": tdname})()
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": td.name})
            save_app_config(AppConfig(provider=Provider.OPENAI, model="gpt", api_key=None,
                                      mode=ControlMode.HOOTLWO, plan_mode_enabled=True), paths)
            write_config_snapshot(paths.config_dir, {"execution_target": "local"})
            cfg = AppConfig(provider=Provider.OPENAI, model="gpt", api_key=None,
                            mode=ControlMode.HOOTLWO, plan_mode_enabled=True)
            out: list[str] = []
            with self.assertRaises(KeyboardInterrupt):
                rb.resume_with_approved_plan(
                    plan=plan, paths=paths, current=cfg,
                    approve=lambda _m: True, display=out.append, runner=_Interrupting(),
                    emit_thought=lambda _t: None,
                )
            self.assertTrue(any("Tool interrupted" in line for line in out),
                            f"no interrupt line in: {out}")


class DotRecolor(unittest.TestCase):
    def test_failure_and_done_colors(self):
        try:
            from duckln.textual_ui import _message_dot_color as d
        except Exception:
            self.skipTest("textual not available")
        self.assertEqual(d("✗ Step 7 failed (exit_code=1)."), "red")
        self.assertEqual(d("Plan halted: needs a token"), "red")
        self.assertEqual(d("Tool interrupted"), "red")
        self.assertEqual(d("☑ Step 6/12 — Install — done"), "green")
        self.assertEqual(d("✅ Plan executed — your repo is ready! 🎉"), "green")
        self.assertIsNone(d("Installing dependencies — this can take a moment…"))


class EditDiffCard(unittest.TestCase):
    """F7b: edit/diff data + Claude-style edit card."""

    def test_diff_summary_counts_added_removed(self):
        from duckln.harness.tools import _edit_diff_summary
        s = _edit_diff_summary("a\nb\nc\n", "a\nB\nc\nd\n", path="x.py")
        # 'b'→'B' = 1 removed + 1 added; 'd' = 1 added
        self.assertEqual(s["added"], 2)
        self.assertEqual(s["removed"], 1)
        self.assertIn("+B", s["diff"])
        self.assertIn("-b", s["diff"])

    def test_diff_summary_truncates_large_diff(self):
        from duckln.harness.tools import _edit_diff_summary
        big = "\n".join(str(i) for i in range(500))
        s = _edit_diff_summary("", big, path="x.py", max_diff_lines=10)
        self.assertIn("more diff lines", s["diff"])

    def test_fs_edit_handler_returns_diff_keys(self):
        from duckln.harness.tools import _fs_edit_handler, _fs_write_handler, AgentContext
        from duckln.modes import ControlMode
        with tempfile.TemporaryDirectory() as t:
            proj = Path(t)
            f = proj / "app.py"
            f.write_text("x = 1\ny = 2\n", encoding="utf-8")
            ctx = AgentContext(agent_name="a", mode=ControlMode.HOOTLWO,
                               config_dir=proj, project_dir=proj, execution_target="local")
            r = _fs_edit_handler({"path": "app.py", "old_string": "y = 2", "new_string": "y = 3"}, ctx)
            self.assertTrue(r.ok)
            self.assertEqual(r.payload["added"], 1)
            self.assertEqual(r.payload["removed"], 1)
            self.assertIn("diff", r.payload)
            # a fresh write reports created + adds
            w = _fs_write_handler({"path": "new.py", "content": "a\nb\n"}, ctx)
            self.assertTrue(w.ok)
            self.assertTrue(w.payload["created"])
            self.assertEqual(w.payload["added"], 2)

    def test_render_edit_card_header_and_body(self):
        from duckln.ui import render_edit_card
        header = render_edit_card("app.py", added=12, removed=3)
        self.assertIn("app.py", header)
        self.assertIn("+12", header)
        self.assertIn("-3", header)
        self.assertIn("Edit", header)
        # created → "Create"; with diff → body appended on a new line
        card = render_edit_card("new.py", added=2, removed=0, created=True, diff="+x\n+y")
        self.assertIn("Create new.py", _strip_ansi(card))
        self.assertIn("\n", card)
        self.assertIn("+x", card)


def _strip_ansi(s: str) -> str:
    import re
    return re.sub(r"\033\[[0-9;]*m", "", s)


if __name__ == "__main__":
    unittest.main()
