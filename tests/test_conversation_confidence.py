"""Tests for route confidence and tie-breaking helpers."""

from __future__ import annotations

import unittest

from duckln.conversation_policy import RouteCandidate
from duckln.conversation_routes.confidence import (
    build_confidence_snapshot,
    confidence_band,
    explicit_base_candidate,
    should_answer_without_clarify,
    should_clarify_route,
)


class ConversationConfidenceTest(unittest.TestCase):
    def test_build_confidence_snapshot_orders_candidates_and_computes_margin(self) -> None:
        snapshot = build_confidence_snapshot(
            [
                RouteCandidate("capability", "capability", 0.82, "capability"),
                RouteCandidate("small_talk", "rapport", 0.91, "rapport"),
            ]
        )

        self.assertEqual("small_talk", snapshot.top.route_family if snapshot.top else None)
        self.assertAlmostEqual(0.09, snapshot.margin, places=2)

    def test_confidence_band_categorizes_scores(self) -> None:
        self.assertEqual("high", confidence_band(0.91))
        self.assertEqual("medium", confidence_band(0.8))
        self.assertEqual("low", confidence_band(0.6))
        self.assertEqual("uncertain", confidence_band(0.2))

    def test_explicit_base_candidate_prefers_high_confidence_current_turn_family(self) -> None:
        snapshot = build_confidence_snapshot(
            [
                RouteCandidate("small_talk", "rapport", 0.95, "rapport"),
                RouteCandidate("repo_recommendation_single", "repo_recommendation_single", 0.9, "stale thread"),
            ]
        )

        chosen = explicit_base_candidate(
            snapshot,
            base_intent="rapport",
            base_route_family="small_talk",
        )

        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertEqual("small_talk", chosen.route_family)

    def test_should_answer_without_clarify_accepts_clear_top_route(self) -> None:
        snapshot = build_confidence_snapshot(
            [
                RouteCandidate("repo_status", "repo_active", 0.9, "repo status"),
                RouteCandidate("workflow_action", "generic", 0.6, "workflow"),
            ]
        )

        self.assertTrue(
            should_answer_without_clarify(
                snapshot,
                followup_intent=None,
                subject_name="whisper",
            )
        )

    def test_should_clarify_route_on_close_competing_candidates(self) -> None:
        snapshot = build_confidence_snapshot(
            [
                RouteCandidate("workflow_action", "setup_request", 0.8, "setup"),
                RouteCandidate("repo_status", "repo_active", 0.75, "repo"),
            ]
        )

        self.assertTrue(
            should_clarify_route(
                snapshot,
                clarification_option_count=2,
                subject_name=None,
                top_route_family="workflow_action",
                looks_like_repo_pronoun_followup=True,
            )
        )

    def test_explicit_base_candidate_prefers_repo_state_turn_over_recommendation_thread(self) -> None:
        snapshot = build_confidence_snapshot(
            [
                RouteCandidate("repo_status", "repo_lifecycle_specific_status", 0.95, "lifecycle state"),
                RouteCandidate("repo_recommendation_single", "repo_recommendation_single", 0.91, "stale recommendation"),
            ]
        )

        chosen = explicit_base_candidate(
            snapshot,
            base_intent="repo_lifecycle_specific_status",
            base_route_family="repo_status",
        )

        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertEqual("repo_status", chosen.route_family)


if __name__ == "__main__":
    unittest.main()
