"""Bounded cloud runtime helpers for auth checks, launch plans, cleanup, and idle enforcement."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import json
import platform
import re
import shlex
import shutil
from pathlib import Path
from typing import Callable

from duckln.config import ConfigPaths
from duckln.execution_trace import render_tool_invocation_trace
from duckln.runtime_governance import DEFAULT_IDLE_SHUTDOWN_MINUTES, approved_cloud_shapes, default_resource_tags
from duckln.shell import CommandResult, ControlledCommandRunner
from state.store import ManagedResourceRecord, initialize_state_store


AWS_AUTH_SOURCE_URL = "https://docs.aws.amazon.com/cli/latest/reference/sts/get-caller-identity.html"
AWS_CLI_INSTALL_SOURCE_URL = "https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
AWS_RUN_SOURCE_URL = "https://docs.aws.amazon.com/cli/latest/reference/ec2/run-instances.html"
AWS_STOP_SOURCE_URL = "https://docs.aws.amazon.com/cli/latest/reference/ec2/stop-instances.html"
AWS_TERMINATE_SOURCE_URL = "https://docs.aws.amazon.com/cli/latest/reference/ec2/terminate-instances.html"
UBUNTU_AWS_AMI_SOURCE_URL = "https://documentation.ubuntu.com/aws/en/latest/aws-how-to/instances/find-ubuntu-images/"
GCP_AUTH_SOURCE_URL = "https://docs.cloud.google.com/sdk/gcloud/reference/auth/list"
GCP_CLI_INSTALL_SOURCE_URL = "https://cloud.google.com/sdk/docs/install"
GCP_PROJECT_CREATE_SOURCE_URL = "https://cloud.google.com/sdk/gcloud/reference/projects/create"
GCP_CREATE_SOURCE_URL = "https://docs.cloud.google.com/sdk/gcloud/reference/compute/instances/create"
GCP_LABEL_SOURCE_URL = "https://docs.cloud.google.com/sdk/gcloud/reference/compute/instances/add-labels"

IDLE_WARNING_GRACE_MINUTES = 5
DEFAULT_AWS_REGION = "us-east-1"
DEFAULT_GCP_ZONE = "us-central1-a"


@dataclass(frozen=True)
class CloudAuthStatus:
    """Auth and tool availability state for one cloud provider."""

    provider: str
    cli_name: str
    cli_available: bool
    authenticated: bool
    account_label: str | None
    default_region_or_zone: str | None
    project_label: str | None
    message: str
    source_url: str
    install_hint: str | None = None
    auth_hint: str | None = None
    cli_path: str | None = None
    project_configured: bool = False
    region_or_zone_configured: bool = False
    ready: bool = False
    readiness_label: str | None = None
    next_action: str | None = None


@dataclass(frozen=True)
class CloudCliInstallPlan:
    """One user-approved provider CLI install plan from official provider resources."""

    provider: str
    cli_name: str
    command: str
    source_url: str
    post_install_auth_hint: str
    os_type: str
    requires_elevation: bool


@dataclass(frozen=True)
class CloudLaunchRequest:
    """User-approved bounded cloud creation request."""

    provider: str
    display_name: str
    shape: str
    cpu_count: int
    memory_gb: int
    disk_gb: int
    region_or_zone: str
    idle_timeout_minutes: int
    key_pair_name: str | None = None
    subnet_id: str | None = None
    security_group_ids: tuple[str, ...] = ()
    project_id: str | None = None
    repo_key: str | None = None


@dataclass(frozen=True)
class CloudLaunchResult:
    """Outcome of a bounded cloud resource launch."""

    ok: bool
    provider: str
    resource_key: str | None
    display_name: str
    shape: str
    region_or_zone: str
    message: str
    connect_command: str | None
    stop_command: str | None
    cleanup_command: str | None
    source_urls: tuple[str, ...]


@dataclass(frozen=True)
class CloudProjectCreateResult:
    """Outcome of a bounded cloud project/bootstrap step."""

    ok: bool
    provider: str
    project_id: str
    message: str
    source_url: str


@dataclass(frozen=True)
class DiscoveredCloudResource:
    """One provider-side resource discovered by Duckln tags/labels."""

    provider: str
    resource_key: str
    display_name: str
    region_or_zone: str
    shape: str | None
    state: str
    connect_command: str | None
    stop_command: str | None
    cleanup_command: str | None


@dataclass(frozen=True)
class CloudRemoteSessionPlan:
    """Attach and remote-exec commands for a tracked cloud resource."""

    resource_key: str
    provider: str
    display_name: str
    attach_command: str
    remote_exec_prefix: str
    stop_command: str | None
    cleanup_command: str | None
    region_or_zone: str
    shape: str | None


def inspect_cloud_auth(provider: str, *, runner: ControlledCommandRunner | None = None) -> CloudAuthStatus:
    """Check whether a provider CLI is available and authenticated."""

    normalized = provider.strip().lower()
    runner_instance = runner or ControlledCommandRunner(execution_target="local")
    if normalized == "aws":
        aws_cli = _resolve_cloud_cli_path("aws")
        if aws_cli is None:
            install_hint, auth_hint = _cloud_setup_hints("aws")
            return CloudAuthStatus(
                provider="aws",
                cli_name="aws",
                cli_available=False,
                authenticated=False,
                account_label=None,
                default_region_or_zone=None,
                project_label=None,
                message="AWS CLI is not installed or not on PATH.",
                source_url=AWS_CLI_INSTALL_SOURCE_URL,
                install_hint=install_hint,
                auth_hint=auth_hint,
                ready=False,
                readiness_label="AWS CLI missing",
                next_action="install_cli",
            )
        aws_command = shlex.quote(aws_cli)
        identity = runner_instance.run(f"{aws_command} sts get-caller-identity --output json")
        region_result = runner_instance.run(f"{aws_command} configure get region")
        configured_region = (region_result.stdout or "").strip()
        region = configured_region or DEFAULT_AWS_REGION
        if identity.exit_code != 0 or identity.timed_out:
            _install_hint, auth_hint = _cloud_setup_hints("aws")
            return CloudAuthStatus(
                provider="aws",
                cli_name="aws",
                cli_available=True,
                authenticated=False,
                account_label=None,
                default_region_or_zone=region,
                project_label=None,
                message="AWS CLI is installed, but Duckln could not confirm an authenticated identity.",
                source_url=AWS_AUTH_SOURCE_URL,
                auth_hint=auth_hint,
                cli_path=aws_cli,
                region_or_zone_configured=bool(configured_region),
                ready=False,
                readiness_label="AWS needs login",
                next_action="authenticate",
            )
        try:
            payload = json.loads(identity.stdout or "{}")
        except json.JSONDecodeError:
            payload = {}
        account = str(payload.get("Arn") or payload.get("Account") or "").strip() or None
        return CloudAuthStatus(
            provider="aws",
            cli_name="aws",
            cli_available=True,
            authenticated=True,
            account_label=account,
            default_region_or_zone=region,
            project_label=None,
            message=(
                f"AWS authentication is ready for {account or 'the active identity'}."
                if configured_region
                else "AWS authentication is ready, but no default region is configured."
            ),
            source_url=AWS_AUTH_SOURCE_URL,
            cli_path=aws_cli,
            region_or_zone_configured=bool(configured_region),
            ready=bool(configured_region),
            readiness_label="AWS ready" if configured_region else "AWS needs region",
            next_action=None if configured_region else "configure_region",
        )
    if normalized == "gcp":
        gcloud_cli = _resolve_cloud_cli_path("gcloud")
        if gcloud_cli is None:
            install_hint, auth_hint = _cloud_setup_hints("gcp")
            return CloudAuthStatus(
                provider="gcp",
                cli_name="gcloud",
                cli_available=False,
                authenticated=False,
                account_label=None,
                default_region_or_zone=DEFAULT_GCP_ZONE,
                project_label=None,
                message="gcloud CLI is not installed or not on PATH.",
                source_url=GCP_CLI_INSTALL_SOURCE_URL,
                install_hint=install_hint,
                auth_hint=auth_hint,
                ready=False,
                readiness_label="GCP CLI missing",
                next_action="install_cli",
            )
        gcloud_command = shlex.quote(gcloud_cli)
        accounts = runner_instance.run(f'{gcloud_command} auth list --filter=status:ACTIVE --format=json')
        project_result = runner_instance.run(f"{gcloud_command} config get-value project")
        zone_result = runner_instance.run(f"{gcloud_command} config get-value compute/zone")
        project = (project_result.stdout or "").strip()
        if project == "(unset)":
            project = ""
        zone = (zone_result.stdout or "").strip()
        if zone == "(unset)":
            zone = ""
        if accounts.exit_code != 0 or accounts.timed_out:
            _install_hint, auth_hint = _cloud_setup_hints("gcp")
            return CloudAuthStatus(
                provider="gcp",
                cli_name="gcloud",
                cli_available=True,
                authenticated=False,
                account_label=None,
                default_region_or_zone=zone or DEFAULT_GCP_ZONE,
                project_label=project or None,
                message="gcloud CLI is installed, but Duckln could not confirm an active authenticated account.",
                source_url=GCP_AUTH_SOURCE_URL,
                auth_hint=auth_hint,
                cli_path=gcloud_cli,
                project_configured=bool(project),
                region_or_zone_configured=bool(zone),
                ready=False,
                readiness_label="GCP needs login",
                next_action="authenticate",
            )
        try:
            payload = json.loads(accounts.stdout or "[]")
        except json.JSONDecodeError:
            payload = []
        account = None
        if isinstance(payload, list) and payload:
            first = payload[0]
            if isinstance(first, dict):
                account = str(first.get("account") or "").strip() or None
        if not account:
            _install_hint, auth_hint = _cloud_setup_hints("gcp")
            return CloudAuthStatus(
                provider="gcp",
                cli_name="gcloud",
                cli_available=True,
                authenticated=False,
                account_label=None,
                default_region_or_zone=zone or DEFAULT_GCP_ZONE,
                project_label=project or None,
                message="gcloud CLI is installed, but no active authenticated account was found.",
                source_url=GCP_AUTH_SOURCE_URL,
                auth_hint=auth_hint,
                cli_path=gcloud_cli,
                project_configured=bool(project),
                region_or_zone_configured=bool(zone),
                ready=False,
                readiness_label="GCP needs login",
                next_action="authenticate",
            )
        ready = bool(project and zone)
        if not project:
            label = "GCP needs project"
            message = f"GCP account {account} is authenticated, but no project is configured."
            next_action = "configure_project"
        elif not zone:
            label = "GCP needs zone"
            message = f"GCP account {account} is authenticated for project {project}, but no compute zone is configured."
            next_action = "configure_zone"
        else:
            label = "GCP ready"
            message = f"GCP is ready for {account} in project {project}."
            next_action = None
        return CloudAuthStatus(
            provider="gcp",
            cli_name="gcloud",
            cli_available=True,
            authenticated=True,
            account_label=account,
            default_region_or_zone=zone or DEFAULT_GCP_ZONE,
            project_label=project or None,
            message=message,
            source_url=GCP_AUTH_SOURCE_URL,
            cli_path=gcloud_cli,
            project_configured=bool(project),
            region_or_zone_configured=bool(zone),
            ready=ready,
            readiness_label=label,
            next_action=next_action,
        )
    raise ValueError(f"Unsupported cloud provider: {provider}")


def build_cloud_cli_install_plan(provider: str) -> CloudCliInstallPlan:
    """Build an OS-aware install command for the provider CLI.

    Commands are sourced from official provider install pages and remain user-approved.
    """

    normalized = provider.strip().lower()
    os_name = platform.system()
    machine = platform.machine().lower()
    if normalized == "aws":
        if os_name == "Darwin":
            command = (
                "curl -fsSL https://awscli.amazonaws.com/AWSCLIV2.pkg -o /tmp/AWSCLIV2.pkg "
                "&& sudo installer -pkg /tmp/AWSCLIV2.pkg -target / "
                "&& /usr/local/bin/aws --version"
            )
            requires_elevation = True
        elif os_name == "Windows":
            command = "winget install --id Amazon.AWSCLI -e"
            requires_elevation = True
        else:
            arch = "aarch64" if machine in {"arm64", "aarch64"} else "x86_64"
            command = (
                f"curl -fsSL https://awscli.amazonaws.com/awscli-exe-linux-{arch}.zip -o /tmp/awscliv2.zip "
                "&& rm -rf /tmp/aws "
                "&& unzip -q /tmp/awscliv2.zip -d /tmp "
                "&& sudo /tmp/aws/install --update "
                "&& /usr/local/bin/aws --version"
            )
            requires_elevation = True
        return CloudCliInstallPlan(
            provider="aws",
            cli_name="aws",
            command=command,
            source_url=AWS_CLI_INSTALL_SOURCE_URL,
            post_install_auth_hint="After install, authenticate with `aws configure` or `aws sso login`.",
            os_type=os_name or "Unknown",
            requires_elevation=requires_elevation,
        )
    if normalized == "gcp":
        if os_name == "Darwin":
            if shutil.which("brew"):
                command = "brew install --cask google-cloud-sdk && gcloud version"
            else:
                arch = "arm" if machine in {"arm64", "aarch64"} else "x86_64"
                command = (
                    f"curl -fsSL https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-darwin-{arch}.tar.gz "
                    "-o /tmp/google-cloud-cli.tar.gz "
                    "&& rm -rf \"$HOME/.duckln/google-cloud-sdk\" "
                    "&& mkdir -p \"$HOME/.duckln\" "
                    "&& tar -xzf /tmp/google-cloud-cli.tar.gz -C \"$HOME/.duckln\" "
                    "&& \"$HOME/.duckln/google-cloud-sdk/install.sh\" --quiet "
                    "&& \"$HOME/.duckln/google-cloud-sdk/bin/gcloud\" version"
                )
            requires_elevation = False
        elif os_name == "Windows":
            command = "winget install --id Google.CloudSDK -e"
            requires_elevation = True
        else:
            arch = "arm" if machine in {"arm64", "aarch64"} else "x86_64"
            command = (
                f"curl -fsSL https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-linux-{arch}.tar.gz "
                "-o /tmp/google-cloud-cli.tar.gz "
                "&& rm -rf \"$HOME/.duckln/google-cloud-sdk\" "
                "&& mkdir -p \"$HOME/.duckln\" "
                "&& tar -xzf /tmp/google-cloud-cli.tar.gz -C \"$HOME/.duckln\" "
                "&& \"$HOME/.duckln/google-cloud-sdk/install.sh\" --quiet "
                "&& \"$HOME/.duckln/google-cloud-sdk/bin/gcloud\" version"
            )
            requires_elevation = False
        return CloudCliInstallPlan(
            provider="gcp",
            cli_name="gcloud",
            command=command,
            source_url=GCP_CLI_INSTALL_SOURCE_URL,
            post_install_auth_hint="After install, authenticate with `gcloud auth login` and set a project.",
            os_type=os_name or "Unknown",
            requires_elevation=requires_elevation,
        )
    raise ValueError(f"Unsupported cloud provider: {provider}")


def _resolve_cloud_cli_path(cli_name: str) -> str | None:
    found = shutil.which(cli_name)
    if found:
        return found
    home = Path.home()
    candidates: tuple[Path, ...]
    if cli_name == "aws":
        candidates = (
            Path("/usr/local/bin/aws"),
            Path("/opt/homebrew/bin/aws"),
            Path("C:/Program Files/Amazon/AWSCLIV2/aws.exe"),
        )
    elif cli_name == "gcloud":
        candidates = (
            home / ".duckln" / "google-cloud-sdk" / "bin" / "gcloud",
            home / "google-cloud-sdk" / "bin" / "gcloud",
            Path("/opt/homebrew/bin/gcloud"),
            Path("/usr/local/bin/gcloud"),
            Path("C:/Program Files/Google/Cloud SDK/google-cloud-sdk/bin/gcloud.cmd"),
        )
    else:
        candidates = ()
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def _cloud_setup_hints(provider: str) -> tuple[str, str]:
    os_name = platform.system()
    if provider == "aws":
        if os_name == "Darwin":
            install = "Install AWS CLI from the official pkg, or run `brew install awscli` if Homebrew manages your CLIs."
        elif os_name == "Windows":
            install = "Install AWS CLI v2 from the official MSI, or run `winget install Amazon.AWSCLI`."
        else:
            install = "Install AWS CLI v2 using the official Linux installer for your architecture."
        auth = "Authenticate with `aws configure` or `aws sso login`, then re-run `/cloud`."
        return install, auth
    if os_name == "Darwin":
        install = "Install Google Cloud SDK from the official package, or run `brew install --cask google-cloud-sdk`."
    elif os_name == "Windows":
        install = "Install Google Cloud SDK from the official installer, or run `winget install Google.CloudSDK`."
    else:
        install = "Install Google Cloud SDK using the official Linux package instructions for your distro."
    auth = "Authenticate with `gcloud auth login`, set a project with `gcloud config set project <PROJECT_ID>`, then re-run `/cloud`."
    return install, auth


def discover_cloud_regions(provider: str, *, runner: ControlledCommandRunner | None = None) -> tuple[str, ...]:
    """Return provider-backed regions/zones for VM creation menus."""

    values, _failure = discover_cloud_regions_detailed(provider, runner=runner)
    return values


def discover_cloud_regions_detailed(
    provider: str,
    *,
    runner: ControlledCommandRunner | None = None,
    project_id: str | None = None,
) -> tuple[tuple[str, ...], CommandResult | None]:
    """Return regions/zones plus the raw failed CommandResult when discovery fails."""

    normalized = provider.strip().lower()
    runner_instance = runner or ControlledCommandRunner(execution_target="local")
    if normalized == "aws":
        return discover_aws_regions_detailed(runner=runner_instance)
    if normalized == "gcp":
        return discover_gcp_zones_detailed(runner=runner_instance, project_id=project_id)
    return ((), None)


def discover_aws_regions_detailed(
    *,
    runner: ControlledCommandRunner | None = None,
) -> tuple[tuple[str, ...], CommandResult | None]:
    """Return AWS regions with the raw CommandResult exposed on failure for classification."""

    aws_cli = _resolve_cloud_cli_path("aws")
    if aws_cli is None:
        return ((), None)
    runner_instance = runner or ControlledCommandRunner(execution_target="local")
    command = (
        f"{shlex.quote(aws_cli)} ec2 describe-regions --all-regions "
        f"--region {shlex.quote(DEFAULT_AWS_REGION)} "
        f"--query 'Regions[].RegionName' --output json"
    )
    result = runner_instance.run(command)
    if result.exit_code != 0 or result.timed_out:
        return ((), result)
    values = _json_string_list(result)
    return (values, None if values else result)


def discover_gcp_projects(*, runner: ControlledCommandRunner | None = None) -> tuple[str, ...]:
    gcloud_cli = _resolve_cloud_cli_path("gcloud")
    if gcloud_cli is None:
        return ()
    runner_instance = runner or ControlledCommandRunner(execution_target="local")
    result = runner_instance.run(f"{shlex.quote(gcloud_cli)} projects list --format=json")
    if result.exit_code != 0 or result.timed_out:
        return ()
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return ()
    projects: list[str] = []
    for item in payload if isinstance(payload, list) else []:
        if isinstance(item, dict):
            project_id = str(item.get("projectId") or "").strip()
            if project_id:
                projects.append(project_id)
    return tuple(dict.fromkeys(projects))


def create_gcp_project(
    project_id: str,
    *,
    name: str | None = None,
    runner: ControlledCommandRunner | None = None,
) -> CloudProjectCreateResult:
    """Create a GCP project with the official gcloud CLI and select it on success."""

    clean_project_id = project_id.strip()
    if not clean_project_id:
        return CloudProjectCreateResult(
            ok=False,
            provider="gcp",
            project_id="",
            message="Duckln needs a GCP project id before it can create a project.",
            source_url=GCP_PROJECT_CREATE_SOURCE_URL,
        )
    gcloud_cli = _resolve_cloud_cli_path("gcloud")
    if gcloud_cli is None:
        return CloudProjectCreateResult(
            ok=False,
            provider="gcp",
            project_id=clean_project_id,
            message="gcloud CLI is not installed or not on PATH.",
            source_url=GCP_PROJECT_CREATE_SOURCE_URL,
        )
    runner_instance = runner or ControlledCommandRunner(execution_target="local")
    command = f"{shlex.quote(gcloud_cli)} projects create {shlex.quote(clean_project_id)} --format=json --quiet"
    clean_name = (name or "").strip()
    if clean_name:
        command += f" --name {shlex.quote(clean_name)}"
    result = runner_instance.run(command, timeout_seconds=180)
    if result.exit_code != 0 or result.timed_out:
        return CloudProjectCreateResult(
            ok=False,
            provider="gcp",
            project_id=clean_project_id,
            message=f"Duckln could not create GCP project {clean_project_id}. {_first_error_line(result) or 'The command failed.'}",
            source_url=GCP_PROJECT_CREATE_SOURCE_URL,
        )
    select_result = runner_instance.run(f"{shlex.quote(gcloud_cli)} config set project {shlex.quote(clean_project_id)}")
    if select_result.exit_code != 0 or select_result.timed_out:
        return CloudProjectCreateResult(
            ok=False,
            provider="gcp",
            project_id=clean_project_id,
            message=f"GCP project {clean_project_id} was created, but Duckln could not select it. {_first_error_line(select_result) or 'The config command failed.'}",
            source_url=GCP_PROJECT_CREATE_SOURCE_URL,
        )
    return CloudProjectCreateResult(
        ok=True,
        provider="gcp",
        project_id=clean_project_id,
        message=f"GCP project {clean_project_id} created and selected. Billing may still need to be enabled before VM creation.",
        source_url=GCP_PROJECT_CREATE_SOURCE_URL,
    )


def discover_gcp_zones(*, runner: ControlledCommandRunner | None = None, project_id: str | None = None) -> tuple[str, ...]:
    values, _failure = discover_gcp_zones_detailed(runner=runner, project_id=project_id)
    return values


def discover_gcp_zones_detailed(
    *,
    runner: ControlledCommandRunner | None = None,
    project_id: str | None = None,
) -> tuple[tuple[str, ...], CommandResult | None]:
    """Return GCP zones with the raw CommandResult exposed on failure for classification."""

    gcloud_cli = _resolve_cloud_cli_path("gcloud")
    if gcloud_cli is None:
        return ((), None)
    runner_instance = runner or ControlledCommandRunner(execution_target="local")
    command = f"{shlex.quote(gcloud_cli)} compute zones list --format=json"
    if project_id:
        command += f" --project {shlex.quote(project_id)}"
    result = runner_instance.run(command)
    if result.exit_code != 0 or result.timed_out:
        return ((), result)
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return ((), result)
    zones: list[str] = []
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").strip().upper()
        name = str(item.get("name") or "").strip()
        if name and status != "DOWN":
            zones.append(name)
    return (tuple(dict.fromkeys(zones)), None)


def discover_available_cloud_shapes(
    provider: str,
    *,
    region_or_zone: str,
    runner: ControlledCommandRunner | None = None,
) -> tuple[str, ...]:
    """Return every shape name the provider reports for the zone/region (no allowlist gate)."""

    from duckln.cloud_shapes import discover_all_cloud_shapes

    normalized = provider.strip().lower()
    cli_name = "aws" if normalized == "aws" else "gcloud" if normalized == "gcp" else None
    if cli_name is None:
        return ()
    cli_path = _resolve_cloud_cli_path(cli_name)
    if cli_path is None:
        return ()
    runner_instance = runner or ControlledCommandRunner(execution_target="local")
    shapes, _failure = discover_all_cloud_shapes(
        normalized,
        runner=runner_instance,
        cli_path=cli_path,
        region_or_zone=region_or_zone,
    )
    return tuple(dict.fromkeys(shape.name for shape in shapes))


def discover_aws_key_pairs(*, runner: ControlledCommandRunner | None = None, region: str) -> tuple[str, ...]:
    aws_cli = _resolve_cloud_cli_path("aws")
    if aws_cli is None:
        return ()
    runner_instance = runner or ControlledCommandRunner(execution_target="local")
    result = runner_instance.run(
        f"{shlex.quote(aws_cli)} ec2 describe-key-pairs --region {shlex.quote(region)} --query 'KeyPairs[].KeyName' --output json"
    )
    return _json_string_list(result)


def discover_aws_subnets(*, runner: ControlledCommandRunner | None = None, region: str) -> tuple[str, ...]:
    aws_cli = _resolve_cloud_cli_path("aws")
    if aws_cli is None:
        return ()
    runner_instance = runner or ControlledCommandRunner(execution_target="local")
    result = runner_instance.run(
        f"{shlex.quote(aws_cli)} ec2 describe-subnets --region {shlex.quote(region)} --query 'Subnets[].SubnetId' --output json"
    )
    return _json_string_list(result)


def discover_aws_security_groups(*, runner: ControlledCommandRunner | None = None, region: str) -> tuple[str, ...]:
    aws_cli = _resolve_cloud_cli_path("aws")
    if aws_cli is None:
        return ()
    runner_instance = runner or ControlledCommandRunner(execution_target="local")
    result = runner_instance.run(
        f"{shlex.quote(aws_cli)} ec2 describe-security-groups --region {shlex.quote(region)} --query 'SecurityGroups[].GroupId' --output json"
    )
    return _json_string_list(result)


def _json_string_list(result: CommandResult) -> tuple[str, ...]:
    if result.exit_code != 0 or result.timed_out:
        return ()
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return ()
    if not isinstance(payload, list):
        return ()
    values = [str(item).strip() for item in payload if str(item).strip()]
    return tuple(dict.fromkeys(values))


def create_cloud_resource(
    *,
    request: CloudLaunchRequest,
    paths: ConfigPaths,
    runner: ControlledCommandRunner | None = None,
    display: Callable[[str], None] = print,
) -> CloudLaunchResult:
    """Create a bounded Duckln-tagged cloud resource through the provider CLI."""

    normalized = request.provider.strip().lower()
    runner_instance = runner or ControlledCommandRunner(trace=display, execution_target="local")
    if normalized == "aws":
        launch_command = build_aws_launch_command(request)
        launch_result = runner_instance.run(launch_command)
        if launch_result.exit_code != 0 or launch_result.timed_out:
            return CloudLaunchResult(
                ok=False,
                provider="aws",
                resource_key=None,
                display_name=request.display_name,
                shape=request.shape,
                region_or_zone=request.region_or_zone,
                message=f"Duckln could not create the AWS VM. {(_first_error_line(launch_result) or '').strip()}".strip(),
                connect_command=None,
                stop_command=None,
                cleanup_command=None,
                source_urls=(AWS_RUN_SOURCE_URL, UBUNTU_AWS_AMI_SOURCE_URL),
            )
        instance_id = (launch_result.stdout or "").strip()
        if not instance_id:
            return CloudLaunchResult(
                ok=False,
                provider="aws",
                resource_key=None,
                display_name=request.display_name,
                shape=request.shape,
                region_or_zone=request.region_or_zone,
                message="Duckln did not get an AWS instance id back from the launch command.",
                connect_command=None,
                stop_command=None,
                cleanup_command=None,
                source_urls=(AWS_RUN_SOURCE_URL, UBUNTU_AWS_AMI_SOURCE_URL),
            )
        stop_command = f"aws ec2 stop-instances --instance-ids {shlex.quote(instance_id)} --region {shlex.quote(request.region_or_zone)}"
        cleanup_command = f"aws ec2 terminate-instances --instance-ids {shlex.quote(instance_id)} --region {shlex.quote(request.region_or_zone)}"
        aws_cli = shlex.quote(_resolve_cloud_cli_path("aws") or "aws")
        describe_command = (
            f"{aws_cli} ec2 describe-instances "
            f"--instance-ids {shlex.quote(instance_id)} "
            f"--region {shlex.quote(request.region_or_zone)} "
            "--query 'Reservations[0].Instances[0].[PublicDnsName,State.Name]' --output text"
        )
        describe_result = runner_instance.run(describe_command)
        public_dns = None
        if describe_result.exit_code == 0 and not describe_result.timed_out:
            columns = (describe_result.stdout or "").split()
            if columns:
                public_dns = columns[0] if columns[0] != "None" else None
        connect_command = None
        if public_dns and request.key_pair_name:
            connect_command = f"ssh ubuntu@{public_dns}"
        _record_cloud_resource(
            paths=paths,
            provider="aws",
            resource_key=f"aws:{instance_id}",
            display_name=request.display_name,
            shape=request.shape,
            region_or_zone=request.region_or_zone,
            stop_command=stop_command,
            cleanup_command=cleanup_command,
            connect_command=connect_command,
            idle_timeout_minutes=request.idle_timeout_minutes,
            metadata={
                **default_resource_tags(
                    resource_name=request.display_name,
                    resource_kind="cloud_vm",
                    execution_target="aws",
                    repo_key=request.repo_key,
                ),
                "instance_id": instance_id,
                "launch_command": launch_command,
                "source_urls": [AWS_RUN_SOURCE_URL, UBUNTU_AWS_AMI_SOURCE_URL],
                "idle_action": "terminate",
            },
        )
        return CloudLaunchResult(
            ok=True,
            provider="aws",
            resource_key=f"aws:{instance_id}",
            display_name=request.display_name,
            shape=request.shape,
            region_or_zone=request.region_or_zone,
            message=f"Duckln created AWS instance {instance_id} in {request.region_or_zone}.",
            connect_command=connect_command,
            stop_command=stop_command,
            cleanup_command=cleanup_command,
            source_urls=(AWS_RUN_SOURCE_URL, UBUNTU_AWS_AMI_SOURCE_URL),
        )

    if normalized == "gcp":
        launch_command = build_gcp_launch_command(request)
        launch_result = runner_instance.run(launch_command)
        if launch_result.exit_code != 0 or launch_result.timed_out:
            return CloudLaunchResult(
                ok=False,
                provider="gcp",
                resource_key=None,
                display_name=request.display_name,
                shape=request.shape,
                region_or_zone=request.region_or_zone,
                message=f"Duckln could not create the GCP VM. {(_first_error_line(launch_result) or '').strip()}".strip(),
                connect_command=None,
                stop_command=None,
                cleanup_command=None,
                source_urls=(GCP_CREATE_SOURCE_URL, GCP_LABEL_SOURCE_URL),
            )
        stop_command = (
            f"{shlex.quote(_resolve_cloud_cli_path('gcloud') or 'gcloud')} compute instances stop {shlex.quote(request.display_name)} "
            f"--zone {shlex.quote(request.region_or_zone)} --quiet"
        )
        cleanup_command = (
            f"{shlex.quote(_resolve_cloud_cli_path('gcloud') or 'gcloud')} compute instances delete {shlex.quote(request.display_name)} "
            f"--zone {shlex.quote(request.region_or_zone)} --quiet"
        )
        connect_command = f"{shlex.quote(_resolve_cloud_cli_path('gcloud') or 'gcloud')} compute ssh {shlex.quote(request.display_name)} --zone {shlex.quote(request.region_or_zone)}"
        _record_cloud_resource(
            paths=paths,
            provider="gcp",
            resource_key=f"gcp:{request.display_name}:{request.region_or_zone}",
            display_name=request.display_name,
            shape=request.shape,
            region_or_zone=request.region_or_zone,
            stop_command=stop_command,
            cleanup_command=cleanup_command,
            connect_command=connect_command,
            idle_timeout_minutes=request.idle_timeout_minutes,
            metadata={
                **default_resource_tags(
                    resource_name=request.display_name,
                    resource_kind="cloud_vm",
                    execution_target="gcp",
                    repo_key=request.repo_key,
                ),
                "project_id": request.project_id,
                "launch_command": launch_command,
                "source_urls": [GCP_CREATE_SOURCE_URL, GCP_LABEL_SOURCE_URL],
                "idle_action": "terminate",
            },
        )
        return CloudLaunchResult(
            ok=True,
            provider="gcp",
            resource_key=f"gcp:{request.display_name}:{request.region_or_zone}",
            display_name=request.display_name,
            shape=request.shape,
            region_or_zone=request.region_or_zone,
            message=f"Duckln created GCP instance {request.display_name} in {request.region_or_zone}.",
            connect_command=connect_command,
            stop_command=stop_command,
            cleanup_command=cleanup_command,
            source_urls=(GCP_CREATE_SOURCE_URL, GCP_LABEL_SOURCE_URL),
        )

    raise ValueError(f"Unsupported cloud provider: {request.provider}")


def build_aws_launch_command(request: CloudLaunchRequest) -> str:
    """Build the bounded AWS CLI launch command."""

    aws_cli = shlex.quote(_resolve_cloud_cli_path("aws") or "aws")
    tags = _aws_tag_specifications(request)
    parts = [
        f"{aws_cli} ec2 run-instances",
        f"--region {shlex.quote(request.region_or_zone)}",
        "--image-id resolve:ssm:/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id",
        f"--instance-type {shlex.quote(request.shape)}",
        f"--block-device-mappings {shlex.quote(json.dumps([{'DeviceName': '/dev/sda1', 'Ebs': {'VolumeSize': request.disk_gb, 'VolumeType': 'gp3', 'DeleteOnTermination': True}}]))}",
        "--count 1",
        "--query 'Instances[0].InstanceId'",
        "--output text",
        f"--tag-specifications {shlex.quote(tags)}",
    ]
    if request.key_pair_name:
        parts.append(f"--key-name {shlex.quote(request.key_pair_name)}")
    if request.subnet_id:
        parts.append(f"--subnet-id {shlex.quote(request.subnet_id)}")
    if request.security_group_ids:
        security_group_flags = " ".join(shlex.quote(group) for group in request.security_group_ids)
        parts.append(f"--security-group-ids {security_group_flags}")
    return " ".join(parts)


def build_gcp_launch_command(request: CloudLaunchRequest) -> str:
    """Build the bounded GCP gcloud launch command."""

    gcloud_cli = shlex.quote(_resolve_cloud_cli_path("gcloud") or "gcloud")
    labels = _gcp_label_string(request)
    parts = [
        f"{gcloud_cli} compute instances create {shlex.quote(request.display_name)}",
        f"--zone {shlex.quote(request.region_or_zone)}",
        f"--machine-type {shlex.quote(request.shape)}",
        f"--boot-disk-size {shlex.quote(str(request.disk_gb))}GB",
        "--image-family ubuntu-2404-lts-amd64",
        "--image-project ubuntu-os-cloud",
        f"--labels {shlex.quote(labels)}",
        "--quiet",
    ]
    if request.project_id:
        parts.append(f"--project {shlex.quote(request.project_id)}")
    return " ".join(parts)


def discover_tagged_cloud_resources(
    provider: str,
    *,
    runner: ControlledCommandRunner | None = None,
    region_or_zone: str | None = None,
) -> tuple[DiscoveredCloudResource, ...]:
    """Discover Duckln-tagged provider resources directly from the cloud CLI."""

    normalized = provider.strip().lower()
    runner_instance = runner or ControlledCommandRunner(execution_target="local")
    if normalized == "aws":
        region = region_or_zone or DEFAULT_AWS_REGION
        command = (
            "aws ec2 describe-instances "
            f"--region {shlex.quote(region)} "
            "--filters Name=tag:duckln:managed,Values=true "
            "Name=instance-state-name,Values=pending,running,stopping,stopped "
            "--output json"
        )
        result = runner_instance.run(command)
        if result.exit_code != 0 or result.timed_out:
            return ()
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return ()
        records: list[DiscoveredCloudResource] = []
        for reservation in payload.get("Reservations", []) if isinstance(payload, dict) else []:
            if not isinstance(reservation, dict):
                continue
            for instance in reservation.get("Instances", []):
                if not isinstance(instance, dict):
                    continue
                instance_id = str(instance.get("InstanceId") or "").strip()
                if not instance_id:
                    continue
                state = ""
                state_payload = instance.get("State")
                if isinstance(state_payload, dict):
                    state = str(state_payload.get("Name") or "").strip()
                tags = {}
                for tag in instance.get("Tags", []):
                    if isinstance(tag, dict):
                        key = str(tag.get("Key") or "").strip()
                        value = str(tag.get("Value") or "").strip()
                        if key:
                            tags[key] = value
                display_name = tags.get("duckln:resource-name") or tags.get("Name") or instance_id
                public_dns = str(instance.get("PublicDnsName") or "").strip() or None
                connect_command = f"ssh ubuntu@{public_dns}" if public_dns else None
                records.append(
                    DiscoveredCloudResource(
                        provider="aws",
                        resource_key=f"aws:{instance_id}",
                        display_name=display_name,
                        region_or_zone=region,
                        shape=str(instance.get("InstanceType") or "").strip() or None,
                        state=state or "unknown",
                        connect_command=connect_command,
                        stop_command=f"aws ec2 stop-instances --instance-ids {shlex.quote(instance_id)} --region {shlex.quote(region)}",
                        cleanup_command=f"aws ec2 terminate-instances --instance-ids {shlex.quote(instance_id)} --region {shlex.quote(region)}",
                    )
                )
        return tuple(records)

    if normalized == "gcp":
        command = "gcloud compute instances list --filter='labels.duckln_managed=true' --format=json"
        result = runner_instance.run(command)
        if result.exit_code != 0 or result.timed_out:
            return ()
        try:
            payload = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            return ()
        records: list[DiscoveredCloudResource] = []
        for instance in payload if isinstance(payload, list) else []:
            if not isinstance(instance, dict):
                continue
            name = str(instance.get("name") or "").strip()
            zone_url = str(instance.get("zone") or "").strip()
            zone = zone_url.rsplit("/", 1)[-1] if zone_url else (region_or_zone or DEFAULT_GCP_ZONE)
            status = str(instance.get("status") or "").strip().lower() or "unknown"
            machine_type_url = str(instance.get("machineType") or "").strip()
            shape = machine_type_url.rsplit("/", 1)[-1] if machine_type_url else None
            labels = instance.get("labels")
            display_name = name
            if isinstance(labels, dict):
                display_name = str(labels.get("duckln_resource_name") or name).strip() or name
            records.append(
                DiscoveredCloudResource(
                    provider="gcp",
                    resource_key=f"gcp:{name}:{zone}",
                    display_name=display_name,
                    region_or_zone=zone,
                    shape=shape,
                    state=status,
                    connect_command=f"gcloud compute ssh {shlex.quote(name)} --zone {shlex.quote(zone)}",
                    stop_command=f"gcloud compute instances stop {shlex.quote(name)} --zone {shlex.quote(zone)} --quiet",
                    cleanup_command=f"gcloud compute instances delete {shlex.quote(name)} --zone {shlex.quote(zone)} --quiet",
                )
            )
        return tuple(records)

    raise ValueError(f"Unsupported cloud provider: {provider}")


def render_managed_resource_summary(config_dir) -> str:
    """Render a concise cross-provider managed resource summary."""

    records = initialize_state_store(config_dir).list_managed_resources()
    if not records:
        return "Duckln is not currently tracking any managed Docker, VM, or cloud resources."
    lines = ["Duckln-managed resources:"]
    for record in records:
        location = record.region or record.install_root or "local"
        shape = f" • {record.shape}" if record.shape else ""
        lines.append(f"- {record.display_name} [{record.provider}] {record.status} • {location}{shape}")
    return "\n".join(lines)


def resolve_managed_resource(config_dir, *, resource_key: str) -> ManagedResourceRecord | None:
    """Return one tracked managed resource."""

    for record in initialize_state_store(config_dir).list_managed_resources():
        if record.resource_key == resource_key:
            return record
    return None


def build_cloud_remote_session_plan(record: ManagedResourceRecord) -> CloudRemoteSessionPlan | None:
    """Build attach and remote-exec commands for one tracked cloud resource."""

    if record.provider not in {"aws", "gcp"}:
        return None
    connect_command = _connect_command(record)
    if connect_command is None:
        return None
    if record.provider == "aws":
        remote_prefix = f"{connect_command}"
    else:
        remote_prefix = f"{connect_command}"
    return CloudRemoteSessionPlan(
        resource_key=record.resource_key,
        provider=record.provider,
        display_name=record.display_name,
        attach_command=connect_command,
        remote_exec_prefix=remote_prefix,
        stop_command=_stop_command(record),
        cleanup_command=_cleanup_command(record),
        region_or_zone=record.region or "",
        shape=record.shape,
    )


def build_cloud_resize_commands(
    record: ManagedResourceRecord, *, resource: str, disk_gb: int = 0, instance_type: str = ""
) -> tuple[str, ...]:
    """Plan 115: cost-careful cloud resize. DISK grows the volume in place
    (`aws ec2 modify-volume` / `gcloud compute disks resize`). RAM/CPU change the
    instance type (stop → modify → start) and need a chosen `instance_type` (sizes are
    discrete, so the caller picks the smallest fitting type). Returns () when the needed
    parameter is missing or the provider is unknown — caller falls back to guidance."""
    provider = (record.provider or "").lower()
    meta = getattr(record, "metadata", {}) or {}
    if provider == "aws":
        instance_id = meta.get("instance_id", "")
        if not instance_id:
            return ()
        if resource == "disk" and disk_gb > 0:
            vol = meta.get("root_volume_id", "")
            if not vol:
                return ()
            return (f"aws ec2 modify-volume --volume-id {shlex.quote(vol)} --size {int(disk_gb)}",)
        if resource in ("ram", "cpu") and instance_type:
            return (
                f"aws ec2 stop-instances --instance-ids {shlex.quote(instance_id)}",
                f"aws ec2 modify-instance-attribute --instance-id {shlex.quote(instance_id)} --instance-type {shlex.quote(instance_type)}",
                f"aws ec2 start-instances --instance-ids {shlex.quote(instance_id)}",
            )
        return ()
    if provider == "gcp":
        name = meta.get("instance_name", "") or record.display_name
        zone = meta.get("zone", "")
        if not (name and zone):
            return ()
        if resource == "disk" and disk_gb > 0:
            disk = meta.get("disk_name", name)
            return (f"gcloud compute disks resize {shlex.quote(disk)} --zone {shlex.quote(zone)} --size {int(disk_gb)}GB --quiet",)
        if resource in ("ram", "cpu") and instance_type:
            return (
                f"gcloud compute instances stop {shlex.quote(name)} --zone {shlex.quote(zone)} --quiet",
                f"gcloud compute instances set-machine-type {shlex.quote(name)} --zone {shlex.quote(zone)} --machine-type {shlex.quote(instance_type)} --quiet",
                f"gcloud compute instances start {shlex.quote(name)} --zone {shlex.quote(zone)} --quiet",
            )
        return ()
    return ()


def build_cloud_tunnel_command(
    record: ManagedResourceRecord, *, local_port: int, remote_port: int
) -> str | None:
    """Plan 98: an `ssh -L`/`gcloud … -- -L` port-forward to the cloud instance so a
    served app is reachable at localhost:<local_port> on the host — no firewall change.
    Runs in the background (`-N -f`). Returns None when no session plan is available."""
    plan = build_cloud_remote_session_plan(record)
    if plan is None or not getattr(plan, "attach_command", ""):
        return None
    fwd = f"-L {int(local_port)}:localhost:{int(remote_port)} -N -f"
    if record.provider == "aws":
        return f"{plan.attach_command} {fwd}"
    # gcloud compute ssh passes ssh flags after `--`.
    return f"{plan.attach_command} -- {fwd}"


def build_cloud_remote_exec_command(
    record: ManagedResourceRecord,
    *,
    remote_command: str,
    remote_cwd: str | None = None,
) -> str | None:
    """Wrap one bounded command so Duckln can execute it inside the tracked cloud VM."""

    plan = build_cloud_remote_session_plan(record)
    if plan is None:
        return None
    if remote_cwd:
        shell_body = f"cd {shlex.quote(remote_cwd)} && {remote_command}"
    else:
        shell_body = remote_command
    if record.provider == "aws":
        return f"{plan.attach_command} {shlex.quote(f'bash -lc {shlex.quote(shell_body)}')}"
    return f"{plan.attach_command} --command {shlex.quote(f'bash -lc {shlex.quote(shell_body)}')}"


def stage_cloud_terminal_attach(
    *,
    record: ManagedResourceRecord,
    terminal_executor: object | None,
    display: Callable[[str], None] = print,
) -> bool:
    """Open the tracked cloud resource in Duckln's terminal pane."""

    if terminal_executor is None or not hasattr(terminal_executor, "run_terminal_command"):
        return False
    plan = build_cloud_remote_session_plan(record)
    if plan is None:
        return False
    display(
        render_tool_invocation_trace(
            title="cloud terminal attach",
            tool_id=f"cloud.{record.provider}_sdk",
            action=f"Open {record.display_name} in Duckln's terminal pane",
            detail_lines=(
                f"Provider: {record.provider.upper()}",
                f"Attach command: {plan.attach_command}",
            ),
            execution_target=record.provider,
        )
    )
    return bool(terminal_executor.run_terminal_command(command=plan.attach_command))


def record_managed_resource_activity(config_dir, *, resource_key: str) -> ManagedResourceRecord | None:
    """Refresh activity timestamp and clear idle warning state."""

    record = resolve_managed_resource(config_dir, resource_key=resource_key)
    if record is None:
        return None
    metadata = dict(record.metadata)
    metadata.pop("idle_warning_at", None)
    refreshed = replace(record, last_activity_at=_utc_now(), metadata=metadata)
    _persist_resource_record(config_dir, refreshed)
    return refreshed


def extend_managed_resource_keepalive(
    config_dir,
    *,
    resource_key: str,
    extra_minutes: int,
) -> ManagedResourceRecord | None:
    """Extend a tracked resource idle timeout and refresh its activity timestamp."""

    store = initialize_state_store(config_dir)
    for record in store.list_managed_resources():
        if record.resource_key != resource_key:
            continue
        new_timeout = max(1, int(record.idle_timeout_minutes or DEFAULT_IDLE_SHUTDOWN_MINUTES) + max(1, int(extra_minutes)))
        metadata = dict(record.metadata)
        metadata.pop("idle_warning_at", None)
        refreshed = replace(
            record,
            idle_timeout_minutes=new_timeout,
            last_activity_at=_utc_now(),
            metadata=metadata,
        )
        _persist_resource_record(config_dir, refreshed)
        return refreshed
    return None


def enforce_managed_resource_idle_policies(
    *,
    config_dir,
    runner: ControlledCommandRunner | None = None,
    display: Callable[[str], None] = print,
    now: datetime | None = None,
) -> None:
    """Warn and eventually auto-stop or terminate expired managed resources."""

    store = initialize_state_store(config_dir)
    current = now or datetime.now(timezone.utc)
    runner_instance = runner or ControlledCommandRunner(trace=display, execution_target="local")
    for record in store.list_managed_resources():
        if record.status not in {"running", "interactive", "ready", "provisioned"}:
            continue
        if record.idle_timeout_minutes is None:
            continue
        if not record.last_activity_at:
            continue
        try:
            last_activity = _parse_utc(record.last_activity_at)
        except ValueError:
            continue
        idle_deadline = last_activity + timedelta(minutes=record.idle_timeout_minutes)
        if current < idle_deadline:
            continue
        metadata = dict(record.metadata)
        warning_at = metadata.get("idle_warning_at")
        if not isinstance(warning_at, str) or not warning_at.strip():
            metadata["idle_warning_at"] = _to_iso(current)
            updated = replace(record, metadata=metadata)
            _persist_resource_record(config_dir, updated)
            display(
                f"Duckln warning: {record.display_name} has been idle for {record.idle_timeout_minutes} minutes. "
                f"Duckln will {'terminate' if _idle_action_command(record) == _cleanup_command(record) else 'stop'} it after a {IDLE_WARNING_GRACE_MINUTES}-minute grace period unless you keep it alive."
            )
            continue
        try:
            warning_time = _parse_utc(warning_at)
        except ValueError:
            warning_time = current
        if current < warning_time + timedelta(minutes=IDLE_WARNING_GRACE_MINUTES):
            continue
        action_command = _idle_action_command(record)
        if not action_command:
            continue
        result = runner_instance.run(action_command, cwd=record.install_root or None)
        metadata.pop("idle_warning_at", None)
        metadata["last_idle_action_command"] = action_command
        metadata["last_idle_action_at"] = _utc_now()
        if result.exit_code == 0 and not result.timed_out:
            stopped_status = "terminated" if record.provider in {"aws", "gcp"} and action_command == _cleanup_command(record) else "stopped"
            updated = replace(record, status=stopped_status, metadata=metadata, last_activity_at=_utc_now())
            _persist_resource_record(config_dir, updated)
            display(f"Duckln idle policy {stopped_status} {record.display_name} automatically after the warning grace period.")
        else:
            metadata["idle_action_error"] = _first_error_line(result) or "Idle action failed."
            updated = replace(record, metadata=metadata)
            _persist_resource_record(config_dir, updated)
            display(f"Duckln tried to enforce the idle policy for {record.display_name}, but the cleanup command failed.")


def cleanup_managed_resource(
    *,
    config_dir,
    resource_key: str,
    runner: ControlledCommandRunner | None = None,
    destructive: bool = True,
) -> tuple[bool, str]:
    """Run the tracked stop or cleanup command for one managed resource."""

    store = initialize_state_store(config_dir)
    runner_instance = runner or ControlledCommandRunner(execution_target="local")
    for record in store.list_managed_resources():
        if record.resource_key != resource_key:
            continue
        command = _cleanup_command(record) if destructive else _stop_command(record)
        if not command:
            return False, f"Duckln does not have a tracked {'cleanup' if destructive else 'stop'} command for {record.display_name}."
        result = runner_instance.run(command, cwd=record.install_root or None)
        metadata = dict(record.metadata)
        metadata["last_cleanup_command"] = command
        metadata["last_cleanup_at"] = _utc_now()
        if result.exit_code == 0 and not result.timed_out:
            updated = replace(record, status="terminated" if destructive else "stopped", metadata=metadata, last_activity_at=_utc_now())
            _persist_resource_record(config_dir, updated)
            return True, f"Duckln {'removed' if destructive else 'stopped'} {record.display_name}."
        metadata["last_cleanup_error"] = _first_error_line(result) or "Cleanup failed."
        updated = replace(record, metadata=metadata)
        _persist_resource_record(config_dir, updated)
        return False, f"Duckln could not {'remove' if destructive else 'stop'} {record.display_name} cleanly."
    return False, "Duckln could not find that managed resource."


def _record_cloud_resource(
    *,
    paths: ConfigPaths,
    provider: str,
    resource_key: str,
    display_name: str,
    shape: str,
    region_or_zone: str,
    stop_command: str | None,
    cleanup_command: str | None,
    connect_command: str | None,
    idle_timeout_minutes: int,
    metadata: dict[str, object],
) -> None:
    store = initialize_state_store(paths.config_dir)
    resource_metadata = dict(metadata)
    if stop_command:
        resource_metadata["stop_command"] = stop_command
    if cleanup_command:
        resource_metadata["cleanup_command"] = cleanup_command
    if connect_command:
        resource_metadata["connect_command"] = connect_command
    store.upsert_managed_resource(
        resource_key=resource_key,
        resource_kind="cloud_vm",
        provider=provider,
        display_name=display_name,
        execution_target=provider,
        region=region_or_zone,
        shape=shape,
        status="running",
        idle_timeout_minutes=idle_timeout_minutes,
        last_activity_at=_utc_now(),
        metadata=resource_metadata,
    )


def _connect_command(record: ManagedResourceRecord) -> str | None:
    value = record.metadata.get("connect_command")
    return str(value).strip() or None if isinstance(value, str) else None


def _persist_resource_record(config_dir, record: ManagedResourceRecord) -> None:
    initialize_state_store(config_dir).upsert_managed_resource(
        resource_key=record.resource_key,
        resource_kind=record.resource_kind,
        provider=record.provider,
        display_name=record.display_name,
        execution_target=record.execution_target,
        region=record.region,
        shape=record.shape,
        install_root=record.install_root,
        status=record.status,
        idle_timeout_minutes=record.idle_timeout_minutes,
        last_activity_at=record.last_activity_at,
        metadata=record.metadata,
    )


def _aws_tag_specifications(request: CloudLaunchRequest) -> str:
    tags = [
        ("duckln:managed", "true"),
        ("duckln:resource-name", request.display_name),
        ("duckln:resource-kind", "cloud_vm"),
        ("duckln:execution-target", "aws"),
    ]
    if request.repo_key:
        tags.append(("duckln:repo-key", request.repo_key))
    rendered = ",".join(f"{{Key={key},Value={value}}}" for key, value in tags)
    return f"ResourceType=instance,Tags=[{rendered}] ResourceType=volume,Tags=[{rendered}]"


def _gcp_label_string(request: CloudLaunchRequest) -> str:
    labels = {
        "duckln_managed": "true",
        "duckln_resource_name": _sanitize_gcp_label_value(request.display_name),
        "duckln_resource_kind": "cloud-vm",
        "duckln_execution_target": "gcp",
    }
    if request.repo_key:
        labels["duckln_repo_key"] = _sanitize_gcp_label_value(request.repo_key)
    return ",".join(f"{key}={value}" for key, value in labels.items())


def _sanitize_gcp_label_value(value: str) -> str:
    lowered = re.sub(r"[^a-z0-9_-]+", "-", value.strip().lower())
    lowered = lowered.strip("-_") or "duckln"
    return lowered[:63]


def _idle_action_command(record: ManagedResourceRecord) -> str | None:
    idle_action = str(record.metadata.get("idle_action") or "").strip().lower()
    cleanup = _cleanup_command(record)
    stop = _stop_command(record)
    if idle_action == "stop":
        return stop or cleanup
    return cleanup or stop


def _stop_command(record: ManagedResourceRecord) -> str | None:
    value = record.metadata.get("stop_command")
    return str(value).strip() or None if isinstance(value, str) else None


def _cleanup_command(record: ManagedResourceRecord) -> str | None:
    value = record.metadata.get("cleanup_command")
    return str(value).strip() or None if isinstance(value, str) else None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _first_error_line(result: CommandResult) -> str | None:
    text = result.stderr or result.stdout
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None
