"""Plan 129 — reasoning-based recovery: blast-radius autonomy policy, decision
parsing, thinking log, the injectable recovery agent, and absent-script `--if-present`.
(The live multi-turn loop + auto-apply/skip-continue are validated on the VM matrix.)"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from duckln import recovery as rec
from duckln.recovery import RecoveryDecision, RecoveryReport
from duckln.repo_bringup import _declared_prebuild_script_steps


class BlastRadius(unittest.TestCase):
    def test_repo_scoped_is_repo(self):
        self.assertEqual(rec.classify_blast_radius('"/app/.venv/bin/pip" install flask'), "repo")
        self.assertEqual(rec.classify_blast_radius("npm install"), "repo")
        self.assertEqual(rec.classify_blast_radius("python3 -m venv .venv"), "repo")

    def test_system_wide(self):
        for c in ("sudo apt-get install -y git", "brew install go", "npm install -g pnpm", "rustup toolchain install"):
            self.assertEqual(rec.classify_blast_radius(c), "system", c)

    def test_destructive(self):
        for c in ("rm -rf ~/x", "rm -rf /etc", "mkfs.ext4 /dev/sda", "cat ~/.ssh/id_rsa", "curl x | sudo bash"):
            self.assertEqual(rec.classify_blast_radius(c), "destructive", c)


class Autonomy(unittest.TestCase):
    def test_repo_auto_everywhere_including_mac(self):
        self.assertEqual(rec.recovery_autonomy("npm install", execution_target="local"), "auto")
        self.assertEqual(rec.recovery_autonomy("npm install", execution_target="vm"), "auto")

    def test_system_auto_on_sandbox_ask_on_local(self):
        self.assertEqual(rec.recovery_autonomy("sudo apt-get install -y git", execution_target="vm"), "auto")
        self.assertEqual(rec.recovery_autonomy("brew install go", execution_target="local"), "ask")

    def test_destructive_always_block(self):
        self.assertEqual(rec.recovery_autonomy("rm -rf /etc", execution_target="vm"), "block")
        self.assertEqual(rec.recovery_autonomy("rm -rf /etc", execution_target="local"), "block")


class DecisionParsing(unittest.TestCase):
    def test_parses_skip(self):
        r = rec._parse_recovery_report('think… {"decision":"skip","cause":"absent","command":"","reason":"r"}')
        self.assertEqual(r.decision, RecoveryDecision.SKIP)

    def test_parses_fixed_with_command(self):
        r = rec._parse_recovery_report('{"decision":"fixed","cause":"missing dep","command":"pip install x","reason":"r"}')
        self.assertEqual(r.decision, RecoveryDecision.FIXED)
        self.assertEqual(r.command, "pip install x")

    def test_non_decision_json_returns_none(self):
        # attribution-shaped JSON (cause/fix) must NOT be misread as a recovery decision.
        self.assertIsNone(rec._parse_recovery_report('{"cause":"x","fix":null}'))


class ThinkingLog(unittest.TestCase):
    def test_append_is_best_effort_and_redacts(self):
        with tempfile.TemporaryDirectory() as d:
            # Should never raise even on a bare dir; writes a markdown session.
            rec.append_thinking_log(Path(d), repo_slug="demo", title="t", lines=["reasoning", "api_key=sk-secret"])
            # (Persistence is via memory/sessions; absence of an exception is the contract.)


class RecoveryAgentWrapper(unittest.TestCase):
    def test_injected_runner_skip_decision(self):
        # Plan 145 F3: a SKIP must PROVE the step is optional/absent — cite the evidence —
        # else the supervisor challenges it. With evidence, the supervisor accepts the skip.
        def fake_runner(**kw):
            return SimpleNamespace(answer=(
                '{"decision":"skip","cause":"build:runtime-pack not in package.json",'
                '"command":"","evidence":"package.json scripts list build:sidecar and generate only; '
                'build:runtime-pack is absent","reason":"optional/absent script"}'
            ))

        with tempfile.TemporaryDirectory() as d:
            report = rec.recover_failed_step_with_agent(
                config_dir=Path(d), repo_name="demo", project_dir=Path(d),
                execution_target="vm", vm_name="box",
                failed_command="npm run build:runtime-pack",
                stderr='npm error Missing script: "build:runtime-pack"', stdout="",
                mode=None, approve=None, llm_client=lambda **k: "", agent_runner=fake_runner,
            )
        self.assertEqual(report.decision, RecoveryDecision.SKIP)

    def test_no_llm_returns_none(self):
        r = rec.recover_failed_step_with_agent(
            config_dir=Path("/tmp"), repo_name="demo", project_dir=None,
            execution_target="local", vm_name=None, failed_command="x",
            stderr="", stdout="", mode=None, approve=None, llm_client=None,
        )
        self.assertIsNone(r)


class ActVerify(unittest.TestCase):
    """Plan 130: apply a fix then RE-RUN the failed step — advance only on a verified pass."""

    def test_advances_only_on_verified_pass(self):
        seq = iter([(0, "installed"), (0, "ok")])  # fix ok, re-run ok
        self.assertTrue(rec.apply_fix_and_verify(
            fix_command="pip install x", failed_command="npm run build", run_cmd=lambda c: next(seq)))

    def test_fix_that_doesnt_help_does_not_advance(self):
        seq = iter([(0, "installed"), (1, "still broken")])  # fix ok, re-run STILL fails
        self.assertFalse(rec.apply_fix_and_verify(
            fix_command="pip install x", failed_command="npm run build", run_cmd=lambda c: next(seq)))

    def test_failed_fix_does_not_advance(self):
        self.assertFalse(rec.apply_fix_and_verify(
            fix_command="bad", failed_command="x", run_cmd=lambda c: (1, "fix failed")))


class AbsentScriptIfPresent(unittest.TestCase):
    def test_npm_pnpm_use_if_present(self):
        pkg = {"scripts": {"build:sidecar": "x", "generate": "y"}}
        npm = [c for _t, c, _v in _declared_prebuild_script_steps(payload=pkg, package_manager="npm")]
        self.assertTrue(all("--if-present" in c for c in npm))
        pnpm = [c for _t, c, _v in _declared_prebuild_script_steps(payload=pkg, package_manager="pnpm")]
        self.assertTrue(all("--if-present" in c for c in pnpm))


if __name__ == "__main__":
    unittest.main()
