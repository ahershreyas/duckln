"""Tests for deterministic repair intake and dependency approval evidence."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from duckln.repair_intake import (
    DependencyApprovalDecision,
    build_approved_dependency_command,
    build_dependency_approval_request,
    build_runtime_dependency_repair_request,
    compress_failure_for_remote,
    infer_runtime_dependency_install_command,
    plan_runtime_repair,
    render_dependency_approval_lines,
    summarize_failure_incident,
)


class RepairIntakeTest(unittest.TestCase):
    def test_summarize_failure_incident_extracts_high_signal_without_raw_log_dump(self) -> None:
        incident = summarize_failure_incident(
            command=".venv/bin/python -m pip install -r requirements.txt",
            stderr=(
                "Collecting torch\n"
                "ERROR: Could not find a version that satisfies the requirement torch==9.9.9\n"
                "ERROR: No matching distribution found for torch==9.9.9\n"
            ),
        )

        self.assertEqual("dependency_install_failure", incident.category)
        self.assertIn("torch", incident.summary.lower())
        self.assertLess(len(incident.summary), 421)

    def test_summarize_failure_incident_detects_ubuntu_quoted_command_not_found(self) -> None:
        incident = summarize_failure_incident(
            command=None,
            stderr="Command 'multipass' not found, but can be installed with:\nsudo snap install multipass",
        )

        self.assertEqual("missing_command", incident.category)
        self.assertEqual("multipass", incident.package_hint)
        self.assertIn("multipass", incident.summary.lower())

    def test_summarize_failure_incident_prefers_signal_line_over_vm_banner_noise(self) -> None:
        incident = summarize_failure_incident(
            command="run openclaw",
            stderr=(
                "Users logged in: 0\n"
                "Last login: Mon May 4 01:17:49 2026\n"
                "Command 'multipass' not found, but can be installed with:\n"
                "sudo snap install multipass\n"
            ),
        )

        self.assertIn("command 'multipass' not found", str(incident.fatal_line or "").lower())
        self.assertEqual("missing_command", incident.category)

    def test_compress_failure_for_remote_prefers_summary_over_raw_stderr(self) -> None:
        compressed = compress_failure_for_remote(
            command="python app.py",
            stderr="\n".join(f"line {index}" for index in range(30)) + "\nTraceback (most recent call last):\nModuleNotFoundError: No module named 'fastapi'",
        )

        self.assertIn("missing python module", compressed.lower())
        self.assertIn("fastapi", compressed.lower())
        self.assertLess(len(compressed), 421)

    def test_build_dependency_approval_request_parses_requirements_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            requirements = project_dir / "requirements.txt"
            requirements.write_text("requests==2.32.0\nnumpy>=1.26\n", encoding="utf-8")

            request = build_dependency_approval_request(
                prompt="Install requirements",
                command=".venv/bin/python -m pip install -r requirements.txt",
                project_dir=project_dir,
            )

        self.assertEqual(2, len(request.items))
        self.assertEqual("requests", request.items[0].dependency)
        self.assertEqual("==2.32.0", request.items[0].version)
        self.assertTrue(request.manifest_paths)
        self.assertTrue(request.source_urls)

    def test_render_dependency_approval_lines_includes_structured_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            package_json = project_dir / "package.json"
            package_json.write_text(
                '{"dependencies": {"next": "14.2.0"}, "devDependencies": {"typescript": "5.5.0"}}',
                encoding="utf-8",
            )
            request = build_dependency_approval_request(
                prompt="Install node dependencies",
                command="npm install",
                project_dir=project_dir,
            )

        lines = render_dependency_approval_lines(request)

        self.assertIn("Duckln dependency approval review:", lines[0])
        self.assertTrue(any("next 14.2.0" in line for line in lines))
        self.assertTrue(any("typescript 5.5.0" in line for line in lines))

    def test_build_approved_dependency_command_can_materialize_partial_python_install(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "requirements.txt").write_text("requests==2.32.0\nnumpy>=1.26\n", encoding="utf-8")
            request = build_dependency_approval_request(
                prompt="Install requirements",
                command=".venv/bin/python -m pip install -r requirements.txt",
                project_dir=project_dir,
            )

        command = build_approved_dependency_command(
            request=request,
            decision=DependencyApprovalDecision(
                approved=True,
                approve_all=False,
                selected_item_ids=("req:requests",),
            ),
        )

        self.assertEqual(".venv/bin/python -m pip install requests==2.32.0", command)

    def test_infer_runtime_dependency_install_command_prefers_manifest_backed_smallest_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "package.json").write_text('{"dependencies":{"next":"14.2.0"}}', encoding="utf-8")
            (project_dir / "package-lock.json").write_text("{}", encoding="utf-8")

            command = infer_runtime_dependency_install_command(project_dir)

        self.assertEqual("npm ci", command)

    def test_build_runtime_dependency_repair_request_uses_repo_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "requirements.txt").write_text("fastapi==0.111.0\nuvicorn==0.30.0\n", encoding="utf-8")

            request = build_runtime_dependency_repair_request(
                repo_name="demo-repo",
                project_dir=project_dir,
            )

        self.assertIsNotNone(request)
        assert request is not None
        self.assertEqual(".venv/bin/python -m pip install -r requirements.txt", request.command)
        self.assertTrue(any(item.dependency == "fastapi" for item in request.items))

    def test_plan_runtime_repair_prefers_repo_dependency_repair_for_missing_python_module(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "requirements.txt").write_text("fastapi==0.111.0\n", encoding="utf-8")
            incident = summarize_failure_incident(
                command="python app.py",
                stderr="ModuleNotFoundError: No module named 'fastapi'",
            )

            plan = plan_runtime_repair(
                repo_name="demo-repo",
                project_dir=project_dir,
                incident=incident,
            )

        self.assertEqual("repo_dependency_repair", plan.action_key)
        self.assertIsNotNone(plan.approval_request)

    def test_summarize_failure_incident_routes_cloud_auth_failures_to_cloud_specialist(self) -> None:
        incident = summarize_failure_incident(
            command="aws ec2 run-instances",
            stderr="Unable to locate credentials. Run aws configure.",
        )

        self.assertEqual("cloud_auth_failure", incident.category)
        self.assertEqual("cloud", incident.specialist_name)
        self.assertEqual("cloud_repair", incident.route_family)

    def test_plan_runtime_repair_surfaces_auth_guidance_without_dependency_retry(self) -> None:
        incident = summarize_failure_incident(
            command="python app.py",
            stderr="Authentication failed: missing OPENAI_API_KEY",
        )

        plan = plan_runtime_repair(
            repo_name="demo-repo",
            project_dir=None,
            incident=incident,
        )

        self.assertEqual("auth_guidance", plan.action_key)
        self.assertEqual("auth", plan.specialist_name)
        self.assertTrue(plan.requires_official_docs_lookup)
        self.assertIsNone(plan.approval_request)

    def test_plan_runtime_repair_detects_node_dependency_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "package.json").write_text('{"dependencies":{"next":"14.2.0"}}', encoding="utf-8")
            (project_dir / "package-lock.json").write_text("{}", encoding="utf-8")
            incident = summarize_failure_incident(
                command="npm run dev",
                stderr="npm ERR! Cannot find module 'next/dist/server'",
            )

            plan = plan_runtime_repair(
                repo_name="demo-node",
                project_dir=project_dir,
                incident=incident,
            )

        self.assertEqual("node_dependency_failure", incident.category)
        self.assertEqual("repo_dependency_repair", plan.action_key)
        self.assertEqual("node", plan.specialist_name)
        self.assertEqual("node_repair", plan.route_family)

    def test_plan_runtime_repair_detects_vm_bootstrap_failures(self) -> None:
        incident = summarize_failure_incident(
            command="multipass launch duckln-vm",
            stderr="multipass launch failed: cloud-init status check timed out",
        )

        plan = plan_runtime_repair(
            repo_name="demo-vm",
            project_dir=None,
            incident=incident,
        )

        self.assertEqual("vm_bootstrap_failure", incident.category)
        self.assertEqual("vm_repair_escalation", plan.action_key)
        self.assertEqual("vm", plan.specialist_name)
        self.assertTrue(plan.requires_official_docs_lookup)

    def test_plan_runtime_repair_detects_vm_transport_failures(self) -> None:
        incident = summarize_failure_incident(
            command="multipass shell duckln-vm-1test",
            stderr="shell failed: ssh connection failed: Failed to connect: No route to host",
        )

        plan = plan_runtime_repair(
            repo_name="demo-vm",
            project_dir=None,
            incident=incident,
        )

        self.assertEqual("vm_bootstrap_failure", incident.category)
        self.assertEqual("vm_repair_escalation", plan.action_key)
        self.assertEqual("vm", plan.specialist_name)
        self.assertEqual("vm_repair", plan.route_family)


if __name__ == "__main__":
    unittest.main()
