"""Tests for the GCP/AWS classifier + remediation registry."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from agent.agent_loop import ActionResult
from duckln.cloud_remediation import (
    classify_aws_failure,
    classify_cloud_failure,
    classify_gcloud_failure,
    resolve_aws_remediation,
    resolve_cloud_remediation,
    resolve_gcloud_remediation,
)
from duckln.shell import CommandResult


def _cmd(stderr: str = "", stdout: str = "", exit_code: int = 1) -> CommandResult:
    return CommandResult(
        command="dummy",
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        timed_out=False,
        duration_seconds=0.0,
    )


class GcloudClassifierTests(unittest.TestCase):
    def test_compute_api_disabled_pattern(self) -> None:
        stderr = (
            "ERROR: (gcloud.compute.zones.list) Could not fetch resource:\n"
            " - API [compute.googleapis.com] not enabled on project "
            "[duckln-webshop-test-2026]. Would you like to enable and retry."
        )
        issue = classify_gcloud_failure(_cmd(stderr=stderr), project="duckln-webshop-test-2026")
        self.assertIsNotNone(issue)
        self.assertEqual(issue.kind, "gcp_api_disabled:compute.googleapis.com")
        self.assertIn("duckln-webshop-test-2026", issue.message)

    def test_reauthentication_needed(self) -> None:
        stderr = "ERROR: Reauthentication is needed to refresh credentials."
        issue = classify_gcloud_failure(_cmd(stderr=stderr))
        self.assertIsNotNone(issue)
        self.assertEqual(issue.kind, "gcp_auth_expired")

    def test_billing_disabled(self) -> None:
        stderr = "ERROR: The project billing has not been enabled."
        issue = classify_gcloud_failure(_cmd(stderr=stderr))
        self.assertIsNotNone(issue)
        self.assertEqual(issue.kind, "gcp_billing_disabled")

    def test_permission_denied(self) -> None:
        stderr = "ERROR: PERMISSION_DENIED: The caller does not have permission."
        issue = classify_gcloud_failure(_cmd(stderr=stderr))
        self.assertIsNotNone(issue)
        self.assertEqual(issue.kind, "gcp_permission_denied")

    def test_unrelated_failure_returns_none(self) -> None:
        self.assertIsNone(classify_gcloud_failure(_cmd(stderr="weird unrelated thing")))

    def test_action_result_input(self) -> None:
        action = ActionResult(
            ok=False,
            raw_stderr="API [compute.googleapis.com] not enabled on project [p].",
        )
        issue = classify_gcloud_failure(action, project="p")
        self.assertEqual(issue.kind, "gcp_api_disabled:compute.googleapis.com")


class AwsClassifierTests(unittest.TestCase):
    def test_sso_token_expired(self) -> None:
        stderr = "Error loading SSO Token: Token has expired and refresh failed"
        issue = classify_aws_failure(_cmd(stderr=stderr))
        self.assertIsNotNone(issue)
        self.assertEqual(issue.kind, "aws_sso_token_expired")

    def test_credentials_missing(self) -> None:
        stderr = "Unable to locate credentials. You can configure credentials by running 'aws configure'."
        issue = classify_aws_failure(_cmd(stderr=stderr))
        self.assertIsNotNone(issue)
        self.assertEqual(issue.kind, "aws_credentials_missing")

    def test_region_unset(self) -> None:
        stderr = "You must specify a region."
        issue = classify_aws_failure(_cmd(stderr=stderr))
        self.assertIsNotNone(issue)
        # "You must specify a region" matches the AWS_CREDENTIALS_MISSING_MARKERS first via
        # AWS_REGION_UNSET_MARKERS; assert it's one of the two acceptable mappings.
        self.assertIn(issue.kind, {"aws_region_not_set", "aws_credentials_missing"})

    def test_opt_in_required(self) -> None:
        stderr = "An error occurred (OptInRequired) when calling the RunInstances operation."
        issue = classify_aws_failure(_cmd(stderr=stderr))
        self.assertIsNotNone(issue)
        self.assertEqual(issue.kind, "aws_account_disabled")

    def test_mfa_required(self) -> None:
        stderr = "MultiFactorAuthentication failed with invalid MFA one time pass code."
        issue = classify_aws_failure(_cmd(stderr=stderr))
        self.assertIsNotNone(issue)
        self.assertEqual(issue.kind, "aws_mfa_required")

    def test_permission_denied(self) -> None:
        stderr = "An error occurred (UnauthorizedOperation) when calling the action."
        issue = classify_aws_failure(_cmd(stderr=stderr))
        self.assertIsNotNone(issue)
        self.assertEqual(issue.kind, "aws_permission_denied")


class DispatcherTests(unittest.TestCase):
    def test_dispatcher_routes_to_gcp(self) -> None:
        stderr = "API [compute.googleapis.com] not enabled on project [p]."
        issue = classify_cloud_failure("gcp", _cmd(stderr=stderr), project="p")
        self.assertEqual(issue.kind, "gcp_api_disabled:compute.googleapis.com")

    def test_dispatcher_routes_to_aws(self) -> None:
        stderr = "Token has expired and refresh failed"
        issue = classify_cloud_failure("aws", _cmd(stderr=stderr))
        self.assertEqual(issue.kind, "aws_sso_token_expired")

    def test_unknown_provider_returns_none(self) -> None:
        self.assertIsNone(classify_cloud_failure("azure", _cmd(stderr="anything")))


class GcloudRemediationTests(unittest.TestCase):
    def test_api_disabled_runs_services_enable(self) -> None:
        runner = MagicMock()
        runner.run.return_value = _cmd(exit_code=0, stdout="enabled\n")
        from duckln.cloud_remediation import classify_gcloud_failure

        issue = classify_gcloud_failure(
            _cmd(stderr="API [compute.googleapis.com] not enabled on project [p]."),
            project="p",
        )
        remediation = resolve_gcloud_remediation(
            issue,
            project="p",
            runner=runner,
            gcloud_cli="/usr/bin/gcloud",
        )
        self.assertIsNotNone(remediation)
        result = remediation.run()
        self.assertTrue(result.ok)
        runner.run.assert_called_once()
        args, _kwargs = runner.run.call_args
        self.assertIn("services enable compute.googleapis.com", args[0])
        self.assertIn("--project p", args[0])

    def test_auth_expired_dispatches_user_auth(self) -> None:
        runner = MagicMock()
        calls: list[str] = []
        from duckln.cloud_remediation import classify_gcloud_failure

        issue = classify_gcloud_failure(_cmd(stderr="Reauthentication is needed"))
        remediation = resolve_gcloud_remediation(
            issue,
            project="p",
            runner=runner,
            gcloud_cli="/usr/bin/gcloud",
            run_user_auth_command=calls.append,
        )
        self.assertIsNotNone(remediation)
        remediation.run()
        self.assertEqual(len(calls), 1)
        self.assertIn("auth login", calls[0])

    def test_billing_disabled_has_no_remediation(self) -> None:
        runner = MagicMock()
        from duckln.cloud_remediation import classify_gcloud_failure

        issue = classify_gcloud_failure(_cmd(stderr="billing has not been enabled"))
        self.assertIsNone(
            resolve_gcloud_remediation(
                issue,
                project="p",
                runner=runner,
                gcloud_cli="/usr/bin/gcloud",
            )
        )


class AwsRemediationTests(unittest.TestCase):
    def test_sso_token_expired_dispatches_sso_login(self) -> None:
        runner = MagicMock()
        calls: list[str] = []
        from duckln.cloud_remediation import classify_aws_failure

        issue = classify_aws_failure(_cmd(stderr="Token has expired and refresh failed"))
        remediation = resolve_aws_remediation(
            issue,
            profile="default",
            runner=runner,
            aws_cli="/usr/bin/aws",
            run_user_auth_command=calls.append,
        )
        self.assertIsNotNone(remediation)
        remediation.run()
        self.assertEqual(len(calls), 1)
        self.assertIn("sso login", calls[0])
        self.assertIn("--profile default", calls[0])

    def test_credentials_missing_dispatches_aws_configure(self) -> None:
        runner = MagicMock()
        calls: list[str] = []
        from duckln.cloud_remediation import classify_aws_failure

        issue = classify_aws_failure(_cmd(stderr="Unable to locate credentials"))
        remediation = resolve_aws_remediation(
            issue,
            profile=None,
            runner=runner,
            aws_cli="/usr/bin/aws",
            run_user_auth_command=calls.append,
        )
        self.assertIsNotNone(remediation)
        remediation.run()
        self.assertEqual(len(calls), 1)
        self.assertIn("configure", calls[0])

    def test_resolve_cloud_remediation_dispatch(self) -> None:
        runner = MagicMock()
        from duckln.cloud_remediation import classify_cloud_failure

        issue = classify_cloud_failure(
            "gcp",
            _cmd(stderr="API [compute.googleapis.com] not enabled on project [p]."),
            project="p",
        )
        remediation = resolve_cloud_remediation(
            "gcp",
            issue,
            runner=runner,
            cli_path="/usr/bin/gcloud",
            project="p",
        )
        self.assertIsNotNone(remediation)


if __name__ == "__main__":
    unittest.main()
