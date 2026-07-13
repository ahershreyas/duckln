"""Tests for the provider-backed VM shape discovery + categorization helpers."""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock

from duckln.cloud_shapes import (
    DiscoveredCloudShape,
    derived_default_disk_gb,
    discover_all_aws_shapes,
    discover_all_cloud_shapes,
    discover_all_gcp_shapes,
    group_shapes_into_categories,
    render_shape_label,
)
from duckln.shell import CommandResult


def _cmd(*, stdout: str = "", stderr: str = "", exit_code: int = 0) -> CommandResult:
    return CommandResult(
        command="dummy",
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        timed_out=False,
        duration_seconds=0.0,
    )


_GCP_SAMPLE = [
    {"name": "e2-standard-2", "guestCpus": 2, "memoryMb": 8192, "accelerators": []},
    {"name": "n2-highmem-32", "guestCpus": 32, "memoryMb": 262144, "accelerators": []},
    {"name": "c3-standard-4", "guestCpus": 4, "memoryMb": 16384, "accelerators": []},
    {
        "name": "g2-standard-8",
        "guestCpus": 8,
        "memoryMb": 32768,
        "accelerators": [
            {"guestAcceleratorType": "nvidia-l4", "guestAcceleratorCount": 1}
        ],
    },
    {
        "name": "a2-highgpu-1g",
        "guestCpus": 12,
        "memoryMb": 87040,
        "accelerators": [
            {"guestAcceleratorType": "nvidia-tesla-a100", "guestAcceleratorCount": 1}
        ],
    },
    {
        "name": "a3-highgpu-2g",
        "guestCpus": 26,
        "memoryMb": 234000,
        "accelerators": [
            {"guestAcceleratorType": "nvidia-h100-80gb", "guestAcceleratorCount": 2}
        ],
    },
]

_AWS_SAMPLE = {
    "InstanceTypes": [
        {
            "InstanceType": "m5.large",
            "VCpuInfo": {"DefaultVCpus": 2},
            "MemoryInfo": {"SizeInMiB": 8192},
        },
        {
            "InstanceType": "c5.4xlarge",
            "VCpuInfo": {"DefaultVCpus": 16},
            "MemoryInfo": {"SizeInMiB": 32768},
        },
        {
            "InstanceType": "g4dn.xlarge",
            "VCpuInfo": {"DefaultVCpus": 4},
            "MemoryInfo": {"SizeInMiB": 16384},
            "GpuInfo": {"Gpus": [{"Name": "T4", "Count": 1}]},
        },
        {
            "InstanceType": "p4d.24xlarge",
            "VCpuInfo": {"DefaultVCpus": 96},
            "MemoryInfo": {"SizeInMiB": 1179648},
            "GpuInfo": {"Gpus": [{"Name": "A100", "Count": 8}]},
        },
    ]
}


class GcpParserTests(unittest.TestCase):
    def test_parses_cpu_shapes_without_gpu(self) -> None:
        runner = MagicMock()
        runner.run.return_value = _cmd(stdout=json.dumps(_GCP_SAMPLE))
        shapes, failure = discover_all_gcp_shapes(
            runner=runner, gcloud_cli="/usr/bin/gcloud", zone="us-central1-a"
        )
        self.assertIsNone(failure)
        names = {s.name for s in shapes}
        self.assertIn("e2-standard-2", names)
        self.assertIn("n2-highmem-32", names)
        e2 = next(s for s in shapes if s.name == "e2-standard-2")
        self.assertEqual(e2.cpu_count, 2)
        self.assertAlmostEqual(e2.memory_gb, 8.0, places=1)
        self.assertIsNone(e2.gpu_kind)
        self.assertEqual(e2.gpu_count, 0)
        self.assertEqual(e2.family, "e2")

    def test_parses_gpu_shapes_and_normalizes_kind(self) -> None:
        runner = MagicMock()
        runner.run.return_value = _cmd(stdout=json.dumps(_GCP_SAMPLE))
        shapes, _ = discover_all_gcp_shapes(
            runner=runner, gcloud_cli="/usr/bin/gcloud", zone="us-central1-a"
        )
        g2 = next(s for s in shapes if s.name == "g2-standard-8")
        self.assertEqual(g2.gpu_kind, "L4")
        self.assertEqual(g2.gpu_count, 1)
        a2 = next(s for s in shapes if s.name == "a2-highgpu-1g")
        self.assertEqual(a2.gpu_kind, "A100")
        self.assertEqual(a2.gpu_count, 1)
        a3 = next(s for s in shapes if s.name == "a3-highgpu-2g")
        self.assertEqual(a3.gpu_kind, "H100")
        self.assertEqual(a3.gpu_count, 2)

    def test_returns_failure_on_nonzero_exit(self) -> None:
        runner = MagicMock()
        runner.run.return_value = _cmd(exit_code=1, stderr="PERMISSION_DENIED")
        shapes, failure = discover_all_gcp_shapes(
            runner=runner, gcloud_cli="/usr/bin/gcloud", zone="us-central1-a"
        )
        self.assertEqual(shapes, ())
        self.assertIsNotNone(failure)


class AwsParserTests(unittest.TestCase):
    def test_parses_cpu_and_gpu_shapes(self) -> None:
        runner = MagicMock()
        runner.run.return_value = _cmd(stdout=json.dumps(_AWS_SAMPLE))
        shapes, _failure = discover_all_aws_shapes(
            runner=runner, aws_cli="/usr/bin/aws", region="us-east-1"
        )
        self.assertEqual(len(shapes), 4)
        m5 = next(s for s in shapes if s.name == "m5.large")
        self.assertEqual(m5.cpu_count, 2)
        self.assertEqual(m5.gpu_kind, None)
        g4 = next(s for s in shapes if s.name == "g4dn.xlarge")
        self.assertEqual(g4.gpu_kind, "T4")
        self.assertEqual(g4.gpu_count, 1)
        p4 = next(s for s in shapes if s.name == "p4d.24xlarge")
        self.assertEqual(p4.gpu_kind, "A100")
        self.assertEqual(p4.gpu_count, 8)


class DispatcherTests(unittest.TestCase):
    def test_dispatch_routes_by_provider(self) -> None:
        runner = MagicMock()
        runner.run.return_value = _cmd(stdout=json.dumps(_GCP_SAMPLE))
        shapes_gcp, _ = discover_all_cloud_shapes(
            "gcp", runner=runner, cli_path="/usr/bin/gcloud", region_or_zone="us-central1-a"
        )
        self.assertEqual(len(shapes_gcp), 6)
        runner.run.return_value = _cmd(stdout=json.dumps(_AWS_SAMPLE))
        shapes_aws, _ = discover_all_cloud_shapes(
            "aws", runner=runner, cli_path="/usr/bin/aws", region_or_zone="us-east-1"
        )
        self.assertEqual(len(shapes_aws), 4)

    def test_unknown_provider_returns_empty(self) -> None:
        runner = MagicMock()
        shapes, failure = discover_all_cloud_shapes(
            "azure", runner=runner, cli_path="/usr/bin/az", region_or_zone="eastus"
        )
        self.assertEqual(shapes, ())
        self.assertIsNone(failure)
        runner.run.assert_not_called()


class CategorizerTests(unittest.TestCase):
    def _build_shapes(self) -> tuple[DiscoveredCloudShape, ...]:
        return (
            DiscoveredCloudShape("gcp", "e2-standard-4", 4, 16.0, None, 0, "e2"),
            DiscoveredCloudShape("gcp", "c3-standard-8", 8, 32.0, None, 0, "c3"),
            DiscoveredCloudShape("gcp", "n2-highmem-32", 32, 256.0, None, 0, "n2"),
            DiscoveredCloudShape("gcp", "g2-standard-8", 8, 32.0, "L4", 1, "g2"),
            DiscoveredCloudShape("gcp", "a2-highgpu-1g", 12, 85.0, "A100", 1, "a2"),
            DiscoveredCloudShape("gcp", "a3-highgpu-2g", 26, 234.0, "H100", 2, "a3"),
        )

    def test_grouping_puts_shapes_in_expected_buckets(self) -> None:
        categories = group_shapes_into_categories(self._build_shapes())
        labels = [c.label for c in categories]
        self.assertIn("CPU - General Purpose", labels)
        self.assertIn("CPU - Compute Optimized", labels)
        self.assertIn("CPU - Memory Optimized", labels)
        self.assertIn("GPU - L4", labels)
        self.assertIn("GPU - A100", labels)
        self.assertIn("GPU - H100", labels)

    def test_gpu_buckets_ordered_by_count_then_cpu(self) -> None:
        shapes = (
            DiscoveredCloudShape("gcp", "g2-standard-8", 8, 32.0, "L4", 1, "g2"),
            DiscoveredCloudShape("gcp", "g2-standard-4", 4, 16.0, "L4", 1, "g2"),
        )
        categories = group_shapes_into_categories(shapes)
        l4 = next(c for c in categories if c.label == "GPU - L4")
        self.assertEqual(l4.shapes[0].name, "g2-standard-4")
        self.assertEqual(l4.shapes[1].name, "g2-standard-8")


class DiskAndLabelTests(unittest.TestCase):
    def test_derived_disk_gpu(self) -> None:
        shape = DiscoveredCloudShape("gcp", "g2-standard-8", 8, 32.0, "L4", 1, "g2")
        self.assertEqual(derived_default_disk_gb(shape), 80)

    def test_derived_disk_large_cpu(self) -> None:
        shape = DiscoveredCloudShape("gcp", "n2-standard-32", 32, 128.0, None, 0, "n2")
        self.assertEqual(derived_default_disk_gb(shape), 60)

    def test_derived_disk_small_cpu(self) -> None:
        shape = DiscoveredCloudShape("gcp", "e2-standard-2", 2, 8.0, None, 0, "e2")
        self.assertEqual(derived_default_disk_gb(shape), 30)

    def test_label_format_gpu(self) -> None:
        shape = DiscoveredCloudShape("gcp", "a3-highgpu-2g", 26, 234.0, "H100", 2, "a3")
        label = render_shape_label(shape)
        self.assertIn("a3-highgpu-2g", label)
        self.assertIn("26 CPU", label)
        self.assertIn("234 GB RAM", label)
        self.assertIn("80 GB disk", label)
        self.assertIn("2× H100 GPU", label)

    def test_label_format_cpu(self) -> None:
        shape = DiscoveredCloudShape("gcp", "c3-standard-4", 4, 16.0, None, 0, "c3")
        label = render_shape_label(shape)
        self.assertIn("c3-standard-4", label)
        self.assertIn("Compute CPU", label)


if __name__ == "__main__":
    unittest.main()
