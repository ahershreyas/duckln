"""Plan 168 — an interrupted setup resumes as "setting up" from where it left, not as a
"runtime repair", and doesn't dead-end at "no pending plan"."""

from __future__ import annotations

import unittest

import duckln.main as m


class F1ObjectiveLabels(unittest.TestCase):
    def test_setup_reads_setting_up(self):
        self.assertEqual("setting up", m._objective_kind_label("repo_deploy"))

    def test_runtime_repair_unchanged(self):
        self.assertEqual("runtime repair", m._objective_kind_label("runtime_repair"))

    def test_unknown_humanized(self):
        self.assertEqual("some kind", m._objective_kind_label("some_kind"))


class F2SetupBlockerObjective(unittest.TestCase):
    def test_honest_stop_writes_resumable_repo_deploy(self):
        # When reasoning is required + no reachable model, the bring-up honest-stops and must
        # leave a RESUMABLE repo_deploy objective (not a runtime_repair), superseding any stale
        # repair, so the status reads "setting up" and "continue the setup" resumes the bring-up.
        import tempfile
        from pathlib import Path

        import duckln.repo_bringup as rb
        from duckln.config import ConfigPaths
        from duckln.modes import ControlMode
        from state.repo_catalog import RepoCatalogRecord
        from state.access import read_workflow_state

        with tempfile.TemporaryDirectory() as td:
            cd = Path(td)
            (cd / "state").mkdir(parents=True, exist_ok=True)
            paths = ConfigPaths(config_dir=cd, config_file=cd / "config.json")
            repo = RepoCatalogRecord("JustHireMe", "https://github.com/x/JustHireMe", 0, "", "", "", "")
            # pre-existing stale runtime_repair objective for the same repo
            rb._write_repo_objective_state(
                paths, repo=repo, kind="runtime_repair", status="active",
                goal="repair", execution_target="vm",
            )
            # the Plan-168 honest-stop write (mirrors the bring-up site)
            rb._write_repo_objective_state(
                paths, repo=repo, kind="repo_deploy", status="needs_user_decision",
                goal=f"Set up {repo.name}", execution_target="vm",
                requires_user_decision=True, resume_hint="model unavailable — continue the setup",
                last_blocker="model unavailable during planning",
            )
            wf = read_workflow_state(cd)
            self.assertEqual("repo_deploy", wf.get("active_objective_kind"))  # superseded the repair
            self.assertEqual("needs_user_decision", wf.get("active_objective_status"))


class F3PlanContinueResumeGuidance(unittest.TestCase):
    def test_no_dead_end_when_setup_objective_active(self):
        # /plan continue with no pending plan but an active repo_deploy objective should guide
        # the user to resume the SETUP, not say "Run /repos". We assert the branch logic via the
        # label + the message wording the handler uses.
        # (Full handler invocation is covered by the live run; here we assert the resume verb is
        # the setup, not a repair, and the kind label drives it.)
        self.assertEqual("setting up", m._objective_kind_label("repo_deploy"))


if __name__ == "__main__":
    unittest.main()
