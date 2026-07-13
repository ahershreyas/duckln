from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import duckln.repo_bringup as rb
from duckln.ai_client import Provider
from duckln.config import AppConfig, resolve_config_paths, save_app_config
from duckln.modes import ControlMode
from duckln.plan_mode import PlanRecord, PlanStep
from duckln.repo_bringup import (
    _ensure_host_bind,
    _host_reachable_url,
    _looks_server_ready,
)
from duckln.ui import render_plan_panel
from state.access import write_config_snapshot


class TestHelpers(unittest.TestCase):
    def test_server_ready_signals(self):
        self.assertTrue(_looks_server_ready("VITE v7.3.3 ready in 390 ms"))
        self.assertTrue(_looks_server_ready("Local:   http://localhost:1420/"))
        self.assertFalse(_looks_server_ready("Reading package lists... Done"))

    def test_host_bind_per_tool(self):
        self.assertEqual(_ensure_host_bind("npm run dev", execution_target="vm", underlying="vite"), "npm run dev -- --host 0.0.0.0")
        self.assertEqual(_ensure_host_bind("npm run dev", execution_target="vm", underlying="next dev"), "npm run dev -- -H 0.0.0.0")
        self.assertEqual(_ensure_host_bind("uvicorn app:app", execution_target="vm"), "uvicorn app:app --host 0.0.0.0")
        self.assertEqual(_ensure_host_bind("npm run dev", execution_target="local"), "npm run dev")  # local untouched
        self.assertEqual(_ensure_host_bind("npm run dev --host 0.0.0.0", execution_target="vm"), "npm run dev --host 0.0.0.0")  # already bound

    def test_host_reachable_url(self):
        # local → unchanged
        self.assertEqual(_host_reachable_url("http://localhost:1420/", execution_target="local", vm_name=None), "http://localhost:1420/")


class TestAmendmentPanelRenders(unittest.TestCase):
    def test_amendment_step_no_crash(self):
        plan = PlanRecord(
            plan_id="p", objective="o", context_summary="c",
            steps=(PlanStep(index=1, title="Recovery", description="", command="npm install",
                            safety_class="S2", verification="ok", rationale="", estimated_seconds=5,
                            origin="amendment"),),
            risks=(), rollback="", estimated_seconds=5, created_at="t", status="amended",
            repo_slug="a/b", mode_at_creation="hootlwo",
        )
        out = render_plan_panel(plan)  # must NOT raise NameError(DUCKLN_WARNING)
        self.assertIn("amendment", out)


class _ServerRunner:
    """Fake runner: launch returns a PID; first poll shows a served URL."""
    def __init__(self, serve=True):
        self.calls = []
        self.serve = serve

    def run(self, command, **kw):
        self.calls.append(command)
        if "DUCKLN_RUN_PID" in command:
            return SimpleNamespace(stdout="DUCKLN_RUN_PID:4242", exit_code=0, timed_out=False)
        # poll
        if self.serve:
            return SimpleNamespace(stdout="VITE ready\n  Local: http://localhost:1420/\nDUCKLN_ALIVE", exit_code=0, timed_out=False)
        return SimpleNamespace(stdout="Error: boom\nDUCKLN_DEAD", exit_code=0, timed_out=False)


def _run_plan(serve, td):
    paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": td.name})
    write_config_snapshot(paths.config_dir, {"execution_target": "local"})
    cfg = AppConfig(provider=Provider.OPENAI, model="gpt", api_key=None,
                    mode=ControlMode.HOOTLWO, plan_mode_enabled=True)
    save_app_config(cfg, paths)
    plan = PlanRecord(
        plan_id="p1", objective="o", context_summary="family=node_typescript",
        steps=(PlanStep(index=1, title="Run app", description="", command="npm run dev",
                        safety_class="S0", verification="serves", rationale="", estimated_seconds=10,
                        target="local"),),
        risks=(), rollback="", estimated_seconds=10, created_at="t", status="approved",
        repo_slug="acme/demo", mode_at_creation="hootlwo",
    )
    from duckln.repo_bringup import resume_with_approved_plan
    runner = _ServerRunner(serve=serve)
    orig = rb.ControlledCommandRunner
    rb.ControlledCommandRunner = lambda **kw: runner
    # speed up the poll
    import duckln.repo_bringup as _rb
    out, opened = [], []
    orig_open = _rb._open_in_local_browser
    _rb._open_in_local_browser = lambda url, *, display: opened.append(url)
    try:
        result = resume_with_approved_plan(
            plan=plan, paths=paths, current=cfg, approve=lambda _m: True,
            display=out.append, runner=runner,
        )
    finally:
        rb.ControlledCommandRunner = orig
        _rb._open_in_local_browser = orig_open
    return result, out, opened, runner


class TestRunServerSuccess(unittest.TestCase):
    def test_serving_is_success_and_opens_browser(self):
        with tempfile.TemporaryDirectory() as tdname:
            td = type("T", (), {"name": tdname})()
            result, out, opened, runner = _run_plan(serve=True, td=td)
            joined = "\n".join(out)
            self.assertIn("your repo is ready", joined)          # completed, not failed
            self.assertNotIn("failed", joined.lower())
            self.assertTrue(any("localhost:1420" in u for u in opened))  # opened in local browser
            # launched detached (nohup), not a blocking `npm run dev`
            self.assertTrue(any("nohup" in c for c in runner.calls))

    def test_crash_routes_to_amendment(self):
        with tempfile.TemporaryDirectory() as tdname:
            td = type("T", (), {"name": tdname})()
            result, out, opened, runner = _run_plan(serve=False, td=td)
            joined = "\n".join(out)
            self.assertNotIn("your repo is ready", joined)
            self.assertIn("exited before", joined.lower())


if __name__ == "__main__":
    unittest.main()
