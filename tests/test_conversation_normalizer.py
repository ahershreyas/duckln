"""Tests for the extracted conversation normalizer and router primitives."""

from __future__ import annotations

import unittest

from duckln.conversation_routes.normalizer import classify_conversation_intent, normalize_conversation_turn
from duckln.conversation_routes.router import route_family_for_intent


class ConversationNormalizerTest(unittest.TestCase):
    def test_normalizer_maps_noisy_small_talk_to_rapport(self) -> None:
        variants = ("How u doing?", "how you doing", "How ya doing", "how’s it going")

        for variant in variants:
            with self.subTest(variant=variant):
                turn = normalize_conversation_turn(variant)
                self.assertEqual("rapport", turn.base_intent)

    def test_normalizer_maps_noisy_capability_questions(self) -> None:
        variants = (
            "what can your help me with",
            "what can u help me with",
            "what all can you help me with",
        )

        for variant in variants:
            with self.subTest(variant=variant):
                self.assertEqual("capability", classify_conversation_intent(variant))

    def test_normalizer_maps_active_repo_question_variants(self) -> None:
        variants = (
            "do i have an active repo in my system",
            "is there an active repo",
            "what repo are you currently tracking",
            "is there aactive repo",
        )

        for variant in variants:
            with self.subTest(variant=variant):
                actual = classify_conversation_intent(variant)
                if variant == "is there aactive repo":
                    self.assertIn(actual, {"repo_active", "repo_lifecycle_active"})
                else:
                    self.assertEqual("repo_active", actual)

    def test_normalizer_maps_repo_lifecycle_inventory_variants(self) -> None:
        variants = (
            "can you show me all repos that we have installed or active",
            "could you check if we already have an repo in process or installed or active",
            "show me repos that we have installed",
            "do we have any repos already installed",
            "can you tell me repos that are already setup and ready",
            "can you tell me active repos in duckln",
            "can you tell me active or running repos if we have any",
        )

        for variant in variants:
            with self.subTest(variant=variant):
                expected = "repo_lifecycle_inventory"
                self.assertEqual(expected, classify_conversation_intent(variant))

    def test_normalizer_maps_repo_lifecycle_specific_status_variants(self) -> None:
        variants = (
            "do we have whisper installed and active",
            "is whisper installed and active",
            "do we have whisper repo",
            "do wehave whisper",
        )

        for variant in variants:
            with self.subTest(variant=variant):
                self.assertEqual("repo_lifecycle_specific_status", classify_conversation_intent(variant))

    def test_normalizer_maps_repo_lifecycle_active_variants(self) -> None:
        variants = (
            "is there a active repo which we worked previously",
            "which repo do you currently have active",
            "can you show me active repos",
            "which repos we have active",
        )

        for variant in variants:
            with self.subTest(variant=variant):
                expected = "repo_lifecycle_inventory" if "active repos" in variant or "repos we have active" in variant else "repo_lifecycle_active"
                self.assertEqual(expected, classify_conversation_intent(variant))

    def test_normalizer_maps_repo_path_and_vm_variants(self) -> None:
        cases = {
            "show me the repo path": "repo_path_show",
            "where did you install whisper": "repo_path_show",
            "show me path for whisper": "repo_path_show",
            "what the location of whisper in my system": "repo_path_show",
            "can you show me its path": "repo_path_show",
            "show me location of all the repos that is setup in environment": "repo_path_inventory",
            "what is whisper github path": "repo_source_show",
            "can you give me its github path": "repo_source_show",
            "show me all github links for tracked repos": "repo_source_inventory",
            "show me live repo sessions": "repo_live_sessions",
            "show me live repo sessions on vm": "repo_live_sessions_vm",
            "show me live repos in docker": "repo_live_sessions_docker",
            "show me actually running repos on cloud": "repo_live_sessions_cloud",
            "show me live repos on local": "repo_live_sessions_local",
            "what is vm": "vm_definition",
            "do you have the skills deploy vm": "vm_capability",
            "how many vms are there on my local system": "vm_list",
            "how many docker containers are there on my local system": "docker_list",
            "show docker containers": "docker_list",
            "which repo do you recommend to put in vm": "vm_repo_recommendation",
            "can you provide vm friendly choice repos give me top 3": "vm_repo_recommendation",
            "give me three vm friendly repo choices": "vm_repo_recommendation",
            "show me vm repos": "repo_inventory_vm",
            "show me all repos on duckln-vm-200": "repo_inventory_vm",
            "show me all repos in duckln-vm-fi": "repo_inventory_vm",
            "show me local repos": "repo_inventory_local",
            "list local repos that are ready": "repo_inventory_local",
        }

        for variant, expected in cases.items():
            with self.subTest(variant=variant):
                self.assertEqual(expected, classify_conversation_intent(variant))

    def test_normalizer_maps_understanding_checks_to_repair(self) -> None:
        variants = (
            "do you understand what i am asking",
            "that's not what i asked",
            "you are not understanding me",
        )

        for variant in variants:
            with self.subTest(variant=variant):
                self.assertEqual("conversation_repair", classify_conversation_intent(variant))

    def test_normalizer_maps_restatement_requests_to_restate(self) -> None:
        variants = (
            "say that again simply",
            "restate that",
            "say it plainly",
        )

        for variant in variants:
            with self.subTest(variant=variant):
                self.assertEqual("conversation_restate", classify_conversation_intent(variant))

    def test_router_maps_repo_active_to_repo_status_family(self) -> None:
        self.assertEqual("repo_status", route_family_for_intent("repo_active"))

    def test_router_maps_live_repo_sessions_to_own_family(self) -> None:
        self.assertEqual("repo_live_sessions", route_family_for_intent("repo_live_sessions"))

    def test_normalizer_maps_launch_and_remove_variants_structurally(self) -> None:
        cases = {
            "can you launch whisper": "repo_run",
            "run openclaw on duckln-vm-200": "repo_run",
            "how can we launch whisper": "repo_run",
            "how can we launch whiper /Users/shreyas/.duckln/projects/whisper": "repo_run",
            "can you help uninstall whiper": "repo_removal",
            "how can we access whisper": "repo_access",
            "can you tell me if whisper is running": "repo_lifecycle_specific_status",
            "can you check if its ready and is error free to run": "repo_verify",
            "can you check if duckln is error free": "system_verify",
            "can you setup whisper": "setup_request",
            "compare whisper vs open-webui": "repo_recommendation_compare",
            "run it": "repo_run",
            "stop whisper": "repo_stop",
            "stop it": "repo_stop",
            "show logs for whisper": "repo_logs",
            "remove it": "repo_removal",
            "where did you install it": "repo_path_show",
            "show me its path": "repo_path_show",
            "open it": "repo_access",
        }

        for variant, expected in cases.items():
            with self.subTest(variant=variant):
                self.assertEqual(expected, classify_conversation_intent(variant))

    def test_vm_management_commands_do_not_route_as_repo_removal(self) -> None:
        cases = {
            "delete all VMs except duckln-vm-200": "vm_delete",
            "remove vm duckln-vm-openclaw": "vm_delete",
            "run VM duckln-vm-openclaw": "vm_open",
            "run duckln-vm-openclaw": "vm_open",
            "connect to VM duckln-vm-200": "vm_open",
        }

        for variant, expected in cases.items():
            with self.subTest(variant=variant):
                self.assertEqual(expected, classify_conversation_intent(variant))
                self.assertEqual("vm_info", route_family_for_intent(expected))


if __name__ == "__main__":
    unittest.main()
