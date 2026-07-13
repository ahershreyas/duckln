"""Tests for extracted conversation route helpers."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from duckln.conversation_routes.decision_responses import (
    build_clarification_reply,
    build_utility_fallback_reply,
)
from duckln.conversation_routes.followups import resolve_followup_intent
from duckln.conversation_routes.local_general import build_local_general_reply
from duckln.conversation_routes.recovery import clarification_options_for_candidates, clarification_options_for_message
from duckln.conversation_routes.replies import (
    clarification_options,
    memory_meta_summary,
    repo_inventory_text,
    repo_memory_meta_summary,
    repo_overview_options,
    repo_status_text,
    utility_fallback_options,
)
from duckln.conversation_policy import RouteCandidate
from duckln.conversation_routes.repo_responses import (
    build_repo_active_draft,
    build_repo_inventory_draft,
    build_repo_lifecycle_active_draft,
    build_repo_lifecycle_inventory_draft,
    build_repo_run_draft,
    build_repo_lifecycle_status_draft,
    build_repo_source_draft,
    build_repo_status_draft,
    build_repo_verify_draft,
)
from duckln.conversation_routes.repair import restatement_reply_options


class ConversationRouteHelpersTest(unittest.TestCase):
    @dataclass(frozen=True)
    class _Reply:
        text: str
        intent: str
        steer: str | None = None
        specialist_name: str = "test"
        action: str | None = None
        user_alias: str | None = None

    def _build_local_general_reply(self, *, intent: str, message: str, context: object | None = None):
        if context is None:
            context = SimpleNamespace(
                active_repo=None,
                mentioned_repo=None,
                machine_explanation="",
                execution_target="local",
                active_vm_name=None,
                durable_recommendation=SimpleNamespace(primary_repo=None),
                agent_context=SimpleNamespace(
                    memory=SimpleNamespace(
                        promoted_heuristics=(),
                        session_summary=None,
                        project_summary=None,
                    )
                ),
                followup_state=SimpleNamespace(last_route_family=None),
                durable_repo_memory=None,
                thread_state=SimpleNamespace(active_repo_name=None),
                pending_offer=SimpleNamespace(kind=None),
                records=(),
                promoted_heuristics=(),
                recent_thread_summary=None,
            )
        return build_local_general_reply(
            intent=intent,
            message=message,
            normalized_compact=" ".join(message.lower().split()),
            current=None,
            config_dir=None,
            system_probe=None,
            context=context,
            recent_turns=(),
            recent_replies=(),
            specialist_name="conversation-fallback",
            prefers_direct_tone=False,
            reply_factory=self._Reply,
            pick_reply=lambda options, _normalized, _recent: options[0],
            extract_user_alias=lambda message: None,
            join_blocks=lambda *parts: "\n\n".join(parts),
            bullet_block=lambda items: "\n".join(f"- {item}" for item in items),
            build_system_summary_reply=lambda system_probe: SimpleNamespace(summary="system"),
            memory_meta_summary=lambda **kwargs: "memory",
            repair_reply_texts=lambda **kwargs: ("repair",),
            restatement_reply_texts=lambda **kwargs: ("restate",),
            list_learning_records=lambda config_dir: (),
        )

    def test_clarification_options_builds_short_questions(self) -> None:
        options = clarification_options(("repo recommendation", "repo status"))

        self.assertTrue(options[0].startswith("Do you mean"))
        self.assertIn("repo status", options[0])

    def test_clarification_options_for_candidates_derives_likely_interpretations(self) -> None:
        context = SimpleNamespace(
            mentioned_repo=None,
            active_repo=None,
            durable_repo_memory=None,
            pending_offer=SimpleNamespace(kind="recommend_best_fit"),
        )

        options = clarification_options_for_candidates(
            (
                RouteCandidate("repo_recommendation_single", "repo_recommendation_single", 0.82, "test"),
                RouteCandidate("system_summary", "system_capacity", 0.79, "test"),
                RouteCandidate("workflow_action", "setup_request", 0.77, "test"),
            ),
            context=context,
        )

        labels = tuple(option.label for option in options)
        self.assertIn("repo recommendation", labels)
        self.assertIn("system capacity", labels)

    def test_clarification_options_for_message_prefers_lifecycle_choices_over_stale_shortlist_labels(self) -> None:
        context = SimpleNamespace(
            mentioned_repo=None,
            active_repo=None,
            durable_repo_memory=None,
            pending_offer=SimpleNamespace(kind="recommend_best_fit"),
            followup_state=SimpleNamespace(
                pending_clarification_options=("second-best repo", "full shortlist", "next step for private-gpt")
            ),
        )

        options = clarification_options_for_message(
            "do we already have a repo",
            context=context,
            looks_like_repo_pronoun_followup=lambda _text: False,
        )

        labels = tuple(option.label for option in options)
        self.assertIn("active repo status", labels)
        self.assertIn("all tracked repos", labels)
        self.assertIn("repo recommendation", labels)
        self.assertNotIn("second-best repo", labels)

    def test_clarification_options_for_candidates_stays_lifecycle_focused_when_repo_state_and_recommendation_compete(self) -> None:
        context = SimpleNamespace(
            mentioned_repo=None,
            active_repo=None,
            durable_repo_memory=None,
            pending_offer=SimpleNamespace(kind="recommend_best_fit"),
        )

        options = clarification_options_for_candidates(
            (
                RouteCandidate("repo_status", "repo_lifecycle_active", 0.81, "lifecycle"),
                RouteCandidate("repo_inventory", "repo_lifecycle_inventory", 0.79, "inventory"),
                RouteCandidate("repo_recommendation_rationale", "repo_recommendation_rationale", 0.78, "stale recommendation"),
            ),
            context=context,
            normalized_compact="do we already have a repo",
        )

        labels = tuple(option.label for option in options)
        self.assertIn("active repo status", labels)
        self.assertIn("all tracked repos", labels)
        self.assertNotIn("why that repo", labels)

    def test_build_repo_source_draft_returns_tracked_source_url(self) -> None:
        draft = build_repo_source_draft(
            repo_name="whisper",
            source_repo_url="https://github.com/openai/whisper",
        )

        self.assertEqual("repo_source_show", draft.intent)
        self.assertIn("https://github.com/openai/whisper", draft.text)

    def test_build_repo_source_draft_handles_missing_source_url(self) -> None:
        draft = build_repo_source_draft(
            repo_name="custom-repo",
            source_repo_url=None,
        )

        self.assertEqual("repo_source_show", draft.intent)
        self.assertIn("does not have a public source repo url recorded", draft.text.lower())

    def test_utility_fallback_options_prefers_live_recommendation_thread(self) -> None:
        options = utility_fallback_options(
            repo_name=None,
            shortlist_primary="private-gpt",
            shortlist_secondary="FastChat",
            pending_offer_kind="recommend_best_fit",
        )

        self.assertIn("pick the best repo", options[0])
        self.assertNotIn("We still have a live recommendation thread", options[0])

    def test_restatement_reply_options_keep_response_plain(self) -> None:
        options = restatement_reply_options(
            last_route_family="repo_recommendation_single",
            repo_name="whisper",
            recommendation_primary="private-gpt",
        )

        self.assertIn("Plain version", options[0])
        self.assertIn("private-gpt", options[0])

    def test_memory_meta_summary_stays_bounded(self) -> None:
        summary = memory_meta_summary(
            config_dir_present=True,
            recommendation_repo_name="Whisper",
            has_session_summary=True,
            candidate_count=2,
            promoted_count=1,
            heuristic_count=1,
        )

        self.assertIn("Whisper", summary)
        self.assertIn("promoted heuristic", summary)

    def test_repo_memory_meta_summary_uses_bullets(self) -> None:
        summary = repo_memory_meta_summary(
            repo_name="whisper",
            facts=("Tracked setup state says whisper is ready", "Duckln has bounded repo notes for it"),
        )

        self.assertIn("- Tracked setup state says whisper is ready", summary)

    def test_repo_overview_options_return_detailed_and_brief_variants(self) -> None:
        detailed, brief = repo_overview_options(
            repo_name="private-gpt",
            description="a local document chat app",
            practical_bits=("Setup looks moderate.",),
            next_step="I can inspect its requirements next.",
        )

        self.assertIn("I can inspect its requirements next.", detailed)
        self.assertNotIn("I can inspect its requirements next.", brief)

    def test_repo_inventory_text_uses_comparison_style_lines(self) -> None:
        text = repo_inventory_text(
            [
                {"name": "whisper", "status": "ready"},
                {"name": "private-gpt", "status": "planned"},
            ]
        )

        self.assertIn("- whisper: ready", text)

    def test_repo_status_text_includes_location_when_present(self) -> None:
        text = repo_status_text(repo_name="whisper", status="ready", location="/tmp/whisper")

        self.assertIn("/tmp/whisper", text)

    def test_repo_status_draft_uses_tracked_state(self) -> None:
        draft = build_repo_status_draft(
            repo_name="whisper",
            tracked_status="ready",
            install_location="/tmp/whisper",
        )

        self.assertEqual("repo_status", draft.intent)
        self.assertIn("whisper", draft.text.lower())
        self.assertIn("/tmp/whisper", draft.text)

    def test_repo_verify_draft_treats_check_as_live_verification(self) -> None:
        draft = build_repo_verify_draft(
            repo_name="whisper",
            repo_key="https://example.com/whisper",
            tracked_status="ready",
            install_location="/tmp/whisper",
            run_command=".venv/bin/python -m whisper --help",
            last_blocker=None,
            last_run_result="passed",
        )

        self.assertEqual("repo_verify", draft.intent)
        self.assertEqual("verify_repo", draft.action)
        self.assertIn("bounded live check", draft.text.lower())
        self.assertIn("instead of only trusting tracked state", draft.text.lower())

    def test_repo_inventory_draft_handles_empty_state(self) -> None:
        draft = build_repo_inventory_draft(inventory=[])

        self.assertEqual("repo_inventory", draft.intent)
        self.assertIn("does not have any prepared repos", draft.text.lower())

    def test_repo_lifecycle_inventory_draft_uses_lifecycle_language(self) -> None:
        draft = build_repo_lifecycle_inventory_draft(
            inventory=[
                {"name": "whisper", "status": "ready"},
                {"name": "open-webui", "status": "running"},
            ]
        )

        self.assertEqual("repo_lifecycle_inventory", draft.intent)
        self.assertIn("installed, active, or otherwise prepared", draft.text.lower())
        self.assertIn("whisper", draft.text.lower())

    def test_repo_active_draft_handles_missing_active_repo(self) -> None:
        draft = build_repo_active_draft(active_name=None, active_status=None)

        self.assertEqual("repo_active", draft.intent)
        self.assertIn("no active repo context", draft.text.lower())

    def test_repo_lifecycle_active_draft_answers_directly(self) -> None:
        draft = build_repo_lifecycle_active_draft(active_name="whisper", active_status="ready")

        self.assertEqual("repo_lifecycle_active", draft.intent)
        self.assertIn("no live running repo session", draft.text.lower())
        self.assertIn("last recorded state is ready", draft.text.lower())

    def test_repo_lifecycle_status_draft_answers_installed_or_active_question(self) -> None:
        draft = build_repo_lifecycle_status_draft(
            repo_name="whisper",
            tracked_status="ready",
            install_location="/tmp/whisper",
        )

        self.assertEqual("repo_lifecycle_specific_status", draft.intent)
        self.assertIn("currently has whisper tracked as ready", draft.text.lower())
        self.assertIn("/tmp/whisper", draft.text)

    def test_repo_run_draft_treats_cli_tool_as_usage_not_service(self) -> None:
        draft = build_repo_run_draft(
            repo_name="whisper",
            repo_key="https://example.com/whisper",
            run_command=None,
            verify_command=".venv/bin/python -m whisper --help",
            manual_command=".venv/bin/python -m whisper <audio-file> --model base",
            runtime_kind="cli_tool",
            last_blocker=None,
            last_run_result="verified",
        )

        self.assertEqual("repo_run", draft.intent)
        self.assertEqual("run_repo", draft.action)
        self.assertIn("cli tool", draft.text.lower())
        self.assertIn("<audio-file>", draft.text)

    def test_repo_run_draft_treats_cli_tool_verification_command_as_usage_not_service(self) -> None:
        draft = build_repo_run_draft(
            repo_name="whisper",
            repo_key="https://example.com/whisper",
            run_command=".venv/bin/python -m whisper --help",
            verify_command=".venv/bin/python -m whisper --help",
            manual_command=".venv/bin/python -m whisper <audio-file> --model base",
            runtime_kind="cli_tool",
            last_blocker=None,
            last_run_result="verified",
        )

        self.assertEqual("repo_run", draft.intent)
        self.assertEqual("run_repo", draft.action)
        self.assertIn("cli tool", draft.text.lower())
        self.assertIn("verify", draft.text.lower())
        self.assertIn("<audio-file>", draft.text)

    def test_local_general_capability_handler_returns_structured_bullets(self) -> None:
        context = SimpleNamespace(
            active_repo=None,
            mentioned_repo=None,
            machine_explanation="",
            durable_recommendation=SimpleNamespace(primary_repo=None),
            agent_context=SimpleNamespace(
                memory=SimpleNamespace(
                    promoted_heuristics=(),
                    session_summary=None,
                    project_summary=None,
                )
            ),
            followup_state=SimpleNamespace(last_route_family=None),
            durable_repo_memory=None,
            thread_state=SimpleNamespace(active_repo_name=None),
            pending_offer=SimpleNamespace(kind=None),
        )

        reply = build_local_general_reply(
            intent="capability",
            message="what can you help me with?",
            normalized_compact="what can you help me with",
            current=None,
            config_dir=None,
            system_probe=None,
            context=context,
            recent_turns=(),
            recent_replies=(),
            specialist_name="conversation-fallback",
            prefers_direct_tone=False,
            reply_factory=self._Reply,
            pick_reply=lambda options, _normalized, _recent: options[0],
            extract_user_alias=lambda message: None,
            join_blocks=lambda *parts: "\n\n".join(parts),
            bullet_block=lambda items: "\n".join(f"- {item}" for item in items),
            build_system_summary_reply=lambda system_probe: SimpleNamespace(summary="system"),
            memory_meta_summary=lambda **kwargs: "memory",
            repair_reply_texts=lambda **kwargs: ("repair",),
            restatement_reply_texts=lambda **kwargs: ("restate",),
            list_learning_records=lambda config_dir: (),
        )

        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertEqual("capability", reply.intent)
        self.assertIn("- Pick a repo from /repos", reply.text)

    def test_local_general_docker_list_counts_local_containers(self) -> None:
        completed = SimpleNamespace(
            returncode=0,
            stdout="web\tUp 2 minutes\nworker\tExited (0) 1 hour ago\n",
            stderr="",
        )
        with patch("duckln.conversation_routes.local_general.shutil.which", return_value="/usr/local/bin/docker"), patch(
            "duckln.conversation_routes.local_general.subprocess.run",
            return_value=completed,
        ) as run_mock:
            reply = self._build_local_general_reply(
                intent="docker_list",
                message="how many docker containers are there?",
            )

        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertEqual("docker_list", reply.intent)
        self.assertIn("2 local Docker containers", reply.text)
        self.assertIn("| Container | Status", reply.text)
        self.assertIn("| web", reply.text)
        self.assertIn("Up 2 minutes", reply.text)
        run_mock.assert_called_once()

    def test_local_general_docker_list_handles_missing_docker(self) -> None:
        with patch("duckln.conversation_routes.local_general.shutil.which", return_value=None):
            reply = self._build_local_general_reply(
                intent="docker_list",
                message="show docker containers",
            )

        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertIn("Docker is not installed", reply.text)

    def test_decision_response_builders_return_bounded_replies(self) -> None:
        clarification = build_clarification_reply(
            decision=SimpleNamespace(
                clarification_options=(
                    SimpleNamespace(label="repo recommendation"),
                    SimpleNamespace(label="repo status"),
                )
            ),
            recent_replies=(),
            reply_factory=self._Reply,
            pick_reply=lambda options, _normalized, _recent: options[0],
            clarification_reply_texts=lambda labels: (f"Do you mean {labels[0]} or {labels[1]}?",),
        )
        self.assertEqual("clarify", clarification.intent)
        self.assertIn("repo recommendation", clarification.text)

        fallback = build_utility_fallback_reply(
            context=SimpleNamespace(
                mentioned_repo=None,
                active_repo=None,
                durable_repo_memory=None,
                durable_recommendation=SimpleNamespace(primary_repo="private-gpt", secondary_repo="FastChat"),
                pending_offer=SimpleNamespace(kind="recommend_best_fit"),
            ),
            recent_replies=(),
            reply_factory=self._Reply,
            pick_reply=lambda options, _normalized, _recent: options[0],
            utility_fallback_reply_texts=lambda **kwargs: ("I can pick the best repo for this machine.",),
        )
        self.assertEqual("generic", fallback.intent)
        self.assertIn("best repo", fallback.text)

    def test_followup_resolution_ignores_stale_thread_for_protected_current_turns(self) -> None:
        followup_state = SimpleNamespace(
            pending_next_action="recommend_best_fit",
            pending_offer_kind="recommend_best_fit",
            pending_offer_id="offer:1",
            pending_offer_thread_id="thread:1",
            active_thread_id="thread:1",
            pending_repo_key="repo:whisper",
            pending_repo_name="whisper",
            last_supervisor_decision="recommend_best_fit",
        )

        self.assertIsNone(
            resolve_followup_intent(
                "how u doing",
                followup_state=followup_state,
                acceptance_phrase=lambda text: None,
                thread_ids_match=lambda **kwargs: True,
            )
        )

    def test_followup_resolution_can_continue_repo_verify_offer(self) -> None:
        followup_state = SimpleNamespace(
            pending_next_action="verify_repo",
            pending_offer_kind="verify_repo",
            pending_offer_id="offer:1",
            pending_offer_thread_id="thread:1",
            active_thread_id="thread:1",
            pending_repo_key="repo:whisper",
            pending_repo_name="whisper",
            last_supervisor_decision="repo_verify",
        )

        self.assertEqual(
            "repo_verify",
            resolve_followup_intent(
                "yes",
                followup_state=followup_state,
                acceptance_phrase=lambda text: text in {"yes", "yes please"},
                thread_ids_match=lambda **kwargs: True,
            ),
        )
        self.assertIsNone(
            resolve_followup_intent(
                "do i have an active repo in my system",
                followup_state=followup_state,
                acceptance_phrase=lambda text: None,
                thread_ids_match=lambda **kwargs: True,
            )
        )
        self.assertIsNone(
            resolve_followup_intent(
                "can you show me all repos that we have installed or active",
                followup_state=followup_state,
                acceptance_phrase=lambda text: None,
                thread_ids_match=lambda **kwargs: True,
            )
        )
        self.assertIsNone(
            resolve_followup_intent(
                "do we have whisper installed and active",
                followup_state=followup_state,
                acceptance_phrase=lambda text: None,
                thread_ids_match=lambda **kwargs: True,
            )
        )


if __name__ == "__main__":
    unittest.main()
