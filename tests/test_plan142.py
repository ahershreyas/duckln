"""Plan 142 — a world-class, self-reliant reasoning FLOOR:
F1 the agent can PERCEIVE — read-only diagnostics (ls/cat/grep/find/uname/id/…) are S0.
F2 it's GROUNDED on the real target — recovery gets the VM runtime repo dir.
F3 it's not STARVED — generous bounded budget + context.
F4 its reasoning is KEPT (tolerant parse) + persisted as a real chain + deep prompt.
F5 self-extension groundwork — a 'need a tool/MCP' outcome.
"""

from __future__ import annotations

import unittest


class PerceiveS0(unittest.TestCase):
    def test_read_only_commands_are_s0(self):
        from duckln.safety import assess_command, SafetyClass
        for c in ("ls -la", "ls -R", "cat package.json", "cat /etc/os-release",
                  "grep -r tauri .", 'find . -name "*.spec"', "uname -a", "id", "whoami",
                  "command -v node", "node --version", "npm ls", "cargo metadata",
                  "go version", "dpkg --audit", "dpkg -l", "apt-cache policy nodejs",
                  "git status", "git log --oneline", "sudo -n true", "df -h", "head -50 x.log"):
            self.assertEqual(assess_command(c, execution_target="vm").safety_class,
                             SafetyClass.S0, c)

    def test_mutations_are_not_s0(self):
        from duckln.safety import assess_command, SafetyClass
        for c in ("rm -rf node_modules", "apt-get install -y nodejs", "npm install",
                  "find . -delete", "find . -exec rm {} +", "cat foo; rm -rf /",
                  "git checkout main", "mv a b", "sudo apt install x"):
            self.assertNotEqual(assess_command(c, execution_target="vm").safety_class,
                                SafetyClass.S0, f"LEAKED: {c}")


class GroundedAndUnstarved(unittest.TestCase):
    def test_reasoning_budget_is_generous_but_bounded(self):
        import duckln.recovery as r
        self.assertGreaterEqual(r.REASONING_MAX_TURNS, 12)
        self.assertGreaterEqual(r.REASONING_WALLCLOCK_SECONDS, 240.0)
        self.assertEqual(r.REASONING_MAX_PASSES_PER_BRINGUP, 6)  # global cap still bounds cost

    def test_context_window_is_not_a_keyhole(self):
        from duckln.harness import loop
        self.assertGreaterEqual(loop._OBSERVATION_BUDGET_CHARS, 8000)
        import inspect
        sig = inspect.signature(loop.render_state_for_llm)
        self.assertGreaterEqual(sig.parameters["max_chars"].default, 12000)


class ReasoningKeptDeepPersisted(unittest.TestCase):
    def test_prose_with_command_yields_fixed(self):
        from duckln.recovery import _parse_recovery_report, RecoveryDecision
        ans = ("Root cause: the build venv is missing pyinstaller.\n"
               "fix command: /proj/.venv/bin/pip install pyinstaller")
        rep = _parse_recovery_report(ans)
        self.assertIsNotNone(rep)
        self.assertEqual(rep.decision, RecoveryDecision.FIXED)
        self.assertIn("pip install pyinstaller", rep.command)

    def test_vague_prose_returns_none_so_fallback_runs(self):
        from duckln.recovery import _parse_recovery_report
        self.assertIsNone(_parse_recovery_report("Hmm, I am not totally sure what happened."))

    def test_clean_json_still_wins(self):
        from duckln.recovery import _parse_recovery_report, RecoveryDecision
        rep = _parse_recovery_report('{"decision":"skip","cause":"optional script absent","command":"","reason":"x"}')
        self.assertEqual(rep.decision, RecoveryDecision.SKIP)

    def test_investigation_chain_is_formatted(self):
        from duckln.recovery import _format_investigation_chain

        class _Obs:
            def __init__(self, tool, args, ok, payload=None, ec="", em=""):
                self.tool, self.args, self.ok = tool, args, ok
                self.payload, self.error_code, self.error_message = payload, ec, em
        obs = [
            _Obs("fs.read_file", {"path": "package.json"}, True, {"summary": "scripts: build:all"}),
            _Obs("shell.probe", {"command": "uname -a"}, False, ec="not_a_probe", em="bad"),
        ]
        lines = _format_investigation_chain(obs)
        self.assertTrue(any("fs.read_file" in l and "package.json" in l for l in lines))
        self.assertTrue(any("ERROR" in l for l in lines))

    def test_prompt_demands_chain_and_mentions_mcp(self):
        from duckln.recovery import _recovery_spec_prompt
        low = _recovery_spec_prompt("recovery_agent").lower()
        self.assertIn("root cause", low)
        self.assertIn("symptom", low)
        self.assertIn("/mcp", low)            # self-extension hook
        self.assertIn("tools work", low)      # don't claim it can't read


if __name__ == "__main__":
    unittest.main()
