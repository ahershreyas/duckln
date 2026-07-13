from __future__ import annotations

import unittest
from types import SimpleNamespace

from duckln.plan_mode import PlanRecord, PlanStep
from duckln.repo_bringup import (
    RepoBringUpStep,
    _classify_required_env,
    _drop_satisfied_prereqs,
    _format_precheck_summary,
    _target_label,
    offer_env_key_setup,
)
from duckln.ui import render_plan_panel


class TestLeanPlan(unittest.TestCase):
    def test_drops_verify_and_version_managed_keeps_missing(self):
        steps = (
            RepoBringUpStep(purpose="Ensure node (>=24) is installed before setup runs", command="x"),
            RepoBringUpStep(purpose="Ensure git is installed before setup runs", command="x"),
            RepoBringUpStep(purpose="Ensure npm is installed before setup runs", command="x"),
            RepoBringUpStep(purpose="Verify README prerequisite: Node.js and npm", command="x"),
            RepoBringUpStep(purpose="Run README install command", command="npm ci"),
            RepoBringUpStep(purpose="Ensure rust is installed before setup runs", command="x"),
        )
        kept = _drop_satisfied_prereqs(steps, {"node", "npm", "git"})
        purposes = [s.purpose for s in kept]
        self.assertEqual(purposes, ["Run README install command", "Ensure rust is installed before setup runs"])

    def test_verify_kept_when_a_referenced_tool_missing(self):
        steps = (RepoBringUpStep(purpose="Verify README prerequisite: Node.js and npm", command="x"),)
        # node satisfied but npm NOT → keep (don't drop a partially-satisfied verify)
        self.assertEqual(len(_drop_satisfied_prereqs(steps, {"node"})), 1)


class TestPanelFields(unittest.TestCase):
    def _plan(self, **kw):
        base = dict(
            plan_id="p", objective="Set up demo on vm", context_summary="family=node_typescript",
            steps=(PlanStep(index=1, title="Run", description="", command="npm run dev",
                            safety_class="S0", verification="ok", rationale="", estimated_seconds=5),),
            risks=(), rollback="", estimated_seconds=5, created_at="t", status="pending",
            repo_slug="acme/demo", mode_at_creation="hootlwo",
        )
        base.update(kw)
        return PlanRecord(**base)

    def test_panel_shows_target_and_precheck(self):
        plan = self._plan(target_label="Ubuntu VM 'duckln-vm'", precheck_summary="node v18, git present; repo already cloned")
        out = render_plan_panel(plan)
        self.assertIn("duckln-vm", out)
        self.assertIn("node v18", out)
        self.assertIn("Pre-check", out)

    def test_round_trip(self):
        plan = self._plan(target_label="Local machine", precheck_summary="git present; repo not cloned yet",
                          required_env_keys=("OPENAI_API_KEY",))
        p2 = PlanRecord.from_dict(plan.to_dict())
        self.assertEqual(p2.target_label, "Local machine")
        self.assertEqual(p2.required_env_keys, ("OPENAI_API_KEY",))

    def test_helpers(self):
        self.assertEqual(_target_label("vm", "duckln-vm"), "Ubuntu VM 'duckln-vm'")
        self.assertEqual(_target_label("local", None), "Local machine")
        self.assertIn("repo already cloned", _format_precheck_summary({"git"}, {}, True))


class TestEnvKeyClassify(unittest.TestCase):
    def test_alternatives_vs_individual(self):
        alts, ind = _classify_required_env(["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DATABASE_URL"])
        self.assertEqual(set(alts), {"OPENAI_API_KEY", "ANTHROPIC_API_KEY"})
        self.assertEqual(ind, ("DATABASE_URL",))

    def test_single_ai_key_is_individual(self):
        alts, ind = _classify_required_env(["OPENAI_API_KEY"])
        self.assertEqual(alts, {})
        self.assertEqual(ind, ("OPENAI_API_KEY",))


class _Runner:
    def __init__(self):
        self.commands = []

    def run(self, command, **kw):
        self.commands.append(command)
        return SimpleNamespace(exit_code=0, stdout="", stderr="", timed_out=False)


class TestOfferEnvKeySetup(unittest.TestCase):
    def test_asks_provider_writes_chosen_key_redacted(self):
        import duckln.repo_bringup as rb

        runner = _Runner()
        orig = rb.ControlledCommandRunner
        rb.ControlledCommandRunner = lambda **kw: runner
        out: list[str] = []
        try:
            plan = PlanRecord(
                plan_id="p", objective="o", context_summary="c", steps=(), risks=(), rollback="",
                estimated_seconds=1, created_at="t", status="completed", repo_slug="acme/demo",
                mode_at_creation="hootlwo",
                required_env_keys=("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DATABASE_URL"),
            )
            from duckln.config import ConfigPaths
            import tempfile
            from pathlib import Path
            with tempfile.TemporaryDirectory() as tmp:
                paths = ConfigPaths(config_dir=Path(tmp), config_file=Path(tmp) / "c.json")
                offer_env_key_setup(
                    plan=plan, paths=paths, execution_target="local", vm_name=None,
                    display=out.append,
                    text_prompt=lambda prompt, default="": "sk-SECRETVALUE123" if "OPENAI" in prompt or "DATABASE" in prompt else "",
                    select_prompt=lambda q, opts: "OpenAI",
                )
        finally:
            rb.ControlledCommandRunner = orig

        joined = "\n".join(out) + "\n".join(runner.commands)
        # Chose OpenAI → wrote OPENAI_API_KEY + the individual DATABASE_URL; NOT Anthropic.
        self.assertTrue(any("OPENAI_API_KEY" in c for c in runner.commands))
        self.assertTrue(any("DATABASE_URL" in c for c in runner.commands))
        self.assertFalse(any("ANTHROPIC_API_KEY=" in c for c in runner.commands))
        # The secret value is never shown in displayed lines.
        self.assertNotIn("sk-SECRETVALUE123", "\n".join(out))

    def test_no_keys_no_prompt(self):
        out: list[str] = []
        called = {"n": 0}
        plan = PlanRecord(
            plan_id="p", objective="o", context_summary="c", steps=(), risks=(), rollback="",
            estimated_seconds=1, created_at="t", status="completed", repo_slug="a/b",
            mode_at_creation="hootlwo", required_env_keys=(),
        )
        from duckln.config import ConfigPaths
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            paths = ConfigPaths(config_dir=Path(tmp), config_file=Path(tmp) / "c.json")
            offer_env_key_setup(
                plan=plan, paths=paths, execution_target="local", vm_name=None,
                display=out.append,
                text_prompt=lambda *a, **k: (called.__setitem__("n", called["n"] + 1) or "x"),
                select_prompt=lambda *a, **k: "x",
            )
        self.assertEqual(called["n"], 0)


if __name__ == "__main__":
    unittest.main()
