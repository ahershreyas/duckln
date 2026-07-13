"""Tests for runtime governance helpers."""

from __future__ import annotations

import unittest

from duckln.runtime_governance import (
    DEFAULT_IDLE_SHUTDOWN_MINUTES,
    approved_cloud_shapes,
    default_resource_tags,
    describe_runtime_transport,
    format_usage_footer,
)


class RuntimeGovernanceTest(unittest.TestCase):
    def test_approved_cloud_shapes_expose_shortlist(self) -> None:
        aws = approved_cloud_shapes("aws")

        self.assertTrue(aws)
        self.assertTrue(any(shape.shape == "g5.xlarge" for shape in aws))

    def test_default_resource_tags_include_duckln_markers(self) -> None:
        tags = default_resource_tags(
            resource_name="duckln-vm",
            resource_kind="vm",
            execution_target="vm",
            repo_key="https://example.com/repo",
        )

        self.assertEqual("true", tags["duckln:managed"])
        self.assertEqual("https://example.com/repo", tags["duckln:repo-key"])

    def test_describe_runtime_transport_detects_docker_run_name(self) -> None:
        hint = describe_runtime_transport(
            "docker run --name whisper-app -p 8080:8080 whisper:latest",
            cwd="/tmp/whisper",
            repo_name="whisper",
        )

        self.assertEqual("docker", hint.connection_type)
        self.assertEqual("whisper-app", hint.docker_name)
        self.assertEqual("docker stop whisper-app", hint.stop_command)

    def test_describe_runtime_transport_detects_compose_project(self) -> None:
        hint = describe_runtime_transport(
            "docker compose up -d",
            cwd="/tmp/open-webui",
            repo_name="open-webui",
        )

        self.assertEqual("docker", hint.connection_type)
        self.assertEqual("open-webui", hint.docker_name)
        self.assertEqual("docker compose down", hint.stop_command)

    def test_format_usage_footer_keeps_cost_optional(self) -> None:
        self.assertEqual("tokens 10/5/15", format_usage_footer(prompt_tokens=10, completion_tokens=5, total_tokens=15, estimated_cost_usd=None))
        self.assertIn(f"{DEFAULT_IDLE_SHUTDOWN_MINUTES}", str(DEFAULT_IDLE_SHUTDOWN_MINUTES))


if __name__ == "__main__":
    unittest.main()
