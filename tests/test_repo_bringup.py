"""Tests for repo bring-up foundation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent.probe import GpuProbeState, SystemProbe
from duckln.config import resolve_config_paths
from duckln.modes import ControlMode
from duckln.repo_bringup import (
    DebugRecoverySpecialist,
    FailureType,
    RepoFamily,
    RecoveryDecision,
    bring_up_selected_repo,
    classify_repo_family,
    infer_repo_setup_plan,
    inspect_repo_for_bringup,
    resolve_managed_project_dir,
)
from duckln.shell import CommandResult
from state.access import read_session_summary_state
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

            plan = infer_repo_setup_plan(self.repo, project_dir)

            self.assertEqual(("README.md", "requirements.txt"), plan.detected_files)
            self.assertEqual("python -m venv .venv", plan.steps[0].command)
            self.assertEqual(".venv/bin/python -m pip install -r requirements.txt", plan.steps[1].command)
            self.assertEqual(RepoFamily.PYTHON, plan.repo_family)
            self.assertEqual("python", plan.specialist_name)

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
            self.assertEqual("pnpm install --frozen-lockfile", plan.steps[0].command)

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
            self.assertEqual(".venv/bin/python -m pip install -r requirements.txt", plan.steps[1].command)

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
            self.assertTrue(any("Repo bring-up foundation completed." == message for message in displayed))

    def test_bring_up_selected_repo_in_hootlwo_waits_when_clone_needs_approval(self) -> None:
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

            self.assertEqual((), result.executed_commands)
            self.assertFalse(project_dir.exists())
            self.assertTrue(any("suggested only" in message or "Approve" in message for message in displayed))

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
            self.assertTrue(any("retry python with the smallest corrected setup step" in message for message in displayed))
            self.assertIn(
                "Debug/Recovery classified dependency_install_failure",
                read_session_summary_state(paths.config_dir, session_id="repo bring-up recovery alpha") or "",
            )

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
