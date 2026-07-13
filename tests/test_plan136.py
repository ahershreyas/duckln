"""Plan 136 — Duckln must SELF-CORRECT and reason deeply, never give up on a solvable issue:
F1 sanitize a malformed command (`/cd …`) before it runs.
F2 deterministic self-correct for 127 / "command not found" slash-builtin typos.
F3 the recovery agent is instructed to investigate OS/privileges/access before concluding.
F4 the amendment cap counts only DISTINCT blockers (a repeated identical fix halts honestly).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import duckln.repo_bringup as rb
from duckln.shell import sanitize_shell_command


class SanitizeCommand(unittest.TestCase):
    def test_strips_stray_slash_on_builtins(self):
        self.assertEqual(
            sanitize_shell_command('/cd "${HOME}/x" && sudo apt --fix-broken install'),
            'cd "${HOME}/x" && sudo apt --fix-broken install',
        )
        self.assertEqual(sanitize_shell_command("/sudo apt update"), "sudo apt update")
        self.assertEqual(sanitize_shell_command("/npm run build"), "npm run build")

    def test_keeps_genuine_absolute_path(self):
        # a real absolute-path executable must NOT be mangled
        self.assertEqual(sanitize_shell_command("/usr/bin/python app.py"), "/usr/bin/python app.py")
        self.assertEqual(sanitize_shell_command("/home/ubuntu/.venv/bin/pip install x"),
                         "/home/ubuntu/.venv/bin/pip install x")

    def test_trims_artifacts_and_leaves_clean_unchanged(self):
        self.assertEqual(sanitize_shell_command("`npm install`"), "npm install")
        self.assertEqual(sanitize_shell_command("$ npm install"), "npm install")
        self.assertEqual(sanitize_shell_command("npm run build"), "npm run build")
        self.assertEqual(sanitize_shell_command(""), "")

    def test_wrap_sanitizes_before_running(self):
        # the broadest guard: _wrap_command_for_execution_target sanitizes its command.
        with tempfile.TemporaryDirectory() as t:
            from state.store import initialize_state_store
            from state.access import write_workflow_state
            cfg = Path(t)
            initialize_state_store(cfg)
            write_workflow_state(cfg, {"active_runtime_vm_name": "duckln-vm",
                                       "active_runtime_execution_target": "vm"})
            wrapped, _ = rb._wrap_command_for_execution_target(
                config_dir=cfg, execution_target="vm",
                command='/cd "${HOME}/x" && echo hi', cwd=None, preferred_vm_name="duckln-vm",
            )
            self.assertIsNotNone(wrapped)
            self.assertNotIn("/cd ", wrapped)
            self.assertIn("cd ", wrapped)


class DeterministicSelfCorrect(unittest.TestCase):
    def test_command_not_found_typo_is_auto_fixed(self):
        from duckln.diagnostics import match_deterministic_fix, ErrorCategory
        f = match_deterministic_fix(
            stderr="-bash: /cd: No such file or directory", exit_code=127,
            command='/cd "${HOME}/x" && sudo apt --fix-broken install', execution_target="vm",
        )
        self.assertIsNotNone(f)
        self.assertEqual(f.category, ErrorCategory.COMMAND_NOT_FOUND)
        self.assertEqual(f.fix_command, 'cd "${HOME}/x" && sudo apt --fix-broken install')

    def test_well_formed_not_found_is_not_a_typo_fix(self):
        from duckln.diagnostics import match_deterministic_fix
        # a real missing binary (no stray slash) is NOT the typo-corrector's job — it may
        # match the existing "install the missing tool" rule, but must NOT be treated as a
        # malformed-command typo (which would just re-run the same broken command).
        f = match_deterministic_fix(
            stderr="bash: frobnicate: command not found", exit_code=127,
            command="frobnicate --x", execution_target="vm",
        )
        self.assertTrue(f is None or f.fix_title != "Re-run the corrected command")


class DeeperRecoveryReasoning(unittest.TestCase):
    def test_recovery_prompt_directs_environment_investigation(self):
        from duckln.recovery import _recovery_spec_prompt
        low = _recovery_spec_prompt("recovery_agent").lower()
        self.assertIn("os-release", low)          # identify the OS
        self.assertIn("sudo -n", low)             # check privileges
        self.assertIn("command -v", low)          # check access/tool presence
        self.assertIn("least-privilege", low)     # prefer the command that works
        self.assertIn("stray leading slash", low) # validate its own command
        # don't give up prematurely — block only after checking OS/privileges + an alternative
        self.assertIn("only after", low)
        self.assertIn("at least one alternative", low)

    def test_recovery_report_command_is_sanitized(self):
        from duckln.recovery import _parse_recovery_report, RecoveryDecision
        rep = _parse_recovery_report('{"decision":"fixed","cause":"x","command":"/cd /tmp && ls","reason":"y"}')
        self.assertIsNotNone(rep)
        self.assertEqual(rep.decision, RecoveryDecision.FIXED)
        self.assertEqual(rep.command, "cd /tmp && ls")  # /cd sanitized


class AmendmentCapDistinctBlockers(unittest.TestCase):
    def test_repeated_fix_signature_is_detected(self):
        with tempfile.TemporaryDirectory() as t:
            from state.store import initialize_state_store
            cfg = Path(t)
            initialize_state_store(cfg)
            sig = rb._failure_fix_signature("npm run build", "npm install")
            self.assertFalse(rb._was_fix_attempted(cfg, "repo", sig))
            rb._mark_fix_attempted(cfg, "repo", sig)
            self.assertTrue(rb._was_fix_attempted(cfg, "repo", sig))
            # a different fix for the same command is a DISTINCT signature
            sig2 = rb._failure_fix_signature("npm run build", "npm ci")
            self.assertFalse(rb._was_fix_attempted(cfg, "repo", sig2))


if __name__ == "__main__":
    unittest.main()
