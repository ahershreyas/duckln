"""Plan 145 — evidence-backed, supervisor-challenged reasoning + deep logging.
F2 a RecoveryReport carries cited EVIDENCE (parsed from JSON or prose).
F3 a skeptical SUPERVISOR challenges a conclusion before it's trusted; an unproven
   BLOCK/SKIP is downgraded to EXHAUSTED, while a FIXED may still prove itself (caller verify).
F5 the recovery system prompt requires cited evidence; episodes are written (smoke).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import duckln.recovery as rec
from duckln.recovery import RecoveryDecision, RecoveryReport, supervise_recovery_conclusion


class EvidenceField(unittest.TestCase):
    def test_report_carries_evidence_and_has_evidence(self):
        r = RecoveryReport(decision=RecoveryDecision.FIXED, cause="x", command="pip install y", evidence="read req.txt: y pinned")
        self.assertTrue(r.has_evidence)
        self.assertFalse(RecoveryReport(decision=RecoveryDecision.FIXED, cause="x", command="z").has_evidence)

    def test_json_payload_parses_evidence(self):
        rep = rec._parse_recovery_report(
            '{"decision":"fixed","cause":"missing dep","command":"pip install y",'
            '"evidence":"requirements.txt lists y; import failed","reason":"r"}'
        )
        self.assertIsNotNone(rep)
        self.assertIn("requirements.txt", rep.evidence)

    def test_prose_evidence_extracted(self):
        rep = rec._report_from_prose(
            "Cause: build script needs PyInstaller\nEvidence: scripts/build.mjs runs python -m PyInstaller\nfix command: pip install pyinstaller"
        )
        self.assertIsNotNone(rep)
        self.assertEqual(rep.decision, RecoveryDecision.FIXED)
        self.assertIn("PyInstaller", rep.evidence)

    def test_recovery_system_prompt_demands_evidence(self):
        self.assertIn("evidence", rec._recovery_spec_prompt("recovery_agent").lower())
        self.assertIn("evidence", rec._recovery_spec_prompt("recovery_supervisor").lower())


class SupervisorGate(unittest.TestCase):
    def test_empirically_verified_short_circuits(self):
        # proof beats debate: a verified fix is accepted with no review at all.
        r = RecoveryReport(decision=RecoveryDecision.FIXED, cause="x", command="c")  # no evidence
        accepted, crit = supervise_recovery_conclusion(r, empirically_verified=True)
        self.assertTrue(accepted)
        self.assertEqual(crit, "")

    def test_block_without_evidence_is_challenged(self):
        r = RecoveryReport(decision=RecoveryDecision.BLOCK, cause="needs a key")
        accepted, crit = supervise_recovery_conclusion(r)
        self.assertFalse(accepted)
        self.assertTrue(crit)

    def test_block_with_evidence_accepted(self):
        r = RecoveryReport(decision=RecoveryDecision.BLOCK, cause="needs a key",
                           evidence="error: 401 Unauthorized — OPENAI_API_KEY required")
        self.assertTrue(supervise_recovery_conclusion(r)[0])

    def test_skip_without_evidence_is_challenged(self):
        r = RecoveryReport(decision=RecoveryDecision.SKIP, cause="optional")
        self.assertFalse(supervise_recovery_conclusion(r)[0])

    def test_fixed_without_evidence_is_challenged(self):
        r = RecoveryReport(decision=RecoveryDecision.FIXED, cause="x", command="pip install y")
        self.assertFalse(supervise_recovery_conclusion(r)[0])

    def test_fixed_without_command_is_challenged(self):
        r = RecoveryReport(decision=RecoveryDecision.FIXED, cause="x", command="")
        self.assertFalse(supervise_recovery_conclusion(r)[0])

    def test_none_report_not_accepted(self):
        self.assertFalse(supervise_recovery_conclusion(None)[0])

    def test_injected_review_is_used(self):
        r = RecoveryReport(decision=RecoveryDecision.BLOCK, cause="x")  # no evidence
        # an injected (model) reviewer overrides the deterministic gate.
        self.assertTrue(supervise_recovery_conclusion(r, review=lambda _r: (True, ""))[0])
        self.assertFalse(supervise_recovery_conclusion(r, review=lambda _r: (False, "prove it"))[0])


class SupervisorWiredIntoRecovery(unittest.TestCase):
    def _run(self, answer: str):
        def fake_runner(**kw):
            return SimpleNamespace(answer=answer)

        with tempfile.TemporaryDirectory() as d:
            return rec.recover_failed_step_with_agent(
                config_dir=Path(d), repo_name="demo", project_dir=Path(d),
                execution_target="vm", vm_name="box", failed_command="npm run build",
                stderr="boom", stdout="", mode=None, approve=None,
                llm_client=lambda **k: "", agent_runner=fake_runner,
            )

    def test_unproven_block_downgraded_to_exhausted(self):
        # a BLOCK with no evidence can't prove itself → supervisor downgrades to EXHAUSTED.
        rep = self._run('{"decision":"block","cause":"needs a paid key","command":"","reason":"r"}')
        self.assertEqual(rep.decision, RecoveryDecision.EXHAUSTED)

    def test_proven_block_stands(self):
        rep = self._run('{"decision":"block","cause":"needs a paid key","command":"",'
                        '"evidence":"401 Unauthorized — API key required","reason":"r"}')
        self.assertEqual(rep.decision, RecoveryDecision.BLOCK)

    def test_fixed_without_evidence_still_returned_for_caller_verify(self):
        # proof beats debate: a FIXED proposal is NOT downgraded — the caller verifies it.
        rep = self._run('{"decision":"fixed","cause":"missing dep","command":"pip install y","reason":"r"}')
        self.assertEqual(rep.decision, RecoveryDecision.FIXED)
        self.assertEqual(rep.command, "pip install y")


class DeterministicHintNotDecider(unittest.TestCase):
    """F1: the deterministic match is passed to the agent as a HINT (not a decider)."""

    def test_hint_reaches_the_agent_question(self):
        captured = {}

        def fake_runner(**kw):
            captured["q"] = kw.get("question", "")
            return SimpleNamespace(answer='{"decision":"fixed","cause":"c","command":"pip install y","evidence":"req lists y"}')

        with tempfile.TemporaryDirectory() as d:
            rec.recover_failed_step_with_agent(
                config_dir=Path(d), repo_name="demo", project_dir=Path(d),
                execution_target="vm", vm_name="box", failed_command="x",
                stderr="boom", stdout="", mode=None, approve=None,
                llm_client=lambda **k: "", agent_runner=fake_runner,
                hint="HINT: candidate fix=`pip install y`",
            )
        self.assertIn("HINT", captured["q"])  # the hint is offered to the model
        self.assertIn("candidate fix", captured["q"])


class NaturalLanguageIntent(unittest.TestCase):
    """F7: a plain message (no slash) classifies to the right intent."""

    def test_intents(self):
        from duckln.repo_agent import classify_repo_agent_intent as c
        self.assertEqual(c("show your reasoning"), "reasoning")
        self.assertEqual(c("why did you do that"), "reasoning")
        self.assertEqual(c("that's wrong, recheck"), "recheck")
        self.assertEqual(c("are you sure?"), "recheck")
        self.assertEqual(c("add type hints to utils.py"), "do")
        self.assertEqual(c("where is the entry point?"), "ask")
        self.assertEqual(c("/ask something"), "")  # explicit slash is left alone
        self.assertEqual(c("hello there"), "")      # small talk → normal chat

    def test_reasoning_beats_question_cue(self):
        # "show me your reasoning" must be 'reasoning', not 'ask' (it contains "show me").
        from duckln.repo_agent import classify_repo_agent_intent as c
        self.assertEqual(c("show me your reasoning please"), "reasoning")


if __name__ == "__main__":
    unittest.main()
