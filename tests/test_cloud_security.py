"""Tests for the GCP API-key audit + remediation."""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock

from duckln.cloud_remediation import (
    ApiKeyAuditFinding,
    GCP_API_KEY_CREDENTIALS_URL,
    audit_gcp_api_keys,
    classify_gcp_api_key_findings,
    resolve_gcp_api_key_remediation,
)
from duckln.shell import CommandResult


def _cmd(*, exit_code: int = 0, stdout: str = "", stderr: str = "") -> CommandResult:
    return CommandResult(
        command="dummy",
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        timed_out=False,
        duration_seconds=0.0,
    )


_RESTRICTED_KEY = {
    "uid": "key-restricted",
    "displayName": "Backend Server Key",
    "restrictions": {
        "apiTargets": [{"service": "generativelanguage.googleapis.com"}],
        "serverKeyRestrictions": {"allowedIps": ["10.0.0.0/8"]},
    },
}
_UNRESTRICTED_KEY = {
    "uid": "key-unrestricted",
    "displayName": "Public Demo Key",
    "restrictions": {},
}
_GEMINI_WITHOUT_REFERRER_KEY = {
    "uid": "key-gemini-loose",
    "displayName": "Gemini Web Key",
    "restrictions": {
        "apiTargets": [{"service": "generativelanguage.googleapis.com"}],
    },
}


class AuditParserTests(unittest.TestCase):
    def test_audit_lists_findings_with_restriction_state(self) -> None:
        runner = MagicMock()
        runner.run.return_value = _cmd(
            exit_code=0,
            stdout=json.dumps([_RESTRICTED_KEY, _UNRESTRICTED_KEY, _GEMINI_WITHOUT_REFERRER_KEY]),
        )
        findings, failure = audit_gcp_api_keys(runner=runner, gcloud_cli="/usr/bin/gcloud", project="p")
        self.assertIsNone(failure)
        self.assertEqual(len(findings), 3)
        unrestricted = {f.key_uid for f in findings if f.is_unrestricted}
        self.assertIn("key-unrestricted", unrestricted)
        self.assertIn("key-gemini-loose", unrestricted)
        self.assertNotIn("key-restricted", unrestricted)
        runner.run.assert_called_once()
        args, _kwargs = runner.run.call_args
        self.assertIn("services api-keys list", args[0])
        self.assertIn("--project p", args[0])

    def test_audit_returns_failure_on_nonzero_exit(self) -> None:
        runner = MagicMock()
        runner.run.return_value = _cmd(exit_code=1, stderr="PERMISSION_DENIED")
        findings, failure = audit_gcp_api_keys(runner=runner, gcloud_cli="/usr/bin/gcloud", project="p")
        self.assertEqual(findings, ())
        self.assertIsNotNone(failure)
        self.assertIn("PERMISSION_DENIED", failure.stderr)

    def test_audit_returns_empty_when_no_project(self) -> None:
        runner = MagicMock()
        findings, failure = audit_gcp_api_keys(runner=runner, gcloud_cli="/usr/bin/gcloud", project="")
        self.assertEqual(findings, ())
        self.assertIsNone(failure)
        runner.run.assert_not_called()


class ClassifierTests(unittest.TestCase):
    def test_no_unrestricted_keys_returns_none(self) -> None:
        findings = (
            ApiKeyAuditFinding(
                key_uid="ok",
                display_name="ok",
                api_targets=("generativelanguage.googleapis.com",),
                has_referrer_restrictions=True,
                has_ip_restrictions=False,
                has_app_restrictions=False,
                is_unrestricted=False,
            ),
        )
        self.assertIsNone(classify_gcp_api_key_findings(findings, project="p"))

    def test_unrestricted_key_produces_issue_with_count_kind(self) -> None:
        findings = (
            ApiKeyAuditFinding(
                key_uid="loose-1",
                display_name="Demo",
                api_targets=(),
                has_referrer_restrictions=False,
                has_ip_restrictions=False,
                has_app_restrictions=False,
                is_unrestricted=True,
            ),
            ApiKeyAuditFinding(
                key_uid="loose-2",
                display_name=None,
                api_targets=("generativelanguage.googleapis.com",),
                has_referrer_restrictions=False,
                has_ip_restrictions=False,
                has_app_restrictions=False,
                is_unrestricted=True,
            ),
        )
        issue = classify_gcp_api_key_findings(findings, project="p")
        self.assertIsNotNone(issue)
        self.assertEqual(issue.kind, "gcp_unrestricted_api_keys:2")
        self.assertIn("p", issue.message)


class RemediationTests(unittest.TestCase):
    def test_remediation_opens_credentials_url_with_project(self) -> None:
        opens: list[str] = []
        displays: list[str] = []
        issue = classify_gcp_api_key_findings(
            (
                ApiKeyAuditFinding(
                    key_uid="x",
                    display_name="x",
                    api_targets=(),
                    has_referrer_restrictions=False,
                    has_ip_restrictions=False,
                    has_app_restrictions=False,
                    is_unrestricted=True,
                ),
            ),
            project="duckln-webshop-test-2026",
        )
        remediation = resolve_gcp_api_key_remediation(
            issue,
            project="duckln-webshop-test-2026",
            open_browser=lambda url: opens.append(url) or True,
            display_output=displays.append,
        )
        self.assertIsNotNone(remediation)
        result = remediation.run()
        self.assertTrue(result.ok)
        self.assertEqual(len(opens), 1)
        self.assertIn(GCP_API_KEY_CREDENTIALS_URL, opens[0])
        self.assertIn("project=duckln-webshop-test-2026", opens[0])
        self.assertTrue(any("Opened" in line for line in displays))

    def test_remediation_falls_back_to_print_when_browser_fails(self) -> None:
        displays: list[str] = []
        issue = classify_gcp_api_key_findings(
            (
                ApiKeyAuditFinding(
                    key_uid="x",
                    display_name="x",
                    api_targets=(),
                    has_referrer_restrictions=False,
                    has_ip_restrictions=False,
                    has_app_restrictions=False,
                    is_unrestricted=True,
                ),
            ),
            project="p",
        )
        remediation = resolve_gcp_api_key_remediation(
            issue,
            project="p",
            open_browser=lambda _u: False,
            display_output=displays.append,
        )
        result = remediation.run()
        self.assertTrue(result.ok)
        self.assertTrue(any("Open this URL" in line for line in displays))

    def test_unrelated_issue_kind_returns_none(self) -> None:
        from agent.agent_loop import IssueClassification

        issue = IssueClassification(kind="gcp_billing_disabled", message="x")
        self.assertIsNone(
            resolve_gcp_api_key_remediation(issue, project="p", open_browser=lambda _u: True)
        )


if __name__ == "__main__":
    unittest.main()
