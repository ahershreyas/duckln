"""Plan 134 — three live-VM fixes:
F1 header target chip = authoritative execution target (VM bring-up shows "local", not a
   stale/sticky "local-vm" from scraped terminal output).
F2 a RECOGNIZED repo-scoped fix auto-applies + verifies + resumes (HOOTLWO) instead of
   pausing for `/plan approve`; HITL/HOTL + system/destructive fixes still propose.
F3 PLAN IT RIGHT: the target-side Python-backend setup installs PyInstaller up front when
   a PyInstaller-shaped *.spec is present, so a `build:sidecar`-style prebuild never fails
   with "No module named PyInstaller".
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import duckln.repo_bringup as rb
from duckln.modes import ControlMode


class _Step:
    def __init__(self, command="npm run build:sidecar", title="build", cwd=""):
        self.command = command
        self.title = title
        self.cwd = cwd


class _Plan:
    repo_slug = "owner/demo"


class _Understanding:
    execution_target = "vm"
    repo_family = "node_typescript"
    os_name = "ubuntu"


class _Repo:
    name = "demo"
    repo_url = "https://github.com/owner/demo"


def _paths(t):
    from duckln.config import resolve_config_paths
    from state.store import initialize_state_store

    paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": t})
    initialize_state_store(paths.config_dir)
    return paths


# --- F1: authoritative target label -----------------------------------------
class HeaderTargetLabel(unittest.TestCase):
    def test_vm_target_reasserts_as_local_not_local_vm(self):
        from duckln.main import _reassert_cloud_terminal_target_from_state
        from state.access import write_workflow_state

        with tempfile.TemporaryDirectory() as t:
            paths = _paths(t)
            write_workflow_state(paths.config_dir, {
                "active_runtime_execution_target": "vm",
                "active_runtime_vm_name": "duckln-vm",
            })
            calls: list[dict] = []

            class Term:
                def update_connection(self, **kw):
                    calls.append(kw)

            _reassert_cloud_terminal_target_from_state(paths=paths, terminal_interface=Term())
            self.assertEqual(len(calls), 1)
            # A VM bring-up runs via a LOCAL terminal pane + multipass exec → chip "local",
            # NOT the generic sticky "local-vm" the old stdout inference produced.
            self.assertEqual(calls[0]["connection_type"], "local")

    def test_terminal_stdout_does_not_drive_the_chip(self):
        # The inference helper still classifies a VM prompt (used only for the
        # "already in a VM shell → skip multipass shell" optimization), but it must no
        # longer be the source of the header chip.
        from duckln.textual_ui import infer_terminal_connection_from_output
        self.assertEqual("vm", infer_terminal_connection_from_output("ubuntu@duckln-vm:~$ "))
        self.assertIsNone(infer_terminal_connection_from_output("(base) shreyas@Air % "))


# --- F2: recognized fix auto-applies, not pause ------------------------------
class AutoApplyRecoveryFix(unittest.TestCase):
    def _common(self, t):
        return dict(
            failed_step=_Step(), plan=_Plan(), paths=_paths(t),
            understanding=_Understanding(), fake_repo=_Repo(),
            exec_target="vm", vm="duckln-vm", stderr="No module named PyInstaller",
            display=lambda _m: None,
        )

    def test_repo_scoped_fix_autoapplies_and_resumes_in_hootlwo(self):
        with tempfile.TemporaryDirectory() as t:
            with patch("duckln.recovery.apply_fix_and_verify", return_value=True):
                out = rb._auto_apply_recovery_fix(
                    fix_command="/proj/backend/.venv/bin/pip install pyinstaller",
                    fix_cause="PyInstaller missing", mode=ControlMode.HOOTLWO,
                    **self._common(t),
                )
            self.assertIs(out, rb._RECOVERY_CONTINUE)

    def test_unverified_fix_falls_back_to_proposal(self):
        with tempfile.TemporaryDirectory() as t:
            with patch("duckln.recovery.apply_fix_and_verify", return_value=False):
                out = rb._auto_apply_recovery_fix(
                    fix_command="/proj/backend/.venv/bin/pip install pyinstaller",
                    fix_cause="x", mode=ControlMode.HOOTLWO, **self._common(t),
                )
            self.assertIsNone(out)

    def test_non_hootlwo_mode_never_autoapplies(self):
        with tempfile.TemporaryDirectory() as t:
            # Even with a verifying fix, HITL/HOTL keep the user in the loop → propose.
            with patch("duckln.recovery.apply_fix_and_verify", return_value=True) as m:
                for mode in (ControlMode.HITL, ControlMode.HOTL):
                    out = rb._auto_apply_recovery_fix(
                        fix_command="/proj/.venv/bin/pip install pyinstaller",
                        fix_cause="x", mode=mode, **self._common(t),
                    )
                    self.assertIsNone(out)
                m.assert_not_called()  # gated out before any apply

    def test_system_fix_on_local_asks_not_autoapply(self):
        with tempfile.TemporaryDirectory() as t:
            common = self._common(t)
            common["exec_target"] = "local"
            with patch("duckln.recovery.apply_fix_and_verify", return_value=True) as m:
                out = rb._auto_apply_recovery_fix(
                    fix_command="sudo apt-get install -y libwebkit2gtk-4.0-dev",
                    fix_cause="missing system lib", mode=ControlMode.HOOTLWO, **common,
                )
            self.assertIsNone(out)
            m.assert_not_called()

    def test_empty_fix_command_is_noop(self):
        with tempfile.TemporaryDirectory() as t:
            out = rb._auto_apply_recovery_fix(
                fix_command="", fix_cause="x", mode=ControlMode.HOOTLWO, **self._common(t),
            )
            self.assertIsNone(out)


# --- F3: plan it right — install PyInstaller before the prebuild -------------
class PlanTimeBuildToolScan(unittest.TestCase):
    def test_secondary_setup_installs_pyinstaller_from_spec(self):
        cmd = rb._SECONDARY_PY_SETUP_CMD
        self.assertIn("*.spec", cmd)
        self.assertIn("pip install pyinstaller", cmd)
        self.assertIn("import PyInstaller", cmd)          # idempotent import-check first
        self.assertIn("Analysis", cmd)                    # PyInstaller-spec signal
        self.assertTrue(cmd.rstrip().endswith("done"))    # still a valid one-liner shape

    def test_secondary_setup_step_emitted_for_node_prebuild(self):
        steps = rb._secondary_python_setup_steps(("package.json",), has_prebuild=True)
        self.assertEqual(len(steps), 1)
        self.assertIn("pip install pyinstaller", steps[0][1])
        # no prebuild / no package.json → no step (no false positives)
        self.assertEqual(rb._secondary_python_setup_steps(("package.json",), has_prebuild=False), ())
        self.assertEqual(rb._secondary_python_setup_steps(("requirements.txt",), has_prebuild=True), ())


if __name__ == "__main__":
    unittest.main()
