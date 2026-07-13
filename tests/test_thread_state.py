"""Tests for scoped conversation thread helpers."""

from __future__ import annotations

import unittest

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile

from agent.probe import GpuProbeState, SystemProbe
from duckln.agent_context import AgentContextService
from duckln.conversation_agent import _build_conversation_context, _read_supervisor_followup_state
from duckln.conversation_routes.threading import (
    followup_expiry_for_reply,
    offer_id_for_reply,
    state_is_expired,
    thread_expiry_for_reply,
    thread_id_for_reply,
    thread_ids_match,
)
from state.access import initialize_managed_memory_state
from state.repo_catalog import resolve_local_repo_catalog_cache_path
from state.store import initialize_state_store
from state.access import write_config_snapshot, write_followup_state


class ThreadStateHelpersTest(unittest.TestCase):
    def test_thread_id_reuses_prior_for_repo_followups(self) -> None:
        thread_id = thread_id_for_reply(
            has_repo_thread=True,
            intent="repo_overview",
            route_family="repo_overview",
            repo_token="https://example.com/whisper",
            prior_thread_id="repo:123",
        )

        self.assertEqual("repo:123", thread_id)

    def test_offer_id_requires_offer_kind_and_thread(self) -> None:
        self.assertIsNone(offer_id_for_reply(offer_kind=None, thread_id="thread:1"))
        self.assertIsNotNone(offer_id_for_reply(offer_kind="inspect_requirements", thread_id="thread:1"))

    def test_thread_ids_match_only_blocks_when_both_are_present_and_different(self) -> None:
        self.assertTrue(thread_ids_match(pending_offer_thread_id=None, active_thread_id="thread:1"))
        self.assertTrue(thread_ids_match(pending_offer_thread_id="thread:1", active_thread_id=None))
        self.assertTrue(thread_ids_match(pending_offer_thread_id="thread:1", active_thread_id="thread:1"))
        self.assertFalse(thread_ids_match(pending_offer_thread_id="thread:1", active_thread_id="thread:2"))

    def test_expiry_helpers_mark_past_timestamps_as_expired(self) -> None:
        now = datetime.now(timezone.utc)

        self.assertFalse(state_is_expired(thread_expiry_for_reply(thread_id="repo:1", now=now), now=now))
        self.assertFalse(state_is_expired(followup_expiry_for_reply(offer_id="offer:1", now=now), now=now))
        self.assertTrue(state_is_expired((now - timedelta(minutes=1)).isoformat(timespec="seconds"), now=now))

    def test_read_followup_state_prunes_thread_fields_for_new_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            write_config_snapshot(temp_dir, {"session.current_id": "session-2"})
            write_followup_state(
                temp_dir,
                {
                    "active_session_id": "session-1",
                    "active_thread_id": "repo:1",
                    "active_thread_expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(timespec="seconds"),
                    "pending_offer_kind": "recommend_best_fit",
                    "pending_offer_expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(timespec="seconds"),
                },
            )

            state = _read_supervisor_followup_state(temp_dir)

            self.assertIsNone(state.active_thread_id)
            self.assertIsNone(state.pending_offer_kind)

    def test_build_conversation_context_separates_live_offer_thread_and_durable_repo_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            initialize_managed_memory_state(config_dir)
            write_config_snapshot(
                config_dir,
                {
                    "execution_target": "local",
                    "session.current_id": "session-7",
                },
            )
            write_followup_state(
                config_dir,
                {
                    "active_session_id": "session-7",
                    "active_topic": "repo_recommendation_single",
                    "active_thread_id": "recommendation:42",
                    "active_thread_expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(timespec="seconds"),
                    "pending_offer_kind": "recommend_best_fit",
                    "pending_offer_label": "pick the best repo for this machine",
                    "pending_offer_repo_name": "private-gpt",
                    "pending_offer_id": "offer:42",
                    "pending_offer_thread_id": "recommendation:42",
                    "pending_offer_expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(timespec="seconds"),
                    "shortlist_primary_repo": "private-gpt",
                    "shortlist_secondary_repo": "text-generation-webui",
                    "shortlist_repo_names": ["text-generation-webui", "fastchat"],
                },
            )

            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                """[
  {
    "name": "whisper",
    "html_url": "https://example.com/whisper",
    "stargazers_count": 1200,
    "description": "speech model",
    "updated_at": "2026-01-01T00:00:00Z",
    "topics": ["audio", "speech"],
    "language": "Python"
  },
  {
    "name": "private-gpt",
    "html_url": "https://example.com/private-gpt",
    "stargazers_count": 900,
    "description": "local rag",
    "updated_at": "2026-01-02T00:00:00Z",
    "topics": ["llm", "rag"],
    "language": "Python"
  }
]""",
                encoding="utf-8",
            )

            store = initialize_state_store(config_dir)
            store.upsert_managed_memory(
                memory_key="session:alpha",
                memory_kind="session",
                relative_path="sessions/alpha.md",
                content="# Session\n\nUser asked about whisper and repo recommendations.",
            )
            store.upsert_promoted_heuristic(
                heuristic_key="conversation:feedback_style",
                family="conversation",
                subject_key="feedback_style",
                summary="Prefer direct grounded phrasing.",
                confidence=0.8,
                metadata={"intent": "feedback_style"},
            )
            store.upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path="/tmp/whisper",
                status="ready",
                summary="whisper is ready",
                metadata={"repo_name": "whisper", "install_location": "/tmp/whisper"},
            )
            store.upsert_learning_record(
                learning_key="repo:https://example.com/whisper",
                family="repo",
                subject_key="https://example.com/whisper",
                summary="whisper had a valid local setup",
                signal="success",
                metadata={"repo_name": "whisper"},
            )
            AgentContextService().record_recommendation_context(
                config_dir=config_dir,
                repo=type("Repo", (), {"name": "private-gpt", "repo_url": "https://example.com/private-gpt"})(),
                reason="it is the cleaner first fit",
                alternatives=("text-generation-webui", "fastchat"),
                caveat=None,
                system_hint="Apple Silicon laptop",
            )

            probe = SystemProbe(
                operating_system="Darwin",
                architecture="arm64",
                cpu_logical_cores=8,
                ram_bytes=16 * 1024**3,
                disk_free_bytes=200 * 1024**3,
                python_version="3.11.9",
                gpu=GpuProbeState(
                    backend="mps",
                    summary="Apple Silicon detected; MPS available.",
                    cuda_capable=False,
                    cuda_available=False,
                    mps_capable=True,
                    mps_available=True,
                ),
            )

            context = _build_conversation_context(
                "do i have an active repo in my system?",
                current=None,
                config_dir=config_dir,
                system_probe=probe,
                recent_turns=(),
                context_service=AgentContextService(),
                followup_state=_read_supervisor_followup_state(config_dir),
            )

            self.assertEqual("recommendation:42", context.live_thread.thread_id)
            self.assertEqual("recommend_best_fit", context.pending_offer.kind)
            self.assertEqual("private-gpt", context.durable_recommendation.primary_repo)
            self.assertEqual("text-generation-webui", context.durable_recommendation.secondary_repo)
            self.assertIsNotNone(context.durable_repo_memory)
            self.assertEqual("whisper", context.durable_repo_memory.repo_name)
            self.assertEqual("ready", context.durable_repo_memory.tracked_status)
            self.assertEqual("/tmp/whisper", context.durable_repo_memory.install_location)
            self.assertTrue(context.durable_repo_memory.has_learning_signal)
            self.assertFalse(context.durable_repo_memory.in_recommendation_memory)
            self.assertIsNotNone(context.repo_knowledge)
            self.assertIsNotNone(context.repo_state_snapshot)
            self.assertIsNotNone(context.active_repo_snapshot)
            self.assertGreaterEqual(len(context.repo_inventory_snapshot), 1)
            self.assertEqual("whisper", context.active_repo_snapshot.repo_name)
            self.assertIsNotNone(context.recommendation_memory)
            self.assertEqual("private-gpt", context.recommendation_memory["repo_name"])
            self.assertIn("User asked about whisper", context.recent_thread_summary or "")
            self.assertIn("whisper is ready", context.project_summary or "")
            self.assertEqual(1, len(context.promoted_heuristics))


if __name__ == "__main__":
    unittest.main()
