"""Plan 67: rendering tests for plan panel, oneline, clarification, attribution."""

from __future__ import annotations

import unittest

from duckln.plan_mode import (
    ClarificationQuestion,
    PlanRecord,
    PlanStep,
    render_plan_markdown,
    parse_plan_markdown,
)
from duckln.ui import (
    render_clarification_prompt,
    render_error_attribution,
    render_plan_oneline,
    render_plan_panel,
)


def _sample_plan() -> PlanRecord:
    return PlanRecord(
        plan_id="abc12345",
        objective="Set up x",
        context_summary="family=node_typescript; os=Darwin/arm64",
        steps=(
            PlanStep(
                index=1, title="Install deps",
                description="Install JS deps from package.json.",
                command="npm install", safety_class="S1",
                verification="exit 0", rationale="package.json present",
                estimated_seconds=60, confidence=0.9,
            ),
            PlanStep(
                index=2, title="Run dev server",
                description="Launch the dev server.",
                command="npm run dev", safety_class="S2",
                verification="port up", rationale="README says so",
                estimated_seconds=30, confidence=0.8, depends_on=(1,),
            ),
        ),
        risks=("one step needs sudo",),
        rollback="rm -rf node_modules",
        estimated_seconds=90,
        created_at="2026-05-19T00:00:00Z",
        status="pending",
        repo_slug="owner/x",
        mode_at_creation="hotl",
    )


class RenderTest(unittest.TestCase):
    def test_panel_includes_all_step_titles(self) -> None:
        out = render_plan_panel(_sample_plan())
        self.assertIn("Install deps", out)
        self.assertIn("Run dev server", out)
        self.assertIn("Plan Mode", out)
        self.assertIn("Set up x", out)
        self.assertIn("/plan approve", out)

    def test_oneline_includes_status_and_steps(self) -> None:
        out = render_plan_oneline(_sample_plan())
        self.assertIn("pending", out)
        self.assertIn("2 step", out)

    def test_clarification_prompt_render(self) -> None:
        q = ClarificationQuestion(
            text="Which version of Node?",
            options=("18", "20"),
            default="20",
            applies_to_step_indices=(1,),
        )
        out = render_clarification_prompt((q,))
        self.assertIn("Which version of Node?", out)
        self.assertIn("18, 20", out)

    def test_error_attribution_render_with_fix(self) -> None:
        plan = _sample_plan()
        step = plan.steps[0]
        fix = PlanStep(
            index=0, title="Install vite", description="missing dep",
            command="npm install --save-dev vite", safety_class="S1",
            verification="exit 0", rationale="package.json references vite",
            estimated_seconds=30, confidence=0.8, origin="amendment",
        )
        out = render_error_attribution(
            failed_step=step,
            cause="vite is missing from node_modules",
            fix_step=fix,
            amendment_count=1,
        )
        self.assertIn("failed", out.lower())
        self.assertIn("Install vite", out)
        self.assertIn("/plan continue", out)

    def test_markdown_round_trip(self) -> None:
        plan = _sample_plan()
        md = render_plan_markdown(plan)
        self.assertIn("# Plan: Set up x", md)
        self.assertIn("npm install", md)
        reparsed = parse_plan_markdown(md)
        self.assertEqual(reparsed.objective, plan.objective)
        self.assertEqual(len(reparsed.steps), len(plan.steps))
        self.assertEqual(reparsed.steps[0].command, "npm install")
        self.assertEqual(reparsed.status, "edited")  # round-trip marks as edited


if __name__ == "__main__":
    unittest.main()
