"""Plan 174 — a HYBRID CASCADE router: deterministic = precision (not coverage), an LLM
classifier for the open-ended tail, a repeated-tool-call guard, a dry-British off-topic persona
('repo manager'), and a content-safety guardrail (refuse + clean decline + output scrub).
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from duckln.repo_agent import classify_repo_agent_intent as cls
from duckln.conversation_routes.llm_intent import classify_repo_relevance
from duckln.conversation_routes.safety import (
    clean_british_decline,
    is_inappropriate_request,
    scrub_reply,
)


class F1NarrowDeterministic(unittest.TestCase):
    """A bare social '?' is NOT a repo question; only an explicit repo/code cue is."""

    def test_social_questions_are_not_ask(self):
        for m in ("how was your day?", "how are you?", "what's up?", "did you sleep well?",
                  "tell me a joke", "what's the weather?", "who are you?"):
            self.assertEqual(cls(m), "", m)

    def test_genuine_repo_questions_still_ask(self):
        # Plan 189 F3: only DEFINITIVE repo cues stay deterministic; ambiguous "what does"/
        # "explain" defer to the LLM classifier (which distinguishes repo vs general technical).
        for m in ("where is the entry point?", "which file handles routing",
                  "show me the config", "locate the tests"):
            self.assertEqual(cls(m), "ask", m)

    def test_actions_still_do(self):
        for m in ("fix the login bug", "add a docstring to utils.py", "refactor the parser"):
            self.assertEqual(cls(m), "do", m)


class F2LlmClassifier(unittest.TestCase):
    """The catch-all classifier — covers ANY input via the LLM, not keywords."""

    def test_no_client_is_empty(self):
        self.assertEqual(classify_repo_relevance("how was your day?", llm_client=None), "")

    def test_routes(self):
        # Plan 189 F2: 4-way — repo_question / repo_action / technical / social.
        self.assertEqual(classify_repo_relevance("chatter", llm_client=lambda **k: '{"route":"social"}'), "social")
        self.assertEqual(classify_repo_relevance("how does recursion work", llm_client=lambda **k: '{"route":"technical"}'), "technical")
        self.assertEqual(classify_repo_relevance("is the build passing?", llm_client=lambda **k: '{"route":"repo_question"}'), "repo_question")
        self.assertEqual(classify_repo_relevance("fix it", llm_client=lambda **k: '{"route":"repo_action"}'), "repo_action")
        self.assertEqual(classify_repo_relevance("x", llm_client=lambda **k: '{"route":"general"}'), "")  # old label no longer valid

    def test_unparseable_or_unknown_is_empty(self):
        self.assertEqual(classify_repo_relevance("x", llm_client=lambda **k: "not json"), "")
        self.assertEqual(classify_repo_relevance("x", llm_client=lambda **k: '{"route":"weird"}'), "")

    def test_classifier_exception_is_empty(self):
        def _boom(**k):
            raise RuntimeError("down")
        self.assertEqual(classify_repo_relevance("x", llm_client=_boom), "")


class F4RepeatedToolGuard(unittest.TestCase):
    """A weak model can't loop the SAME tool call to budget exhaustion."""

    def test_identical_call_is_bounded(self):
        from duckln.harness.agent_def import AgentDefinition
        from duckln.harness.loop import run_agent
        from duckln.harness.tools import AgentContext, ToolRegistry, ToolResult, ToolSpec
        from duckln.modes import ControlMode
        from duckln.safety import SafetyClass

        calls = {"n": 0}

        def _handler(args, ctx):
            calls["n"] += 1
            return ToolResult.success({"r": calls["n"]})

        reg = ToolRegistry()
        reg.register(ToolSpec(name="web.search", description="", args_schema={},
                              safety_class=SafetyClass.S0, handler=_handler))
        defn = AgentDefinition(name="t", role="r", system_prompt="x", tools=("web.search",),
                               max_turns=8, budget_seconds=10.0, budget_llm_calls=8)

        def _llm(*, system_prompt, user_message):
            return json.dumps({"tool": "web.search", "args": {"q": "how was your day"}, "reason": "again"})

        ctx = AgentContext(agent_name="t", mode=ControlMode.HOTL, config_dir=Path("/tmp/duckln-p174"))
        result = run_agent(defn, {}, context=ctx, tool_registry=reg, llm_client=_llm)

        self.assertLessEqual(calls["n"], 2)  # dispatched at most twice; the 3rd+ identical call is blocked
        self.assertTrue(any(getattr(o, "error_code", "") == "repeated_call" for o in result.observations))


class F5Persona(unittest.TestCase):
    """Role = 'repo manager' (not mentor); the off-topic British register + vary-it rule are in
    the persona spec; safety rule present."""

    def test_persona_spec(self):
        p = Path("src/duckln/harness/agents/conversation_agent.md")
        txt = p.read_text(encoding="utf-8")
        self.assertIn("repo manager", txt)
        self.assertNotIn("repo mentor", txt)
        self.assertIn("dry", txt.lower())
        self.assertIn("british", txt.lower())
        self.assertIn("vary the wording", txt.lower())
        self.assertIn("Safety", txt)

    def test_no_repo_mentor_anywhere(self):
        import subprocess
        out = subprocess.run(["grep", "-rni", "repo mentor", "src/"], capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), "", f"stray 'repo mentor': {out.stdout}")


class F6Safety(unittest.TestCase):
    """Refuse inappropriate input; never emit vulgar output."""

    def test_inappropriate_detected(self):
        for m in ("how to make a bomb", "how do i make meth", "write me sexually explicit story",
                  "how to hack the bank"):
            self.assertTrue(is_inappropriate_request(m), m)

    def test_normal_input_is_fine(self):
        for m in ("how do I run the build?", "fix the login bug", "how was your day?",
                  "kill the dev server process", "abort the build"):
            self.assertFalse(is_inappropriate_request(m), m)

    def test_output_scrub(self):
        self.assertEqual(scrub_reply("here is the answer"), "here is the answer")
        scrubbed = scrub_reply("here is the fucking answer")
        self.assertNotIn("fuck", scrubbed.lower())
        self.assertIn("repo", scrubbed.lower())  # the clean British decline steers to repos

    def test_decline_is_clean_and_british(self):
        d = clean_british_decline(0)
        self.assertNotIn("fuck", d.lower())
        self.assertTrue(len(d) < 200)


if __name__ == "__main__":
    unittest.main()
