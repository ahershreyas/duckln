from __future__ import annotations

import unittest

from duckln.plan_mode import PlanRecord, PlanStep
from duckln.ui import (
    build_chat_interface,
    format_thought_for_seconds,
    render_main_task_header,
    render_plan_panel,
    render_step_line,
)


def _step(i, title, cmd, *, target="vm", source="", cwd="", evidence=""):
    return PlanStep(
        index=i, title=title, description="", command=cmd, safety_class="S1",
        verification="ok", rationale="why", estimated_seconds=10, target=target,
        source=source, evidence_excerpt=evidence, cwd=cwd,
    )


class TestInlineBuild(unittest.TestCase):
    def test_inline_is_not_split_pane(self):
        chat = build_chat_interface(session_header="h", user_name="u", ui_mode="inline")
        self.assertNotEqual(type(chat).__name__, "SplitPaneChatInterface")


class TestRichPlanPanelRenders(unittest.TestCase):
    def test_panel_renders_all_plan81_step_types(self):
        # A realistic multi-step plan exercising monorepo cwd + compose + codegen + env.
        plan = PlanRecord(
            plan_id="p1", objective="Set up demo on vm", context_summary="family=node_typescript; Node requires Node >=20",
            steps=(
                _step(1, "Clone demo", "git clone --depth 1 https://github.com/acme/demo /r", target="vm", source="duckln-deterministic"),
                _step(2, "Install deps", "pnpm install", target="vm", cwd="/r"),
                _step(3, "Create the environment file", "cp .env.example .env", target="vm", source=".env.example", cwd="/r/apps/web", evidence="DATABASE_URL, OPENAI_API_KEY"),
                _step(4, "Start backing services", "docker compose up -d db", target="vm", source="docker-compose", cwd="/r"),
                _step(5, "Generate Prisma client", "npx prisma generate", target="vm", source="package.json", cwd="/r/apps/web"),
                _step(6, "Run demo", "pnpm dev", target="vm", source="repo-knowledge", cwd="/r/apps/web"),
            ),
            risks=(), rollback="rm -rf node_modules", estimated_seconds=120,
            created_at="2026-05-29T00:00:00Z", status="pending", repo_slug="acme/demo",
            mode_at_creation="hootlwo", critic_reasoning="Supervisor approved: sound path.",
        )
        out = render_plan_panel(plan)
        self.assertIsInstance(out, str)
        for needle in ("docker compose up -d db", "npx prisma generate", "pnpm dev", "cp .env.example .env"):
            self.assertIn(needle, out)
        # New evidence/where context renders without crashing.
        self.assertIn("docker-compose", out)


class TestRenderHelpers(unittest.TestCase):
    def test_helpers(self):
        self.assertEqual(format_thought_for_seconds(28), "Thought for 28s")
        self.assertIn("Main task", render_main_task_header("X"))
        self.assertIn("├─", render_step_line("sub", indent=1))


if __name__ == "__main__":
    unittest.main()
