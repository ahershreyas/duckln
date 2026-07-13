"""Tests for route-scoped prompt context assembly."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from duckln.context_assembler import assemble_supervisor_prompt_context
from state.access import initialize_managed_memory_state, write_config_snapshot


def _fake_context():
    return SimpleNamespace(
        followup_state=SimpleNamespace(last_route_family="repo_recommendation_single"),
        durable_repo_memory=SimpleNamespace(
            repo_name="whisper",
            tracked_status="ready",
            install_location="/tmp/whisper",
            has_repo_knowledge=True,
        ),
        durable_recommendation=SimpleNamespace(
            primary_repo="private-gpt",
            secondary_repo="text-generation-webui",
            alternatives=("text-generation-webui", "fastchat"),
        ),
        pending_offer=SimpleNamespace(kind="recommend_best_fit"),
        execution_target="local",
        active_vm_name=None,
    )


class ContextAssemblerTest(unittest.TestCase):
    def test_repair_context_omits_recommendation_shortlist_details(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            initialize_managed_memory_state(config_dir)
            write_config_snapshot(config_dir, {"onboarding_complete": "true"})

            bundle = assemble_supervisor_prompt_context(
                config_dir=config_dir,
                route_family="conversation_repair",
                context=_fake_context(),
            )
            rendered = bundle.render()

            self.assertIn("Last answered route family: repo_recommendation_single", rendered)
            self.assertIn("Do not pull in recommendation shortlist details", rendered)
            self.assertNotIn("Current recommendation pick", rendered)

    def test_repo_status_context_prefers_tracked_repo_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            initialize_managed_memory_state(config_dir)
            write_config_snapshot(config_dir, {"onboarding_complete": "true"})

            bundle = assemble_supervisor_prompt_context(
                config_dir=config_dir,
                route_family="repo_status",
                context=_fake_context(),
            )
            rendered = bundle.render()

            self.assertIn("Tracked repo subject: whisper.", rendered)
            self.assertIn("Tracked repo status: ready.", rendered)
            self.assertIn("Tracked install location: /tmp/whisper.", rendered)
            self.assertIn("Prefer tracked repo state and repo knowledge over recommendation memory.", rendered)
            self.assertNotIn("Current recommendation pick", rendered)

    def test_repo_verify_context_stays_on_repo_state_not_shortlist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            initialize_managed_memory_state(config_dir)
            write_config_snapshot(config_dir, {"onboarding_complete": "true"})

            bundle = assemble_supervisor_prompt_context(
                config_dir=config_dir,
                route_family="repo_verify",
                context=_fake_context(),
            )
            rendered = bundle.render()

            self.assertIn("Tracked repo subject: whisper.", rendered)
            self.assertIn("Tracked repo status: ready.", rendered)
            self.assertIn("Prefer tracked repo state and repo knowledge over recommendation memory.", rendered)
            self.assertNotIn("Current recommendation pick", rendered)

    def test_system_verify_context_prefers_healthcheck_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            initialize_managed_memory_state(config_dir)
            write_config_snapshot(config_dir, {"onboarding_complete": "true"})

            bundle = assemble_supervisor_prompt_context(
                config_dir=config_dir,
                route_family="system_verify",
                context=_fake_context(),
            )
            rendered = bundle.render()

            self.assertIn("bounded system verification", rendered.lower())
            self.assertIn("healthcheck", rendered.lower())

    def test_recommendation_context_includes_only_shortlist_and_offer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            initialize_managed_memory_state(config_dir)
            write_config_snapshot(config_dir, {"onboarding_complete": "true"})

            bundle = assemble_supervisor_prompt_context(
                config_dir=config_dir,
                route_family="repo_recommendation_single",
                context=_fake_context(),
            )
            rendered = bundle.render()

            self.assertIn("Current recommendation pick: private-gpt.", rendered)
            self.assertIn("Current runner-up: text-generation-webui.", rendered)
            self.assertIn("Known shortlist alternatives: text-generation-webui, fastchat.", rendered)
            self.assertIn("Live pending offer: recommend_best_fit.", rendered)
            self.assertNotIn("Tracked install location", rendered)


if __name__ == "__main__":
    unittest.main()
