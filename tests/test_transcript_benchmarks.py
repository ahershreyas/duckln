"""Paraphrase-heavy transcript benchmarks for real conversation stability."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from agent.probe import GpuProbeState, SystemProbe
from duckln.conversation_agent import ConversationTurn, FreeTextReply
from duckln.main import _respond_to_free_text
from state.access import initialize_managed_memory_state, write_followup_state
from state.repo_catalog import resolve_local_repo_catalog_cache_path
from state.store import initialize_state_store


def _benchmark_probe() -> SystemProbe:
    return SystemProbe(
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


def _seed_stale_recommendation_thread(config_dir: Path) -> None:
    initialize_managed_memory_state(config_dir)
    write_followup_state(
        config_dir,
        {
            "active_topic": "repo_recommendation_single",
            "last_route_family": "repo_recommendation_single",
            "pending_offer_kind": "recommend_best_fit",
            "pending_offer_id": "offer:1",
            "pending_offer_thread_id": "thread:1",
            "active_thread_id": "thread:1",
            "pending_offer_repo_name": "whisper",
            "last_discussed_repo_name": "whisper",
            "shortlist_primary_repo": "private-gpt",
            "shortlist_secondary_repo": "fastchat",
        },
    )
    initialize_state_store(config_dir).upsert_repo_state(
        repo_key="https://example.com/whisper",
        status="ready",
        summary="whisper is ready",
        repo_path="/tmp/whisper",
        repo_url="https://example.com/whisper",
        metadata={"repo_name": "whisper", "install_location": "/tmp/whisper"},
    )


def _seed_multi_target_live_sessions(config_dir: Path) -> None:
    store = initialize_state_store(config_dir)
    store.upsert_repo_state(
        repo_key="https://example.com/whisper",
        status="running",
        summary="whisper running",
        repo_path="/tmp/whisper",
        repo_url="https://example.com/whisper",
        execution_target="local",
        metadata={"repo_name": "whisper", "install_location": "/tmp/whisper"},
    )
    store.upsert_repo_state(
        repo_key="https://example.com/private-gpt",
        status="running",
        summary="private-gpt running",
        repo_path="/home/ubuntu/private-gpt",
        repo_url="https://example.com/private-gpt",
        execution_target="vm",
        vm_name="duckln-vm-1",
        metadata={"repo_name": "private-gpt", "install_location": "/home/ubuntu/private-gpt"},
    )
    store.upsert_repo_state(
        repo_key="https://example.com/open-webui",
        status="interactive",
        summary="open-webui interactive",
        repo_path="/workspace/open-webui",
        repo_url="https://example.com/open-webui",
        execution_target="docker",
        metadata={"repo_name": "open-webui", "docker_name": "open-webui-stack"},
    )
    store.upsert_repo_state(
        repo_key="https://example.com/comfyui",
        status="running",
        summary="comfyui running",
        repo_path="/home/duckln/comfyui",
        repo_url="https://example.com/comfyui",
        execution_target="gcp",
        metadata={"repo_name": "comfyui", "cloud_vendor": "GCP", "cloud_region": "us-central1-a"},
    )


def _run_transcript_step(
    *,
    user_text: str,
    config_dir: Path,
    recent_turns: list[ConversationTurn],
    recent_replies: list[FreeTextReply],
    system_probe: SystemProbe | None = None,
) -> FreeTextReply:
    reply = _respond_to_free_text(
        user_text,
        config_dir=config_dir,
        system_probe=system_probe,
        recent_turns=tuple(recent_turns),
        recent_replies=tuple(recent_replies),
    )
    recent_turns.append(ConversationTurn(role="user", content=user_text))
    recent_turns.append(ConversationTurn(role="assistant", content=reply.text))
    recent_replies.append(reply)
    return reply


class TranscriptBenchmarksTest(unittest.TestCase):
    def test_paraphrase_benchmark_current_turn_priority_under_stale_recommendation_thread(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            _seed_stale_recommendation_thread(config_dir)

            benchmark_cases = {
                "rapport": (
                    "How u doing?",
                    "how ya doing",
                    "how's it going",
                    "how are things going",
                ),
                "capability": (
                    "what can your help me with?",
                    "what can u help me with",
                    "what all can you help me with",
                    "what exactly can you help me do here",
                ),
                "repo_active": (
                    "do i have an active repo in my system?",
                    "which repo is active",
                    "what repo are you currently tracking",
                ),
                "repo_lifecycle_active": (
                    "which repo do you currently have active",
                ),
                "conversation_repair": (
                    "Do you understand what i am asking?",
                    "that's not what i asked",
                    "you are not understanding me",
                    "that is not what i meant",
                ),
            }

            for expected_intent, variants in benchmark_cases.items():
                for variant in variants:
                    with self.subTest(intent=expected_intent, variant=variant):
                        reply = _respond_to_free_text(variant, config_dir=config_dir)
                        self.assertEqual(expected_intent, reply.intent)
                        self.assertNotIn("recommendation thread", reply.text.lower())

    def test_paraphrase_benchmark_restate_variants_stay_plain(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            initialize_managed_memory_state(config_dir)
            write_followup_state(
                config_dir,
                {
                    "last_route_family": "repo_recommendation_single",
                    "last_discussed_repo_name": "whisper",
                    "shortlist_primary_repo": "private-gpt",
                    "pending_offer_kind": "recommend_best_fit",
                    "pending_offer_id": "offer:1",
                    "pending_offer_thread_id": "thread:1",
                    "active_thread_id": "thread:1",
                },
            )

            for variant in ("say that again simply", "restate that", "say it plainly"):
                with self.subTest(variant=variant):
                    reply = _respond_to_free_text(variant, config_dir=config_dir)
                    self.assertEqual("conversation_restate", reply.intent)
                    self.assertTrue(
                        "plain version" in reply.text.lower() or "short version" in reply.text.lower()
                    )
                    self.assertNotIn("live recommendation thread", reply.text.lower())

    def test_paraphrase_benchmark_ambiguous_turns_clarify_instead_of_generic_fallback(self) -> None:
        for variant in (
            "the thing is not working",
            "help me with it",
            "what about that one",
            "run that",
            "what about that repo",
        ):
            with self.subTest(variant=variant):
                reply = _respond_to_free_text(variant)
                self.assertEqual("clarify", reply.intent)
                self.assertIn("do you mean", reply.text.lower())
                self.assertNotIn("not fully sure what you mean yet", reply.text.lower())

    def test_paraphrase_benchmark_active_repo_explanation_variants_stay_direct(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            _seed_stale_recommendation_thread(config_dir)

            for variant in (
                "what do you mean by active repo",
                "what does active repo mean",
                "what do you mean you still have whisper as active repo",
                "mean by active repo",
            ):
                with self.subTest(variant=variant):
                    reply = _respond_to_free_text(variant, config_dir=config_dir)
                    self.assertEqual("repo_active_explanation", reply.intent)
                    self.assertIn("active repo", reply.text.lower())
                    self.assertNotIn("second-best repo", reply.text.lower())

    def test_paraphrase_benchmark_truly_opaque_turns_use_contextual_clarification(self) -> None:
        for variant in (
            "blargle wobble maybe",
            "the thing with the stuff",
            "uhh something is off somehow",
        ):
            with self.subTest(variant=variant):
                reply = _respond_to_free_text(variant)
                self.assertEqual("clarify", reply.intent)
                self.assertEqual("clarify", reply.route_family)
                self.assertIn("do you mean", reply.text.lower())
                self.assertNotIn("/help", reply.text.lower())

    def test_paraphrase_benchmark_route_consistency_across_critical_intents(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            _seed_stale_recommendation_thread(config_dir)

            benchmark_cases = {
                "rapport": (
                    "How u doing?",
                    "how ya doing",
                    "how's it going",
                    "how are things going",
                ),
                "capability": (
                    "what can your help me with?",
                    "what can u help me with",
                    "what all can you help me with",
                    "what exactly can you help me do here",
                ),
                "conversation_repair": (
                    "Do you understand what i am asking?",
                    "that's not what i asked",
                    "you are not understanding me",
                    "that is not what i meant",
                ),
                "conversation_restate": (
                    "say that again simply",
                    "restate that",
                    "say it plainly",
                    "repeat that in simple words",
                ),
                "clarify": (
                    "the thing is not working",
                    "help me with it",
                    "what about that one",
                    "run that",
                ),
                "repo_active": (
                    "do i have an active repo in my system?",
                    "which repo is active",
                    "what repo are you currently tracking",
                ),
                "repo_lifecycle_active": (
                    "which repo do you currently have active",
                    "is there a active repo which we worked previously",
                ),
                "repo_lifecycle_specific_status": (
                    "do we have whisper installed and active",
                    "is whisper installed and active",
                ),
                "repo_lifecycle_inventory": (
                    "can you show me all repos that we have installed or active",
                    "could you check if we already have an repo in process or installed or active",
                ),
                "repo_live_sessions": (
                    "show me live repo sessions",
                    "show me actually running repos right now",
                ),
                "repo_live_sessions_vm": (
                    "show me live repo sessions on vm",
                ),
                "repo_live_sessions_docker": (
                    "show me live repos in docker",
                ),
                "repo_live_sessions_cloud": (
                    "show me actually running repos on cloud",
                ),
                "repo_active_explanation": (
                    "what do you mean by active repo",
                    "what does active repo mean",
                    "what do you mean you still have whisper as active repo",
                    "mean by active repo",
                ),
            }

            total_variants = 0
            for expected_intent, variants in benchmark_cases.items():
                for variant in variants:
                    total_variants += 1
                    with self.subTest(intent=expected_intent, variant=variant):
                        reply = _respond_to_free_text(variant, config_dir=config_dir)
                        self.assertEqual(expected_intent, reply.intent)
            self.assertGreaterEqual(total_variants, 28)

    def test_paraphrase_benchmark_live_repo_sessions_stay_runtime_grounded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            _seed_multi_target_live_sessions(config_dir)

            benchmark_cases = {
                "repo_live_sessions": (
                    "show me live repo sessions",
                    "show me actually running repos right now",
                ),
                "repo_live_sessions_local": (
                    "show me live repos on local",
                ),
                "repo_live_sessions_vm": (
                    "show me live repo sessions on vm",
                ),
                "repo_live_sessions_docker": (
                    "show me live repos in docker",
                ),
                "repo_live_sessions_cloud": (
                    "show me actually running repos on cloud",
                ),
            }

            for expected_intent, variants in benchmark_cases.items():
                for variant in variants:
                    with self.subTest(intent=expected_intent, variant=variant):
                        reply = _respond_to_free_text(variant, config_dir=config_dir)
                        self.assertEqual(expected_intent, reply.intent)
                        self.assertIn("live repo sessions", reply.text.lower())
                        self.assertNotIn("installed, active, or otherwise prepared", reply.text.lower())

    def test_transcript_benchmark_local_repo_transitions_keep_local_repo_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            local_path = config_dir / "projects" / "whisper"
            initialize_state_store(config_dir).upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path=str(local_path),
                execution_target="local",
                status="running",
                summary="whisper running locally",
                metadata={
                    "repo_name": "whisper",
                    "install_location": str(local_path),
                    "run_command": ".venv/bin/python -m whisper /tmp/example.mp3 --model base",
                    "access_hint": "Use the managed project directory for the next shell step.",
                },
            )

            recent_turns: list[ConversationTurn] = []
            recent_replies: list[FreeTextReply] = []

            inventory = _run_transcript_step(
                user_text="show me live repos on local",
                config_dir=config_dir,
                recent_turns=recent_turns,
                recent_replies=recent_replies,
            )
            path_reply = _run_transcript_step(
                user_text="where is it installed",
                config_dir=config_dir,
                recent_turns=recent_turns,
                recent_replies=recent_replies,
            )
            run_reply = _run_transcript_step(
                user_text="can you run it",
                config_dir=config_dir,
                recent_turns=recent_turns,
                recent_replies=recent_replies,
            )
            access_reply = _run_transcript_step(
                user_text="open it",
                config_dir=config_dir,
                recent_turns=recent_turns,
                recent_replies=recent_replies,
            )

            self.assertEqual("repo_live_sessions_local", inventory.intent)
            self.assertEqual("repo_path_show", path_reply.intent)
            self.assertIn(str(local_path), path_reply.text)
            self.assertEqual("repo_run", run_reply.intent)
            self.assertIn("whisper", run_reply.text.lower())
            self.assertEqual("repo_access", access_reply.intent)
            self.assertIn("whisper", access_reply.text.lower())
            self.assertIn("start point", access_reply.text.lower())

    def test_transcript_benchmark_vm_repo_transitions_keep_vm_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            vm_path = "/home/ubuntu/private-gpt"
            initialize_state_store(config_dir).upsert_repo_state(
                repo_key="https://example.com/private-gpt",
                repo_url="https://example.com/private-gpt",
                repo_path=vm_path,
                execution_target="vm",
                vm_name="duckln-vm-1",
                status="running",
                summary="private-gpt running in vm",
                metadata={
                    "repo_name": "private-gpt",
                    "install_location": vm_path,
                    "run_command": "python -m private_gpt",
                    "access_hint": "Use Duckln inside the VM shell for the next step.",
                },
            )

            recent_turns: list[ConversationTurn] = []
            recent_replies: list[FreeTextReply] = []

            inventory = _run_transcript_step(
                user_text="show me live repo sessions on vm",
                config_dir=config_dir,
                recent_turns=recent_turns,
                recent_replies=recent_replies,
            )
            path_reply = _run_transcript_step(
                user_text="where is it located",
                config_dir=config_dir,
                recent_turns=recent_turns,
                recent_replies=recent_replies,
            )
            run_reply = _run_transcript_step(
                user_text="run it",
                config_dir=config_dir,
                recent_turns=recent_turns,
                recent_replies=recent_replies,
            )
            access_reply = _run_transcript_step(
                user_text="access it",
                config_dir=config_dir,
                recent_turns=recent_turns,
                recent_replies=recent_replies,
            )

            self.assertEqual("repo_live_sessions_vm", inventory.intent)
            self.assertEqual("repo_path_show", path_reply.intent)
            self.assertIn(vm_path, path_reply.text)
            self.assertIn("vm duckln-vm-1", path_reply.text.lower())
            self.assertEqual("repo_run", run_reply.intent)
            self.assertIn("private-gpt", run_reply.text.lower())
            self.assertEqual("repo_access", access_reply.intent)
            self.assertIn("vm", access_reply.text.lower())

    def test_transcript_benchmark_cloud_repo_transitions_keep_cloud_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            cloud_path = "/home/duckln/open-webui"
            initialize_state_store(config_dir).upsert_repo_state(
                repo_key="https://example.com/open-webui",
                repo_url="https://example.com/open-webui",
                repo_path=cloud_path,
                execution_target="gcp",
                status="running",
                summary="open-webui ready on GCP",
                metadata={
                    "repo_name": "open-webui",
                    "install_location": cloud_path,
                    "cloud_vendor": "GCP",
                    "cloud_region": "us-central1-a",
                    "cloud_resource_key": "gcp:duckln-gcp:us-central1-a",
                },
            )

            recent_turns: list[ConversationTurn] = []
            recent_replies: list[FreeTextReply] = []

            inventory = _run_transcript_step(
                user_text="show me actually running repos on cloud",
                config_dir=config_dir,
                recent_turns=recent_turns,
                recent_replies=recent_replies,
            )
            path_reply = _run_transcript_step(
                user_text="where is it installed",
                config_dir=config_dir,
                recent_turns=recent_turns,
                recent_replies=recent_replies,
            )
            access_reply = _run_transcript_step(
                user_text="open shell in it",
                config_dir=config_dir,
                recent_turns=recent_turns,
                recent_replies=recent_replies,
            )

            self.assertEqual("repo_live_sessions_cloud", inventory.intent)
            self.assertEqual("repo_path_show", path_reply.intent)
            self.assertIn(cloud_path, path_reply.text)
            self.assertIn("gcp cloud target in us-central1-a", path_reply.text.lower())
            self.assertEqual("repo_access", access_reply.intent)
            self.assertIn("cloud target", access_reply.text.lower())

    def test_transcript_benchmark_docker_repo_transitions_keep_docker_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            docker_path = "/workspace/open-webui"
            initialize_state_store(config_dir).upsert_repo_state(
                repo_key="https://example.com/open-webui",
                repo_url="https://example.com/open-webui",
                repo_path=docker_path,
                execution_target="docker",
                status="interactive",
                summary="open-webui interactive in docker",
                metadata={
                    "repo_name": "open-webui",
                    "install_location": docker_path,
                    "docker_name": "open-webui-stack",
                    "run_command": "docker compose up",
                    "access_hint": "Use Duckln’s terminal pane for the Docker shell.",
                },
            )

            recent_turns: list[ConversationTurn] = []
            recent_replies: list[FreeTextReply] = []

            inventory = _run_transcript_step(
                user_text="show me live repos in docker",
                config_dir=config_dir,
                recent_turns=recent_turns,
                recent_replies=recent_replies,
            )
            path_reply = _run_transcript_step(
                user_text="where is it located",
                config_dir=config_dir,
                recent_turns=recent_turns,
                recent_replies=recent_replies,
            )
            stop_reply = _run_transcript_step(
                user_text="stop it",
                config_dir=config_dir,
                recent_turns=recent_turns,
                recent_replies=recent_replies,
            )
            access_reply = _run_transcript_step(
                user_text="attach to it",
                config_dir=config_dir,
                recent_turns=recent_turns,
                recent_replies=recent_replies,
            )

            self.assertEqual("repo_live_sessions_docker", inventory.intent)
            self.assertEqual("repo_path_show", path_reply.intent)
            self.assertIn(docker_path, path_reply.text)
            self.assertIn("docker container open-webui-stack", path_reply.text.lower())
            self.assertEqual("repo_stop", stop_reply.intent)
            self.assertEqual("repo_access", access_reply.intent)
            self.assertIn("docker", access_reply.text.lower())

    def test_transcript_benchmark_custom_linked_repo_transitions_keep_source_and_run_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            linked_path = config_dir / "linked" / "custom-alpha"
            linked_key = f"linked://local/{linked_path}"
            initialize_state_store(config_dir).upsert_repo_state(
                repo_key=linked_key,
                repo_url="https://github.com/example/custom-alpha",
                repo_path=str(linked_path),
                execution_target="local",
                status="linked",
                summary="custom-alpha linked",
                managed_by_duckln=False,
                metadata={
                    "repo_name": "custom-alpha",
                    "install_location": str(linked_path),
                    "source_repo_url": "https://github.com/example/custom-alpha",
                    "access_hint": "Use the linked project directory for the next shell step.",
                },
            )

            recent_turns: list[ConversationTurn] = []
            recent_replies: list[FreeTextReply] = []

            status_reply = _run_transcript_step(
                user_text="do we have custom-alpha repo setup",
                config_dir=config_dir,
                recent_turns=recent_turns,
                recent_replies=recent_replies,
            )
            source_reply = _run_transcript_step(
                user_text="what is its github path",
                config_dir=config_dir,
                recent_turns=recent_turns,
                recent_replies=recent_replies,
            )
            run_reply = _run_transcript_step(
                user_text="run it",
                config_dir=config_dir,
                recent_turns=recent_turns,
                recent_replies=recent_replies,
            )
            access_reply = _run_transcript_step(
                user_text="open it",
                config_dir=config_dir,
                recent_turns=recent_turns,
                recent_replies=recent_replies,
            )

            self.assertIn(status_reply.intent, {"repo_lifecycle_specific_status", "repo_status"})
            self.assertEqual("repo_source_show", source_reply.intent)
            self.assertIn("https://github.com/example/custom-alpha", source_reply.text)
            self.assertEqual("repo_run", run_reply.intent)
            self.assertIn("does not have a verified run command", run_reply.text.lower())
            self.assertEqual("repo_access", access_reply.intent)
            self.assertIn("custom-alpha", access_reply.text.lower())

    def test_long_mixed_session_benchmark_preserves_turn_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            initialize_managed_memory_state(config_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-10",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 10,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            },
                            {
                                "name": "private-gpt",
                                "repo_url": "https://example.com/private-gpt",
                                "stars": 9,
                                "description": "Local document chat.",
                                "category": "LLM",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            },
                            {
                                "name": "fastchat",
                                "repo_url": "https://example.com/fastchat",
                                "stars": 8,
                                "description": "Chat serving stack.",
                                "category": "LLM",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            },
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            initialize_state_store(config_dir).upsert_repo_state(
                repo_key="https://example.com/whisper",
                status="ready",
                summary="whisper is ready",
                repo_path="/tmp/whisper",
                repo_url="https://example.com/whisper",
                metadata={"repo_name": "whisper", "install_location": "/tmp/whisper"},
            )

            probe = _benchmark_probe()
            recent_turns: list[ConversationTurn] = []
            recent_replies: list[FreeTextReply] = []

            def step(user_text: str) -> FreeTextReply:
                reply = _respond_to_free_text(
                    user_text,
                    config_dir=config_dir,
                    system_probe=probe,
                    recent_turns=tuple(recent_turns),
                    recent_replies=tuple(recent_replies),
                )
                recent_turns.append(ConversationTurn(role="user", content=user_text))
                recent_turns.append(ConversationTurn(role="assistant", content=reply.text))
                recent_replies.append(reply)
                return reply

            greeting = step("Hi there")
            capability = step("what can you help me with?")
            recommendation = step("which repo do you recommend for my system")
            repair = step("sorry i did not understand")
            restate = step("say that again simply")
            active = step("do i have an active repo in my system?")
            removal = step("help me uninstall whisper")

            self.assertEqual("greeting", greeting.intent)
            self.assertEqual("capability", capability.intent)
            self.assertTrue(recommendation.intent.startswith("repo_recommendation"))
            self.assertEqual("conversation_repair", repair.intent)
            self.assertEqual("conversation_restate", restate.intent)
            self.assertEqual("repo_active", active.intent)
            self.assertEqual("repo_removal", removal.intent)
            self.assertNotIn("second-best repo", repair.text.lower())
            self.assertNotIn("live recommendation thread", restate.text.lower())
            self.assertIn("active repo", active.text.lower())
            self.assertIn("remove whisper", removal.text.lower())

    def test_long_mixed_session_benchmark_preserves_clarify_then_fallback_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            _seed_stale_recommendation_thread(config_dir)

            probe = _benchmark_probe()
            recent_turns: list[ConversationTurn] = []
            recent_replies: list[FreeTextReply] = []

            def step(user_text: str) -> FreeTextReply:
                reply = _respond_to_free_text(
                    user_text,
                    config_dir=config_dir,
                    system_probe=probe,
                    recent_turns=tuple(recent_turns),
                    recent_replies=tuple(recent_replies),
                )
                recent_turns.append(ConversationTurn(role="user", content=user_text))
                recent_turns.append(ConversationTurn(role="assistant", content=reply.text))
                recent_replies.append(reply)
                return reply

            social = step("How u doing?")
            ambiguous = step("the thing is not working")
            repair = step("that is not what i meant")
            active = step("which repo is active")
            opaque = step("blargle wobble maybe")

            self.assertEqual("rapport", social.intent)
            self.assertEqual("clarify", ambiguous.intent)
            self.assertEqual("conversation_repair", repair.intent)
            self.assertEqual("repo_active", active.intent)
            self.assertEqual("clarify", opaque.intent)
            self.assertNotIn("recommendation thread", social.text.lower())
            self.assertIn("do you mean", ambiguous.text.lower())
            self.assertNotIn("second-best repo", repair.text.lower())
            self.assertIn("active repo", active.text.lower())
            self.assertIn("do you mean", opaque.text.lower())

    def test_long_mixed_session_benchmark_repo_lifecycle_questions_ignore_recommendation_shortlist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            _seed_stale_recommendation_thread(config_dir)

            probe = _benchmark_probe()
            recent_turns: list[ConversationTurn] = []
            recent_replies: list[FreeTextReply] = []

            def step(user_text: str) -> FreeTextReply:
                reply = _respond_to_free_text(
                    user_text,
                    config_dir=config_dir,
                    system_probe=probe,
                    recent_turns=tuple(recent_turns),
                    recent_replies=tuple(recent_replies),
                )
                recent_turns.append(ConversationTurn(role="user", content=user_text))
                recent_turns.append(ConversationTurn(role="assistant", content=reply.text))
                recent_replies.append(reply)
                return reply

            recommendation = step("which repo do you recommend for my system")
            inventory = step("Could you check if we already have an repo in process or installed or active?")
            previous = step("is there a active repo which we worked previously?")
            specific = step("Do we have whisper installed and active")
            all_repos = step("can you show me all repos that we have installed or active?")

            self.assertTrue(recommendation.intent.startswith("repo_recommendation"))

            self.assertEqual("repo_lifecycle_inventory", inventory.intent)
            self.assertIn("tracked", inventory.text.lower())
            self.assertNotIn("private-gpt is first", inventory.text.lower())
            self.assertNotIn("full shortlist", inventory.text.lower())

            self.assertEqual("repo_lifecycle_active", previous.intent)
            self.assertIn("active repo", previous.text.lower())
            self.assertNotIn("second-best repo", previous.text.lower())

            self.assertEqual("repo_lifecycle_specific_status", specific.intent)
            self.assertIn("whisper", specific.text.lower())
            self.assertNotIn("private-gpt", specific.text.lower())
            self.assertNotIn("full shortlist", specific.text.lower())

            self.assertEqual("repo_lifecycle_inventory", all_repos.intent)
            self.assertIn("tracked", all_repos.text.lower())
            self.assertNotIn("compare the top two", all_repos.text.lower())
            self.assertNotIn("private-gpt is first", all_repos.text.lower())

    def test_long_mixed_session_benchmark_vm_and_removal_questions_stay_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            _seed_stale_recommendation_thread(config_dir)

            probe = _benchmark_probe()
            recent_turns: list[ConversationTurn] = []
            recent_replies: list[FreeTextReply] = []

            def step(user_text: str) -> FreeTextReply:
                reply = _respond_to_free_text(
                    user_text,
                    config_dir=config_dir,
                    system_probe=probe,
                    recent_turns=tuple(recent_turns),
                    recent_replies=tuple(recent_replies),
                )
                recent_turns.append(ConversationTurn(role="user", content=user_text))
                recent_turns.append(ConversationTurn(role="assistant", content=reply.text))
                recent_replies.append(reply)
                return reply

            recommendation = step("which repo do you recommend for my system")
            inventory = step("Do we have any repos already installed?")
            removal = step("can you help uninstall whisper?")
            vm_recommendation = step("Which repo do yo recommend to put in VM?")
            vm_capability = step("Do you have the skills deploy VM?")
            vm_definition = step("Do you know what is VM?")

            self.assertTrue(recommendation.intent.startswith("repo_recommendation"))
            self.assertEqual("repo_lifecycle_inventory", inventory.intent)
            self.assertNotIn("private-gpt is first", inventory.text.lower())
            self.assertEqual("repo_removal", removal.intent)
            self.assertEqual("remove_repo", removal.action)
            self.assertIn("confirm", removal.text.lower())
            self.assertEqual("vm_repo_recommendation", vm_recommendation.intent)
            self.assertIn("linux isolation", vm_recommendation.text.lower())
            self.assertNotIn("private-gpt is first", vm_recommendation.text.lower())
            self.assertEqual("vm_capability", vm_capability.intent)
            self.assertIn("vm", vm_capability.text.lower())
            self.assertNotIn("second-best repo", vm_capability.text.lower())
            self.assertEqual("vm_definition", vm_definition.intent)
            self.assertIn("multipass", vm_definition.text.lower())
            self.assertNotIn("private-gpt", vm_definition.text.lower())

    def test_long_mixed_session_benchmark_inventory_launch_typo_and_vm_top_n_stay_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            _seed_stale_recommendation_thread(config_dir)

            probe = _benchmark_probe()
            recent_turns: list[ConversationTurn] = []
            recent_replies: list[FreeTextReply] = []

            def step(user_text: str) -> FreeTextReply:
                reply = _respond_to_free_text(
                    user_text,
                    config_dir=config_dir,
                    system_probe=probe,
                    recent_turns=tuple(recent_turns),
                    recent_replies=tuple(recent_replies),
                )
                recent_turns.append(ConversationTurn(role="user", content=user_text))
                recent_turns.append(ConversationTurn(role="assistant", content=reply.text))
                recent_replies.append(reply)
                return reply

            inventory = step("can you tell me repos that are already setup and ready")
            launch = step("can you launch whisper")
            launch_how = step("how can we launch whisper")
            launch_typo_path = step("how can we launch whiper /tmp/whisper")
            removal_typo = step("can you help uninstall whiper")
            vm_top_n = step("can you provide VM friendly choice repos give me top 3")

            self.assertEqual("repo_lifecycle_inventory", inventory.intent)
            self.assertIn("whisper", inventory.text.lower())
            self.assertNotIn("second-best repo", inventory.text.lower())
            self.assertEqual("repo_run", launch.intent)
            self.assertEqual("run_repo", launch.action)
            self.assertNotIn("compare the top two", launch.text.lower())
            self.assertEqual("repo_run", launch_how.intent)
            self.assertEqual("repo_run", launch_typo_path.intent)
            self.assertNotIn("full shortlist", launch_typo_path.text.lower())
            self.assertEqual("repo_removal", removal_typo.intent)
            self.assertEqual("remove_repo", removal_typo.action)
            self.assertIn("confirm", removal_typo.text.lower())
            self.assertEqual("vm_repo_recommendation", vm_top_n.intent)
            self.assertIn("vm-first", vm_top_n.text.lower())
            self.assertNotIn("private-gpt is first", vm_top_n.text.lower())


if __name__ == "__main__":
    unittest.main()
