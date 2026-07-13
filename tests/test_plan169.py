"""Plan 169 — resume-greeting UX: no idle "Working…" spinner, honest kind-aware wording,
no phantom "/plan approve", objective facts → LLM hint, and an honest model-reachability gate."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckln.main as m
from duckln.config import AppConfig, ConfigPaths
from duckln.modes import ControlMode
from state.access import write_config_snapshot, write_workflow_state
from state.repo_catalog import RepoCatalogRecord


class _FakeChat:
    def __init__(self):
        self.objective_calls = []

    def update_objective_status(self, rendered, **kwargs):
        self.objective_calls.append((rendered, kwargs))

    def clear_objective_status(self):
        self.objective_calls.append(("__clear__", {}))


class F1NoIdleSpinner(unittest.TestCase):
    def test_greeting_marks_objective_awaiting_user(self):
        # At the resume greeting the carried-over objective is AWAITING the user — `show_startup_
        # messages` marks it `requires_user_decision`, so the status footer renders non-spinning
        # ("Awaiting your input") instead of a fake "Working… 30s" before the user answers.
        from state.access import read_workflow_state

        with tempfile.TemporaryDirectory() as td:
            cd = Path(td)
            now = datetime.now(timezone.utc)
            write_config_snapshot(cd, {"session.started_at": (now - timedelta(minutes=5)).isoformat(timespec="seconds")})
            write_workflow_state(cd, {
                "active_objective_id": "runtime_repair:r",
                "active_objective_kind": "runtime_repair",
                "active_objective_repo_name": "JustHireMe",
                "active_objective_repo_key": "r",
                "active_objective_status": "active",
                "active_objective_requires_user_decision": False,
                "active_objective_updated_at": now.isoformat(timespec="seconds"),
            })
            paths = ConfigPaths(config_dir=cd, config_file=cd / "config.json")
            m._startup_messages_shown = False
            try:
                m.show_startup_messages(display_fn=lambda _x: None, current=_cfg(plan_mode=False), paths=paths)
            finally:
                m._startup_messages_shown = False
            wf = read_workflow_state(cd)
            self.assertTrue(wf.get("active_objective_requires_user_decision"))
            # …and an awaiting-user objective renders a NON-spinning footer.
            snap = m._AGENT_CONTEXT_SERVICE.load_workflow_state(config_dir=cd)
            derived = m._render_chat_objective_activity(snap)
            if derived is not None:
                self.assertFalse(derived[1])  # spinner off


class F2ResumeVerb(unittest.TestCase):
    def test_kind_aware_verb(self):
        self.assertEqual("setting up", m._objective_resume_verb("repo_deploy"))
        self.assertEqual("fixing", m._objective_resume_verb("runtime_repair"))
        self.assertEqual("working on", m._objective_resume_verb("whatever"))


def _repo():
    return RepoCatalogRecord("JustHireMe", "https://github.com/x/JustHireMe", 0, "", "", "", "")


def _cfg(*, plan_mode):
    return AppConfig(provider=None, model=None, api_key=None, mode=ControlMode.HOOTLWO, plan_mode_enabled=plan_mode)


class F3PlanModeHonestMessage(unittest.TestCase):
    def test_runtime_repair_offers_consent_not_plan_off(self):
        # Plan 188 F2c: no more "/plan off" dead-end — Duckln OFFERS to fix it (Yes/No). Declining
        # leaves it paused; the message references neither "/plan approve" nor "/plan off".
        with tempfile.TemporaryDirectory() as td:
            cd = Path(td)
            paths = ConfigPaths(config_dir=cd, config_file=cd / "config.json")
            out = []
            m._run_runtime_repair_workflow(
                repo=_repo(), current=_cfg(plan_mode=True), paths=paths,
                display_output=out.append, approve_prompt=lambda _m: False,  # decline the offer
                terminal_interface=None, run_summary="boom",
            )
            joined = "\n".join(out)
            self.assertNotIn("/plan approve", joined)
            self.assertNotIn("/plan off", joined)
            self.assertIn("paused", joined.lower())


class F5ReachabilityGate(unittest.TestCase):
    def test_no_provider_does_not_block(self):
        # No provider configured (e.g. tests) is NOT the F5 scenario — don't honest-stop here.
        # F5 targets a CONFIGURED-but-unreachable model (asserted via the followup test below).
        with tempfile.TemporaryDirectory() as td:
            self.assertTrue(m._repair_model_reachable(Path(td)))

    def test_resume_followup_honest_stops_when_unreachable(self):
        # The user-facing resume "yes" path: a pending runtime-repair followup with no usable
        # model → honest-stop message + no objective begun (gated before the spin). Verified at
        # the followup entry; the shared deterministic workflow is unaffected (and stays tested).
        with tempfile.TemporaryDirectory() as td:
            cd = Path(td)
            (cd / "state").mkdir(parents=True, exist_ok=True)
            paths = ConfigPaths(config_dir=cd, config_file=cd / "config.json")
            repo = _repo()
            from state.access import write_followup_state

            write_followup_state(cd, {"pending_next_action": "repair_repo_runtime",
                                      "pending_repo_key": repo.repo_url, "pending_repo_name": repo.name})
            out = []
            orig_resolve, orig_reach = m._resolve_repo_from_action_key, m._repair_model_reachable
            m._resolve_repo_from_action_key = lambda _cd, _k: repo
            m._repair_model_reachable = lambda _cd: False  # simulate the model not connected
            try:
                handled = m._handle_pending_runtime_repair_followup(
                    command="fix it", current=_cfg(plan_mode=False), paths=paths,
                    display_output=out.append, approve_prompt=lambda _m: True, terminal_interface=None,
                )
            finally:
                m._resolve_repo_from_action_key, m._repair_model_reachable = orig_resolve, orig_reach
            joined = "\n".join(out).lower()
            self.assertTrue(handled)
            self.assertIn("can't reach the model", joined)


class F4ObjectiveFactsAvailable(unittest.TestCase):
    def test_snapshot_exposes_objective_kind_and_status(self):
        # F4 threads these into the conversation LLM context; guard the source fields exist.
        from duckln.agent_context import WorkflowStateSnapshot

        fields = getattr(WorkflowStateSnapshot, "__dataclass_fields__", {})
        self.assertIn("active_objective_kind", fields)
        self.assertIn("active_objective_status", fields)


if __name__ == "__main__":
    unittest.main()
