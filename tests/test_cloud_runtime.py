"""Tests for bounded cloud runtime helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import tempfile
import unittest
from unittest.mock import patch

from duckln.cloud_runtime import (
    CloudLaunchRequest,
    build_cloud_cli_install_plan,
    build_cloud_remote_exec_command,
    build_aws_launch_command,
    build_gcp_launch_command,
    create_cloud_resource,
    create_gcp_project,
    discover_available_cloud_shapes,
    discover_cloud_regions,
    discover_tagged_cloud_resources,
    enforce_managed_resource_idle_policies,
    inspect_cloud_auth,
)
from duckln.config import resolve_config_paths
from duckln.shell import CommandResult
from state.store import initialize_state_store


class FakeRunner:
    def __init__(self, responses: dict[str, CommandResult]) -> None:
        self.responses = responses
        self.commands: list[str] = []

    def run(self, command: str, *, timeout_seconds: float = 30.0, cwd: str | None = None, env=None) -> CommandResult:
        self.commands.append(command)
        return self.responses.get(
            command,
            CommandResult(
                command=command,
                exit_code=0,
                stdout="",
                stderr="",
                timed_out=False,
                duration_seconds=0.01,
            ),
        )


def _ok(command: str, *, stdout: str = "") -> CommandResult:
    return CommandResult(
        command=command,
        exit_code=0,
        stdout=stdout,
        stderr="",
        timed_out=False,
        duration_seconds=0.01,
    )


class CloudRuntimeTest(unittest.TestCase):
    def test_inspect_cloud_auth_reports_missing_aws_cli(self) -> None:
        with patch("duckln.cloud_runtime.shutil.which", return_value=None):
            status = inspect_cloud_auth("aws")

        self.assertFalse(status.cli_available)
        self.assertFalse(status.authenticated)
        self.assertIn("not installed", status.message.lower())
        self.assertIsNotNone(status.install_hint)
        self.assertIn("install", status.install_hint.lower())
        self.assertIn("getting-started-install", status.source_url)

    def test_inspect_cloud_auth_reports_missing_gcloud_cli_with_next_steps(self) -> None:
        with patch("duckln.cloud_runtime.shutil.which", return_value=None), patch(
            "duckln.cloud_runtime.Path.exists", return_value=False
        ):
            status = inspect_cloud_auth("gcp")

        self.assertFalse(status.cli_available)
        self.assertFalse(status.authenticated)
        self.assertIsNotNone(status.install_hint)
        self.assertIsNotNone(status.auth_hint)
        self.assertIn("sdk/docs/install", status.source_url)

    def test_inspect_cloud_auth_empty_gcp_account_needs_login_not_ready(self) -> None:
        runner = FakeRunner(
            {
                "/opt/homebrew/bin/gcloud auth list --filter=status:ACTIVE --format=json": _ok(
                    "/opt/homebrew/bin/gcloud auth list --filter=status:ACTIVE --format=json",
                    stdout="[]",
                ),
                "/opt/homebrew/bin/gcloud config get-value project": _ok(
                    "/opt/homebrew/bin/gcloud config get-value project",
                    stdout="demo-project\n",
                ),
                "/opt/homebrew/bin/gcloud config get-value compute/zone": _ok(
                    "/opt/homebrew/bin/gcloud config get-value compute/zone",
                    stdout="us-central1-a\n",
                ),
            }
        )
        with patch("duckln.cloud_runtime.shutil.which", return_value="/opt/homebrew/bin/gcloud"):
            status = inspect_cloud_auth("gcp", runner=runner)

        self.assertFalse(status.authenticated)
        self.assertFalse(status.ready)
        self.assertEqual("GCP needs login", status.readiness_label)
        self.assertIn("no active authenticated account", status.message)

    def test_discover_cloud_regions_uses_provider_cli_inventory(self) -> None:
        # Bootstrap --region is passed so discovery still works when no region is configured;
        # without it, aws ec2 describe-regions would error with "You must specify a region".
        command = (
            "/usr/local/bin/aws ec2 describe-regions --all-regions "
            "--region us-east-1 --query 'Regions[].RegionName' --output json"
        )
        runner = FakeRunner({command: _ok(command, stdout='["us-east-1","eu-west-2"]')})

        with patch("duckln.cloud_runtime.shutil.which", return_value="/usr/local/bin/aws"):
            regions = discover_cloud_regions("aws", runner=runner)

        self.assertEqual(("us-east-1", "eu-west-2"), regions)

    def test_discover_available_cloud_shapes_returns_full_provider_catalog(self) -> None:
        # New contract: no approved-list filter; every shape the provider returns is included.
        command = (
            "/usr/local/bin/aws ec2 describe-instance-types "
            "--region us-east-1 --output json --no-cli-pager"
        )
        payload = {
            "InstanceTypes": [
                {"InstanceType": "t3.large", "VCpuInfo": {"DefaultVCpus": 2}, "MemoryInfo": {"SizeInMiB": 8192}},
                {"InstanceType": "m7i.large", "VCpuInfo": {"DefaultVCpus": 2}, "MemoryInfo": {"SizeInMiB": 8192}},
                {
                    "InstanceType": "p4d.24xlarge",
                    "VCpuInfo": {"DefaultVCpus": 96},
                    "MemoryInfo": {"SizeInMiB": 1179648},
                    "GpuInfo": {"Gpus": [{"Name": "A100", "Count": 8}]},
                },
            ]
        }
        runner = FakeRunner({command: _ok(command, stdout=json.dumps(payload))})

        with patch("duckln.cloud_runtime.shutil.which", return_value="/usr/local/bin/aws"):
            shapes = discover_available_cloud_shapes("aws", region_or_zone="us-east-1", runner=runner)

        self.assertEqual(("t3.large", "m7i.large", "p4d.24xlarge"), shapes)

    def test_create_gcp_project_uses_official_cli_and_selects_project(self) -> None:
        create_command = "/opt/homebrew/bin/gcloud projects create duckln-demo --format=json --quiet --name 'Duckln Demo'"
        select_command = "/opt/homebrew/bin/gcloud config set project duckln-demo"
        runner = FakeRunner(
            {
                create_command: _ok(create_command, stdout='{"projectId":"duckln-demo"}'),
                select_command: _ok(select_command, stdout="Updated property [core/project]."),
            }
        )

        with patch("duckln.cloud_runtime.shutil.which", return_value="/opt/homebrew/bin/gcloud"):
            result = create_gcp_project("duckln-demo", name="Duckln Demo", runner=runner)

        self.assertTrue(result.ok)
        self.assertEqual("duckln-demo", result.project_id)
        self.assertEqual([create_command, select_command], runner.commands)
        self.assertIn("gcloud/reference/projects/create", result.source_url)

    def test_build_cloud_cli_install_plan_uses_official_provider_sources(self) -> None:
        with patch("duckln.cloud_runtime.platform.system", return_value="Darwin"), patch(
            "duckln.cloud_runtime.shutil.which", return_value="/opt/homebrew/bin/brew"
        ):
            gcp_plan = build_cloud_cli_install_plan("gcp")
        with patch("duckln.cloud_runtime.platform.system", return_value="Darwin"):
            aws_plan = build_cloud_cli_install_plan("aws")

        self.assertIn("brew install --cask google-cloud-sdk", gcp_plan.command)
        self.assertIn("cloud.google.com/sdk/docs/install", gcp_plan.source_url)
        self.assertIn("AWSCLIV2.pkg", aws_plan.command)
        self.assertIn("docs.aws.amazon.com/cli", aws_plan.source_url)

    def test_build_aws_launch_command_contains_shortlist_and_tags(self) -> None:
        request = CloudLaunchRequest(
            provider="aws",
            display_name="duckln-aws",
            shape="t3.large",
            cpu_count=2,
            memory_gb=8,
            disk_gb=30,
            region_or_zone="us-east-1",
            idle_timeout_minutes=30,
            key_pair_name="duckln-key",
            security_group_ids=("sg-123",),
        )

        command = build_aws_launch_command(request)

        self.assertIn("aws ec2 run-instances", command)
        self.assertIn("--image-id resolve:ssm:/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id", command)
        self.assertIn("--instance-type t3.large", command)
        self.assertIn("--key-name duckln-key", command)
        self.assertIn("duckln:managed", command)

    def test_build_gcp_launch_command_contains_labels_and_image_family(self) -> None:
        request = CloudLaunchRequest(
            provider="gcp",
            display_name="duckln-gcp",
            shape="e2-standard-4",
            cpu_count=4,
            memory_gb=16,
            disk_gb=30,
            region_or_zone="us-central1-a",
            idle_timeout_minutes=30,
            project_id="demo-project",
        )

        command = build_gcp_launch_command(request)

        self.assertIn("gcloud compute instances create duckln-gcp", command)
        self.assertIn("--image-family ubuntu-2404-lts-amd64", command)
        self.assertIn("--image-project ubuntu-os-cloud", command)
        self.assertIn("--project demo-project", command)
        self.assertIn("duckln_managed=true", command)

    def test_create_gcp_cloud_resource_records_managed_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            request = CloudLaunchRequest(
                provider="gcp",
                display_name="duckln-gcp",
                shape="e2-standard-4",
                cpu_count=4,
                memory_gb=16,
                disk_gb=30,
                region_or_zone="us-central1-a",
                idle_timeout_minutes=30,
                project_id="demo-project",
            )
            command = build_gcp_launch_command(request)
            runner = FakeRunner({command: _ok(command, stdout="created\n")})

            result = create_cloud_resource(request=request, paths=paths, runner=runner, display=lambda _line: None)

            self.assertTrue(result.ok)
            records = initialize_state_store(paths.config_dir).list_managed_resources(provider="gcp")
            self.assertEqual(1, len(records))
            self.assertEqual("duckln-gcp", records[0].display_name)
            self.assertEqual("running", records[0].status)
            self.assertEqual("gcp", records[0].execution_target)

    def test_discover_tagged_gcp_resources_parses_list_output(self) -> None:
        command = "gcloud compute instances list --filter='labels.duckln_managed=true' --format=json"
        payload = [
            {
                "name": "duckln-gcp",
                "zone": "https://www.googleapis.com/compute/v1/projects/demo/zones/us-central1-a",
                "machineType": "https://www.googleapis.com/compute/v1/projects/demo/zones/us-central1-a/machineTypes/e2-standard-4",
                "status": "RUNNING",
                "labels": {"duckln_managed": "true", "duckln_resource_name": "duckln-gcp"},
            }
        ]
        runner = FakeRunner({command: _ok(command, stdout=json.dumps(payload))})

        resources = discover_tagged_cloud_resources("gcp", runner=runner)

        self.assertEqual(1, len(resources))
        self.assertEqual("duckln-gcp", resources[0].display_name)
        self.assertEqual("us-central1-a", resources[0].region_or_zone)

    def test_build_cloud_remote_exec_command_wraps_gcp_transport(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            store = initialize_state_store(paths.config_dir)
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
            record = store.list_managed_resources(provider="gcp")[0]

            wrapped = build_cloud_remote_exec_command(
                record,
                remote_command="python3 --version",
                remote_cwd="~/.duckln",
            )

            self.assertIsNotNone(wrapped)
            self.assertIn("gcloud compute ssh duckln-gcp --zone us-central1-a --command", wrapped)
            self.assertIn("python3 --version", wrapped)
            self.assertIn("~/.duckln", wrapped)

    def test_idle_enforcement_warns_then_cleans_up(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            store = initialize_state_store(paths.config_dir)
            stale_activity = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat()
            stop_command = "docker stop whisper-app"
            store.upsert_managed_resource(
                resource_key="docker:whisper-app",
                resource_kind="docker_container",
                provider="docker",
                display_name="whisper-app",
                execution_target="docker",
                status="running",
                idle_timeout_minutes=30,
                last_activity_at=stale_activity,
                metadata={"stop_command": stop_command, "idle_action": "stop"},
            )
            displayed: list[str] = []
            runner = FakeRunner({stop_command: _ok(stop_command)})

            warning_time = datetime.now(timezone.utc)
            enforce_managed_resource_idle_policies(
                config_dir=paths.config_dir,
                runner=runner,
                display=displayed.append,
                now=warning_time,
            )
            enforce_managed_resource_idle_policies(
                config_dir=paths.config_dir,
                runner=runner,
                display=displayed.append,
                now=warning_time + timedelta(minutes=6),
            )

            records = store.list_managed_resources(provider="docker")
            self.assertEqual("stopped", records[0].status)
            self.assertTrue(any("warning" in line.lower() for line in displayed))
            self.assertTrue(any("automatically" in line.lower() for line in displayed))
