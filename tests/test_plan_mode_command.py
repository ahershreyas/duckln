"""Plan 67: /plan slash command + handler tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from duckln.ai_client import Provider
from duckln.config import (
    AppConfig,
    ConfigPaths,
    resolve_config_paths,
    save_app_config,
)
from duckln.main import _handle_plan_command, handle_session_command
from duckln.modes import ControlMode
from duckln.plan_mode import PlanRecord, PlanStep
from state.access import (
    clear_pending_plan,
    read_pending_plan,
    write_pending_plan,
)


def _config(plan_mode: bool = False) -> tuple[ConfigPaths, AppConfig]:
    td = tempfile.TemporaryDirectory()
    paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": td.name})
    cfg = AppConfig(
        provider=Provider.OPENAI, model="gpt-4o-mini", api_key="key",
        mode=ControlMode.HOTL, plan_mode_enabled=plan_mode,
    )
    save_app_config(cfg, paths)
    return paths, cfg, td  # type: ignore


def _sample_plan(plan_id: str = "abc12345") -> PlanRecord:
    return PlanRecord(
        plan_id=plan_id,
        objective="Set up x",
        context_summary="family=node",
        steps=(PlanStep(
            index=1, title="install", description="", command="echo ok",
            safety_class="S0", verification="exit 0", rationale="",
            estimated_seconds=1, confidence=0.9,
        ),),
        risks=(), rollback="", estimated_seconds=1,
        created_at="2026-05-19T00:00:00Z", status="pending",
        repo_slug="owner/x", mode_at_creation="hotl",
    )


class PlanCommandTest(unittest.TestCase):
    def test_status_always_on_no_pending(self) -> None:
        # Plan 188: Plan Mode is always on — status never shows "off".
        paths, cfg, td = _config(plan_mode=True)
        self.addCleanup(td.cleanup)
        out: list[str] = []
        _handle_plan_command(command="/plan", current=cfg, paths=paths, display=out.append, approve=None)
        joined = "\n".join(out)
        self.assertIn("Plan Mode: on", joined)
        self.assertIn("No pending plan", joined)

    def test_on_is_noop_explains_always_on(self) -> None:
        # Plan 188: `/plan on` no longer toggles — it explains Plan Mode is always on.
        paths, cfg, td = _config(plan_mode=True)
        self.addCleanup(td.cleanup)
        out: list[str] = []
        updated = _handle_plan_command(command="/plan on", current=cfg, paths=paths, display=out.append, approve=None)
        self.assertTrue(updated.plan_mode_enabled)  # unchanged (still on)
        self.assertTrue(any("always on" in line for line in out))

    def test_off_is_noop_explains_always_on(self) -> None:
        # Plan 188: `/plan off` no longer disables — it explains Plan Mode is always on.
        paths, cfg, td = _config(plan_mode=True)
        self.addCleanup(td.cleanup)
        out: list[str] = []
        updated = _handle_plan_command(command="/plan off", current=cfg, paths=paths, display=out.append, approve=None)
        self.assertTrue(updated.plan_mode_enabled)  # NOT disabled
        joined = "\n".join(out)
        self.assertIn("always on", joined)
        self.assertNotIn("disabled", joined.lower())

    def test_approve_with_no_pending_friendly_error(self) -> None:
        paths, cfg, td = _config(plan_mode=True)
        self.addCleanup(td.cleanup)
        out: list[str] = []
        _handle_plan_command(command="/plan approve", current=cfg, paths=paths, display=out.append, approve=None)
        joined = "\n".join(out)
        self.assertIn("No pending plan", joined)

    def test_show_renders_pending(self) -> None:
        paths, cfg, td = _config(plan_mode=True)
        self.addCleanup(td.cleanup)
        write_pending_plan(paths.config_dir, _sample_plan().to_dict())
        out: list[str] = []
        _handle_plan_command(command="/plan show", current=cfg, paths=paths, display=out.append, approve=None)
        joined = "\n".join(out)
        self.assertIn("Set up x", joined)

    def test_reject_clears_pending_and_appends_history(self) -> None:
        from state.access import list_plan_history

        paths, cfg, td = _config(plan_mode=True)
        self.addCleanup(td.cleanup)
        write_pending_plan(paths.config_dir, _sample_plan().to_dict())
        out: list[str] = []
        _handle_plan_command(command="/plan reject", current=cfg, paths=paths, display=out.append, approve=None)
        self.assertIsNone(read_pending_plan(paths.config_dir))
        history = list_plan_history(paths.config_dir, limit=5)
        self.assertTrue(any(entry.get("decision") == "rejected" for entry in history))

    def test_edit_in_textual_ui_does_not_block(self) -> None:
        # Plan 69 Fix 2: with terminal_interface set, /plan edit must NOT spawn a
        # blocking TTY editor; it writes the file and instructs /plan reload.
        import duckln.main as _main

        paths, cfg, td = _config(plan_mode=True)
        self.addCleanup(td.cleanup)
        write_pending_plan(paths.config_dir, _sample_plan().to_dict())
        out: list[str] = []
        spawned: list = []

        def _fake_open(path):
            spawned.append(path)
            return True

        orig = _main._open_path_non_blocking
        _main._open_path_non_blocking = _fake_open
        try:
            _handle_plan_command(
                command="/plan edit", current=cfg, paths=paths,
                display=out.append, approve=None, terminal_interface=object(),
            )
        finally:
            _main._open_path_non_blocking = orig
        joined = "\n".join(out)
        self.assertIn("/plan reload", joined)
        self.assertEqual(len(spawned), 1)

    def test_reload_reparses_edited_markdown(self) -> None:
        from duckln.plan_mode import (
            PlanRecord,
            ensure_plan_dir,
            plan_markdown_path,
            render_plan_markdown,
        )

        paths, cfg, td = _config(plan_mode=True)
        self.addCleanup(td.cleanup)
        plan = _sample_plan()
        write_pending_plan(paths.config_dir, plan.to_dict())
        ensure_plan_dir(paths.config_dir)
        path = plan_markdown_path(paths.config_dir, plan.plan_id)
        md = render_plan_markdown(plan).replace("install", "install-edited")
        path.write_text(md, encoding="utf-8")
        out: list[str] = []
        _handle_plan_command(
            command="/plan reload", current=cfg, paths=paths,
            display=out.append, approve=None,
        )
        reloaded = read_pending_plan(paths.config_dir)
        self.assertIsNotNone(reloaded)
        self.assertEqual(reloaded["status"], "edited")
        self.assertTrue(any("reloaded" in line.lower() for line in out))

    def test_history_subcommand(self) -> None:
        from state.access import append_plan_history

        paths, cfg, td = _config(plan_mode=True)
        self.addCleanup(td.cleanup)
        append_plan_history(
            paths.config_dir,
            {"plan_id": "p1", "objective": "obj", "steps": [], "amendment_count": 0},
            decision="completed",
        )
        out: list[str] = []
        _handle_plan_command(command="/plan history", current=cfg, paths=paths, display=out.append, approve=None)
        joined = "\n".join(out)
        self.assertIn("history", joined.lower())


if __name__ == "__main__":
    unittest.main()
