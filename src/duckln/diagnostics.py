"""Deterministic diagnostics and error classification helpers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import importlib.util
import re
import sys
from typing import TYPE_CHECKING, Any

from duckln.modes import ControlMode

if TYPE_CHECKING:
    from duckln.config import AppConfig


REDACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"\b(ghp|gho|ghu|github_pat)_[A-Za-z0-9_]{8,}\b"), "[REDACTED_TOKEN]"),
    (re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\s*[:=]\s*([^\s,;]+)"), r"\1=[REDACTED]"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]"),
    (re.compile(r"\b(?:\+?\d[\d .()-]{7,}\d)\b"), "[REDACTED_PHONE]"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "[REDACTED_IP]"),
)

class ErrorCategory(str, Enum):
    """Deterministic error classes for Python and AI environment issues."""

    MISSING_MODULE = "missing_module"
    PIP_PYTHON_MISMATCH = "pip_python_mismatch"
    FILE_NOT_FOUND = "file_not_found"
    PERMISSION_DENIED = "permission_denied"
    CUDA_TORCH_MISMATCH = "cuda_torch_mismatch"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ClassificationResult:
    """Classification result for stderr text."""

    category: ErrorCategory
    confidence: float
    reason: str


@dataclass(frozen=True)
class MinimalContext:
    """Minimal diagnostic context suitable for privacy-first payloads."""

    command: str
    category: ErrorCategory
    redacted_stderr: str
    diagnostics: dict[str, str]


@dataclass(frozen=True)
class PayloadBundle:
    """LLM-safe payload built according to the active control mode."""

    mode: ControlMode
    payload: dict[str, Any]


@dataclass(frozen=True)
class HealthcheckReport:
    """Result of validating the current environment and provider connectivity."""

    ok: bool
    lines: tuple[str, ...]

    def render(self) -> str:
        return "\n".join(self.lines)


def redact_sensitive_data(text: str) -> str:
    """Mask obvious secrets and PII before diagnostics leave Duckln."""

    redacted = text
    for pattern, replacement in REDACTION_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def classify_error(stderr: str) -> ClassificationResult:
    """Classify common Python and AI environment failures deterministically."""

    normalized = stderr.lower()

    if "modulenotfounderror" in normalized:
        return ClassificationResult(
            category=ErrorCategory.MISSING_MODULE,
            confidence=0.98,
            reason="Detected ModuleNotFoundError in stderr.",
        )

    if ("no module named" in normalized and "pip" in normalized) or (
        "which pip" in normalized and "which python" in normalized
    ):
        return ClassificationResult(
            category=ErrorCategory.PIP_PYTHON_MISMATCH,
            confidence=0.85,
            reason="Detected pip/python environment mismatch indicators.",
        )

    if any(token in normalized for token in ("no such file or directory", "filenotfounderror", "not found")):
        return ClassificationResult(
            category=ErrorCategory.FILE_NOT_FOUND,
            confidence=0.9,
            reason="Detected missing file or path indicators.",
        )

    if "permission denied" in normalized:
        return ClassificationResult(
            category=ErrorCategory.PERMISSION_DENIED,
            confidence=0.95,
            reason="Detected permission denied indicators.",
        )

    if any(token in normalized for token in ("cuda", "torch not compiled with cuda", "libcudart", "cudnn")):
        return ClassificationResult(
            category=ErrorCategory.CUDA_TORCH_MISMATCH,
            confidence=0.88,
            reason="Detected CUDA or torch environment mismatch indicators.",
        )

    return ClassificationResult(
        category=ErrorCategory.UNKNOWN,
        confidence=0.2,
        reason="No high-confidence deterministic classifier matched.",
    )


def gather_minimal_context(
    *,
    command: str,
    stderr: str,
    python_version: str | None = None,
    pip_version: str | None = None,
    environment_notes: dict[str, str] | None = None,
) -> MinimalContext:
    """Collect only the minimum diagnostic context needed for the current failure."""

    classification = classify_error(stderr)
    diagnostics: dict[str, str] = {}

    if python_version and classification.category in {
        ErrorCategory.MISSING_MODULE,
        ErrorCategory.PIP_PYTHON_MISMATCH,
        ErrorCategory.CUDA_TORCH_MISMATCH,
    }:
        diagnostics["python_version"] = python_version

    if pip_version and classification.category in {
        ErrorCategory.MISSING_MODULE,
        ErrorCategory.PIP_PYTHON_MISMATCH,
    }:
        diagnostics["pip_version"] = pip_version

    if environment_notes:
        for key, value in environment_notes.items():
            if value:
                diagnostics[key] = value

    return MinimalContext(
        command=command,
        category=classification.category,
        redacted_stderr=redact_sensitive_data(stderr),
        diagnostics=diagnostics,
    )


def build_minimal_payload(mode: ControlMode, context: MinimalContext) -> PayloadBundle:
    """Build a privacy-first LLM payload according to the active control mode."""

    payload: dict[str, Any] = {
        "command": context.command,
        "error_category": context.category.value,
        "stderr": context.redacted_stderr,
    }

    if mode is ControlMode.HITL:
        return PayloadBundle(mode=mode, payload=payload)

    if mode is ControlMode.HOTL:
        payload["diagnostics"] = dict(context.diagnostics)
        return PayloadBundle(mode=mode, payload=payload)

    payload["diagnostics"] = dict(context.diagnostics)
    payload["context_scope"] = "controlled"
    return PayloadBundle(mode=mode, payload=payload)


def run_healthcheck(config: AppConfig, *, client: Any | None = None) -> HealthcheckReport:
    """Validate environment basics and provider connectivity with clear pass/fail lines."""

    from duckln.ai_client import get_provider_adapter

    lines: list[str] = []
    checks_ok = True

    python_ok = sys.version_info >= (3, 11)
    lines.append(_format_check("Python", python_ok, f"{sys.version.split()[0]} detected"))
    checks_ok = checks_ok and python_ok

    pip_ok = importlib.util.find_spec("pip") is not None
    lines.append(_format_check("pip", pip_ok, "pip module available" if pip_ok else "pip module not available"))
    checks_ok = checks_ok and pip_ok

    httpx_ok = importlib.util.find_spec("httpx") is not None
    lines.append(_format_check("httpx", httpx_ok, "httpx installed" if httpx_ok else "httpx missing"))
    checks_ok = checks_ok and httpx_ok

    inquirer_ok = importlib.util.find_spec("InquirerPy") is not None
    lines.append(
        _format_check(
            "InquirerPy",
            inquirer_ok,
            "InquirerPy installed" if inquirer_ok else "InquirerPy missing",
        )
    )
    checks_ok = checks_ok and inquirer_ok

    adapter = get_provider_adapter(config.provider)
    validation = adapter.validate_api_key(config.api_key, client=client)
    provider_ok = validation.ok
    lines.append(
        _format_check(
            f"{config.provider.label} connectivity",
            provider_ok,
            validation.message,
        )
    )
    checks_ok = checks_ok and provider_ok

    if validation.ok:
        model_validation = adapter.validate_model(
            config.api_key,
            config.model,
            client=client,
            models=validation.models,
        )
        model_ok = model_validation.ok
        lines.append(_format_check("Configured model", model_ok, model_validation.message))
        checks_ok = checks_ok and model_ok
    else:
        lines.append(_format_check("Configured model", False, "Skipped because provider validation failed."))
        checks_ok = False

    return HealthcheckReport(ok=checks_ok, lines=tuple(lines))


def _format_check(name: str, ok: bool, detail: str) -> str:
    status = "PASS" if ok else "FAIL"
    return f"[{status}] {name}: {redact_sensitive_data(detail)}"
