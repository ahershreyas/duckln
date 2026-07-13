"""Tests for conversation routing policy contracts and examples."""

from __future__ import annotations

import unittest

from duckln.conversation_policy import (
    bootstrap_heuristics,
    conversation_examples_for_family,
    response_contract_for_family,
)


class ConversationPolicyTest(unittest.TestCase):
    def test_response_contract_exists_for_core_route_families(self) -> None:
        for route_family in (
            "small_talk",
            "capability",
            "system_summary",
            "system_verify",
            "memory_meta",
            "user_identity_meta",
            "conversation_repair",
            "conversation_restate",
            "repo_recommendation_single",
            "repo_recommendation_compare",
            "repo_requirements",
            "repo_fit",
            "repo_verify",
            "clarify",
            "utility_fallback",
        ):
            contract = response_contract_for_family(route_family)
            self.assertEqual(route_family, contract.route_family)
            self.assertTrue(contract.answer_shape)
            self.assertTrue(contract.style_name)

    def test_examples_cover_natural_chat_and_repo_followups(self) -> None:
        self.assertTrue(conversation_examples_for_family("small_talk"))
        self.assertTrue(conversation_examples_for_family("user_identity_meta"))
        self.assertTrue(conversation_examples_for_family("conversation_repair"))
        self.assertTrue(conversation_examples_for_family("conversation_restate"))
        self.assertTrue(conversation_examples_for_family("repo_recommendation_single"))
        self.assertTrue(conversation_examples_for_family("repo_recommendation_compare"))
        self.assertTrue(conversation_examples_for_family("repo_requirements"))
        self.assertTrue(conversation_examples_for_family("repo_verify"))
        self.assertTrue(conversation_examples_for_family("system_verify"))
        self.assertTrue(conversation_examples_for_family("clarify"))

    def test_examples_do_not_ship_old_robotic_report_labels(self) -> None:
        for route_family in ("repo_recommendation_single", "repo_requirements", "utility_fallback"):
            for example in conversation_examples_for_family(route_family):
                lowered = example.assistant.lower()
                self.assertNotIn("your system:", lowered)
                self.assertNotIn("repo likely needs:", lowered)
                self.assertNotIn("practical judgment:", lowered)
                self.assertNotIn("the main tradeoff is", lowered)

    def test_bootstrap_heuristics_ship_first_run_priors(self) -> None:
        heuristics = bootstrap_heuristics()
        route_families = {heuristic.route_family for heuristic in heuristics}
        self.assertIn("small_talk", route_families)
        self.assertIn("capability", route_families)
        self.assertIn("user_identity_meta", route_families)
        self.assertIn("conversation_repair", route_families)
        self.assertIn("conversation_restate", route_families)
        self.assertIn("repo_recommendation_single", route_families)
        self.assertIn("repo_requirements", route_families)
        self.assertIn("repo_fit", route_families)
        self.assertIn("repo_verify", route_families)
        self.assertIn("system_verify", route_families)


if __name__ == "__main__":
    unittest.main()
