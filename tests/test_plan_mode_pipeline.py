"""Plan 67: pipeline stage tests — propose, critique, classify, clarify."""

from __future__ import annotations

import json
import unittest

from duckln.plan_mode import (
    LOW_CONFIDENCE_THRESHOLD,
    MAX_CLARIFICATION_QUESTIONS,
    PLAN_STATUS_FAILED,
    PLAN_STATUS_PENDING,
    PlanRecord,
    RepoUnderstanding,
    _classify_and_verify,
    _critique_and_order,
    _collect_clarifications,
    _propose_candidates,
    generate_plan,
    parse_plan_json,
    validate_plan_dict,
)


def _understanding(**overrides) -> RepoUnderstanding:
    base = dict(
        repo_slug="owner/x",
        repo_path="/tmp/x",
        objective="Set up x",
        os_name="Darwin",
        arch="arm64",
        detected_runtimes=("node",),
        detected_files=("package.json", "README.md"),
        repo_family="node_typescript",
        readme_excerpt="npm install then npm run dev",
        config_excerpts={},
        recent_failures=(),
        recent_skills=(),
        execution_target="local",
        control_mode="hotl",
        needs_clarification=False,
        clarification_seed=None,
    )
    base.update(overrides)
    return RepoUnderstanding(**base)


class ScriptedLLM:
    """Fake LLMClient that returns a queue of responses."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def __call__(self, *, system_prompt: str, user_message: str) -> str:
        self.calls.append((system_prompt, user_message))
        if not self._responses:
            raise RuntimeError("no more scripted responses")
        return self._responses.pop(0)


class ParsePlanJsonTest(unittest.TestCase):
    def test_strips_json_fence(self) -> None:
        raw = "Here you go:\n```json\n[{\"title\":\"a\"}]\n```"
        self.assertEqual(parse_plan_json(raw), [{"title": "a"}])

    def test_handles_trailing_prose(self) -> None:
        raw = "{\"a\":1}\n\nLet me know!"
        self.assertEqual(parse_plan_json(raw), {"a": 1})

    def test_raises_on_no_json(self) -> None:
        with self.assertRaises(ValueError):
            parse_plan_json("not json at all")


class ProposeCandidatesTest(unittest.TestCase):
    def test_parses_simple_array(self) -> None:
        llm = ScriptedLLM([
            json.dumps([
                {"title": "Install deps", "command": "npm install", "confidence": 0.9, "estimated_seconds": 60},
                {"title": "Run dev", "command": "npm run dev", "confidence": 0.8, "estimated_seconds": 10},
            ]),
        ])
        candidates = _propose_candidates(understanding=_understanding(), llm_client=llm)
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0].command, "npm install")

    def test_drops_blocked_command(self) -> None:
        llm = ScriptedLLM([
            json.dumps([
                {"title": "Wipe", "command": "rm -rf /", "confidence": 0.99, "estimated_seconds": 1},
                {"title": "Good", "command": "npm install", "confidence": 0.9, "estimated_seconds": 60},
            ]),
        ])
        candidates = _propose_candidates(understanding=_understanding(), llm_client=llm)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].command, "npm install")


class CritiqueAndOrderTest(unittest.TestCase):
    def test_renumbers_and_carries_confidence(self) -> None:
        llm = ScriptedLLM([
            json.dumps({
                "ordered_steps": [
                    {"index": 1, "title": "Install", "description": "d1", "command": "npm install",
                     "rationale": "r1", "verification": "exit 0", "depends_on": [],
                     "confidence": 0.9, "estimated_seconds": 60},
                    {"index": 2, "title": "Run", "description": "d2", "command": "npm run dev",
                     "rationale": "r2", "verification": "port up", "depends_on": [1],
                     "confidence": 0.7, "estimated_seconds": 10},
                ],
                "dropped": [{"title": "pip install", "reason": "wrong family"}],
                "reasoning": "Standard Node pipeline.",
            }),
        ])
        from duckln.plan_mode import _CandidateStep
        candidates = (
            _CandidateStep(title="Install", command="npm install", confidence=0.9, estimated_seconds=60),
            _CandidateStep(title="Run", command="npm run dev", confidence=0.7, estimated_seconds=10),
        )
        ordered, dropped, reasoning = _critique_and_order(
            candidates=candidates,
            understanding=_understanding(),
            llm_client=llm,
        )
        self.assertEqual(len(ordered), 2)
        self.assertEqual(ordered[0].index, 1)
        self.assertEqual(ordered[1].depends_on, (1,))
        self.assertTrue(reasoning)
        self.assertEqual(len(dropped), 1)


class ClassifyAndVerifyTest(unittest.TestCase):
    def test_safety_classes_assigned(self) -> None:
        from duckln.plan_mode import PlanStep

        steps = (
            PlanStep(index=1, title="install", description="", command="npm install",
                     safety_class="S0", verification=None, rationale="",
                     estimated_seconds=60, confidence=0.9),
        )
        classified, blocked = _classify_and_verify(steps)
        self.assertEqual(len(classified), 1)
        self.assertIn(classified[0].safety_class, ("S0", "S1", "S2", "S3"))
        self.assertIsNotNone(classified[0].verification)
        self.assertEqual(blocked, ())


class ClarificationTest(unittest.TestCase):
    def test_no_questions_when_confidence_high(self) -> None:
        from duckln.plan_mode import PlanStep

        steps = (
            PlanStep(index=1, title="install", description="", command="npm install",
                     safety_class="S1", verification="exit 0", rationale="",
                     estimated_seconds=60, confidence=0.95),
        )
        llm = ScriptedLLM([])  # should NOT be called
        questions = _collect_clarifications(
            steps=steps, understanding=_understanding(), llm_client=llm,
        )
        self.assertEqual(questions, ())
        self.assertEqual(llm.calls, [])

    def test_questions_capped_at_three(self) -> None:
        from duckln.plan_mode import PlanStep

        steps = tuple(
            PlanStep(index=i, title=f"s{i}", description="", command=None,
                     safety_class="S0", verification=None, rationale="",
                     estimated_seconds=1, confidence=0.3)
            for i in range(1, 6)
        )
        llm = ScriptedLLM([
            json.dumps({"questions": [
                {"text": f"q{i}", "options": ["a", "b"], "default": None, "applies_to_step_indices": [i]}
                for i in range(1, 6)
            ]}),
        ])
        questions = _collect_clarifications(
            steps=steps, understanding=_understanding(), llm_client=llm,
        )
        self.assertLessEqual(len(questions), MAX_CLARIFICATION_QUESTIONS)


class GeneratePlanEndToEndTest(unittest.TestCase):
    def test_failure_at_propose_stage_returns_failed_status(self) -> None:
        llm = ScriptedLLM(["not json at all, just prose"])
        plan = generate_plan(
            objective="Set up x",
            understanding=_understanding(),
            llm_client=llm,
            mode="hotl",
        )
        self.assertEqual(plan.status, PLAN_STATUS_FAILED)
        self.assertTrue(plan.risks)

    def test_happy_path_returns_pending_plan(self) -> None:
        llm = ScriptedLLM([
            # Stage 2
            json.dumps([
                {"title": "Install deps", "command": "npm install", "confidence": 0.9, "estimated_seconds": 60},
            ]),
            # Stage 3
            json.dumps({
                "ordered_steps": [
                    {"index": 1, "title": "Install deps", "description": "Install package deps.",
                     "command": "npm install", "rationale": "package.json present",
                     "verification": "exit 0", "depends_on": [], "confidence": 0.9,
                     "estimated_seconds": 60}
                ],
                "dropped": [],
                "reasoning": "Trivial."
            }),
            # Stage 5 (high confidence → would NOT be called; if called, returns empty)
        ])
        plan = generate_plan(
            objective="Set up x",
            understanding=_understanding(),
            llm_client=llm,
            mode="hotl",
        )
        self.assertEqual(plan.status, PLAN_STATUS_PENDING)
        self.assertEqual(len(plan.steps), 1)
        self.assertEqual(plan.steps[0].command, "npm install")
        self.assertEqual(plan.mode_at_creation, "hotl")
        issues = validate_plan_dict(plan.to_dict())
        self.assertEqual(issues, [], f"valid plan should pass validation, got: {issues}")

    def test_critique_failure_falls_back_to_candidates(self) -> None:
        # Plan 70: propose succeeds, critique raises (no second scripted
        # response) → plan is still pending with candidate-derived numbered steps.
        llm = ScriptedLLM([
            json.dumps([
                {"title": "Install", "command": "npm install", "confidence": 0.9, "estimated_seconds": 60},
                {"title": "Build", "command": "npm run build", "confidence": 0.8, "estimated_seconds": 30},
            ]),
        ])
        plan = generate_plan(
            objective="Set up x", understanding=_understanding(), llm_client=llm, mode="hotl",
        )
        self.assertEqual(plan.status, PLAN_STATUS_PENDING)
        self.assertEqual([s.index for s in plan.steps], [1, 2])
        self.assertEqual(plan.steps[0].command, "npm install")
        self.assertIn("Critique stage unavailable", plan.critic_reasoning)

    def test_propose_failure_still_yields_failed(self) -> None:
        # Plan 70: only a propose-stage failure (genuine LLM/transport error)
        # marks the plan failed.
        llm = ScriptedLLM([])  # first call raises "no more scripted responses"
        plan = generate_plan(
            objective="Set up x", understanding=_understanding(), llm_client=llm, mode="hotl",
        )
        self.assertEqual(plan.status, PLAN_STATUS_FAILED)

    def test_steps_numbered_sequentially_even_with_messy_indices(self) -> None:
        # Plan 69 Fix 4: regardless of the indices the LLM emits, the sealed
        # plan must number steps 1..N densely and the panel must render them so.
        llm = ScriptedLLM([
            json.dumps([
                {"title": "A", "command": "npm install", "confidence": 0.9, "estimated_seconds": 5},
                {"title": "B", "command": "npm run build", "confidence": 0.9, "estimated_seconds": 5},
                {"title": "C", "command": "npm run start", "confidence": 0.9, "estimated_seconds": 5},
            ]),
            json.dumps({
                "ordered_steps": [
                    {"index": 10, "title": "A", "description": "d", "command": "npm install",
                     "rationale": "r", "verification": "exit 0", "depends_on": [], "confidence": 0.9, "estimated_seconds": 5},
                    {"index": 5, "title": "B", "description": "d", "command": "npm run build",
                     "rationale": "r", "verification": "exit 0", "depends_on": [10], "confidence": 0.9, "estimated_seconds": 5},
                    {"index": 7, "title": "C", "description": "d", "command": "npm run start",
                     "rationale": "r", "verification": "exit 0", "depends_on": [5], "confidence": 0.9, "estimated_seconds": 5},
                ],
                "dropped": [], "reasoning": "ok",
            }),
        ])
        plan = generate_plan(
            objective="Set up x", understanding=_understanding(), llm_client=llm, mode="hotl",
        )
        self.assertEqual([s.index for s in plan.steps], [1, 2, 3])
        from duckln.ui import render_plan_panel
        panel = render_plan_panel(plan)
        self.assertIn("Step 1", panel)
        self.assertIn("Step 2", panel)
        self.assertIn("Step 3", panel)
        # Step 1 must appear before Step 2 in the rendered text.
        self.assertLess(panel.index("Step 1"), panel.index("Step 2"))


if __name__ == "__main__":
    unittest.main()
