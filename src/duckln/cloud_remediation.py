"""Classifier + remediation registry for GCP and AWS provider failures."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import shlex
from typing import Callable

from agent.agent_loop import ActionResult, IssueClassification, Remediation
from duckln.shell import CommandResult, ControlledCommandRunner


GENERATIVE_LANGUAGE_API = "generativelanguage.googleapis.com"
GCP_API_KEY_CREDENTIALS_URL = "https://console.cloud.google.com/apis/credentials"
GCP_API_KEY_DOCS_URL = "https://docs.cloud.google.com/docs/authentication/api-keys#api-keys-bound-sa"


@dataclass(frozen=True)
class ApiKeyAuditFinding:
    """One GCP API key inspected during the post-auth security audit."""

    key_uid: str
    display_name: str | None
    api_targets: tuple[str, ...]
    has_referrer_restrictions: bool
    has_ip_restrictions: bool
    has_app_restrictions: bool
    is_unrestricted: bool


GCP_API_DISABLED_PATTERN = re.compile(
    r"API \[([a-z0-9\-]+\.googleapis\.com)\] not enabled",
    re.IGNORECASE,
)
GCP_AUTH_EXPIRED_MARKERS = (
    "reauthentication is needed",
    "credentials are no longer valid",
    "your credentials have expired",
    "you do not currently have an active account",
)
GCP_BILLING_MARKERS = (
    "billing has not been enabled",
    "billing_disabled",
    "billing account",
)
GCP_PERMISSION_MARKERS = (
    "permission_denied",
    "permission denied",
    "the caller does not have permission",
)

AWS_SSO_EXPIRED_MARKERS = (
    "token has expired and refresh failed",
    "the sso session associated with this profile has expired",
    "error loading sso token",
)
AWS_CREDENTIALS_MISSING_MARKERS = (
    "unable to locate credentials",
    "could not connect to the endpoint url",
    "you must specify a region",
)
AWS_REGION_UNSET_MARKERS = (
    "you must specify a region",
    "no region was specified",
)
AWS_OPTIN_MARKERS = ("optinrequired",)
AWS_PERMISSION_MARKERS = ("unauthorizedoperation", "accessdeniedexception", "access denied")
AWS_MFA_MARKERS = ("multifactorauthentication failed", "mfa is required")

GCP_CONSOLE_URL = "https://console.cloud.google.com/"
AWS_CONSOLE_URL = "https://console.aws.amazon.com/"
GCP_SERVICES_ENABLE_URL = "https://cloud.google.com/sdk/gcloud/reference/services/enable"
AWS_SSO_LOGIN_URL = "https://docs.aws.amazon.com/cli/latest/reference/sso/login.html"
AWS_CONFIGURE_URL = "https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-quickstart.html"


def _haystack(result: CommandResult | ActionResult) -> str:
    if isinstance(result, ActionResult):
        return f"{result.raw_stderr}\n{result.raw_stdout}".lower()
    return f"{result.stderr or ''}\n{result.stdout or ''}".lower()


def classify_gcloud_failure(
    result: CommandResult | ActionResult,
    *,
    project: str | None = None,
) -> IssueClassification | None:
    """Map a failed gcloud CommandResult/ActionResult to an IssueClassification."""

    text = result.stderr if isinstance(result, CommandResult) else result.raw_stderr
    combined = _haystack(result)
    match = GCP_API_DISABLED_PATTERN.search(text or "")
    if match:
        api_name = match.group(1).lower()
        target = f" on project {project}" if project else ""
        return IssueClassification(
            kind=f"gcp_api_disabled:{api_name}",
            message=f"GCP API {api_name} is not enabled{target}.",
            source_url=GCP_SERVICES_ENABLE_URL,
        )
    if any(marker in combined for marker in GCP_AUTH_EXPIRED_MARKERS):
        return IssueClassification(
            kind="gcp_auth_expired",
            message="GCP credentials have expired or no active account is set.",
            source_url=GCP_CONSOLE_URL,
        )
    if any(marker in combined for marker in GCP_BILLING_MARKERS):
        target = f" for project {project}" if project else ""
        return IssueClassification(
            kind="gcp_billing_disabled",
            message=f"Billing is not enabled{target}. Enable billing in the GCP console before continuing.",
            source_url=GCP_CONSOLE_URL,
        )
    if any(marker in combined for marker in GCP_PERMISSION_MARKERS):
        return IssueClassification(
            kind="gcp_permission_denied",
            message="GCP rejected the request as permission denied. Grant the missing IAM role and retry.",
            source_url=GCP_CONSOLE_URL,
        )
    return None


def classify_aws_failure(result: CommandResult | ActionResult) -> IssueClassification | None:
    """Map a failed aws CommandResult/ActionResult to an IssueClassification."""

    combined = _haystack(result)
    if any(marker in combined for marker in AWS_SSO_EXPIRED_MARKERS):
        return IssueClassification(
            kind="aws_sso_token_expired",
            message="AWS SSO session has expired. Sign in again to refresh credentials.",
            source_url=AWS_SSO_LOGIN_URL,
        )
    if "unable to locate credentials" in combined:
        return IssueClassification(
            kind="aws_credentials_missing",
            message="AWS credentials are not configured.",
            source_url=AWS_CONFIGURE_URL,
        )
    if any(marker in combined for marker in AWS_REGION_UNSET_MARKERS):
        return IssueClassification(
            kind="aws_region_not_set",
            message="AWS default region is not set.",
            source_url=AWS_CONFIGURE_URL,
        )
    if any(marker in combined for marker in AWS_OPTIN_MARKERS):
        return IssueClassification(
            kind="aws_account_disabled",
            message="AWS reports the account or service is not opted in. Enable it in the console.",
            source_url=AWS_CONSOLE_URL,
        )
    if any(marker in combined for marker in AWS_MFA_MARKERS):
        return IssueClassification(
            kind="aws_mfa_required",
            message="AWS requires multi-factor authentication for this action.",
            source_url=AWS_CONSOLE_URL,
        )
    if any(marker in combined for marker in AWS_PERMISSION_MARKERS):
        return IssueClassification(
            kind="aws_permission_denied",
            message="AWS rejected the request as unauthorized. Grant the missing IAM permission and retry.",
            source_url=AWS_CONSOLE_URL,
        )
    return None


def classify_cloud_failure(
    provider: str,
    result: CommandResult | ActionResult,
    *,
    project: str | None = None,
) -> IssueClassification | None:
    """Provider-agnostic dispatcher for classify_gcloud_failure / classify_aws_failure."""

    normalized = (provider or "").strip().lower()
    if normalized == "gcp":
        return classify_gcloud_failure(result, project=project)
    if normalized == "aws":
        return classify_aws_failure(result)
    return None


def _wrap_command_result(result: CommandResult) -> ActionResult:
    ok = result.exit_code == 0 and not result.timed_out
    outcome = ""
    if not ok:
        first_err = (result.stderr or result.stdout or "").strip().splitlines()
        outcome = first_err[0] if first_err else "Command failed."
    return ActionResult(ok=ok, outcome=outcome, raw_stdout=result.stdout or "", raw_stderr=result.stderr or "")


def resolve_gcloud_remediation(
    issue: IssueClassification,
    *,
    project: str | None,
    runner: ControlledCommandRunner,
    gcloud_cli: str,
    run_user_auth_command: Callable[[str], None] | None = None,
) -> Remediation | None:
    """Build a Remediation for a classified GCP issue, or None for un-fixable kinds."""

    if issue.kind.startswith("gcp_api_disabled:"):
        api_name = issue.kind.split(":", 1)[1]
        target_project = (project or "").strip()
        if not target_project:
            return None
        command = (
            f"{shlex.quote(gcloud_cli)} services enable {shlex.quote(api_name)} "
            f"--project {shlex.quote(target_project)}"
        )

        def _run_enable() -> ActionResult:
            return _wrap_command_result(runner.run(command, timeout_seconds=180))

        return Remediation(
            kind=issue.kind,
            label=f"Enable {api_name} on project {target_project}",
            run=_run_enable,
            source_url=GCP_SERVICES_ENABLE_URL,
        )
    if issue.kind == "gcp_auth_expired" and run_user_auth_command is not None:
        command = f"{shlex.quote(gcloud_cli)} auth login"

        def _run_auth() -> ActionResult:
            run_user_auth_command(command)
            return ActionResult(ok=True, outcome="Launched gcloud auth login in the terminal.")

        return Remediation(
            kind=issue.kind,
            label="Sign in to gcloud (browser flow)",
            run=_run_auth,
            source_url=GCP_CONSOLE_URL,
        )
    return None


def resolve_aws_remediation(
    issue: IssueClassification,
    *,
    profile: str | None,
    runner: ControlledCommandRunner,
    aws_cli: str,
    run_user_auth_command: Callable[[str], None] | None = None,
) -> Remediation | None:
    """Build a Remediation for a classified AWS issue, or None for un-fixable kinds."""

    if issue.kind == "aws_sso_token_expired" and run_user_auth_command is not None:
        command = f"{shlex.quote(aws_cli)} sso login"
        if profile:
            command += f" --profile {shlex.quote(profile)}"

        def _run_sso_login() -> ActionResult:
            run_user_auth_command(command)
            return ActionResult(ok=True, outcome="Launched aws sso login in the terminal.")

        return Remediation(
            kind=issue.kind,
            label="Refresh AWS SSO session",
            run=_run_sso_login,
            source_url=AWS_SSO_LOGIN_URL,
        )
    if issue.kind == "aws_credentials_missing" and run_user_auth_command is not None:
        command = f"{shlex.quote(aws_cli)} configure"

        def _run_configure() -> ActionResult:
            run_user_auth_command(command)
            return ActionResult(ok=True, outcome="Launched aws configure in the terminal.")

        return Remediation(
            kind=issue.kind,
            label="Configure AWS credentials",
            run=_run_configure,
            source_url=AWS_CONFIGURE_URL,
        )
    return None


def resolve_cloud_remediation(
    provider: str,
    issue: IssueClassification,
    *,
    runner: ControlledCommandRunner,
    cli_path: str,
    project: str | None = None,
    profile: str | None = None,
    run_user_auth_command: Callable[[str], None] | None = None,
) -> Remediation | None:
    """Provider-agnostic dispatcher for resolve_gcloud_remediation / resolve_aws_remediation."""

    normalized = (provider or "").strip().lower()
    if normalized == "gcp":
        return resolve_gcloud_remediation(
            issue,
            project=project,
            runner=runner,
            gcloud_cli=cli_path,
            run_user_auth_command=run_user_auth_command,
        )
    if normalized == "aws":
        return resolve_aws_remediation(
            issue,
            profile=profile,
            runner=runner,
            aws_cli=cli_path,
            run_user_auth_command=run_user_auth_command,
        )
    return None


def _classify_api_key_entry(entry: dict) -> ApiKeyAuditFinding:
    """Inspect one entry from `gcloud services api-keys list --format=json`."""

    uid = str(entry.get("uid") or entry.get("name") or "").strip()
    display_name = entry.get("displayName")
    restrictions = entry.get("restrictions") or {}
    api_targets_raw = restrictions.get("apiTargets") or []
    api_targets: list[str] = []
    for item in api_targets_raw if isinstance(api_targets_raw, list) else []:
        if isinstance(item, dict):
            service = str(item.get("service") or "").strip()
            if service:
                api_targets.append(service)
    has_referrer = bool(restrictions.get("browserKeyRestrictions"))
    has_ip = bool(restrictions.get("serverKeyRestrictions"))
    has_app = bool(restrictions.get("iosKeyRestrictions") or restrictions.get("androidKeyRestrictions"))
    has_any_consumer_restriction = has_referrer or has_ip or has_app
    has_any_restriction = bool(restrictions) and (bool(api_targets) or has_any_consumer_restriction)
    targets_generative = GENERATIVE_LANGUAGE_API in api_targets
    is_unrestricted = (not has_any_restriction) or (targets_generative and not has_any_consumer_restriction)
    return ApiKeyAuditFinding(
        key_uid=uid,
        display_name=str(display_name) if display_name else None,
        api_targets=tuple(api_targets),
        has_referrer_restrictions=has_referrer,
        has_ip_restrictions=has_ip,
        has_app_restrictions=has_app,
        is_unrestricted=is_unrestricted,
    )


def audit_gcp_api_keys(
    *,
    runner: ControlledCommandRunner,
    gcloud_cli: str,
    project: str,
) -> tuple[tuple[ApiKeyAuditFinding, ...], CommandResult | None]:
    """List GCP API keys for the project and classify each by restriction state."""

    project_clean = (project or "").strip()
    if not project_clean:
        return ((), None)
    command = (
        f"{shlex.quote(gcloud_cli)} services api-keys list "
        f"--project {shlex.quote(project_clean)} --format=json"
    )
    result = runner.run(command, timeout_seconds=60)
    if result.exit_code != 0 or result.timed_out:
        return ((), result)
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return ((), result)
    findings: list[ApiKeyAuditFinding] = []
    for entry in payload if isinstance(payload, list) else []:
        if isinstance(entry, dict):
            findings.append(_classify_api_key_entry(entry))
    return (tuple(findings), None)


def classify_gcp_api_key_findings(
    findings: tuple[ApiKeyAuditFinding, ...],
    *,
    project: str | None = None,
) -> IssueClassification | None:
    """Return an IssueClassification when any unrestricted API key is found."""

    unrestricted = tuple(finding for finding in findings if finding.is_unrestricted)
    if not unrestricted:
        return None
    sample = ", ".join(
        finding.display_name or finding.key_uid or "<unnamed key>"
        for finding in unrestricted[:3]
    )
    suffix = "" if len(unrestricted) <= 3 else f" and {len(unrestricted) - 3} more"
    where = f" on project {project}" if project else ""
    message = (
        f"{len(unrestricted)} unrestricted GCP API key(s) detected{where}: "
        f"{sample}{suffix}. Restrict keys (referrer/IP/API target) or move to a "
        "service-account-bound credential before they are exploited."
    )
    return IssueClassification(
        kind=f"gcp_unrestricted_api_keys:{len(unrestricted)}",
        message=message,
        source_url=GCP_API_KEY_DOCS_URL,
    )


def resolve_gcp_api_key_remediation(
    issue: IssueClassification,
    *,
    project: str,
    open_browser: Callable[[str], bool] | None = None,
    display_output: Callable[[str], None] | None = None,
) -> Remediation | None:
    """Build a Remediation that opens the GCP credentials console for the project."""

    if not issue.kind.startswith("gcp_unrestricted_api_keys:"):
        return None
    project_clean = (project or "").strip()
    if not project_clean:
        url = GCP_API_KEY_CREDENTIALS_URL
    else:
        url = f"{GCP_API_KEY_CREDENTIALS_URL}?project={project_clean}"

    def _run_open() -> ActionResult:
        opened = False
        if open_browser is not None:
            try:
                opened = bool(open_browser(url))
            except Exception:
                opened = False
        if display_output is not None:
            if opened:
                display_output(f"Opened {url} for you to restrict the flagged API keys.")
            else:
                display_output(f"Open this URL to restrict the flagged API keys: {url}")
        return ActionResult(ok=True, outcome=f"Surfaced credentials console for project {project_clean}.")

    return Remediation(
        kind=issue.kind,
        label=f"Open GCP credentials console for project {project_clean or '(default)'}",
        run=_run_open,
        source_url=GCP_API_KEY_DOCS_URL,
    )
