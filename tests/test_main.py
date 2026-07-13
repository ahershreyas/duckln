"""Tests for slash commands and the runtime command palette."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
import re
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from duckln.ai_client import Provider
from duckln.config import (
    AppConfig,
    OnboardingResult,
    SessionExitRequested,
    load_app_config,
    resolve_config_paths,
    save_app_config,
)
from duckln.main import (
    _build_chat_approve_prompt,
    _build_chat_secret_prompt,
    _build_chat_select_prompt,
    _build_chat_text_prompt,
    _build_named_runtime_prerequisite_plan,
    _drain_terminal_runtime_incidents,
    _discover_repo_run_command,
    _handle_pending_repo_followup,
    _handle_repo_access_action,
    _handle_pending_runtime_repair_followup,
    _handle_global_browser_link_intent,
    _handle_global_stop_command,
    _handle_repo_logs_action,
    _handle_repo_restart_action,
    _handle_repo_remove_action,
    _handle_repo_run_action,
    _handle_repo_stop_action,
    _begin_active_deploy_objective,
    _clear_active_deploy_objective,
    _fetch_official_doc_excerpt,
    _lookup_vm_failure_recipe,
    _orchestrate_repo_to_running,
    _render_active_deploy_status_summary,
    _render_runtime_footer,
    extract_failing_package,
    lookup_repo_failure_recipe,
    _render_chat_objective_status,
    _render_repo_preflight_summary,
    _render_repo_requirements_summary,
    _runtime_error_text_for_search,
    _respond_to_free_text,
    _run_runtime_repair_workflow,
    _runtime_workflow_blocks_incident_repair,
    _footer_execution_label,
    _sync_chat_activity_from_output,
    _sync_chat_execution_context,
    _sync_chat_objective_status,
    _session_memory_state,
    _normalize_runtime_slash_command,
    _select_or_create_gcp_project,
    _looks_like_global_stop_command,
    get_slash_command_descriptors,
    handle_session_command,
    main,
)
from duckln.vm import VmProvisionResult, VmSetupFailureType
from duckln.conversation_agent import ConversationTurn, FreeTextReply
from duckln.conversation_routes.decision_responses import build_utility_fallback_reply
from duckln.modes import ControlMode
from duckln.repair_intake import DependencyApprovalRequest, DependencyApprovalItem
from duckln.repair_intake import DependencyApprovalDecision
from duckln.repair_intake import plan_runtime_repair, summarize_failure_incident
from duckln.web_runtime import RuntimeRepairEvidence
from duckln.repo_bringup import assess_repo_preflight, infer_repo_setup_plan, resolve_managed_project_dir
from duckln.repos import (
    CANCEL_REPO_SELECTION,
    CUSTOM_GITHUB_REPO_CHOICE,
    RECENT_CUSTOM_REPOS_CHOICE,
    format_repo_catalog_choice,
    open_repo_catalog,
)
from duckln.selections import ConversationChoiceOption, ConversationChoicePrompt, conversation_choice_labels, resolve_conversation_choice
from duckln.textual_ui import WebPreviewSnapshot, fetch_web_preview_snapshot
from state.access import initialize_managed_memory_state, write_config_snapshot, write_followup_state, write_workflow_state
from state.access import read_followup_state, read_workflow_state
from state.repo_catalog import RepoCatalogRecord, RepoCatalogRefreshResult, load_sorted_local_repo_catalog, resolve_local_repo_catalog_cache_path
from state.store import initialize_state_store
from agent.probe import GpuProbeState, SystemProbe


@dataclass
class FakeResponse:
    status_code: int
    payload: dict
    text: str = ""

    def json(self) -> dict:
        return self.payload


# Plan 121: the provider flow now does a real round-trip (verify_live_reply) after the
# key validates. This canned reply satisfies every adapter's parse_conversation_text.
_CONVERSATION_REPLY_PAYLOAD = {
    "choices": [{"message": {"content": "Connection OK — test reply."}}],
    "content": [{"type": "text", "text": "Connection OK — test reply."}],
    "response": "Connection OK — test reply.",
}


class FakeHttpClient:
    def __init__(self, responses: dict[str, object] | None = None) -> None:
        self.responses = responses or {}

    def get(self, url: str, *, headers: dict[str, str], timeout: float) -> FakeResponse:
        response = self.responses[url]
        if isinstance(response, dict):
            auth_header = headers.get("Authorization") or headers.get("x-api-key") or ""
            return response[auth_header]
        return response

    def post(self, url: str, *, headers: dict[str, str], json: dict, timeout: float) -> FakeResponse:
        # Configured URLs win (so tests that pin a specific conversation reply keep
        # working); an un-configured POST is the live verification round-trip, answered
        # with a generic reply so verify_live_reply succeeds.
        if url in self.responses:
            response = self.responses[url]
            if isinstance(response, dict):
                return response[json["name"]]
            return response
        return FakeResponse(status_code=200, payload=dict(_CONVERSATION_REPLY_PAYLOAD))


class FakeTerminalInterface:
    def __init__(self) -> None:
        self.run_commands: list[tuple[str, str | None]] = []
        self.interrupt_calls = 0
        self.preview_requests: list[tuple[str, str | None]] = []
        self.incidents: list[dict[str, object]] = []
        self.activity_calls: list[tuple[str, bool]] = []
        self.terminal_url: str | None = None
        self.open_terminal_url_calls = 0
        self.connection_updates: list[dict[str, object]] = []

    def run_terminal_command(self, *, command: str, cwd: str | None = None) -> bool:
        self.run_commands.append((command, cwd))
        return True

    def open_web_preview(self, *, url: str, title_hint: str | None = None) -> bool:
        self.preview_requests.append((url, title_hint))
        return True

    def last_terminal_url(self) -> str | None:
        return self.terminal_url

    def open_last_terminal_url(self) -> bool:
        self.open_terminal_url_calls += 1
        return bool(self.terminal_url)

    def update_connection(self, **kwargs) -> None:
        self.connection_updates.append(dict(kwargs))

    def interrupt_terminal(self) -> bool:
        self.interrupt_calls += 1
        return True

    def consume_terminal_incidents(self) -> tuple[dict[str, object], ...]:
        queued = tuple(self.incidents)
        self.incidents.clear()
        return queued

    def set_activity_text(self, message: str, *, spinner: bool = True) -> None:
        self.activity_calls.append((message, spinner))

    def clear_activity_text(self) -> None:
        self.activity_calls.append(("", False))


class FakePullProcess:
    def __init__(self, lines: tuple[str, ...] = ("pulling manifest\n", "success\n"), exit_code: int = 0) -> None:
        self.stdout = iter(lines)
        self._exit_code = exit_code

    def wait(self) -> int:
        return self._exit_code


class FakeActivityChat:
    def __init__(self) -> None:
        self.activity_calls: list[tuple[str, bool]] = []
        self.cleared = 0
        self.objective_calls: list[str] = []
        self.objective_activity_calls: list[tuple[str | None, bool]] = []
        self.objective_keys: list[str | None] = []
        self.objective_cleared = 0

    def set_activity_text(self, message: str, *, spinner: bool = True) -> None:
        self.activity_calls.append((message, spinner))

    def clear_activity_text(self) -> None:
        self.cleared += 1

    def update_objective_status(
        self,
        message: str,
        *,
        activity_message: str | None = None,
        activity_spinner: bool = False,
        objective_key: str | None = None,
    ) -> None:
        self.objective_calls.append(message)
        self.objective_activity_calls.append((activity_message, activity_spinner))
        self.objective_keys.append(objective_key)

    def clear_objective_status(self) -> None:
        self.objective_cleared += 1


class VmFailureRecipeLookupTest(unittest.TestCase):
    def _failure(self, *, message: str, technical_details: str = "") -> VmProvisionResult:
        return VmProvisionResult(
            ok=False,
            vm_name="duckln-vm",
            message=message,
            connection_commands=(),
            failure_type=VmSetupFailureType.UNKNOWN,
            recommended_action="retry",
            technical_details=technical_details,
        )

    def test_recipe_matches_already_exists_signature(self) -> None:
        recipe = _lookup_vm_failure_recipe(
            self._failure(message="instance 'duckln-vm' already exists; choose another name."),
        )
        self.assertIsNotNone(recipe)
        self.assertIn("multipass.run", recipe.docs_url)
        self.assertEqual("multipass delete {vm_name} --purge", recipe.fix_command)
        self.assertTrue(recipe.auto_apply)

    def test_recipe_matches_dns_signature(self) -> None:
        recipe = _lookup_vm_failure_recipe(
            self._failure(message="could not resolve host: api.snapcraft.io"),
        )
        self.assertIsNotNone(recipe)
        self.assertIn("DNS", recipe.description)

    def test_recipe_returns_none_for_unknown_signature(self) -> None:
        recipe = _lookup_vm_failure_recipe(self._failure(message="something else entirely"))
        self.assertIsNone(recipe)


class RepoFailureRecipeLookupTest(unittest.TestCase):
    def test_npm_eacces_signature_matches(self) -> None:
        recipe = lookup_repo_failure_recipe("npm ERR! EACCES permission denied opening cache")
        self.assertIsNotNone(recipe)
        self.assertIn("npm", recipe.fix_command)
        self.assertIn("docs.npmjs.com", recipe.docs_url)

    def test_pip_resolution_impossible_signature_matches(self) -> None:
        recipe = lookup_repo_failure_recipe("ERROR: pip's dependency resolver: ResolutionImpossible")
        self.assertIsNotNone(recipe)
        self.assertIn("pip", recipe.docs_url)

    def test_docker_daemon_signature_matches(self) -> None:
        recipe = lookup_repo_failure_recipe("Cannot connect to the Docker daemon at unix:///var/run/docker.sock")
        self.assertIsNotNone(recipe)
        self.assertIn("docker.com", recipe.docs_url)

    def test_unknown_signature_returns_none(self) -> None:
        self.assertIsNone(lookup_repo_failure_recipe("totally unrelated error"))


class RuntimePrerequisitePlanTest(unittest.TestCase):
    def test_vm_node_prerequisite_uses_versioned_nodesource_and_verification(self) -> None:
        plan = _build_named_runtime_prerequisite_plan(
            dependency="node",
            execution_target="vm",
            reason="Node is missing or too old.",
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual("nodesource", plan.installer)
        self.assertIn("setup_22.x", plan.install_command)
        self.assertIn("Number(process.versions.node", plan.verification_command or "")


class TerminalConnectionLabelTest(unittest.TestCase):
    class _Chat:
        def __init__(self) -> None:
            self.connection_calls: list[dict[str, object]] = []

        def update_connection(self, **kwargs: object) -> None:
            self.connection_calls.append(kwargs)

    def test_footer_uses_local_when_docker_target_has_no_attached_container(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            write_config_snapshot(config_dir, {"execution_target": "docker"})
            write_workflow_state(config_dir, {"active_runtime_execution_target": "docker"})

            self.assertEqual("Local", _footer_execution_label(config_dir))

    def test_sync_chat_keeps_local_for_stale_docker_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            write_workflow_state(config_dir, {"active_runtime_execution_target": "docker"})
            chat = self._Chat()

            _sync_chat_execution_context(chat=chat, config_dir=config_dir)

            self.assertEqual("local", chat.connection_calls[-1]["connection_type"])

    def test_sync_chat_keeps_local_for_running_docker_without_terminal_attach(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            write_workflow_state(
                config_dir,
                {
                    "active_runtime_execution_target": "docker",
                    "active_runtime_docker_name": "open-webui-stack",
                    "active_runtime_status": "running",
                },
            )
            chat = self._Chat()

            _sync_chat_execution_context(chat=chat, config_dir=config_dir)

            self.assertEqual("Local", _footer_execution_label(config_dir))
            self.assertEqual("local", chat.connection_calls[-1]["connection_type"])

    def test_sync_chat_keeps_local_for_saved_vm_target_without_terminal_attach(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            write_config_snapshot(
                config_dir,
                {
                    "execution_target": "vm",
                    "active_vm_name": "duckln-vm-fi",
                },
            )
            write_workflow_state(
                config_dir,
                {
                    "active_runtime_execution_target": "vm",
                    "active_runtime_vm_name": "duckln-vm-fi",
                    "active_runtime_status": "running",
                },
            )
            chat = self._Chat()

            _sync_chat_execution_context(chat=chat, config_dir=config_dir)

            self.assertEqual("Local", _footer_execution_label(config_dir))
            self.assertEqual("local", chat.connection_calls[-1]["connection_type"])

    def test_sync_chat_keeps_header_local_even_for_attached_vm_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            write_workflow_state(
                config_dir,
                {
                    "active_runtime_execution_target": "vm",
                    "active_runtime_vm_name": "duckln-vm-fi",
                    "active_runtime_status": "interactive",
                    "active_runtime_command_kind": "attach",
                },
            )
            chat = self._Chat()

            _sync_chat_execution_context(chat=chat, config_dir=config_dir)

            self.assertEqual("Local", _footer_execution_label(config_dir))
            self.assertEqual("local", chat.connection_calls[-1]["connection_type"])

    def test_sync_chat_uses_docker_when_container_is_attached(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            write_workflow_state(
                config_dir,
                {
                    "active_runtime_execution_target": "docker",
                    "active_runtime_docker_name": "open-webui-stack",
                    "active_runtime_status": "interactive",
                    "active_runtime_command_kind": "attach",
                },
            )
            chat = self._Chat()

            _sync_chat_execution_context(chat=chat, config_dir=config_dir)

            self.assertEqual("Docker", _footer_execution_label(config_dir))
            self.assertEqual("docker", chat.connection_calls[-1]["connection_type"])
            self.assertEqual("open-webui-stack", chat.connection_calls[-1]["docker_name"])

    def test_runtime_footer_does_not_render_target_label(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            write_config_snapshot(config_dir, {"execution_target": "docker"})

            footer = re.sub(r"\x1b\[[0-9;]*m", "", _render_runtime_footer(config_dir))

            self.assertNotIn("target", footer.lower())
            self.assertNotIn("Docker", footer)


class DeployObjectiveTransitionsTest(unittest.TestCase):
    def test_begin_then_clear_round_trip(self) -> None:
        from state.access import read_workflow_state

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            repo = RepoCatalogRecord(
                name="alpha",
                repo_url="https://example.com/alpha",
                stars=10,
                description="t",
                category="LLM",
                framework="Python",
                last_updated="2026-04-30",
            )
            _begin_active_deploy_objective(paths=paths, repo=repo, execution_target="vm")
            workflow = read_workflow_state(paths.config_dir)
            self.assertEqual("repo_deploy", workflow.get("active_objective_kind"))
            self.assertEqual("alpha", workflow.get("active_objective_repo_name"))

            _clear_active_deploy_objective(paths=paths)
            workflow = read_workflow_state(paths.config_dir)
            self.assertIsNone(workflow.get("active_objective_kind"))


class ExtractFailingPackageTest(unittest.TestCase):
    def test_pip_no_matching_distribution(self) -> None:
        pkg = extract_failing_package("ERROR: No matching distribution found for badpkg==1.2.3")
        self.assertEqual(("pip", "badpkg"), pkg)

    def test_pip_could_not_find_a_version(self) -> None:
        pkg = extract_failing_package("Could not find a version that satisfies the requirement weirddep>=2.0")
        self.assertEqual(("pip", "weirddep"), pkg)

    def test_npm_404(self) -> None:
        pkg = extract_failing_package("npm ERR! 404 Not Found - GET https://registry.npmjs.org/missingpkg - 'missingpkg' is not in this registry")
        self.assertEqual(("npm", "missingpkg"), pkg)

    def test_unrelated_message_returns_none(self) -> None:
        self.assertIsNone(extract_failing_package("compile failed: missing brace"))


class InternetSkillTest(unittest.TestCase):
    def test_default_state_is_off(self) -> None:
        from duckln.internet_skill import is_internet_enabled

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            self.assertFalse(is_internet_enabled(paths.config_dir))

    def test_set_and_read_round_trip(self) -> None:
        from duckln.internet_skill import is_internet_enabled, set_internet_enabled

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            set_internet_enabled(paths.config_dir, True)
            self.assertTrue(is_internet_enabled(paths.config_dir))
            set_internet_enabled(paths.config_dir, False)
            self.assertFalse(is_internet_enabled(paths.config_dir))

    def test_search_returns_empty_when_off(self) -> None:
        from duckln.internet_skill import internet_search_summary

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            results = internet_search_summary("anything", config_dir=paths.config_dir)
            self.assertEqual((), results)

    def test_status_renderer(self) -> None:
        from duckln.internet_skill import render_internet_status, set_internet_enabled

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            self.assertIn("OFF", render_internet_status(paths.config_dir))
            set_internet_enabled(paths.config_dir, True)
            self.assertIn("ON", render_internet_status(paths.config_dir))


class StatusContradictionTest(unittest.TestCase):
    def test_failed_overrides_waiting_approval(self) -> None:
        workflow = SimpleNamespace(
            active_objective_kind="runtime_repair",
            active_objective_repo_name="openclaw",
            active_objective_attempt_count=0,
            active_objective_max_attempts=5,
            active_objective_status="failed",
            active_objective_execution_target="vm",
            active_objective_requires_user_decision=True,
            active_repair_phase="vm_repair_escalation",
            active_incident_category="vm_bootstrap_failure",
        )
        rendered = _render_chat_objective_status(workflow)
        self.assertIn("failed", rendered)
        self.assertIn("decide next step", rendered)
        self.assertNotIn("waiting approval", rendered)
        self.assertNotIn("attempt 0/5", rendered)

    def test_runtime_escalation_chooser_returns_retry_when_user_picks_retry(self) -> None:
        from duckln.main import (
            _surface_runtime_escalation_choice,
            _RUNTIME_ESCALATION_CHOICE_RETRY,
        )

        captured: dict[str, object] = {}

        class FakeChat:
            supports_live = True

            def select_choice(self, message, choices, **_kwargs):
                captured["message"] = message
                captured["choices"] = tuple(choices)
                return choices[0]

        outputs: list[str] = []
        chosen = _surface_runtime_escalation_choice(
            repo_name="openclaw",
            blocker_summary="VM transport failed five times in a row.",
            max_attempts=5,
            current_target="vm",
            chat=FakeChat(),
            display_output=outputs.append,
        )

        self.assertEqual(_RUNTIME_ESCALATION_CHOICE_RETRY, chosen)
        self.assertIn("openclaw", captured["message"])
        self.assertTrue(any("Retry" in choice for choice in captured["choices"]))
        self.assertTrue(any("Switch" in choice for choice in captured["choices"]))
        self.assertTrue(any("Pause" in choice for choice in captured["choices"]))
        self.assertIn("VM transport failed", " ".join(outputs))

    def test_runtime_escalation_chooser_omits_switch_local_when_already_local(self) -> None:
        from duckln.main import _surface_runtime_escalation_choice

        captured: dict[str, object] = {}

        class FakeChat:
            supports_live = True

            def select_choice(self, message, choices, **_kwargs):
                captured["choices"] = tuple(choices)
                return choices[-1]  # pause

        _surface_runtime_escalation_choice(
            repo_name="openclaw",
            blocker_summary="local pip install failure",
            max_attempts=3,
            current_target="local",
            chat=FakeChat(),
            display_output=lambda _msg: None,
        )

        self.assertFalse(any("Switch" in choice for choice in captured["choices"]))

    def test_runtime_escalation_chooser_falls_back_to_pause_when_no_live_chat(self) -> None:
        from duckln.main import (
            _surface_runtime_escalation_choice,
            _RUNTIME_ESCALATION_CHOICE_PAUSE,
        )

        outputs: list[str] = []
        chosen = _surface_runtime_escalation_choice(
            repo_name="openclaw",
            blocker_summary="VM transport failed",
            max_attempts=5,
            current_target="vm",
            chat=None,
            display_output=outputs.append,
        )
        self.assertEqual(_RUNTIME_ESCALATION_CHOICE_PAUSE, chosen)
        # Headline + fallback hint should both render so the user is never silenced.
        self.assertEqual(2, len(outputs))
        self.assertIn("openclaw", outputs[0])
        self.assertIn("/repos", outputs[1])

    def test_active_with_user_decision_shows_waiting_on_you(self) -> None:
        workflow = SimpleNamespace(
            active_objective_kind="runtime_repair",
            active_objective_repo_name="openclaw",
            active_objective_attempt_count=2,
            active_objective_max_attempts=5,
            active_objective_status="active",
            active_objective_execution_target="vm",
            active_objective_requires_user_decision=True,
            active_repair_phase=None,
            active_incident_category=None,
        )
        rendered = _render_chat_objective_status(workflow)
        self.assertIn("waiting on you", rendered)

    def test_awaiting_user_action_surfaces_escalation_chooser(self) -> None:
        from duckln.main import (
            _surface_runtime_escalation_choice,
            _RUNTIME_ESCALATION_CHOICE_RETRY,
            _RUNTIME_ESCALATION_CHOICE_PAUSE,
        )

        captured: dict[str, object] = {}

        class FakeChat:
            supports_live = True

            def select_choice(self, message, choices, **_kwargs):
                captured["message"] = message
                captured["choices"] = tuple(choices)
                # Simulate user picking Retry (first option)
                return choices[0]

        outputs: list[str] = []
        # The awaiting_user_action branch now calls _surface_runtime_escalation_choice
        # with the specialist_message as the blocker so the user sees what is needed
        # before they can decide (e.g. "multipass not installed").
        chosen = _surface_runtime_escalation_choice(
            repo_name="openclaw",
            blocker_summary="Multipass is not installed — VM path unavailable.",
            max_attempts=5,
            current_target="vm",
            chat=FakeChat(),
            display_output=outputs.append,
        )

        self.assertEqual(_RUNTIME_ESCALATION_CHOICE_RETRY, chosen)
        self.assertIn("Multipass is not installed", " ".join(outputs))
        self.assertIn("openclaw", captured["message"])
        self.assertTrue(any("Retry" in choice for choice in captured["choices"]))
        self.assertTrue(any("Switch" in choice for choice in captured["choices"]))


class ParallelPrepChecksTest(unittest.TestCase):
    def test_parallel_prep_runs_disk_and_vm_checks_concurrently(self) -> None:
        from duckln.main import _run_parallel_prep_checks

        outcomes = _run_parallel_prep_checks(
            execution_target="local",
            vm_name=None,
            display=lambda _msg: None,
        )
        self.assertIn("disk_space", outcomes)
        self.assertIn("vm_transport", outcomes)
        self.assertIn("not applicable", outcomes["vm_transport"])


class ReposStatusRendererTest(unittest.TestCase):
    def test_status_summary_returns_no_objective_message_when_none(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            text = _render_active_deploy_status_summary(paths.config_dir)
            self.assertIn("No active Duckln deploy", text)

    def test_status_summary_renders_active_deploy_objective(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            repo = RepoCatalogRecord(
                name="alpha",
                repo_url="https://example.com/alpha",
                stars=10,
                description="t",
                category="LLM",
                framework="Python",
                last_updated="2026-04-30",
            )
            _begin_active_deploy_objective(paths=paths, repo=repo, execution_target="vm")
            text = _render_active_deploy_status_summary(paths.config_dir)
            self.assertIn("alpha", text)
            self.assertIn("vm", text)
            self.assertIn("repo deploy", text)
            self.assertIn("phase", text)


class SummarizeRunHistoryTest(unittest.TestCase):
    def test_returns_empty_for_single_row(self) -> None:
        from duckln.main import _summarize_run_history_rows
        from types import SimpleNamespace as NS

        digest = _summarize_run_history_rows([
            NS(command_name="run", status="success", summary="ok", finished_at=""),
        ])
        self.assertEqual("", digest)

    def test_surfaces_most_common_command(self) -> None:
        from duckln.main import _summarize_run_history_rows
        from types import SimpleNamespace as NS

        rows = [
            NS(command_name="set_up_repo", status="success", summary="ok", finished_at=""),
            NS(command_name="set_up_repo", status="success", summary="ok", finished_at=""),
            NS(command_name="run_repo", status="success", summary="ok", finished_at=""),
        ]
        digest = _summarize_run_history_rows(rows)
        self.assertIn("most run = set_up_repo", digest)
        self.assertIn("(2×)", digest)

    def test_surfaces_failure_rate_and_most_failing(self) -> None:
        from duckln.main import _summarize_run_history_rows
        from types import SimpleNamespace as NS

        rows = [
            NS(command_name="run_repo", status="failed", summary="x", finished_at=""),
            NS(command_name="run_repo", status="failed", summary="x", finished_at=""),
            NS(command_name="set_up_repo", status="success", summary="x", finished_at=""),
            NS(command_name="set_up_repo", status="success", summary="x", finished_at=""),
        ]
        digest = _summarize_run_history_rows(rows)
        self.assertIn("failure rate = 50%", digest)
        self.assertIn("most-failing = run_repo", digest)


class SkillsCommandTest(unittest.TestCase):
    def test_skills_renderer_returns_hint_when_no_skills(self) -> None:
        from duckln.main import _render_skills_summary

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            from state.access import initialize_managed_memory_state

            initialize_managed_memory_state(paths.config_dir)
            text = _render_skills_summary(paths.config_dir)
            self.assertIn("Duckln skills (3 available", text)
            self.assertIn("Repo README bring-up", text)
            self.assertIn("Debug / Recovery", text)
            self.assertIn("VM / Docker target routing", text)

    def test_skills_renderer_lists_materialized_skill_notes(self) -> None:
        from duckln.main import _render_skills_summary
        from state.access import initialize_managed_memory_state, write_skill_memory_state

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            initialize_managed_memory_state(paths.config_dir)
            write_skill_memory_state(
                paths.config_dir,
                slug="internet-search",
                title="Internet search via DuckDuckGo",
                summary="Bounded web search for local models.",
            )
            write_skill_memory_state(
                paths.config_dir,
                slug="deploy-python-fastapi",
                title="Deploy: Python FastAPI",
                summary="Curated bring-up recipe.",
            )
            text = _render_skills_summary(paths.config_dir)
            self.assertIn("Duckln skills (5 available", text)
            self.assertIn("Internet search via DuckDuckGo", text)
            self.assertIn("Deploy: Python FastAPI", text)

    def test_skills_renderer_picks_up_skill_files_without_db_record(self) -> None:
        from duckln.main import _render_skills_summary
        from state.access import initialize_managed_memory_state

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            initialize_managed_memory_state(paths.config_dir)
            skills_dir = paths.config_dir / "memory" / "skills"
            skills_dir.mkdir(parents=True, exist_ok=True)
            (skills_dir / "rust-setup.md").write_text("# Rust setup\n\nCargo bring-up notes.\n", encoding="utf-8")

            text = _render_skills_summary(paths.config_dir)

            self.assertIn("Duckln skills (4 available", text)
            self.assertIn("Rust setup", text)

    def test_tools_and_mcp_renderers_show_active_contract(self) -> None:
        from duckln.main import _render_mcp_summary, _render_tools_summary

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})

            tools_text = _render_tools_summary(paths.config_dir)
            mcp_text = _render_mcp_summary(paths.config_dir)

            self.assertIn("Duckln tools", tools_text)
            self.assertIn("shell.command_runner", tools_text)
            self.assertIn("MCP", mcp_text)


class VmNaturalLanguageCommandTest(unittest.TestCase):
    def _runner(self, vm_names: tuple[str, ...]):
        from duckln.shell import CommandResult

        class Runner:
            def __init__(self) -> None:
                self.commands: list[str] = []

            def run(self, command: str, **kwargs):
                self.commands.append(command)
                if command == "multipass list --format json":
                    payload = {"list": {name: {} for name in vm_names}}
                    return CommandResult(command, 0, json.dumps(payload), "", False, 0.0)
                if command.startswith("multipass info "):
                    name = command.split()[2]
                    return CommandResult(command, 0, json.dumps({"info": {name: {"state": "Running"}}}), "", False, 0.0)
                return CommandResult(command, 0, "", "", False, 0.0)

        return Runner()

    def test_delete_all_vms_except_is_vm_action_not_repo_removal(self) -> None:
        from duckln.main import _handle_vm_management_free_text

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            runner = self._runner(("duckln-vm-100", "duckln-vm-200", "duckln-vm-openclaw"))
            displayed: list[str] = []

            with patch("duckln.main.is_multipass_installed", return_value=True):
                handled = _handle_vm_management_free_text(
                    message="delete all VMs except duckln-vm-200",
                    current=AppConfig(provider=Provider.OPENAI, model="gpt-4o-mini", api_key="key", mode=ControlMode.HITL),
                    paths=paths,
                    display_output=displayed.append,
                    select_prompt=None,
                    approve_prompt=lambda message: True,
                    terminal_interface=None,
                    runner=runner,
                )

            self.assertTrue(handled)
            self.assertIn("multipass delete duckln-vm-100 --purge", runner.commands)
            self.assertIn("multipass delete duckln-vm-openclaw --purge", runner.commands)
            self.assertNotIn("multipass delete duckln-vm-200 --purge", runner.commands)
            self.assertTrue(any("deleted VM" in message for message in displayed))

    def test_run_vm_by_name_sets_target_and_opens_terminal(self) -> None:
        from duckln.main import _handle_vm_management_free_text
        from state.access import read_config_snapshot

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            runner = self._runner(("duckln-vm-openclaw",))
            terminal = FakeTerminalInterface()
            displayed: list[str] = []

            with patch("duckln.main.is_multipass_installed", return_value=True):
                handled = _handle_vm_management_free_text(
                    message="run duckln-vm-openclaw",
                    current=AppConfig(provider=Provider.OPENAI, model="gpt-4o-mini", api_key="key", mode=ControlMode.HITL),
                    paths=paths,
                    display_output=displayed.append,
                    select_prompt=None,
                    approve_prompt=None,
                    terminal_interface=terminal,
                    runner=runner,
                )

            self.assertTrue(handled)
            self.assertIn(("multipass shell duckln-vm-openclaw", None), terminal.run_commands)
            snapshot = read_config_snapshot(paths.config_dir)
            self.assertEqual("vm", snapshot["execution_target"])
            self.assertEqual("duckln-vm-openclaw", snapshot["active_vm_name"])


class RepoTargetLabelTest(unittest.TestCase):
    def test_repo_target_label_includes_machine_or_container_name(self) -> None:
        from duckln.main import _repo_target_label
        from types import SimpleNamespace

        self.assertEqual(
            "VM duckln-vm-200",
            _repo_target_label(SimpleNamespace(execution_target="vm", vm_name="duckln-vm-200", metadata={})),
        )
        self.assertEqual(
            "Docker openclaw-web",
            _repo_target_label(SimpleNamespace(execution_target="docker", vm_name=None, metadata={"container_name": "openclaw-web"})),
        )
        self.assertEqual(
            "AWS duckln-gpu eu-west-2",
            _repo_target_label(
                SimpleNamespace(
                    execution_target="aws",
                    vm_name=None,
                    metadata={"cloud_resource_name": "duckln-gpu", "cloud_region": "eu-west-2"},
                )
            ),
        )


class SessionSummaryOnExitTest(unittest.TestCase):
    def test_writes_session_summary_with_active_repo_and_target(self) -> None:
        from duckln.main import _write_session_summary_on_exit
        from duckln.config import AppConfig
        from duckln.modes import ControlMode
        from duckln.config import Provider
        from state.access import (
            initialize_managed_memory_state,
            write_workflow_state,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            initialize_managed_memory_state(paths.config_dir)
            write_workflow_state(
                paths.config_dir,
                {
                    "active_repo_name": "openclaw",
                    "active_objective_repo_name": "openclaw",
                    "active_objective_kind": "runtime_repair",
                    "active_repair_phase": "runtime_verified",
                    "active_objective_execution_target": "vm",
                },
            )
            current = AppConfig(
                provider=Provider.OLLAMA,
                model="gemma2:2b",
                api_key=None,
                base_url=None,
                mode=ControlMode.HOOTLWO,
                onboarding_complete=True,
            )
            _write_session_summary_on_exit(paths=paths, current=current)

            sessions_dir = paths.config_dir / "memory" / "sessions"
            files = list(sessions_dir.glob("exit-latest.md"))
            self.assertEqual(1, len(files), "exit-latest summary should land in memory/sessions/")
            text = files[0].read_text(encoding="utf-8")
            self.assertIn("openclaw", text)
            self.assertIn("vm", text)
            self.assertIn("Ollama", text)
            self.assertIn("gemma2:2b", text)
            self.assertIn("runtime_verified", text)

    def test_session_summary_writer_silently_skips_when_current_is_none(self) -> None:
        from duckln.main import _write_session_summary_on_exit

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            # No exception should be raised, no file should be written.
            _write_session_summary_on_exit(paths=paths, current=None)
            sessions_dir = paths.config_dir / "memory" / "sessions"
            self.assertFalse(any(sessions_dir.glob("exit-latest.md")) if sessions_dir.exists() else False)


class FetchOfficialDocExcerptTest(unittest.TestCase):
    def test_excerpt_strips_html_and_truncates(self) -> None:
        class _FakeResponse:
            status_code = 200
            text = "<html><head><style>x{}</style></head><body><h1>Multipass</h1><p>" + ("alpha " * 200) + "</p></body></html>"

        class _FakeClient:
            def get(self_inner, url):
                self_inner.last_url = url
                return _FakeResponse()

            def close(self_inner):
                pass

        excerpt = _fetch_official_doc_excerpt(
            "https://multipass.run/docs/troubleshooting",
            http_client_factory=_FakeClient,
        )
        self.assertIsNotNone(excerpt)
        self.assertNotIn("<", excerpt)
        self.assertNotIn(">", excerpt)
        self.assertIn("Multipass", excerpt)
        self.assertLessEqual(len(excerpt), 700)  # 600 char cap + ellipsis

    def test_excerpt_returns_none_on_non_200(self) -> None:
        class _FakeResponse:
            status_code = 503
            text = ""

        class _FakeClient:
            def get(self_inner, url):
                return _FakeResponse()

            def close(self_inner):
                pass

        result = _fetch_official_doc_excerpt(
            "https://multipass.run/docs/troubleshooting",
            http_client_factory=_FakeClient,
        )
        self.assertIsNone(result)

    def test_excerpt_returns_none_on_client_exception(self) -> None:
        class _FailingClient:
            def get(self_inner, url):
                raise RuntimeError("network down")

            def close(self_inner):
                pass

        result = _fetch_official_doc_excerpt(
            "https://multipass.run/docs/troubleshooting",
            http_client_factory=_FailingClient,
        )
        self.assertIsNone(result)


class OrchestrateRepoToRunningTest(unittest.TestCase):
    def _make_repo(self) -> RepoCatalogRecord:
        return RepoCatalogRecord(
            name="alpha",
            repo_url="https://example.com/alpha",
            stars=10,
            description="Test repo",
            category="LLM",
            framework="Python",
            last_updated="2026-04-30",
        )

    def _make_config(self, plan_mode_enabled: bool = False) -> AppConfig:
        return AppConfig(
            provider=Provider.OLLAMA,
            model="llama3.2:latest",
            api_key=None,
            base_url="http://localhost:11434",
            mode=ControlMode.HOOTLWO,
            plan_mode_enabled=plan_mode_enabled,
        )

    def _make_result(self, *, ok: bool, message: str = "") -> SimpleNamespace:
        return SimpleNamespace(
            verification_passed=ok,
            should_offer_repair=True,
            failure_type=None,
            message=message,
            recovery_decision=None,
            setup_outcome=None,
        )

    def test_orchestrator_calls_run_when_setup_succeeds(self) -> None:
        chat_steps: list[tuple[str, str, str]] = []

        class _Chat:
            def display_step(self_inner, step_id, label, *, status="running", duration_seconds=None, detail=None) -> None:
                chat_steps.append((step_id, label, status))

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            with (
                patch("duckln.main.bring_up_selected_repo", return_value=self._make_result(ok=True)) as bringup_mock,
                patch("duckln.main.run_prepared_repo", return_value=self._make_result(ok=True)) as run_mock,
                patch("duckln.main._run_runtime_repair_workflow") as repair_mock,
            ):
                _orchestrate_repo_to_running(
                    repo=self._make_repo(),
                    current=self._make_config(),
                    paths=paths,
                    approve=lambda _msg: True,
                    display=lambda _msg: None,
                    chat=_Chat(),
                    runtime_provider="ollama",
                    execution_target="vm",
                    vm_name="duckln-vm",
                    system_probe=None,
                    terminal_interface=None,
                )

        bringup_mock.assert_called_once()
        run_mock.assert_called_once()
        repair_mock.assert_not_called()
        # Deploy step transitions to done at the end.
        deploy_states = [(sid, status) for sid, _label, status in chat_steps if sid == "repo.deploy"]
        self.assertIn(("repo.deploy", "running"), deploy_states)
        self.assertIn(("repo.deploy", "done"), deploy_states)

    def test_orchestrator_runs_repair_then_retries_on_run_failure(self) -> None:
        chat_steps: list[tuple[str, str, str]] = []

        class _Chat:
            def display_step(self_inner, step_id, label, *, status="running", duration_seconds=None, detail=None) -> None:
                chat_steps.append((step_id, label, status))

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            run_results = [
                self._make_result(ok=False, message="boom"),
                self._make_result(ok=True),
            ]
            with (
                patch("duckln.main.bring_up_selected_repo", return_value=self._make_result(ok=True)),
                patch("duckln.main.run_prepared_repo", side_effect=run_results) as run_mock,
                patch("duckln.main._run_runtime_repair_workflow") as repair_mock,
            ):
                _orchestrate_repo_to_running(
                    repo=self._make_repo(),
                    current=self._make_config(),
                    paths=paths,
                    approve=lambda _msg: True,
                    display=lambda _msg: None,
                    chat=_Chat(),
                    runtime_provider="ollama",
                    execution_target="vm",
                    vm_name="duckln-vm",
                    system_probe=None,
                    terminal_interface=None,
                )

        self.assertEqual(2, run_mock.call_count)
        self.assertEqual(1, repair_mock.call_count)
        deploy_states = [(sid, status) for sid, _label, status in chat_steps if sid == "repo.deploy"]
        self.assertIn(("repo.deploy", "done"), deploy_states)

    def test_orchestrator_narrates_phases_in_terminal_pane(self) -> None:
        pane_commands: list[str] = []

        class _Chat:
            def display_step(self_inner, step_id, label, *, status="running", duration_seconds=None, detail=None) -> None:
                pass

            def run_terminal_command(self_inner, *, command, cwd=None) -> bool:
                pane_commands.append(command)
                return True

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            with (
                patch("duckln.main.bring_up_selected_repo", return_value=self._make_result(ok=True)),
                patch("duckln.main.run_prepared_repo", return_value=self._make_result(ok=True)),
            ):
                _orchestrate_repo_to_running(
                    repo=self._make_repo(),
                    current=self._make_config(),
                    paths=paths,
                    approve=lambda _msg: True,
                    display=lambda _msg: None,
                    chat=_Chat(),
                    runtime_provider="ollama",
                    execution_target="vm",
                    vm_name="duckln-vm",
                    system_probe=None,
                    terminal_interface=None,
                )

        joined = "\n".join(pane_commands)
        self.assertIn("# Duckln: deploying alpha on vm", joined)
        self.assertIn("# Duckln: cloning + installing alpha", joined)
        self.assertIn("# Duckln: running alpha", joined)
        # On VM success, the user is dropped into the project directory.
        self.assertTrue(any(cmd.startswith("cd ") for cmd in pane_commands))

    def test_orchestrator_handles_ctrl_c_during_setup_without_raising(self) -> None:
        chat_steps: list[tuple[str, str, str, str | None]] = []

        class _Chat:
            def display_step(self_inner, step_id, label, *, status="running", duration_seconds=None, detail=None) -> None:
                chat_steps.append((step_id, label, status, detail))

            def run_terminal_command(self_inner, *, command, cwd=None) -> bool:
                return True

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            with (
                patch("duckln.main.bring_up_selected_repo", side_effect=KeyboardInterrupt()),
                patch("duckln.main.run_prepared_repo") as run_mock,
                patch("duckln.main._run_runtime_repair_workflow") as repair_mock,
            ):
                # Plan 188: this exercises the DIRECT-execution path (reached with plan mode off,
                # as internal repair/retry callers do) — pin it so it doesn't draft a plan instead.
                result = _orchestrate_repo_to_running(
                    repo=self._make_repo(),
                    current=self._make_config(plan_mode_enabled=False),
                    paths=paths,
                    approve=lambda _msg: True,
                    display=lambda _msg: None,
                    chat=_Chat(),
                    runtime_provider="ollama",
                    execution_target="vm",
                    vm_name="duckln-vm",
                    system_probe=None,
                    terminal_interface=None,
                )

        # Run/repair never invoked when the setup phase is cancelled.
        run_mock.assert_not_called()
        repair_mock.assert_not_called()
        # Cancellation marker shows up in the step trail.
        cancelled = [step for step in chat_steps if step[2] == "failed" and step[3] and "cancelled" in step[3].lower()]
        self.assertTrue(cancelled)
        self.assertIsNone(result)

    def test_orchestrator_stops_after_max_repair_attempts_no_dead_end(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            with (
                patch("duckln.main.bring_up_selected_repo", return_value=self._make_result(ok=True)),
                patch("duckln.main.run_prepared_repo", return_value=self._make_result(ok=False, message="still broken")) as run_mock,
                patch("duckln.main._run_runtime_repair_workflow") as repair_mock,
            ):
                result = _orchestrate_repo_to_running(
                    repo=self._make_repo(),
                    current=self._make_config(),
                    paths=paths,
                    approve=lambda _msg: True,
                    display=lambda _msg: None,
                    chat=None,
                    runtime_provider="ollama",
                    execution_target="vm",
                    vm_name="duckln-vm",
                    system_probe=None,
                    terminal_interface=None,
                )

        # Initial run + 2 retries (capped by _ORCHESTRATE_MAX_REPAIR_ATTEMPTS=2).
        self.assertEqual(3, run_mock.call_count)
        # Repair runs between each retry (twice — never after the final attempt).
        self.assertEqual(2, repair_mock.call_count)
        # Returns the last failed result rather than looping forever.
        self.assertFalse(result.verification_passed)


class SlashCommandTest(unittest.TestCase):
    def test_browser_link_intent_opens_latest_terminal_url_before_repo_repair(self) -> None:
        terminal = FakeTerminalInterface()
        terminal.terminal_url = "https://accounts.google.com/o/oauth2/auth?response_type=code"
        displayed: list[str] = []

        handled = _handle_global_browser_link_intent(
            command="can you reopen the link in browser",
            display_output=displayed.append,
            terminal_interface=terminal,
        )

        self.assertTrue(handled)
        self.assertEqual(1, terminal.open_terminal_url_calls)
        self.assertTrue(any("Opened the latest terminal link" in line for line in displayed))

    def test_global_stop_command_interrupts_terminal_and_clears_active_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            write_workflow_state(
                paths.config_dir,
                {
                    "active_objective_kind": "runtime_repair",
                    "active_objective_repo_key": "https://example.com/openclaw",
                    "active_objective_repo_name": "openclaw",
                    "active_objective_status": "active",
                    "active_runtime_pid": 12345,
                    "active_runtime_stop_command": "docker compose down",
                },
            )
            terminal = FakeTerminalInterface()
            displayed: list[str] = []

            self.assertTrue(_looks_like_global_stop_command("stop now"))
            handled = _handle_global_stop_command(
                paths=paths,
                display_output=displayed.append,
                terminal_interface=terminal,
            )

            workflow = read_workflow_state(paths.config_dir)
            self.assertTrue(handled)
            self.assertEqual(1, terminal.interrupt_calls)
            self.assertTrue(any("Stopped the current Duckln action" in item for item in displayed))
            self.assertEqual("stopped", workflow["active_runtime_status"])
            self.assertIsNone(workflow.get("active_runtime_pid"))
            self.assertIsNone(workflow.get("active_runtime_stop_command"))

    def test_terminal_incident_watcher_ignores_declined_runtime_repair(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            terminal = FakeTerminalInterface()
            terminal.incidents.append(
                {
                    "category": "permission_denied",
                    "summary": "npm error EACCES: permission denied, mkdir '/usr/lib/node_modules/openclaw'",
                }
            )
            write_workflow_state(
                paths.config_dir,
                {
                    "active_repo_key": "https://example.com/openclaw",
                    "active_repo_name": "openclaw",
                    "active_runtime_repo_key": "https://example.com/openclaw",
                    "active_runtime_repo_name": "openclaw",
                    "active_repair_phase": "repair_declined",
                    "active_objective_status": "declined",
                    "active_objective_requires_user_decision": False,
                },
            )
            displayed: list[str] = []

            _drain_terminal_runtime_incidents(
                current=current,
                paths=paths,
                display_output=displayed.append,
                approve_prompt=lambda _prompt: True,
                terminal_interface=terminal,
            )

            self.assertEqual([], displayed)
            self.assertEqual(1, len(terminal.incidents))
            workflow = read_workflow_state(paths.config_dir)
            self.assertTrue(_runtime_workflow_blocks_incident_repair(SimpleNamespace(**workflow)))

    def test_app_prompt_incident_marks_waiting_on_app_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            terminal = FakeTerminalInterface()
            terminal.incidents.append(
                {
                    "category": "app_prompt_active",
                    "summary": "The app running in the terminal is waiting for user input: Continue? Yes/No.",
                    "fatal_line": "Continue? Yes/No",
                }
            )
            write_workflow_state(
                paths.config_dir,
                {
                    "active_repo_key": "https://example.com/openclaw",
                    "active_repo_name": "openclaw",
                    "active_runtime_repo_key": "https://example.com/openclaw",
                    "active_runtime_repo_name": "openclaw",
                    "active_runtime_execution_target": "vm",
                },
            )
            displayed: list[str] = []

            _drain_terminal_runtime_incidents(
                current=current,
                paths=paths,
                display_output=displayed.append,
                approve_prompt=lambda _prompt: True,
                terminal_interface=terminal,
            )

            workflow = read_workflow_state(paths.config_dir)
            self.assertEqual("waiting_on_app_prompt", workflow["active_repair_phase"])
            self.assertEqual("runtime_input_issue", workflow["active_issue_kind"])
            self.assertEqual("needs_user_decision", workflow["active_objective_status"])
            self.assertTrue(workflow["active_objective_requires_user_decision"])
            self.assertFalse(read_followup_state(paths.config_dir).get("pending_next_action"))
            self.assertEqual([], terminal.incidents)
            self.assertTrue(any("waiting for input in the terminal pane" in line for line in displayed))
            self.assertTrue(_runtime_workflow_blocks_incident_repair(SimpleNamespace(**workflow)))

    def test_discover_repo_run_command_offers_yes_no_or_user_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            repo = RepoCatalogRecord(
                "openclaw",
                "https://github.com/openclaw/openclaw",
                10,
                "Openclaw",
                "Agent",
                "Python",
                "2026-05-01",
            )
            displayed: list[str] = []

            class FakeLiveTerminal(FakeTerminalInterface):
                supports_live = True

                def select_choice(self, message: str, choices: tuple[str, ...], **_kwargs) -> str | None:
                    self.choice_message = message
                    self.choices = choices
                    return "Let me type the run command"

                def prompt_text(self, message: str, *, default: str = "", help_text: str | None = None, **_kwargs) -> str | None:
                    self.prompt_message = message
                    self.prompt_default = default
                    self.prompt_help = help_text
                    return "python -m openclaw serve"

            terminal = FakeLiveTerminal()
            with (
                patch("duckln.main.scan_readme_for_run_commands", return_value=("openclaw start",)),
                patch(
                    "duckln.main.search_for_run_command_hint",
                    return_value=RuntimeRepairEvidence(
                        note="Duckln searched public sources.",
                        source_urls=("https://github.com/openclaw/openclaw/blob/main/README.md",),
                        search_query="openclaw github README start command issues",
                    ),
                ),
            ):
                _discover_repo_run_command(
                    repo=repo,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=None,
                    terminal_interface=terminal,
                    execution_target="vm",
                )

            workflow = read_workflow_state(paths.config_dir)
            # Plan 186 F2c: plain-language options — "Run this: <cmd>" / type / skip / cancel.
            self.assertEqual("Run this: openclaw start", terminal.choices[0])
            self.assertEqual("Let me type the run command", terminal.choices[1])
            self.assertEqual("Skip running for now", terminal.choices[2])
            self.assertEqual("Cancel", terminal.choices[3])
            self.assertEqual("Type the command to start openclaw:", terminal.prompt_message)
            self.assertEqual("openclaw start", terminal.prompt_default)
            self.assertEqual("python -m openclaw serve", workflow["active_runtime_command"])

    def test_discover_repo_run_command_uses_choice_before_blank_instruction_box(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            repo = RepoCatalogRecord(
                "openclaw",
                "https://github.com/openclaw/openclaw",
                10,
                "Openclaw",
                "Agent",
                "Python",
                "2026-05-01",
            )

            class FakeLiveTerminal(FakeTerminalInterface):
                supports_live = True

                def select_choice(self, message: str, choices: tuple[str, ...], **_kwargs) -> str | None:
                    self.choice_message = message
                    self.choices = choices
                    return "Skip running for now"

                def prompt_text(self, *_args, **_kwargs) -> str | None:
                    raise AssertionError("Duckln should not open the text box after skip")

            terminal = FakeLiveTerminal()
            with (
                patch("duckln.main.scan_readme_for_run_commands", return_value=()),
                patch(
                    "duckln.main.search_for_run_command_hint",
                    return_value=RuntimeRepairEvidence(note=None, source_urls=(), search_query="openclaw exact error"),
                ),
            ):
                _discover_repo_run_command(
                    repo=repo,
                    paths=paths,
                    display_output=lambda _message: None,
                    approve_prompt=None,
                    terminal_interface=terminal,
                    execution_target="vm",
                )

            workflow = read_workflow_state(paths.config_dir)
            # Plan 186 F2c: a candidate command → "Run this: <cmd>" + type/skip/cancel.
            self.assertEqual("Run this: openclaw gateway status", terminal.choices[0])
            self.assertEqual("Let me type the run command", terminal.choices[1])
            self.assertEqual("Skip running for now", terminal.choices[2])
            self.assertEqual("needs_user_decision", workflow["active_objective_status"])

    def test_discover_repo_run_command_searches_real_terminal_error_before_readme(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            repo = RepoCatalogRecord(
                "openclaw",
                "https://github.com/openclaw/openclaw",
                10,
                "Openclaw",
                "Agent",
                "Python",
                "2026-05-01",
            )
            workflow = SimpleNamespace(
                active_incident_summary="Command 'multipass' not found, but can be installed with:\nsudo snap install multipass",
                active_issue_summary="Supervisor agent does not have a reliable run command for openclaw yet.",
                active_objective_last_blocker=None,
            )

            class FakeLiveTerminal(FakeTerminalInterface):
                supports_live = True

                def select_choice(self, _message: str, choices: tuple[str, ...], **_kwargs) -> str | None:
                    self.choices = choices
                    return "Skip running for now"

                def prompt_text(self, *_args, **_kwargs) -> str | None:
                    raise AssertionError("Skip should not open text input")

            terminal = FakeLiveTerminal()
            captured_error_texts: list[str] = []
            with (
                patch("duckln.main.scan_readme_for_run_commands", return_value=()),
                patch("duckln.main.gather_runtime_repair_evidence") as evidence_mock,
                patch(
                    "duckln.main.search_for_run_command_hint",
                    return_value=RuntimeRepairEvidence(note=None, source_urls=(), search_query="openclaw github README start"),
                ),
            ):
                evidence_mock.side_effect = lambda **kwargs: captured_error_texts.append(kwargs["error_text"]) or RuntimeRepairEvidence(
                    note="Duckln searched the exact terminal error.",
                    source_urls=("https://stackoverflow.com/questions/1/multipass-not-found",),
                    search_query='"Command \'multipass\' not found"',
                )
                _discover_repo_run_command(
                    repo=repo,
                    paths=paths,
                    display_output=lambda _message: None,
                    approve_prompt=None,
                    terminal_interface=terminal,
                    execution_target="vm",
                    failure_summary="Supervisor agent does not have a reliable run command for openclaw yet.",
                    workflow=workflow,
                )

            self.assertTrue(captured_error_texts)
            self.assertIn("Command 'multipass' not found", captured_error_texts[0])
            # Plan 186 F2c: first option is a plain action (run-this or type-the-command), no jargon.
            self.assertTrue(
                terminal.choices[0].startswith("Run this:")
                or terminal.choices[0] == "Let me type the run command"
            )

    def test_conversation_choice_labels_do_not_append_recommended_suffix(self) -> None:
        prompt = ConversationChoicePrompt(
            message="Choose a path",
            options=(
                ConversationChoiceOption(label="Set it up", action_key="set_up", recommended=True),
                ConversationChoiceOption(label="Cancel", action_key="cancel"),
            ),
        )

        self.assertEqual(("Set it up", "Cancel"), conversation_choice_labels(prompt))
        self.assertEqual("set_up", resolve_conversation_choice(prompt, selected_label="Set it up").action_key)
        self.assertEqual("set_up", resolve_conversation_choice(prompt, selected_label="Set it up (Recommended)").action_key)

    def test_sync_chat_activity_from_trace_uses_only_trace_title(self) -> None:
        chat = FakeActivityChat()

        _sync_chat_activity_from_output(
            chat=chat,
            message="Duckln trace: shell command execution.\n1. Tool: Controlled shell runner\n2. Action: Run a bounded shell command",
        )

        self.assertEqual(1, len(chat.activity_calls))
        message, spinner = chat.activity_calls[0]
        self.assertIn("shell command execution", message)
        self.assertTrue(spinner)
        # Activity strings should lead with an action verb, not the generic "Working...".
        self.assertFalse(message.startswith("Working..."))

    def test_render_chat_objective_status_compacts_repo_step_attempt_and_approval(self) -> None:
        workflow = SimpleNamespace(
            active_objective_kind="runtime_repair",
            active_objective_repo_name="openclaw",
            active_objective_attempt_count=2,
            active_objective_max_attempts=5,
            active_objective_status="needs_user_decision",
            active_objective_execution_target="docker",
            active_objective_requires_user_decision=True,
            active_repair_phase="awaiting_runtime_docker_env_approval",
            active_incident_category="docker_env_missing",
        )

        rendered = _render_chat_objective_status(workflow)

        self.assertEqual(
            "openclaw • runtime repair • waiting to repair docker env • waiting on you • attempt 2/5 • docker",
            rendered,
        )

    def test_sync_chat_objective_status_pushes_compact_state_into_chat(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            write_workflow_state(
                paths.config_dir,
                {
                    "active_objective_id": "runtime_repair:https://example.com/openclaw",
                    "active_objective_kind": "runtime_repair",
                    "active_objective_repo_name": "openclaw",
                    "active_objective_attempt_count": 1,
                    "active_objective_max_attempts": 5,
                    "active_objective_status": "active",
                    "active_objective_execution_target": "vm",
                    "active_objective_requires_user_decision": False,
                    "active_repair_phase": "runtime_prerequisite_repair_running",
                },
            )
            chat = FakeActivityChat()

            _sync_chat_objective_status(chat=chat, config_dir=paths.config_dir)

            self.assertEqual(
                ["openclaw • runtime repair • installing prerequisite • can continue • attempt 1/5 • ubuntu vm • auto"],
                chat.objective_calls,
            )
            self.assertEqual(1, len(chat.objective_activity_calls))
            activity_message, activity_spinner = chat.objective_activity_calls[0]
            self.assertIn("installing prerequisite", activity_message)
            self.assertIn("openclaw", activity_message)
            self.assertTrue(activity_spinner)
            self.assertFalse(activity_message.startswith("Working..."))
            self.assertEqual(["runtime_repair:https://example.com/openclaw"], chat.objective_keys)
            self.assertEqual(0, chat.objective_cleared)

    def test_terminal_incident_sets_awaiting_approval_activity_after_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            display: list[str] = []
            chat = FakeTerminalInterface()
            chat.incidents.append(
                {
                    "category": "missing_command",
                    "summary": "Duckln classified this blocker as missing command. Likely command/tool: docker.",
                    "tool_hint": "docker",
                }
            )
            write_workflow_state(
                paths.config_dir,
                {
                    "active_repo_key": "https://example.com/openclaw",
                    "active_repo_name": "openclaw",
                    "active_runtime_repo_key": "https://example.com/openclaw",
                    "active_runtime_repo_name": "openclaw",
                    "active_runtime_execution_target": "local",
                    "active_runtime_command": "docker compose up",
                },
            )

            with patch(
                "duckln.main.gather_runtime_repair_evidence",
                return_value=RuntimeRepairEvidence(
                    note="Duckln checked current official docs for this blocker.",
                    source_urls=("https://docs.docker.com/",),
                    search_query="docker command not found",
                ),
            ):
                _drain_terminal_runtime_incidents(
                    current=SimpleNamespace(),
                    paths=paths,
                    display_output=display.append,
                    approve_prompt=lambda _prompt: False,
                    terminal_interface=chat,
                )

            self.assertIn(("Awaiting approval... openclaw repair is paused", False), chat.activity_calls)
            self.assertTrue(any("paused until you approve" in line for line in display))

    def test_utility_fallback_prefers_current_workflow_repo_over_stale_recommendation(self) -> None:
        context = SimpleNamespace(
            mentioned_repo=None,
            active_repo=None,
            workflow_state=SimpleNamespace(repo_name="openclaw"),
            durable_repo_memory=SimpleNamespace(repo_name="whisper"),
            durable_recommendation=SimpleNamespace(primary_repo="whisper", secondary_repo="private-gpt"),
            pending_offer=SimpleNamespace(kind=None),
        )

        reply = build_utility_fallback_reply(
            context=context,
            recent_replies=(),
            reply_factory=lambda text, **kwargs: SimpleNamespace(text=text, **kwargs),
            pick_reply=lambda options, normalized_compact, recent_replies: options[0],
            utility_fallback_reply_texts=lambda **kwargs: (kwargs["repo_name"], f'{kwargs["shortlist_primary"]}:{kwargs["shortlist_secondary"]}'),
        )

        self.assertEqual("openclaw", reply.text)

    def test_slash_command_descriptors_include_cloud(self) -> None:
        commands = {descriptor.command for descriptor in get_slash_command_descriptors()}

        self.assertIn("/cloud", commands)

    def test_chat_select_prompt_uses_arrow_key_selector_without_inline_typing(self) -> None:
        class FakeChat:
            def __init__(self) -> None:
                self.prepared = 0
                self.restored = 0

            def prepare_selection_overlay(self) -> None:
                self.prepared += 1

            def restore_after_selection(self) -> None:
                self.restored += 1

        chat = FakeChat()
        prompt = _build_chat_select_prompt(chat=chat, chat_input=lambda _message: "", display=lambda _message: None)

        with patch("duckln.main.duckln_select", return_value="OpenAI") as mocked_select:
            selected = prompt("Select your AI provider:", ("OpenRouter", "OpenAI", "Anthropic"))

        self.assertEqual("OpenAI", selected)
        mocked_select.assert_called_once_with("Select your AI provider:", ("OpenRouter", "OpenAI", "Anthropic"))
        self.assertEqual(1, chat.prepared)
        self.assertEqual(1, chat.restored)

    def test_chat_select_prompt_uses_searchable_selector_for_repo_choices(self) -> None:
        class FakeChat:
            def prepare_selection_overlay(self) -> None:
                return None

            def restore_after_selection(self) -> None:
                return None

        chat = FakeChat()
        prompt = _build_chat_select_prompt(chat=chat, chat_input=lambda _message: "", display=lambda _message: None)

        with patch("duckln.main.duckln_search_select", return_value="repo-a") as mocked_search:
            selected = prompt("Select a repository:", tuple(f"repo-{index}" for index in range(20)))

        self.assertEqual("repo-a", selected)
        mocked_search.assert_called_once_with("Select a repository:", tuple(f"repo-{index}" for index in range(20)))

    def test_chat_text_prompt_uses_default_on_empty_response(self) -> None:
        displayed: list[str] = []

        class FakeChat:
            def prompt(self, _message: str, *, input_func, record_input: bool = True) -> str:
                return input_func("")

        prompt = _build_chat_text_prompt(
            chat=FakeChat(),
            chat_input=lambda _message: "",
            display=displayed.append,
        )

        value = prompt("Enter Ollama base URL:", "http://localhost:11434")

        self.assertEqual("http://localhost:11434", value)
        self.assertIn("Press Enter to keep: http://localhost:11434", displayed[0])

    def test_live_chat_text_prompt_uses_structured_overlay_without_chat_fallback(self) -> None:
        displayed: list[str] = []

        class FakeLiveChat:
            supports_live = True

            def prompt_text(self, message: str, *, default: str = "", help_text: str | None = None) -> str | None:
                self.message = message
                self.default = default
                self.help_text = help_text
                return " us-east-1 "

        chat = FakeLiveChat()
        prompt = _build_chat_text_prompt(
            chat=chat,
            chat_input=lambda _message: "",
            display=displayed.append,
        )

        value = prompt("AWS region to inspect:", "us-east-1")

        self.assertEqual("us-east-1", value)
        self.assertEqual("AWS region to inspect:", chat.message)
        self.assertEqual("us-east-1", chat.default)
        self.assertIsNone(chat.help_text)
        self.assertEqual([], displayed)

    def test_live_chat_text_prompt_passes_objective_key_when_available(self) -> None:
        displayed: list[str] = []

        class FakeLiveChat:
            supports_live = True

            def prompt_text(
                self,
                message: str,
                *,
                default: str = "",
                help_text: str | None = None,
                objective_key: str | None = None,
            ) -> str | None:
                self.message = message
                self.default = default
                self.help_text = help_text
                self.objective_key = objective_key
                return " us-east-1 "

        chat = FakeLiveChat()
        prompt = _build_chat_text_prompt(
            chat=chat,
            chat_input=lambda _message: "",
            display=displayed.append,
            objective_key_resolver=lambda: "runtime_repair:openclaw",
        )

        value = prompt("AWS region to inspect:", "us-east-1")

        self.assertEqual("us-east-1", value)
        self.assertEqual("runtime_repair:openclaw", chat.objective_key)
        self.assertEqual([], displayed)

    def test_chat_secret_prompt_does_not_record_input(self) -> None:
        displayed: list[str] = []
        observed_record_flags: list[bool] = []

        class FakeChat:
            def prompt(self, _message: str, *, input_func, record_input: bool = True) -> str:
                observed_record_flags.append(record_input)
                return input_func("")

        prompt = _build_chat_secret_prompt(
            chat=FakeChat(),
            chat_input=lambda _message: "secret-key",
            display=displayed.append,
        )

        value = prompt("Enter your API key:")

        self.assertEqual("secret-key", value)
        self.assertEqual([False], observed_record_flags)
        self.assertEqual(["Enter your API key:"], displayed)

    def test_chat_approve_prompt_uses_arrow_key_confirmation_overlay(self) -> None:
        class FakeChat:
            def __init__(self) -> None:
                self.prepared = 0
                self.restored = 0

            def prepare_selection_overlay(self) -> None:
                self.prepared += 1

            def restore_after_selection(self) -> None:
                self.restored += 1

        chat = FakeChat()
        prompt = _build_chat_approve_prompt(chat=chat, chat_input=lambda _message: "", display=lambda _message: None)

        with patch("duckln.main.duckln_confirm", return_value=True) as mocked_confirm:
            approved = prompt("Clone repository: git clone --depth 1 ...")

        self.assertTrue(approved)
        mocked_confirm.assert_called_once_with("Clone repository: git clone --depth 1 ...", default=True)
        self.assertEqual(1, chat.prepared)
        self.assertEqual(1, chat.restored)

    def test_live_chat_select_prompt_uses_inline_numbered_flow(self) -> None:
        class FakeLiveChat:
            supports_live = True

            def select_choice(self, message: str, choices: tuple[str, ...]) -> str | None:
                self.message = message
                self.choices = choices
                return choices[1]

        prompt = _build_chat_select_prompt(
            chat=FakeLiveChat(),
            chat_input=lambda _message: "2",
            display=lambda _message: None,
        )

        selected = prompt("Select your AI provider:", ("OpenRouter", "OpenAI", "Anthropic"))

        self.assertEqual("OpenAI", selected)

    def test_live_chat_select_prompt_passes_objective_key_when_available(self) -> None:
        class FakeLiveChat:
            supports_live = True

            def select_choice(self, message: str, choices: tuple[str, ...], *, objective_key: str | None = None) -> str | None:
                self.message = message
                self.choices = choices
                self.objective_key = objective_key
                return choices[0]

        chat = FakeLiveChat()
        prompt = _build_chat_select_prompt(
            chat=chat,
            chat_input=lambda _message: "2",
            display=lambda _message: None,
            objective_key_resolver=lambda: "runtime_repair:openclaw",
        )

        selected = prompt("Select your AI provider:", ("OpenRouter", "OpenAI", "Anthropic"))

        self.assertEqual("OpenRouter", selected)
        self.assertEqual("runtime_repair:openclaw", chat.objective_key)

    def test_live_chat_approve_prompt_uses_inline_confirmation(self) -> None:
        class FakeLiveChat:
            supports_live = True

            def confirm_choice(self, message: str, *, default: bool = True) -> bool:
                self.message = message
                self.default = default
                return True

        prompt = _build_chat_approve_prompt(
            chat=FakeLiveChat(),
            chat_input=lambda _message: "1",
            display=lambda _message: None,
        )

        self.assertTrue(prompt("Do you want Duckln to continue?"))

    def test_live_chat_approve_prompt_passes_objective_key_when_available(self) -> None:
        class FakeLiveChat:
            supports_live = True

            def confirm_choice(self, message: str, *, default: bool = True, objective_key: str | None = None) -> bool:
                self.message = message
                self.default = default
                self.objective_key = objective_key
                return True

        chat = FakeLiveChat()
        prompt = _build_chat_approve_prompt(
            chat=chat,
            chat_input=lambda _message: "1",
            display=lambda _message: None,
            objective_key_resolver=lambda: "runtime_repair:openclaw",
        )

        self.assertTrue(prompt("Do you want Duckln to continue?"))
        self.assertEqual("runtime_repair:openclaw", chat.objective_key)

    def test_live_chat_dependency_approval_uses_structured_panel_when_available(self) -> None:
        class FakeLiveChat:
            supports_live = True

            def approve_dependency_plan(self, request: DependencyApprovalRequest) -> bool:
                self.request = request
                return DependencyApprovalDecision(
                    approved=True,
                    approve_all=False,
                    selected_item_ids=("req:requests",),
                )

            def confirm_choice(self, message: str, *, default: bool = True) -> bool:
                raise AssertionError("generic confirm should not be used for dependency approvals")

        prompt = _build_chat_approve_prompt(
            chat=FakeLiveChat(),
            chat_input=lambda _message: "1",
            display=lambda _message: None,
        )
        request = DependencyApprovalRequest(
            prompt="Install requirements",
            command=".venv/bin/python -m pip install -r requirements.txt",
            project_dir="/tmp/example",
            manifest_paths=("/tmp/example/requirements.txt",),
            source_urls=("https://packaging.python.org/en/latest/tutorials/installing-packages/",),
            items=(
                DependencyApprovalItem(
                    item_id="req:requests",
                    dependency="requests",
                    version="==2.32.0",
                    reason="Required by requirements.txt.",
                    source_url="https://packaging.python.org/en/latest/tutorials/installing-packages/",
                    installer="pip",
                    risk_note="Review pinned version before approving.",
                    manifest_path="/tmp/example/requirements.txt",
                ),
            ),
        )

        decision = prompt.approve_dependency_install(request)

        self.assertTrue(decision.approved)
        self.assertFalse(decision.approve_all)
        self.assertEqual(("req:requests",), decision.selected_item_ids)

    def test_live_chat_dependency_approval_passes_objective_key_when_available(self) -> None:
        class FakeLiveChat:
            supports_live = True

            def approve_dependency_plan(
                self,
                request: DependencyApprovalRequest,
                *,
                objective_key: str | None = None,
            ) -> DependencyApprovalDecision:
                self.request = request
                self.objective_key = objective_key
                return DependencyApprovalDecision(
                    approved=True,
                    approve_all=False,
                    selected_item_ids=("req:requests",),
                )

            def confirm_choice(self, message: str, *, default: bool = True) -> bool:
                raise AssertionError("generic confirm should not be used for dependency approvals")

        chat = FakeLiveChat()
        prompt = _build_chat_approve_prompt(
            chat=chat,
            chat_input=lambda _message: "1",
            display=lambda _message: None,
            objective_key_resolver=lambda: "runtime_repair:openclaw",
        )
        request = DependencyApprovalRequest(
            prompt="Install requirements",
            command=".venv/bin/python -m pip install -r requirements.txt",
            project_dir="/tmp/example",
            manifest_paths=("/tmp/example/requirements.txt",),
            source_urls=("https://packaging.python.org/en/latest/tutorials/installing-packages/",),
            items=(
                DependencyApprovalItem(
                    item_id="req:requests",
                    dependency="requests",
                    version="==2.32.0",
                    reason="Required by requirements.txt.",
                    source_url="https://packaging.python.org/en/latest/tutorials/installing-packages/",
                    installer="pip",
                    risk_note="Review pinned version before approving.",
                    manifest_path="/tmp/example/requirements.txt",
                ),
            ),
        )

        decision = prompt.approve_dependency_install(request)

        self.assertTrue(decision.approved)
        self.assertEqual("runtime_repair:openclaw", chat.objective_key)

    def test_command_palette_lists_available_commands(self) -> None:
        commands = get_slash_command_descriptors()

        self.assertEqual(
            ("/help", "/mode", "/provider", "/model", "/models", "/status", "/config", "/repos", "/repos tracked", "/repos active", "/repos status", "/repos history", "/repos live", "/repos path", "/repos link", "/repos remove", "/repos refresh", "/memory clear", "/remember", "/failures", "/failures show", "/failures clear", "/failures window", "/agents", "/agents trace", "/agents costs", "/plan", "/plan precheck", "/plan show", "/plan approve", "/plan continue", "/plan reject", "/plan edit", "/plan reload", "/plan retry", "/plan history", "/vm", "/cloud", "/create aws vm", "/create gcp vm", "/loop", "/loops", "/loop pause", "/loop resume", "/loop delete", "/loop history", "/loop edit", "/loop run", "/healthcheck", "/internet", "/reasoning", "/ui", "/skills", "/skills show", "/skills clear", "/skill add", "/learn", "/tools", "/tools add", "/mcp", "/ask", "/do", "/policy", "/prefs", "/resources"),
            tuple(item.command for item in commands),
        )
        self.assertTrue(all("—" in item.choice_label for item in commands))

    def test_repos_slash_command_can_filter_by_vm_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            store = initialize_state_store(paths.config_dir)
            store.upsert_repo_state(
                repo_key="https://example.com/openclaw",
                repo_url="https://example.com/openclaw",
                repo_path="/home/ubuntu/openclaw",
                execution_target="vm",
                vm_name="duckln-vm-200",
                status="ready",
                summary="openclaw ready",
                metadata={"repo_name": "openclaw"},
            )
            store.upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path="/home/ubuntu/whisper",
                execution_target="vm",
                vm_name="duckln-vm-fi",
                status="ready",
                summary="whisper ready",
                metadata={"repo_name": "whisper"},
            )
            displayed: list[str] = []

            handle_session_command(
                "/repos duckln-vm-200",
                AppConfig(provider=Provider.OPENAI, model="gpt-4o-mini", api_key="key", mode=ControlMode.HITL),
                paths,
                display=displayed.append,
            )

            output = "\n".join(displayed)
            self.assertIn("openclaw", output.lower())
            self.assertIn("duckln-vm-200", output)
            self.assertNotIn("whisper", output.lower())

    def test_free_text_vm_list_includes_tracked_dates_and_repo_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            store = initialize_state_store(config_dir)
            store.upsert_vm_linkage(
                vm_name="duckln-vm-200",
                provider="multipass",
                mode="ubuntu",
                status="running",
                summary="ready",
            )
            store.upsert_repo_state(
                repo_key="https://example.com/openclaw",
                repo_url="https://example.com/openclaw",
                repo_path="/home/ubuntu/openclaw",
                execution_target="vm",
                vm_name="duckln-vm-200",
                status="ready",
                summary="openclaw ready",
                metadata={"repo_name": "openclaw"},
            )

            with patch("duckln.vm.is_multipass_installed", return_value=True), patch(
                "duckln.vm.list_multipass_vm_names",
                return_value=("duckln-vm-200",),
            ):
                reply = _respond_to_free_text("how many vms are there on my local system", config_dir=config_dir)

            self.assertEqual("vm_list", reply.intent)
            self.assertIn("duckln-vm-200", reply.text)
            self.assertIn("created", reply.text.lower())
            self.assertIn("1 repo", reply.text.lower())

    def test_command_palette_echoes_selected_slash_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(provider=Provider.OPENAI, model="gpt-4o-mini", api_key="key", mode=ControlMode.HITL)
            displayed: list[str] = []

            handle_session_command(
                "/",
                current,
                paths,
                select=lambda prompt, choices: next(choice for choice in choices if choice.startswith("/help ")),
                display=displayed.append,
            )

            self.assertIn("> /help", displayed)

    def test_health_alias_normalizes_to_healthcheck(self) -> None:
        self.assertEqual("/healthcheck", _normalize_runtime_slash_command("/health"))
        self.assertEqual("/cloud create aws vm", _normalize_runtime_slash_command("/Create AWS VM"))
        self.assertEqual("/cloud create gcp vm", _normalize_runtime_slash_command("/Create GCP VM"))

    def test_cloud_command_can_open_managed_cloud_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            initialize_state_store(paths.config_dir).upsert_managed_resource(
                resource_key="gcp:duckln-gcp:us-central1-a",
                resource_kind="cloud_vm",
                provider="gcp",
                display_name="duckln-gcp",
                execution_target="gcp",
                region="us-central1-a",
                shape="e2-standard-4",
                status="running",
                metadata={
                    "connect_command": "gcloud compute ssh duckln-gcp --zone us-central1-a",
                    "stop_command": "gcloud compute instances stop duckln-gcp --zone us-central1-a --quiet",
                },
            )
            outputs: list[str] = []
            terminal = FakeTerminalInterface()

            def select_prompt(message: str, choices: tuple[str, ...]) -> str | None:
                if "cloud action" in message.lower():
                    return "Open managed cloud terminal"
                if "managed cloud resource" in message.lower():
                    return "duckln-gcp [gcp] — us-central1-a — running"
                return None

            updated = handle_session_command(
                "/cloud",
                current,
                paths,
                select=select_prompt,
                display=outputs.append,
                terminal_interface=terminal,
            )

            self.assertEqual(current, updated)
            self.assertEqual([("gcloud compute ssh duckln-gcp --zone us-central1-a", None)], terminal.run_commands)
            workflow = read_workflow_state(paths.config_dir)
            self.assertEqual("gcp:duckln-gcp:us-central1-a", workflow.get("active_runtime_cloud_resource_key"))
            self.assertEqual("gcp", workflow.get("active_runtime_execution_target"))

    def test_cloud_auth_missing_cli_offers_approved_auto_install(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            missing = SimpleNamespace(
                provider="gcp",
                cli_name="gcloud",
                cli_available=False,
                authenticated=False,
                account_label=None,
                project_label=None,
                default_region_or_zone="us-central1-a",
                message="gcloud CLI is not installed or not on PATH.",
                source_url="https://cloud.google.com/sdk/docs/install",
                install_hint="Install Google Cloud SDK.",
                auth_hint="Authenticate with gcloud auth login.",
            )
            ready = SimpleNamespace(
                provider="gcp",
                cli_name="gcloud",
                cli_available=True,
                authenticated=False,
                account_label=None,
                project_label=None,
                default_region_or_zone="us-central1-a",
                message="gcloud CLI is installed, but auth is not ready.",
                source_url="https://docs.cloud.google.com/sdk/gcloud/reference/auth/list",
                install_hint=None,
                auth_hint="Authenticate with gcloud auth login.",
            )
            plan = SimpleNamespace(
                provider="gcp",
                cli_name="gcloud",
                command="brew install --cask google-cloud-sdk && gcloud version",
                source_url="https://cloud.google.com/sdk/docs/install",
                post_install_auth_hint="After install, authenticate with gcloud auth login.",
                os_type="Darwin",
                requires_elevation=False,
            )

            class FakeRunner:
                commands: list[str] = []

                def __init__(self, *args, **kwargs) -> None:
                    pass

                def run(self, command: str, *, timeout_seconds: float = 30.0, cwd: str | None = None, env=None):
                    self.commands.append(command)
                    return SimpleNamespace(exit_code=0, timed_out=False, stdout="ok", stderr="")

            displayed: list[str] = []
            approvals: list[str] = []
            with patch("duckln.main.inspect_cloud_auth", side_effect=(missing, ready)), patch(
                "duckln.main.build_cloud_cli_install_plan", return_value=plan
            ), patch("duckln.main.ControlledCommandRunner", FakeRunner):
                handle_session_command(
                    "/cloud",
                    current,
                    paths,
                    select=lambda _message, _choices: "Check GCP auth",
                    approve=lambda message: approvals.append(message) or True,
                    display=displayed.append,
                )

            self.assertTrue(any("Install now?" in approval for approval in approvals))
            self.assertTrue(any("brew install --cask google-cloud-sdk" in command for command in FakeRunner.commands))
            self.assertTrue(any("gcloud install completed" in message for message in displayed))

    def test_cloud_auth_ready_keeps_terminal_local_without_live_resource(self) -> None:
        """Authentication alone must not flip the header — only an actual live cloud resource does."""

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            ready = SimpleNamespace(
                provider="gcp",
                cli_name="gcloud",
                cli_available=True,
                authenticated=True,
                account_label="user@example.com",
                project_label="duckln-demo",
                default_region_or_zone="us-central1-a",
                message="GCP is ready.",
                source_url="https://docs.cloud.google.com/sdk/gcloud/reference/auth/list",
                ready=True,
                readiness_label="GCP ready",
            )
            terminal = FakeTerminalInterface()

            with patch("duckln.main.inspect_cloud_auth", return_value=ready):
                handle_session_command(
                    "/cloud",
                    current,
                    paths,
                    select=lambda message, _choices: "Check GCP auth" if "cloud action" in message.lower() else None,
                    display=lambda _message: None,
                    terminal_interface=terminal,
                )

            self.assertTrue(terminal.connection_updates)
            self.assertEqual("local", terminal.connection_updates[-1]["connection_type"])

    def test_cloud_auth_ready_switches_terminal_target_to_provider_when_resource_live(self) -> None:
        """With a live managed resource, the header should reflect the cloud provider."""

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            from state.store import initialize_state_store

            store = initialize_state_store(paths.config_dir)
            store.upsert_managed_resource(
                resource_key="gcp:duckln-demo:vm-1",
                resource_kind="vm",
                provider="gcp",
                display_name="vm-1",
                status="running",
                execution_target="gcp",
                region="us-central1-a",
            )
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            ready = SimpleNamespace(
                provider="gcp",
                cli_name="gcloud",
                cli_available=True,
                authenticated=True,
                account_label="user@example.com",
                project_label="duckln-demo",
                default_region_or_zone="us-central1-a",
                message="GCP is ready.",
                source_url="https://docs.cloud.google.com/sdk/gcloud/reference/auth/list",
                ready=True,
                readiness_label="GCP ready",
            )
            terminal = FakeTerminalInterface()

            with patch("duckln.main.inspect_cloud_auth", return_value=ready):
                handle_session_command(
                    "/cloud",
                    current,
                    paths,
                    select=lambda message, _choices: "Check GCP auth" if "cloud action" in message.lower() else None,
                    display=lambda _message: None,
                    terminal_interface=terminal,
                )

            self.assertTrue(terminal.connection_updates)
            self.assertEqual("gcp", terminal.connection_updates[-1]["connection_type"])
            self.assertEqual("GCP", terminal.connection_updates[-1]["cloud_vendor"])
            self.assertEqual("us-central1-a", terminal.connection_updates[-1]["cloud_region"])

    def test_gcp_project_selection_can_create_new_project(self) -> None:
        class FakeRunner:
            pass

        displayed: list[str] = []
        prompts: list[tuple[str, tuple[str, ...]]] = []

        def select(message: str, choices: tuple[str, ...]) -> str:
            prompts.append((message, choices))
            return "Create new GCP project"

        def text(message: str, default: str) -> str:
            if "project id" in message.lower():
                return "duckln-demo"
            return "Duckln Demo"

        result = SimpleNamespace(
            ok=True,
            project_id="duckln-demo",
            message="GCP project duckln-demo created and selected.",
            source_url="https://cloud.google.com/sdk/gcloud/reference/projects/create",
        )
        with patch("duckln.main.discover_gcp_projects", return_value=("existing-project",)), patch(
            "duckln.main.create_gcp_project", return_value=result
        ) as create_mock:
            selected = _select_or_create_gcp_project(
                runner=FakeRunner(),
                text_prompt=text,
                select_prompt=select,
                display_output=displayed.append,
            )

        self.assertEqual("duckln-demo", selected)
        self.assertIn("Create new GCP project", prompts[0][1])
        create_mock.assert_called_once()
        self.assertTrue(any("created and selected" in message for message in displayed))

    def test_loop_slash_command_creates_plain_english_loop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )
            # 4th answer (Plan 161 PR1): expiry review window — blank accepts the default.
            answers = iter(("restart whisper if it crashes", "every five minutes", "fix first then notify", ""))
            displayed: list[str] = []

            updated = handle_session_command(
                "/loop",
                current,
                paths,
                text_prompt=lambda message, _default: (self.assertIn("Example:", message) if "What should" in message else None) or next(answers),
                select=lambda _message, _choices: "Create loop",
                display=displayed.append,
            )

            self.assertEqual(current, updated)
            self.assertTrue(any("Loop created:" in message for message in displayed))
            loops = initialize_state_store(paths.config_dir).list_loops()
            self.assertEqual(1, len(loops))
            self.assertEqual("health_monitor", loops[0].type)
            # Default per-type expiry was applied (health_monitor → 30 days).
            self.assertEqual(30, loops[0].expiry_days)
            self.assertIsNotNone(loops[0].expires_at)
            from duckln.loop_runtime import shutdown_loop_scheduler

            shutdown_loop_scheduler()

    def test_loop_management_pause_and_history_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(provider=Provider.OPENAI, model="gpt-4o-mini", api_key="key", mode=ControlMode.HITL)
            store = initialize_state_store(paths.config_dir)
            store.upsert_loop(
                loop_id="loop_001",
                name="Whisper health monitor",
                loop_type="health_monitor",
                schedule='{"kind":"interval","minutes":5}',
                task_description="restart whisper if it crashes",
                tool_scope=("check_process", "restart_service", "send_notification"),
                os_type="Darwin",
            )
            store.record_loop_result(loop_id="loop_001", status="ok", summary="Whisper is healthy.")
            displayed: list[str] = []

            handle_session_command("/loop pause loop_001", current, paths, display=displayed.append)
            handle_session_command("/loop history loop_001", current, paths, display=displayed.append)

            output = "\n".join(displayed)
            self.assertIn("Paused loop_001", output)
            self.assertIn("Whisper is healthy", output)

    def test_help_command_prints_supported_commands_from_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )
            displayed: list[str] = []

            updated = handle_session_command(
                "/help",
                current,
                paths,
                display=displayed.append,
            )

            self.assertEqual(current, updated)
            self.assertEqual(1, len(displayed))
            self.assertIn("Available slash commands:", displayed[0])
            for descriptor in get_slash_command_descriptors():
                self.assertIn(f"- {descriptor.choice_label}", displayed[0])

    def test_mode_command_updates_saved_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )
            displayed: list[str] = []

            updated = handle_session_command(
                "/mode",
                current,
                paths,
                select=lambda prompt, choices: choices[1],
                display=displayed.append,
            )

            self.assertEqual(ControlMode.HOTL, updated.mode)
            # Plan 188: load_app_config always returns Plan Mode on — normalize for the compare.
            self.assertEqual(replace(updated, plan_mode_enabled=True), load_app_config(paths))
            self.assertTrue(any("Updated mode" in message for message in displayed))

    def test_provider_command_preserves_previous_config_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENROUTER,
                model="openrouter/auto",
                api_key="working-key",
                mode=ControlMode.HOTL,
            )
            displayed: list[str] = []
            selections = iter(("OpenAI", "Use this key", "Cancel onboarding"))
            client = FakeHttpClient(
                {
                    "https://api.openai.com/v1/models": {
                        "Bearer bad-key": FakeResponse(status_code=401, payload={}, text="Unauthorized"),
                    }
                }
            )

            updated = handle_session_command(
                "/provider",
                current,
                paths,
                select=lambda prompt, choices: next(selections),
                secret_prompt=lambda prompt: "bad-key",
                display=displayed.append,
                client=client,
            )

            self.assertEqual(current, updated)
            self.assertIsNone(load_app_config(paths))
            self.assertTrue(any("Retryable error:" in message for message in displayed))

    def test_provider_command_cancel_returns_current_config_without_saving(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENROUTER,
                model="openrouter/auto",
                api_key="working-key",
                mode=ControlMode.HOTL,
            )
            displayed: list[str] = []

            updated = handle_session_command(
                "/provider",
                current,
                paths,
                select=lambda prompt, choices: "Cancel",
                secret_prompt=lambda prompt: self.fail("Cancel should not ask for a new key"),
                display=displayed.append,
            )

            self.assertEqual(current, updated)
            self.assertIsNone(load_app_config(paths))
            self.assertEqual(["Provider update cancelled."], displayed)

    def test_model_command_updates_model_after_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENROUTER,
                model="openrouter/auto",
                api_key="router-key",
                mode=ControlMode.HITL,
            )
            client = FakeHttpClient(
                {
                    "https://openrouter.ai/api/v1/models": FakeResponse(
                        status_code=200,
                        payload={"data": [{"id": "z-model", "name": "Zulu"}, {"id": "a-model", "name": "Alpha"}]},
                    )
                }
            )

            displayed: list[str] = []

            updated = handle_session_command(
                "/model",
                current,
                paths,
                select=lambda prompt, choices: choices[1],
                display=displayed.append,
                client=client,
            )

            self.assertEqual("a-model", updated.model)
            # Plan 188: load_app_config always returns Plan Mode on — normalize for the compare.
            self.assertEqual(replace(updated, plan_mode_enabled=True), load_app_config(paths))
            self.assertIn("OpenRouter connection verified.", displayed)
            self.assertIn("OpenRouter is ready with model a-model.", displayed)

    def test_model_command_cancel_returns_current_config_without_saving(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENROUTER,
                model="openrouter/auto",
                api_key="router-key",
                mode=ControlMode.HITL,
            )
            displayed: list[str] = []
            client = FakeHttpClient(
                {
                    "https://openrouter.ai/api/v1/models": FakeResponse(
                        status_code=200,
                        payload={"data": [{"id": "z-model", "name": "Zulu"}, {"id": "a-model", "name": "Alpha"}]},
                    )
                }
            )

            updated = handle_session_command(
                "/model",
                current,
                paths,
                select=lambda prompt, choices: "Cancel",
                display=displayed.append,
                client=client,
            )

            self.assertEqual(current, updated)
            self.assertIsNone(load_app_config(paths))
            self.assertEqual(["OpenRouter connection verified.", "Model update cancelled."], displayed)

    def test_provider_command_supports_ollama_without_api_key_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )
            displayed: list[str] = []
            selections = iter(("Ollama", "llama3.2:latest"))
            client = FakeHttpClient(
                {
                    "http://localhost:11434/api/tags": FakeResponse(
                        status_code=200,
                        payload={"models": [{"name": "llama3.2:latest"}]},
                    )
                }
            )

            updated = handle_session_command(
                "/provider",
                current,
                paths,
                select=lambda prompt, choices: next(selections),
                secret_prompt=lambda prompt: self.fail("Ollama should not prompt for an API key"),
                display=displayed.append,
                client=client,
            )

            self.assertEqual(Provider.OLLAMA, updated.provider)
            self.assertEqual("llama3.2:latest", updated.model)
            self.assertIsNone(updated.api_key)
            self.assertEqual("http://localhost:11434", updated.base_url)
            self.assertIn("Ollama is reachable at http://localhost:11434/api/tags.", displayed)
            self.assertIn("Ollama connection ready.", displayed)

    def test_provider_command_can_switch_to_custom_ollama_base_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )
            displayed: list[str] = []
            selections = iter(("Ollama", "Use a custom Ollama base URL", "llama3.2:latest"))

            class CustomBaseUrlClient(FakeHttpClient):
                def get(self, url: str, *, headers: dict[str, str], timeout: float) -> FakeResponse:
                    if url == "http://localhost:11434/api/tags":
                        raise OSError("connection refused")
                    if url == "http://localhost:11555/api/tags":
                        return FakeResponse(
                            status_code=200,
                            payload={"models": [{"name": "llama3.2:latest"}]},
                        )
                    raise AssertionError(f"Unexpected URL {url}")

            updated = handle_session_command(
                "/provider",
                current,
                paths,
                select=lambda prompt, choices: next(selections),
                secret_prompt=lambda prompt: self.fail("Ollama should not prompt for an API key"),
                text_prompt=lambda prompt, default="": "http://localhost:11555/api/tags",
                display=displayed.append,
                client=CustomBaseUrlClient(),
            )

            self.assertEqual(Provider.OLLAMA, updated.provider)
            self.assertEqual("llama3.2:latest", updated.model)
            self.assertIsNone(updated.api_key)
            self.assertEqual("http://localhost:11555", updated.base_url)
            self.assertIn("Checking local Ollama runtime at http://localhost:11555/api/tags.", displayed)
            # Plan 188: load_app_config always returns Plan Mode on — normalize for the compare.
            self.assertEqual(replace(updated, plan_mode_enabled=True), load_app_config(paths))

    def test_provider_command_can_start_ollama_in_hotl_after_user_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            displayed: list[str] = []
            selections = iter(("Ollama", "Start Ollama now", "llama3.2:latest"))
            process_calls: list[list[str]] = []

            class RecoveringOllamaClient(FakeHttpClient):
                def __init__(self) -> None:
                    super().__init__()
                    self.calls = 0

                def get(self, url: str, *, headers: dict[str, str], timeout: float) -> FakeResponse:
                    self.calls += 1
                    if self.calls == 1:
                        raise OSError("connection refused")
                    return FakeResponse(
                        status_code=200,
                        payload={"models": [{"name": "llama3.2:latest"}]},
                    )

            def fake_popen(command, stdout=None, stderr=None, text=True, bufsize=1):
                process_calls.append(list(command))
                return FakePullProcess(lines=())

            with (
                patch("duckln.ai_client.shutil.which", return_value="/usr/local/bin/ollama"),
                patch("duckln.config.shutil.which", return_value="/usr/local/bin/ollama"),
                patch("duckln.config.subprocess.Popen", side_effect=fake_popen),
                patch("duckln.config.time.sleep"),
            ):
                updated = handle_session_command(
                    "/provider",
                    current,
                    paths,
                    select=lambda prompt, choices: next(selections),
                    secret_prompt=lambda prompt: self.fail("Ollama should not prompt for an API key"),
                    display=displayed.append,
                    client=RecoveringOllamaClient(),
                )

            self.assertEqual(Provider.OLLAMA, updated.provider)
            self.assertEqual("llama3.2:latest", updated.model)
            self.assertEqual([["ollama", "serve"]], process_calls)
            self.assertIn("Starting Ollama with `ollama serve`...", displayed)
            self.assertIn("Ollama connection ready.", displayed)

    def test_model_command_reuses_ollama_pull_flow_and_preserves_null_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OLLAMA,
                model="llama3.2:latest",
                api_key=None,
                base_url="http://localhost:11434",
                mode=ControlMode.HITL,
            )
            displayed: list[str] = []
            selections = iter(("Enter a model name manually",))
            pull_commands: list[list[str]] = []

            class PullableOllamaClient:
                def __init__(self) -> None:
                    self.models = ["llama3.2:latest"]

                def get(self, url: str, *, headers: dict[str, str], timeout: float) -> FakeResponse:
                    return FakeResponse(
                        status_code=200,
                        payload={"models": [{"name": model_name} for model_name in self.models]},
                    )

            client = PullableOllamaClient()

            def fake_popen(command, stdout=None, stderr=None, text=True, bufsize=1):
                pull_commands.append(list(command))
                client.models.append(command[-1])
                return FakePullProcess()

            with patch("duckln.config.subprocess.Popen", side_effect=fake_popen), \
                 patch("duckln.config.verify_live_reply", return_value=True):  # Plan 182 F1: live check
                updated = handle_session_command(
                    "/model",
                    current,
                    paths,
                    select=lambda prompt, choices: next(selections),
                    text_prompt=lambda prompt, default="": "mistral:latest",
                    display=displayed.append,
                    client=client,
                )

            self.assertEqual(Provider.OLLAMA, updated.provider)
            self.assertEqual("mistral:latest", updated.model)
            self.assertIsNone(updated.api_key)
            self.assertEqual("http://localhost:11434", updated.base_url)
            self.assertEqual([["ollama", "pull", "mistral:latest"]], pull_commands)
            self.assertIn("Running `ollama pull mistral:latest`...", displayed)
            # Plan 186 F1a: phase noise ("pulling manifest") is collapsed, not stacked in chat;
            # a single confirmation appears on completion.
            self.assertNotIn("pulling manifest", displayed)
            self.assertIn("✓ mistral:latest downloaded and ready.", displayed)
            self.assertIn("Ollama is ready with model mistral:latest.", displayed)
            # Plan 188: load_app_config always returns Plan Mode on — normalize for the compare.
            self.assertEqual(replace(updated, plan_mode_enabled=True), load_app_config(paths))

    def test_config_menu_routes_to_provider_flow(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENROUTER,
                model="openrouter/auto",
                api_key="router-key",
                mode=ControlMode.HITL,
            )
            selections = iter(
                (
                    "/provider — Change the AI provider, API key, and model.",
                    "Anthropic",
                    "Use this key",
                    "claude-3-5-haiku",
                )
            )
            client = FakeHttpClient(
                {
                    "https://api.anthropic.com/v1/models": FakeResponse(
                        status_code=200,
                        payload={"data": [{"id": "claude-3-5-haiku", "display_name": "Claude 3.5 Haiku"}]},
                    )
                }
            )

            updated = handle_session_command(
                "/config",
                current,
                paths,
                select=lambda prompt, choices: next(selections),
                secret_prompt=lambda prompt: "anthropic-key",
                display=lambda message: None,
                client=client,
            )

            self.assertEqual(Provider.ANTHROPIC, updated.provider)
            self.assertEqual("claude-3-5-haiku", updated.model)
            # Plan 188: load_app_config always returns Plan Mode on — normalize for the compare.
            self.assertEqual(replace(updated, plan_mode_enabled=True), load_app_config(paths))

    def test_slash_palette_executes_selected_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )
            selections = iter(
                (
                    "/mode — Change how much Duckln can do for you.",
                    "HOTL — Human-on-the-Loop: AI suggests exact commands; you approve before execution.",
                )
            )

            updated = handle_session_command(
                "/",
                current,
                paths,
                select=lambda prompt, choices: next(selections),
                display=lambda message: None,
            )

            self.assertEqual(ControlMode.HOTL, updated.mode)
            # Plan 188: load_app_config always returns Plan Mode on — normalize for the compare.
            self.assertEqual(replace(updated, plan_mode_enabled=True), load_app_config(paths))

    def test_healthcheck_command_reports_status_without_changing_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )
            displayed: list[str] = []
            client = FakeHttpClient(
                {
                    "https://api.openai.com/v1/models": FakeResponse(
                        status_code=200,
                        payload={"data": [{"id": "gpt-4o-mini"}]},
                    )
                }
            )

            updated = handle_session_command(
                "/healthcheck",
                current,
                paths,
                display=displayed.append,
                client=client,
            )

            self.assertEqual(current, updated)
            self.assertTrue(any("[PASS]" in message or "[FAIL]" in message for message in displayed))

    def test_repos_command_displays_selected_cached_repo_without_changing_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )
            repo_record = {
                "name": "alpha",
                "repo_url": "https://example.com/alpha",
                "stars": 50,
                "description": "Alpha repository for tests",
                "category": "LLM",
                "framework": "Python",
                "last_updated": "2026-03-22",
            }
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps({"last_updated": "2026-03-23", "repos": [repo_record]}) + "\n", encoding="utf-8")
            displayed: list[str] = []

            def select_prompt(prompt, choices):
                if "Where should Duckln set up" in prompt:
                    return "Cancel"
                if "You selected" in prompt:
                    return "Cancel"
                return next(choice for choice in choices if choice.startswith("alpha "))

            updated = handle_session_command(
                "/repos",
                current,
                paths,
                select=select_prompt,
                display=displayed.append,
            )

            self.assertEqual(current, updated)
            joined = "\n".join(displayed)
            self.assertIn("You selected alpha.", joined)
            self.assertIn("Okay. I’ll leave alpha untouched.", displayed)
            self.assertNotIn("Supervisor agent selected", joined)
            self.assertNotIn("Execution target:", joined)
            self.assertNotIn("System fit:", joined)
            self.assertNotIn("Why:", joined)

    def test_repos_command_defers_preflight_until_after_user_choice(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )
            repo_record = {
                "name": "alpha",
                "repo_url": "https://example.com/alpha",
                "stars": 50,
                "description": "Alpha repository for tests",
                "category": "LLM",
                "framework": "Python",
                "last_updated": "2026-03-22",
            }
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps({"last_updated": "2026-03-23", "repos": [repo_record]}) + "\n", encoding="utf-8")
            displayed: list[str] = []

            with patch("duckln.main.assess_repo_preflight") as preflight_mock:
                updated = handle_session_command(
                    "/repos",
                    current,
                    paths,
                    select=lambda prompt, choices: "Cancel"
                    if "Where should Duckln set up" in prompt or "You selected" in prompt
                    else next(choice for choice in choices if choice.startswith("alpha ")),
                    display=displayed.append,
                )

            self.assertEqual(current, updated)
            preflight_mock.assert_not_called()
            self.assertIn("You selected alpha.", "\n".join(displayed))

    def test_repos_command_prompts_environment_selection_for_custom_catalog_repo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )
            displayed: list[str] = []
            prompts: list[str] = []
            selected_repo = RepoCatalogRecord(
                name="custom-alpha",
                repo_url="https://github.com/example/custom-alpha",
                stars=42,
                description="Custom alpha repo.",
                category="LLM",
                framework="Python",
                last_updated="2026-04-01",
            )

            def select_prompt(prompt: str, choices: tuple[str, ...]) -> str:
                prompts.append(prompt)
                if "Where should Duckln set up custom-alpha?" in prompt:
                    return "Cancel"
                raise AssertionError(f"Unexpected prompt: {prompt}")

            with patch("duckln.main.open_repo_catalog", return_value=selected_repo):
                updated = handle_session_command(
                    "/repos",
                    current,
                    paths,
                    select=select_prompt,
                    display=displayed.append,
                )

            self.assertEqual(current, updated)
            self.assertTrue(any("Where should Duckln set up custom-alpha?" in prompt for prompt in prompts))
            self.assertIn("You selected custom-alpha.", "\n".join(displayed))
            self.assertIn("Okay. I’ll leave custom-alpha untouched.", displayed)

    def test_repos_command_returns_cleanly_when_repo_selection_is_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )
            repo_record = {
                "name": "alpha",
                "repo_url": "https://example.com/alpha",
                "stars": 50,
                "description": "Alpha repository for tests",
                "category": "LLM",
                "framework": "Python",
                "last_updated": "2026-03-22",
            }
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps({"last_updated": "2026-03-23", "repos": [repo_record]}) + "\n", encoding="utf-8")
            displayed: list[str] = []

            updated = handle_session_command(
                "/repos",
                current,
                paths,
                select=lambda prompt, choices: CANCEL_REPO_SELECTION,
                display=displayed.append,
            )

            self.assertEqual(current, updated)
            self.assertEqual(["No repository selected."], displayed)

    def test_repos_refresh_command_reports_success_without_changing_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )
            displayed: list[str] = []

            with patch("duckln.main.refresh_local_repo_catalog") as refresh_catalog:
                refresh_catalog.return_value = RepoCatalogRefreshResult(
                    ok=True,
                    message="Repo catalog refreshed: 14 repositories cached.",
                    records=(),
                )

                updated = handle_session_command(
                    "/repos refresh",
                    current,
                    paths,
                    display=displayed.append,
                )

            self.assertEqual(current, updated)
            refresh_catalog.assert_called_once_with(paths.config_dir)
            self.assertEqual(
                ["Refreshing the cached repo catalog...", "Repo catalog refreshed: 14 repositories cached."],
                displayed,
            )

    def test_repos_refresh_command_reports_retryable_error_without_changing_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )
            displayed: list[str] = []

            with patch("duckln.main.refresh_local_repo_catalog") as refresh_catalog:
                refresh_catalog.return_value = RepoCatalogRefreshResult(
                    ok=False,
                    message="Repo catalog refresh failed: GitHub topic fetch failed for 'llm' (HTTP 503).",
                    records=(),
                )

                updated = handle_session_command(
                    "/repos refresh",
                    current,
                    paths,
                    display=displayed.append,
                )

            self.assertEqual(current, updated)
            refresh_catalog.assert_called_once_with(paths.config_dir)
            self.assertEqual(
                [
                    "Refreshing the cached repo catalog...",
                    "Retryable error: Repo catalog refresh failed: GitHub topic fetch failed for 'llm' (HTTP 503).",
                ],
                displayed,
            )

    def test_vm_command_guides_install_without_changing_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )
            displayed: list[str] = []

            from unittest.mock import patch

            with patch("duckln.main.create_multipass_vm") as create_vm:
                create_vm.side_effect = lambda paths, text_prompt=None, display=print, approve_prompt=None, runner=None, system_probe=None: display("Multipass is not installed.")
                updated = handle_session_command(
                    "/vm",
                    current,
                    paths,
                    text_prompt=lambda prompt, default: default,
                    display=displayed.append,
                )

            self.assertEqual(current, updated)
            self.assertIn("Multipass is not installed.", displayed)

    def test_vm_command_runs_configure_step_after_successful_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )

            from unittest.mock import patch
            from duckln.vm import VmProvisionResult

            with (
                patch("duckln.main.create_multipass_vm") as create_vm,
                patch("duckln.main.configure_existing_multipass_vm") as configure_vm,
            ):
                create_vm.return_value = VmProvisionResult(
                    ok=True,
                    vm_name="duckln-vm",
                    message="Ubuntu VM created and ready.",
                    connection_commands=("multipass shell duckln-vm",),
                )

                updated = handle_session_command(
                    "/vm",
                    current,
                    paths,
                    select=lambda prompt, choices: "No",
                    text_prompt=lambda prompt, default: default,
                    display=lambda message: None,
                )

            self.assertEqual(current, updated)
            configure_vm.assert_called_once()
            args, kwargs = configure_vm.call_args
            self.assertEqual(("duckln-vm", paths), args)
            self.assertEqual("No", kwargs["select"]("prompt", ("Yes", "No")))

    def test_memory_clear_command_cancels_without_changes_when_confirmation_is_declined(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )
            displayed: list[str] = []
            selections = iter(("Clear session history only", "No"))

            updated = handle_session_command(
                "/memory clear",
                current,
                paths,
                select=lambda prompt, choices: next(selections),
                display=displayed.append,
            )

            self.assertEqual(current, updated)
            self.assertIn("Delete session history and saved session summaries only.", displayed)
            self.assertIn("No memory was cleared.", displayed)

    def test_memory_clear_command_returns_immediately_for_cancel_option(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )
            displayed: list[str] = []

            updated = handle_session_command(
                "/memory clear",
                current,
                paths,
                select=lambda prompt, choices: "Cancel and return",
                display=displayed.append,
            )

            self.assertEqual(current, updated)
            self.assertEqual([], displayed)

    def test_memory_clear_command_runs_selected_scope_after_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )
            displayed: list[str] = []
            selections = iter(("Clear everything and reset to factory", "Yes"))

            with self.assertRaises(SessionExitRequested):
                handle_session_command(
                    "/memory clear",
                    current,
                    paths,
                    select=lambda prompt, choices: next(selections),
                    display=displayed.append,
                )

            self.assertIn("Delete Duckln state and reset managed memory to its default contract.", displayed)
            self.assertIn("Cleared Duckln state and reset managed memory.", displayed)
            self.assertIsNone(load_app_config(paths))

    def test_main_runtime_loop_routes_slash_commands_until_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )
            save_app_config(current, paths)
            displayed: list[str] = []
            user_inputs = iter(("/", "/memory clear", "exit"))

            with (
                patch("duckln.main.resolve_config_paths", return_value=paths),
                patch(
                    "duckln.main.probe_system",
                    return_value=SystemProbe(
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
                    ),
                ),
                patch("duckln.main.record_system_probe"),
                patch("duckln.main.handle_session_command", side_effect=lambda command, current, paths, **kwargs: current) as handle_command,
            ):
                exit_code = main(
                    input_func=lambda prompt: next(user_inputs),
                    display=displayed.append,
                )

            self.assertEqual(0, exit_code)
            self.assertEqual(["/", "/memory clear"], [call.args[0] for call in handle_command.call_args_list])
            self.assertTrue(any(message.startswith("🐣 ") for message in displayed))
            self.assertIn("Exiting Duckln.", displayed)
            self.assertTrue(any("provider : OpenAI" in re.sub(r"\x1b\[[0-9;]*m", "", message) for message in displayed))

    def test_main_non_slash_input_gets_conversational_command_hint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            save_app_config(
                AppConfig(
                    provider=Provider.OPENAI,
                    model="gpt-4o-mini",
                    api_key="openai-key",
                    mode=ControlMode.HITL,
                ),
                paths,
            )
            displayed: list[str] = []
            user_inputs = iter(("change provider", "exit"))

            with (
                patch("duckln.main.resolve_config_paths", return_value=paths),
                patch("duckln.main.probe_system", return_value=_probe()),
                patch("duckln.main.record_system_probe"),
            ):
                exit_code = main(
                    input_func=lambda prompt: next(user_inputs),
                    display=displayed.append,
                )

            self.assertEqual(0, exit_code)
            self.assertTrue(any("/provider" in message for message in displayed))
            self.assertFalse(any("Use / for commands. Type exit to quit." == message for message in displayed))

    def test_repos_command_shows_compact_status_box_before_bringup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )
            repo_record = {
                "name": "alpha",
                "repo_url": "https://example.com/alpha",
                "stars": 50,
                "description": "Alpha repository for tests",
                "category": "LLM",
                "framework": "Python",
                "last_updated": "2026-03-22",
            }
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps({"last_updated": "2026-03-23", "repos": [repo_record]}) + "\n", encoding="utf-8")
            displayed: list[str] = []

            ok_result = SimpleNamespace(
                verification_passed=True,
                should_offer_repair=False,
                failure_type=None,
                message="ok",
                recovery_decision=None,
                setup_outcome=None,
            )
            with (
                patch("duckln.main.bring_up_selected_repo", return_value=ok_result),
                patch("duckln.main.run_prepared_repo", return_value=ok_result),
            ):
                handle_session_command(
                    "/repos",
                    current,
                    paths,
                    select=lambda prompt, choices: next(choice for choice in choices if choice.startswith("alpha "))
                    if "Select a repository" in prompt
                    else "Set up on local machine"
                    if "Where should Duckln set up" in prompt
                    else "Set it up",
                    display=displayed.append,
                )

            joined = re.sub(r"\x1b\[[0-9;]*m", "", "\n".join(str(item) for item in displayed))
            self.assertIn("You selected alpha.", joined)
            self.assertIn("Reading the repo files and checking the safest setup route.", joined)
            self.assertNotIn("Supervisor agent", joined)

    def test_render_repo_preflight_summary_uses_grouped_machine_fit_sections(self) -> None:
        preflight = SimpleNamespace(
            fit_status="workable_but_tight",
            local_vm_recommendation="Use an Ubuntu VM if you want more breathing room.",
            feasibility=SimpleNamespace(
                usable_ram_gib=11.5,
                failure_modes=("swap pressure", "slow first install"),
                compute_path="CPU or MPS",
            ),
        )
        repo = RepoCatalogRecord("alpha", "https://example.com/alpha", 50, "Alpha repository", "LLM", "Python", "2026-03-22")

        rendered = _render_repo_preflight_summary(repo=repo, preflight=preflight)

        self.assertIn("Machine fit", rendered)
        self.assertIn("alpha can work here, but memory will be tight.", rendered.lower())
        self.assertIn("Practical notes", rendered)
        self.assertIn("- Usable RAM:", rendered)
        self.assertIn("- Watch-out:", rendered)
        self.assertIn("Use an Ubuntu VM", rendered)

    def test_render_repo_requirements_summary_groups_tools_and_fit_notes(self) -> None:
        preflight = SimpleNamespace(
            fit_status="comfortable",
            local_vm_recommendation="Stay local first.",
            feasibility=SimpleNamespace(
                usable_ram_gib=23.0,
                failure_modes=(),
            ),
        )
        repo = RepoCatalogRecord("alpha", "https://example.com/alpha", 50, "Alpha repository", "LLM", "Python", "2026-03-22")
        repo_knowledge = SimpleNamespace(
            cpu_profile="CPU-friendly",
            ram_profile="16-24 GiB local target",
            gpu_profile="MPS optional",
            required_tools=("git", "python", "ffmpeg"),
        )

        rendered = _render_repo_requirements_summary(repo=repo, preflight=preflight, repo_knowledge=repo_knowledge)

        self.assertIn("You'll likely want:", rendered)
        self.assertIn("- git", rendered)
        self.assertIn("- python", rendered)
        self.assertIn("Practical fit", rendered)
        self.assertIn("- Profile:", rendered)
        self.assertIn("- Usable RAM:", rendered)

    def test_main_accepts_slash_exit_and_prints_session_header(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            save_app_config(
                AppConfig(
                    provider=Provider.OPENAI,
                    model="gpt-4o-mini",
                    api_key="openai-key",
                    mode=ControlMode.HOTL,
                    user_name="Shreyas",
                    onboarding_complete=True,
                    safety_accepted_at="2026-04-04T00:00:00+00:00",
                    preferred_mode=ControlMode.HOTL,
                ),
                paths,
            )
            displayed: list[str] = []

            with (
                patch("duckln.main.resolve_config_paths", return_value=paths),
                patch("duckln.main.probe_system", return_value=_probe()),
                patch("duckln.main.record_system_probe"),
            ):
                exit_code = main(
                    argv=[],
                    input_func=lambda prompt: "/exit",
                    display=displayed.append,
                )

            self.assertEqual(0, exit_code)
            joined = re.sub(r"\x1b\[[0-9;]*m", "", "\n".join(displayed))
            self.assertIn("◆ Duckln", joined)
            self.assertIn("provider : OpenAI", joined)
            self.assertIn("model    : gpt-4o-mini", joined)
            self.assertIn("mode     : HOTL", joined)
            self.assertIn("user     : Shreyas", joined)
            self.assertIn("/provider to change", joined)
            self.assertTrue(any(message.startswith("🐣 ") for message in displayed))
            self.assertIn("Exiting Duckln.", displayed)

    def test_free_text_fallback_defaults_to_helpful_guidance(self) -> None:
        response = _respond_to_free_text("can you help me get started")

        self.assertTrue("tell me" in response.text.lower() or "narrow" in response.text.lower())

    def test_free_text_fallback_maps_repo_requests_to_repos_command(self) -> None:
        response = _respond_to_free_text("I want to set up a repository")

        self.assertIn("/repos", response.text)

    def test_free_text_fallback_handles_greeting_naturally(self) -> None:
        response = _respond_to_free_text("hello")

        self.assertTrue(response.text.startswith(("Hi.", "Hello.", "Hey.")))
        self.assertNotIn("Tell me what you want to change, or use /help", response.text)

    def test_free_text_fallback_handles_how_are_you_naturally(self) -> None:
        response = _respond_to_free_text("how are you")

        self.assertTrue(any(phrase in response.text for phrase in ("Doing well", "Good.", "Doing well and ready")))
        self.assertIn("help", response.text.lower())

    def test_free_text_fallback_handles_what_can_you_do_naturally(self) -> None:
        response = _respond_to_free_text("what can you do")

        self.assertTrue(any(phrase in response.text.lower() for phrase in ("strongest", "handle practical terminal tasks", "my lane is")))
        self.assertIn("repo", response.text.lower())

    def test_free_text_fallback_handles_whisper_setup_request(self) -> None:
        response = _respond_to_free_text("help me set up whisper")

        self.assertIn("/repos", response.text)
        self.assertTrue("local" in response.text.lower() and "vm" in response.text.lower())

    def test_free_text_fallback_answers_repo_setup_confidence_more_directly(self) -> None:
        response = _respond_to_free_text("how good are you with the setup of repos?")

        self.assertIn("repo", response.text.lower())
        self.assertTrue(any(phrase in response.text.lower() for phrase in ("strong", "built for", "strongest")))
        self.assertNotIn("Try /help", response.text)

    def test_free_text_fallback_answers_confidence_questions_directly(self) -> None:
        response = _respond_to_free_text("how confident are you with your ability?")

        self.assertIn("confident", response.text.lower())
        self.assertTrue(any(phrase in response.text.lower() for phrase in ("repo setup", "terminal setup", "practical setup")))
        self.assertNotIn("/help", response.text)

    def test_free_text_fallback_answers_system_followup_from_probe(self) -> None:
        response = _respond_to_free_text(
            "how do you know i am using mac",
            system_probe=SystemProbe(
                operating_system="Darwin",
                architecture="arm64",
                cpu_logical_cores=8,
                ram_bytes=16 * 1024**3,
                disk_free_bytes=100 * 1024**3,
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
        )

        self.assertIn("Duckln checked and stored the local system", response.text)
        self.assertIn("Apple Silicon", response.text)

    def test_free_text_fallback_answers_system_capacity_directly(self) -> None:
        response = _respond_to_free_text(
            "can you tell me about my system capacity?",
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
        )

        self.assertIn("8 logical CPU cores", response.text)
        self.assertIn("8.0 GiB RAM", response.text)
        self.assertIn("MPS-capable", response.text)
        self.assertNotIn("/help", response.text)
        self.assertEqual("system_summary", response.route_family)

    def test_free_text_fallback_answers_memory_meta_from_learning_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            store = initialize_state_store(config_dir)
            store.upsert_learning_record(
                learning_key="conversation:phrase:recommendation",
                family="conversation",
                subject_key="phrase:recommendation",
                summary="Route messy recommendation phrasing directly.",
                signal="corrected",
            )
            store.upsert_learning_record(
                learning_key="conversation:phrase:recommendation",
                family="conversation",
                subject_key="phrase:recommendation",
                summary="Route messy recommendation phrasing directly.",
                signal="corrected",
            )

            response = _respond_to_free_text("did you learn anything today from this conversation?", config_dir=config_dir)

            self.assertIn("promoted heuristic", response.text.lower())
            self.assertNotIn("/help", response.text)
            self.assertEqual("memory_meta", response.route_family)

    def test_free_text_ambiguous_turn_prefers_clarification_over_generic_fallback(self) -> None:
        response = _respond_to_free_text("help me with it")

        self.assertEqual("clarify", response.intent)
        self.assertEqual("clarify", response.route_family)
        self.assertIn("do you mean", response.text.lower())
        self.assertNotIn("/help", response.text)
        self.assertNotIn("/repos", response.text)

    def test_free_text_ambiguous_repo_lifecycle_turn_prefers_state_clarification_over_shortlist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            write_followup_state(
                config_dir,
                {
                    "active_topic": "repo_recommendation_single",
                    "last_route_family": "repo_recommendation_single",
                    "pending_offer_kind": "recommend_best_fit",
                    "pending_offer_id": "offer:1",
                    "pending_offer_thread_id": "thread:1",
                    "active_thread_id": "thread:1",
                    "pending_clarification_options": ["second-best repo", "full shortlist", "next step for private-gpt"],
                    "shortlist_primary_repo": "private-gpt",
                    "shortlist_secondary_repo": "fastchat",
                },
            )

            response = _respond_to_free_text("do we already have a repo?", config_dir=config_dir)

            self.assertEqual("clarify", response.intent)
            self.assertIn("active repo status", response.text.lower())
            self.assertTrue("all tracked repos" in response.text.lower() or "repo recommendation" in response.text.lower())
            self.assertNotIn("second-best repo", response.text.lower())
            self.assertNotIn("full shortlist", response.text.lower())

    def test_free_text_conversation_repair_explains_previous_reply_instead_of_stale_thread(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            write_followup_state(
                config_dir,
                {
                    "last_route_family": "repo_capability_coverage",
                    "pending_offer_kind": "recommend_best_fit",
                    "pending_offer_id": "offer:1",
                    "pending_offer_thread_id": "thread:1",
                    "active_thread_id": "thread:1",
                },
            )

            response = _respond_to_free_text("sorry i did not understand", config_dir=config_dir)

            self.assertEqual("conversation_repair", response.intent)
            self.assertIn("repos duckln can help with", response.text.lower())
            self.assertNotIn("second-best repo", response.text.lower())

    def test_free_text_conversation_repair_handles_elaborate_without_shortlist_loop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            write_followup_state(
                config_dir,
                {
                    "last_route_family": "repo_recommendation_single",
                    "last_discussed_repo_name": "whisper",
                    "pending_offer_kind": "recommend_best_fit",
                    "pending_offer_id": "offer:1",
                    "pending_offer_thread_id": "thread:1",
                    "active_thread_id": "thread:1",
                },
            )

            response = _respond_to_free_text("can you elaborate what are you talking about?", config_dir=config_dir)

            self.assertEqual("conversation_repair", response.intent)
            self.assertIn("recommendation", response.text.lower())
            self.assertNotIn("second-best repo", response.text.lower())

    def test_free_text_conversation_restate_says_it_again_plainly_without_reopening_thread(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
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

            response = _respond_to_free_text("say that again simply", config_dir=config_dir)

            self.assertEqual("conversation_restate", response.intent)
            self.assertEqual("conversation_restate", response.route_family)
            self.assertIn("plain version", response.text.lower())
            self.assertNotIn("live recommendation thread", response.text.lower())

    def test_free_text_capability_defaults_to_structured_helpful_block(self) -> None:
        response = _respond_to_free_text("what can you help me with?")

        self.assertEqual("capability", response.intent)
        self.assertIn("Here’s where I’m useful", response.text)
        self.assertIn("- Pick a repo from /repos", response.text)

    def test_free_text_mixed_session_keeps_repair_turns_out_of_stale_recommendation_loop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-09",
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
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            probe = SystemProbe(
                operating_system="Darwin",
                architecture="arm64",
                cpu_logical_cores=8,
                ram_bytes=16 * 1024**3,
                disk_free_bytes=100 * 1024**3,
                python_version="3.11.8",
                gpu=GpuProbeState(
                    backend="mps",
                    summary="Apple Silicon with MPS.",
                    cuda_capable=False,
                    cuda_available=False,
                    mps_capable=True,
                    mps_available=True,
                ),
            )

            capability = _respond_to_free_text("what can you help me with?", config_dir=config_dir, system_probe=probe)
            recommendation = _respond_to_free_text(
                "which repo do you recommend for my system",
                config_dir=config_dir,
                system_probe=probe,
                recent_replies=(capability,),
            )
            repair = _respond_to_free_text(
                "sorry i did not understand",
                config_dir=config_dir,
                system_probe=probe,
                recent_replies=(capability, recommendation),
            )
            elaborate = _respond_to_free_text(
                "can you elaborate what are you talking about?",
                config_dir=config_dir,
                system_probe=probe,
                recent_replies=(recommendation, repair),
            )

            self.assertEqual("capability", capability.intent)
            self.assertIn("- Pick a repo from /repos", capability.text)
            self.assertTrue(recommendation.intent.startswith("repo_recommendation"))
            self.assertEqual("conversation_repair", repair.intent)
            self.assertEqual("conversation_repair", elaborate.intent)
            self.assertNotIn("We still have a live recommendation thread", repair.text)
            self.assertNotIn("second-best repo", elaborate.text.lower())
            self.assertNotIn("next step for whisper", elaborate.text.lower())

    def test_free_text_low_confidence_turn_prefers_clarification_when_likely_interpretations_exist(self) -> None:
        # A genuinely ambiguous turn ASKS (generic clarify) — "ask, don't guess". Plan 193 only
        # removes the DANGEROUS repo-ASSUMING options (active repo status / continue repairing / path).
        response = _respond_to_free_text("the thing is not working")

        self.assertEqual("clarify", response.route_family)
        self.assertIn("do you mean", response.text.lower())
        self.assertNotIn("active repo status", response.text.lower())
        self.assertNotIn("continue repairing", response.text.lower())

    def test_free_text_truly_opaque_turn_falls_back_to_contextual_clarification(self) -> None:
        response = _respond_to_free_text("blargle wobble maybe")

        self.assertEqual("clarify", response.route_family)
        self.assertIn("do you mean", response.text.lower())
        self.assertNotIn("active repo status", response.text.lower())

    def test_free_text_current_turn_wins_over_live_recommendation_thread_for_social_status_and_repair(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
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
                },
            )
            initialize_state_store(config_dir).upsert_repo_state(
                repo_key="https://example.com/whisper",
                status="ready",
                summary="whisper is ready",
                repo_path="/tmp/whisper",
                repo_url="https://example.com/whisper",
                metadata={"repo_name": "whisper"},
            )

            social = _respond_to_free_text("How u doing?", config_dir=config_dir)
            capability = _respond_to_free_text("what can your help me with?", config_dir=config_dir)
            active = _respond_to_free_text("do i have an active repo in my system?", config_dir=config_dir)
            repair = _respond_to_free_text("Do you understand what i am asking?", config_dir=config_dir)

            self.assertEqual("rapport", social.intent)
            self.assertNotIn("recommendation thread", social.text.lower())
            self.assertEqual("capability", capability.intent)
            self.assertIn("terminal", capability.text.lower())
            self.assertEqual("repo_active", active.intent)
            self.assertIn("active repo", active.text.lower())
            self.assertNotIn("recommendation thread", active.text.lower())
            self.assertEqual("conversation_repair", repair.intent)
            self.assertNotIn("second-best repo", repair.text.lower())

    def test_free_text_active_repo_question_tolerates_small_typo_and_stays_repo_grounded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-05",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            initialize_state_store(config_dir).upsert_repo_state(
                repo_key="https://example.com/whisper",
                status="ready",
                summary="whisper is ready",
                repo_path="/tmp/whisper",
                repo_url="https://example.com/whisper",
                metadata={"repo_name": "whisper"},
            )

            active = _respond_to_free_text("is there aactive repo", config_dir=config_dir)

            self.assertIn(active.intent, {"repo_active", "repo_lifecycle_active"})
            self.assertIn("whisper", active.text.lower())
            self.assertIn("active repo", active.text.lower())

    def test_free_text_fallback_answers_repo_requirement_followup_for_whisper(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            response = _respond_to_free_text(
                "how much memory and cpu will be required for whisper",
                config_dir=config_dir,
                system_probe=SystemProbe(
                    operating_system="Darwin",
                    architecture="arm64",
                    cpu_logical_cores=8,
                    ram_bytes=16 * 1024**3,
                    disk_free_bytes=100 * 1024**3,
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
                recent_turns=(
                    ConversationTurn(role="user", content="which repo do you recommend for my system"),
                    ConversationTurn(role="assistant", content="For this Apple Silicon Mac, I'd start with open-webui, whisper, or speechbrain."),
                ),
            )

            self.assertIn("whisper", response.text.lower())
            self.assertTrue(any(token in response.text.lower() for token in ("8-16 gb", "16 gb", "cpu")))
            self.assertNotIn("/memory clear", response.text)

    def test_free_text_fallback_answers_minimum_requirement_question_for_whisper(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            store = initialize_state_store(config_dir)
            store.upsert_repo_knowledge(
                repo_key="https://example.com/whisper",
                repo_name="whisper",
                repo_url="https://example.com/whisper",
                source="remote",
                summary="whisper knowledge (remote). requirements: a few CPU cores; 8-16 GB RAM; GPU optional.",
                cpu_profile="a few CPU cores",
                ram_profile="8-16 GB RAM",
                gpu_profile="GPU optional",
                required_tools=("Python", "ffmpeg"),
            )

            response = _respond_to_free_text(
                "what the minimum requirement for whisper repo?",
                config_dir=config_dir,
                system_probe=SystemProbe(
                    operating_system="Darwin",
                    architecture="arm64",
                    cpu_logical_cores=8,
                    ram_bytes=16 * 1024**3,
                    disk_free_bytes=100 * 1024**3,
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
                recent_turns=(ConversationTurn(role="user", content="which repo do you recommend"),),
            )

            self.assertIn("whisper", response.text.lower())
            self.assertIn("8-16 gb ram", response.text.lower())
            self.assertIn("ffmpeg", response.text.lower())
            self.assertNotIn("/repos", response.text)

    def test_free_text_fallback_handles_robotic_feedback_directly(self) -> None:
        response = _respond_to_free_text("you sound very robotic")

        self.assertIn("too mechanical", response.text.lower())
        self.assertIn("more direct", response.text.lower())

    def test_free_text_fallback_answers_install_location_access_and_removal_from_repo_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            project_dir = config_dir / "projects" / "whisper"
            store = initialize_state_store(config_dir)
            store.upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path=str(project_dir),
                status="ready",
                summary="Whisper ready.",
            )
            store.upsert_repo_knowledge(
                repo_key="https://example.com/whisper",
                repo_name="whisper",
                repo_url="https://example.com/whisper",
                source="bringup-success",
                summary="Supervisor agent confirmed whisper setup.",
                metadata={
                    "install_location": str(project_dir),
                    "environment_path": str(project_dir / ".venv"),
                    "run_command": ".venv/bin/python -m pip --version",
                    "access_hint": "Use the CLI from the managed project directory.",
                    "removal_hint": f"remove the project directory at {project_dir}; remove the virtualenv at {project_dir / '.venv'}",
                },
            )

            install_reply = _respond_to_free_text("where is whisper installed?", config_dir=config_dir)
            access_reply = _respond_to_free_text("how can i access whisper?", config_dir=config_dir)
            removal_reply = _respond_to_free_text("how do i remove whisper?", config_dir=config_dir)

            self.assertIn(str(project_dir), install_reply.text)
            self.assertIn(".venv/bin/python -m pip --version", access_reply.text)
            self.assertIn("Use the CLI", access_reply.text)
            self.assertIn("remove the project directory", removal_reply.text)

    def test_free_text_fallback_lists_setup_repos_and_active_repo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            store = initialize_state_store(config_dir)
            store.upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path=str(config_dir / "projects" / "whisper"),
                status="ready",
                summary="Whisper ready.",
                metadata={"repo_name": "whisper"},
            )
            store.upsert_repo_state(
                repo_key="https://example.com/open-webui",
                repo_url="https://example.com/open-webui",
                repo_path=str(config_dir / "projects" / "open-webui"),
                status="running",
                summary="open-webui running.",
                metadata={"repo_name": "open-webui"},
            )

            inventory_reply = _respond_to_free_text("which repos do i have setup in my system", config_dir=config_dir)
            active_reply = _respond_to_free_text("which repo is active", config_dir=config_dir)

            self.assertIn("whisper", inventory_reply.text.lower())
            self.assertIn("open-webui", inventory_reply.text.lower())
            self.assertIn("open-webui", active_reply.text.lower())
            self.assertIn("running", active_reply.text.lower())

    def test_free_text_fallback_answers_repo_inventory_and_status_for_natural_phrasing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            project_dir = config_dir / "projects" / "whisper"
            store = initialize_state_store(config_dir)
            store.upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path=str(project_dir),
                status="ready",
                summary="Whisper ready.",
                metadata={"repo_name": "whisper", "install_location": str(project_dir)},
            )
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            inventory_reply = _respond_to_free_text("do i have any repos setup right now in system", config_dir=config_dir)
            status_reply = _respond_to_free_text("is whisper setup in my system right now?", config_dir=config_dir)

            self.assertIn("whisper", inventory_reply.text.lower())
            self.assertIn("whisper", status_reply.text.lower())
            self.assertIn("ready", status_reply.text.lower())
            self.assertIn(str(project_dir), status_reply.text)

    def test_free_text_repo_path_answers_natural_location_phrase(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            project_dir = config_dir / "projects" / "whisper"
            initialize_state_store(config_dir).upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path=str(project_dir),
                status="ready",
                summary="Whisper ready.",
                metadata={"repo_name": "whisper", "install_location": str(project_dir)},
            )

            reply = _respond_to_free_text("what the location of whisper in my system?", config_dir=config_dir)

            self.assertEqual("repo_path_show", reply.intent)
            self.assertIn(str(project_dir), reply.text)

    def test_free_text_repo_path_inventory_answers_all_tracked_locations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            llama_path = config_dir / "projects" / "llama.cpp"
            whisper_path = config_dir / "projects" / "whisper"
            store = initialize_state_store(config_dir)
            store.upsert_repo_state(
                repo_key="https://example.com/llama.cpp",
                repo_url="https://example.com/llama.cpp",
                repo_path=str(llama_path),
                status="selected",
                summary="llama.cpp selected.",
                metadata={"repo_name": "llama.cpp", "install_location": str(llama_path)},
            )
            store.upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path=str(whisper_path),
                status="ready",
                summary="Whisper ready.",
                metadata={"repo_name": "whisper", "install_location": str(whisper_path)},
            )

            reply = _respond_to_free_text("can you show me location of all the repos that is setup in environment", config_dir=config_dir)

            self.assertEqual("repo_path_inventory", reply.intent)
            self.assertIn(str(llama_path), reply.text)
            self.assertIn(str(whisper_path), reply.text)
            self.assertIn("tracked repo paths", reply.text.lower())

    def test_free_text_inventory_handles_transcript_like_phrase(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            store = initialize_state_store(config_dir)
            store.upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path=str(config_dir / "projects" / "whisper"),
                status="ready",
                summary="Whisper ready.",
                metadata={"repo_name": "whisper"},
            )

            reply = _respond_to_free_text("do we have any repos that we have setup", config_dir=config_dir)

            self.assertIn(reply.intent, {"repo_inventory", "repo_lifecycle_inventory"})
            self.assertIn("whisper", reply.text.lower())
            self.assertNotIn("not quite on the target", reply.text.lower())

    def test_free_text_repo_lifecycle_questions_ignore_stale_recommendation_thread(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            project_dir = config_dir / "projects" / "whisper"
            initialize_state_store(config_dir).upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path=str(project_dir),
                status="ready",
                summary="Whisper ready.",
                metadata={"repo_name": "whisper", "install_location": str(project_dir)},
            )
            write_followup_state(
                config_dir,
                {
                    "active_topic": "repo_recommendation_single",
                    "last_route_family": "repo_recommendation_single",
                    "pending_offer_kind": "recommend_best_fit",
                    "pending_offer_id": "offer:1",
                    "pending_offer_thread_id": "thread:1",
                    "active_thread_id": "thread:1",
                    "pending_offer_repo_name": "private-gpt",
                    "last_discussed_repo_name": "private-gpt",
                    "shortlist_primary_repo": "private-gpt",
                    "shortlist_secondary_repo": "fastchat",
                },
            )
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-11",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 10,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            inventory_reply = _respond_to_free_text(
                "can you show me all repos that we have installed or active?",
                config_dir=config_dir,
            )
            active_inventory_reply = _respond_to_free_text(
                "can you tell me active repos in duckln",
                config_dir=config_dir,
            )
            active_reply = _respond_to_free_text(
                "is there a active repo which we worked previously?",
                config_dir=config_dir,
            )
            status_reply = _respond_to_free_text(
                "Do we have whisper installed and active",
                config_dir=config_dir,
            )
            bare_status_reply = _respond_to_free_text(
                "do wehave whisper",
                config_dir=config_dir,
            )

            self.assertIn("whisper", inventory_reply.text.lower())
            self.assertIn(active_inventory_reply.intent, {"repo_inventory", "repo_lifecycle_inventory"})
            self.assertIn("whisper", active_inventory_reply.text.lower())
            self.assertNotIn("full shortlist", active_inventory_reply.text.lower())
            self.assertIn("installed, active, or otherwise prepared", inventory_reply.text.lower())
            self.assertNotIn("private-gpt is first", inventory_reply.text.lower())
            self.assertNotIn("full shortlist", inventory_reply.text.lower())
            self.assertIn(active_reply.intent, {"repo_lifecycle_active", "repo_active"})
            self.assertIn("current active repo context is whisper", active_reply.text.lower())
            self.assertIn("no live running repo session", active_reply.text.lower())
            self.assertNotIn("full shortlist", active_reply.text.lower())
            self.assertIn(status_reply.intent, {"repo_lifecycle_specific_status", "repo_status"})
            self.assertIn("currently has whisper tracked as ready", status_reply.text.lower())
            self.assertIn("whisper", status_reply.text.lower())
            self.assertIn("ready", status_reply.text.lower())
            self.assertNotIn("private-gpt", status_reply.text.lower())
            self.assertIn(bare_status_reply.intent, {"repo_lifecycle_specific_status", "repo_status"})
            self.assertIn("whisper", bare_status_reply.text.lower())
            self.assertNotIn("full shortlist", bare_status_reply.text.lower())

    def test_free_text_active_repos_prefers_live_runtime_meaning_over_tracked_ready_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            initialize_state_store(config_dir).upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path=str(config_dir / "projects" / "whisper"),
                status="ready",
                summary="Whisper ready.",
                metadata={"repo_name": "whisper"},
            )

            reply = _respond_to_free_text("can you show me active repos", config_dir=config_dir)

            self.assertIn(reply.intent, {"repo_lifecycle_inventory", "repo_inventory"})
            self.assertIn("whisper", reply.text.lower())

    def test_free_text_active_repos_handles_reordered_phrase(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            initialize_state_store(config_dir).upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path=str(config_dir / "projects" / "whisper"),
                status="ready",
                summary="Whisper ready.",
                metadata={"repo_name": "whisper"},
            )

            reply = _respond_to_free_text("which repos we have active", config_dir=config_dir)

            self.assertIn(reply.intent, {"repo_lifecycle_inventory", "repo_inventory"})
            self.assertIn("whisper", reply.text.lower())

    def test_free_text_plural_active_repos_inventory_lists_multiple_tracked_repos(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            store = initialize_state_store(config_dir)
            store.upsert_repo_state(
                repo_key="https://github.com/ggerganov/llama.cpp",
                repo_url="https://github.com/ggerganov/llama.cpp",
                repo_path=str(config_dir / "projects" / "llama.cpp"),
                status="selected",
                summary="llama.cpp selected.",
                metadata={"repo_name": "llama.cpp"},
            )
            store.upsert_repo_state(
                repo_key="https://github.com/openai/whisper",
                repo_url="https://github.com/openai/whisper",
                repo_path=str(config_dir / "projects" / "whisper"),
                status="ready",
                summary="Whisper ready.",
                metadata={"repo_name": "whisper"},
            )

            reply = _respond_to_free_text("can you show me all active repos you tracked", config_dir=config_dir)

            self.assertIn(reply.intent, {"repo_lifecycle_inventory", "repo_inventory"})
            self.assertIn("llama.cpp", reply.text.lower())
            self.assertIn("whisper", reply.text.lower())
            self.assertNotIn("current selected repo context is llama.cpp", reply.text.lower())

    def test_free_text_repo_source_answers_followup_github_path_phrase(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            initialize_state_store(config_dir).upsert_repo_state(
                repo_key="https://github.com/openai/whisper",
                repo_url="https://github.com/openai/whisper",
                repo_path=str(config_dir / "projects" / "whisper"),
                status="ready",
                summary="Whisper ready.",
                metadata={"repo_name": "whisper"},
            )

            _respond_to_free_text("which repos we have active", config_dir=config_dir)
            reply = _respond_to_free_text("can you give me its github path", config_dir=config_dir)

            self.assertEqual("repo_source_show", reply.intent)
            self.assertIn("https://github.com/openai/whisper", reply.text)

    def test_free_text_repo_path_answers_followup_possessive_path_phrase(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            project_dir = config_dir / "projects" / "whisper"
            initialize_state_store(config_dir).upsert_repo_state(
                repo_key="https://github.com/openai/whisper",
                repo_url="https://github.com/openai/whisper",
                repo_path=str(project_dir),
                status="ready",
                summary="Whisper ready.",
                metadata={"repo_name": "whisper", "install_location": str(project_dir)},
            )

            _respond_to_free_text("which repo is active", config_dir=config_dir)
            reply = _respond_to_free_text("can you show me its path", config_dir=config_dir)

            self.assertEqual("repo_path_show", reply.intent)
            self.assertIn(str(project_dir), reply.text)

    def test_free_text_repo_source_inventory_answers_all_tracked_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            store = initialize_state_store(config_dir)
            store.upsert_repo_state(
                repo_key="https://github.com/ggerganov/llama.cpp",
                repo_url="https://github.com/ggerganov/llama.cpp",
                repo_path=str(config_dir / "projects" / "llama.cpp"),
                status="selected",
                summary="llama.cpp selected.",
                metadata={
                    "repo_name": "llama.cpp",
                    "source_repo_url": "https://github.com/ggerganov/llama.cpp",
                },
            )
            store.upsert_repo_state(
                repo_key="https://github.com/openai/whisper",
                repo_url="https://github.com/openai/whisper",
                repo_path=str(config_dir / "projects" / "whisper"),
                status="ready",
                summary="Whisper ready.",
                metadata={
                    "repo_name": "whisper",
                    "source_repo_url": "https://github.com/openai/whisper",
                },
            )

            reply = _respond_to_free_text("can you show me all github links for tracked repos", config_dir=config_dir)

            self.assertEqual("repo_source_inventory", reply.intent)
            self.assertIn("https://github.com/ggerganov/llama.cpp", reply.text)
            self.assertIn("https://github.com/openai/whisper", reply.text)

    def test_free_text_repo_source_answers_explicit_repo_source_question(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            initialize_state_store(config_dir).upsert_repo_state(
                repo_key="https://github.com/Significant-Gravitas/AutoGPT",
                repo_url="https://github.com/Significant-Gravitas/AutoGPT",
                repo_path=str(config_dir / "projects" / "AutoGPT"),
                status="ready",
                summary="AutoGPT ready.",
                metadata={"repo_name": "AutoGPT"},
            )

            reply = _respond_to_free_text("show me the github link for autogpt", config_dir=config_dir)

            self.assertEqual("repo_source_show", reply.intent)
            self.assertIn("https://github.com/Significant-Gravitas/AutoGPT", reply.text)

    def test_free_text_repo_source_handles_linked_repo_without_recorded_source_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            linked_path = config_dir / "linked" / "custom-repo"
            initialize_state_store(config_dir).upsert_repo_state(
                repo_key=f"linked://local/{linked_path}",
                repo_url=f"linked://local/{linked_path}",
                repo_path=str(linked_path),
                status="linked",
                summary="Custom repo linked.",
                metadata={"repo_name": "custom-repo"},
            )

            reply = _respond_to_free_text("what is custom-repo github path", config_dir=config_dir)

            self.assertEqual("repo_source_show", reply.intent)
            self.assertIn("does not have a public source repo url recorded", reply.text.lower())

    def test_free_text_what_about_repo_name_resolves_to_specific_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            store = initialize_state_store(config_dir)
            store.upsert_repo_state(
                repo_key="https://github.com/ggerganov/llama.cpp",
                repo_url="https://github.com/ggerganov/llama.cpp",
                repo_path=str(config_dir / "projects" / "llama.cpp"),
                status="selected",
                summary="llama.cpp selected.",
                metadata={"repo_name": "llama.cpp"},
            )
            store.upsert_repo_state(
                repo_key="https://github.com/openai/whisper",
                repo_url="https://github.com/openai/whisper",
                repo_path=str(config_dir / "projects" / "whisper"),
                status="ready",
                summary="Whisper ready.",
                metadata={"repo_name": "whisper"},
            )

            inventory_reply = _respond_to_free_text("can you show me all active repos you tracked", config_dir=config_dir)
            reply = _respond_to_free_text(
                "what about whisper",
                config_dir=config_dir,
                recent_turns=(
                    ConversationTurn(role="user", content="can you show me all active repos you tracked"),
                    ConversationTurn(role="assistant", content=inventory_reply.text),
                ),
                recent_replies=(inventory_reply,),
            )

            self.assertIn(reply.intent, {"repo_lifecycle_specific_status", "repo_status"})
            self.assertIn("whisper", reply.text.lower())
            self.assertIn("ready", reply.text.lower())

    def test_free_text_repo_verification_request_prefers_live_check_over_tracked_ready_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            store = initialize_state_store(config_dir)
            store.upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path=str(config_dir / "projects" / "whisper"),
                status="ready",
                summary="Whisper ready.",
                metadata={
                    "repo_name": "whisper",
                    "install_location": str(config_dir / "projects" / "whisper"),
                    "run_command": ".venv/bin/python -m whisper --help",
                    "last_run_result": "passed",
                },
            )
            write_workflow_state(
                config_dir,
                {
                    "active_repo_key": "https://example.com/whisper",
                    "active_repo_name": "whisper",
                },
            )

            reply = _respond_to_free_text(
                "can you check if its ready and is error free to run?",
                config_dir=config_dir,
            )

            self.assertEqual("repo_verify", reply.intent)
            self.assertEqual("verify_repo", reply.action)
            self.assertIn("bounded live check", reply.text.lower())
            self.assertIn("instead of only trusting tracked state", reply.text.lower())
            self.assertNotIn("private-gpt", reply.text.lower())

    def test_free_text_system_verification_request_routes_to_healthcheck_not_shortlist(self) -> None:
        reply = _respond_to_free_text("Can you check if Duckln is erro free")

        self.assertEqual("system_verify", reply.intent)
        self.assertEqual("/healthcheck", reply.steer)
        self.assertIn("/healthcheck", reply.text.lower())
        self.assertNotIn("full shortlist", reply.text.lower())
        self.assertNotIn("second-best repo", reply.text.lower())

    def test_free_text_social_identity_question_is_not_hijacked_by_repo_thread(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            store = initialize_state_store(config_dir)
            store.upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path=str(config_dir / "projects" / "whisper"),
                status="ready",
                summary="Whisper ready.",
                metadata={"repo_name": "whisper"},
            )

            reply = _respond_to_free_text(
                "Do yu kno who i am",
                config_dir=config_dir,
                recent_turns=(
                    ConversationTurn(role="user", content="tell me about whisper"),
                    ConversationTurn(role="assistant", content="Whisper is the active repo here."),
                ),
            )

            self.assertEqual("user_identity_meta", reply.intent)
            self.assertIn("not unless", reply.text.lower())
            self.assertNotIn("we’re already talking", reply.text.lower())
            self.assertNotIn("whisper", reply.text.lower())

    def test_free_text_alias_update_intent_is_detected(self) -> None:
        reply = _respond_to_free_text("I am X151")

        self.assertEqual("user_alias_set", reply.intent)
        self.assertEqual("update_user_alias", reply.action)
        self.assertEqual("X151", reply.user_alias)

    def test_free_text_alias_update_handles_can_you_call_me_as(self) -> None:
        reply = _respond_to_free_text("can you call me as X151")

        self.assertEqual("user_alias_set", reply.intent)
        self.assertEqual("update_user_alias", reply.action)
        self.assertEqual("X151", reply.user_alias)

    def test_free_text_active_repo_explanation_answers_directly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            store = initialize_state_store(config_dir)
            store.upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path=str(config_dir / "projects" / "whisper"),
                status="ready",
                summary="Whisper ready.",
                metadata={"repo_name": "whisper"},
            )

            reply = _respond_to_free_text("what do you mean you still have whisper as active repo?", config_dir=config_dir)

            self.assertEqual("repo_active_explanation", reply.intent)
            self.assertIn("active repo just means", reply.text.lower())
            self.assertNotIn("what Duckln remembers about it", reply.text)

    def test_free_text_conversation_repair_plain_elaborate_does_not_snap_back_to_repo_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            store = initialize_state_store(config_dir)
            store.upsert_repo_state(
                repo_key="https://example.com/llama.cpp",
                repo_url="https://example.com/llama.cpp",
                repo_path=str(config_dir / "projects" / "llama.cpp"),
                status="selected",
                summary="llama.cpp selected.",
                metadata={"repo_name": "llama.cpp"},
            )
            earlier = _respond_to_free_text("what do you mean active repo", config_dir=config_dir)

            reply = _respond_to_free_text(
                "can you elaborate",
                config_dir=config_dir,
                recent_turns=(
                    ConversationTurn(role="user", content="what do you mean active repo"),
                    ConversationTurn(role="assistant", content=earlier.text),
                ),
                recent_replies=(earlier,),
            )

            self.assertEqual("conversation_repair", reply.intent)
            self.assertNotIn("full shortlist", reply.text.lower())
            self.assertNotIn("next step for llama.cpp", reply.text.lower())

    def test_free_text_next_step_guidance_does_not_fall_back_to_active_repo_loop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            store = initialize_state_store(config_dir)
            store.upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path=str(config_dir / "projects" / "whisper"),
                status="ready",
                summary="Whisper ready.",
                metadata={"repo_name": "whisper"},
            )

            reply = _respond_to_free_text("tell me what to do next", config_dir=config_dir)

            self.assertEqual("next_step_guidance", reply.intent)
            self.assertIn("next", reply.text.lower())
            self.assertNotIn("i still have whisper as the active repo", reply.text.lower())

    def test_free_text_recommendation_question_after_run_issue_stays_on_current_repo_issue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            },
                            {
                                "name": "private-gpt",
                                "repo_url": "https://example.com/private-gpt",
                                "stars": 50000,
                                "description": "Private LLM docs app.",
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
            store = initialize_state_store(config_dir)
            project_dir = config_dir / "projects" / "whisper"
            store.upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path=str(project_dir),
                status="failed",
                summary="Supervisor agent saw a run issue for whisper.",
                metadata={
                    "repo_name": "whisper",
                    "install_location": str(project_dir),
                    "run_command": ".venv/bin/python -m whisper --help",
                    "last_run_result": "failed",
                    "last_blocker": "Supervisor agent saw a run issue for whisper.",
                },
            )
            write_workflow_state(
                config_dir,
                {
                    "active_repo_key": "https://example.com/whisper",
                    "active_repo_name": "whisper",
                    "active_issue_kind": "run_issue",
                    "active_issue_summary": "Supervisor agent saw a run issue for whisper.",
                },
            )

            reply = _respond_to_free_text("so what do you recommend?", config_dir=config_dir)

            self.assertEqual("next_step_guidance", reply.intent)
            self.assertIn("whisper", reply.text.lower())
            self.assertIn("run issue", reply.text.lower())
            self.assertNotIn("private-gpt", reply.text.lower())

    def test_free_text_run_pronoun_after_workflow_issue_resolves_to_workflow_repo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            store = initialize_state_store(config_dir)
            project_dir = config_dir / "projects" / "whisper"
            store.upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path=str(project_dir),
                status="failed",
                summary="Supervisor agent saw a run issue for whisper.",
                metadata={
                    "repo_name": "whisper",
                    "install_location": str(project_dir),
                    "run_command": ".venv/bin/python -m whisper --help",
                    "last_run_result": "failed",
                    "last_blocker": "Supervisor agent saw a run issue for whisper.",
                },
            )
            write_workflow_state(
                config_dir,
                {
                    "active_repo_key": "https://example.com/whisper",
                    "active_repo_name": "whisper",
                    "active_issue_kind": "run_issue",
                    "active_issue_summary": "Supervisor agent saw a run issue for whisper.",
                },
            )

            reply = _respond_to_free_text("run it", config_dir=config_dir)

            self.assertEqual("run_repo", reply.action)
            self.assertEqual("https://example.com/whisper", reply.action_repo_key)
            self.assertIn("whisper", reply.text.lower())

    def test_free_text_remove_typo_with_path_after_workflow_issue_resolves_to_workflow_repo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            store = initialize_state_store(config_dir)
            project_dir = config_dir / "projects" / "whisper"
            store.upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path=str(project_dir),
                status="ready",
                summary="Whisper ready.",
                metadata={
                    "repo_name": "whisper",
                    "install_location": str(project_dir),
                    "removal_hint": f"remove the project directory at {project_dir}",
                },
            )
            write_workflow_state(
                config_dir,
                {
                    "active_repo_key": "https://example.com/whisper",
                    "active_repo_name": "whisper",
                    "pending_destructive_action": "remove_repo",
                    "pending_destructive_repo_key": "https://example.com/whisper",
                    "pending_destructive_repo_name": "whisper",
                    "pending_destructive_path": str(project_dir),
                    "pending_destructive_target": "local machine",
                },
            )

            reply = _respond_to_free_text(f"can you help uninstall whiper {project_dir}", config_dir=config_dir)

            self.assertEqual("remove_repo", reply.action)
            self.assertEqual("https://example.com/whisper", reply.action_repo_key)
            self.assertIn("whisper", reply.text.lower())

    def test_free_text_path_pronoun_after_workflow_issue_resolves_to_workflow_repo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            store = initialize_state_store(config_dir)
            project_dir = config_dir / "projects" / "whisper"
            store.upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path=str(project_dir),
                status="ready",
                summary="Whisper ready.",
                metadata={
                    "repo_name": "whisper",
                    "install_location": str(project_dir),
                },
            )
            write_workflow_state(
                config_dir,
                {
                    "active_repo_key": "https://example.com/whisper",
                    "active_repo_name": "whisper",
                    "active_issue_kind": "run_issue",
                    "active_issue_summary": "Supervisor agent saw a run issue for whisper.",
                },
            )

            reply = _respond_to_free_text("where did you install it", config_dir=config_dir)

            self.assertEqual("repo_path_show", reply.intent)
            self.assertIn(str(project_dir), reply.text)

    def test_free_text_fallback_answers_repo_capability_coverage_without_recommending(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            },
                            {
                                "name": "open-webui",
                                "repo_url": "https://example.com/open-webui",
                                "stars": 129200,
                                "description": "A local model UI.",
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

            reply = _respond_to_free_text("which repos can you help me with", config_dir=config_dir)

            self.assertIn("whisper", reply.text.lower())
            self.assertIn("open-webui", reply.text.lower())
            self.assertNotIn("i’d start with", reply.text.lower())

    def test_free_text_fallback_answers_repo_capability_coverage_for_my_system_more_narrowly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            },
                            {
                                "name": "comfyui",
                                "repo_url": "https://example.com/comfyui",
                                "stars": 85000,
                                "description": "Diffusion UI.",
                                "category": "Stable Diffusion",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            },
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            reply = _respond_to_free_text(
                "which repos can you help me with my system",
                config_dir=config_dir,
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
            )

            self.assertTrue("realistic" in reply.text.lower() and ("starting points" in reply.text.lower() or "better first bets" in reply.text.lower()))
            self.assertIn("whisper", reply.text.lower())

    def test_free_text_followup_yes_after_capability_coverage_triggers_recommendation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            first = _respond_to_free_text("which repos can you help me with my system", config_dir=config_dir)
            second = _respond_to_free_text("yes please", config_dir=config_dir)

            self.assertEqual("repo_capability_coverage", first.intent)
            self.assertNotIn("more realistic better first bets", first.text.lower())
            self.assertTrue(second.intent.startswith("repo_recommendation"))
            self.assertIn("whisper", second.text.lower())

    def test_free_text_followup_yes_pease_continues_recommendation_offer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            _respond_to_free_text("which repos can you help me with on my system", config_dir=config_dir)
            reply = _respond_to_free_text("yes pease", config_dir=config_dir)

            self.assertTrue(reply.intent.startswith("repo_recommendation"))
            self.assertIn("whisper", reply.text.lower())

    def test_free_text_followup_pronoun_resolves_to_recommended_repo_requirements(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            store = initialize_state_store(config_dir)
            store.upsert_repo_knowledge(
                repo_key="https://example.com/whisper",
                repo_name="whisper",
                repo_url="https://example.com/whisper",
                source="remote",
                summary="whisper knowledge (remote). requirements: a few CPU cores; 8-16 GB RAM; GPU optional.",
                cpu_profile="a few CPU cores",
                ram_profile="8-16 GB RAM",
                gpu_profile="GPU optional",
                required_tools=("Python", "ffmpeg"),
            )

            _respond_to_free_text("what repo do you recommend", config_dir=config_dir)
            reply = _respond_to_free_text("yes please inspect its requirements", config_dir=config_dir)

            self.assertEqual("repo_requirements", reply.intent)
            self.assertIn("whisper", reply.text.lower())
            self.assertIn("ffmpeg", reply.text.lower())

    def test_free_text_followup_yes_after_recommendation_binds_pending_repo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            store = initialize_state_store(config_dir)
            store.upsert_repo_knowledge(
                repo_key="https://example.com/whisper",
                repo_name="whisper",
                repo_url="https://example.com/whisper",
                source="remote",
                summary="whisper knowledge (remote). requirements: a few CPU cores; 8-16 GB RAM; GPU optional.",
                cpu_profile="a few CPU cores",
                ram_profile="8-16 GB RAM",
                gpu_profile="GPU optional",
                required_tools=("Python", "ffmpeg"),
            )

            _respond_to_free_text("recommend one repo for my system", config_dir=config_dir)
            followup = _respond_to_free_text("yes please", config_dir=config_dir)

            self.assertEqual("repo_requirements", followup.intent)
            self.assertIn("whisper", followup.text.lower())
            self.assertNotIn("your system:", followup.text.lower())
            self.assertGreaterEqual(followup.route_confidence or 0.0, 0.78)

    def test_free_text_followup_yes_please_suggest_one_best_for_my_machine_continues_thread(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "open-webui",
                                "repo_url": "https://example.com/open-webui",
                                "stars": 120000,
                                "description": "Model UI.",
                                "category": "LLM",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            _respond_to_free_text("which repos can you help me with", config_dir=config_dir)
            followup = _respond_to_free_text("yes please suggest one best for my machine", config_dir=config_dir)

            self.assertTrue(followup.intent.startswith("repo_recommendation"))
            self.assertIn("open-webui", followup.text.lower())
            self.assertNotIn("i’m not quite on the target yet", followup.text.lower())

    def test_free_text_followup_yes_please_could_inspect_continues_thread(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            store = initialize_state_store(config_dir)
            store.upsert_repo_knowledge(
                repo_key="https://example.com/whisper",
                repo_name="whisper",
                repo_url="https://example.com/whisper",
                source="remote",
                summary="whisper knowledge (remote). requirements: a few CPU cores; 8-16 GB RAM; GPU optional.",
                cpu_profile="a few CPU cores",
                ram_profile="8-16 GB RAM",
                gpu_profile="GPU optional",
                required_tools=("Python", "ffmpeg"),
            )

            _respond_to_free_text("recommend me one", config_dir=config_dir)
            followup = _respond_to_free_text("yes please could inspect", config_dir=config_dir)

            self.assertEqual("repo_requirements", followup.intent)
            self.assertIn("whisper", followup.text.lower())
            self.assertIn("ffmpeg", followup.text.lower())

    def test_free_text_repo_overview_uses_active_repo_subject(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            store = initialize_state_store(config_dir)
            store.upsert_repo_state(
                repo_key="https://github.com/example/kokoro",
                repo_url="https://github.com/example/kokoro",
                repo_path=str(config_dir / "projects" / "kokoro"),
                status="ready",
                summary="kokoro ready.",
                metadata={"repo_name": "kokoro"},
            )
            store.upsert_repo_knowledge(
                repo_key="https://github.com/example/kokoro",
                repo_name="kokoro",
                repo_url="https://github.com/example/kokoro",
                source="remote",
                summary="kokoro knowledge (remote). requirements: Python; 8-16 GB RAM; GPU optional.",
                setup_complexity="moderate",
                required_tools=("Python",),
            )

            reply = _respond_to_free_text("can you give me an idea what kokoro is ?", config_dir=config_dir)

            self.assertEqual("repo_overview", reply.intent)
            self.assertIn("kokoro", reply.text.lower())
            self.assertNotIn("not quite on the target", reply.text.lower())

    def test_free_text_repo_overview_handles_explicit_private_gpt_question(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "private-gpt",
                                "repo_url": "https://example.com/private-gpt",
                                "stars": 100,
                                "description": "Interact with your documents using the power of GPT, 100% privately, no data leaks.",
                                "category": "LLM",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            reply = _respond_to_free_text("what is private-gpt", config_dir=config_dir)

            self.assertEqual("repo_overview", reply.intent)
            self.assertIn("private-gpt", reply.text.lower())
            self.assertNotIn("looks like interact", reply.text.lower())

    def test_free_text_repo_alternatives_can_answer_second_choice(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            store = initialize_state_store(config_dir)
            write_followup_state(
                config_dir,
                {
                    "pending_repo_key": "https://example.com/private-gpt",
                    "pending_repo_name": "private-gpt",
                    "last_discussed_repo_key": "https://example.com/private-gpt",
                    "last_discussed_repo_name": "private-gpt",
                    "last_recommendation_alternatives": ("text-generation-webui", "fastchat"),
                },
            )
            records = (
                RepoCatalogRecord("private-gpt", "https://example.com/private-gpt", 100, "Private docs", "LLM", "Python", "2026-04-01"),
                RepoCatalogRecord("text-generation-webui", "https://example.com/text-generation-webui", 90, "Text UI", "LLM", "Python", "2026-04-01"),
                RepoCatalogRecord("fastchat", "https://example.com/fastchat", 80, "Fast chat", "LLM", "Python", "2026-04-01"),
            )

            def fake_score(*_args, **_kwargs):
                from duckln.conversation_agent import RecommendationCandidate

                return (
                    RecommendationCandidate(records[0], 0.9, "it is the best overall fit", "memory will be tight", "heavier overall", "mps-capable", "workable_but_tight", "bounded estimate"),
                    RecommendationCandidate(records[1], 0.85, "it is the cleaner backup", "downloads will take space", "slightly less complete", "mps-capable", "comfortable", "bounded estimate"),
                    RecommendationCandidate(records[2], 0.8, "it can still work", "setup is fussier", "lower polish", "mps-capable", "comfortable", "bounded estimate"),
                )

            with patch("duckln.conversation_agent.load_sorted_local_repo_catalog", return_value=records), patch(
                "duckln.conversation_agent._score_recommendation_candidates",
                side_effect=fake_score,
            ):
                reply = _respond_to_free_text("is there a second choice that you could recommend from the repos?", config_dir=config_dir)

            self.assertEqual("repo_alternatives", reply.intent)
            self.assertIn("text-generation-webui", reply.text.lower())
            self.assertNotIn("private-gpt is the cleanest place to start", reply.text.lower())

    def test_free_text_repo_alternatives_handles_transcript_like_second_best_phrasing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            write_followup_state(
                config_dir,
                {
                    "pending_repo_key": "https://example.com/private-gpt",
                    "pending_repo_name": "private-gpt",
                    "last_discussed_repo_key": "https://example.com/private-gpt",
                    "last_discussed_repo_name": "private-gpt",
                    "last_recommendation_alternatives": ("text-generation-webui", "fastchat"),
                    "shortlist_repo_names": ("private-gpt", "text-generation-webui", "fastchat"),
                    "shortlist_primary_repo": "private-gpt",
                    "shortlist_secondary_repo": "text-generation-webui",
                    "active_topic": "repo_recommendation_single",
                },
            )
            records = (
                RepoCatalogRecord("private-gpt", "https://example.com/private-gpt", 100, "Private docs", "LLM", "Python", "2026-04-01"),
                RepoCatalogRecord("text-generation-webui", "https://example.com/text-generation-webui", 90, "Text UI", "LLM", "Python", "2026-04-01"),
                RepoCatalogRecord("fastchat", "https://example.com/fastchat", 80, "Fast chat", "LLM", "Python", "2026-04-01"),
            )

            def fake_score(*_args, **_kwargs):
                from duckln.conversation_agent import RecommendationCandidate

                return (
                    RecommendationCandidate(records[0], 0.9, "it is the best overall fit", "memory will be tight", "heavier overall", "mps-capable", "workable_but_tight", "bounded estimate"),
                    RecommendationCandidate(records[1], 0.85, "it is the cleaner backup", "downloads will take space", "slightly less complete", "mps-capable", "comfortable", "bounded estimate"),
                    RecommendationCandidate(records[2], 0.8, "it can still work", "setup is fussier", "lower polish", "mps-capable", "comfortable", "bounded estimate"),
                )

            with patch("duckln.conversation_agent.load_sorted_local_repo_catalog", return_value=records), patch(
                "duckln.conversation_agent._score_recommendation_candidates",
                side_effect=fake_score,
            ):
                reply = _respond_to_free_text("what the second best recommendation", config_dir=config_dir)

            self.assertEqual("repo_alternatives", reply.intent)
            self.assertIn("text-generation-webui", reply.text.lower())
            self.assertNotIn("private-gpt", reply.text.lower().split("the next best option is")[0])

    def test_free_text_repo_memory_meta_answers_repo_memory_question(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            store = initialize_state_store(config_dir)
            store.upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path=str(config_dir / "projects" / "whisper"),
                status="ready",
                summary="Whisper ready.",
                metadata={"repo_name": "whisper"},
            )
            store.upsert_repo_knowledge(
                repo_key="https://example.com/whisper",
                repo_name="whisper",
                repo_url="https://example.com/whisper",
                source="remote",
                summary="Whisper knowledge (remote). requirements: Python; ffmpeg.",
                setup_complexity="low",
                required_tools=("Python", "ffmpeg"),
            )

            reply = _respond_to_free_text("is whisper in your memory", config_dir=config_dir)

            self.assertEqual("repo_memory_meta", reply.intent)
            self.assertIn("whisper", reply.text.lower())
            self.assertNotIn("not fully sure", reply.text.lower())

    def test_free_text_repo_removal_uses_resolved_repo_without_generic_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            store = initialize_state_store(config_dir)
            project_dir = config_dir / "projects" / "whisper"
            store.upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path=str(project_dir),
                status="ready",
                summary="Whisper ready.",
                metadata={"repo_name": "whisper", "install_location": str(project_dir)},
            )

            reply = _respond_to_free_text("help me uninstall whisper", config_dir=config_dir)

            self.assertEqual("repo_removal", reply.intent)
            self.assertEqual("remove_repo", reply.action)
            self.assertEqual("https://example.com/whisper", reply.action_repo_key)
            self.assertIn("whisper", reply.text.lower())
            self.assertIn("remove", reply.text.lower())
            self.assertNotIn("name it directly", reply.text.lower())
            self.assertIn("tracked", reply.text.lower())

    def test_free_text_repo_removal_uses_durable_target_metadata_for_docker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            store = initialize_state_store(config_dir)
            store.upsert_repo_state(
                repo_key="https://example.com/open-webui",
                repo_url="https://example.com/open-webui",
                repo_path="/workspace/open-webui",
                execution_target="docker",
                status="interactive",
                summary="open-webui running in docker",
                metadata={
                    "repo_name": "open-webui",
                    "install_location": "/workspace/open-webui",
                    "docker_name": "open-webui-stack",
                },
            )

            reply = _respond_to_free_text("remove open-webui from docker", config_dir=config_dir)

            self.assertEqual("repo_removal", reply.intent)
            self.assertEqual("remove_repo", reply.action)
            self.assertIn("docker", reply.text.lower())
            self.assertIn("open-webui-stack", reply.text.lower())

    def test_free_text_repo_removal_uses_durable_target_metadata_for_cloud(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            store = initialize_state_store(config_dir)
            store.upsert_repo_state(
                repo_key="https://example.com/open-webui",
                repo_url="https://example.com/open-webui",
                repo_path="/home/duckln/open-webui",
                execution_target="gcp",
                status="ready",
                summary="open-webui ready on cloud",
                metadata={
                    "repo_name": "open-webui",
                    "install_location": "/home/duckln/open-webui",
                    "cloud_vendor": "GCP",
                    "cloud_region": "us-central1-a",
                },
            )

            reply = _respond_to_free_text("remove open-webui from cloud", config_dir=config_dir)

            self.assertEqual("repo_removal", reply.intent)
            self.assertEqual("remove_repo", reply.action)
            self.assertIn("gcp", reply.text.lower())
            self.assertIn("us-central1-a", reply.text.lower())

    def test_handle_repo_remove_action_requires_confirmation_and_can_delete_local_repo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            project_dir = paths.config_dir / "projects" / "whisper"
            project_dir.mkdir(parents=True, exist_ok=True)
            initialize_state_store(paths.config_dir).upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path=str(project_dir),
                execution_target="local",
                status="ready",
                summary="Whisper ready.",
                metadata={"repo_name": "whisper", "install_location": str(project_dir)},
            )
            reply = FreeTextReply(text="", intent="repo_removal", action="remove_repo", action_repo_key="https://example.com/whisper")
            current = AppConfig(provider=Provider.OPENAI, model="gpt-4o-mini", api_key="k", mode=ControlMode.HOTL)
            displayed: list[str] = []

            _handle_repo_remove_action(
                reply=reply,
                current=current,
                paths=paths,
                display_output=displayed.append,
                approve_prompt=lambda prompt: True,
            )

            self.assertFalse(project_dir.exists())
            self.assertIsNone(initialize_state_store(paths.config_dir).get_latest_repo_state())
            self.assertTrue(any("cleared the tracked state" in message.lower() for message in displayed))

    def test_handle_repo_remove_action_cancels_without_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            project_dir = paths.config_dir / "projects" / "whisper"
            project_dir.mkdir(parents=True, exist_ok=True)
            initialize_state_store(paths.config_dir).upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path=str(project_dir),
                execution_target="local",
                status="ready",
                summary="Whisper ready.",
                metadata={"repo_name": "whisper", "install_location": str(project_dir)},
            )
            reply = FreeTextReply(text="", intent="repo_removal", action="remove_repo", action_repo_key="https://example.com/whisper")
            current = AppConfig(provider=Provider.OPENAI, model="gpt-4o-mini", api_key="k", mode=ControlMode.HITL)
            displayed: list[str] = []

            _handle_repo_remove_action(
                reply=reply,
                current=current,
                paths=paths,
                display_output=displayed.append,
                approve_prompt=lambda prompt: False,
            )

            self.assertTrue(project_dir.exists())
            self.assertIsNotNone(initialize_state_store(paths.config_dir).get_latest_repo_state())
            self.assertTrue(any("cancelled removing whisper" in message.lower() for message in displayed))

    def test_handle_repo_remove_action_can_delete_cloud_repo_path(self) -> None:
        class FakeRunner:
            def __init__(self) -> None:
                self.commands: list[str] = []

            def run(self, command: str, *, timeout_seconds: float = 30.0, cwd: str | None = None, env=None):
                from duckln.shell import CommandResult

                self.commands.append(command)
                return CommandResult(
                    command=command,
                    exit_code=0,
                    stdout="",
                    stderr="",
                    timed_out=False,
                    duration_seconds=0.01,
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            remote_path = "~/.duckln/projects/whisper"
            store = initialize_state_store(paths.config_dir)
            store.upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path=remote_path,
                execution_target="gcp",
                status="ready",
                summary="Whisper ready.",
                metadata={
                    "repo_name": "whisper",
                    "install_location": remote_path,
                    "cloud_resource_key": "gcp:duckln-gcp:us-central1-a",
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
                },
            )
            reply = FreeTextReply(text="", intent="repo_removal", action="remove_repo", action_repo_key="https://example.com/whisper")
            current = AppConfig(provider=Provider.OPENAI, model="gpt-4o-mini", api_key="k", mode=ControlMode.HOTL)
            displayed: list[str] = []
            runner = FakeRunner()

            _handle_repo_remove_action(
                reply=reply,
                current=current,
                paths=paths,
                display_output=displayed.append,
                approve_prompt=lambda prompt: True,
                runner=runner,
            )

            self.assertIsNone(initialize_state_store(paths.config_dir).get_latest_repo_state())
            self.assertTrue(any("removed the GCP repo path".lower() in message.lower() for message in displayed))
            self.assertTrue(any("gcloud compute ssh duckln-gcp --zone us-central1-a --command" in command for command in runner.commands))
            self.assertTrue(any("rm -rf" in command and ".duckln/projects/whisper" in command for command in runner.commands))

    def test_handle_repo_remove_action_can_stop_docker_runtime_before_deleting_repo(self) -> None:
        class FakeRunner:
            def __init__(self) -> None:
                self.commands: list[tuple[str, str | None]] = []

            def run(self, command: str, *, timeout_seconds: float = 30.0, cwd: str | None = None, env=None):
                from duckln.shell import CommandResult

                self.commands.append((command, cwd))
                return CommandResult(
                    command=command,
                    exit_code=0,
                    stdout="",
                    stderr="",
                    timed_out=False,
                    duration_seconds=0.01,
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            project_dir = paths.config_dir / "projects" / "open-webui"
            project_dir.mkdir(parents=True, exist_ok=True)
            store = initialize_state_store(paths.config_dir)
            store.upsert_repo_state(
                repo_key="https://example.com/open-webui",
                repo_url="https://example.com/open-webui",
                repo_path=str(project_dir),
                execution_target="docker",
                status="interactive",
                summary="open-webui in docker",
                metadata={
                    "repo_name": "open-webui",
                    "install_location": str(project_dir),
                    "docker_name": "open-webui-stack",
                },
            )
            store.upsert_managed_resource(
                resource_key="docker:open-webui-stack",
                resource_kind="docker_runtime",
                provider="docker",
                display_name="open-webui-stack",
                execution_target="docker",
                install_root=str(project_dir),
                status="running",
                metadata={"stop_command": "docker compose down"},
            )
            reply = FreeTextReply(text="", intent="repo_removal", action="remove_repo", action_repo_key="https://example.com/open-webui")
            current = AppConfig(provider=Provider.OPENAI, model="gpt-4o-mini", api_key="k", mode=ControlMode.HOTL)
            displayed: list[str] = []
            runner = FakeRunner()

            _handle_repo_remove_action(
                reply=reply,
                current=current,
                paths=paths,
                display_output=displayed.append,
                approve_prompt=lambda prompt: True,
                runner=runner,
            )

            self.assertFalse(project_dir.exists())
            self.assertIsNone(initialize_state_store(paths.config_dir).get_latest_repo_state())
            self.assertTrue(any(command == "docker compose down" for command, _cwd in runner.commands))
            self.assertTrue(any("docker runtime" in message.lower() for message in displayed))

    def test_repos_slash_commands_show_tracked_state_live_sessions_and_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            initialize_state_store(paths.config_dir).upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path="/tmp/whisper",
                execution_target="local",
                status="ready",
                summary="Whisper ready.",
                metadata={"repo_name": "whisper", "install_location": "/tmp/whisper"},
            )
            initialize_state_store(paths.config_dir).upsert_repo_state(
                repo_key="https://example.com/open-webui",
                repo_url="https://example.com/open-webui",
                repo_path="/tmp/open-webui",
                execution_target="docker",
                status="interactive",
                summary="open-webui interactive",
                metadata={"repo_name": "open-webui", "docker_name": "open-webui-stack"},
            )
            current = AppConfig(provider=Provider.OPENAI, model="gpt-4o-mini", api_key="k", mode=ControlMode.HOTL)
            displayed: list[str] = []

            handle_session_command("/repos tracked", current, paths, display=displayed.append)
            handle_session_command("/repos active", current, paths, display=displayed.append)
            handle_session_command("/repos live", current, paths, display=displayed.append)
            handle_session_command("/repos path", current, paths, display=displayed.append)
            handle_session_command("/repo active", current, paths, display=displayed.append)
            handle_session_command("/repo live", current, paths, display=displayed.append)

            self.assertTrue(any("tracking these repos" in message.lower() for message in displayed))
            self.assertTrue(any("live repo session is open-webui" in message.lower() for message in displayed))
            self.assertTrue(any("tracking these live repo sessions" in message.lower() for message in displayed))
            self.assertTrue(any("/tmp/open-webui" in message for message in displayed))
            self.assertGreaterEqual(sum("tracking these live repo sessions" in message.lower() for message in displayed), 2)

    def test_repos_remove_command_can_select_and_remove_tracked_repo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            project_dir = paths.config_dir / "projects" / "whisper"
            project_dir.mkdir(parents=True, exist_ok=True)
            initialize_state_store(paths.config_dir).upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path=str(project_dir),
                execution_target="local",
                status="ready",
                summary="Whisper ready.",
                metadata={"repo_name": "whisper", "install_location": str(project_dir)},
            )
            current = AppConfig(provider=Provider.OPENAI, model="gpt-4o-mini", api_key="k", mode=ControlMode.HOTL)
            displayed: list[str] = []

            handle_session_command(
                "/repos remove",
                current,
                paths,
                select=lambda prompt, choices: choices[0],
                approve=lambda prompt: True,
                display=displayed.append,
            )

            self.assertFalse(project_dir.exists())
            self.assertIsNone(initialize_state_store(paths.config_dir).get_latest_repo_state())
            self.assertTrue(any("cleared the tracked state" in message.lower() for message in displayed))

    def test_free_text_repo_path_answers_from_tracked_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            project_dir = config_dir / "projects" / "whisper"
            initialize_state_store(config_dir).upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path=str(project_dir),
                execution_target="local",
                status="ready",
                summary="Whisper ready.",
                metadata={"repo_name": "whisper", "install_location": str(project_dir)},
            )

            reply = _respond_to_free_text("show me the repo path", config_dir=config_dir)

            self.assertEqual("repo_path_show", reply.intent)
            self.assertIn(str(project_dir), reply.text)
            self.assertIn("local machine", reply.text.lower())

    def test_free_text_vm_definition_and_capability_answer_directly(self) -> None:
        definition = _respond_to_free_text("Do you know what is VM?")
        capability = _respond_to_free_text("Do you have the skills deploy VM?")

        self.assertEqual("vm_definition", definition.intent)
        self.assertIn("multipass", definition.text.lower())
        self.assertNotIn("private-gpt", definition.text.lower())
        self.assertEqual("vm_capability", capability.intent)
        self.assertIn("create", capability.text.lower())
        self.assertIn("vm", capability.text.lower())
        self.assertNotIn("second-best repo", capability.text.lower())

    def test_free_text_vm_repo_recommendation_answers_in_vm_terms(self) -> None:
        response = _respond_to_free_text(
            "Which repo do yo recommend to put in VM?",
            system_probe=SystemProbe(
                operating_system="Darwin",
                architecture="arm64",
                cpu_logical_cores=8,
                ram_bytes=16 * 1024**3,
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
        )

        self.assertEqual("vm_repo_recommendation", response.intent)
        self.assertIn("vm", response.text.lower())
        self.assertIn("linux isolation", response.text.lower())
        self.assertNotIn("private-gpt is first", response.text.lower())

    def test_free_text_vm_and_local_repo_inventory_filter_by_target(self) -> None:
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
                repo_key="https://example.com/private-gpt",
                repo_url="https://example.com/private-gpt",
                repo_path="/home/ubuntu/private-gpt",
                execution_target="vm",
                vm_name="duckln-vm-1",
                status="ready",
                summary="private-gpt ready in vm",
                metadata={"repo_name": "private-gpt"},
            )

            local_reply = _respond_to_free_text("show me local repos", config_dir=config_dir)
            vm_reply = _respond_to_free_text("show me vm repos", config_dir=config_dir)

            self.assertEqual("repo_inventory_local", local_reply.intent)
            self.assertIn("whisper", local_reply.text.lower())
            self.assertNotIn("private-gpt", local_reply.text.lower())
            self.assertEqual("repo_inventory_vm", vm_reply.intent)
            self.assertIn("private-gpt", vm_reply.text.lower())
            self.assertNotIn("whisper", vm_reply.text.lower())

    def test_free_text_vm_repo_inventory_filters_by_vm_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            store = initialize_state_store(config_dir)
            store.upsert_repo_state(
                repo_key="https://example.com/openclaw",
                repo_url="https://example.com/openclaw",
                repo_path="/home/ubuntu/openclaw",
                execution_target="vm",
                vm_name="duckln-vm-200",
                status="ready",
                summary="openclaw ready in vm 200",
                metadata={"repo_name": "openclaw"},
            )
            store.upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path="/home/ubuntu/whisper",
                execution_target="vm",
                vm_name="duckln-vm-fi",
                status="ready",
                summary="whisper ready in other vm",
                metadata={"repo_name": "whisper"},
            )

            reply = _respond_to_free_text("show me all repos on duckln-vm-200", config_dir=config_dir)

            self.assertEqual("repo_inventory_vm", reply.intent)
            self.assertIn("openclaw", reply.text.lower())
            self.assertIn("duckln-vm-200", reply.text)
            self.assertNotIn("whisper", reply.text.lower())

    def test_free_text_run_repo_on_named_vm_resolves_without_broad_clarification(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            initialize_state_store(config_dir).upsert_repo_state(
                repo_key="https://example.com/openclaw",
                repo_url="https://example.com/openclaw",
                repo_path="/home/ubuntu/openclaw",
                execution_target="vm",
                vm_name="duckln-vm-200",
                status="ready",
                summary="openclaw ready",
                metadata={
                    "repo_name": "openclaw",
                    "run_command": "openclaw gateway --port 18789 --verbose",
                },
            )

            reply = _respond_to_free_text("run openclaw on duckln-vm-200", config_dir=config_dir)

            self.assertEqual("repo_run", reply.intent)
            self.assertEqual("run_repo", reply.action)
            self.assertIn("openclaw gateway", reply.text)

    def test_free_text_transcript_local_inventory_and_path_followup_stays_local_grounded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            local_path = config_dir / "projects" / "whisper"
            initialize_state_store(config_dir).upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path=str(local_path),
                execution_target="local",
                status="ready",
                summary="whisper ready locally",
                metadata={"repo_name": "whisper", "install_location": str(local_path)},
            )

            inventory_reply = _respond_to_free_text("which repos are ready on local right now", config_dir=config_dir)
            path_reply = _respond_to_free_text(
                "where is whisper installed locally",
                config_dir=config_dir,
                recent_turns=(
                    ConversationTurn(role="user", content="which repos are ready on local right now"),
                    ConversationTurn(role="assistant", content=inventory_reply.text),
                ),
                recent_replies=(inventory_reply,),
            )

            self.assertEqual("repo_inventory_local", inventory_reply.intent)
            self.assertIn("whisper", inventory_reply.text.lower())
            self.assertEqual("repo_path_show", path_reply.intent)
            self.assertIn(str(local_path), path_reply.text)
            self.assertIn("local machine", path_reply.text.lower())

    def test_free_text_transcript_vm_inventory_and_path_followup_stays_vm_grounded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            vm_path = "/home/ubuntu/private-gpt"
            initialize_state_store(config_dir).upsert_repo_state(
                repo_key="https://example.com/private-gpt",
                repo_url="https://example.com/private-gpt",
                repo_path=vm_path,
                execution_target="vm",
                vm_name="duckln-vm-1",
                status="ready",
                summary="private-gpt ready in vm",
                metadata={"repo_name": "private-gpt", "install_location": vm_path},
            )

            inventory_reply = _respond_to_free_text("what repos are in vm right now", config_dir=config_dir)
            path_reply = _respond_to_free_text(
                "where is private-gpt located",
                config_dir=config_dir,
                recent_turns=(
                    ConversationTurn(role="user", content="what repos are in vm right now"),
                    ConversationTurn(role="assistant", content=inventory_reply.text),
                ),
                recent_replies=(inventory_reply,),
            )

            self.assertEqual("repo_inventory_vm", inventory_reply.intent)
            self.assertIn("private-gpt", inventory_reply.text.lower())
            self.assertEqual("repo_path_show", path_reply.intent)
            self.assertIn(vm_path, path_reply.text)
            self.assertIn("vm duckln-vm-1", path_reply.text.lower())

    def test_free_text_transcript_cloud_inventory_and_path_followup_stays_cloud_grounded(self) -> None:
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
                },
            )

            inventory_reply = _respond_to_free_text("show me cloud repos that are active", config_dir=config_dir)
            path_reply = _respond_to_free_text(
                "where is open-webui installed",
                config_dir=config_dir,
                recent_turns=(
                    ConversationTurn(role="user", content="show me cloud repos that are active"),
                    ConversationTurn(role="assistant", content=inventory_reply.text),
                ),
                recent_replies=(inventory_reply,),
            )

            self.assertEqual("repo_inventory_cloud", inventory_reply.intent)
            self.assertIn("open-webui", inventory_reply.text.lower())
            self.assertEqual("repo_path_show", path_reply.intent)
            self.assertIn(cloud_path, path_reply.text)
            self.assertIn("gcp cloud target in us-central1-a", path_reply.text.lower())

    def test_free_text_transcript_docker_inventory_path_and_stop_follow_docker_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            docker_path = "/workspace/open-webui"
            initialize_state_store(config_dir).upsert_repo_state(
                repo_key="https://example.com/open-webui",
                repo_url="https://example.com/open-webui",
                repo_path=docker_path,
                execution_target="docker",
                status="interactive",
                summary="open-webui running in docker",
                metadata={
                    "repo_name": "open-webui",
                    "install_location": docker_path,
                    "docker_name": "open-webui-stack",
                    "run_command": "docker compose up",
                },
            )
            write_workflow_state(
                config_dir,
                {
                    "active_repo_key": "https://example.com/open-webui",
                    "active_repo_name": "open-webui",
                    "active_runtime_status": "interactive",
                    "active_runtime_repo_key": "https://example.com/open-webui",
                    "active_runtime_repo_name": "open-webui",
                    "active_runtime_execution_target": "docker",
                    "active_runtime_docker_name": "open-webui-stack",
                    "active_runtime_stop_hint": "Duckln can stop the tracked Docker runtime for open-webui when you ask.",
                },
            )

            inventory_reply = _respond_to_free_text("show me docker repos", config_dir=config_dir)
            path_reply = _respond_to_free_text("where is open-webui located", config_dir=config_dir)
            stop_reply = _respond_to_free_text("can you stop open-webui container", config_dir=config_dir)

            self.assertEqual("repo_inventory_docker", inventory_reply.intent)
            self.assertIn("open-webui", inventory_reply.text.lower())
            self.assertEqual("repo_path_show", path_reply.intent)
            self.assertIn(docker_path, path_reply.text)
            self.assertIn("docker container open-webui-stack", path_reply.text.lower())
            self.assertEqual("repo_stop", stop_reply.intent)
            self.assertEqual("stop_repo", stop_reply.action)
            self.assertIn("live runtime session", stop_reply.text.lower())

    def test_free_text_live_repo_sessions_variants_filter_by_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            store = initialize_state_store(config_dir)
            store.upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path="/tmp/whisper",
                execution_target="local",
                status="running",
                summary="whisper running locally",
                metadata={"repo_name": "whisper", "install_location": "/tmp/whisper"},
            )
            store.upsert_repo_state(
                repo_key="https://example.com/private-gpt",
                repo_url="https://example.com/private-gpt",
                repo_path="/home/ubuntu/private-gpt",
                execution_target="vm",
                vm_name="duckln-vm-1",
                status="running",
                summary="private-gpt running in vm",
                metadata={"repo_name": "private-gpt", "install_location": "/home/ubuntu/private-gpt"},
            )
            store.upsert_repo_state(
                repo_key="https://example.com/open-webui",
                repo_url="https://example.com/open-webui",
                repo_path="/workspace/open-webui",
                execution_target="docker",
                status="interactive",
                summary="open-webui interactive in docker",
                metadata={"repo_name": "open-webui", "docker_name": "open-webui-stack"},
            )
            store.upsert_repo_state(
                repo_key="https://example.com/comfyui",
                repo_url="https://example.com/comfyui",
                repo_path="/home/duckln/comfyui",
                execution_target="gcp",
                status="running",
                summary="comfyui running in cloud",
                metadata={"repo_name": "comfyui", "cloud_vendor": "GCP", "cloud_region": "us-central1-a"},
            )

            generic_reply = _respond_to_free_text("show me live repo sessions", config_dir=config_dir)
            local_reply = _respond_to_free_text("show me live repos on local", config_dir=config_dir)
            vm_reply = _respond_to_free_text("show me live repo sessions on vm", config_dir=config_dir)
            docker_reply = _respond_to_free_text("show me live repos in docker", config_dir=config_dir)
            cloud_reply = _respond_to_free_text("show me actually running repos on cloud", config_dir=config_dir)

            self.assertEqual("repo_live_sessions", generic_reply.intent)
            self.assertIn("whisper", generic_reply.text.lower())
            self.assertIn("private-gpt", generic_reply.text.lower())
            self.assertEqual("repo_live_sessions_local", local_reply.intent)
            self.assertIn("whisper", local_reply.text.lower())
            self.assertNotIn("private-gpt", local_reply.text.lower())
            self.assertEqual("repo_live_sessions_vm", vm_reply.intent)
            self.assertIn("private-gpt", vm_reply.text.lower())
            self.assertNotIn("whisper", vm_reply.text.lower())
            self.assertEqual("repo_live_sessions_docker", docker_reply.intent)
            self.assertIn("open-webui", docker_reply.text.lower())
            self.assertEqual("repo_live_sessions_cloud", cloud_reply.intent)
            self.assertIn("comfyui", cloud_reply.text.lower())

    def test_free_text_live_repo_sessions_for_custom_linked_repo_follow_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            linked_path = config_dir / "linked" / "custom-alpha"
            linked_key = f"linked://local/{linked_path}"
            initialize_state_store(config_dir).upsert_repo_state(
                repo_key=linked_key,
                repo_url="https://github.com/example/custom-alpha",
                repo_path=str(linked_path),
                execution_target="local",
                status="running",
                summary="custom-alpha running locally",
                managed_by_duckln=False,
                metadata={
                    "repo_name": "custom-alpha",
                    "install_location": str(linked_path),
                    "source_repo_url": "https://github.com/example/custom-alpha",
                },
            )

            sessions_reply = _respond_to_free_text("show me live repo sessions", config_dir=config_dir)
            source_reply = _respond_to_free_text(
                "what is its github path",
                config_dir=config_dir,
                recent_turns=(
                    ConversationTurn(role="user", content="show me live repo sessions"),
                    ConversationTurn(role="assistant", content=sessions_reply.text),
                ),
                recent_replies=(sessions_reply,),
            )

            self.assertEqual("repo_live_sessions", sessions_reply.intent)
            self.assertIn("custom-alpha", sessions_reply.text.lower())
            self.assertEqual("repo_source_show", source_reply.intent)
            self.assertIn("https://github.com/example/custom-alpha", source_reply.text)

    def test_free_text_transcript_custom_linked_repo_source_and_run_guidance_follow_state(self) -> None:
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
                },
            )

            status_reply = _respond_to_free_text("do we have custom-alpha repo setup", config_dir=config_dir)
            source_reply = _respond_to_free_text(
                "can you give me its github link",
                config_dir=config_dir,
                recent_turns=(
                    ConversationTurn(role="user", content="do we have custom-alpha repo setup"),
                    ConversationTurn(role="assistant", content=status_reply.text),
                ),
                recent_replies=(status_reply,),
            )
            run_reply = _respond_to_free_text("can you run custom-alpha", config_dir=config_dir)

            self.assertIn(status_reply.intent, {"repo_lifecycle_specific_status", "repo_status"})
            self.assertIn("custom-alpha", status_reply.text.lower())
            self.assertEqual("repo_source_show", source_reply.intent)
            self.assertIn("https://github.com/example/custom-alpha", source_reply.text)
            self.assertEqual("repo_run", run_reply.intent)
            self.assertEqual("run_repo", run_reply.action)
            self.assertIn("does not have a verified run command", run_reply.text.lower())

    def test_free_text_transcript_local_repo_access_is_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            repo_path = config_dir / "projects" / "custom-alpha"
            initialize_state_store(config_dir).upsert_repo_state(
                repo_key="https://example.com/custom-alpha",
                repo_url="https://example.com/custom-alpha",
                repo_path=str(repo_path),
                execution_target="local",
                status="ready",
                summary="custom-alpha ready",
                metadata={
                    "repo_name": "custom-alpha",
                    "install_location": str(repo_path),
                    "access_hint": "Use the managed project directory for the next shell step.",
                },
            )

            reply = _respond_to_free_text("how can i access custom-alpha", config_dir=config_dir)

            self.assertEqual("repo_access", reply.intent)
            self.assertEqual("attach_repo", reply.action)
            self.assertIn("open custom-alpha", reply.text.lower())

    def test_free_text_transcript_docker_repo_access_is_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            initialize_state_store(config_dir).upsert_repo_state(
                repo_key="https://example.com/open-webui",
                repo_url="https://example.com/open-webui",
                repo_path="/workspace/open-webui",
                execution_target="docker",
                status="running",
                summary="open-webui running in docker",
                metadata={
                    "repo_name": "open-webui",
                    "install_location": "/workspace/open-webui",
                    "docker_name": "open-webui-stack",
                    "access_hint": "Use Duckln’s terminal pane for the Docker shell.",
                },
            )

            reply = _respond_to_free_text("attach to open-webui", config_dir=config_dir)

            self.assertEqual("repo_access", reply.intent)
            self.assertEqual("attach_repo", reply.action)
            self.assertIn("docker", reply.text.lower())

    def test_free_text_transcript_cloud_repo_access_is_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            initialize_state_store(config_dir).upsert_repo_state(
                repo_key="https://example.com/open-webui",
                repo_url="https://example.com/open-webui",
                repo_path="~/.duckln/projects/open-webui",
                execution_target="gcp",
                status="ready",
                summary="open-webui ready in cloud",
                metadata={
                    "repo_name": "open-webui",
                    "install_location": "~/.duckln/projects/open-webui",
                    "cloud_vendor": "GCP",
                    "cloud_region": "us-central1-a",
                    "cloud_resource_key": "gcp:duckln-gcp:us-central1-a",
                },
            )

            reply = _respond_to_free_text("open shell in open-webui", config_dir=config_dir)

            self.assertEqual("repo_access", reply.intent)
            self.assertEqual("attach_repo", reply.action)
            self.assertIn("cloud target", reply.text.lower())

    def test_free_text_public_github_repo_url_registers_recent_custom_repo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            record = RepoCatalogRecord(
                name="custom-alpha",
                repo_url="https://github.com/example/custom-alpha",
                stars=42,
                description="Custom alpha repo.",
                category="LLM",
                framework="Python",
                last_updated="2026-04-01",
            )
            with patch("duckln.conversation_agent.resolve_public_github_repo_record", return_value=record):
                reply = _respond_to_free_text(
                    "what is https://github.com/example/custom-alpha",
                    config_dir=config_dir,
                )

            recent = initialize_state_store(config_dir).list_recent_custom_repos()
            self.assertEqual("repo_overview", reply.intent)
            self.assertIn("custom-alpha", reply.text.lower())
            self.assertEqual(1, len(recent))
            self.assertEqual("https://github.com/example/custom-alpha", recent[0].repo_url)

    def test_repos_link_tracks_existing_repo_with_manual_run_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            repo_dir = Path(temp_dir) / "linked-alpha"
            (repo_dir / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
            (repo_dir / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
            (repo_dir / "requirements.txt").write_text("requests\n", encoding="utf-8")
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            displayed: list[str] = []

            def text_prompt(prompt: str, default: str = "") -> str | None:
                if prompt.startswith("Enter the existing repo path"):
                    return str(repo_dir)
                if prompt.startswith("Optional: paste the public repo URL"):
                    return "https://github.com/example/linked-alpha"
                if prompt.startswith("Repo name to display"):
                    return "linked-alpha"
                return default

            updated = handle_session_command(
                "/repos link",
                current,
                paths,
                text_prompt=text_prompt,
                display=displayed.append,
            )

            self.assertEqual(current, updated)
            latest = initialize_state_store(paths.config_dir).get_latest_repo_state()
            self.assertIsNotNone(latest)
            assert latest is not None
            self.assertFalse(latest.managed_by_duckln)
            self.assertEqual(str(repo_dir.resolve()), latest.repo_path)
            self.assertEqual("linked-alpha", latest.metadata["repo_name"])
            self.assertEqual("manual_link", latest.metadata["origin_kind"])
            self.assertIsNone(latest.metadata["run_command"])
            self.assertEqual(".venv/bin/python -m pip --version", latest.metadata["verify_command"])
            self.assertTrue(any("could not infer a safe manual run command" in message.lower() for message in displayed))

            reply = _respond_to_free_text("run this repo linked-alpha", config_dir=paths.config_dir)

            self.assertEqual("run_repo", reply.action)
            self.assertIn("does not have a verified run command", reply.text.lower())

    def test_repos_link_tracks_remote_cloud_repo_path_without_local_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            write_config_snapshot(paths.config_dir, {"execution_target": "gcp"})
            initialize_state_store(paths.config_dir).upsert_managed_resource(
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
            displayed: list[str] = []

            def text_prompt(prompt: str, default: str = "") -> str | None:
                if prompt.startswith("Enter the existing repo path"):
                    return "~/.duckln/projects/remote-alpha"
                if prompt.startswith("Optional: paste the public repo URL"):
                    return "https://github.com/example/remote-alpha"
                if prompt.startswith("Repo name to display"):
                    return "remote-alpha"
                return default

            updated = handle_session_command(
                "/repos link",
                current,
                paths,
                text_prompt=text_prompt,
                display=displayed.append,
            )

            self.assertEqual(current, updated)
            latest = initialize_state_store(paths.config_dir).get_latest_repo_state()
            self.assertIsNotNone(latest)
            assert latest is not None
            self.assertEqual("gcp", latest.execution_target)
            self.assertEqual("~/.duckln/projects/remote-alpha", latest.metadata["install_location"])
            self.assertEqual("gcp:duckln-gcp:us-central1-a", latest.metadata["cloud_resource_key"])
            self.assertEqual("https://github.com/example/remote-alpha", latest.repo_url)
            self.assertTrue(any("remote repo path" in message.lower() or "remote repo" in message.lower() for message in displayed))

    def test_free_text_followup_uses_chosen_recommendation_not_stale_active_repo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "open-webui",
                                "repo_url": "https://example.com/open-webui",
                                "stars": 120000,
                                "description": "Model UI.",
                                "category": "LLM",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            store = initialize_state_store(config_dir)
            store.upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path=str(config_dir / "projects" / "whisper"),
                status="ready",
                summary="Whisper ready.",
            )
            store.upsert_repo_knowledge(
                repo_key="https://example.com/open-webui",
                repo_name="open-webui",
                repo_url="https://example.com/open-webui",
                source="remote",
                summary="open-webui knowledge (remote). requirements: 6-10 GB RAM; GPU optional.",
                cpu_profile="a few CPU cores",
                ram_profile="6-10 GB RAM",
                gpu_profile="GPU optional",
                required_tools=("Python",),
            )

            first = _respond_to_free_text("recommend me one", config_dir=config_dir)
            followup = _respond_to_free_text("yes please", config_dir=config_dir)

            self.assertEqual("open-webui", first.recommendation_repo_name)
            self.assertEqual("repo_requirements", followup.intent)
            self.assertIn("open-webui", followup.text.lower())
            self.assertNotIn("whisper", followup.text.lower())

    def test_free_text_greeting_varies_with_recent_history(self) -> None:
        first = _respond_to_free_text("hi")
        second = _respond_to_free_text(
            "hi",
            recent_turns=(
                ConversationTurn(role="user", content="hi"),
                ConversationTurn(role="assistant", content=first.text),
            ),
            recent_replies=(first,),
        )

        self.assertNotEqual(first.text, second.text)

    def test_free_text_fallback_handles_compound_system_and_repo_requirement_phrase(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            response = _respond_to_free_text(
                "how much compute does my system have and how much is required by whisper /repos",
                config_dir=config_dir,
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
            )

            self.assertEqual("repo_requirements", response.intent)
            self.assertIn("whisper", response.text.lower())
            self.assertNotIn("your system:", response.text.lower())
            self.assertNotIn("repo likely needs:", response.text.lower())
            self.assertNotIn("practical judgment:", response.text.lower())
            self.assertNotIn("not recommended (not recommended)", response.text.lower())

    def test_free_text_recommendation_compare_handles_whisperx_or_whisper(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            },
                            {
                                "name": "whisperX",
                                "repo_url": "https://example.com/whisperx",
                                "stars": 20000,
                                "description": "Timestamped ASR.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            },
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            response = _respond_to_free_text(
                "Do you recommend WhisperX or Whisper",
                config_dir=config_dir,
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
            )

            self.assertEqual("repo_recommendation_compare", response.intent)
            self.assertIn("whisper", response.text.lower())
            self.assertIn("whisperx", response.text.lower())
            self.assertTrue("choose" in response.text.lower() or "start" in response.text.lower())

    def test_free_text_capability_plus_recommendation_routes_to_recommendation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            response = _respond_to_free_text(
                "which repos can you help me with do you have any recommendations for my system",
                config_dir=config_dir,
            )

            self.assertTrue(response.intent.startswith("repo_recommendation"))
            self.assertIn("whisper", response.text.lower())

    def test_recommendation_and_preflight_share_same_fit_label(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            store = initialize_state_store(config_dir)
            store.upsert_repo_knowledge(
                repo_key="https://example.com/whisper",
                repo_name="whisper",
                repo_url="https://example.com/whisper",
                source="remote",
                summary="whisper knowledge (remote). requirements: a few CPU cores; 8-16 GB RAM; GPU optional.",
                cpu_profile="a few CPU cores",
                ram_profile="8-16 GB RAM",
                gpu_profile="GPU optional",
                required_tools=("Python", "ffmpeg"),
            )
            probe = SystemProbe(
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
            )

            response = _respond_to_free_text(
                "recommend one repo for my system",
                config_dir=config_dir,
                system_probe=probe,
            )
            records = tuple(load_sorted_local_repo_catalog(config_dir))
            self.assertTrue(records)
            preflight = assess_repo_preflight(
                records[0],
                config_dir=config_dir,
                system_probe=probe,
            )

            self.assertIsNotNone(response.recommendation_fit_label)
            self.assertEqual(preflight.fit_status, response.recommendation_fit_label)

    def test_followup_state_persists_route_metadata_for_clarification(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)

            _respond_to_free_text("run that", config_dir=config_dir)

            store = initialize_state_store(config_dir)
            snapshot = store.read_config_values(prefix="conversation.followup.")
            self.assertIn("conversation.followup.last_route_family", snapshot)
            self.assertEqual('"clarify"', snapshot["conversation.followup.last_route_family"])
            self.assertIn("conversation.followup.pending_clarification_options", snapshot)

    def test_free_text_fallback_can_trigger_repo_run_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            store = initialize_state_store(config_dir)
            store.upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path=str(config_dir / "projects" / "whisper"),
                status="ready",
                summary="Whisper ready.",
                metadata={"repo_name": "whisper"},
            )

            reply = _respond_to_free_text("run this repo whisper", config_dir=config_dir)

            self.assertEqual("run_repo", reply.action)
            self.assertEqual("https://example.com/whisper", reply.action_repo_key)
            self.assertIn("does not have a verified run command", reply.text.lower())

    def test_free_text_repo_run_for_cli_tool_help_command_offers_materialized_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            initialize_state_store(config_dir).upsert_repo_state(
                repo_key="https://github.com/openai/whisper",
                repo_url="https://github.com/openai/whisper",
                repo_path=str(config_dir / "projects" / "whisper"),
                status="ready",
                summary="Whisper ready.",
                metadata={
                    "repo_name": "whisper",
                    "run_command": ".venv/bin/python -m whisper --help",
                    "verify_command": ".venv/bin/python -m whisper --help",
                    "manual_command": ".venv/bin/python -m whisper <audio-file> --model base",
                    "runtime_kind": "cli_tool",
                },
            )

            reply = _respond_to_free_text("can you run whisper", config_dir=config_dir)

            self.assertEqual("repo_run", reply.intent)
            self.assertEqual("run_repo", reply.action)
            self.assertIn("cli tool", reply.text.lower())
            self.assertIn("<audio-file>", reply.text)

    def test_handle_repo_run_action_materializes_cli_task_before_running(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            project_dir = resolve_managed_project_dir(
                paths.config_dir,
                RepoCatalogRecord(
                    name="whisper",
                    repo_url="https://github.com/openai/whisper",
                    stars=97200,
                    description="Speech recognition.",
                    category="Audio",
                    framework="Python",
                    last_updated="2026-04-17",
                ),
            )
            (project_dir / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
            (project_dir / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
            initialize_state_store(paths.config_dir).upsert_repo_state(
                repo_key="https://github.com/openai/whisper",
                repo_url="https://github.com/openai/whisper",
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
            displayed: list[str] = []

            with patch("duckln.main.run_prepared_repo") as run_mock:
                run_mock.return_value.message = "Supervisor agent opened whisper."
                run_mock.return_value.verification_passed = True
                _handle_repo_run_action(
                    reply=FreeTextReply(
                        text="",
                        intent="repo_run",
                        action="run_repo",
                        action_repo_key="https://github.com/openai/whisper",
                    ),
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=lambda _prompt: True,
                    text_prompt=lambda _message, _default="": "sample.wav",
                )

            self.assertEqual(
                ".venv/bin/python -m whisper sample.wav --model base",
                run_mock.call_args.kwargs["runtime_command_override"],
            )

    def test_handle_repo_run_action_asks_once_with_materialized_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            project_dir = resolve_managed_project_dir(
                paths.config_dir,
                RepoCatalogRecord(
                    name="whisper",
                    repo_url="https://github.com/openai/whisper",
                    stars=97200,
                    description="Speech recognition.",
                    category="Audio",
                    framework="Python",
                    last_updated="2026-04-17",
                ),
            )
            (project_dir / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
            (project_dir / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
            initialize_state_store(paths.config_dir).upsert_repo_state(
                repo_key="https://github.com/openai/whisper",
                repo_url="https://github.com/openai/whisper",
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
            prompts: list[str] = []

            with patch("duckln.main.run_prepared_repo") as run_mock:
                run_mock.return_value = SimpleNamespace(
                    message="Supervisor agent opened whisper.",
                    verification_passed=True,
                    message_already_displayed=False,
                    should_offer_repair=False,
                )
                _handle_repo_run_action(
                    reply=FreeTextReply(
                        text="",
                        intent="repo_run",
                        action="run_repo",
                        action_repo_key="https://github.com/openai/whisper",
                    ),
                    current=current,
                    paths=paths,
                    display_output=lambda _message: None,
                    approve_prompt=lambda prompt: prompts.append(prompt) or True,
                    text_prompt=lambda _message, _default="": "sample.wav",
                )

            self.assertEqual(
                ["Run whisper: .venv/bin/python -m whisper sample.wav --model base"],
                prompts,
            )
            self.assertTrue(run_mock.call_args.kwargs["runtime_step_preapproved"])

    def test_handle_repo_run_action_prefers_attached_file_for_cli_task_materialization(self) -> None:
        class AttachmentTerminal(FakeTerminalInterface):
            def __init__(self) -> None:
                super().__init__()
                self._attached_files = ["/tmp/audio.wav"]
                self.consumed_files: list[str] = []

            def list_attached_files(self) -> tuple[str, ...]:
                return tuple(self._attached_files)

            def consume_attached_file(self, path: str) -> None:
                self.consumed_files.append(path)
                self._attached_files = [item for item in self._attached_files if item != path]

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            project_dir = resolve_managed_project_dir(
                paths.config_dir,
                RepoCatalogRecord(
                    name="whisper",
                    repo_url="https://github.com/openai/whisper",
                    stars=97200,
                    description="Speech recognition.",
                    category="Audio",
                    framework="Python",
                    last_updated="2026-04-17",
                ),
            )
            (project_dir / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
            (project_dir / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
            initialize_state_store(paths.config_dir).upsert_repo_state(
                repo_key="https://github.com/openai/whisper",
                repo_url="https://github.com/openai/whisper",
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
            displayed: list[str] = []
            terminal = AttachmentTerminal()

            with patch("duckln.main.run_prepared_repo") as run_mock:
                run_mock.return_value.message = "Supervisor agent opened whisper."
                run_mock.return_value.verification_passed = True
                _handle_repo_run_action(
                    reply=FreeTextReply(
                        text="",
                        intent="repo_run",
                        action="run_repo",
                        action_repo_key="https://github.com/openai/whisper",
                    ),
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=lambda _prompt: True,
                    text_prompt=lambda _message, _default="": (_ for _ in ()).throw(
                        AssertionError("text prompt should not be used when an attached file is available")
                    ),
                    terminal_interface=terminal,
                )

            self.assertEqual(
                ".venv/bin/python -m whisper /tmp/audio.wav --model base",
                run_mock.call_args.kwargs["runtime_command_override"],
            )
            self.assertEqual(["/tmp/audio.wav"], terminal.consumed_files)
            self.assertTrue(any("Attached files used: /tmp/audio.wav" in line for line in displayed))
            self.assertTrue(any("repo task materialization" in message.lower() for message in displayed))

    def test_handle_repo_run_action_does_not_repeat_already_displayed_supervisor_message(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            project_dir = resolve_managed_project_dir(
                paths.config_dir,
                RepoCatalogRecord(
                    name="whisper",
                    repo_url="https://github.com/openai/whisper",
                    stars=97200,
                    description="Speech recognition.",
                    category="Audio",
                    framework="Python",
                    last_updated="2026-04-17",
                ),
            )
            (project_dir / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
            (project_dir / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
            initialize_state_store(paths.config_dir).upsert_repo_state(
                repo_key="https://github.com/openai/whisper",
                repo_url="https://github.com/openai/whisper",
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
            displayed: list[str] = []
            repeated_message = "Supervisor agent is not treating whisper as a long-running repo service."

            with patch("duckln.main.run_prepared_repo") as run_mock:
                run_mock.return_value = SimpleNamespace(
                    message=repeated_message,
                    verification_passed=False,
                    message_already_displayed=True,
                    should_offer_repair=False,
                )
                _handle_repo_run_action(
                    reply=FreeTextReply(
                        text="",
                        intent="repo_run",
                        action="run_repo",
                        action_repo_key="https://github.com/openai/whisper",
                    ),
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=lambda _prompt: True,
                    text_prompt=lambda _message, _default="": "sample.wav",
                )

            self.assertNotIn(repeated_message, displayed)

    def test_handle_repo_run_action_resolves_linked_repo_from_tracked_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            repo_dir = Path(temp_dir) / "linked-alpha"
            repo_dir.mkdir(parents=True, exist_ok=True)
            linked_key = f"linked://local/{repo_dir.resolve().as_posix()}"
            initialize_state_store(paths.config_dir).upsert_repo_state(
                repo_key=linked_key,
                repo_url="https://github.com/example/linked-alpha",
                repo_path=str(repo_dir.resolve()),
                status="linked",
                summary="linked repo ready",
                managed_by_duckln=False,
                metadata={
                    "repo_name": "linked-alpha",
                    "run_command": "python -m pip --version",
                    "install_location": str(repo_dir.resolve()),
                },
            )
            displayed: list[str] = []

            with patch("duckln.main.run_prepared_repo") as run_mock:
                run_mock.return_value.message = "Supervisor agent verified linked-alpha."
                run_mock.return_value.verification_passed = True
                _handle_repo_run_action(
                    reply=FreeTextReply(text="", intent="repo_run", action="run_repo", action_repo_key=linked_key),
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=lambda _prompt: True,
                )

            passed_repo = run_mock.call_args.args[0]
            self.assertEqual("linked-alpha", passed_repo.name)
            self.assertEqual(linked_key, passed_repo.repo_url)
            self.assertTrue(any("verified linked-alpha" in message.lower() for message in displayed))

    def test_handle_repo_run_action_repairs_after_failed_run_when_user_approves(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            store = initialize_state_store(paths.config_dir)
            store.upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path=str(paths.config_dir / "projects" / "whisper"),
                status="ready",
                summary="Whisper ready.",
                metadata={"repo_name": "whisper"},
            )
            displayed: list[str] = []
            prompts: list[str] = []
            reply = _respond_to_free_text("run this repo whisper", config_dir=paths.config_dir)
            terminal = FakeTerminalInterface()

            class RunResults:
                def __init__(self) -> None:
                    self.calls = 0

                def __call__(self, *args, **kwargs):
                    self.calls += 1
                    class Result:
                        pass
                    result = Result()
                    if self.calls == 1:
                        result.message = "Supervisor agent saw a run issue for whisper."
                        result.verification_passed = False
                    else:
                        result.message = "Supervisor agent started whisper successfully."
                        result.verification_passed = True
                    return result

            with (
                patch("duckln.main.run_prepared_repo", side_effect=RunResults()) as run_mock,
                patch("duckln.main.bring_up_selected_repo") as repair_mock,
                patch("duckln.main.gather_runtime_repair_evidence") as evidence_mock,
            ):
                repair_mock.return_value.message = "Supervisor agent repaired whisper."
                repair_mock.return_value.verification_passed = True
                evidence_mock.return_value = SimpleNamespace(
                    note="Duckln checked current official docs for this blocker. Source: [1] https://docs.example/fix",
                    source_urls=("https://docs.example/fix",),
                    search_query="whisper runtime blocker fix",
                )
                _handle_repo_run_action(
                    reply=reply,
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=lambda prompt: prompts.append(prompt) or True,
                    terminal_interface=terminal,
                )

            self.assertEqual(2, run_mock.call_count)
            self.assertIs(terminal, run_mock.call_args_list[1].kwargs["terminal_executor"])
            repair_mock.assert_called_once()
            self.assertTrue(any("Duckln trace: repo runtime review." in message for message in displayed))
            self.assertTrue(any("Duckln trace: runtime repair evidence." in message for message in displayed))
            self.assertTrue(any("https://docs.example/fix" in message for message in displayed))
            self.assertTrue(any("Duckln trace: runtime repair handoff." in message for message in displayed))
            self.assertTrue(any("Duckln trace: post-repair rerun." in message for message in displayed))
            self.assertTrue(any("Duckln checked current official docs" in message for message in displayed))
            self.assertTrue(any("whisper runtime blocker fix" in message for message in displayed))

    def test_drain_terminal_runtime_incidents_persists_runtime_repair_offer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            terminal = FakeTerminalInterface()
            terminal.incidents.append(
                {
                    "category": "missing_command",
                    "summary": "Duckln classified this blocker as missing command. Toolchain: python. Likely package/module: ffmpeg. Fatal line: FileNotFoundError: [Errno 2] No such file or directory: 'ffmpeg'.",
                    "fatal_line": "FileNotFoundError: [Errno 2] No such file or directory: 'ffmpeg'",
                    "package_hint": "ffmpeg",
                    "tool_hint": "python",
                    "relevant_lines": ("FileNotFoundError: [Errno 2] No such file or directory: 'ffmpeg'",),
                }
            )
            write_workflow_state(
                paths.config_dir,
                {
                    "active_repo_key": "https://example.com/whisper",
                    "active_repo_name": "whisper",
                    "active_runtime_repo_key": "https://example.com/whisper",
                    "active_runtime_repo_name": "whisper",
                    "active_runtime_execution_target": "local",
                    "active_runtime_command": ".venv/bin/python -m whisper /tmp/example.mp3 --model base",
                    "active_runtime_cwd": str(Path(temp_dir) / "projects" / "whisper"),
                },
            )
            displayed: list[str] = []

            with patch("duckln.main.gather_runtime_repair_evidence") as evidence_mock:
                evidence_mock.return_value = SimpleNamespace(
                    note="Duckln checked current official docs for this blocker. Source: [1] https://ffmpeg.org/documentation.html",
                    source_urls=("https://ffmpeg.org/documentation.html",),
                    search_query="ffmpeg missing whisper runtime",
                )
                _drain_terminal_runtime_incidents(
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=lambda _prompt: True,
                    terminal_interface=terminal,
                )

            followup_state = read_followup_state(paths.config_dir)
            workflow_state = read_workflow_state(paths.config_dir)
            self.assertEqual("repair_repo_runtime", followup_state["pending_next_action"])
            self.assertEqual("missing_command", workflow_state["active_incident_category"])
            self.assertTrue(any("terminal incident intake" in message.lower() for message in displayed))
            self.assertTrue(any("live runtime blocker" in message.lower() for message in displayed))

    def test_drain_terminal_runtime_incidents_dedupes_same_blocker_in_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            incident = {
                "category": "missing_command",
                "summary": "Duckln classified this blocker as missing command. Likely package/module: ffmpeg.",
                "fatal_line": "FileNotFoundError: [Errno 2] No such file or directory: 'ffmpeg'",
                "package_hint": "ffmpeg",
                "tool_hint": "python",
                "relevant_lines": ("FileNotFoundError: [Errno 2] No such file or directory: 'ffmpeg'",),
            }
            terminal = FakeTerminalInterface()
            terminal.incidents.extend((incident, dict(incident)))
            write_workflow_state(
                paths.config_dir,
                {
                    "active_repo_key": "https://example.com/whisper",
                    "active_repo_name": "whisper",
                    "active_runtime_repo_key": "https://example.com/whisper",
                    "active_runtime_repo_name": "whisper",
                    "active_runtime_execution_target": "local",
                    "active_runtime_command": ".venv/bin/python -m whisper /tmp/example.mp3 --model base",
                    "active_runtime_cwd": str(Path(temp_dir) / "projects" / "whisper"),
                },
            )
            displayed: list[str] = []

            with patch("duckln.main.gather_runtime_repair_evidence") as evidence_mock:
                evidence_mock.return_value = SimpleNamespace(
                    note="Duckln checked current official docs for this blocker. Source: [1] https://ffmpeg.org/documentation.html",
                    source_urls=("https://ffmpeg.org/documentation.html",),
                    search_query="ffmpeg missing whisper runtime",
                )
                _drain_terminal_runtime_incidents(
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=lambda _prompt: True,
                    terminal_interface=terminal,
                )

            self.assertEqual(1, sum("terminal incident intake" in message.lower() for message in displayed))
            self.assertEqual(1, sum("live runtime blocker" in message.lower() for message in displayed))
            evidence_mock.assert_called_once()

    def test_drain_terminal_runtime_incidents_auto_continues_preapproved_objective(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "openclaw",
                                "repo_url": "https://example.com/openclaw",
                                "stars": 1234,
                                "description": "Openclaw.",
                                "category": "Agents",
                                "framework": "Docker",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            terminal = FakeTerminalInterface()
            terminal.incidents.append(
                {
                    "category": "docker_env_missing",
                    "summary": 'Duckln classified this blocker as docker env missing. Fatal line: invalid spec: :/home/node/.openclaw: empty section between colons.',
                }
            )
            write_workflow_state(
                paths.config_dir,
                {
                    "active_repo_key": "https://example.com/openclaw",
                    "active_repo_name": "openclaw",
                    "active_runtime_repo_key": "https://example.com/openclaw",
                    "active_runtime_repo_name": "openclaw",
                    "active_runtime_execution_target": "local",
                    "active_runtime_command": "docker compose up",
                    "active_runtime_cwd": str(Path(temp_dir) / "projects" / "openclaw"),
                    "active_objective_kind": "runtime_repair",
                    "active_objective_repo_key": "https://example.com/openclaw",
                    "active_objective_repo_name": "openclaw",
                    "active_objective_status": "active",
                    "active_objective_requires_user_decision": False,
                },
            )
            displayed: list[str] = []
            with patch("duckln.main._run_runtime_repair_workflow") as repair_mock:
                _drain_terminal_runtime_incidents(
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=lambda _prompt: True,
                    terminal_interface=terminal,
                )
            repair_mock.assert_called_once()
            self.assertFalse(any("live runtime blocker" in message.lower() for message in displayed))

    def test_runtime_repair_incident_then_acceptance_continues_same_docker_repo_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "openclaw",
                                "repo_url": "https://example.com/openclaw",
                                "stars": 1234,
                                "description": "Openclaw.",
                                "category": "Agents",
                                "framework": "Docker",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            terminal = FakeTerminalInterface()
            terminal.incidents.append(
                {
                    "category": "docker_env_missing",
                    "summary": (
                        'Duckln classified this blocker as docker env missing. Fatal line: '
                        'WARN[0000] The "OPENCLAW_CONFIG_DIR" variable is not set. '
                        "invalid spec: :/home/node/.openclaw: empty section between colons."
                    ),
                    "fatal_line": "invalid spec: :/home/node/.openclaw: empty section between colons.",
                }
            )
            write_workflow_state(
                paths.config_dir,
                {
                    "active_repo_key": "https://example.com/openclaw",
                    "active_repo_name": "openclaw",
                    "active_runtime_repo_key": "https://example.com/openclaw",
                    "active_runtime_repo_name": "openclaw",
                    "active_runtime_execution_target": "docker",
                    "active_runtime_command": "docker compose up",
                    "active_runtime_cwd": str(Path(temp_dir) / "projects" / "openclaw"),
                },
            )
            displayed: list[str] = []

            with patch("duckln.main.gather_runtime_repair_evidence") as evidence_mock:
                evidence_mock.return_value = SimpleNamespace(
                    note="Duckln checked current official docs for this blocker. Source: [1] https://docs.example/openclaw-docker",
                    source_urls=("https://docs.example/openclaw-docker",),
                    search_query="openclaw docker env missing",
                )
                _drain_terminal_runtime_incidents(
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=lambda _prompt: True,
                    terminal_interface=terminal,
                )

            followup_state = read_followup_state(paths.config_dir)
            workflow_state = read_workflow_state(paths.config_dir)
            self.assertEqual("repair_repo_runtime", followup_state["pending_next_action"])
            self.assertEqual("https://example.com/openclaw", followup_state["pending_repo_key"])
            self.assertEqual(
                "runtime_repair:https://example.com/openclaw",
                followup_state["pending_objective_id"],
            )
            self.assertEqual(
                "runtime_repair:https://example.com/openclaw",
                followup_state["pending_offer_objective_id"],
            )
            self.assertEqual("docker_env_missing", workflow_state["active_incident_category"])
            self.assertIn("openclaw", workflow_state["active_runtime_repo_name"])

            with patch("duckln.main._run_runtime_repair_workflow") as repair_mock:
                handled = _handle_pending_runtime_repair_followup(
                    command="yes fix it",
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=lambda _prompt: True,
                    terminal_interface=terminal,
                )

            self.assertTrue(handled)
            repair_mock.assert_called_once()
            self.assertEqual("openclaw", repair_mock.call_args.kwargs["repo"].name)
            self.assertEqual("docker", repair_mock.call_args.kwargs["workflow"].active_runtime_execution_target)
            self.assertTrue(any("terminal incident intake" in message.lower() for message in displayed))
            self.assertTrue(any("continuing the bounded runtime repair path for openclaw" in message.lower() for message in displayed))

    def test_drain_terminal_runtime_incidents_skips_duplicate_active_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            terminal = FakeTerminalInterface()
            terminal.incidents.append(
                {
                    "category": "missing_command",
                    "summary": "Duckln classified this blocker as missing command. Likely package/module: ffmpeg.",
                    "fatal_line": "FileNotFoundError: [Errno 2] No such file or directory: 'ffmpeg'",
                    "package_hint": "ffmpeg",
                    "tool_hint": "python",
                    "relevant_lines": ("FileNotFoundError: [Errno 2] No such file or directory: 'ffmpeg'",),
                }
            )
            write_workflow_state(
                paths.config_dir,
                {
                    "active_repo_key": "https://example.com/whisper",
                    "active_repo_name": "whisper",
                    "active_runtime_repo_key": "https://example.com/whisper",
                    "active_runtime_repo_name": "whisper",
                    "active_runtime_execution_target": "local",
                    "active_runtime_command": ".venv/bin/python -m whisper /tmp/example.mp3 --model base",
                    "active_runtime_cwd": str(Path(temp_dir) / "projects" / "whisper"),
                    "active_issue_kind": "run_issue",
                    "active_issue_summary": "Duckln classified this blocker as missing command. Likely package/module: ffmpeg.",
                    "active_incident_category": "missing_command",
                    "active_incident_summary": "Duckln classified this blocker as missing command. Likely package/module: ffmpeg.",
                    "active_repair_phase": "runtime_failed",
                },
            )
            write_followup_state(
                paths.config_dir,
                {
                    "pending_next_action": "repair_repo_runtime",
                    "pending_offer_kind": "repair_repo_runtime",
                    "pending_repo_key": "https://example.com/whisper",
                    "pending_repo_name": "whisper",
                },
            )
            displayed: list[str] = []

            with patch("duckln.main.gather_runtime_repair_evidence") as evidence_mock:
                _drain_terminal_runtime_incidents(
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=lambda _prompt: True,
                    terminal_interface=terminal,
                )

            self.assertEqual([], displayed)
            evidence_mock.assert_not_called()

    def test_pending_runtime_repair_followup_runs_repair_workflow_on_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            write_followup_state(
                paths.config_dir,
                {
                    "pending_next_action": "repair_repo_runtime",
                    "pending_offer_kind": "repair_repo_runtime",
                    "pending_repo_key": "https://example.com/whisper",
                    "pending_repo_name": "whisper",
                },
            )
            write_workflow_state(
                paths.config_dir,
                {
                    "active_repo_key": "https://example.com/whisper",
                    "active_repo_name": "whisper",
                    "active_runtime_repo_key": "https://example.com/whisper",
                    "active_runtime_repo_name": "whisper",
                    "active_runtime_execution_target": "local",
                    "active_runtime_command": ".venv/bin/python -m whisper /tmp/example.mp3 --model base",
                    "active_runtime_cwd": str(Path(temp_dir) / "projects" / "whisper"),
                    "active_issue_summary": "Duckln classified this blocker as missing command. Likely package/module: ffmpeg.",
                },
            )
            displayed: list[str] = []

            with patch("duckln.main._run_runtime_repair_workflow") as repair_mock:
                handled = _handle_pending_runtime_repair_followup(
                    command="yes please fix it",
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=lambda _prompt: True,
                    terminal_interface=FakeTerminalInterface(),
                )

            self.assertTrue(handled)
            repair_mock.assert_called_once()
            self.assertTrue(any("continuing the bounded runtime repair path" in message.lower() for message in displayed))

    def test_handle_pending_runtime_repair_followup_accepts_please_ask(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            write_followup_state(
                paths.config_dir,
                {
                    "pending_next_action": "repair_repo_runtime",
                    "pending_offer_kind": "repair_repo_runtime",
                    "pending_repo_key": "https://example.com/whisper",
                    "pending_repo_name": "whisper",
                },
            )
            write_workflow_state(
                paths.config_dir,
                {
                    "active_repo_key": "https://example.com/whisper",
                    "active_repo_name": "whisper",
                    "active_runtime_repo_key": "https://example.com/whisper",
                    "active_runtime_repo_name": "whisper",
                    "active_runtime_execution_target": "local",
                    "active_issue_summary": "Duckln paused because approval is still required.",
                    "active_repair_phase": "awaiting_runtime_approval",
                },
            )
            displayed: list[str] = []

            with patch("duckln.main._run_runtime_repair_workflow") as repair_mock:
                handled = _handle_pending_runtime_repair_followup(
                    command="please ask",
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=lambda _prompt: True,
                    terminal_interface=FakeTerminalInterface(),
                )

            self.assertTrue(handled)
            repair_mock.assert_called_once()
            self.assertTrue(any("continuing the bounded runtime repair path" in message.lower() for message in displayed))

    def test_handle_pending_runtime_repair_followup_uses_workflow_state_when_followup_state_was_lost(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            write_workflow_state(
                paths.config_dir,
                {
                    "active_repo_key": "https://example.com/whisper",
                    "active_repo_name": "whisper",
                    "active_runtime_repo_key": "https://example.com/whisper",
                    "active_runtime_repo_name": "whisper",
                    "active_runtime_execution_target": "local",
                    "active_runtime_command": ".venv/bin/python -m whisper /tmp/example.mp3 --model base",
                    "active_runtime_cwd": str(Path(temp_dir) / "projects" / "whisper"),
                    "active_issue_kind": "run_issue",
                    "active_issue_summary": "Duckln classified this blocker as missing command. Likely package/module: ffmpeg.",
                    "active_repair_phase": "repair_declined",
                },
            )
            displayed: list[str] = []

            with patch("duckln.main._run_runtime_repair_workflow") as repair_mock:
                handled = _handle_pending_runtime_repair_followup(
                    command="yes continue repair whisper",
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=lambda _prompt: True,
                    terminal_interface=FakeTerminalInterface(),
                )

            self.assertTrue(handled)
            repair_mock.assert_called_once()
            self.assertTrue(any("continuing the bounded runtime repair path" in message.lower() for message in displayed))

    def test_handle_pending_runtime_repair_followup_reactivates_objective_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "openclaw",
                                "repo_url": "https://example.com/openclaw",
                                "stars": 1234,
                                "description": "Openclaw.",
                                "category": "Agents",
                                "framework": "Docker",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            write_workflow_state(
                paths.config_dir,
                {
                    "active_repo_key": "https://example.com/openclaw",
                    "active_repo_name": "openclaw",
                    "active_objective_kind": "runtime_repair",
                    "active_objective_repo_key": "https://example.com/openclaw",
                    "active_objective_repo_name": "openclaw",
                    "active_objective_status": "needs_user_decision",
                    "active_objective_requires_user_decision": True,
                    "active_issue_kind": "run_issue",
                    "active_issue_summary": "Duckln classified this blocker as docker env missing.",
                    "active_repair_phase": "repair_failed",
                },
            )
            displayed: list[str] = []
            with patch("duckln.main._run_runtime_repair_workflow"):
                handled = _handle_pending_runtime_repair_followup(
                    command="yes fix it",
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=lambda _prompt: True,
                    terminal_interface=FakeTerminalInterface(),
                )
            self.assertTrue(handled)
            workflow = read_workflow_state(paths.config_dir)
            self.assertEqual("active", workflow["active_objective_status"])
            self.assertFalse(workflow["active_objective_requires_user_decision"])

    def test_handle_pending_runtime_repair_followup_prefers_persisted_objective_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            },
                            {
                                "name": "openclaw",
                                "repo_url": "https://example.com/openclaw",
                                "stars": 1234,
                                "description": "Openclaw.",
                                "category": "Agents",
                                "framework": "Docker",
                                "last_updated": "2026-04-01",
                            },
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            write_followup_state(
                paths.config_dir,
                {
                    "pending_next_action": "repair_repo_runtime",
                    "pending_offer_kind": "repair_repo_runtime",
                    "pending_objective_id": "runtime_repair:https://example.com/whisper",
                    "pending_offer_objective_id": "runtime_repair:https://example.com/whisper",
                    "pending_repo_key": "https://example.com/whisper",
                    "pending_repo_name": "whisper",
                    "active_execution_target": "docker",
                },
            )
            write_workflow_state(
                paths.config_dir,
                {
                    "active_repo_key": "https://example.com/openclaw",
                    "active_repo_name": "openclaw",
                    "active_runtime_repo_key": "https://example.com/openclaw",
                    "active_runtime_repo_name": "openclaw",
                    "active_runtime_execution_target": "local",
                    "active_objective_kind": "runtime_repair",
                    "active_objective_repo_key": "https://example.com/openclaw",
                    "active_objective_repo_name": "openclaw",
                    "active_objective_execution_target": "local",
                    "active_objective_status": "needs_user_decision",
                    "active_objective_requires_user_decision": True,
                    "active_issue_kind": "run_issue",
                    "active_issue_summary": "Duckln classified this blocker as a Docker runtime issue.",
                    "active_repair_phase": "repair_failed",
                },
            )
            displayed: list[str] = []

            with patch("duckln.main._run_runtime_repair_workflow") as repair_mock:
                handled = _handle_pending_runtime_repair_followup(
                    command="yes fix it",
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=lambda _prompt: True,
                    terminal_interface=FakeTerminalInterface(),
                )

            self.assertTrue(handled)
            repair_mock.assert_called_once()
            self.assertEqual("whisper", repair_mock.call_args.kwargs["repo"].name)
            self.assertIsNone(repair_mock.call_args.kwargs["workflow"])
            workflow = read_workflow_state(paths.config_dir)
            self.assertEqual("https://example.com/whisper", workflow["active_objective_repo_key"])
            self.assertEqual("docker", workflow["active_objective_execution_target"])

    def test_handle_pending_runtime_repair_followup_preserves_vm_execution_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "private-gpt",
                                "repo_url": "https://example.com/private-gpt",
                                "stars": 321,
                                "description": "Private GPT.",
                                "category": "LLM",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            write_workflow_state(
                paths.config_dir,
                {
                    "active_repo_key": "https://example.com/private-gpt",
                    "active_repo_name": "private-gpt",
                    "active_runtime_repo_key": "https://example.com/private-gpt",
                    "active_runtime_repo_name": "private-gpt",
                    "active_runtime_execution_target": "vm",
                    "active_objective_kind": "runtime_repair",
                    "active_objective_repo_key": "https://example.com/private-gpt",
                    "active_objective_repo_name": "private-gpt",
                    "active_objective_execution_target": "vm",
                    "active_objective_status": "needs_user_decision",
                    "active_objective_requires_user_decision": True,
                    "active_issue_kind": "run_issue",
                    "active_issue_summary": "Duckln classified this blocker as a VM runtime issue.",
                    "active_repair_phase": "repair_failed",
                },
            )
            with patch("duckln.main._run_runtime_repair_workflow"):
                handled = _handle_pending_runtime_repair_followup(
                    command="sure continue",
                    current=current,
                    paths=paths,
                    display_output=lambda _message: None,
                    approve_prompt=lambda _prompt: True,
                    terminal_interface=FakeTerminalInterface(),
                )

            self.assertTrue(handled)
            workflow = read_workflow_state(paths.config_dir)
            self.assertEqual("vm", workflow["active_objective_execution_target"])
            self.assertEqual("active", workflow["active_objective_status"])

    def test_handle_pending_runtime_repair_followup_preserves_cloud_execution_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "open-webui",
                                "repo_url": "https://example.com/open-webui",
                                "stars": 1234,
                                "description": "Open WebUI.",
                                "category": "LLM",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            write_workflow_state(
                paths.config_dir,
                {
                    "active_repo_key": "https://example.com/open-webui",
                    "active_repo_name": "open-webui",
                    "active_runtime_repo_key": "https://example.com/open-webui",
                    "active_runtime_repo_name": "open-webui",
                    "active_runtime_execution_target": "gcp",
                    "active_objective_kind": "runtime_repair",
                    "active_objective_repo_key": "https://example.com/open-webui",
                    "active_objective_repo_name": "open-webui",
                    "active_objective_execution_target": "gcp",
                    "active_objective_status": "needs_user_decision",
                    "active_objective_requires_user_decision": True,
                    "active_issue_kind": "run_issue",
                    "active_issue_summary": "Duckln classified this blocker as a cloud runtime issue.",
                    "active_repair_phase": "repair_failed",
                },
            )
            with patch("duckln.main._run_runtime_repair_workflow"):
                handled = _handle_pending_runtime_repair_followup(
                    command="go ahead",
                    current=current,
                    paths=paths,
                    display_output=lambda _message: None,
                    approve_prompt=lambda _prompt: True,
                    terminal_interface=FakeTerminalInterface(),
                )

            self.assertTrue(handled)
            workflow = read_workflow_state(paths.config_dir)
            self.assertEqual("gcp", workflow["active_objective_execution_target"])
            self.assertEqual("active", workflow["active_objective_status"])

    def test_handle_pending_runtime_repair_followup_preserves_docker_execution_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "openclaw",
                                "repo_url": "https://example.com/openclaw",
                                "stars": 1234,
                                "description": "Openclaw.",
                                "category": "Agents",
                                "framework": "Docker",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            write_workflow_state(
                paths.config_dir,
                {
                    "active_repo_key": "https://example.com/openclaw",
                    "active_repo_name": "openclaw",
                    "active_runtime_repo_key": "https://example.com/openclaw",
                    "active_runtime_repo_name": "openclaw",
                    "active_runtime_execution_target": "docker",
                    "active_objective_kind": "runtime_repair",
                    "active_objective_repo_key": "https://example.com/openclaw",
                    "active_objective_repo_name": "openclaw",
                    "active_objective_execution_target": "docker",
                    "active_objective_status": "needs_user_decision",
                    "active_objective_requires_user_decision": True,
                    "active_issue_kind": "run_issue",
                    "active_issue_summary": "Duckln classified this blocker as a Docker runtime issue.",
                    "active_repair_phase": "repair_failed",
                },
            )
            with patch("duckln.main._run_runtime_repair_workflow"):
                handled = _handle_pending_runtime_repair_followup(
                    command="alright fix it",
                    current=current,
                    paths=paths,
                    display_output=lambda _message: None,
                    approve_prompt=lambda _prompt: True,
                    terminal_interface=FakeTerminalInterface(),
                )

            self.assertTrue(handled)
            workflow = read_workflow_state(paths.config_dir)
            self.assertEqual("docker", workflow["active_objective_execution_target"])
            self.assertEqual("active", workflow["active_objective_status"])

    def test_plan_runtime_repair_detects_docker_env_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
            incident = summarize_failure_incident(
                command="docker compose up",
                stderr=(
                    'WARN[0000] The "OPENCLAW_CONFIG_DIR" variable is not set. Defaulting to a blank string.\n'
                    'WARN[0000] The "OPENCLAW_WORKSPACE_DIR" variable is not set. Defaulting to a blank string.\n'
                    "invalid spec: :/home/node/.openclaw: empty section between colons\n"
                ),
            )
            plan = plan_runtime_repair(
                repo_name="openclaw",
                project_dir=project_dir,
                incident=incident,
            )
            self.assertEqual("docker_env_repair", plan.action_key)
            self.assertIn("docker compose up", plan.repair_command or "")
            self.assertTrue(any(name == "OPENCLAW_CONFIG_DIR" for name, _value in plan.repair_env_vars))

    def test_handle_repo_run_action_skips_repeat_prompt_for_preapproved_objective(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "openclaw",
                                "repo_url": "https://example.com/openclaw",
                                "stars": 1234,
                                "description": "Openclaw.",
                                "category": "Agents",
                                "framework": "Docker",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            write_workflow_state(
                paths.config_dir,
                {
                    "active_objective_kind": "runtime_repair",
                    "active_objective_repo_key": "https://example.com/openclaw",
                    "active_objective_repo_name": "openclaw",
                    "active_objective_status": "active",
                    "active_objective_requires_user_decision": False,
                },
            )
            displayed: list[str] = []
            with patch("duckln.main.run_prepared_repo") as run_mock:
                run_mock.return_value.message = "Supervisor agent started openclaw successfully."
                run_mock.return_value.verification_passed = True
                run_mock.return_value.message_already_displayed = False
                _handle_repo_run_action(
                    reply=FreeTextReply(text="", intent="repo_run", action="run_repo", action_repo_key="https://example.com/openclaw"),
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=lambda _prompt: (_ for _ in ()).throw(AssertionError("approve_prompt should not be called")),
                    text_prompt=None,
                    terminal_interface=FakeTerminalInterface(),
                )
            workflow = read_workflow_state(paths.config_dir)
            self.assertNotIn("active_repair_phase", workflow)

    def test_pending_repo_followup_continues_pending_run_step(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            write_followup_state(
                paths.config_dir,
                {
                    "pending_next_action": "run_repo",
                    "pending_repo_key": "https://example.com/whisper",
                    "pending_repo_name": "whisper",
                },
            )
            displayed: list[str] = []

            with patch("duckln.main._handle_repo_run_action") as run_mock:
                handled = _handle_pending_repo_followup(
                    command="yes fix it",
                    current=current,
                    paths=paths,
                    system_probe=None,
                    display_output=displayed.append,
                    approve_prompt=lambda _prompt: True,
                    text_prompt=lambda _message, default="": default,
                    select_prompt=None,
                    terminal_interface=FakeTerminalInterface(),
                )

            self.assertTrue(handled)
            run_mock.assert_called_once()
            self.assertTrue(any("continuing the pending run repo step" in message.lower() for message in displayed))

    def test_pending_repo_followup_retries_vm_setup_after_vm_create_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "openclaw",
                                "repo_url": "https://example.com/openclaw",
                                "stars": 75000,
                                "description": "Agent framework.",
                                "category": "Agent",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            write_followup_state(
                paths.config_dir,
                {
                    "pending_next_action": "set_up_repo",
                    "pending_repo_key": "https://example.com/openclaw",
                    "pending_repo_name": "openclaw",
                    "last_completed_step": "vm_create_failed",
                },
            )
            displayed: list[str] = []

            with patch("duckln.main.create_multipass_vm") as create_mock, patch(
                "duckln.main.configure_existing_multipass_vm"
            ) as configure_mock, patch("duckln.main.bring_up_selected_repo") as bringup_mock:
                create_mock.return_value = SimpleNamespace(ok=True, vm_name="duckln-vm")
                handled = _handle_pending_repo_followup(
                    command="fix it",
                    current=current,
                    paths=paths,
                    system_probe=None,
                    display_output=displayed.append,
                    approve_prompt=lambda _prompt: True,
                    text_prompt=lambda _message, default="": default,
                    select_prompt=lambda _message, choices: choices[0] if choices else None,
                    terminal_interface=FakeTerminalInterface(),
                )

            self.assertTrue(handled)
            create_mock.assert_called_once()
            configure_mock.assert_called_once()
            bringup_mock.assert_called_once()
            self.assertTrue(any("continuing the vm setup path" in message.lower() for message in displayed))

    def test_repos_command_requires_explicit_environment_selection_before_setup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            prompts: list[str] = []

            def select_prompt(prompt, choices):
                prompts.append(prompt)
                if "Select a repository" in prompt:
                    return next(choice for choice in choices if choice.startswith("whisper "))
                return "Cancel"

            with patch("duckln.main.bring_up_selected_repo") as bringup_mock:
                handle_session_command(
                    "/repos",
                    current,
                    paths,
                    select=select_prompt,
                    display=lambda _message: None,
                )

            bringup_mock.assert_not_called()
            self.assertTrue(any("Where should Duckln set up whisper?" in prompt for prompt in prompts))

    def test_pending_repo_followup_uses_persisted_execution_target_instead_of_snapshot_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            write_config_snapshot(paths.config_dir, {"execution_target": "local"})
            write_followup_state(
                paths.config_dir,
                {
                    "pending_next_action": "set_up_repo",
                    "pending_repo_key": "https://example.com/whisper",
                    "pending_repo_name": "whisper",
                    "active_execution_target": "vm",
                    "active_vm_name": "duckln-vm",
                },
            )

            with patch("duckln.main.bring_up_selected_repo") as bringup_mock:
                handled = _handle_pending_repo_followup(
                    command="fix it",
                    current=current,
                    paths=paths,
                    system_probe=None,
                    display_output=lambda _message: None,
                    approve_prompt=lambda _prompt: True,
                    text_prompt=lambda _message, default="": default,
                    select_prompt=lambda _message, choices: choices[0] if choices else None,
                    terminal_interface=FakeTerminalInterface(),
                )

            self.assertTrue(handled)
            self.assertEqual("vm", bringup_mock.call_args.kwargs["execution_target"])

    def test_free_text_records_routing_decision_in_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)

            _respond_to_free_text("do we have any active repo?", config_dir=config_dir)

            records = initialize_state_store(config_dir).list_routing_decisions(limit=5)

            self.assertTrue(records)
            latest = records[0]
            self.assertEqual("repo_state", latest.top_level_category)
            self.assertIn(latest.final_route_family, {"repo_status", "repo_inventory", "clarify"})
            self.assertIn("do we have any active repo", latest.normalized_text)

    def test_run_runtime_repair_workflow_guides_user_for_input_shape_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            store = initialize_state_store(paths.config_dir)
            store.upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path=str(Path(temp_dir) / "projects" / "whisper"),
                status="ready",
                summary="Whisper ready.",
                metadata={
                    "repo_name": "whisper",
                    "manual_command": ".venv/bin/python -m whisper <audio-file> --model base",
                    "run_command": ".venv/bin/python -m whisper --help",
                },
            )
            displayed: list[str] = []

            _run_runtime_repair_workflow(
                repo=RepoCatalogRecord(
                    name="whisper",
                    repo_url="https://example.com/whisper",
                    stars=75000,
                    description="Speech recognition.",
                    category="Audio",
                    framework="Python",
                    last_updated="2026-04-01",
                ),
                current=current,
                paths=paths,
                display_output=displayed.append,
                approve_prompt=lambda _prompt: True,
                terminal_interface=FakeTerminalInterface(),
                run_summary="usage: whisper [-h] [--model MODEL] audio",
                runtime_command_override=".venv/bin/python -m whisper",
                workflow=SimpleNamespace(
                    active_runtime_execution_target="local",
                    active_runtime_cwd=str(Path(temp_dir) / "projects" / "whisper"),
                    active_runtime_vm_name=None,
                    active_runtime_cloud_resource_key=None,
                    active_runtime_cloud_vendor=None,
                    active_runtime_cloud_region=None,
                    active_runtime_cloud_shape=None,
                ),
            )

            workflow_state = read_workflow_state(paths.config_dir)
            self.assertEqual("runtime_input_issue", workflow_state["active_issue_kind"])
            self.assertTrue(any("command-shape or input issue" in message.lower() for message in displayed))

    def test_run_runtime_repair_workflow_prefers_repo_dependency_repair_before_broad_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            project_dir = Path(temp_dir) / "projects" / "demo"
            project_dir.mkdir(parents=True, exist_ok=True)
            (project_dir / "requirements.txt").write_text("fastapi==0.111.0\nuvicorn==0.30.0\n", encoding="utf-8")
            displayed: list[str] = []
            captured_requests: list[DependencyApprovalRequest] = []

            class RuntimeRepairApprover:
                def __call__(self, _prompt: str) -> bool:
                    return True

                def approve_dependency_install(self, request: DependencyApprovalRequest) -> DependencyApprovalDecision:
                    captured_requests.append(request)
                    return DependencyApprovalDecision(
                        approved=True,
                        approve_all=True,
                        selected_item_ids=tuple(item.item_id for item in request.items),
                    )

            class SuccessfulRunner:
                def __init__(self, *args, **kwargs) -> None:
                    pass

                def run(self, command: str, cwd: str | None = None):
                    return SimpleNamespace(
                        exit_code=0,
                        stdout="installed",
                        stderr="",
                        timed_out=False,
                    )

            with (
                patch("duckln.main.ControlledCommandRunner", SuccessfulRunner),
                patch("duckln.main._run_bounded_runtime_check", return_value=(True, "Supervisor agent verified demo after dependency repair.")) as rerun_mock,
                patch("duckln.main.bring_up_selected_repo") as repair_mock,
            ):
                _run_runtime_repair_workflow(
                    repo=RepoCatalogRecord(
                        name="demo",
                        repo_url="https://example.com/demo",
                        stars=50,
                        description="Demo repo.",
                        category="Python",
                        framework="Python",
                        last_updated="2026-04-01",
                    ),
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=RuntimeRepairApprover(),
                    terminal_interface=FakeTerminalInterface(),
                    run_summary="Traceback (most recent call last): ModuleNotFoundError: No module named 'fastapi'",
                    runtime_command_override=".venv/bin/python app.py",
                    workflow=SimpleNamespace(
                        active_runtime_execution_target="local",
                        active_runtime_cwd=str(project_dir),
                        active_runtime_vm_name=None,
                        active_runtime_cloud_resource_key=None,
                        active_runtime_cloud_vendor=None,
                        active_runtime_cloud_region=None,
                        active_runtime_cloud_shape=None,
                    ),
                )

            self.assertTrue(captured_requests)
            self.assertEqual(".venv/bin/python -m pip install -r requirements.txt", captured_requests[0].command)
            rerun_mock.assert_called_once()
            repair_mock.assert_not_called()
            self.assertTrue(any("runtime repair planning" in message.lower() for message in displayed))
            self.assertTrue(any("runtime dependency repair review" in message.lower() for message in displayed))
            self.assertTrue(any("repaired the repo dependencies" in message.lower() for message in displayed))

    def test_run_runtime_repair_workflow_surfaces_auth_guidance_before_broad_repair_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            displayed: list[str] = []
            store = initialize_state_store(paths.config_dir)
            store.upsert_repo_state(
                repo_key="https://example.com/secure-demo",
                repo_url="https://example.com/secure-demo",
                repo_path=str(Path(temp_dir) / "projects" / "secure-demo"),
                status="ready",
                summary="Secure demo ready.",
                metadata={
                    "repo_name": "secure-demo",
                    "missing_auth_variables": ["OPENAI_API_KEY"],
                },
            )

            def fake_repair_evidence(**kwargs):
                displayed.append("Duckln checked current official docs for this blocker.")
                return SimpleNamespace(note="docs", source_urls=("https://platform.openai.com/docs",), search_query="openai api key missing")

            with (
                patch("duckln.main._show_runtime_repair_evidence", side_effect=fake_repair_evidence),
                patch("duckln.main.bring_up_selected_repo") as repair_mock,
            ):
                _run_runtime_repair_workflow(
                    repo=RepoCatalogRecord(
                        name="secure-demo",
                        repo_url="https://example.com/secure-demo",
                        stars=50,
                        description="Secure demo repo.",
                        category="Python",
                        framework="Python",
                        last_updated="2026-04-01",
                    ),
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=lambda _prompt: True,
                    terminal_interface=FakeTerminalInterface(),
                    run_summary="Authentication failed: missing OPENAI_API_KEY",
                    runtime_command_override=".venv/bin/python app.py",
                    workflow=SimpleNamespace(
                        active_runtime_execution_target="local",
                        active_runtime_cwd=str(Path(temp_dir) / "projects" / "secure-demo"),
                        active_runtime_vm_name=None,
                        active_runtime_cloud_resource_key=None,
                        active_runtime_cloud_vendor=None,
                        active_runtime_cloud_region=None,
                        active_runtime_cloud_shape=None,
                    ),
                )

            repair_mock.assert_not_called()
            workflow_state = read_workflow_state(paths.config_dir)
            self.assertEqual("auth_guidance", workflow_state["active_repair_phase"])
            self.assertTrue(any("will not silently copy or invent credentials" in message.lower() for message in displayed))
            self.assertTrue(any("checked current official docs" in message.lower() for message in displayed))

    def test_run_runtime_repair_workflow_rechecks_auth_when_env_is_now_present(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            displayed: list[str] = []
            store = initialize_state_store(paths.config_dir)
            store.upsert_repo_state(
                repo_key="https://example.com/secure-demo",
                repo_url="https://example.com/secure-demo",
                repo_path=str(Path(temp_dir) / "projects" / "secure-demo"),
                status="ready",
                summary="Secure demo ready.",
                metadata={
                    "repo_name": "secure-demo",
                    "missing_auth_variables": ["OPENAI_API_KEY"],
                    "auth_requirements": [{"env_var": "OPENAI_API_KEY", "provider": "OpenAI"}],
                },
            )

            with (
                patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}, clear=False),
                patch("duckln.main._run_bounded_runtime_check", return_value=(True, "Supervisor agent verified secure-demo after auth re-check.")) as rerun_mock,
                patch("duckln.main.bring_up_selected_repo") as repair_mock,
            ):
                _run_runtime_repair_workflow(
                    repo=RepoCatalogRecord(
                        name="secure-demo",
                        repo_url="https://example.com/secure-demo",
                        stars=50,
                        description="Secure demo repo.",
                        category="Python",
                        framework="Python",
                        last_updated="2026-04-01",
                    ),
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=lambda _prompt: True,
                    terminal_interface=FakeTerminalInterface(),
                    run_summary="Authentication failed: missing OPENAI_API_KEY",
                    runtime_command_override=".venv/bin/python app.py",
                    workflow=SimpleNamespace(
                        active_runtime_execution_target="local",
                        active_runtime_cwd=str(Path(temp_dir) / "projects" / "secure-demo"),
                        active_runtime_vm_name=None,
                        active_runtime_cloud_resource_key=None,
                        active_runtime_cloud_vendor=None,
                        active_runtime_cloud_region=None,
                        active_runtime_cloud_shape=None,
                    ),
                )

            rerun_mock.assert_called_once()
            repair_mock.assert_not_called()
            self.assertTrue(any("required auth for secure-demo is now present" in message.lower() for message in displayed))

    def test_run_runtime_repair_workflow_rechecks_cloud_auth_before_broad_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OLLAMA,
                model="gemma2:2b",
                api_key="unused",
                mode=ControlMode.HOTL,
            )
            displayed: list[str] = []

            with (
                patch(
                    "duckln.main.inspect_cloud_auth",
                    return_value=SimpleNamespace(
                        provider="aws",
                        cli_name="aws",
                        cli_available=True,
                        authenticated=True,
                        account_label="arn:aws:iam::123456789012:user/test",
                        default_region_or_zone="us-east-1",
                        project_label=None,
                        message="AWS authentication looks ready.",
                        source_url="https://docs.aws.amazon.com/cli/latest/reference/sts/get-caller-identity.html",
                    ),
                ) as auth_mock,
                patch("duckln.main._show_runtime_repair_evidence", return_value=SimpleNamespace(note="docs", source_urls=(), search_query=None)),
                patch("duckln.main._run_bounded_runtime_check", return_value=(True, "Supervisor agent verified cloud-demo after cloud auth re-check.")) as rerun_mock,
                patch("duckln.main.bring_up_selected_repo") as repair_mock,
            ):
                _run_runtime_repair_workflow(
                    repo=RepoCatalogRecord(
                        name="cloud-demo",
                        repo_url="https://example.com/cloud-demo",
                        stars=50,
                        description="Cloud demo repo.",
                        category="Cloud",
                        framework="Python",
                        last_updated="2026-04-01",
                    ),
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=lambda _prompt: True,
                    terminal_interface=FakeTerminalInterface(),
                    run_summary="Unable to locate credentials for the AWS runtime.",
                    runtime_command_override="python app.py",
                    workflow=SimpleNamespace(
                        active_runtime_execution_target="aws",
                        active_runtime_cwd=str(Path(temp_dir) / "projects" / "cloud-demo"),
                        active_runtime_vm_name=None,
                        active_runtime_cloud_resource_key="cloud:aws-demo",
                        active_runtime_cloud_vendor="AWS",
                        active_runtime_cloud_region="us-east-1",
                        active_runtime_cloud_shape="t3.large",
                    ),
                )

            auth_mock.assert_called_once()
            rerun_mock.assert_called_once()
            repair_mock.assert_not_called()
            self.assertTrue(any("auth is ready again and will retry" in message.lower() for message in displayed))

    def test_run_runtime_repair_workflow_recovers_vm_before_broad_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            displayed: list[str] = []

            class VmRecoveryRunner:
                def __init__(self, *args, **kwargs) -> None:
                    self.commands: list[str] = []

                def run(self, command: str, cwd: str | None = None):
                    self.commands.append(command)
                    if command.startswith("multipass info "):
                        return SimpleNamespace(exit_code=0, stdout=json.dumps({"info": {"duckln-vm": {"state": "Running"}}}), stderr="", timed_out=False)
                    return SimpleNamespace(exit_code=0, stdout="ok", stderr="", timed_out=False)

            with (
                patch("duckln.main.is_multipass_installed", return_value=True),
                patch("duckln.main.list_multipass_vm_names", return_value=("duckln-vm",)),
                patch("duckln.main.ControlledCommandRunner", VmRecoveryRunner),
                patch("duckln.main._show_runtime_repair_evidence", return_value=SimpleNamespace(note="docs", source_urls=(), search_query=None)),
                patch("duckln.main._run_bounded_runtime_check", return_value=(True, "Supervisor agent verified vm-demo after VM recovery.")) as rerun_mock,
                patch("duckln.main.bring_up_selected_repo") as repair_mock,
            ):
                _run_runtime_repair_workflow(
                    repo=RepoCatalogRecord(
                        name="vm-demo",
                        repo_url="https://example.com/vm-demo",
                        stars=50,
                        description="VM demo repo.",
                        category="Infra",
                        framework="Python",
                        last_updated="2026-04-01",
                    ),
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=lambda _prompt: True,
                    terminal_interface=FakeTerminalInterface(),
                    run_summary="multipass launch failed: cloud-init status check timed out",
                    runtime_command_override="python app.py",
                    workflow=SimpleNamespace(
                        active_runtime_execution_target="vm",
                        active_runtime_cwd=str(Path(temp_dir) / "projects" / "vm-demo"),
                        active_runtime_vm_name="duckln-vm",
                        active_runtime_cloud_resource_key=None,
                        active_runtime_cloud_vendor=None,
                        active_runtime_cloud_region=None,
                        active_runtime_cloud_shape=None,
                    ),
                )

            rerun_mock.assert_called_once()
            repair_mock.assert_not_called()
            self.assertTrue(any("verified the tracked vm duckln-vm" in message.lower() for message in displayed))

    def test_run_runtime_repair_workflow_verifies_python_dependency_contract_before_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            project_dir = Path(temp_dir) / "projects" / "demo-python"
            project_dir.mkdir(parents=True, exist_ok=True)
            (project_dir / "requirements.txt").write_text("fastapi==0.111.0\nuvicorn==0.30.0\n", encoding="utf-8")
            displayed: list[str] = []
            captured_requests: list[DependencyApprovalRequest] = []

            class RuntimeRepairApprover:
                def __call__(self, _prompt: str) -> bool:
                    return True

                def approve_dependency_install(self, request: DependencyApprovalRequest) -> DependencyApprovalDecision:
                    captured_requests.append(request)
                    return DependencyApprovalDecision(
                        approved=True,
                        approve_all=True,
                        selected_item_ids=tuple(item.item_id for item in request.items),
                    )

            class PythonRunner:
                commands: list[str] = []

                def __init__(self, *args, **kwargs) -> None:
                    pass

                def run(self, command: str, cwd: str | None = None):
                    PythonRunner.commands.append(command)
                    return SimpleNamespace(exit_code=0, stdout="ok", stderr="", timed_out=False)

            with (
                patch("duckln.main.ControlledCommandRunner", PythonRunner),
                patch("duckln.main._run_bounded_runtime_check", return_value=(True, "Supervisor agent verified demo-python after python repair.")) as rerun_mock,
                patch("duckln.main.bring_up_selected_repo") as repair_mock,
            ):
                _run_runtime_repair_workflow(
                    repo=RepoCatalogRecord(
                        name="demo-python",
                        repo_url="https://example.com/demo-python",
                        stars=50,
                        description="Python demo repo.",
                        category="Python",
                        framework="Python",
                        last_updated="2026-04-01",
                    ),
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=RuntimeRepairApprover(),
                    terminal_interface=FakeTerminalInterface(),
                    run_summary="Traceback (most recent call last): ModuleNotFoundError: No module named 'fastapi'",
                    runtime_command_override=".venv/bin/python -m uvicorn app:app --reload",
                    workflow=SimpleNamespace(
                        active_runtime_execution_target="local",
                        active_runtime_cwd=str(project_dir),
                        active_runtime_vm_name=None,
                        active_runtime_cloud_resource_key=None,
                        active_runtime_cloud_vendor=None,
                        active_runtime_cloud_region=None,
                        active_runtime_cloud_shape=None,
                    ),
                )

            rerun_mock.assert_called_once()
            repair_mock.assert_not_called()
            self.assertGreaterEqual(len(captured_requests), 1)
            self.assertEqual(".venv/bin/python -m pip install -r requirements.txt", captured_requests[0].command)
            self.assertIn('.venv/bin/python -c "import fastapi"', PythonRunner.commands)
            self.assertTrue(any("python dependency verification" in message.lower() for message in displayed))

    def test_run_runtime_repair_workflow_repairs_node_toolchain_before_repo_dependency_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            project_dir = Path(temp_dir) / "projects" / "demo-node"
            project_dir.mkdir(parents=True, exist_ok=True)
            (project_dir / "package.json").write_text(json.dumps({"dependencies": {"next": "14.2.0"}}), encoding="utf-8")
            (project_dir / "package-lock.json").write_text("{}", encoding="utf-8")
            displayed: list[str] = []
            captured_requests: list[DependencyApprovalRequest] = []

            class RuntimeRepairApprover:
                def __call__(self, _prompt: str) -> bool:
                    return True

                def approve_dependency_install(self, request: DependencyApprovalRequest) -> DependencyApprovalDecision:
                    captured_requests.append(request)
                    return DependencyApprovalDecision(
                        approved=True,
                        approve_all=True,
                        selected_item_ids=tuple(item.item_id for item in request.items),
                    )

            class NodeToolchainRunner:
                commands: list[str] = []
                node_installed = False

                def __init__(self, *args, **kwargs) -> None:
                    pass

                def run(self, command: str, cwd: str | None = None):
                    NodeToolchainRunner.commands.append(command)
                    normalized = " ".join(command.split())
                    if normalized == "node --version && npm --version":
                        return SimpleNamespace(exit_code=1, stdout="", stderr="node: command not found", timed_out=False)
                    if "process.versions.node" in normalized and not NodeToolchainRunner.node_installed:
                        return SimpleNamespace(exit_code=1, stdout="", stderr="node: command not found", timed_out=False)
                    if normalized == "brew install node":
                        NodeToolchainRunner.node_installed = True
                    return SimpleNamespace(exit_code=0, stdout="ok", stderr="", timed_out=False)

            with (
                patch("duckln.main.ControlledCommandRunner", NodeToolchainRunner),
                patch("duckln.main._run_bounded_runtime_check", return_value=(True, "Supervisor agent verified demo-node after node repair.")) as rerun_mock,
                patch("duckln.main.bring_up_selected_repo") as repair_mock,
            ):
                _run_runtime_repair_workflow(
                    repo=RepoCatalogRecord(
                        name="demo-node",
                        repo_url="https://example.com/demo-node",
                        stars=50,
                        description="Node demo repo.",
                        category="Node",
                        framework="Node",
                        last_updated="2026-04-01",
                    ),
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=RuntimeRepairApprover(),
                    terminal_interface=FakeTerminalInterface(),
                    run_summary="npm ERR! Cannot find module 'next/dist/server'",
                    runtime_command_override="npm run dev",
                    workflow=SimpleNamespace(
                        active_runtime_execution_target="local",
                        active_runtime_cwd=str(project_dir),
                        active_runtime_vm_name=None,
                        active_runtime_cloud_resource_key=None,
                        active_runtime_cloud_vendor=None,
                        active_runtime_cloud_region=None,
                        active_runtime_cloud_shape=None,
                    ),
                )

            rerun_mock.assert_called_once()
            repair_mock.assert_not_called()
            self.assertGreaterEqual(len(captured_requests), 2)
            self.assertEqual("brew install node", captured_requests[0].command)
            self.assertEqual("npm ci", captured_requests[1].command)
            self.assertTrue(any("stack toolchain review" in message.lower() for message in displayed))
            self.assertTrue(any("runtime dependency repair review" in message.lower() for message in displayed))
            self.assertTrue(any("stack dependency verification" in message.lower() for message in displayed))
            self.assertIn("npm ls --depth=0", NodeToolchainRunner.commands)

    def test_run_runtime_repair_workflow_verifies_rust_dependency_contract_before_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            project_dir = Path(temp_dir) / "projects" / "demo-rust"
            project_dir.mkdir(parents=True, exist_ok=True)
            (project_dir / "Cargo.toml").write_text("[package]\nname='demo-rust'\nversion='0.1.0'\n", encoding="utf-8")
            (project_dir / "Cargo.lock").write_text("# lock\n", encoding="utf-8")
            displayed: list[str] = []
            captured_requests: list[DependencyApprovalRequest] = []

            class RuntimeRepairApprover:
                def __call__(self, _prompt: str) -> bool:
                    return True

                def approve_dependency_install(self, request: DependencyApprovalRequest) -> DependencyApprovalDecision:
                    captured_requests.append(request)
                    return DependencyApprovalDecision(
                        approved=True,
                        approve_all=True,
                        selected_item_ids=tuple(item.item_id for item in request.items),
                    )

            class RustRunner:
                commands: list[str] = []

                def __init__(self, *args, **kwargs) -> None:
                    pass

                def run(self, command: str, cwd: str | None = None):
                    RustRunner.commands.append(command)
                    return SimpleNamespace(exit_code=0, stdout="ok", stderr="", timed_out=False)

            with (
                patch("duckln.main.ControlledCommandRunner", RustRunner),
                patch("duckln.main._run_bounded_runtime_check", return_value=(True, "Supervisor agent verified demo-rust after rust repair.")) as rerun_mock,
                patch("duckln.main.bring_up_selected_repo") as repair_mock,
            ):
                _run_runtime_repair_workflow(
                    repo=RepoCatalogRecord(
                        name="demo-rust",
                        repo_url="https://example.com/demo-rust",
                        stars=50,
                        description="Rust demo repo.",
                        category="Rust",
                        framework="Rust",
                        last_updated="2026-04-01",
                    ),
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=RuntimeRepairApprover(),
                    terminal_interface=FakeTerminalInterface(),
                    run_summary="error: failed to compile crate `demo-rust`",
                    runtime_command_override="cargo run",
                    workflow=SimpleNamespace(
                        active_runtime_execution_target="local",
                        active_runtime_cwd=str(project_dir),
                        active_runtime_vm_name=None,
                        active_runtime_cloud_resource_key=None,
                        active_runtime_cloud_vendor=None,
                        active_runtime_cloud_region=None,
                        active_runtime_cloud_shape=None,
                    ),
                )

            rerun_mock.assert_called_once()
            repair_mock.assert_not_called()
            self.assertGreaterEqual(len(captured_requests), 1)
            self.assertEqual("cargo build", captured_requests[0].command)
            self.assertIn("cargo check --locked", RustRunner.commands)
            self.assertTrue(any("stack dependency verification" in message.lower() for message in displayed))

    def test_run_runtime_repair_workflow_verifies_go_dependency_contract_before_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            project_dir = Path(temp_dir) / "projects" / "demo-go"
            project_dir.mkdir(parents=True, exist_ok=True)
            (project_dir / "go.mod").write_text("module example.com/demo-go\n\ngo 1.22\n", encoding="utf-8")
            displayed: list[str] = []
            captured_requests: list[DependencyApprovalRequest] = []

            class RuntimeRepairApprover:
                def __call__(self, _prompt: str) -> bool:
                    return True

                def approve_dependency_install(self, request: DependencyApprovalRequest) -> DependencyApprovalDecision:
                    captured_requests.append(request)
                    return DependencyApprovalDecision(
                        approved=True,
                        approve_all=True,
                        selected_item_ids=tuple(item.item_id for item in request.items),
                    )

            class GoRunner:
                commands: list[str] = []

                def __init__(self, *args, **kwargs) -> None:
                    pass

                def run(self, command: str, cwd: str | None = None):
                    GoRunner.commands.append(command)
                    return SimpleNamespace(exit_code=0, stdout="ok", stderr="", timed_out=False)

            with (
                patch("duckln.main.ControlledCommandRunner", GoRunner),
                patch("duckln.main._run_bounded_runtime_check", return_value=(True, "Supervisor agent verified demo-go after go repair.")) as rerun_mock,
                patch("duckln.main.bring_up_selected_repo") as repair_mock,
            ):
                _run_runtime_repair_workflow(
                    repo=RepoCatalogRecord(
                        name="demo-go",
                        repo_url="https://example.com/demo-go",
                        stars=50,
                        description="Go demo repo.",
                        category="Go",
                        framework="Go",
                        last_updated="2026-04-01",
                    ),
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=RuntimeRepairApprover(),
                    terminal_interface=FakeTerminalInterface(),
                    run_summary="go: no required module provides package github.com/example/missing",
                    runtime_command_override="go run main.go",
                    workflow=SimpleNamespace(
                        active_runtime_execution_target="local",
                        active_runtime_cwd=str(project_dir),
                        active_runtime_vm_name=None,
                        active_runtime_cloud_resource_key=None,
                        active_runtime_cloud_vendor=None,
                        active_runtime_cloud_region=None,
                        active_runtime_cloud_shape=None,
                    ),
                )

            rerun_mock.assert_called_once()
            repair_mock.assert_not_called()
            self.assertGreaterEqual(len(captured_requests), 1)
            self.assertEqual("go mod download", captured_requests[0].command)
            self.assertIn("go build ./...", GoRunner.commands)
            self.assertTrue(any("stack dependency verification" in message.lower() for message in displayed))

    def test_run_runtime_repair_workflow_bootstraps_cloud_runtime_before_repo_repair(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            project_dir = Path(temp_dir) / "projects" / "cloud-node"
            project_dir.mkdir(parents=True, exist_ok=True)
            (project_dir / "package.json").write_text(json.dumps({"dependencies": {"next": "14.2.0"}}), encoding="utf-8")
            (project_dir / "package-lock.json").write_text("{}", encoding="utf-8")
            displayed: list[str] = []

            class RuntimeRepairApprover:
                def __call__(self, _prompt: str) -> bool:
                    return True

                def approve_dependency_install(self, request: DependencyApprovalRequest) -> DependencyApprovalDecision:
                    return DependencyApprovalDecision(
                        approved=True,
                        approve_all=True,
                        selected_item_ids=tuple(item.item_id for item in request.items),
                    )

            class CloudBootstrapRunner:
                commands: list[str] = []

                def __init__(self, *args, **kwargs) -> None:
                    pass

                def run(self, command: str, cwd: str | None = None):
                    CloudBootstrapRunner.commands.append(command)
                    return SimpleNamespace(exit_code=0, stdout="ok", stderr="", timed_out=False)

            record = SimpleNamespace(
                resource_key="cloud:aws-demo",
                provider="aws",
                display_name="duckln-aws-demo",
                region="us-east-1",
                shape="t3.large",
                metadata={"connect_command": "ssh aws-demo"},
            )

            with (
                patch("duckln.main.resolve_managed_resource", return_value=record),
                patch("duckln.main.build_cloud_remote_exec_command", return_value="ssh aws-demo 'bootstrap'"),
                patch("duckln.main.record_managed_resource_activity"),
                patch("duckln.main.ControlledCommandRunner", CloudBootstrapRunner),
                patch("duckln.main._attempt_runtime_dependency_repair", return_value="installed"),
                patch("duckln.main._run_bounded_runtime_check", return_value=(True, "Supervisor agent verified cloud-node after cloud bootstrap.")) as rerun_mock,
                patch("duckln.main.bring_up_selected_repo") as repair_mock,
            ):
                _run_runtime_repair_workflow(
                    repo=RepoCatalogRecord(
                        name="cloud-node",
                        repo_url="https://example.com/cloud-node",
                        stars=50,
                        description="Cloud node repo.",
                        category="Node",
                        framework="Node",
                        last_updated="2026-04-01",
                    ),
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=RuntimeRepairApprover(),
                    terminal_interface=FakeTerminalInterface(),
                    run_summary="npm ERR! Cannot find module 'next/dist/server'",
                    runtime_command_override="npm run dev",
                    workflow=SimpleNamespace(
                        active_runtime_execution_target="aws",
                        active_runtime_cwd=str(project_dir),
                        active_runtime_vm_name=None,
                        active_runtime_cloud_resource_key="cloud:aws-demo",
                        active_runtime_cloud_vendor="AWS",
                        active_runtime_cloud_region="us-east-1",
                        active_runtime_cloud_shape="t3.large",
                    ),
                )

            rerun_mock.assert_called_once()
            repair_mock.assert_not_called()
            self.assertTrue(any("cloud runtime bootstrap review" in message.lower() for message in displayed))
            self.assertTrue(any("repaired the tracked cloud runtime path" in message.lower() for message in displayed))

    def test_run_runtime_repair_workflow_verifies_cloud_docker_runtime_services_before_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            project_dir = Path(temp_dir) / "projects" / "cloud-docker"
            project_dir.mkdir(parents=True, exist_ok=True)
            (project_dir / "compose.yaml").write_text("services:\n  web:\n    image: nginx:latest\n", encoding="utf-8")
            displayed: list[str] = []

            class CloudDockerRunner:
                commands: list[str] = []

                def __init__(self, *args, **kwargs) -> None:
                    pass

                def run(self, command: str, cwd: str | None = None):
                    CloudDockerRunner.commands.append(command)
                    return SimpleNamespace(exit_code=0, stdout="ok", stderr="", timed_out=False)

            with (
                patch("duckln.main._attempt_runtime_cloud_bootstrap_repair", return_value="bootstrapped"),
                patch("duckln.main._wrap_command_for_execution_target", side_effect=lambda **kwargs: (f"wrapped::{kwargs['command']}", None)),
                patch("duckln.main.ControlledCommandRunner", CloudDockerRunner),
                patch("duckln.main._attempt_runtime_dependency_repair", return_value="installed"),
                patch("duckln.main._run_bounded_runtime_check", return_value=(True, "Supervisor agent verified cloud-docker after docker repair.")) as rerun_mock,
                patch("duckln.main.bring_up_selected_repo") as repair_mock,
            ):
                _run_runtime_repair_workflow(
                    repo=RepoCatalogRecord(
                        name="cloud-docker",
                        repo_url="https://example.com/cloud-docker",
                        stars=50,
                        description="Cloud docker repo.",
                        category="Docker",
                        framework="Docker",
                        last_updated="2026-04-01",
                    ),
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=lambda _prompt: True,
                    terminal_interface=FakeTerminalInterface(),
                    run_summary="docker compose failed: no such service: web",
                    runtime_command_override="docker compose up",
                    workflow=SimpleNamespace(
                        active_runtime_execution_target="aws",
                        active_runtime_cwd=str(project_dir),
                        active_runtime_vm_name=None,
                        active_runtime_cloud_resource_key="cloud:aws-demo",
                        active_runtime_cloud_vendor="AWS",
                        active_runtime_cloud_region="us-east-1",
                        active_runtime_cloud_shape="t3.large",
                    ),
                )

            rerun_mock.assert_called_once()
            repair_mock.assert_not_called()
            self.assertTrue(any("cloud runtime service review" in message.lower() for message in displayed))
            self.assertTrue(any("tracked cloud docker/runtime services are ready" in message.lower() for message in displayed))
            self.assertIn("wrapped::docker --version && docker compose version && docker compose config >/dev/null", CloudDockerRunner.commands)

    def test_run_runtime_repair_workflow_uses_live_dependency_approval_panel_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            project_dir = Path(temp_dir) / "projects" / "demo"
            project_dir.mkdir(parents=True, exist_ok=True)
            (project_dir / "requirements.txt").write_text("fastapi==0.111.0\nuvicorn==0.30.0\n", encoding="utf-8")
            displayed: list[str] = []
            captured_requests: list[DependencyApprovalRequest] = []

            class FakeLiveChat:
                supports_live = True

                def approve_dependency_plan(self, request: DependencyApprovalRequest) -> DependencyApprovalDecision:
                    captured_requests.append(request)
                    return DependencyApprovalDecision(
                        approved=True,
                        approve_all=True,
                        selected_item_ids=tuple(item.item_id for item in request.items),
                    )

                def confirm_choice(self, message: str, *, default: bool = True) -> bool:
                    raise AssertionError("generic confirm should not be used for runtime dependency approvals")

            live_prompt = _build_chat_approve_prompt(
                chat=FakeLiveChat(),
                chat_input=lambda _message: "1",
                display=displayed.append,
            )

            class SuccessfulRunner:
                def __init__(self, *args, **kwargs) -> None:
                    pass

                def run(self, command: str, cwd: str | None = None):
                    return SimpleNamespace(
                        exit_code=0,
                        stdout="installed",
                        stderr="",
                        timed_out=False,
                    )

            with (
                patch("duckln.main.ControlledCommandRunner", SuccessfulRunner),
                patch("duckln.main._run_bounded_runtime_check", return_value=(True, "Supervisor agent verified demo after dependency repair.")) as rerun_mock,
                patch("duckln.main.bring_up_selected_repo") as repair_mock,
            ):
                _run_runtime_repair_workflow(
                    repo=RepoCatalogRecord(
                        name="demo",
                        repo_url="https://example.com/demo",
                        stars=50,
                        description="Demo repo.",
                        category="Python",
                        framework="Python",
                        last_updated="2026-04-01",
                    ),
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=live_prompt,
                    terminal_interface=FakeTerminalInterface(),
                    run_summary="Traceback (most recent call last): ModuleNotFoundError: No module named 'fastapi'",
                    runtime_command_override=".venv/bin/python app.py",
                    workflow=SimpleNamespace(
                        active_runtime_execution_target="local",
                        active_runtime_cwd=str(project_dir),
                        active_runtime_vm_name=None,
                        active_runtime_cloud_resource_key=None,
                        active_runtime_cloud_vendor=None,
                        active_runtime_cloud_region=None,
                        active_runtime_cloud_shape=None,
                    ),
                )

            self.assertTrue(captured_requests)
            self.assertEqual("Duckln security review for demo: repair repo dependencies", captured_requests[0].prompt)
            self.assertEqual(".venv/bin/python -m pip install -r requirements.txt", captured_requests[0].command)
            rerun_mock.assert_called_once()
            repair_mock.assert_not_called()

    def test_handle_repo_run_action_can_offer_to_open_repo_endpoint_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "open-webui",
                                "repo_url": "https://example.com/open-webui",
                                "stars": 129200,
                                "description": "A local model UI.",
                                "category": "LLM",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            initialize_state_store(config_dir).upsert_repo_state(
                repo_key="https://example.com/open-webui",
                repo_url="https://example.com/open-webui",
                repo_path=str(config_dir / "projects" / "open-webui"),
                status="running",
                summary="open-webui running.",
                metadata={"repo_name": "open-webui", "access_hint": "Open http://localhost:8080 after startup."},
            )
            reply = _respond_to_free_text("run this repo open-webui", config_dir=config_dir)
            displayed: list[str] = []

            with (
                patch("duckln.main.run_prepared_repo") as run_mock,
                patch("duckln.main.ControlledCommandRunner") as runner_cls,
            ):
                run_mock.return_value.message = "Supervisor agent started open-webui successfully."
                run_mock.return_value.verification_passed = True
                runner_cls.return_value.run.return_value.exit_code = 0
                runner_cls.return_value.run.return_value.timed_out = False
                runner_cls.return_value.run.return_value.stdout = ""
                runner_cls.return_value.run.return_value.stderr = ""
                runner_cls.return_value.run.return_value.command = "open 'http://localhost:8080'"
                runner_cls.return_value.run.return_value.duration_seconds = 0.1

                approvals = iter([True, True])
                _handle_repo_run_action(
                    reply=reply,
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=lambda _prompt: next(approvals),
                )

            self.assertTrue(any("opened open-webui" in message.lower() for message in displayed))

    def test_handle_repo_run_action_auto_opens_web_preview_inside_duckln_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "open-webui",
                                "repo_url": "https://example.com/open-webui",
                                "stars": 129200,
                                "description": "A local model UI.",
                                "category": "LLM",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            initialize_state_store(config_dir).upsert_repo_state(
                repo_key="https://example.com/open-webui",
                repo_url="https://example.com/open-webui",
                repo_path=str(config_dir / "projects" / "open-webui"),
                status="running",
                summary="open-webui running.",
                metadata={
                    "repo_name": "open-webui",
                    "access_hint": "Open http://localhost:8080 after startup.",
                },
            )
            reply = _respond_to_free_text("run this repo open-webui", config_dir=config_dir)
            displayed: list[str] = []
            prompts: list[str] = []
            terminal = FakeTerminalInterface()

            with patch("duckln.main.run_prepared_repo") as run_mock:
                run_mock.return_value.message = "Supervisor agent started open-webui successfully."
                run_mock.return_value.verification_passed = True
                _handle_repo_run_action(
                    reply=reply,
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=lambda prompt: prompts.append(prompt) or True,
                    terminal_interface=terminal,
                )

            self.assertEqual([("http://localhost:8080", "open-webui")], terminal.preview_requests)
            self.assertEqual(1, len(prompts))
            self.assertTrue(any("managed browser window" in message.lower() for message in displayed))

    def test_handle_repo_run_action_keeps_terminal_handoff_when_runtime_is_terminal_backed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "llama.cpp",
                                "repo_url": "https://example.com/llama.cpp",
                                "stars": 100000,
                                "description": "Local inference.",
                                "category": "LLM",
                                "framework": "C++",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            initialize_state_store(config_dir).upsert_repo_state(
                repo_key="https://example.com/llama.cpp",
                repo_url="https://example.com/llama.cpp",
                repo_path=str(config_dir / "projects" / "llama.cpp"),
                status="running",
                summary="llama.cpp running.",
                metadata={
                    "repo_name": "llama.cpp",
                    "runtime_transport": "terminal_pane",
                    "install_location": str(config_dir / "projects" / "llama.cpp"),
                },
            )
            write_workflow_state(
                config_dir,
                {
                    "active_repo_key": "https://example.com/llama.cpp",
                    "active_repo_name": "llama.cpp",
                    "active_runtime_status": "running",
                    "active_runtime_repo_key": "https://example.com/llama.cpp",
                    "active_runtime_repo_name": "llama.cpp",
                    "active_runtime_cwd": str(config_dir / "projects" / "llama.cpp"),
                },
            )
            reply = _respond_to_free_text("run this repo llama.cpp", config_dir=config_dir)
            displayed: list[str] = []
            terminal = FakeTerminalInterface()

            with patch("duckln.main.run_prepared_repo") as run_mock:
                run_mock.return_value.message = "Supervisor agent started llama.cpp successfully."
                run_mock.return_value.verification_passed = True
                _handle_repo_run_action(
                    reply=reply,
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=lambda _prompt: True,
                    terminal_interface=terminal,
                )

            self.assertEqual([], terminal.run_commands)
            self.assertEqual([], terminal.preview_requests)
            self.assertTrue(any("kept llama.cpp live in duckln’s terminal pane" in message.lower() for message in displayed))

    def test_handle_repo_run_action_auto_attaches_repo_when_runtime_needs_shell_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "custom-alpha",
                                "repo_url": "https://example.com/custom-alpha",
                                "stars": 1200,
                                "description": "Custom repo.",
                                "category": "Tools",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            repo_dir = config_dir / "projects" / "custom-alpha"
            repo_dir.mkdir(parents=True, exist_ok=True)
            initialize_state_store(config_dir).upsert_repo_state(
                repo_key="https://example.com/custom-alpha",
                repo_url="https://example.com/custom-alpha",
                repo_path=str(repo_dir),
                status="running",
                summary="custom-alpha running.",
                metadata={
                    "repo_name": "custom-alpha",
                    "install_location": str(repo_dir),
                    "access_hint": "Use the managed project directory.",
                },
            )
            write_workflow_state(
                config_dir,
                {
                    "active_repo_key": "https://example.com/custom-alpha",
                    "active_repo_name": "custom-alpha",
                    "active_runtime_status": "running",
                    "active_runtime_repo_key": "https://example.com/custom-alpha",
                    "active_runtime_repo_name": "custom-alpha",
                    "active_runtime_cwd": str(repo_dir),
                },
            )
            reply = _respond_to_free_text("run this repo custom-alpha", config_dir=config_dir)
            displayed: list[str] = []
            prompts: list[str] = []
            terminal = FakeTerminalInterface()

            with patch("duckln.main.run_prepared_repo") as run_mock:
                run_mock.return_value.message = "Supervisor agent started custom-alpha successfully."
                run_mock.return_value.verification_passed = True
                _handle_repo_run_action(
                    reply=reply,
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=lambda prompt: prompts.append(prompt) or True,
                    terminal_interface=terminal,
                )

            self.assertEqual(1, len(terminal.run_commands))
            self.assertIn("bash -lc", terminal.run_commands[0][0])
            self.assertEqual(1, len(prompts))
            self.assertTrue(any("opened custom-alpha in duckln’s terminal pane" in message.lower() for message in displayed))

    def test_handle_repo_stop_action_stops_tracked_runtime_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "open-webui",
                                "repo_url": "https://example.com/open-webui",
                                "stars": 129200,
                                "description": "A local model UI.",
                                "category": "LLM",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            config_dir = Path(temp_dir)
            initialize_state_store(config_dir).upsert_repo_state(
                repo_key="https://example.com/open-webui",
                repo_url="https://example.com/open-webui",
                repo_path=str(config_dir / "projects" / "open-webui"),
                status="running",
                summary="open-webui running.",
                metadata={"repo_name": "open-webui", "runtime_pid": 43210},
            )
            write_workflow_state(
                config_dir,
                {
                    "active_repo_key": "https://example.com/open-webui",
                    "active_repo_name": "open-webui",
                    "active_runtime_status": "running",
                    "active_runtime_command": "uvicorn main:app",
                    "active_runtime_command_kind": "start",
                    "active_runtime_repo_key": "https://example.com/open-webui",
                    "active_runtime_repo_name": "open-webui",
                    "active_runtime_cwd": str(config_dir / "projects" / "open-webui"),
                    "active_runtime_pid": 43210,
                },
            )
            displayed: list[str] = []
            reply = FreeTextReply(text="", intent="repo_stop", action="stop_repo", action_repo_key="https://example.com/open-webui")

            with patch("duckln.main.ControlledCommandRunner") as runner_cls:
                runner_cls.return_value.stop_background.return_value = True
                _handle_repo_stop_action(
                    reply=reply,
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=lambda _prompt: True,
                )

            self.assertTrue(any("stopped open-webui successfully" in message.lower() for message in displayed))

    def test_handle_repo_stop_action_interrupts_terminal_backed_runtime_without_pid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 97200,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            config_dir = Path(temp_dir)
            initialize_state_store(config_dir).upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path=str(config_dir / "projects" / "whisper"),
                status="ready",
                summary="whisper staged in terminal.",
                metadata={"repo_name": "whisper"},
            )
            write_workflow_state(
                config_dir,
                {
                    "active_repo_key": "https://example.com/whisper",
                    "active_repo_name": "whisper",
                    "active_runtime_status": "interactive",
                    "active_runtime_command": ".venv/bin/python -m whisper --help",
                    "active_runtime_command_kind": "verification",
                    "active_runtime_repo_key": "https://example.com/whisper",
                    "active_runtime_repo_name": "whisper",
                    "active_runtime_cwd": str(config_dir / "projects" / "whisper"),
                    "active_runtime_stop_hint": "Duckln can interrupt the active terminal-pane work for whisper when you ask.",
                },
            )
            displayed: list[str] = []
            reply = FreeTextReply(text="", intent="repo_stop", action="stop_repo", action_repo_key="https://example.com/whisper")
            terminal = FakeTerminalInterface()

            _handle_repo_stop_action(
                reply=reply,
                current=current,
                paths=paths,
                display_output=displayed.append,
                approve_prompt=lambda _prompt: True,
                terminal_interface=terminal,
            )

            self.assertEqual(1, terminal.interrupt_calls)
            self.assertTrue(any("stopped whisper successfully" in message.lower() for message in displayed))

    def test_free_text_restart_repo_reply_is_grounded_in_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            initialize_state_store(config_dir).upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path=str(config_dir / "projects" / "whisper"),
                status="running",
                summary="whisper running",
                metadata={"repo_name": "whisper", "run_command": ".venv/bin/python -m whisper run"},
            )
            write_workflow_state(
                config_dir,
                {
                    "active_repo_key": "https://example.com/whisper",
                    "active_repo_name": "whisper",
                    "active_runtime_status": "running",
                    "active_runtime_repo_key": "https://example.com/whisper",
                    "active_runtime_repo_name": "whisper",
                    "active_runtime_stop_hint": "Duckln can stop the tracked whisper session first.",
                },
            )

            reply = _respond_to_free_text("restart whisper", config_dir=config_dir)

            self.assertEqual("repo_restart", reply.intent)
            self.assertEqual("restart_repo", reply.action)
            self.assertIn("stop", reply.text.lower())
            self.assertIn("re-running", reply.text.lower())

    def test_handle_repo_restart_action_stops_then_runs_repo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 97200,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            initialize_state_store(config_dir).upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path=str(config_dir / "projects" / "whisper"),
                status="running",
                summary="whisper running",
                metadata={"repo_name": "whisper", "run_command": ".venv/bin/python -m whisper run"},
            )
            write_workflow_state(
                config_dir,
                {
                    "active_repo_key": "https://example.com/whisper",
                    "active_repo_name": "whisper",
                    "active_runtime_status": "running",
                    "active_runtime_command": ".venv/bin/python -m whisper run",
                    "active_runtime_command_kind": "start",
                    "active_runtime_repo_key": "https://example.com/whisper",
                    "active_runtime_repo_name": "whisper",
                    "active_runtime_cwd": str(config_dir / "projects" / "whisper"),
                    "active_runtime_pid": 43210,
                },
            )
            displayed: list[str] = []

            with (
                patch("duckln.main.ControlledCommandRunner") as runner_cls,
                patch("duckln.main.run_prepared_repo") as run_mock,
            ):
                runner_cls.return_value.run.return_value.exit_code = 0
                runner_cls.return_value.run.return_value.timed_out = False
                runner_cls.return_value.stop_background.return_value = True
                run_mock.return_value.message = "Supervisor agent started whisper successfully."
                run_mock.return_value.verification_passed = True

                _handle_repo_restart_action(
                    reply=FreeTextReply(text="", intent="repo_restart", action="restart_repo", action_repo_key="https://example.com/whisper"),
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=lambda _prompt: True,
                )

            self.assertEqual(1, run_mock.call_count)
            self.assertTrue(any("repo restart review" in message.lower() for message in displayed))
            self.assertTrue(any("stopped whisper successfully" in message.lower() for message in displayed))

    def test_handle_repo_restart_action_falls_back_to_fresh_run_without_live_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 97200,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            initialize_state_store(config_dir).upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path=str(config_dir / "projects" / "whisper"),
                status="ready",
                summary="whisper ready",
                metadata={"repo_name": "whisper", "run_command": ".venv/bin/python -m whisper run"},
            )
            displayed: list[str] = []

            with patch("duckln.main.run_prepared_repo") as run_mock:
                run_mock.return_value.message = "Supervisor agent started whisper successfully."
                run_mock.return_value.verification_passed = True

                _handle_repo_restart_action(
                    reply=FreeTextReply(text="", intent="repo_restart", action="restart_repo", action_repo_key="https://example.com/whisper"),
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=lambda _prompt: True,
                )

            self.assertEqual(1, run_mock.call_count)
            self.assertTrue(any("falling back to a fresh bounded run" in message.lower() for message in displayed))

    def test_handle_repo_access_action_opens_local_repo_in_terminal_pane(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            repo_dir = Path(temp_dir) / "projects" / "custom-alpha"
            repo_dir.mkdir(parents=True, exist_ok=True)
            initialize_state_store(paths.config_dir).upsert_repo_state(
                repo_key="https://example.com/custom-alpha",
                repo_url="https://example.com/custom-alpha",
                repo_path=str(repo_dir),
                status="ready",
                summary="custom-alpha ready",
                metadata={
                    "repo_name": "custom-alpha",
                    "install_location": str(repo_dir),
                    "access_hint": "Use the managed project directory.",
                },
            )
            terminal = FakeTerminalInterface()
            displayed: list[str] = []

            _handle_repo_access_action(
                reply=FreeTextReply(text="", intent="repo_access", action="attach_repo", action_repo_key="https://example.com/custom-alpha"),
                current=current,
                paths=paths,
                display_output=displayed.append,
                approve_prompt=lambda _prompt: True,
                terminal_interface=terminal,
            )

            self.assertEqual(1, len(terminal.run_commands))
            self.assertIn("bash -lc", terminal.run_commands[0][0])
            self.assertTrue(any("opened custom-alpha in duckln’s terminal pane" in message.lower() for message in displayed))

    def test_handle_repo_access_action_opens_docker_repo_in_terminal_pane(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            initialize_state_store(paths.config_dir).upsert_repo_state(
                repo_key="https://example.com/open-webui",
                repo_url="https://example.com/open-webui",
                repo_path="/workspace/open-webui",
                execution_target="docker",
                status="running",
                summary="open-webui ready in docker",
                metadata={
                    "repo_name": "open-webui",
                    "install_location": "/workspace/open-webui",
                    "docker_name": "open-webui-stack",
                },
            )
            terminal = FakeTerminalInterface()
            displayed: list[str] = []

            _handle_repo_access_action(
                reply=FreeTextReply(text="", intent="repo_access", action="attach_repo", action_repo_key="https://example.com/open-webui"),
                current=current,
                paths=paths,
                display_output=displayed.append,
                approve_prompt=lambda _prompt: True,
                terminal_interface=terminal,
            )

            self.assertEqual(1, len(terminal.run_commands))
            self.assertIn("docker exec -it", terminal.run_commands[0][0])
            self.assertIn("open-webui-stack", terminal.run_commands[0][0])

    def test_handle_repo_access_action_opens_cloud_repo_in_terminal_pane(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            store = initialize_state_store(paths.config_dir)
            store.upsert_repo_state(
                repo_key="https://example.com/open-webui",
                repo_url="https://example.com/open-webui",
                repo_path="~/.duckln/projects/open-webui",
                execution_target="gcp",
                status="ready",
                summary="open-webui ready in cloud",
                metadata={
                    "repo_name": "open-webui",
                    "install_location": "~/.duckln/projects/open-webui",
                    "cloud_vendor": "GCP",
                    "cloud_region": "us-central1-a",
                    "cloud_resource_key": "gcp:duckln-gcp:us-central1-a",
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
            terminal = FakeTerminalInterface()
            displayed: list[str] = []

            _handle_repo_access_action(
                reply=FreeTextReply(text="", intent="repo_access", action="attach_repo", action_repo_key="https://example.com/open-webui"),
                current=current,
                paths=paths,
                display_output=displayed.append,
                approve_prompt=lambda _prompt: True,
                terminal_interface=terminal,
            )

            self.assertEqual(1, len(terminal.run_commands))
            self.assertIn("gcloud compute ssh duckln-gcp --zone us-central1-a --command", terminal.run_commands[0][0])

    def test_handle_repo_access_action_opens_repo_endpoint_when_url_is_best_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            initialize_state_store(paths.config_dir).upsert_repo_state(
                repo_key="https://example.com/open-webui",
                repo_url="https://example.com/open-webui",
                repo_path=str(Path(temp_dir) / "projects" / "open-webui"),
                status="running",
                summary="open-webui running",
                metadata={
                    "repo_name": "open-webui",
                    "install_location": str(Path(temp_dir) / "projects" / "open-webui"),
                    "access_hint": "Open http://localhost:8080 after startup.",
                },
            )
            displayed: list[str] = []

            with patch("duckln.main.ControlledCommandRunner") as runner_cls:
                runner_cls.return_value.run.return_value.exit_code = 0
                runner_cls.return_value.run.return_value.timed_out = False
                _handle_repo_access_action(
                    reply=FreeTextReply(text="", intent="repo_access", action="attach_repo", action_repo_key="https://example.com/open-webui"),
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=lambda _prompt: True,
                    terminal_interface=None,
                )

            self.assertTrue(any("opened open-webui for you" in message.lower() for message in displayed))

    def test_handle_repo_access_action_prefers_duckln_web_preview_when_live_ui_supports_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HOTL,
            )
            initialize_state_store(paths.config_dir).upsert_repo_state(
                repo_key="https://example.com/open-webui",
                repo_url="https://example.com/open-webui",
                repo_path=str(Path(temp_dir) / "projects" / "open-webui"),
                status="running",
                summary="open-webui running",
                metadata={
                    "repo_name": "open-webui",
                    "install_location": str(Path(temp_dir) / "projects" / "open-webui"),
                    "access_hint": "Open http://localhost:8080 after startup.",
                },
            )
            displayed: list[str] = []
            terminal = FakeTerminalInterface()

            with patch("duckln.main.ControlledCommandRunner") as runner_cls:
                _handle_repo_access_action(
                    reply=FreeTextReply(text="", intent="repo_access", action="attach_repo", action_repo_key="https://example.com/open-webui"),
                    current=current,
                    paths=paths,
                    display_output=displayed.append,
                    approve_prompt=lambda _prompt: True,
                    terminal_interface=terminal,
                )

            self.assertEqual([("http://localhost:8080", "open-webui")], terminal.preview_requests)
            runner_cls.assert_not_called()
            self.assertTrue(any("managed browser window" in message.lower() for message in displayed))

    def test_fetch_web_preview_snapshot_extracts_html_title_and_excerpt(self) -> None:
        html = """
        <html>
          <head><title>Open WebUI</title></head>
          <body><h1>Welcome to Open WebUI</h1><p>Model dashboard is ready.</p></body>
        </html>
        """

        class FakeResponse:
            def __init__(self) -> None:
                self.headers = {"content-type": "text/html; charset=utf-8"}
                self.text = html
                self.status_code = 200
                self.is_success = True
                self.url = "http://localhost:8080/"

        with patch("duckln.textual_ui.httpx.get", return_value=FakeResponse()):
            snapshot = fetch_web_preview_snapshot("http://localhost:8080")

        self.assertEqual("Open WebUI", snapshot.title)
        self.assertTrue(snapshot.ok)
        self.assertEqual("http://localhost:8080/", snapshot.final_url)
        self.assertTrue(any("Welcome to Open WebUI" in line for line in snapshot.excerpt_lines))

    def test_fetch_web_preview_snapshot_reports_network_failure_cleanly(self) -> None:
        with patch("duckln.textual_ui.httpx.get", side_effect=RuntimeError("connection refused")):
            snapshot = fetch_web_preview_snapshot("http://localhost:8080")

        self.assertFalse(snapshot.ok)
        self.assertIn("connection refused", snapshot.error_message)

    def test_handle_repo_logs_action_shows_bounded_log_excerpt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "open-webui",
                                "repo_url": "https://example.com/open-webui",
                                "stars": 129200,
                                "description": "A local model UI.",
                                "category": "LLM",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            log_path = Path(temp_dir) / "runtime" / "open-webui.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("line one\nline two\nline three\n", encoding="utf-8")
            write_workflow_state(
                paths.config_dir,
                {
                    "active_repo_key": "https://example.com/open-webui",
                    "active_repo_name": "open-webui",
                    "active_runtime_status": "running",
                    "active_runtime_repo_key": "https://example.com/open-webui",
                    "active_runtime_repo_name": "open-webui",
                    "active_runtime_log_path": str(log_path),
                },
            )
            displayed: list[str] = []
            reply = FreeTextReply(text="", intent="repo_logs", action="show_repo_logs", action_repo_key="https://example.com/open-webui")

            _handle_repo_logs_action(
                reply=reply,
                paths=paths,
                display_output=displayed.append,
            )

            self.assertTrue(any("line three" in message.lower() for message in displayed))

    def test_remember_command_persists_concise_session_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )
            initialize_state_store(paths.config_dir).upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path=str(paths.config_dir / "projects" / "whisper"),
                status="ready",
                summary="Whisper ready.",
                metadata={"repo_name": "whisper"},
            )
            displayed: list[str] = []

            updated = handle_session_command("/remember", current, paths, display=displayed.append)

            self.assertEqual(current, updated)
            self.assertTrue(any("remembered the current context" in line.lower() for line in displayed))
            record = initialize_state_store(paths.config_dir).get_managed_memory_record("session:remember-latest")
            self.assertIsNotNone(record)
            assert record is not None
            self.assertIn("whisper", record.content.lower())

    def test_free_text_fallback_recommends_repos_from_cached_catalog_for_macbook(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "ollama",
                                "repo_url": "https://example.com/ollama",
                                "stars": 166400,
                                "description": "Run local models.",
                                "category": "LLM",
                                "framework": "Go/Ollama",
                                "last_updated": "2026-04-01",
                            },
                            {
                                "name": "open-webui",
                                "repo_url": "https://example.com/open-webui",
                                "stars": 129200,
                                "description": "A local model UI.",
                                "category": "LLM",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            },
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            },
                            {
                                "name": "stable-diffusion-webui",
                                "repo_url": "https://example.com/sd",
                                "stars": 162000,
                                "description": "Stable Diffusion UI.",
                                "category": "Stable Diffusion",
                                "framework": "Python/PyTorch",
                                "last_updated": "2026-04-01",
                            },
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            response = _respond_to_free_text(
                "can you tell me which repo i must setup on macbook",
                config_dir=config_dir,
                system_probe=SystemProbe(
                    operating_system="Darwin",
                    architecture="arm64",
                    cpu_logical_cores=8,
                    ram_bytes=16 * 1024**3,
                    disk_free_bytes=100 * 1024**3,
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
            )

            self.assertIn("Apple Silicon Mac", response.text)
            self.assertIn("whisper", response.text)
            self.assertIn("open-webui", response.text)
            self.assertIn("/repos", response.text)
            self.assertNotIn("stable-diffusion-webui", response.text)

    def test_free_text_fallback_narrows_to_one_best_repo_after_repeat_recommendation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "open-webui",
                                "repo_url": "https://example.com/open-webui",
                                "stars": 129200,
                                "description": "A local model UI.",
                                "category": "LLM",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            },
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            },
                            {
                                "name": "speechbrain",
                                "repo_url": "https://example.com/speechbrain",
                                "stars": 20000,
                                "description": "Speech toolkit.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            },
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            system_probe = SystemProbe(
                operating_system="Darwin",
                architecture="arm64",
                cpu_logical_cores=8,
                ram_bytes=16 * 1024**3,
                disk_free_bytes=100 * 1024**3,
                python_version="3.11.8",
                gpu=GpuProbeState(
                    backend="mps",
                    summary="Apple Silicon detected; MPS available.",
                    cuda_capable=False,
                    cuda_available=False,
                    mps_capable=True,
                    mps_available=True,
                ),
            )
            earlier = _respond_to_free_text(
                "which repo do you recommend for my system",
                config_dir=config_dir,
                system_probe=system_probe,
            )
            response = _respond_to_free_text(
                "which repo do you recommend for my system",
                config_dir=config_dir,
                system_probe=system_probe,
                recent_replies=(earlier,),
            )

            self.assertTrue("start" in response.text.lower() or "point you to" in response.text.lower())
            self.assertNotIn("The main tradeoff is", response.text)
            self.assertNotIn("Fit:", response.text)

    def test_free_text_single_recommendation_question_stays_direct(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "private-gpt",
                                "repo_url": "https://example.com/private-gpt",
                                "stars": 50000,
                                "description": "Private document chat.",
                                "category": "LLM",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            },
                            {
                                "name": "text-generation-webui",
                                "repo_url": "https://example.com/textgen",
                                "stars": 40000,
                                "description": "Local text generation UI.",
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

            response = _respond_to_free_text("which repo do you recommend for my system", config_dir=config_dir)

            self.assertEqual("repo_recommendation_single", response.intent)
            self.assertIn("private-gpt", response.text.lower())
            self.assertNotIn("most realistic starting points", response.text.lower())
            self.assertNotIn("cleanest first bet", response.text.lower())
            self.assertNotIn("requirements next, or we can move into /repos", response.text.lower())

    def test_free_text_recommends_one_repo_with_rationale_before_repos_steer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            },
                            {
                                "name": "stable-diffusion-webui",
                                "repo_url": "https://example.com/sd",
                                "stars": 162000,
                                "description": "Stable Diffusion UI.",
                                "category": "Stable Diffusion",
                                "framework": "Python/PyTorch CUDA",
                                "last_updated": "2026-04-01",
                            },
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            response = _respond_to_free_text(
                "recommend one repo for my system",
                config_dir=config_dir,
                system_probe=SystemProbe(
                    operating_system="Darwin",
                    architecture="arm64",
                    cpu_logical_cores=8,
                    ram_bytes=16 * 1024**3,
                    disk_free_bytes=100 * 1024**3,
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
            )

            self.assertTrue("whisper" in response.text.lower() and ("i’d start with" in response.text.lower() or "i’d point you to" in response.text.lower()))
            self.assertIn("because", response.text.lower())
            self.assertIn("/repos", response.text)
            self.assertNotIn("Try /repos", response.text)

    def test_free_text_recommendation_handles_typo_in_repo_question(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            response = _respond_to_free_text("whih repos do you recommend", config_dir=config_dir)

            self.assertEqual("repo_recommendation_single", response.intent)
            self.assertIn("whisper", response.text.lower())

    def test_free_text_recommendation_handles_messy_natural_phrasing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            },
                            {
                                "name": "AutoGPT",
                                "repo_url": "https://example.com/autogpt",
                                "stars": 167000,
                                "description": "Autonomous agent framework.",
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
            system_probe = SystemProbe(
                operating_system="Darwin",
                architecture="arm64",
                cpu_logical_cores=8,
                ram_bytes=8 * 1024**3,
                disk_free_bytes=60 * 1024**3,
                python_version="3.11.8",
                gpu=GpuProbeState(
                    backend="mps",
                    summary="Apple Silicon detected; MPS available.",
                    cuda_capable=False,
                    cuda_available=False,
                    mps_capable=True,
                    mps_available=True,
                ),
            )

            response = _respond_to_free_text(
                "what repos you recommend",
                config_dir=config_dir,
                system_probe=system_probe,
            )

            self.assertTrue("i’d start with" in response.text.lower() or "i’d point you to" in response.text.lower())
            self.assertIn("whisper", response.text.lower())
            self.assertNotIn("try /repos", response.text.lower())

    def test_free_text_recommendation_breaks_out_after_repeated_repos_deflection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            prior = FreeTextReply(
                text="/repos is still the right path for that. I can stay with you while you use it.",
                intent="command_hint",
                steer="/repos",
            )

            response = _respond_to_free_text(
                "but is there a repo you think will best to run on my system",
                config_dir=config_dir,
                system_probe=SystemProbe(
                    operating_system="Darwin",
                    architecture="arm64",
                    cpu_logical_cores=8,
                    ram_bytes=16 * 1024**3,
                    disk_free_bytes=80 * 1024**3,
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
                recent_replies=(prior,),
            )

            self.assertTrue("i’d start with" in response.text.lower() or "i’d point you to" in response.text.lower())
            self.assertIn("whisper", response.text.lower())
            self.assertIn("apple silicon", response.text.lower())

    def test_free_text_recommendation_rationale_uses_persisted_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            },
                            {
                                "name": "open-webui",
                                "repo_url": "https://example.com/open-webui",
                                "stars": 129200,
                                "description": "A local model UI.",
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
            system_probe = SystemProbe(
                operating_system="Darwin",
                architecture="arm64",
                cpu_logical_cores=8,
                ram_bytes=16 * 1024**3,
                disk_free_bytes=100 * 1024**3,
                python_version="3.11.8",
                gpu=GpuProbeState(
                    backend="mps",
                    summary="Apple Silicon detected; MPS available.",
                    cuda_capable=False,
                    cuda_available=False,
                    mps_capable=True,
                    mps_available=True,
                ),
            )

            earlier = _respond_to_free_text(
                "recommend one repo for my system",
                config_dir=config_dir,
                system_probe=system_probe,
            )
            response = _respond_to_free_text(
                "why do you recommend that repo",
                config_dir=config_dir,
                system_probe=system_probe,
                recent_replies=(earlier,),
            )

            self.assertIn((earlier.recommendation_repo_name or "").lower(), response.text.lower())
            self.assertNotIn("i recommended", response.text.lower())
            self.assertTrue("because" in response.text.lower() or "stayed" in response.text.lower())

    def test_free_text_repo_requirements_use_probe_grounding(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            store = initialize_state_store(config_dir)
            store.upsert_repo_knowledge(
                repo_key="https://example.com/whisper",
                repo_name="whisper",
                repo_url="https://example.com/whisper",
                source="remote",
                summary="whisper knowledge (remote). requirements: a few CPU cores; 8-16 GB RAM; GPU optional.",
                cpu_profile="a few CPU cores",
                ram_profile="8-16 GB RAM",
                gpu_profile="GPU optional",
                freshness_checked_at="2099-01-01T00:00:00+00:00",
            )
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            response = _respond_to_free_text(
                "what does whisper need on this machine",
                config_dir=config_dir,
                system_probe=SystemProbe(
                    operating_system="Darwin",
                    architecture="arm64",
                    cpu_logical_cores=8,
                    ram_bytes=16 * 1024**3,
                    disk_free_bytes=100 * 1024**3,
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
            )

            self.assertIn("compute path", response.text.lower())
            self.assertIn("mps-capable", response.text.lower())
            self.assertIn("16.0 GiB total RAM", response.text)
            self.assertIn("8 logical CPU cores", response.text)

    def test_free_text_repo_fit_judgment_is_direct_and_hardware_aware(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            store = initialize_state_store(config_dir)
            store.upsert_repo_knowledge(
                repo_key="https://example.com/comfyui",
                repo_name="ComfyUI",
                repo_url="https://example.com/comfyui",
                source="remote",
                summary="ComfyUI remote knowledge.",
                cpu_profile="moderate CPU",
                ram_profile="16+ GB RAM",
                gpu_profile="GPU preferred",
                apple_silicon_notes="Apple Silicon can run this, but CUDA-specific acceleration is not available.",
                freshness_checked_at="2099-01-01T00:00:00+00:00",
            )
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "ComfyUI",
                                "repo_url": "https://example.com/comfyui",
                                "stars": 100000,
                                "description": "Diffusion node UI.",
                                "category": "Stable Diffusion",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            response = _respond_to_free_text(
                "will comfyui run comfortably on my system",
                config_dir=config_dir,
                system_probe=SystemProbe(
                    operating_system="Darwin",
                    architecture="arm64",
                    cpu_logical_cores=8,
                    ram_bytes=8 * 1024**3,
                    disk_free_bytes=100 * 1024**3,
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
            )

            self.assertIn("comfyui", response.text.lower())
            self.assertIn("compute path", response.text.lower())
            self.assertTrue("likely frustrating" in response.text.lower() or "not recommended" in response.text.lower())

    def test_free_text_fallback_avoids_repeating_same_stock_line_across_turns(self) -> None:
        first = _respond_to_free_text("how are you")
        second = _respond_to_free_text(
            "what are you expertise",
            recent_turns=(ConversationTurn(role="user", content="how are you"),),
            recent_replies=(first,),
        )

        self.assertNotEqual(first.text, second.text)
        self.assertNotEqual(first.intent, second.intent)
        self.assertIn("strong", second.text.lower())

    def test_free_text_fallback_avoids_repeating_same_steer_repeatedly(self) -> None:
        prior_a = _respond_to_free_text("help me set up a repository")
        prior_b = _respond_to_free_text(
            "I need help with a repository",
            recent_turns=(ConversationTurn(role="user", content="help me set up a repository"),),
            recent_replies=(prior_a,),
        )
        reply = _respond_to_free_text(
            "repository help",
            recent_turns=(
                ConversationTurn(role="user", content="help me set up a repository"),
                ConversationTurn(role="assistant", content=prior_a.text),
                ConversationTurn(role="user", content="I need help with a repository"),
            ),
            recent_replies=(prior_a, prior_b),
        )

        self.assertNotEqual(prior_b.text, reply.text)
        self.assertIn("/repos", prior_a.text)

    def test_free_text_provider_polish_can_refine_small_talk_reply(self) -> None:
        current = AppConfig(
            provider=Provider.OPENROUTER,
            model="openai/gpt-4o-mini",
            api_key="router-key",
            mode=ControlMode.HITL,
        )
        client = FakeHttpClient(
            {
                "https://openrouter.ai/api/v1/chat/completions": FakeResponse(
                    status_code=200,
                    payload={
                        "choices": [
                            {
                                "message": {
                                    "content": "I’m here and ready. Tell me the setup or repo you want help with."
                                }
                            }
                        ]
                    },
                )
            }
        )

        response = _respond_to_free_text(
            "hi how are you today",
            current=current,
            client=client,
        )

        self.assertTrue(response.provider_backed)
        self.assertIn("ready", response.text.lower())
        self.assertEqual("small_talk", response.route_family)

    def test_provider_polish_cannot_change_recommended_repo_or_fit_label(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-04-05",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 75000,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-04-01",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            store = initialize_state_store(config_dir)
            store.upsert_repo_knowledge(
                repo_key="https://example.com/whisper",
                repo_name="whisper",
                repo_url="https://example.com/whisper",
                source="remote",
                summary="whisper knowledge (remote). requirements: a few CPU cores; 8-16 GB RAM; GPU optional.",
                cpu_profile="a few CPU cores",
                ram_profile="8-16 GB RAM",
                gpu_profile="GPU optional",
                required_tools=("Python", "ffmpeg"),
            )
            current = AppConfig(
                provider=Provider.OPENROUTER,
                model="openai/gpt-4o-mini",
                api_key="router-key",
                mode=ControlMode.HITL,
            )
            client = FakeHttpClient(
                {
                    "https://openrouter.ai/api/v1/chat/completions": FakeResponse(
                        status_code=200,
                        payload={
                            "choices": [
                                {
                                    "message": {
                                        "content": "I would start with open-webui here. Fit: comfortable."
                                    }
                                }
                            ]
                        },
                    )
                }
            )

            response = _respond_to_free_text(
                "recommend me one",
                current=current,
                config_dir=config_dir,
                client=client,
            )

            self.assertIn("whisper", response.text.lower())
            self.assertNotIn("open-webui", response.text.lower())
            self.assertEqual("whisper", response.recommendation_repo_name)

    def test_main_runs_onboarding_before_entering_loop_when_config_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            configured = AppConfig(
                provider=Provider.OPENROUTER,
                model="openrouter/auto",
                api_key="router-key",
                mode=ControlMode.HOTL,
            )

            with (
                patch("duckln.main.resolve_config_paths", return_value=paths),
                patch(
                    "duckln.main.probe_system",
                    return_value=SystemProbe(
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
                    ),
                ),
                patch("duckln.main.record_system_probe"),
                patch("duckln.main.ensure_first_run_preferences"),
                patch(
                    "duckln.main.run_onboarding",
                    return_value=OnboardingResult(config=configured, saved_to=paths.config_file),
                ) as run_onboarding_mock,
            ):
                exit_code = main(
                    input_func=lambda prompt: "exit",
                    display=lambda message: None,
                )

            self.assertEqual(0, exit_code)
            run_onboarding_mock.assert_called_once()

    def test_main_checks_ollama_runtime_on_session_start_and_retries_with_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            save_app_config(
                AppConfig(
                    provider=Provider.OLLAMA,
                    model="llama3.2:latest",
                    api_key=None,
                    base_url="http://localhost:11434",
                    mode=ControlMode.HITL,
                ),
                paths,
            )
            displayed: list[str] = []

            class RecoveringOllamaClient:
                def __init__(self) -> None:
                    self.calls = 0

                def get(self, url: str, *, headers: dict[str, str], timeout: float) -> FakeResponse:
                    self.calls += 1
                    if self.calls == 1:
                        raise OSError("connection refused")
                    return FakeResponse(
                        status_code=200,
                        payload={"models": [{"name": "llama3.2:latest"}]},
                    )

            with (
                patch("duckln.main.resolve_config_paths", return_value=paths),
                patch(
                    "duckln.main.probe_system",
                    return_value=SystemProbe(
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
                    ),
                ),
                patch("duckln.main.record_system_probe"),
                patch("duckln.main.time.sleep"),
            ):
                exit_code = main(
                    input_func=lambda prompt: "exit",
                    display=displayed.append,
                    client=RecoveringOllamaClient(),
                )

            self.assertEqual(0, exit_code)
            self.assertIn(
                "Checking local Ollama runtime at http://localhost:11434/api/tags (attempt 1/3)...",
                displayed,
            )
            self.assertTrue(any("Run `ollama serve`, then Duckln will retry detection." in message for message in displayed))
            self.assertIn(
                "Checking local Ollama runtime at http://localhost:11434/api/tags (attempt 2/3)...",
                displayed,
            )
            self.assertIn("Ollama is reachable at http://localhost:11434/api/tags.", displayed)

    def test_repos_command_passes_current_provider_and_execution_target_to_bringup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OLLAMA,
                model="llama3.2:latest",
                api_key=None,
                base_url="http://localhost:11434",
                mode=ControlMode.HITL,
            )
            repo_record = {
                "name": "alpha",
                "repo_url": "https://example.com/alpha",
                "stars": 50,
                "description": "Alpha repository for tests",
                "category": "LLM",
                "framework": "Python",
                "last_updated": "2026-03-22",
            }
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps({"last_updated": "2026-03-23", "repos": [repo_record]}) + "\n", encoding="utf-8")
            write_config_snapshot(
                paths.config_dir,
                {
                    "execution_target": "vm",
                    "execution_vm_name": "duckln-vm",
                    "active_vm_name": "duckln-vm",
                },
            )

            with (
                patch("duckln.main.bring_up_selected_repo") as bringup_mock,
                patch("duckln.main.ensure_multipass_vm_running", return_value=True),
                patch("duckln.main.verify_vm_exec_connectivity", return_value=True),
                patch("duckln.main.vm_exists_in_multipass", return_value=True),
            ):
                updated = handle_session_command(
                    "/repos",
                    current,
                    paths,
                    select=lambda prompt, choices: next(choice for choice in choices if choice.startswith("alpha "))
                    if "Select a repository" in prompt
                    else next(choice for choice in choices if choice.startswith("Set up in Ubuntu VM"))
                    if "Where should Duckln set up" in prompt
                    else next(choice for choice in choices if "use existing" in choice.lower())
                    if "already active" in prompt.lower()
                    else "Set it up",
                    text_prompt=lambda message, default="": default,
                    display=lambda message: None,
                )

            self.assertEqual(current, updated)
            self.assertEqual("ollama", bringup_mock.call_args.kwargs["runtime_provider"])
            self.assertEqual("vm", bringup_mock.call_args.kwargs["execution_target"])

    def test_repos_vm_target_prompt_lists_all_existing_vms(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OLLAMA,
                model="llama3.2:latest",
                api_key=None,
                base_url="http://localhost:11434",
                mode=ControlMode.HITL,
            )
            selected_repo = RepoCatalogRecord(
                name="alpha",
                repo_url="https://example.com/alpha",
                stars=50,
                description="Alpha repository for tests",
                category="LLM",
                framework="Python",
                last_updated="2026-03-22",
            )
            write_config_snapshot(
                paths.config_dir,
                {
                    "execution_target": "vm",
                    "execution_vm_name": "duckln-vm-200",
                    "active_vm_name": "duckln-vm-200",
                },
            )
            vm_prompt_choices: list[tuple[str, ...]] = []

            def select_prompt(prompt: str, choices: tuple[str, ...]) -> str:
                if "Select a repository" in prompt:
                    return "alpha"
                if "Where should Duckln set up" in prompt:
                    return next(choice for choice in choices if choice.startswith("Set up in Ubuntu VM"))
                if "Choose the Ubuntu VM" in prompt:
                    vm_prompt_choices.append(choices)
                    return "Use existing VM 'duckln-vm-openclaw'"
                return "Set it up"

            with (
                patch("duckln.main.open_repo_catalog", return_value=selected_repo),
                patch("duckln.main.list_multipass_vm_names", return_value=("duckln-vm-200", "duckln-vm-openclaw")),
                patch("duckln.main.ensure_multipass_vm_running", return_value=True),
                patch("duckln.main.verify_vm_exec_connectivity", return_value=True),
                patch("duckln.main.bring_up_selected_repo") as bringup_mock,
            ):
                updated = handle_session_command(
                    "/repos",
                    current,
                    paths,
                    select=select_prompt,
                    text_prompt=lambda message, default="": default,
                    display=lambda message: None,
                    terminal_interface=FakeTerminalInterface(),
                )

            self.assertEqual(current, updated)
            self.assertTrue(vm_prompt_choices)
            self.assertIn("Use existing VM 'duckln-vm-200'", vm_prompt_choices[0])
            self.assertIn("Use existing VM 'duckln-vm-openclaw'", vm_prompt_choices[0])
            self.assertEqual("duckln-vm-openclaw", bringup_mock.call_args.kwargs["vm_name"])

    def test_repos_command_routes_unreachable_vm_into_vm_repair_without_shell_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OLLAMA,
                model="llama3.2:latest",
                api_key=None,
                base_url="http://localhost:11434",
                mode=ControlMode.HOOTLWO,
            )
            repo_record = {
                "name": "openclaw",
                "repo_url": "https://example.com/openclaw",
                "stars": 50,
                "description": "Openclaw repository for tests",
                "category": "Agent",
                "framework": "Python",
                "last_updated": "2026-03-22",
            }
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps({"last_updated": "2026-03-23", "repos": [repo_record]}) + "\n", encoding="utf-8")
            write_config_snapshot(
                paths.config_dir,
                {
                    "execution_target": "vm",
                    "execution_vm_name": "duckln-vm",
                    "active_vm_name": "duckln-vm",
                },
            )
            displayed: list[str] = []
            terminal = FakeTerminalInterface()

            with (
                # Mock the VM listing so the test is deterministic (it must not depend on the dev
                # machine's REAL multipass VMs — otherwise a live `duckln-vm-pro` etc. diverges it).
                patch("duckln.main.list_multipass_vms_or_none", return_value=("duckln-vm",)),
                patch("duckln.main.list_multipass_vm_names", return_value=("duckln-vm",)),
                patch("duckln.main.ensure_multipass_vm_running", return_value=True),
                patch("duckln.main.verify_vm_exec_connectivity", return_value=False),
                patch("duckln.main.restart_multipass_vm", return_value=False),
                patch("duckln.main.vm_exists_in_multipass", return_value=True),
                patch("duckln.main._run_runtime_repair_workflow") as repair_mock,
                patch("duckln.main.bring_up_selected_repo") as bringup_mock,
            ):
                updated = handle_session_command(
                    "/repos",
                    current,
                    paths,
                    select=lambda prompt, choices: next(choice for choice in choices if choice.startswith("openclaw "))
                    if "Select a repository" in prompt
                    else next(choice for choice in choices if choice.startswith("Set up in Ubuntu VM"))
                    if "Where should Duckln set up" in prompt
                    else next(choice for choice in choices if "use existing" in choice.lower())
                    if "already active" in prompt.lower()
                    else "Set it up",
                    text_prompt=lambda message, default="": default,
                    display=displayed.append,
                    terminal_interface=terminal,
                )

            self.assertEqual(current, updated)
            repair_mock.assert_called_once()
            bringup_mock.assert_not_called()
            self.assertEqual([], terminal.run_commands)
            workflow = read_workflow_state(paths.config_dir)
            self.assertEqual("duckln-vm", workflow["active_runtime_vm_name"])
            self.assertEqual("vm", workflow["active_runtime_execution_target"])
            self.assertEqual("vm_bootstrap_failure", workflow["active_incident_category"])
            self.assertTrue(any("blocker is vm transport" in message.lower() for message in displayed))

    def test_repos_command_can_inspect_requirements_without_starting_setup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-03-23",
                        "repos": [
                            {
                                "name": "whisper",
                                "repo_url": "https://example.com/whisper",
                                "stars": 50,
                                "description": "Speech recognition.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-03-22",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            displayed: list[str] = []

            with patch("duckln.main.bring_up_selected_repo") as bringup_mock:
                updated = handle_session_command(
                    "/repos",
                    current,
                    paths,
                    select=lambda prompt, choices: next(choice for choice in choices if choice.startswith("whisper "))
                    if "Select a repository" in prompt
                    else "Set up on local machine"
                    if "Where should Duckln set up" in prompt
                    else "Inspect requirements first",
                    display=displayed.append,
                )

            self.assertEqual(current, updated)
            bringup_mock.assert_not_called()
            joined = "\n".join(displayed).lower()
            self.assertIn("you selected whisper.", joined)
            self.assertIn("whisper looks more like", joined)
            self.assertNotIn("supervisor agent requirements for", joined)

    def test_repos_command_can_switch_poor_fit_repo_to_vm_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-03-23",
                        "repos": [
                            {
                                "name": "kokoro",
                                "repo_url": "https://example.com/kokoro",
                                "stars": 50,
                                "description": "Voice repo.",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-03-22",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            fake_preflight = SimpleNamespace(
                fit_status="not_recommended",
                local_vm_recommendation="A small Ubuntu VM would be the cleaner path here.",
                feasibility=SimpleNamespace(
                    usable_ram_gib=5.0,
                    failure_modes=("swap pressure", "slower inference"),
                    compute_path="mps-capable",
                ),
            )
            displayed: list[str] = []

            with (
                patch("duckln.main.assess_repo_preflight", return_value=fake_preflight),
                patch("duckln.main.create_multipass_vm", return_value=SimpleNamespace(ok=True, vm_name="duckln-vm")),
                patch("duckln.main.configure_existing_multipass_vm"),
                patch("duckln.main.bring_up_selected_repo") as bringup_mock,
                patch("duckln.main.ensure_multipass_vm_running", return_value=True),
                patch("duckln.main.verify_vm_exec_connectivity", return_value=True),
            ):
                updated = handle_session_command(
                    "/repos",
                    current,
                    paths,
                    select=lambda prompt, choices: next(choice for choice in choices if choice.startswith("kokoro "))
                    if "Select a repository" in prompt
                    else "Set up on local machine"
                    if "Where should Duckln set up" in prompt
                    else "Set it up"
                    if "What do you want to do next?" in prompt
                    else "Use a VM instead",
                    text_prompt=lambda prompt, default="": default or "duckln-vm",
                    display=displayed.append,
                )

            self.assertEqual(current, updated)
            self.assertEqual("vm", bringup_mock.call_args.kwargs["execution_target"])
            self.assertTrue(any("switch kokoro onto a vm path" in message.lower() for message in displayed))

    def test_session_memory_state_requires_materialized_memory_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            save_app_config(
                AppConfig(
                    provider=Provider.OPENAI,
                    model="gpt-4o-mini",
                    api_key="openai-key",
                    mode=ControlMode.HITL,
                    user_name="Shreyas",
                    safety_accepted_at="2026-04-04T00:00:00+00:00",
                    onboarding_complete=True,
                ),
                paths,
            )
            (config_dir / "memory" / "AGENTS.md").unlink()

            self.assertEqual("not initialized", _session_memory_state(config_dir))

            initialize_managed_memory_state(config_dir)

            self.assertEqual("ready", _session_memory_state(config_dir))


class RepoSelectionTest(unittest.TestCase):
    def test_format_repo_catalog_choice_is_clean_and_readable(self) -> None:
        label = format_repo_catalog_choice(
            RepoCatalogRecord(
                name="duck",
                repo_url="https://example.com/duck",
                stars=123,
                description="A very long description that should be shortened for the dropdown label so it stays readable.",
                category="LLM",
                framework="Python",
                last_updated="2026-03-23",
            )
        )

        self.assertIn("duck", label)
        self.assertIn("123", label)
        self.assertIn("LLM", label)
        self.assertIn("Python", label)
        self.assertTrue("..." in label or "…" in label)

    def test_open_repo_catalog_returns_selected_repo_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            records = [
                {
                    "name": "low",
                    "repo_url": "https://example.com/low",
                    "stars": 5,
                    "description": "Low repo",
                    "category": "Vision",
                    "framework": "Rust",
                    "last_updated": "2026-03-20",
                },
                {
                    "name": "top",
                    "repo_url": "https://example.com/top",
                    "stars": 10,
                    "description": "Top repo",
                    "category": "LLM",
                    "framework": "Python",
                    "last_updated": "2026-03-21",
                },
            ]
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps({"last_updated": "2026-03-23", "repos": records}) + "\n", encoding="utf-8")

            selected = open_repo_catalog(paths, select=lambda prompt, choices: next(choice for choice in choices if choice.startswith("top ")))

            self.assertIsNotNone(selected)
            assert selected is not None
            self.assertEqual("top", selected.name)
            self.assertEqual("https://example.com/top", selected.repo_url)

    def test_open_repo_catalog_returns_none_for_explicit_cancel_choice(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-03-23",
                        "repos": [
                            {
                                "name": "top",
                                "repo_url": "https://example.com/top",
                                "stars": 10,
                                "description": "Top repo",
                                "category": "LLM",
                                "framework": "Python",
                                "last_updated": "2026-03-21",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            selected = open_repo_catalog(paths, select=lambda prompt, choices: CANCEL_REPO_SELECTION)

            self.assertIsNone(selected)

    def test_open_repo_catalog_can_intake_public_github_repo_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-03-23",
                        "repos": [
                            {
                                "name": "top",
                                "repo_url": "https://example.com/top",
                                "stars": 10,
                                "description": "Top repo",
                                "category": "LLM",
                                "framework": "Python",
                                "last_updated": "2026-03-21",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            record = RepoCatalogRecord(
                name="custom-alpha",
                repo_url="https://github.com/example/custom-alpha",
                stars=42,
                description="Custom alpha repo.",
                category="LLM",
                framework="Python",
                last_updated="2026-04-01",
            )

            with patch("duckln.repos.resolve_public_github_repo_record", return_value=record):
                selected = open_repo_catalog(
                    paths,
                    select=lambda prompt, choices: CUSTOM_GITHUB_REPO_CHOICE,
                    text_prompt=lambda prompt, default="": "https://github.com/example/custom-alpha",
                )

            self.assertIsNotNone(selected)
            assert selected is not None
            self.assertEqual("custom-alpha", selected.name)
            recent = initialize_state_store(paths.config_dir).list_recent_custom_repos()
            self.assertEqual(1, len(recent))
            self.assertEqual("https://github.com/example/custom-alpha", recent[0].repo_url)

    def test_open_repo_catalog_can_select_recent_custom_repo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-03-23",
                        "repos": [
                            {
                                "name": "top",
                                "repo_url": "https://example.com/top",
                                "stars": 10,
                                "description": "Top repo",
                                "category": "LLM",
                                "framework": "Python",
                                "last_updated": "2026-03-21",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            initialize_state_store(paths.config_dir).upsert_recent_custom_repo(
                repo_url="https://github.com/example/custom-alpha",
                repo_name="custom-alpha",
                stars=42,
                description="Custom alpha repo.",
                category="LLM",
                framework="Python",
                last_updated="2026-04-01",
                metadata={"source": "custom_github"},
            )

            selected = open_repo_catalog(
                paths,
                select=lambda prompt, choices: RECENT_CUSTOM_REPOS_CHOICE
                if prompt == "Select a repository:"
                else next(choice for choice in choices if choice.startswith("custom-alpha ")),
            )

            self.assertIsNotNone(selected)
            assert selected is not None
            self.assertEqual("custom-alpha", selected.name)

    def test_open_repo_catalog_default_path_uses_searchable_arrow_key_selector(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            repo_record = {
                "name": "top",
                "repo_url": "https://example.com/top",
                "stars": 10,
                "description": "Top repo",
                "category": "LLM",
                "framework": "Python",
                "last_updated": "2026-03-21",
            }
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps({"last_updated": "2026-03-23", "repos": [repo_record]}) + "\n", encoding="utf-8")

            with patch(
                "duckln.repos.duckln_search_select",
                return_value=format_repo_catalog_choice(
                    RepoCatalogRecord(
                        name=repo_record["name"],
                        repo_url=repo_record["repo_url"],
                        stars=repo_record["stars"],
                        description=repo_record["description"],
                        category=repo_record["category"],
                        framework=repo_record["framework"],
                        last_updated=repo_record["last_updated"],
                    )
                ),
            ) as mocked_search:
                selected = open_repo_catalog(paths)

            self.assertIsNotNone(selected)
            assert selected is not None
            self.assertEqual("top", selected.name)
            mocked_search.assert_called_once()

    def test_repos_command_in_hitl_suggests_clone_without_mutating_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            current = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="openai-key",
                mode=ControlMode.HITL,
            )
            cache_path = resolve_local_repo_catalog_cache_path(Path(temp_dir))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-03-23",
                        "repos": [
                            {
                                "name": "alpha",
                                "repo_url": "https://example.com/alpha",
                                "stars": 50,
                                "description": "Alpha repository for tests",
                                "category": "LLM",
                                "framework": "Python",
                                "last_updated": "2026-03-22",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            displayed: list[str] = []

            updated = handle_session_command(
                "/repos",
                current,
                paths,
                select=lambda prompt, choices: next(choice for choice in choices if choice.startswith("alpha "))
                if "Select a repository" in prompt
                else "Set up on local machine"
                if "Where should Duckln set up" in prompt
                else "Set it up",
                display=displayed.append,
            )

            self.assertEqual(current, updated)
            self.assertTrue(any("Supervisor agent paused before clone" in message for message in displayed))


class RuntimeSearchTextTest(unittest.TestCase):
    def test_runtime_error_text_prefers_fatal_line_when_only_classifier_text_exists(self) -> None:
        text = _runtime_error_text_for_search(
            failure_summary=(
                "Clone failed: Duckln classified this blocker as missing command. "
                "Likely package/module: bash. Fatal line: bash: line 1: multipass: command not found"
            ),
            workflow=None,
        )

        self.assertEqual("bash: line 1: multipass: command not found", text)


if __name__ == "__main__":
    unittest.main()


def _probe() -> SystemProbe:
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
