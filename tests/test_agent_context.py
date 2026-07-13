"""Tests for shared agent context and repo knowledge retrieval."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from duckln.agent_context import AgentContextService, build_hardware_reasoning
from agent.probe import GpuProbeState, SystemProbe
from state.access import clear_memory_scope, write_followup_state, write_workflow_state
from state.store import initialize_state_store
from state.repo_catalog import RepoCatalogRecord


class AgentContextServiceTest(unittest.TestCase):
    def test_resolve_repo_knowledge_detects_stack_runtime_and_auth_requirements(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            project_dir = config_dir / "go-app"
            project_dir.mkdir(parents=True, exist_ok=True)
            (project_dir / "go.mod").write_text("module example.com/go-app\n\ngo 1.22\n", encoding="utf-8")
            (project_dir / "main.go").write_text("package main\nfunc main(){}\n", encoding="utf-8")
            (project_dir / ".env.example").write_text("OPENAI_API_KEY=\n", encoding="utf-8")
            repo = RepoCatalogRecord(
                "go-app",
                "https://example.com/go-app",
                100,
                "Go service.",
                "Custom",
                "Go",
                "2026-04-01",
            )

            knowledge = AgentContextService().resolve_repo_knowledge(
                repo=repo,
                config_dir=config_dir,
                project_dir=project_dir,
            )

            self.assertEqual("go", knowledge.stack_family)
            self.assertEqual("interactive_app", knowledge.runtime_style)
            self.assertTrue(any(item.env_var == "OPENAI_API_KEY" for item in knowledge.auth_requirements))

    def test_load_recommendation_memory_returns_only_recommendation_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            service = AgentContextService()
            repo = RepoCatalogRecord(
                "whisper",
                "https://github.com/openai/whisper",
                75000,
                "Speech recognition.",
                "Audio",
                "Python",
                "2026-04-01",
            )

            service.record_recommendation_context(
                config_dir=config_dir,
                repo=repo,
                reason="it fits Apple Silicon without leaning on CUDA",
                alternatives=("open-webui", "speechbrain"),
                caveat="larger models still want more RAM",
                system_hint="Apple Silicon Mac",
            )

            payload = service.load_recommendation_memory(config_dir=config_dir)

            self.assertIsNotNone(payload)
            assert payload is not None
            self.assertEqual("whisper", payload["repo_name"])
            self.assertEqual(["open-webui", "speechbrain"], payload["alternatives"])

    def test_build_memory_context_reads_persisted_recommendation_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            service = AgentContextService()
            repo = RepoCatalogRecord(
                "whisper",
                "https://github.com/openai/whisper",
                75000,
                "Speech recognition.",
                "Audio",
                "Python",
                "2026-04-01",
            )

            service.record_recommendation_context(
                config_dir=config_dir,
                repo=repo,
                reason="it fits Apple Silicon without leaning on CUDA",
                alternatives=("open-webui", "speechbrain"),
                caveat="larger models still want more RAM",
                system_hint="Apple Silicon Mac",
            )

            memory = service.build_memory_context(config_dir=config_dir, repo_key=repo.repo_url)

            self.assertIsNotNone(memory.recommendation_context)
            assert memory.recommendation_context is not None
            self.assertEqual("whisper", memory.recommendation_context["repo_name"])
            self.assertEqual(["open-webui", "speechbrain"], memory.recommendation_context["alternatives"])

    def test_build_memory_context_includes_promoted_heuristics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            store = initialize_state_store(config_dir)
            store.upsert_promoted_heuristic(
                heuristic_key="conversation:feedback_style",
                family="conversation",
                subject_key="feedback_style",
                summary="Prefer more direct and less mechanical phrasing in greetings and capability replies.",
                confidence=0.8,
                metadata={"intent": "feedback_style"},
            )

            memory = AgentContextService().build_memory_context(config_dir=config_dir)

            self.assertEqual(1, len(memory.promoted_heuristics))
            self.assertEqual("conversation", memory.promoted_heuristics[0]["family"])

    def test_build_memory_context_includes_followup_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            write_followup_state(
                config_dir,
                {
                    "pending_repo_name": "whisper",
                    "pending_next_action": "inspect_requirements",
                },
            )

            memory = AgentContextService().build_memory_context(config_dir=config_dir)

            self.assertIsNotNone(memory.followup_state)
            assert memory.followup_state is not None
            self.assertEqual("whisper", memory.followup_state["pending_repo_name"])
            self.assertEqual("inspect_requirements", memory.followup_state["pending_next_action"])

    def test_load_recent_thread_summary_returns_only_session_and_followup_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            store = initialize_state_store(config_dir)
            store.upsert_managed_memory(
                memory_key="session:alpha",
                memory_kind="session",
                relative_path="sessions/alpha.md",
                title="alpha",
                content="Kept the repo setup path bounded.",
            )
            write_followup_state(
                config_dir,
                {
                    "pending_repo_name": "whisper",
                    "pending_next_action": "inspect_requirements",
                },
            )

            summary = AgentContextService().load_recent_thread_summary(config_dir=config_dir)

            self.assertEqual("Kept the repo setup path bounded.", summary.session_summary)
            self.assertIsNotNone(summary.followup_state)
            assert summary.followup_state is not None
            self.assertEqual("whisper", summary.followup_state["pending_repo_name"])

    def test_load_repo_memory_notes_returns_only_repo_knowledge_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            repo_key = "https://github.com/openai/whisper"
            initialize_state_store(config_dir).upsert_repo_knowledge(
                repo_key=repo_key,
                repo_name="whisper",
                repo_url=repo_key,
                source="local",
                summary="whisper knowledge (local).",
            )

            notes = AgentContextService().load_repo_memory_notes(config_dir=config_dir, repo_key=repo_key)

            self.assertEqual(("whisper knowledge (local).",), notes)

    def test_load_repo_state_snapshot_returns_tracked_state_without_repo_knowledge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            repo = RepoCatalogRecord(
                "whisper",
                "https://github.com/openai/whisper",
                75000,
                "Speech recognition.",
                "Audio",
                "Python",
                "2026-04-01",
            )
            initialize_state_store(config_dir).upsert_repo_state(
                repo_key=repo.repo_url,
                repo_url=repo.repo_url,
                repo_path="/tmp/whisper",
                status="ready",
                summary="whisper ready",
                metadata={
                    "repo_name": "whisper",
                    "install_location": "/tmp/whisper",
                    "access_hint": "cd /tmp/whisper",
                    "run_command": "python app.py",
                },
            )

            snapshot = AgentContextService().load_repo_state_snapshot(config_dir=config_dir, repo=repo)

            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            self.assertEqual("whisper", snapshot.repo_name)
            self.assertEqual("ready", snapshot.status)
            self.assertEqual("/tmp/whisper", snapshot.install_location)
            self.assertEqual("cd /tmp/whisper", snapshot.access_hint)
            self.assertEqual("python app.py", snapshot.run_command)
            self.assertEqual("local", snapshot.execution_target)
            self.assertTrue(snapshot.managed_by_duckln)

    def test_load_repo_inventory_snapshot_returns_bounded_inventory_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            store = initialize_state_store(config_dir)
            store.upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path="/tmp/whisper",
                status="ready",
                summary="whisper ready",
                metadata={"repo_name": "whisper", "install_location": "/tmp/whisper"},
            )
            store.upsert_repo_state(
                repo_key="https://example.com/open-webui",
                repo_url="https://example.com/open-webui",
                repo_path="/tmp/open-webui",
                status="running",
                summary="open-webui running",
                metadata={"repo_name": "open-webui", "install_location": "/tmp/open-webui"},
            )

            snapshot = AgentContextService().load_repo_inventory_snapshot(config_dir=config_dir)

            self.assertEqual(2, len(snapshot))
            pairs = {(entry.repo_name, entry.status) for entry in snapshot}
            self.assertIn(("whisper", "ready"), pairs)
            self.assertIn(("open-webui", "running"), pairs)
            self.assertTrue(all(entry.execution_target == "local" for entry in snapshot))

    def test_load_workflow_state_combines_active_repo_issue_and_pending_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            repo = RepoCatalogRecord(
                "whisper",
                "https://github.com/openai/whisper",
                75000,
                "Speech recognition.",
                "Audio",
                "Python",
                "2026-04-01",
            )
            initialize_state_store(config_dir).upsert_repo_state(
                repo_key=repo.repo_url,
                repo_url=repo.repo_url,
                repo_path="/tmp/whisper",
                status="failed",
                summary="Supervisor agent saw a run issue for whisper.",
                metadata={
                    "repo_name": "whisper",
                    "install_location": "/tmp/whisper",
                    "run_command": ".venv/bin/python -m whisper --help",
                    "last_run_result": "failed",
                    "last_blocker": "Supervisor agent saw a run issue for whisper.",
                },
            )
            write_workflow_state(
                config_dir,
                {
                    "active_objective_id": f"runtime_repair:{repo.repo_url}",
                    "active_objective_kind": "runtime_repair",
                    "active_objective_repo_key": repo.repo_url,
                    "active_objective_repo_name": "whisper",
                    "active_objective_status": "needs_user_decision",
                    "active_objective_goal": "Get whisper running.",
                    "active_objective_execution_target": "local",
                    "active_objective_runtime_command": ".venv/bin/python -m whisper serve",
                    "active_objective_last_blocker": "ffmpeg missing",
                    "active_objective_attempt_count": 2,
                    "active_objective_max_attempts": 5,
                    "active_objective_requires_user_decision": True,
                    "active_objective_resume_hint": "Last time Duckln was fixing whisper.",
                    "active_objective_started_at": "2026-04-23T10:00:00+00:00",
                    "active_objective_updated_at": "2026-04-23T10:05:00+00:00",
                    "active_runtime_status": "running",
                    "active_runtime_command": ".venv/bin/python -m whisper serve",
                    "active_runtime_command_kind": "start",
                    "active_runtime_repo_key": repo.repo_url,
                    "active_runtime_repo_name": "whisper",
                    "active_runtime_cwd": "/tmp/whisper",
                    "active_runtime_pid": 321,
                    "active_runtime_log_path": "/tmp/whisper.log",
                    "active_runtime_execution_target": "local",
                    "active_runtime_attach_hint": "Use the managed repo directory.",
                    "active_runtime_logs_hint": "tail -n 40 /tmp/whisper.log",
                    "active_runtime_stop_hint": "Duckln can stop whisper from here.",
                    "pending_destructive_action": "remove_repo",
                    "pending_destructive_repo_key": repo.repo_url,
                    "pending_destructive_repo_name": "whisper",
                    "pending_destructive_path": "/tmp/whisper",
                    "pending_destructive_target": "local machine",
                },
            )

            active = AgentContextService().load_active_repo_snapshot(config_dir=config_dir)
            workflow = AgentContextService().load_workflow_state(config_dir=config_dir, active_repo_snapshot=active)

            self.assertIsNotNone(workflow)
            assert workflow is not None
            self.assertEqual("whisper", workflow.repo_name)
            self.assertEqual("run_issue", workflow.active_issue_kind)
            self.assertEqual("remove_repo", workflow.pending_destructive_action)
            self.assertEqual(".venv/bin/python -m whisper --help", workflow.run_command)
            self.assertEqual("/tmp/whisper", workflow.install_location)
            self.assertEqual("failed", workflow.tracked_status)
            self.assertEqual("runtime_repair", workflow.active_objective_kind)
            self.assertEqual(f"runtime_repair:{repo.repo_url}", workflow.active_objective_id)
            self.assertEqual("whisper", workflow.active_objective_repo_name)
            self.assertEqual(2, workflow.active_objective_attempt_count)
            self.assertEqual(5, workflow.active_objective_max_attempts)
            self.assertTrue(workflow.active_objective_requires_user_decision)
            self.assertEqual("running", workflow.active_runtime_status)
            self.assertEqual(321, workflow.active_runtime_pid)
            self.assertEqual("/tmp/whisper.log", workflow.active_runtime_log_path)

    def test_load_active_repo_snapshot_returns_latest_tracked_repo_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            store = initialize_state_store(config_dir)
            store.upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path="/tmp/whisper",
                status="ready",
                summary="whisper ready",
                metadata={"repo_name": "whisper"},
            )
            store.upsert_repo_state(
                repo_key="https://example.com/open-webui",
                repo_url="https://example.com/open-webui",
                repo_path="/tmp/open-webui",
                status="running",
                summary="open-webui running",
                metadata={"repo_name": "open-webui"},
            )

            snapshot = AgentContextService().load_active_repo_snapshot(config_dir=config_dir)

            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            self.assertEqual("open-webui", snapshot.repo_name)
            self.assertEqual("running", snapshot.status)
            self.assertEqual("local", snapshot.execution_target)

    def test_load_repo_inventory_snapshot_can_filter_local_vs_vm_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            store = initialize_state_store(config_dir)
            store.upsert_repo_state(
                repo_key="https://example.com/local-whisper",
                repo_url="https://example.com/local-whisper",
                repo_path="/tmp/whisper",
                execution_target="local",
                status="ready",
                summary="local whisper ready",
                metadata={"repo_name": "whisper"},
            )
            store.upsert_repo_state(
                repo_key="https://example.com/vm-private-gpt",
                repo_url="https://example.com/vm-private-gpt",
                repo_path="/home/ubuntu/private-gpt",
                execution_target="vm",
                vm_name="duckln-vm-1",
                status="ready",
                summary="vm private-gpt ready",
                metadata={"repo_name": "private-gpt"},
            )

            local_snapshot = AgentContextService().load_repo_inventory_snapshot(
                config_dir=config_dir,
                execution_target="local",
            )
            vm_snapshot = AgentContextService().load_repo_inventory_snapshot(
                config_dir=config_dir,
                execution_target="vm",
                vm_name="duckln-vm-1",
            )

            self.assertEqual(("whisper",), tuple(entry.repo_name for entry in local_snapshot))
            self.assertEqual(("private-gpt",), tuple(entry.repo_name for entry in vm_snapshot))

    def test_load_live_repo_sessions_snapshot_returns_only_running_or_interactive_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            store = initialize_state_store(config_dir)
            store.upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path="/tmp/whisper",
                execution_target="local",
                status="ready",
                summary="whisper ready",
                metadata={"repo_name": "whisper"},
            )
            store.upsert_repo_state(
                repo_key="https://example.com/open-webui",
                repo_url="https://example.com/open-webui",
                repo_path="/tmp/open-webui",
                execution_target="docker",
                status="interactive",
                summary="open-webui interactive",
                metadata={"repo_name": "open-webui", "docker_name": "open-webui-stack"},
            )
            write_workflow_state(
                config_dir,
                {
                    "active_runtime_repo_key": "https://example.com/private-gpt",
                    "active_runtime_repo_name": "private-gpt",
                    "active_runtime_status": "running",
                    "active_runtime_execution_target": "vm",
                    "active_runtime_vm_name": "duckln-vm-1",
                    "active_runtime_cwd": "/home/ubuntu/private-gpt",
                    "active_runtime_attach_hint": "Attach through the VM shell.",
                },
            )

            snapshot = AgentContextService().load_live_repo_sessions_snapshot(config_dir=config_dir)

            self.assertEqual(2, len(snapshot))
            names = {entry.repo_name for entry in snapshot}
            self.assertEqual({"open-webui", "private-gpt"}, names)
            self.assertNotIn("whisper", names)

    def test_load_active_repo_snapshot_can_filter_vm_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            store = initialize_state_store(config_dir)
            store.upsert_repo_state(
                repo_key="https://example.com/local-whisper",
                repo_url="https://example.com/local-whisper",
                repo_path="/tmp/whisper",
                execution_target="local",
                status="ready",
                summary="local whisper ready",
                metadata={"repo_name": "whisper"},
            )
            store.upsert_repo_state(
                repo_key="https://example.com/vm-private-gpt",
                repo_url="https://example.com/vm-private-gpt",
                repo_path="/home/ubuntu/private-gpt",
                execution_target="vm",
                vm_name="duckln-vm-1",
                status="running",
                summary="vm private-gpt running",
                metadata={"repo_name": "private-gpt"},
            )

            snapshot = AgentContextService().load_active_repo_snapshot(
                config_dir=config_dir,
                execution_target="vm",
                vm_name="duckln-vm-1",
            )

            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            self.assertEqual("private-gpt", snapshot.repo_name)
            self.assertEqual("vm", snapshot.execution_target)
            self.assertEqual("duckln-vm-1", snapshot.vm_name)

    def test_resolve_repo_knowledge_prefers_local_repo_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            project_dir = config_dir / "projects" / "whisper"
            project_dir.mkdir(parents=True, exist_ok=True)
            (project_dir / "README.md").write_text(
                "# Whisper\n\nRequires ffmpeg.\n\n`python -m venv .venv`\n`pip install -r requirements.txt`\n",
                encoding="utf-8",
            )
            (project_dir / "requirements.txt").write_text("openai-whisper\nffmpeg-python\n", encoding="utf-8")

            service = AgentContextService(remote_fetcher=lambda _repo: (_ for _ in ()).throw(AssertionError("remote fetch should not be used")))
            context = service.resolve_repo_knowledge(
                repo=RepoCatalogRecord(
                    "whisper",
                    "https://github.com/openai/whisper",
                    75000,
                    "Speech recognition.",
                    "Audio",
                    "Python",
                    "2026-04-01",
                ),
                config_dir=config_dir,
                project_dir=project_dir,
            )

            self.assertEqual("local", context.source)
            self.assertIn("Python", context.required_tools)
            self.assertIn("ffmpeg", tuple(tool.lower() for tool in context.required_tools))
            self.assertIn("8-16 GB RAM", context.summary)

    def test_resolve_repo_knowledge_uses_cached_record_when_local_repo_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            store = initialize_state_store(config_dir)
            store.upsert_repo_knowledge(
                repo_key="https://github.com/openai/whisper",
                repo_name="whisper",
                repo_url="https://github.com/openai/whisper",
                source="remote",
                summary="whisper knowledge (remote). requirements: a few CPU cores; 8-16 GB RAM; GPU optional.",
                cpu_profile="a few CPU cores",
                ram_profile="8-16 GB RAM",
                gpu_profile="GPU optional",
                freshness_checked_at="2099-01-01T00:00:00+00:00",
            )

            service = AgentContextService(remote_fetcher=lambda _repo: (_ for _ in ()).throw(AssertionError("remote fetch should not be used")))
            context = service.resolve_repo_knowledge(
                repo=RepoCatalogRecord(
                    "whisper",
                    "https://github.com/openai/whisper",
                    75000,
                    "Speech recognition.",
                    "Audio",
                    "Python",
                    "2026-04-01",
                ),
                config_dir=config_dir,
                project_dir=config_dir / "projects" / "missing-whisper",
            )

            self.assertEqual("remote", context.source)
            self.assertEqual("8-16 GB RAM", context.ram_profile)

    def test_resolve_repo_knowledge_falls_back_to_remote_targeted_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            service = AgentContextService(
                remote_fetcher=lambda _repo: {
                    "README.md": "# open-webui\n\nRun with Docker or Python.",
                    "Dockerfile": "FROM python:3.11",
                }
            )

            context = service.resolve_repo_knowledge(
                repo=RepoCatalogRecord(
                    "open-webui",
                    "https://github.com/open-webui/open-webui",
                    100000,
                    "Local model UI.",
                    "LLM",
                    "Python",
                    "2026-04-01",
                ),
                config_dir=config_dir,
                project_dir=config_dir / "projects" / "missing-open-webui",
            )

            self.assertEqual("remote", context.source)
            self.assertIn("Docker", context.required_tools)
            self.assertIn("open-webui knowledge", context.summary)
            stored = initialize_state_store(config_dir).get_repo_knowledge("https://github.com/open-webui/open-webui")
            self.assertIsNotNone(stored)

    def test_repo_knowledge_infers_runtime_metadata_used_by_hardware_reasoning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            service = AgentContextService(remote_fetcher=lambda _repo: {})
            context = service.resolve_repo_knowledge(
                repo=RepoCatalogRecord(
                    "ComfyUI",
                    "https://github.com/Comfy-Org/ComfyUI",
                    100000,
                    "Diffusion graph UI.",
                    "Stable Diffusion",
                    "Python/PyTorch",
                    "2026-04-01",
                ),
                config_dir=config_dir,
                project_dir=config_dir / "projects" / "missing-comfy",
            )
            self.assertIn("lower_bound_ram_gib", context.metadata)
            self.assertIn("comfortable_ram_gib", context.metadata)
            self.assertEqual("high", context.metadata.get("cuda_dependence"))

            reasoning = build_hardware_reasoning(
                system_probe=SystemProbe(
                    operating_system="Darwin",
                    architecture="arm64",
                    cpu_logical_cores=8,
                    ram_bytes=8 * 1024**3,
                    disk_free_bytes=64 * 1024**3,
                    python_version="3.11.8",
                    gpu=GpuProbeState(
                        backend="mps",
                        summary="Apple Silicon detected; MPS available.",
                        cuda_capable=False,
                        cuda_available=False,
                        mps_capable=True,
                        mps_available=True,
                    ),
                ),
                repo_knowledge=context,
            )
            self.assertEqual("CUDA-heavy", reasoning.repo_preference)
            self.assertIn(reasoning.practical_effect, {"slow but usable", "likely frustrating", "not recommended"})

    def test_clear_memory_scope_project_removes_repo_knowledge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            store = initialize_state_store(config_dir)
            repo_key = "https://github.com/openai/whisper"
            store.upsert_repo_state(repo_key=repo_key, status="ready", summary="Whisper ready.")
            store.upsert_repo_knowledge(
                repo_key=repo_key,
                repo_name="whisper",
                repo_url=repo_key,
                source="local",
                summary="whisper knowledge (local).",
            )

            result = clear_memory_scope(config_dir, scope="project")

            self.assertTrue(result.cleared)
            self.assertIsNone(initialize_state_store(config_dir).get_repo_knowledge(repo_key))


if __name__ == "__main__":
    unittest.main()
