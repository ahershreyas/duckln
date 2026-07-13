"""End-to-end tests for the /cloud Fix-now / Fix-later / Ignore loop helpers."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from duckln.config import ConfigPaths
from duckln.main import (
    _confirm_or_switch_gcp_project,
    _run_aws_region_configuration_loop,
    _run_gcp_api_key_audit_step,
    _run_gcp_zone_configuration_loop,
)
from duckln.shell import CommandResult
from state.access import read_pending_cloud_remediation


def _cmd(*, exit_code: int = 0, stdout: str = "", stderr: str = "") -> CommandResult:
    return CommandResult(
        command="dummy",
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        timed_out=False,
        duration_seconds=0.0,
    )


class FakeRunner:
    """A ControlledCommandRunner stand-in driven by a scripted command->result map."""

    def __init__(self, *, script: list[tuple[str, CommandResult]]) -> None:
        self._script = script
        self.calls: list[str] = []

    def run(self, command: str, *_args, **_kwargs) -> CommandResult:
        self.calls.append(command)
        for needle, result in self._script:
            if needle in command:
                return result
        return _cmd(exit_code=127, stderr=f"unscripted command: {command}")


def _make_paths() -> tuple[ConfigPaths, tempfile.TemporaryDirectory]:
    tmp = tempfile.TemporaryDirectory()
    config_dir = Path(tmp.name)
    config_file = config_dir / "config.json"
    return ConfigPaths(config_dir=config_dir, config_file=config_file), tmp


class GcpZoneLoopTests(unittest.TestCase):
    def test_api_not_enabled_then_fix_now_succeeds(self) -> None:
        paths, tmp = _make_paths()
        try:
            zones_payload = json.dumps(
                [
                    {"name": "us-central1-a", "status": "UP"},
                    {"name": "us-central1-b", "status": "UP"},
                ]
            )
            api_disabled_stderr = (
                "ERROR: (gcloud.compute.zones.list) Could not fetch resource:\n"
                " - API [compute.googleapis.com] not enabled on project "
                "[duckln-webshop-test-2026]. Would you like to enable and retry."
            )

            script = [
                ("services enable compute.googleapis.com", _cmd(exit_code=0, stdout="enabled")),
                ("compute zones list", _cmd(exit_code=0, stdout=zones_payload)),
                ("config set compute/zone", _cmd(exit_code=0, stdout="")),
            ]
            runner = FakeRunner(script=script)

            # First call to discover_gcp_zones_detailed should fail with the API-disabled
            # stderr. We intercept by ordering: the FakeRunner returns the api-disabled
            # error the FIRST time `compute zones list` is run, then the success payload
            # the SECOND time. Implement by replacing the entry after first hit.
            zones_calls = {"count": 0}

            def conditional_run(command: str, *args, **kwargs) -> CommandResult:
                if "compute zones list" in command:
                    zones_calls["count"] += 1
                    if zones_calls["count"] == 1:
                        return _cmd(exit_code=1, stderr=api_disabled_stderr)
                return FakeRunner.run(runner, command, *args, **kwargs)

            runner.run = conditional_run  # type: ignore[assignment]

            display: list[str] = []
            choices_made: list[str] = []

            def select_prompt(prompt: str, options) -> str:
                choices_made.append(prompt)
                if "What should Duckln do" in prompt:
                    return "Fix now"
                if "Choose GCP zone" in prompt:
                    return "us-central1-b"
                return options[0]

            ok = _run_gcp_zone_configuration_loop(
                paths=paths,
                runner=runner,
                cli="/usr/bin/gcloud",
                gcloud_cli_path="/usr/bin/gcloud",
                project="duckln-webshop-test-2026",
                default_zone="us-central1-a",
                select_prompt=select_prompt,
                text_prompt=lambda _p, _d: _d,
                display_output=display.append,
                terminal_interface=None,
            )
            self.assertTrue(ok, msg=f"loop did not complete; display={display}")
            joined_calls = " ".join(runner.calls)
            self.assertIn("config set compute/zone us-central1-b", joined_calls)
            self.assertIn("services enable compute.googleapis.com", joined_calls)
            self.assertTrue(any("compute.googleapis.com" in line for line in display))
            self.assertIsNone(read_pending_cloud_remediation(paths.config_dir))
        finally:
            tmp.cleanup()

    def test_api_not_enabled_then_fix_later_persists_followup(self) -> None:
        paths, tmp = _make_paths()
        try:
            api_disabled_stderr = (
                "API [compute.googleapis.com] not enabled on project [p]."
            )
            runner = FakeRunner(
                script=[("compute zones list", _cmd(exit_code=1, stderr=api_disabled_stderr))]
            )

            def select_prompt(prompt: str, options) -> str:
                if "What should Duckln do" in prompt:
                    return "Fix later"
                return options[0]

            ok = _run_gcp_zone_configuration_loop(
                paths=paths,
                runner=runner,
                cli="/usr/bin/gcloud",
                gcloud_cli_path="/usr/bin/gcloud",
                project="p",
                default_zone="us-central1-a",
                select_prompt=select_prompt,
                text_prompt=lambda _p, _d: _d,
                display_output=lambda _line: None,
                terminal_interface=None,
            )
            self.assertFalse(ok)
            payload = read_pending_cloud_remediation(paths.config_dir)
            self.assertIsNotNone(payload)
            self.assertEqual(payload.get("provider"), "gcp")
            self.assertTrue(payload.get("kind", "").startswith("gcp_api_disabled:"))
            self.assertEqual(payload.get("project"), "p")
        finally:
            tmp.cleanup()

    def test_api_not_enabled_then_ignore_skips_step(self) -> None:
        paths, tmp = _make_paths()
        try:
            api_disabled_stderr = (
                "API [compute.googleapis.com] not enabled on project [p]."
            )
            runner = FakeRunner(
                script=[("compute zones list", _cmd(exit_code=1, stderr=api_disabled_stderr))]
            )

            def select_prompt(prompt: str, options) -> str:
                if "What should Duckln do" in prompt:
                    return "Ignore"
                return options[0]

            ok = _run_gcp_zone_configuration_loop(
                paths=paths,
                runner=runner,
                cli="/usr/bin/gcloud",
                gcloud_cli_path="/usr/bin/gcloud",
                project="p",
                default_zone="us-central1-a",
                select_prompt=select_prompt,
                text_prompt=lambda _p, _d: _d,
                display_output=lambda _line: None,
                terminal_interface=None,
            )
            # Ignoring discovery means there is no zone to set; the second step also has
            # no selected zone and will fail without classification. Confirm the loop
            # did NOT complete and did NOT persist a pending remediation (ignore is final).
            self.assertFalse(ok)
            self.assertIsNone(read_pending_cloud_remediation(paths.config_dir))
        finally:
            tmp.cleanup()


class AwsRegionLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self._patcher = patch(
            "duckln.cloud_runtime._resolve_cloud_cli_path",
            return_value="/usr/bin/aws",
        )
        self._patcher.start()

    def tearDown(self) -> None:
        self._patcher.stop()

    def test_sso_expired_then_fix_now_dispatches_login_and_succeeds(self) -> None:
        paths, tmp = _make_paths()
        try:
            regions_payload = json.dumps(["us-east-1", "us-west-2"])
            sso_expired_stderr = "Error loading SSO Token: Token has expired and refresh failed"
            describe_calls = {"count": 0}

            def conditional_run(command: str, *args, **kwargs) -> CommandResult:
                if "ec2 describe-regions" in command:
                    describe_calls["count"] += 1
                    if describe_calls["count"] == 1:
                        return _cmd(exit_code=255, stderr=sso_expired_stderr)
                    return _cmd(exit_code=0, stdout=regions_payload)
                if "configure set region" in command:
                    return _cmd(exit_code=0)
                return _cmd(exit_code=127, stderr=f"unscripted: {command}")

            runner = type("R", (), {"run": staticmethod(conditional_run), "calls": []})()
            user_auth_calls: list[str] = []

            class TerminalStub:
                def run_terminal_command(self, *, command: str) -> None:
                    user_auth_calls.append(command)

            def select_prompt(prompt: str, options) -> str:
                if "What should Duckln do" in prompt:
                    return "Fix now"
                if "Choose AWS default region" in prompt:
                    return "us-west-2"
                return options[0]

            ok = _run_aws_region_configuration_loop(
                paths=paths,
                runner=runner,
                cli="/usr/bin/aws",
                aws_cli_path="/usr/bin/aws",
                default_region="us-east-1",
                select_prompt=select_prompt,
                text_prompt=lambda _p, _d: _d,
                display_output=lambda _line: None,
                terminal_interface=TerminalStub(),
            )
            self.assertTrue(ok)
            self.assertTrue(any("sso login" in call for call in user_auth_calls))
        finally:
            tmp.cleanup()

    def test_credentials_missing_then_fix_later_persists_for_aws(self) -> None:
        paths, tmp = _make_paths()
        try:
            def conditional_run(command: str, *args, **kwargs) -> CommandResult:
                if "ec2 describe-regions" in command:
                    return _cmd(exit_code=253, stderr="Unable to locate credentials")
                return _cmd(exit_code=127, stderr="unscripted")

            runner = type("R", (), {"run": staticmethod(conditional_run)})()

            def select_prompt(prompt: str, options) -> str:
                if "What should Duckln do" in prompt:
                    return "Fix later"
                return options[0]

            ok = _run_aws_region_configuration_loop(
                paths=paths,
                runner=runner,
                cli="/usr/bin/aws",
                aws_cli_path="/usr/bin/aws",
                default_region="us-east-1",
                select_prompt=select_prompt,
                text_prompt=lambda _p, _d: _d,
                display_output=lambda _line: None,
                terminal_interface=None,
            )
            self.assertFalse(ok)
            payload = read_pending_cloud_remediation(paths.config_dir)
            self.assertIsNotNone(payload)
            self.assertEqual(payload.get("provider"), "aws")
            self.assertEqual(payload.get("kind"), "aws_credentials_missing")
        finally:
            tmp.cleanup()


class ProjectConfirmTests(unittest.TestCase):
    def test_yes_continue_keeps_current_project(self) -> None:
        def select_prompt(prompt: str, options) -> str:
            self.assertIn("Continue with this project", prompt)
            return "Yes, continue"

        chosen = _confirm_or_switch_gcp_project(
            runner=type("R", (), {"run": staticmethod(lambda *_a, **_k: None)})(),
            current_project="duckln-webshop-test-2026",
            select_prompt=select_prompt,
            text_prompt=lambda _p, _d: _d,
            display_output=lambda _line: None,
        )
        self.assertEqual(chosen, "duckln-webshop-test-2026")

    def test_no_switch_invokes_picker(self) -> None:
        with patch("duckln.main._select_or_create_gcp_project", return_value="another-project") as picker:
            def select_prompt(prompt: str, options) -> str:
                return "No, pick another"

            chosen = _confirm_or_switch_gcp_project(
                runner=type("R", (), {"run": staticmethod(lambda *_a, **_k: None)})(),
                current_project="duckln-webshop-test-2026",
                select_prompt=select_prompt,
                text_prompt=lambda _p, _d: _d,
                display_output=lambda _line: None,
            )
            self.assertEqual(chosen, "another-project")
            picker.assert_called_once()

    def test_cancel_returns_empty(self) -> None:
        chosen = _confirm_or_switch_gcp_project(
            runner=type("R", (), {"run": staticmethod(lambda *_a, **_k: None)})(),
            current_project="duckln-webshop-test-2026",
            select_prompt=lambda _p, _o: "Cancel",
            text_prompt=lambda _p, _d: _d,
            display_output=lambda _line: None,
        )
        self.assertEqual(chosen, "")

    def test_no_current_project_jumps_to_picker(self) -> None:
        with patch("duckln.main._select_or_create_gcp_project", return_value="picked") as picker:
            chosen = _confirm_or_switch_gcp_project(
                runner=type("R", (), {"run": staticmethod(lambda *_a, **_k: None)})(),
                current_project="",
                select_prompt=lambda _p, _o: "Yes, continue",
                text_prompt=lambda _p, _d: _d,
                display_output=lambda _line: None,
            )
            self.assertEqual(chosen, "picked")
            picker.assert_called_once()


class ApiKeyAuditStepTests(unittest.TestCase):
    UNRESTRICTED_PAYLOAD = json.dumps(
        [
            {
                "uid": "loose-1",
                "displayName": "Demo Key",
                "restrictions": {},
            }
        ]
    )

    def test_audit_finds_unrestricted_key_fix_now_completes(self) -> None:
        paths, tmp = _make_paths()
        try:
            opens: list[str] = []

            def runner_run(command: str, *_args, **_kwargs) -> CommandResult:
                if "services api-keys list" in command:
                    return _cmd(exit_code=0, stdout=self.UNRESTRICTED_PAYLOAD)
                return _cmd(exit_code=127, stderr="unscripted")

            runner = type("R", (), {"run": staticmethod(runner_run)})()

            def select_prompt(prompt: str, options) -> str:
                if "What should Duckln do" in prompt:
                    return "Fix now"
                return options[0]

            with patch("duckln.main._open_browser_url", side_effect=lambda url: opens.append(url) or True):
                ok = _run_gcp_api_key_audit_step(
                    paths=paths,
                    runner=runner,
                    gcloud_cli_path="/usr/bin/gcloud",
                    project="duckln-webshop-test-2026",
                    select_prompt=select_prompt,
                    display_output=lambda _line: None,
                )
            self.assertTrue(ok)
            self.assertEqual(len(opens), 1)
            self.assertIn("duckln-webshop-test-2026", opens[0])
        finally:
            tmp.cleanup()

    def test_audit_fix_later_persists_pending_remediation(self) -> None:
        paths, tmp = _make_paths()
        try:
            def runner_run(command: str, *_args, **_kwargs) -> CommandResult:
                if "services api-keys list" in command:
                    return _cmd(exit_code=0, stdout=self.UNRESTRICTED_PAYLOAD)
                return _cmd(exit_code=127, stderr="unscripted")

            runner = type("R", (), {"run": staticmethod(runner_run)})()

            def select_prompt(prompt: str, options) -> str:
                if "What should Duckln do" in prompt:
                    return "Fix later"
                return options[0]

            ok = _run_gcp_api_key_audit_step(
                paths=paths,
                runner=runner,
                gcloud_cli_path="/usr/bin/gcloud",
                project="p",
                select_prompt=select_prompt,
                display_output=lambda _line: None,
            )
            self.assertFalse(ok)
            payload = read_pending_cloud_remediation(paths.config_dir)
            self.assertIsNotNone(payload)
            self.assertTrue(str(payload.get("kind", "")).startswith("gcp_unrestricted_api_keys:"))
            self.assertEqual(payload.get("provider"), "gcp")
            self.assertEqual(payload.get("project"), "p")
        finally:
            tmp.cleanup()

    def test_audit_passes_silently_when_all_keys_restricted(self) -> None:
        paths, tmp = _make_paths()
        try:
            restricted_payload = json.dumps(
                [
                    {
                        "uid": "ok",
                        "displayName": "Backend",
                        "restrictions": {
                            "apiTargets": [{"service": "generativelanguage.googleapis.com"}],
                            "serverKeyRestrictions": {"allowedIps": ["10.0.0.0/8"]},
                        },
                    }
                ]
            )

            def runner_run(command: str, *_args, **_kwargs) -> CommandResult:
                if "services api-keys list" in command:
                    return _cmd(exit_code=0, stdout=restricted_payload)
                return _cmd(exit_code=127, stderr="unscripted")

            runner = type("R", (), {"run": staticmethod(runner_run)})()
            picker_calls: list[str] = []

            ok = _run_gcp_api_key_audit_step(
                paths=paths,
                runner=runner,
                gcloud_cli_path="/usr/bin/gcloud",
                project="p",
                select_prompt=lambda prompt, _o: picker_calls.append(prompt) or "Fix now",
                display_output=lambda _line: None,
            )
            self.assertTrue(ok)
            self.assertEqual(picker_calls, [])  # no remediation picker shown
        finally:
            tmp.cleanup()


class CloudHeaderUpdateTests(unittest.TestCase):
    @staticmethod
    def _build_status(provider: str = "gcp"):
        from duckln.cloud_runtime import CloudAuthStatus

        return CloudAuthStatus(
            provider=provider,
            cli_name="gcloud" if provider == "gcp" else "aws",
            cli_available=True,
            authenticated=True,
            account_label="user@example.com",
            default_region_or_zone="us-central1-a",
            project_label="p",
            message="ok",
            source_url="https://example.com",
            cli_path="/usr/bin/gcloud",
            project_configured=True,
            region_or_zone_configured=True,
            ready=True,
            readiness_label=f"{provider.upper()} ready",
            next_action=None,
        )

    def test_mark_cloud_provider_target_keeps_header_local_when_no_resource(self) -> None:
        from duckln.main import _mark_cloud_provider_target

        paths, tmp = _make_paths()
        try:
            calls: list[dict] = []

            class TerminalStub:
                def update_connection(self, **kwargs) -> None:
                    calls.append(kwargs)

            _mark_cloud_provider_target(
                paths,
                provider="gcp",
                status=self._build_status(),
                terminal_interface=TerminalStub(),
            )
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["connection_type"], "local")
        finally:
            tmp.cleanup()

    def test_mark_cloud_provider_target_flips_header_when_live_resource_exists(self) -> None:
        from duckln.main import _mark_cloud_provider_target
        from state.store import initialize_state_store

        paths, tmp = _make_paths()
        try:
            store = initialize_state_store(paths.config_dir)
            store.upsert_managed_resource(
                resource_key="gcp:project:vm-1",
                resource_kind="vm",
                provider="gcp",
                display_name="vm-1",
                status="running",
                execution_target="gcp",
                region="us-central1-a",
            )
            calls: list[dict] = []

            class TerminalStub:
                def update_connection(self, **kwargs) -> None:
                    calls.append(kwargs)

            _mark_cloud_provider_target(
                paths,
                provider="gcp",
                status=self._build_status(),
                terminal_interface=TerminalStub(),
            )
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["connection_type"], "gcp")
            self.assertEqual(calls[0]["cloud_vendor"], "GCP")
            self.assertEqual(calls[0]["cloud_region"], "us-central1-a")
        finally:
            tmp.cleanup()


class HeaderReassertionTests(unittest.TestCase):
    def test_reassert_reads_workflow_state_and_calls_update_connection(self) -> None:
        from duckln.main import _reassert_cloud_terminal_target_from_state
        from state.access import write_workflow_state
        from state.store import initialize_state_store

        paths, tmp = _make_paths()
        try:
            # Live resource must exist for the header to reflect cloud.
            store = initialize_state_store(paths.config_dir)
            store.upsert_managed_resource(
                resource_key="gcp:project:vm-1",
                resource_kind="vm",
                provider="gcp",
                display_name="vm-1",
                status="running",
                execution_target="gcp",
                region="us-central1-a",
            )
            write_workflow_state(
                paths.config_dir,
                {
                    "active_runtime_execution_target": "gcp",
                    "active_runtime_cloud_vendor": "GCP",
                    "active_runtime_cloud_region": "us-central1-a",
                },
            )
            calls: list[dict] = []

            class TerminalStub:
                def update_connection(self, **kwargs) -> None:
                    calls.append(kwargs)

            _reassert_cloud_terminal_target_from_state(paths=paths, terminal_interface=TerminalStub())
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["connection_type"], "gcp")
            self.assertEqual(calls[0]["cloud_vendor"], "GCP")
            self.assertEqual(calls[0]["cloud_region"], "us-central1-a")
        finally:
            tmp.cleanup()

    def test_reassert_falls_back_to_local_when_no_live_resource(self) -> None:
        from duckln.main import _reassert_cloud_terminal_target_from_state
        from state.access import write_workflow_state

        paths, tmp = _make_paths()
        try:
            # Stale state from a past session — no managed resource exists.
            write_workflow_state(
                paths.config_dir,
                {
                    "active_runtime_execution_target": "gcp",
                    "active_runtime_cloud_vendor": "GCP",
                },
            )
            calls: list[dict] = []

            class TerminalStub:
                def update_connection(self, **kwargs) -> None:
                    calls.append(kwargs)

            _reassert_cloud_terminal_target_from_state(paths=paths, terminal_interface=TerminalStub())
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["connection_type"], "local")
        finally:
            tmp.cleanup()

    def test_reassert_defaults_to_local_when_no_state(self) -> None:
        from duckln.main import _reassert_cloud_terminal_target_from_state

        paths, tmp = _make_paths()
        try:
            calls: list[dict] = []

            class TerminalStub:
                def update_connection(self, **kwargs) -> None:
                    calls.append(kwargs)

            _reassert_cloud_terminal_target_from_state(paths=paths, terminal_interface=TerminalStub())
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["connection_type"], "local")
        finally:
            tmp.cleanup()

    def test_reassert_is_noop_when_terminal_interface_is_none(self) -> None:
        from duckln.main import _reassert_cloud_terminal_target_from_state

        paths, tmp = _make_paths()
        try:
            _reassert_cloud_terminal_target_from_state(paths=paths, terminal_interface=None)
        finally:
            tmp.cleanup()


class FallbackHeaderTests(unittest.TestCase):
    def test_terminal_chat_interface_update_connection_records_hint(self) -> None:
        from duckln.ui import TerminalChatInterface

        chat = TerminalChatInterface(session_header="Duckln", user_name="user")
        chat.update_connection(connection_type="gcp")
        self.assertEqual(chat._connection_hint, "Google Cloud")
        chat.update_connection(connection_type="aws")
        self.assertEqual(chat._connection_hint, "AWS Cloud")


class CancelHintTests(unittest.TestCase):
    def test_with_cancel_hint_appends_suffix(self) -> None:
        from duckln.main import _with_cancel_hint

        result = _with_cancel_hint("Choose a zone:")
        self.assertIn("Choose a zone:", result)
        self.assertIn("Press Q or Esc to cancel", result)

    def test_with_cancel_hint_does_not_double_append(self) -> None:
        from duckln.main import _with_cancel_hint

        once = _with_cancel_hint("Pick:")
        twice = _with_cancel_hint(once)
        self.assertEqual(once, twice)

    def test_wrap_select_with_cancel_hint_passes_through_options_and_decorates_prompt(self) -> None:
        from duckln.main import _wrap_select_with_cancel_hint

        seen: dict[str, object] = {}

        def inner(prompt: str, options) -> str:
            seen["prompt"] = prompt
            seen["options"] = options
            return options[0]

        wrapped = _wrap_select_with_cancel_hint(inner)
        result = wrapped("Pick a project:", ("a", "b"))
        self.assertEqual(result, "a")
        self.assertIn("Pick a project:", seen["prompt"])
        self.assertIn("Press Q or Esc to cancel", seen["prompt"])
        self.assertEqual(seen["options"], ("a", "b"))

    def test_wrap_select_with_cancel_hint_handles_none_inner(self) -> None:
        from duckln.main import _wrap_select_with_cancel_hint

        self.assertIsNone(_wrap_select_with_cancel_hint(None))


if __name__ == "__main__":
    unittest.main()
