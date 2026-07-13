"""Tests for repo bring-up foundation."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import tempfile
import unittest
from pathlib import Path

from agent.probe import GpuProbeState, SystemProbe
from duckln.config import resolve_config_paths
from duckln.modes import ControlMode
from duckln.planning import resolve_repo_plan_path
from duckln.repair_intake import DependencyApprovalDecision
from duckln.repo_bringup import (
    _mode_decision_for_step,
    _infer_runtime_access_url,
    _prerequisite_recovery_plan,
    _supervisor_final_summary,
    _wrap_command_for_execution_target,
    DebugRecoveryAssessment,
    DebugRecoverySpecialist,
    FailureType,
    RepoBringUpPlan,
    RepoBringUpStep,
    RepoFamily,
    RepoSetupOutcome,
    RepoVerificationOutcome,
    RecoveryDecision,
    bring_up_selected_repo,
    classify_runtime_command,
    classify_repo_family,
    derive_repo_runtime_hints,
    dir_exists_check,
    file_exists_check,
    infer_repo_setup_plan,
    infer_readme_workflow_steps,
    inspect_repo_for_bringup,
    _parse_llm_readme_workflow_json,
    resolve_managed_project_dir,
    resolve_runtime_project_dir,
    run_prepared_repo,
    scan_readme_for_run_commands,
    venv_create_command,
    venv_python_path,
    venv_python_test_command,
)
from duckln.shell import BackgroundCommandSession, CommandResult
from state.access import read_session_summary_state, write_workflow_state
from state.repo_catalog import RepoCatalogRecord
from state.store import initialize_state_store


class FakeRunner:
    def __init__(
        self,
        project_dir: Path,
        *,
        create_requirements: bool = True,
        failures: dict[str, str] | None = None,
    ) -> None:
        self.project_dir = project_dir
        self.create_requirements = create_requirements
        self.failures = failures or {}
        self.commands: list[tuple[str, str | None]] = []
        self.background_commands: list[tuple[str, str | None, str | None]] = []

    def run(self, command: str, *, timeout_seconds: float = 30.0, cwd: str | None = None, env=None) -> CommandResult:
        self.commands.append((command, cwd))
        if command in self.failures:
            return CommandResult(
                command=command,
                exit_code=1,
                stdout="",
                stderr=self.failures[command],
                timed_out=False,
                duration_seconds=0.01,
            )
        if command.startswith("git clone "):
            self.project_dir.mkdir(parents=True, exist_ok=True)
            if self.create_requirements:
                (self.project_dir / "requirements.txt").write_text("requests\n", encoding="utf-8")
        elif command == "python -m venv .venv":
            venv_python = self.project_dir / ".venv" / "bin"
            venv_python.mkdir(parents=True, exist_ok=True)
            (venv_python / "python").write_text("", encoding="utf-8")
        return CommandResult(
            command=command,
            exit_code=0,
            stdout="",
            stderr="",
            timed_out=False,
            duration_seconds=0.01,
        )

    def start_background(self, command: str, *, cwd: str | None = None, env=None, log_path=None) -> BackgroundCommandSession:
        self.background_commands.append((command, cwd, str(log_path) if log_path is not None else None))
        return BackgroundCommandSession(
            command=command,
            pid=43210,
            cwd=cwd,
            log_path=str(log_path) if log_path is not None else None,
            started_at=0.0,
        )


class FakeTerminalExecutor:
    def __init__(self) -> None:
        self.commands: list[tuple[str, str | None]] = []
        self.interrupt_calls = 0

    def run_terminal_command(self, *, command: str, cwd: str | None = None) -> bool:
        self.commands.append((command, cwd))
        return True

    def interrupt_terminal(self) -> bool:
        self.interrupt_calls += 1
        return True


class RetryOnceRunner(FakeRunner):
    def __init__(self, project_dir: Path, failed_command: str, stderr: str) -> None:
        super().__init__(project_dir)
        self.failed_command = failed_command
        self.stderr = stderr
        self._failed_once = False

    def run(self, command: str, *, timeout_seconds: float = 30.0, cwd: str | None = None, env=None) -> CommandResult:
        if command == self.failed_command and not self._failed_once:
            self.commands.append((command, cwd))
            self._failed_once = True
            return CommandResult(
                command=command,
                exit_code=1,
                stdout="",
                stderr=self.stderr,
                timed_out=False,
                duration_seconds=0.01,
            )
        return super().run(command, timeout_seconds=timeout_seconds, cwd=cwd, env=env)


class CrossOsVenvHelperTest(unittest.TestCase):
    def test_remote_target_uses_python3_regardless_of_host(self) -> None:
        self.assertEqual("python3 -m venv .venv", venv_create_command(execution_target="vm"))
        self.assertEqual("python3 -m venv .venv", venv_create_command(execution_target="aws"))

    def test_remote_target_uses_posix_venv_python_path(self) -> None:
        self.assertEqual(".venv/bin/python", venv_python_path(execution_target="vm"))

    def test_remote_target_emits_test_x_check(self) -> None:
        self.assertEqual("test -x .venv/bin/python", venv_python_test_command(execution_target="vm"))

    def test_local_windows_uses_py_launcher(self) -> None:
        from unittest.mock import patch

        with patch("duckln.repo_bringup.platform.system", return_value="Windows"):
            self.assertEqual("py -m venv .venv", venv_create_command(execution_target="local"))
            self.assertEqual(".venv\\Scripts\\python.exe", venv_python_path(execution_target="local"))
            self.assertEqual(
                'if exist ".venv\\Scripts\\python.exe" (exit 0) else (exit 1)',
                venv_python_test_command(execution_target="local"),
            )

    def test_local_macos_uses_python(self) -> None:
        from unittest.mock import patch

        with patch("duckln.repo_bringup.platform.system", return_value="Darwin"):
            self.assertEqual("python -m venv .venv", venv_create_command(execution_target="local"))
            self.assertEqual(".venv/bin/python", venv_python_path(execution_target="local"))

    def test_file_exists_check_uses_test_on_posix_and_if_exist_on_windows(self) -> None:
        from unittest.mock import patch

        # Remote target is always POSIX (Ubuntu VM/cloud).
        self.assertEqual("test -f go.mod", file_exists_check("go.mod", execution_target="vm"))
        with patch("duckln.repo_bringup.platform.system", return_value="Windows"):
            self.assertEqual('if exist "go.mod" (exit 0) else (exit 1)',
                             file_exists_check("go.mod", execution_target="local"))
        with patch("duckln.repo_bringup.platform.system", return_value="Darwin"):
            self.assertEqual("test -f go.mod", file_exists_check("go.mod", execution_target="local"))

    def test_dir_exists_check_emits_per_os_form(self) -> None:
        from unittest.mock import patch

        self.assertEqual("test -d build", dir_exists_check("build", execution_target="vm"))
        with patch("duckln.repo_bringup.platform.system", return_value="Windows"):
            self.assertEqual('if exist "build\\" (exit 0) else (exit 1)',
                             dir_exists_check("build", execution_target="local"))


class RepoBringUpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = RepoCatalogRecord(
            name="alpha",
            repo_url="https://example.com/alpha",
            stars=50,
            description="Alpha repository",
            category="LLM",
            framework="Python",
            last_updated="2026-03-22",
        )

    def test_infer_repo_setup_plan_prefers_requirements_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "README.md").write_text("# Alpha\n", encoding="utf-8")
            (project_dir / "requirements.txt").write_text("requests\n", encoding="utf-8")

            plan = infer_repo_setup_plan(self.repo, project_dir, config_dir=project_dir)

            self.assertEqual(("README.md", "requirements.txt"), plan.detected_files)
            self.assertEqual("python -m venv .venv", plan.steps[0].command)
            self.assertEqual(".venv/bin/python -m pip install -r requirements.txt", plan.steps[1].command)
            self.assertEqual(RepoFamily.PYTHON, plan.repo_family)
            self.assertEqual("python", plan.specialist_name)
            self.assertIsNotNone(plan.subagent_prompt)
            self.assertTrue(plan.subagent_workspace_files)

    def test_dependency_install_step_uses_structured_approver_in_all_modes(self) -> None:
        requests: list[object] = []

        class StructuredApprover:
            def approve_dependency_install(self, request) -> bool:
                requests.append(request)
                return DependencyApprovalDecision(
                    approved=True,
                    approve_all=False,
                    selected_item_ids=("req:requests",),
                )

            def __call__(self, _message: str) -> bool:
                raise AssertionError("generic approval should not be used for dependency installs")

        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "requirements.txt").write_text("requests==2.32.0\nnumpy>=1.26\n", encoding="utf-8")

            decision, effective_command = _mode_decision_for_step(
                ControlMode.HOOTLWO,
                ".venv/bin/python -m pip install -r requirements.txt",
                approve=StructuredApprover(),
                prompt="Install requirements",
                project_dir=project_dir,
            )

        self.assertTrue(decision.allowed)
        self.assertEqual(1, len(requests))
        self.assertEqual("requests", requests[0].items[0].dependency)
        self.assertIn("requests==2.32.0", effective_command)

    def test_supervisor_routes_cmake_repo_to_cpp_native_specialist(self) -> None:
        native_repo = RepoCatalogRecord(
            name="llama.cpp",
            repo_url="https://github.com/ggerganov/llama.cpp",
            stars=50,
            description="Port of LLaMA model in C/C++",
            category="LLM",
            framework="C++/Local Runtime",
            last_updated="2026-03-22",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "README.md").write_text("# llama.cpp\nNative runtime\n", encoding="utf-8")
            (project_dir / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.16)\n", encoding="utf-8")

            plan = infer_repo_setup_plan(native_repo, project_dir, system_probe=_fake_probe())

            self.assertEqual(RepoFamily.CPP_NATIVE, plan.repo_family)
            self.assertEqual("cpp-native", plan.specialist_name)
            self.assertEqual("cmake -S . -B build", plan.steps[0].command)

    def test_supervisor_routes_node_repo_to_node_typescript_specialist(self) -> None:
        node_repo = RepoCatalogRecord(
            name="web-ui",
            repo_url="https://github.com/example/web-ui",
            stars=10,
            description="TypeScript frontend",
            category="LLM",
            framework="TypeScript",
            last_updated="2026-03-22",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "package.json").write_text('{"name":"web-ui"}\n', encoding="utf-8")
            (project_dir / "pnpm-lock.yaml").write_text("lockfileVersion: 9\n", encoding="utf-8")

            plan = infer_repo_setup_plan(node_repo, project_dir, system_probe=_fake_probe())

            self.assertEqual(RepoFamily.NODE_TYPESCRIPT, plan.repo_family)
            self.assertEqual("node-typescript", plan.specialist_name)
            self.assertEqual(("Install Node dependencies",), tuple(step.purpose for step in plan.steps))
            # Plan 173 F2: node install always includes devDependencies (build tools like
            # vite/tsc are devDeps; a NODE_ENV=production env would otherwise strip them).
            self.assertEqual("pnpm install --frozen-lockfile --prod=false", plan.steps[0].command)

    def test_supervisor_routes_go_repo_to_go_specialist(self) -> None:
        go_repo = RepoCatalogRecord(
            name="go-api",
            repo_url="https://github.com/example/go-api",
            stars=10,
            description="Go service",
            category="Custom",
            framework="Go",
            last_updated="2026-03-22",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "go.mod").write_text("module example.com/go-api\n\ngo 1.22\n", encoding="utf-8")
            (project_dir / "main.go").write_text("package main\nfunc main(){}\n", encoding="utf-8")

            plan = infer_repo_setup_plan(go_repo, project_dir, system_probe=_fake_probe())

            self.assertEqual(RepoFamily.GO_NATIVE, plan.repo_family)
            self.assertEqual("go-native", plan.specialist_name)
            self.assertEqual("go mod download", plan.steps[0].command)
            self.assertEqual("go list ./...", plan.steps[1].command)

    def test_supervisor_classifies_audio_diffusion_and_multi_service_families(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_dir = Path(temp_dir) / "audio"
            diffusion_dir = Path(temp_dir) / "diffusion"
            services_dir = Path(temp_dir) / "services"
            audio_dir.mkdir()
            diffusion_dir.mkdir()
            services_dir.mkdir()
            (audio_dir / "requirements.txt").write_text("coqui-tts\n", encoding="utf-8")
            (audio_dir / "README.md").write_text("Coqui TTS speech synthesis\n", encoding="utf-8")
            (diffusion_dir / "requirements.txt").write_text("torch\n", encoding="utf-8")
            (diffusion_dir / "README.md").write_text("Stable Diffusion inference UI\n", encoding="utf-8")
            (services_dir / "package.json").write_text('{"name":"stack"}\n', encoding="utf-8")
            (services_dir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

            audio_inspection = inspect_repo_for_bringup(self.repo, audio_dir, system_probe=_fake_probe())
            diffusion_inspection = inspect_repo_for_bringup(self.repo, diffusion_dir, system_probe=_fake_probe())
            services_inspection = inspect_repo_for_bringup(self.repo, services_dir, system_probe=_fake_probe())

            self.assertEqual(RepoFamily.AUDIO, classify_repo_family(audio_inspection))
            self.assertEqual(RepoFamily.DIFFUSION_HEAVY, classify_repo_family(diffusion_inspection))
            self.assertEqual(RepoFamily.MULTI_SERVICE, classify_repo_family(services_inspection))

    def test_supervisor_routes_audio_repo_to_audio_specialist(self) -> None:
        audio_repo = RepoCatalogRecord(
            name="coqui-tts",
            repo_url="https://github.com/coqui-ai/TTS",
            stars=100,
            description="Coqui TTS voice synthesis",
            category="Audio",
            framework="Python/PyTorch",
            last_updated="2026-03-22",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "README.md").write_text("Speech synthesis with Coqui TTS\n", encoding="utf-8")
            (project_dir / "requirements.txt").write_text("torch\n", encoding="utf-8")

            plan = infer_repo_setup_plan(audio_repo, project_dir, system_probe=_fake_probe())

            self.assertEqual(RepoFamily.AUDIO, plan.repo_family)
            self.assertEqual("audio", plan.specialist_name)
            self.assertEqual("python -m venv .venv", plan.steps[0].command)

    def test_supervisor_routes_diffusion_repo_to_diffusion_specialist(self) -> None:
        diffusion_repo = RepoCatalogRecord(
            name="ComfyUI",
            repo_url="https://github.com/Comfy-Org/ComfyUI",
            stars=100,
            description="Stable Diffusion node UI",
            category="Stable Diffusion",
            framework="Python/PyTorch",
            last_updated="2026-03-22",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "README.md").write_text("ComfyUI Stable Diffusion workflows\n", encoding="utf-8")
            (project_dir / "requirements.txt").write_text("torch\n", encoding="utf-8")

            plan = infer_repo_setup_plan(diffusion_repo, project_dir, system_probe=_fake_probe())

            self.assertEqual(RepoFamily.DIFFUSION_HEAVY, plan.repo_family)
            self.assertEqual("diffusion-heavy", plan.specialist_name)

    def test_inspect_repo_for_bringup_uses_cached_remote_setup_files_when_local_repo_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            store = initialize_state_store(paths.config_dir)
            store.upsert_repo_knowledge(
                repo_key=self.repo.repo_url,
                repo_name=self.repo.name,
                repo_url=self.repo.repo_url,
                source="remote",
                summary="Remote knowledge for alpha.",
                setup_files=("README.md", "requirements.txt"),
                freshness_checked_at=datetime.now(timezone.utc).isoformat(),
            )

            inspection = inspect_repo_for_bringup(
                self.repo,
                resolve_managed_project_dir(paths.config_dir, self.repo),
                config_dir=paths.config_dir,
                execution_target="gcp",
            )

            self.assertIn("requirements.txt", inspection.detected_files)

    def test_bring_up_selected_repo_executes_cloud_wrapped_setup_commands(self) -> None:
        class MissingRemoteDirRunner(FakeRunner):
            def __init__(self, project_dir: Path) -> None:
                super().__init__(project_dir)
                self._missing_dir_reported = False

            def run(self, command: str, *, timeout_seconds: float = 30.0, cwd: str | None = None, env=None) -> CommandResult:
                if "test -d ." in command and not self._missing_dir_reported:
                    self._missing_dir_reported = True
                    self.commands.append((command, cwd))
                    return CommandResult(
                        command=command,
                        exit_code=1,
                        stdout="",
                        stderr="missing",
                        timed_out=False,
                        duration_seconds=0.01,
                    )
                return super().run(command, timeout_seconds=timeout_seconds, cwd=cwd, env=env)

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            store = initialize_state_store(paths.config_dir)
            store.upsert_repo_knowledge(
                repo_key=self.repo.repo_url,
                repo_name=self.repo.name,
                repo_url=self.repo.repo_url,
                source="remote",
                summary="Remote alpha knowledge.",
                setup_files=("README.md", "requirements.txt"),
                freshness_checked_at=datetime.now(timezone.utc).isoformat(),
            )
            store.upsert_managed_resource(
                resource_key="gcp:duckln-gcp:us-central1-a",
                resource_kind="cloud_vm",
                provider="gcp",
                display_name="duckln-gcp",
                execution_target="gcp",
                region="us-central1-a",
                shape="e2-standard-4",
                status="running",
                metadata={"connect_command": "gcloud compute ssh duckln-gcp --zone us-central1-a"},
            )
            write_workflow_state(
                paths.config_dir,
                {
                    "active_runtime_execution_target": "gcp",
                    "active_runtime_cloud_resource_key": "gcp:duckln-gcp:us-central1-a",
                    "active_runtime_cloud_vendor": "GCP",
                    "active_runtime_cloud_region": "us-central1-a",
                    "active_runtime_cloud_shape": "e2-standard-4",
                },
            )
            runner = MissingRemoteDirRunner(resolve_managed_project_dir(paths.config_dir, self.repo))

            result = bring_up_selected_repo(
                self.repo,
                ControlMode.HOTL,
                paths,
                runner=runner,
                approve=lambda _prompt: True,
                display=lambda _line: None,
                execution_target="gcp",
            )

            self.assertTrue(result.verification_passed)
            self.assertEqual("~/.duckln/projects/alpha", result.setup_outcome.install_location)
            wrapped_commands = [command for command, _cwd in runner.commands]
            self.assertTrue(any("gcloud compute ssh duckln-gcp --zone us-central1-a --command" in command for command in wrapped_commands))
            self.assertTrue(any("git clone --depth 1 https://example.com/alpha" in command and ".duckln/projects/alpha" in command for command in wrapped_commands))
            self.assertTrue(any(".venv/bin/python -m pip install -r requirements.txt" in command for command in wrapped_commands))

    def test_run_prepared_repo_uses_tracked_run_command_from_repo_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            repo = RepoCatalogRecord(
                name="whisper",
                repo_url="https://example.com/whisper",
                stars=50,
                description="Speech recognition",
                category="Audio",
                framework="Python",
                last_updated="2026-03-22",
            )
            project_dir = resolve_managed_project_dir(paths.config_dir, repo)
            project_dir.mkdir(parents=True, exist_ok=True)
            (project_dir / "README.md").write_text("# whisper\n", encoding="utf-8")
            initialize_state_store(paths.config_dir).upsert_repo_state(
                repo_key=repo.repo_url,
                repo_url=repo.repo_url,
                repo_path=str(project_dir),
                status="ready",
                summary="Whisper ready.",
                metadata={
                    "repo_name": "whisper",
                    "install_location": str(project_dir),
                    "verify_command": ".venv/bin/python -m whisper --help",
                    "manual_command": ".venv/bin/python -m whisper <audio-file> --model base",
                    "runtime_kind": "cli_tool",
                    "access_hint": "Use the CLI from the managed project directory.",
                },
            )

            runner = FakeRunner(project_dir)
            result = run_prepared_repo(
                repo,
                ControlMode.HOTL,
                paths,
                runner=runner,
                approve=lambda _prompt: True,
                display=lambda _line: None,
                verification_only=True,
            )

            self.assertTrue(result.verification_passed)
            self.assertIn(".venv/bin/python -m whisper --help", result.message)
            self.assertEqual([(".venv/bin/python -m whisper --help", str(project_dir))], runner.commands)

    def test_run_prepared_repo_does_not_claim_smoke_command_started_a_runtime_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            repo = RepoCatalogRecord(
                name="whisper",
                repo_url="https://example.com/whisper",
                stars=50,
                description="Speech recognition",
                category="Audio",
                framework="Python",
                last_updated="2026-03-22",
            )
            project_dir = resolve_managed_project_dir(paths.config_dir, repo)
            project_dir.mkdir(parents=True, exist_ok=True)
            initialize_state_store(paths.config_dir).upsert_repo_state(
                repo_key=repo.repo_url,
                repo_url=repo.repo_url,
                repo_path=str(project_dir),
                status="ready",
                summary="Whisper ready.",
                metadata={
                    "repo_name": "whisper",
                    "install_location": str(project_dir),
                    "verify_command": ".venv/bin/python -m whisper --help",
                    "manual_command": ".venv/bin/python -m whisper <audio-file> --model base",
                    "runtime_kind": "cli_tool",
                    "access_hint": "Use the CLI from the managed project directory.",
                },
            )

            runner = FakeRunner(project_dir)
            result = run_prepared_repo(
                repo,
                ControlMode.HOTL,
                paths,
                runner=runner,
                approve=lambda _prompt: True,
                display=lambda _line: None,
            )

            self.assertFalse(result.verification_passed)
            self.assertFalse(result.should_offer_repair)
            self.assertIn("not treating whisper as a long-running repo service", result.message.lower())
            self.assertIn("<audio-file>", result.message)
            self.assertEqual([], runner.background_commands)

    def test_run_prepared_repo_does_not_treat_help_run_command_as_long_running_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            repo = RepoCatalogRecord(
                name="whisper",
                repo_url="https://example.com/whisper",
                stars=50,
                description="Speech recognition",
                category="Audio",
                framework="Python",
                last_updated="2026-03-22",
            )
            project_dir = resolve_managed_project_dir(paths.config_dir, repo)
            project_dir.mkdir(parents=True, exist_ok=True)
            initialize_state_store(paths.config_dir).upsert_repo_state(
                repo_key=repo.repo_url,
                repo_url=repo.repo_url,
                repo_path=str(project_dir),
                status="ready",
                summary="Whisper ready.",
                metadata={
                    "repo_name": "whisper",
                    "install_location": str(project_dir),
                    "run_command": ".venv/bin/python -m whisper --help",
                    "verify_command": ".venv/bin/python -m whisper --help",
                    "manual_command": ".venv/bin/python -m whisper <audio-file> --model base",
                    "runtime_kind": "cli_tool",
                    "access_hint": "Use the CLI from the managed project directory.",
                },
            )

            runner = FakeRunner(project_dir)
            result = run_prepared_repo(
                repo,
                ControlMode.HOTL,
                paths,
                runner=runner,
                approve=lambda _prompt: True,
                display=lambda _line: None,
            )

            self.assertFalse(result.verification_passed)
            self.assertFalse(result.should_offer_repair)
            self.assertIn("not treating whisper as a long-running repo service", result.message.lower())
            self.assertIn("<audio-file>", result.message)
            self.assertEqual([], runner.background_commands)

    def test_run_prepared_repo_starts_background_session_for_runtime_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            repo = RepoCatalogRecord(
                name="open-webui",
                repo_url="https://example.com/open-webui",
                stars=50,
                description="Local UI",
                category="LLM",
                framework="Python",
                last_updated="2026-03-22",
            )
            project_dir = resolve_managed_project_dir(paths.config_dir, repo)
            project_dir.mkdir(parents=True, exist_ok=True)
            initialize_state_store(paths.config_dir).upsert_repo_state(
                repo_key=repo.repo_url,
                repo_url=repo.repo_url,
                repo_path=str(project_dir),
                status="ready",
                summary="open-webui ready.",
                metadata={
                    "repo_name": "open-webui",
                    "install_location": str(project_dir),
                    "run_command": "uvicorn main:app --host 127.0.0.1 --port 8080",
                    "access_hint": "Open http://localhost:8080 after startup.",
                },
            )

            runner = FakeRunner(project_dir)
            result = run_prepared_repo(
                repo,
                ControlMode.HOTL,
                paths,
                runner=runner,
                approve=lambda _prompt: True,
                display=lambda _line: None,
            )

            self.assertTrue(result.verification_passed)
            self.assertIn("started open-webui", result.message.lower())
            self.assertEqual(
                [("uvicorn main:app --host 127.0.0.1 --port 8080", str(project_dir), str(paths.config_dir / "runtime" / "open-webui.log"))],
                runner.background_commands,
            )

    def test_run_prepared_repo_stages_bounded_repo_command_in_terminal_pane(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            repo = RepoCatalogRecord(
                name="whisper",
                repo_url="https://example.com/whisper",
                stars=50,
                description="Speech recognition",
                category="Audio",
                framework="Python",
                last_updated="2026-03-22",
            )
            project_dir = resolve_managed_project_dir(paths.config_dir, repo)
            project_dir.mkdir(parents=True, exist_ok=True)
            initialize_state_store(paths.config_dir).upsert_repo_state(
                repo_key=repo.repo_url,
                repo_url=repo.repo_url,
                repo_path=str(project_dir),
                status="ready",
                summary="Whisper ready.",
                metadata={
                    "repo_name": "whisper",
                    "install_location": str(project_dir),
                    "verify_command": ".venv/bin/python -m whisper --help",
                    "manual_command": ".venv/bin/python -m whisper <audio-file> --model base",
                    "runtime_kind": "cli_tool",
                    "access_hint": "Use the CLI from the managed project directory.",
                },
            )

            terminal = FakeTerminalExecutor()
            result = run_prepared_repo(
                repo,
                ControlMode.HOTL,
                paths,
                runner=FakeRunner(project_dir),
                approve=lambda _prompt: True,
                display=lambda _line: None,
                terminal_executor=terminal,
            )

            self.assertFalse(result.verification_passed)
            self.assertFalse(result.should_offer_repair)
            self.assertIn("not treating whisper as a long-running repo service", result.message.lower())
            self.assertEqual([], terminal.commands)

    def test_run_prepared_repo_starts_cloud_repo_in_terminal_pane_with_wrapped_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            repo = RepoCatalogRecord(
                name="open-webui",
                repo_url="https://example.com/open-webui",
                stars=50,
                description="Local UI",
                category="LLM",
                framework="Python",
                last_updated="2026-03-22",
            )
            remote_dir = resolve_runtime_project_dir(paths.config_dir, repo, execution_target="gcp")
            store = initialize_state_store(paths.config_dir)
            store.upsert_repo_state(
                repo_key=repo.repo_url,
                repo_url=repo.repo_url,
                repo_path=remote_dir,
                execution_target="gcp",
                status="ready",
                summary="open-webui ready.",
                metadata={
                    "repo_name": "open-webui",
                    "install_location": remote_dir,
                    "run_command": "uvicorn main:app --host 127.0.0.1 --port 8080",
                    "access_hint": "Open the printed URL after startup.",
                    "cloud_resource_key": "gcp:duckln-gcp:us-central1-a",
                    "cloud_vendor": "GCP",
                    "cloud_region": "us-central1-a",
                },
            )
            store.upsert_managed_resource(
                resource_key="gcp:duckln-gcp:us-central1-a",
                resource_kind="cloud_vm",
                provider="gcp",
                display_name="duckln-gcp",
                execution_target="gcp",
                region="us-central1-a",
                shape="e2-standard-4",
                status="running",
                metadata={"connect_command": "gcloud compute ssh duckln-gcp --zone us-central1-a"},
            )
            write_workflow_state(
                paths.config_dir,
                {
                    "active_runtime_execution_target": "gcp",
                    "active_runtime_cloud_resource_key": "gcp:duckln-gcp:us-central1-a",
                    "active_runtime_cloud_vendor": "GCP",
                    "active_runtime_cloud_region": "us-central1-a",
                    "active_runtime_cloud_shape": "e2-standard-4",
                },
            )

            terminal = FakeTerminalExecutor()
            result = run_prepared_repo(
                repo,
                ControlMode.HOTL,
                paths,
                runner=FakeRunner(resolve_managed_project_dir(paths.config_dir, repo)),
                approve=lambda _prompt: True,
                display=lambda _line: None,
                terminal_executor=terminal,
            )

            self.assertTrue(result.verification_passed)
            self.assertEqual(1, len(terminal.commands))
            self.assertIsNone(terminal.commands[0][1])
            self.assertIn("gcloud compute ssh duckln-gcp --zone us-central1-a --command", terminal.commands[0][0])
            self.assertIn("uvicorn main:app --host 127.0.0.1 --port 8080", terminal.commands[0][0])

    def test_run_prepared_repo_prefers_repo_bound_cloud_resource_over_active_workflow_resource(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            repo = RepoCatalogRecord(
                name="open-webui",
                repo_url="https://example.com/open-webui",
                stars=50,
                description="Local UI",
                category="LLM",
                framework="Python",
                last_updated="2026-03-22",
            )
            remote_dir = resolve_runtime_project_dir(paths.config_dir, repo, execution_target="gcp")
            store = initialize_state_store(paths.config_dir)
            store.upsert_repo_state(
                repo_key=repo.repo_url,
                repo_url=repo.repo_url,
                repo_path=remote_dir,
                execution_target="gcp",
                status="ready",
                summary="open-webui ready.",
                metadata={
                    "repo_name": "open-webui",
                    "install_location": remote_dir,
                    "run_command": "uvicorn main:app --host 127.0.0.1 --port 8080",
                    "cloud_resource_key": "gcp:repo-bound:us-central1-a",
                    "cloud_vendor": "GCP",
                    "cloud_region": "us-central1-a",
                },
            )
            store.upsert_managed_resource(
                resource_key="gcp:repo-bound:us-central1-a",
                resource_kind="cloud_vm",
                provider="gcp",
                display_name="repo-bound",
                execution_target="gcp",
                region="us-central1-a",
                shape="e2-standard-4",
                status="running",
                metadata={"connect_command": "gcloud compute ssh repo-bound --zone us-central1-a"},
            )
            store.upsert_managed_resource(
                resource_key="gcp:other-active:us-central1-a",
                resource_kind="cloud_vm",
                provider="gcp",
                display_name="other-active",
                execution_target="gcp",
                region="us-central1-a",
                shape="e2-standard-4",
                status="running",
                metadata={"connect_command": "gcloud compute ssh other-active --zone us-central1-a"},
            )
            write_workflow_state(
                paths.config_dir,
                {
                    "active_runtime_execution_target": "gcp",
                    "active_runtime_cloud_resource_key": "gcp:other-active:us-central1-a",
                    "active_runtime_cloud_vendor": "GCP",
                    "active_runtime_cloud_region": "us-central1-a",
                },
            )

            terminal = FakeTerminalExecutor()
            result = run_prepared_repo(
                repo,
                ControlMode.HOTL,
                paths,
                runner=FakeRunner(resolve_managed_project_dir(paths.config_dir, repo)),
                approve=lambda _prompt: True,
                display=lambda _line: None,
                terminal_executor=terminal,
            )

            self.assertTrue(result.verification_passed)
            self.assertIn("gcloud compute ssh repo-bound --zone us-central1-a --command", terminal.commands[0][0])
            self.assertNotIn("other-active", terminal.commands[0][0])

    def test_derive_repo_runtime_hints_prefers_package_json_start_scripts_for_custom_repos(self) -> None:
        linked_repo = RepoCatalogRecord(
            name="custom-web",
            repo_url="linked://local/custom-web",
            stars=0,
            description="Custom linked repo",
            category="Custom",
            framework="Unknown",
            last_updated="2026-03-22",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "package.json").write_text('{"name":"custom-web","scripts":{"dev":"vite","start":"node server.js"}}', encoding="utf-8")

            hints = derive_repo_runtime_hints(linked_repo, project_dir, system_probe=_fake_probe(), config_dir=project_dir)

            self.assertEqual("npm run start", hints.run_command)
            self.assertEqual("service", hints.runtime_kind)

    def test_derive_repo_runtime_hints_prefers_pnpm_lockfile_for_custom_repos(self) -> None:
        linked_repo = RepoCatalogRecord(
            name="custom-pnpm",
            repo_url="linked://local/custom-pnpm",
            stars=0,
            description="Custom linked repo",
            category="Custom",
            framework="Unknown",
            last_updated="2026-03-22",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "package.json").write_text('{"name":"custom-pnpm","scripts":{"dev":"vite"}}', encoding="utf-8")
            (project_dir / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")

            hints = derive_repo_runtime_hints(linked_repo, project_dir, system_probe=_fake_probe(), config_dir=project_dir)

            self.assertEqual("pnpm dev", hints.run_command)
            self.assertEqual("service", hints.runtime_kind)

    def test_derive_repo_runtime_hints_prefers_docker_compose_when_present(self) -> None:
        linked_repo = RepoCatalogRecord(
            name="compose-stack",
            repo_url="linked://local/compose-stack",
            stars=0,
            description="Custom linked repo",
            category="Custom",
            framework="Unknown",
            last_updated="2026-03-22",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

            hints = derive_repo_runtime_hints(linked_repo, project_dir, system_probe=_fake_probe(), config_dir=project_dir)

            self.assertEqual("docker compose up", hints.run_command)
            self.assertEqual("service", hints.runtime_kind)

    def test_derive_repo_runtime_hints_detects_go_runtime_and_auth_requirements(self) -> None:
        linked_repo = RepoCatalogRecord(
            name="go-app",
            repo_url="linked://local/go-app",
            stars=0,
            description="Custom linked repo",
            category="Custom",
            framework="Go",
            last_updated="2026-03-22",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "go.mod").write_text("module example.com/go-app\n\ngo 1.22\n", encoding="utf-8")
            (project_dir / "main.go").write_text("package main\nfunc main(){}\n", encoding="utf-8")
            (project_dir / ".env.example").write_text("OPENAI_API_KEY=\n", encoding="utf-8")
            (project_dir / "README.md").write_text("Set OPENAI_API_KEY before running the service.\n", encoding="utf-8")

            hints = derive_repo_runtime_hints(linked_repo, project_dir, system_probe=_fake_probe(), config_dir=project_dir)

            self.assertEqual("go run .", hints.run_command)
            self.assertEqual("go list ./...", hints.verify_command)
            self.assertEqual("service", hints.runtime_kind)
            self.assertEqual("go", hints.stack_family)
            self.assertTrue(any(item["env_var"] == "OPENAI_API_KEY" for item in hints.auth_requirements))
            self.assertIn("OPENAI_API_KEY", hints.missing_auth_variables)

    def test_infer_runtime_access_url_normalizes_local_service_commands(self) -> None:
        self.assertEqual(
            "http://localhost:8080",
            _infer_runtime_access_url("uvicorn main:app --host 0.0.0.0 --port 8080"),
        )
        self.assertEqual(
            "http://localhost:8502",
            _infer_runtime_access_url("streamlit run app.py --server.port 8502"),
        )

    def test_supervisor_routes_vm_execution_target_to_vm_environment_specialist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "Dockerfile").write_text("FROM python:3.11-slim\n", encoding="utf-8")

            plan = infer_repo_setup_plan(
                self.repo,
                project_dir,
                system_probe=_fake_probe(),
                execution_target="vm",
            )

            self.assertEqual(RepoFamily.VM_ENVIRONMENT, plan.repo_family)
            self.assertEqual("vm-environment", plan.specialist_name)
            self.assertEqual("test -f Dockerfile", plan.steps[0].command)

    def test_supervisor_routes_ollama_provider_signal_to_provider_specialist(self) -> None:
        provider_repo = RepoCatalogRecord(
            name="open-webui",
            repo_url="https://github.com/open-webui/open-webui",
            stars=100,
            description="OpenAI-compatible local UI with Ollama support",
            category="LLM",
            framework="Python",
            last_updated="2026-03-22",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "README.md").write_text("Use Ollama or OpenAI-compatible providers.\n", encoding="utf-8")

            plan = infer_repo_setup_plan(
                provider_repo,
                project_dir,
                system_probe=_fake_probe(),
                runtime_provider="ollama",
            )

            self.assertEqual(RepoFamily.PROVIDER_ROUTING, plan.repo_family)
            self.assertEqual("provider-routing", plan.specialist_name)
            self.assertEqual("which ollama", plan.steps[0].command)

    def test_supervisor_does_not_route_node_repo_to_provider_specialist_only_because_readme_mentions_ollama(self) -> None:
        node_repo = RepoCatalogRecord(
            name="openclaw",
            repo_url="https://github.com/openclaw/openclaw",
            stars=100,
            description="Personal AI assistant",
            category="AI",
            framework="Node",
            last_updated="2026-05-05",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "package.json").write_text('{"name":"openclaw"}', encoding="utf-8")
            (project_dir / "README.md").write_text("OpenClaw can use Ollama local models.\n", encoding="utf-8")

            plan = infer_repo_setup_plan(
                node_repo,
                project_dir,
                system_probe=_fake_probe(),
                runtime_provider="ollama",
            )

            self.assertEqual(RepoFamily.NODE_TYPESCRIPT, plan.repo_family)
            self.assertEqual("node-typescript", plan.specialist_name)

    def test_supervisor_does_not_route_plain_python_repo_to_provider_specialist_for_openrouter_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "requirements.txt").write_text("requests\n", encoding="utf-8")

            plan = infer_repo_setup_plan(
                self.repo,
                project_dir,
                system_probe=_fake_probe(),
                runtime_provider="openrouter",
            )

            self.assertEqual(RepoFamily.PYTHON, plan.repo_family)
            self.assertEqual("python", plan.specialist_name)

    def test_supervisor_escalates_low_confidence_unknown_repo_to_debug_recovery(self) -> None:
        unknown_repo = RepoCatalogRecord(
            name="mystery",
            repo_url="https://example.com/mystery",
            stars=1,
            description="unknown project",
            category="Unknown",
            framework="Unknown",
            last_updated="2026-03-22",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            plan = infer_repo_setup_plan(unknown_repo, Path(temp_dir), system_probe=_fake_probe())

            self.assertEqual("debug-recovery", plan.specialist_name)
            self.assertEqual(RepoFamily.PYTHON, plan.repo_family)
            self.assertEqual((), plan.steps)
            self.assertTrue(plan.playbook_path)
            self.assertTrue(str(plan.playbook_path).endswith("debug_recovery.md"))
            self.assertIn("low-confidence classification", plan.summary)
            self.assertIn("decision=unsupported_case", plan.summary)

    def test_bring_up_selected_repo_in_hotl_executes_clone_and_safe_setup_after_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            project_dir = resolve_managed_project_dir(paths.config_dir, self.repo)
            runner = FakeRunner(project_dir)
            displayed: list[str] = []

            result = bring_up_selected_repo(
                self.repo,
                ControlMode.HOTL,
                paths,
                runner=runner,
                approve=lambda prompt: True,
                display=displayed.append,
            )

            self.assertEqual(
                (
                    f"git clone --depth 1 https://example.com/alpha {project_dir}",
                    "python -m venv .venv",
                    ".venv/bin/python -m pip install -r requirements.txt",
                ),
                result.executed_commands,
            )
            self.assertTrue(result.verification_passed)
            self.assertEqual(RepoFamily.PYTHON, result.repo_family)
            self.assertEqual("python", result.specialist_name)
            joined = "\n".join(displayed)
            self.assertIn("Supervisor agent", joined)
            self.assertIn("Python specialist agent", joined)
            self.assertIn("Verification agent", joined)
            self.assertIn("[", joined)
            self.assertIn("0s", joined)
            self.assertIn("Inspecting setup files and routing to a specialist...", joined)
            self.assertIn("Supervisor agent confirmed the smallest alpha setup path", result.message)
            self.assertIsNotNone(result.setup_outcome)
            assert result.setup_outcome is not None

    def test_bring_up_selected_repo_creates_todo_plan_before_setup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            project_dir = resolve_managed_project_dir(paths.config_dir, self.repo)
            runner = FakeRunner(project_dir)

            bring_up_selected_repo(
                self.repo,
                ControlMode.HITL,
                paths,
                runner=runner,
                display=lambda message: None,
            )

            plan_path = resolve_repo_plan_path(paths.config_dir, self.repo, project_dir=project_dir)
            self.assertTrue(plan_path.exists())
            plan_text = plan_path.read_text(encoding="utf-8")
            self.assertIn("# TODO for alpha", plan_text)
            self.assertIn("## What Duckln understands", plan_text)
            self.assertIn("## What Duckln will change", plan_text)

    def test_bring_up_selected_repo_in_hootlwo_auto_runs_safe_clone_and_setup_steps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            project_dir = resolve_managed_project_dir(paths.config_dir, self.repo)
            runner = FakeRunner(project_dir)
            displayed: list[str] = []

            result = bring_up_selected_repo(
                self.repo,
                ControlMode.HOOTLWO,
                paths,
                runner=runner,
                approve=lambda prompt: False,
                display=displayed.append,
            )

            self.assertEqual(
                (
                    f"git clone --depth 1 https://example.com/alpha {project_dir}",
                    "python -m venv .venv",
                ),
                result.executed_commands,
            )
            self.assertTrue(project_dir.exists())
            self.assertTrue(any("approval is still required" in message.lower() for message in displayed))
            self.assertFalse(result.verification_passed)

    def test_dependency_install_step_shows_official_source_before_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            project_dir = resolve_managed_project_dir(paths.config_dir, self.repo)
            runner = FakeRunner(project_dir)
            displayed: list[str] = []
            approval_prompts: list[str] = []

            result = bring_up_selected_repo(
                self.repo,
                ControlMode.HOTL,
                paths,
                runner=runner,
                approve=lambda prompt: approval_prompts.append(prompt) or True,
                display=displayed.append,
            )

            self.assertTrue(result.verification_passed)
            self.assertTrue(any("Duckln trace: dependency install review." in message for message in displayed))
            self.assertTrue(any("requirements.txt" in prompt for prompt in approval_prompts))
            self.assertTrue(any("packaging.python.org" in prompt for prompt in approval_prompts))

    def test_failed_python_setup_escalates_to_debug_recovery_and_records_compact_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            project_dir = resolve_managed_project_dir(paths.config_dir, self.repo)
            runner = FakeRunner(
                project_dir,
                failures={
                    ".venv/bin/python -m pip install -r requirements.txt": "ERROR: No matching distribution found for badpkg",
                },
            )
            displayed: list[str] = []

            result = bring_up_selected_repo(
                self.repo,
                ControlMode.HOTL,
                paths,
                runner=runner,
                approve=lambda prompt: True,
                display=displayed.append,
            )

            self.assertFalse(result.verification_passed)
            self.assertEqual(FailureType.DEPENDENCY_INSTALL_FAILURE, result.failure_type)
            self.assertEqual(RecoveryDecision.RETRY_SAME_SPECIALIST, result.recovery_decision)
            self.assertIn("Debug/Recovery classified dependency_install_failure", result.message)
            self.assertIn("decision=retry_same_specialist", result.message)
            joined = "\n".join(displayed)
            self.assertIn("Debug / Recovery agent", joined)
            self.assertIn("Checking a recovery route for the setup failure...", joined)
            self.assertTrue(any("retry python with the smallest corrected setup step" in message for message in displayed))
            self.assertIn(
                "Debug/Recovery classified dependency_install_failure",
                read_session_summary_state(paths.config_dir, session_id="repo bring-up recovery alpha") or "",
            )

    def test_bring_up_selected_repo_in_hitl_suggests_steps_without_executing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            displayed: list[str] = []
            project_dir = resolve_managed_project_dir(paths.config_dir, self.repo)
            project_dir.mkdir(parents=True, exist_ok=True)
            (project_dir / "requirements.txt").write_text("requests\n", encoding="utf-8")

            result = bring_up_selected_repo(
                self.repo,
                ControlMode.HITL,
                paths,
                runner=FakeRunner(project_dir),
                approve=lambda prompt: True,
                display=displayed.append,
            )

            self.assertEqual((), result.executed_commands)
            joined = "\n".join(displayed)
            self.assertIn("Smallest bounded setup path:", joined)
            self.assertIn("1. Create project virtualenv: python -m venv .venv", joined)
            self.assertIn("Plan saved", joined)
            self.assertIsNone(result.setup_outcome)

    def test_retry_same_specialist_executes_one_bounded_retry_and_can_succeed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            project_dir = resolve_managed_project_dir(paths.config_dir, self.repo)
            runner = RetryOnceRunner(
                project_dir,
                failed_command=".venv/bin/python -m pip install -r requirements.txt",
                stderr="ERROR: temporary package index failure",
            )

            result = bring_up_selected_repo(
                self.repo,
                ControlMode.HOTL,
                paths,
                runner=runner,
                approve=lambda prompt: True,
                display=lambda message: None,
            )

            self.assertTrue(result.verification_passed)
            self.assertEqual(
                (
                    f"git clone --depth 1 https://example.com/alpha {project_dir}",
                    "python -m venv .venv",
                    ".venv/bin/python -m pip install -r requirements.txt",
                    ".venv/bin/python -m pip install -r requirements.txt",
                ),
                result.executed_commands,
            )
            self.assertIn(
                "decision=retry_same_specialist",
                read_session_summary_state(paths.config_dir, session_id="repo bring-up recovery alpha") or "",
            )

    def test_failed_setup_messages_and_repo_state_redact_sensitive_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            project_dir = resolve_managed_project_dir(paths.config_dir, self.repo)
            runner = FakeRunner(
                project_dir,
                failures={
                    ".venv/bin/python -m pip install -r requirements.txt": "token=ghp_secretvalue12345",
                },
            )

            result = bring_up_selected_repo(
                self.repo,
                ControlMode.HOTL,
                paths,
                runner=runner,
                approve=lambda prompt: True,
                display=lambda message: None,
            )

            self.assertNotIn("ghp_secretvalue12345", result.message)
            latest = initialize_state_store(paths.config_dir).get_latest_repo_state()
            assert latest is not None
            self.assertNotIn("ghp_secretvalue12345", latest.summary)
            self.assertEqual("local", latest.execution_target)
            self.assertTrue(latest.managed_by_duckln)

    def test_run_prepared_repo_uses_tracked_install_location_for_linked_repo(self) -> None:
        linked_repo = RepoCatalogRecord(
            name="linked-alpha",
            repo_url="linked://local/tmp/linked-alpha",
            stars=0,
            description="Linked repo.",
            category="Custom",
            framework="Python",
            last_updated="2026-04-17",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            linked_dir = Path(temp_dir) / "external-linked-alpha"
            linked_dir.mkdir(parents=True, exist_ok=True)
            (linked_dir / "requirements.txt").write_text("requests\n", encoding="utf-8")
            runner = FakeRunner(linked_dir)
            initialize_state_store(paths.config_dir).upsert_repo_state(
                repo_key=linked_repo.repo_url,
                repo_path=str(linked_dir),
                repo_url=linked_repo.repo_url,
                execution_target="local",
                status="linked",
                summary="linked repo ready",
                managed_by_duckln=False,
                metadata={
                    "repo_name": "linked-alpha",
                    "install_location": str(linked_dir),
                    "run_command": "python -m pip --version",
                },
            )

            result = run_prepared_repo(
                linked_repo,
                ControlMode.HOTL,
                paths,
                runner=runner,
                approve=lambda prompt: True,
                display=lambda message: None,
                verification_only=True,
            )

            self.assertTrue(result.verification_passed)
            self.assertEqual(("python -m pip --version", str(linked_dir)), runner.commands[0])

    def test_run_prepared_repo_refreshes_start_command_from_linked_repo_files(self) -> None:
        linked_repo = RepoCatalogRecord(
            name="linked-web",
            repo_url="linked://local/tmp/linked-web",
            stars=0,
            description="Linked repo.",
            category="Custom",
            framework="Node",
            last_updated="2026-04-17",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            linked_dir = Path(temp_dir) / "external-linked-web"
            linked_dir.mkdir(parents=True, exist_ok=True)
            (linked_dir / "package.json").write_text('{"name":"linked-web","scripts":{"start":"vite preview"}}', encoding="utf-8")
            runner = FakeRunner(linked_dir)
            terminal = FakeTerminalExecutor()
            initialize_state_store(paths.config_dir).upsert_repo_state(
                repo_key=linked_repo.repo_url,
                repo_path=str(linked_dir),
                repo_url=linked_repo.repo_url,
                execution_target="local",
                status="linked",
                summary="linked repo ready",
                managed_by_duckln=False,
                metadata={
                    "repo_name": "linked-web",
                    "install_location": str(linked_dir),
                    "run_command": "python -m pip --version",
                    "verify_command": "python -m pip --version",
                },
            )

            result = run_prepared_repo(
                linked_repo,
                ControlMode.HOTL,
                paths,
                runner=runner,
                approve=lambda _prompt: True,
                display=lambda _line: None,
                terminal_executor=terminal,
            )

            self.assertTrue(result.verification_passed)
            self.assertEqual(("npm run start", str(linked_dir)), terminal.commands[0])
            latest = initialize_state_store(paths.config_dir).get_latest_repo_state()
            assert latest is not None
            self.assertEqual("running", latest.status)
            self.assertEqual("npm run start", latest.metadata["last_run_command"])

    def test_run_prepared_repo_materialized_cli_task_runs_in_terminal_pane(self) -> None:
        repo = RepoCatalogRecord(
            name="whisper",
            repo_url="https://github.com/openai/whisper",
            stars=97200,
            description="Speech recognition.",
            category="Audio",
            framework="Python",
            last_updated="2026-04-17",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            project_dir = resolve_managed_project_dir(paths.config_dir, repo)
            (project_dir / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
            (project_dir / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
            initialize_state_store(paths.config_dir).upsert_repo_state(
                repo_key=repo.repo_url,
                repo_url=repo.repo_url,
                repo_path=str(project_dir),
                status="ready",
                summary="Whisper ready.",
                metadata={
                    "repo_name": "whisper",
                    "install_location": str(project_dir),
                    "run_command": ".venv/bin/python -m whisper --help",
                    "verify_command": ".venv/bin/python -m whisper --help",
                    "manual_command": ".venv/bin/python -m whisper <audio-file> --model base",
                    "runtime_kind": "cli_tool",
                },
            )
            terminal = FakeTerminalExecutor()

            result = run_prepared_repo(
                repo,
                ControlMode.HOTL,
                paths,
                runner=FakeRunner(project_dir),
                approve=lambda _prompt: True,
                display=lambda _line: None,
                terminal_executor=terminal,
                runtime_command_override=".venv/bin/python -m whisper sample.wav --model base",
            )

            self.assertFalse(result.should_offer_repair)
            self.assertIn("concrete CLI task", result.message)
            self.assertEqual(
                (".venv/bin/python -m whisper sample.wav --model base", str(project_dir)),
                terminal.commands[0],
            )

    def test_run_prepared_repo_blocks_runtime_when_required_auth_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            repo = RepoCatalogRecord(
                name="secure-app",
                repo_url="https://example.com/secure-app",
                stars=5,
                description="Needs external auth",
                category="Custom",
                framework="Python",
                last_updated="2026-03-22",
            )
            project_dir = resolve_managed_project_dir(paths.config_dir, repo)
            project_dir.mkdir(parents=True, exist_ok=True)
            initialize_state_store(paths.config_dir).upsert_repo_state(
                repo_key=repo.repo_url,
                repo_url=repo.repo_url,
                repo_path=str(project_dir),
                status="ready",
                summary="secure-app ready.",
                metadata={
                    "repo_name": "secure-app",
                    "install_location": str(project_dir),
                    "run_command": "uvicorn main:app --host 127.0.0.1 --port 8080",
                    "auth_requirements": [
                        {
                            "env_var": "DUCKLN_TEST_NEEDS_TOKEN",
                            "provider": "custom",
                            "reason": "needed before startup",
                            "source": ".env.example",
                            "required": True,
                        }
                    ],
                },
            )

            result = run_prepared_repo(
                repo,
                ControlMode.HOTL,
                paths,
                runner=FakeRunner(project_dir),
                approve=lambda _prompt: True,
                display=lambda _line: None,
            )

            self.assertFalse(result.verification_passed)
            self.assertFalse(result.should_offer_repair)
            self.assertIn("required auth is still missing", result.message.lower())
            self.assertIn("DUCKLN_TEST_NEEDS_TOKEN", result.message)

    def test_dependency_install_governance_cites_go_docs_for_go_module_download(self) -> None:
        go_repo = RepoCatalogRecord(
            name="go-api",
            repo_url="https://github.com/example/go-api",
            stars=10,
            description="Go service",
            category="Custom",
            framework="Go",
            last_updated="2026-03-22",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            project_dir = resolve_managed_project_dir(paths.config_dir, go_repo)
            project_dir.mkdir(parents=True, exist_ok=True)
            (project_dir / "go.mod").write_text("module example.com/go-api\n\ngo 1.22\n", encoding="utf-8")
            displayed: list[str] = []
            approval_prompts: list[str] = []

            result = bring_up_selected_repo(
                go_repo,
                ControlMode.HOTL,
                paths,
                runner=FakeRunner(project_dir, create_requirements=False),
                approve=lambda prompt: approval_prompts.append(prompt) or True,
                display=displayed.append,
            )

            self.assertTrue(any("go.dev" in prompt for prompt in approval_prompts))
            self.assertTrue(any("go.mod" in prompt for prompt in approval_prompts))
            self.assertTrue(any("dependency install review" in message.lower() for message in displayed))
            self.assertIn("go mod download", result.executed_commands)

    def test_failed_cmake_setup_requests_missing_build_prerequisite(self) -> None:
        native_repo = RepoCatalogRecord(
            name="native-alpha",
            repo_url="https://example.com/native-alpha",
            stars=3,
            description="C++ runtime",
            category="LLM",
            framework="C++",
            last_updated="2026-03-22",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            project_dir = resolve_managed_project_dir(paths.config_dir, native_repo)
            project_dir.mkdir(parents=True, exist_ok=True)
            (project_dir / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.16)\n", encoding="utf-8")
            runner = FakeRunner(project_dir, failures={"cmake -S . -B build": "cmake: command not found"})

            result = bring_up_selected_repo(
                native_repo,
                ControlMode.HOTL,
                paths,
                runner=runner,
                approve=lambda prompt: True,
                display=lambda message: None,
            )

            self.assertEqual(FailureType.MISSING_COMPILER_BUILD_TOOLS, result.failure_type)
            self.assertEqual(RecoveryDecision.REQUEST_MISSING_PREREQUISITE, result.recovery_decision)

    def test_debug_recovery_classifies_node_runtime_and_mixed_stack_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "package.json").write_text('{"name":"ui"}\n', encoding="utf-8")
            (project_dir / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
            inspection = inspect_repo_for_bringup(self.repo, project_dir, system_probe=_fake_probe())
            specialist = DebugRecoverySpecialist()

            node_result = specialist.assess_failure(
                inspection=inspection,
                repo_family=RepoFamily.NODE_TYPESCRIPT,
                specialist_name="node-typescript",
                attempted_steps=(),
                failed_command="npm install",
                verification_failure="dependency install failed",
                error_output="npm: command not found",
            )
            mixed_result = specialist.assess_failure(
                inspection=inspection,
                repo_family=RepoFamily.PYTHON,
                specialist_name="python",
                attempted_steps=(),
                failed_command=".venv/bin/python -m pip install -r requirements.txt",
                verification_failure="Low-confidence repo-family classification.",
                error_output="package.json and requirements.txt both exist",
                low_confidence=True,
            )

            self.assertEqual(FailureType.NODE_NPM_MISMATCH, node_result.failure_type)
            self.assertEqual(RecoveryDecision.REQUEST_MISSING_PREREQUISITE, node_result.decision)
            self.assertEqual(FailureType.MIXED_STACK_SETUP_CONFLICTS, mixed_result.failure_type)
            self.assertEqual(RecoveryDecision.REROUTE_TO_OTHER_SPECIALIST, mixed_result.decision)
            self.assertEqual(RepoFamily.NODE_TYPESCRIPT, mixed_result.reroute_repo_family)

    def test_debug_recovery_classifies_npm_global_eacces_as_permission(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "package.json").write_text('{"name":"ui"}\n', encoding="utf-8")
            inspection = inspect_repo_for_bringup(self.repo, project_dir, system_probe=_fake_probe())
            specialist = DebugRecoverySpecialist()

            result = specialist.assess_failure(
                inspection=inspection,
                repo_family=RepoFamily.NODE_TYPESCRIPT,
                specialist_name="node-typescript",
                attempted_steps=(),
                failed_command="npm install -g openclaw@latest",
                verification_failure="Run README install command failed.",
                error_output="npm error code EACCES\nnpm error errno -13\nnpm error path /usr/lib/node_modules/openclaw\nnpm error permission denied",
            )

            self.assertEqual(FailureType.PERMISSION_DENIED, result.failure_type)
            self.assertEqual(RecoveryDecision.REQUEST_MISSING_PREREQUISITE, result.decision)

    def test_readme_prerequisite_check_does_not_prompt_for_approval(self) -> None:
        def fail_approval(_message: str) -> bool:
            raise AssertionError("read-only prerequisite checks should not request approval")

        decision, command = _mode_decision_for_step(
            ControlMode.HOOTLWO,
            "node -e \"const cp=require('child_process');cp.execFileSync('npm',['-v'],{stdio:'ignore'});process.exit(Number(process.versions.node.split('.')[0])>=20?0:1)\"",
            approve=fail_approval,
            prompt="Verify README prerequisite: Node.js and npm",
            step_source="readme-prerequisite",
        )

        self.assertTrue(decision.allowed)
        self.assertTrue(decision.auto_run)
        self.assertFalse(decision.requires_approval)
        self.assertIn("execFileSync('npm'", command)

    def test_debug_recovery_classifies_module_and_network_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "requirements.txt").write_text("requests\n", encoding="utf-8")
            inspection = inspect_repo_for_bringup(self.repo, project_dir, system_probe=_fake_probe())
            specialist = DebugRecoverySpecialist()

            module_result = specialist.assess_failure(
                inspection=inspection,
                repo_family=RepoFamily.PYTHON,
                specialist_name="python",
                attempted_steps=(),
                failed_command=".venv/bin/python -m alpha",
                verification_failure="runtime failed",
                error_output="ModuleNotFoundError: No module named 'alpha'",
            )
            network_result = specialist.assess_failure(
                inspection=inspection,
                repo_family=RepoFamily.PYTHON,
                specialist_name="python",
                attempted_steps=(),
                failed_command=".venv/bin/python -m pip install -r requirements.txt",
                verification_failure="install failed",
                error_output="Temporary failure in name resolution",
            )

            self.assertEqual(FailureType.MISSING_PYTHON_MODULE, module_result.failure_type)
            self.assertEqual(FailureType.NETWORK_DOWNLOAD_FAILURE, network_result.failure_type)


class WrapCommandForExecutionTargetTest(unittest.TestCase):
    def test_vm_tilde_path_uses_double_quoted_home_expansion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result, _ = _wrap_command_for_execution_target(
                config_dir=Path(temp_dir),
                execution_target="vm",
                command="test -d .",
                cwd="~/.duckln/projects/openclaw",
                preferred_vm_name="duckln-vm-test",
            )
        self.assertIsNotNone(result)
        # The path must use "${HOME}" so bash can expand it — not single-quoted '~'.
        self.assertIn("${HOME}", result)
        self.assertIn("/.duckln/projects/openclaw", result)
        # Single-quoted tilde is the old broken form; it must not appear.
        self.assertNotIn("'~", result)

    def test_vm_absolute_path_uses_shlex_quote(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result, _ = _wrap_command_for_execution_target(
                config_dir=Path(temp_dir),
                execution_target="vm",
                command="test -d .",
                cwd="/home/ubuntu/projects/openclaw",
                preferred_vm_name="duckln-vm-test",
            )
        self.assertIsNotNone(result)
        self.assertIn("/home/ubuntu/projects/openclaw", result)
        self.assertNotIn("${HOME}", result)

    def test_local_execution_passes_command_through_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result, _ = _wrap_command_for_execution_target(
                config_dir=Path(temp_dir),
                execution_target="local",
                command="echo hello",
                cwd=None,
            )
        self.assertEqual("echo hello", result)


class ScanReadmeForRunCommandsTest(unittest.TestCase):
    def test_extracts_python_run_command_from_fenced_code_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            readme = Path(tmp) / "README.md"
            readme.write_text(
                "# MyApp\n\n## Usage\n\n```bash\npython app.py\n```\n",
                encoding="utf-8",
            )
            result = scan_readme_for_run_commands(Path(tmp))
        self.assertIn("python app.py", result)

    def test_extracts_npm_start_from_inline_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            readme = Path(tmp) / "README.md"
            readme.write_text(
                "Run the app with `npm start` before anything else.\n",
                encoding="utf-8",
            )
            result = scan_readme_for_run_commands(Path(tmp))
        self.assertIn("npm start", result)

    def test_returns_empty_tuple_when_no_readme_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = scan_readme_for_run_commands(Path(tmp))
        self.assertEqual((), result)

    def test_deduplicates_identical_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            readme = Path(tmp) / "README.md"
            readme.write_text(
                "```bash\npython app.py\n```\n\n```bash\npython app.py\n```\n",
                encoding="utf-8",
            )
            result = scan_readme_for_run_commands(Path(tmp))
        self.assertEqual(1, result.count("python app.py"))

    def test_caps_results_at_four(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            readme = Path(tmp) / "README.md"
            readme.write_text(
                "```bash\npython a.py\n```\n```bash\npython b.py\n```\n"
                "```bash\npython c.py\n```\n```bash\npython d.py\n```\n"
                "```bash\npython e.py\n```\n",
                encoding="utf-8",
            )
            result = scan_readme_for_run_commands(Path(tmp))
        self.assertLessEqual(len(result), 4)

    def test_extracts_repo_specific_start_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            readme = Path(tmp) / "README.md"
            readme.write_text(
                "# OpenClaw\n\n## Run\n\n```bash\nopenclaw serve --host 0.0.0.0\n```\n",
                encoding="utf-8",
            )
            result = scan_readme_for_run_commands(Path(tmp), repo_name="openclaw")
        self.assertEqual("openclaw serve --host 0.0.0.0", result[0])
        self.assertEqual("start", classify_runtime_command(result[0]))

    def test_prefers_vm_readme_command_over_macos_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            readme = Path(tmp) / "README.md"
            readme.write_text(
                "# OpenClaw\n\n"
                "## macOS\n\n```bash\nbrew services start openclaw\n```\n\n"
                "## Ubuntu VM\n\n```bash\nopenclaw serve --host 0.0.0.0\n```\n",
                encoding="utf-8",
            )
            result = scan_readme_for_run_commands(Path(tmp), repo_name="openclaw", execution_target="vm")
        self.assertEqual("openclaw serve --host 0.0.0.0", result[0])

    def test_readme_workflow_prefers_recommended_install_sequence(self) -> None:
        repo = RepoCatalogRecord(
            name="openclaw",
            repo_url="https://github.com/openclaw/openclaw",
            stars=100,
            description="Personal AI assistant",
            category="AI",
            framework="Node",
            last_updated="2026-05-05",
        )
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "package.json").write_text('{"name":"openclaw"}', encoding="utf-8")
            (project_dir / "README.md").write_text(
                "# OpenClaw\n\n"
                "Preferred setup: run `openclaw onboard` in your terminal.\n\n"
                "## Install (recommended)\n\n"
                "```bash\n"
                "npm install -g openclaw@latest\n"
                "# or: pnpm add -g openclaw@latest\n\n"
                "openclaw onboard --install-daemon\n"
                "```\n\n"
                "## Quick start\n\n"
                "```bash\n"
                "openclaw gateway --port 18789 --verbose\n"
                "```\n\n"
                "## From source (development)\n\n"
                "```bash\n"
                "git clone https://github.com/openclaw/openclaw.git\n"
                "cd openclaw\n"
                "pnpm install\n"
                "pnpm openclaw setup\n"
                "pnpm gateway:watch\n"
                "```\n",
                encoding="utf-8",
            )
            inspection = inspect_repo_for_bringup(
                repo,
                project_dir,
                system_probe=_fake_probe(),
                runtime_provider="ollama",
                execution_target="vm",
            )

            steps = infer_readme_workflow_steps(inspection)
            plan = infer_repo_setup_plan(
                repo,
                project_dir,
                system_probe=_fake_probe(),
                runtime_provider="ollama",
                execution_target="vm",
            )

        self.assertEqual(
            (
                "node --version && npm --version",
                "npm install -g openclaw@latest",
                "openclaw onboard --install-daemon",
            ),
            tuple(step.command for step in steps),
        )
        # Plan 58 Bug B: build_plan now prepends README-prereq preflight steps
        # for tools discovered in code blocks (npm, pnpm, etc.). The original
        # readme steps remain in plan.steps after the prereq block. Split the
        # plan into prereq and readme segments and verify both.
        prereq_steps = tuple(s for s in plan.steps if s.source == "readme-prereq")
        readme_steps_in_plan = tuple(s for s in plan.steps if s.source != "readme-prereq")
        # The original readme-derived steps must still all be present in order.
        self.assertEqual(
            tuple(step.command for step in steps),
            tuple(step.command for step in readme_steps_in_plan),
        )
        # At least one prereq step should have been prepended (npm at minimum).
        self.assertTrue(prereq_steps, f"Expected prereq steps before readme steps; got {[s.source for s in plan.steps]}")
        # Order: prereq steps come first.
        prereq_indices = [i for i, s in enumerate(plan.steps) if s.source == "readme-prereq"]
        readme_indices = [i for i, s in enumerate(plan.steps) if s.source != "readme-prereq"]
        if prereq_indices and readme_indices:
            self.assertLess(max(prereq_indices), min(readme_indices))

    def test_readme_workflow_uses_llm_json_when_local_patterns_are_unusual(self) -> None:
        repo = RepoCatalogRecord(
            name="strangeapp",
            repo_url="https://github.com/example/strangeapp",
            stars=10,
            description="Unusual setup docs",
            category="Custom",
            framework="Node",
            last_updated="2026-05-05",
        )
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "README.md").write_text(
                "# StrangeApp\n\n## Bring it alive\n\nUse the project ritual below.\n\n```bash\nstrangeapp prepare\nstrangeapp launch\n```\n",
                encoding="utf-8",
            )
            inspection = inspect_repo_for_bringup(repo, project_dir, system_probe=_fake_probe(), execution_target="vm")

            steps = infer_readme_workflow_steps(
                inspection,
                llm_classifier=lambda _inspection, _snippet: (
                    '{"commands":['
                    '{"phase":"setup","command":"strangeapp prepare","reason":"README setup","confidence":0.91},'
                    '{"phase":"run","command":"strangeapp launch","reason":"README run","confidence":0.8}'
                    "]}"
                ),
            )

        self.assertEqual(("strangeapp prepare",), tuple(step.command for step in steps))
        self.assertEqual(("readme-llm",), tuple(step.source for step in steps))

    def test_readme_llm_snippet_redacts_network_and_secret_values(self) -> None:
        repo = RepoCatalogRecord(
            name="strangeapp",
            repo_url="https://github.com/example/strangeapp",
            stars=10,
            description="Unusual setup docs",
            category="Custom",
            framework="Node",
            last_updated="2026-05-05",
        )
        captured: dict[str, str] = {}

        def classifier(_inspection, snippet: str) -> str:
            captured["snippet"] = snippet
            return '{"commands":[{"phase":"setup","command":"strangeapp prepare","confidence":0.9}]}'

        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "README.md").write_text(
                "# StrangeApp\n\n"
                "## Bring it alive\n\n"
                "Internal host 192.168.1.20 and MAC aa:bb:cc:dd:ee:ff.\n"
                "Token: token=secret12345\n\n"
                "Use the project ritual below.\n\n"
                "```bash\nstrangeapp prepare\n```\n",
                encoding="utf-8",
            )
            inspection = inspect_repo_for_bringup(repo, project_dir, system_probe=_fake_probe(), execution_target="vm")
            infer_readme_workflow_steps(inspection, llm_classifier=classifier)

        self.assertNotIn("192.168.1.20", captured["snippet"])
        self.assertNotIn("aa:bb:cc:dd:ee:ff", captured["snippet"])
        self.assertNotIn("secret12345", captured["snippet"])
        self.assertIn("[REDACTED_IP]", captured["snippet"])
        self.assertIn("[REDACTED_MAC]", captured["snippet"])

    def test_recovery_plan_upgrades_node_then_returns_to_remaining_readme_steps(self) -> None:
        repo = RepoCatalogRecord(
            name="openclaw",
            repo_url="https://github.com/openclaw/openclaw",
            stars=10,
            description="AI gateway",
            category="AI",
            framework="Node",
            last_updated="2026-05-05",
        )
        preflight = RepoBringUpStep(
            purpose="Verify README prerequisite: Node.js and npm",
            command="node -e \"const cp=require('child_process');cp.execFileSync('npm',['-v'],{stdio:'ignore'});process.exit(Number(process.versions.node.split('.')[0])>=20?0:1)\"",
            verification_command="node -e \"const cp=require('child_process');cp.execFileSync('npm',['-v'],{stdio:'ignore'});process.exit(Number(process.versions.node.split('.')[0])>=20?0:1)\"",
            source="readme-prerequisite",
        )
        failed_step = RepoBringUpStep(
            purpose="Run README install command",
            command="npm install -g openclaw@latest",
            verification_command="command -v openclaw",
            source="readme",
        )
        plan = RepoBringUpPlan(
            repo=repo,
            project_dir=Path("/tmp/openclaw"),
            detected_files=("README.md", "package.json"),
            steps=(preflight, failed_step),
            summary="README path",
            repo_family=RepoFamily.NODE_TYPESCRIPT,
            specialist_name="node-typescript",
            execution_target="vm",
        )
        recovery = DebugRecoveryAssessment(
            failure_type=FailureType.NODE_NPM_MISMATCH,
            decision=RecoveryDecision.REQUEST_MISSING_PREREQUISITE,
            summary="node fix",
        )

        recovery_plan = _prerequisite_recovery_plan(
            plan=plan,
            failed_step=failed_step,
            failed_command="npm install -g openclaw@latest",
            recovery=recovery,
        )

        self.assertIsNotNone(recovery_plan)
        assert recovery_plan is not None
        self.assertIn("nodesource.com/setup_22.x", recovery_plan.steps[0].command)
        self.assertEqual("npm install -g openclaw@latest", recovery_plan.steps[1].command)

    def test_recovery_plan_asks_for_elevated_readme_install_on_global_eacces(self) -> None:
        repo = RepoCatalogRecord(
            name="openclaw",
            repo_url="https://github.com/openclaw/openclaw",
            stars=10,
            description="AI gateway",
            category="AI",
            framework="Node",
            last_updated="2026-05-05",
        )
        step = RepoBringUpStep(
            purpose="Run README install command",
            command="npm install -g openclaw@latest",
            verification_command="command -v openclaw",
            source="readme",
        )
        plan = RepoBringUpPlan(
            repo=repo,
            project_dir=Path("/tmp/openclaw"),
            detected_files=("README.md", "package.json"),
            steps=(step,),
            summary="README path",
            repo_family=RepoFamily.NODE_TYPESCRIPT,
            specialist_name="node-typescript",
            execution_target="vm",
        )
        recovery = DebugRecoveryAssessment(
            failure_type=FailureType.PERMISSION_DENIED,
            decision=RecoveryDecision.REQUEST_MISSING_PREREQUISITE,
            summary="permission fix",
        )

        recovery_plan = _prerequisite_recovery_plan(
            plan=plan,
            failed_step=step,
            failed_command="npm install -g openclaw@latest",
            recovery=recovery,
        )

        self.assertIsNotNone(recovery_plan)
        assert recovery_plan is not None
        self.assertEqual("sudo npm install -g openclaw@latest", recovery_plan.steps[0].command)
        self.assertEqual("recovery-prerequisite", recovery_plan.steps[0].source)

    def test_supervisor_final_summary_marks_setup_complete_and_launch_prompt(self) -> None:
        repo = RepoCatalogRecord(
            name="openclaw",
            repo_url="https://github.com/openclaw/openclaw",
            stars=10,
            description="AI gateway",
            category="AI",
            framework="Node",
            last_updated="2026-05-05",
        )
        outcome = RepoSetupOutcome(
            install_location="/home/ubuntu/.duckln/projects/openclaw",
            environment_path=None,
            run_command="openclaw gateway --port 18789 --verbose",
            verify_command="openclaw --version",
            manual_command=None,
            runtime_kind="cli",
            stack_family="node",
            auth_requirements=(),
            missing_auth_variables=(),
            access_hint=None,
            verification=RepoVerificationOutcome(
                verified=True,
                summary="openclaw CLI is installed",
                checks_run=("command -v openclaw",),
                verification_command="command -v openclaw",
            ),
            removal_hint="npm uninstall -g openclaw",
            changed_items=("installed README command",),
        )

        summary = _supervisor_final_summary(
            repo=repo,
            outcome=outcome,
            current_mode=ControlMode.HOOTLWO,
        )

        self.assertIn("Setup complete for openclaw.", summary)
        self.assertIn("Run command found: openclaw gateway --port 18789 --verbose. Launch it?", summary)

    def test_llm_readme_json_rejects_blocked_and_os_wrong_commands(self) -> None:
        raw = json.dumps(
            {
                "commands": [
                    {"phase": "install", "command": "brew install strangeapp", "confidence": 0.9},
                    {"phase": "setup", "command": "sudo rm -rf /", "confidence": 0.9},
                    {"phase": "setup", "command": "strangeapp setup", "confidence": 0.9},
                ]
            }
        )

        commands = _parse_llm_readme_workflow_json(raw, repo_name="strangeapp", execution_target="vm")

        self.assertEqual(("strangeapp setup",), tuple(command.command for command in commands))


def _fake_probe() -> SystemProbe:
    return SystemProbe(
        operating_system="Linux",
        architecture="x86_64",
        cpu_logical_cores=8,
        ram_bytes=16 * 1024**3,
        disk_free_bytes=100 * 1024**3,
        python_version="3.11.8",
        gpu=GpuProbeState(
            backend="cpu",
            summary="No CUDA-capable GPU detected.",
            cuda_capable=False,
            cuda_available=False,
            mps_capable=False,
            mps_available=False,
        ),
    )


if __name__ == "__main__":
    unittest.main()
