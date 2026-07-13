"""Plan 68: Plan Mode wiring + target-agnostic execution tests.

Covers the three Plan 68 fixes:
- Fix 2: /repos setup routes through Plan Mode (no direct clone/install).
- Fix 3b: resume_with_approved_plan wraps each command for the active target.
- Fix 1 (data path): the config flip the `+` toggle relies on round-trips.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from dataclasses import replace as dc_replace

from duckln.ai_client import Provider
from duckln.config import (
    AppConfig,
    resolve_config_paths,
    load_app_config,
    save_app_config,
)
from duckln.modes import ControlMode
from duckln.plan_mode import PlanRecord, PlanStep
from state.access import read_pending_plan, write_config_snapshot
from state.repo_catalog import RepoCatalogRecord


def _paths_and_cfg(plan_mode: bool, *, td):
    paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": td.name})
    cfg = AppConfig(
        provider=Provider.OPENAI, model="gpt", api_key=None,
        mode=ControlMode.HOTL, plan_mode_enabled=plan_mode,
    )
    save_app_config(cfg, paths)
    return paths, cfg


class _FakeRunResult:
    def __init__(self, exit_code=0, stdout="ok", stderr=""):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = False


class _RecordingRunner:
    """Captures the exact command strings resume_with_approved_plan runs."""

    def __init__(self):
        self.commands: list[str] = []

    def run(self, command, *args, **kwargs):
        self.commands.append(command)
        return _FakeRunResult(exit_code=0, stdout="ok")


class OrchestrateRoutingTest(unittest.TestCase):
    def test_orchestrate_routes_to_plan_mode_when_enabled(self) -> None:
        from duckln.main import _orchestrate_repo_to_running

        with tempfile.TemporaryDirectory() as tdname:
            td = type("T", (), {"name": tdname})()
            paths, cfg = _paths_and_cfg(True, td=td)
            repo = RepoCatalogRecord(
                name="x", repo_url="owner/x", stars=0,
                description="", category="", framework="", last_updated="",
            )
            out: list[str] = []
            # No LLM provider configured → plan-mode branch returns the friendly
            # "no provider" result WITHOUT running any clone/install commands.
            result = _orchestrate_repo_to_running(
                repo=repo,
                current=cfg,
                paths=paths,
                approve=None,
                display=out.append,
                chat=None,
                runtime_provider="openai",
                execution_target="local",
                vm_name=None,
                system_probe=None,
                terminal_interface=None,
            )
            joined = "\n".join(out).lower()
            self.assertEqual(result.executed_commands, ())
            # Plan 158/160: with no usable model the plan-mode path honest-stops with a friendly
            # message — either "can't connect" (unreachable) or "reachable but too slow/weak —
            # use a stronger model" (unresponsive). It must NOT run the legacy deploy.
            self.assertTrue(
                "plan mode" in joined
                or "provider" in joined
                or "not able to connect to the model" in joined
                or "stronger model" in joined
                or "reachable but" in joined,
                f"expected a plan-mode / model honest-stop message, got: {joined[:200]}",
            )
            # The legacy deploy banner ("Deploy x on local") must NOT appear.
            self.assertNotIn("clone and install", joined)


class TargetWrappingTest(unittest.TestCase):
    def _plan(self) -> PlanRecord:
        return PlanRecord(
            plan_id="t1", objective="o", context_summary="",
            steps=(PlanStep(
                index=1, title="echo", description="", command="echo ok",
                safety_class="S0", verification="exit 0", rationale="",
                estimated_seconds=1, confidence=0.9,
            ),),
            risks=(), rollback="", estimated_seconds=1,
            created_at="2026-05-20T00:00:00Z", status="approved",
            repo_slug="owner/x", mode_at_creation="hootlwo",
        )

    def test_local_target_runs_raw_command(self) -> None:
        from duckln.repo_bringup import resume_with_approved_plan

        with tempfile.TemporaryDirectory() as tdname:
            td = type("T", (), {"name": tdname})()
            paths, _cfg = _paths_and_cfg(True, td=td)
            write_config_snapshot(paths.config_dir, {"execution_target": "local"})
            cfg = AppConfig(
                provider=Provider.OPENAI, model="gpt", api_key=None,
                mode=ControlMode.HOOTLWO, plan_mode_enabled=True,
            )
            runner = _RecordingRunner()
            resume_with_approved_plan(
                plan=self._plan(), paths=paths, current=cfg,
                approve=lambda _m: True, display=lambda _m: None, runner=runner,
            )
            self.assertEqual(runner.commands, ["echo ok"])

    def test_vm_target_wraps_with_multipass_exec(self) -> None:
        from duckln.repo_bringup import resume_with_approved_plan

        with tempfile.TemporaryDirectory() as tdname:
            td = type("T", (), {"name": tdname})()
            paths, _cfg = _paths_and_cfg(True, td=td)
            write_config_snapshot(paths.config_dir, {"execution_target": "vm"})
            cfg = AppConfig(
                provider=Provider.OPENAI, model="gpt", api_key=None,
                mode=ControlMode.HOOTLWO, plan_mode_enabled=True,
            )
            runner = _RecordingRunner()
            resume_with_approved_plan(
                plan=self._plan(), paths=paths, current=cfg,
                approve=lambda _m: True, display=lambda _m: None, runner=runner,
                vm_name="duckln-vm",
            )
            self.assertEqual(len(runner.commands), 1)
            self.assertIn("multipass exec", runner.commands[0])
            self.assertIn("duckln-vm", runner.commands[0])
            self.assertIn("echo ok", runner.commands[0])


class ToggleConfigContractTest(unittest.TestCase):
    """The `+` dropdown toggle loads config, flips plan_mode_enabled, saves.
    The UI method lives on the Textual inner app class (not unit-testable
    without a running app), so we guard the data path it depends on."""

    def test_always_on_even_when_stored_false(self) -> None:
        # Plan 188: there is no OFF — a config stored with plan_mode_enabled=False still LOADS on.
        with tempfile.TemporaryDirectory() as tdname:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": tdname})
            save_app_config(
                AppConfig(provider=Provider.OPENAI, model="gpt", api_key="k",
                          mode=ControlMode.HOTL, plan_mode_enabled=False),
                paths,
            )
            self.assertTrue(load_app_config(paths).plan_mode_enabled)

    def test_plan_note_glyph_resolves(self) -> None:
        from duckln.glyphs import PLAN_NOTE

        self.assertIsInstance(PLAN_NOTE, str)
        self.assertTrue(PLAN_NOTE)


class NoPreApprovalExecutionTest(unittest.TestCase):
    """Plan 69 Fix 3: plan generation must NOT execute any command on the target."""

    def test_generation_does_not_execute_on_remote_target(self) -> None:
        import duckln.repo_bringup as rb
        from duckln.repo_bringup import _generate_plan_for_bringup

        created_runners: list = []
        orig_runner = rb.ControlledCommandRunner

        class _SpyRunner:
            def __init__(self, *a, **k):
                created_runners.append(self)
                self.commands = []

            def run(self, command, *a, **k):
                self.commands.append(command)
                return _FakeRunResult()

        with tempfile.TemporaryDirectory() as tdname:
            td = type("T", (), {"name": tdname})()
            paths, _cfg = _paths_and_cfg(True, td=td)
            write_config_snapshot(paths.config_dir, {"execution_target": "vm"})
            repo = RepoCatalogRecord(
                name="JustHireMe", repo_url="https://github.com/o/JustHireMe", stars=0,
                description="A Node TypeScript app", category="Web", framework="React",
                last_updated="",
            )
            rb.ControlledCommandRunner = _SpyRunner
            try:
                # No LLM client → generation stops at the "no provider" branch,
                # but crucially it must not have run anything on the VM first.
                result = _generate_plan_for_bringup(
                    repo=repo,
                    current_mode=ControlMode.HOOTLWO,
                    paths=paths,
                    execution_target="vm",
                    display=lambda _m: None,
                    llm_client=None,
                    vm_name="duckln-vm",
                    pane_executor=object(),
                )
            finally:
                rb.ControlledCommandRunner = orig_runner
            # Either no runner was constructed at all, or none ran a command.
            self.assertTrue(all(not r.commands for r in created_runners))
            self.assertEqual(result.executed_commands, ())

    def test_family_seeded_from_catalog_metadata(self) -> None:
        from duckln.plan_mode import family_hint_from_metadata, gather_repo_understanding

        hint = family_hint_from_metadata(framework="React", category="Web", description="")
        self.assertEqual(hint, "node_typescript")
        u = gather_repo_understanding(
            objective="x", project_dir=None, repo_slug="o/x", config_dir=None,
            family_hint=hint,
        )
        self.assertEqual(u.repo_family, "node_typescript")
        self.assertFalse(u.needs_clarification)


class StalePlanGuardPlan70Test(unittest.TestCase):
    """Plan 70: a failed/terminal pending plan must NOT block regeneration."""

    def _repo(self):
        return RepoCatalogRecord(
            name="JustHireMe", repo_url="https://github.com/o/JustHireMe", stars=0,
            description="A Node app", category="Web", framework="React", last_updated="",
        )

    def test_failed_plan_is_cleared_and_regeneration_proceeds(self) -> None:
        from duckln.repo_bringup import _generate_plan_for_bringup
        from state.access import read_pending_plan, write_pending_plan

        with tempfile.TemporaryDirectory() as tdname:
            td = type("T", (), {"name": tdname})()
            paths, _cfg = _paths_and_cfg(True, td=td)
            repo = self._repo()
            # Seed a stale FAILED plan for this repo.
            write_pending_plan(paths.config_dir, {
                "plan_id": "old", "status": "failed", "repo_slug": repo.repo_url, "steps": [],
            })
            out: list[str] = []
            # The key behavior: it did NOT short-circuit with "already pending",
            # the stale 'old' plan is gone, and a fresh grounded plan (with a
            # clone-first step) was generated in its place.
            _generate_plan_for_bringup(
                repo=repo, current_mode=ControlMode.HOOTLWO, paths=paths,
                execution_target="local", display=out.append, llm_client=None,
            )
            joined = "\n".join(out).lower()
            self.assertNotIn("already pending", joined)
            after = read_pending_plan(paths.config_dir)
            # Either a fresh plan replaced the stale one, or it was cleared —
            # never the original 'old' failed plan.
            self.assertTrue(after is None or after.get("plan_id") != "old")
            if after is not None:
                cmds = [s.get("command") or "" for s in after.get("steps", [])]
                # The clone step is idempotency-guarded (Plan 127): `if [ -d …/.git ]; then
                # echo duckln-already-cloned; else git clone …; fi` — so check it CONTAINS the
                # clone, not that it starts with it.
                self.assertTrue(any("git clone" in c for c in cmds), cmds)

    def test_repos_regenerates_and_does_not_pin_to_stale_pending(self) -> None:
        # Plan 73 fix: selecting a repo via /repos must NOT show "already
        # pending" and pin the user to a stale draft — it clears and re-plans.
        from duckln.repo_bringup import _generate_plan_for_bringup
        from state.access import read_pending_plan, write_pending_plan

        with tempfile.TemporaryDirectory() as tdname:
            td = type("T", (), {"name": tdname})()
            paths, _cfg = _paths_and_cfg(True, td=td)
            repo = self._repo()
            write_pending_plan(paths.config_dir, {
                "plan_id": "stale-p1", "status": "pending", "repo_slug": repo.repo_url,
                "steps": [{"index": 1, "title": "x", "command": "echo ok", "safety_class": "S0"}],
            })
            out: list[str] = []
            _generate_plan_for_bringup(
                repo=repo, current_mode=ControlMode.HOOTLWO, paths=paths,
                execution_target="local", display=out.append, llm_client=None,
            )
            self.assertNotIn("already pending", "\n".join(out).lower())
            # The stale plan must be gone (cleared or replaced by a fresh one).
            after = read_pending_plan(paths.config_dir)
            self.assertTrue(after is None or after.get("plan_id") != "stale-p1")


class BrandedActivityLabelTest(unittest.TestCase):
    """Plan 69 Fix 5: /plan activity label is branded, never 'Processing …'."""

    def test_plan_edit_label_is_branded(self) -> None:
        from duckln.main import _activity_message_for_slash_command

        label = _activity_message_for_slash_command("/plan edit")
        self.assertNotIn("Processing", label)
        self.assertIn("Plan Mode", label)

    def test_plan_bare_label_is_branded(self) -> None:
        from duckln.main import _activity_message_for_slash_command

        self.assertNotIn("Processing", _activity_message_for_slash_command("/plan"))


class HeaderStatusPlan69Test(unittest.TestCase):
    """Plan 69: the header must not get stuck on a stale 'failed' plan, and the
    target label must agree with the footer (local for a stale VM runtime)."""

    def _cfg_plan_on(self, td) -> None:
        paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": td})
        save_app_config(
            AppConfig(provider=Provider.OLLAMA, model="gemma2:2b", api_key=None,
                      mode=ControlMode.HOOTLWO, plan_mode_enabled=True,
                      base_url="http://localhost:11434"),
            paths,
        )
        return paths

    def test_failed_plan_does_not_show_red_in_header(self) -> None:
        from duckln.textual_ui import _plan_mode_header_segment

        with tempfile.TemporaryDirectory() as td:
            paths = self._cfg_plan_on(td)
            from state.access import write_pending_plan

            write_pending_plan(paths.config_dir, {"plan_id": "x", "status": "failed", "steps": []})
            seg = _plan_mode_header_segment(paths.config_dir)
            self.assertIsNotNone(seg)
            _glyph, style, label = seg
            self.assertEqual(style, "green")
            self.assertEqual(label, "Plan: on")

    def test_pending_plan_shows_orange(self) -> None:
        from duckln.textual_ui import _plan_mode_header_segment

        with tempfile.TemporaryDirectory() as td:
            paths = self._cfg_plan_on(td)
            from state.access import write_pending_plan

            write_pending_plan(paths.config_dir, {"plan_id": "x", "status": "pending", "steps": []})
            _glyph, style, label = _plan_mode_header_segment(paths.config_dir)
            self.assertEqual(style, "orange1")
            self.assertEqual(label, "Plan: pending")

    def test_stale_vm_runtime_resolves_to_local_label(self) -> None:
        from duckln.main import _footer_execution_label, _terminal_connection_context
        from state.access import write_workflow_state

        with tempfile.TemporaryDirectory() as td:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": td})
            write_workflow_state(
                paths.config_dir,
                {"active_runtime_execution_target": "vm", "active_runtime_vm_name": "duckln-vm"},
            )
            self.assertEqual(_terminal_connection_context(paths.config_dir).connection_type, "local")
            self.assertEqual(_footer_execution_label(paths.config_dir), "Local")


if __name__ == "__main__":
    unittest.main()
