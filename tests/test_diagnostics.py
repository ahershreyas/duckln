"""Phase 7 alignment for privacy and diagnostics coverage.

Req: R5, R6, R10
Plan: 6, 7, 12, 13
Tasks:
- Unit tests for classifier rules
- Unit tests for payload redaction
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import unittest

from duckln.ai_client import Provider
from duckln.config import AppConfig
from duckln.diagnostics import (
    ErrorCategory,
    build_minimal_payload,
    classify_error,
    gather_minimal_context,
    redact_sensitive_data,
    run_healthcheck,
)
from duckln.logging_utils import emit_log, log_blocked_command, log_execution_result, log_provider_failure
from duckln.modes import ControlMode


@dataclass
class FakeResponse:
    status_code: int
    payload: dict
    text: str = ""

    def json(self) -> dict:
        return self.payload


class FakeHttpClient:
    def __init__(self, responses: dict[str, FakeResponse]) -> None:
        self.responses = responses

    def get(self, url: str, *, headers: dict[str, str], timeout: float) -> FakeResponse:
        return self.responses[url]


class DiagnosticsReqR5R6R10Plan6Plan7Plan12Plan13Test(unittest.TestCase):
    """Covers redaction, payload shaping, classification, and healthcheck output."""

    def _parse_log_payload(self, entry: str) -> dict:
        return json.loads(entry[entry.find("{"):])

    def test_r5_plan6_redaction_masks_keys_tokens_and_pii(self) -> None:
        text = (
            "api_key=sk-secret-12345678 email test@example.com "
            "phone +44 7700 900123 ip 192.168.1.4 token=ghp_abcdefghijk"
        )

        redacted = redact_sensitive_data(text)

        self.assertNotIn("sk-secret-12345678", redacted)
        self.assertNotIn("test@example.com", redacted)
        self.assertNotIn("192.168.1.4", redacted)
        self.assertIn("[REDACTED", redacted)

    def test_r5_plan6_payload_builder_is_mode_aware(self) -> None:
        context = gather_minimal_context(
            command="python app.py",
            stderr="ModuleNotFoundError: No module named 'torch'",
            python_version="3.11.7",
            pip_version="24.0",
        )

        hitl_payload = build_minimal_payload(ControlMode.HITL, context)
        hotl_payload = build_minimal_payload(ControlMode.HOTL, context)
        hootlwo_payload = build_minimal_payload(ControlMode.HOOTLWO, context)

        self.assertNotIn("diagnostics", hitl_payload.payload)
        self.assertIn("diagnostics", hotl_payload.payload)
        self.assertEqual("controlled", hootlwo_payload.payload["context_scope"])

    def test_r6_plan7_plan13_classifier_detects_expected_categories(self) -> None:
        self.assertEqual(
            ErrorCategory.MISSING_MODULE,
            classify_error("ModuleNotFoundError: No module named 'openai'").category,
        )
        self.assertEqual(
            ErrorCategory.FILE_NOT_FOUND,
            classify_error("FileNotFoundError: [Errno 2] No such file or directory").category,
        )
        self.assertEqual(
            ErrorCategory.PERMISSION_DENIED,
            classify_error("Permission denied: '/usr/local/bin'").category,
        )
        self.assertEqual(
            ErrorCategory.CUDA_TORCH_MISMATCH,
            classify_error("Torch not compiled with CUDA enabled").category,
        )

    def test_r6_plan7_plan13_context_gatherer_collects_only_needed_fields(self) -> None:
        context = gather_minimal_context(
            command="python -m pip install torch",
            stderr="ModuleNotFoundError: No module named 'torch'",
            python_version="3.11.7",
            pip_version="24.0",
            environment_notes={"venv": ".venv", "cwd": "/private/path"},
        )

        self.assertEqual(ErrorCategory.MISSING_MODULE, context.category)
        self.assertEqual("3.11.7", context.diagnostics["python_version"])
        self.assertEqual("24.0", context.diagnostics["pip_version"])
        self.assertEqual(".venv", context.diagnostics["venv"])

    def test_r6_plan12_healthcheck_renders_clear_pass_fail_output(self) -> None:
        config = AppConfig(
            provider=Provider.OPENAI,
            model="gpt-4o-mini",
            api_key="openai-key",
            mode=ControlMode.HOTL,
        )
        client = FakeHttpClient(
            {
                "https://api.openai.com/v1/models": FakeResponse(
                    status_code=200,
                    payload={"data": [{"id": "gpt-4o-mini"}]},
                )
            }
        )

        report = run_healthcheck(config, client=client)

        self.assertIn("[PASS]", report.render())
        self.assertIn("OpenAI connectivity", report.render())

    def test_r10_plan12_structured_log_redacts_sensitive_metadata(self) -> None:
        with self.assertLogs("duckln", level=logging.INFO) as captured:
            emit_log(
                level="info",
                event="test_event",
                message="Testing token=ghp_secretvalue12345",
                metadata={
                    "api_key": "sk-secret-12345678",
                    "email": "person@example.com",
                },
            )

        payload = self._parse_log_payload(captured.output[0])
        self.assertEqual("test_event", payload["event"])
        self.assertNotIn("sk-secret-12345678", captured.output[0])
        self.assertNotIn("person@example.com", captured.output[0])

    def test_r10_plan12_provider_failure_log_uses_structured_fields(self) -> None:
        with self.assertLogs("duckln", level=logging.ERROR) as captured:
            log_provider_failure(
                provider="openai",
                model="gpt-4o-mini",
                status_code=401,
                message="Invalid OpenAI API key.",
            )

        payload = self._parse_log_payload(captured.output[0])
        self.assertEqual("provider_failure", payload["event"])
        self.assertEqual("openai", payload["metadata"]["provider"])
        self.assertEqual("401", payload["metadata"]["status_code"])

    def test_r10_plan12_blocked_command_log_uses_safety_metadata(self) -> None:
        with self.assertLogs("duckln", level=logging.WARNING) as captured:
            log_blocked_command(
                command="rm -rf /tmp/token=abc123",
                reason="Destructive root deletion is blocked.",
                safety_class="S4",
            )

        payload = self._parse_log_payload(captured.output[0])
        self.assertEqual("blocked_command", payload["event"])
        self.assertEqual("S4", payload["metadata"]["safety_class"])

    def test_r10_plan12_execution_result_log_omits_full_outputs(self) -> None:
        with self.assertLogs("duckln", level=logging.INFO) as captured:
            log_execution_result(
                command="python --version",
                success=True,
                exit_code=0,
                timed_out=False,
                duration_seconds=0.1234,
            )

        payload = self._parse_log_payload(captured.output[0])
        self.assertEqual("execution_result", payload["event"])
        self.assertNotIn("stdout", payload["metadata"])
        self.assertNotIn("stderr", payload["metadata"])


if __name__ == "__main__":
    unittest.main()
