"""System probe helpers for hardware-aware Duckln behavior."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import importlib.util
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from typing import Any


GIB = 1024**3


@dataclass(frozen=True)
class GpuProbeState:
    """Structured GPU probe result."""

    backend: str
    summary: str
    cuda_capable: bool
    cuda_available: bool
    mps_capable: bool
    mps_available: bool


@dataclass(frozen=True)
class SystemProbe:
    """Structured local system probe for hardware-aware behavior."""

    operating_system: str
    architecture: str
    cpu_logical_cores: int | None
    ram_bytes: int | None
    disk_free_bytes: int | None
    python_version: str
    gpu: GpuProbeState

    @property
    def is_apple_silicon(self) -> bool:
        return self.operating_system == "Darwin" and self.architecture == "arm64"

    @property
    def is_x86(self) -> bool:
        return self.architecture in {"x86_64", "x86"}

    def to_state_values(self) -> dict[str, str]:
        """Return concise state values for SQLite-backed persistence."""

        values = {
            "system.os": self.operating_system,
            "system.architecture": self.architecture,
            "system.python_version": self.python_version,
            "system.gpu_backend": self.gpu.backend,
            "system.gpu_summary": self.gpu.summary,
            "system.cuda_capable": str(self.gpu.cuda_capable).lower(),
            "system.cuda_available": str(self.gpu.cuda_available).lower(),
            "system.mps_capable": str(self.gpu.mps_capable).lower(),
            "system.mps_available": str(self.gpu.mps_available).lower(),
            "system.apple_silicon": str(self.is_apple_silicon).lower(),
        }
        if self.cpu_logical_cores is not None:
            values["system.cpu_logical_cores"] = str(self.cpu_logical_cores)
        if self.ram_bytes is not None:
            values["system.ram_gib"] = _format_gib(self.ram_bytes)
        if self.disk_free_bytes is not None:
            values["system.disk_free_gib"] = _format_gib(self.disk_free_bytes)
        return values

    def summary(self) -> str:
        """Render a concise one-line probe summary."""

        parts = [f"{_display_os(self.operating_system)} {self.architecture}"]
        if self.cpu_logical_cores is not None:
            parts.append(f"{self.cpu_logical_cores} CPU")
        if self.ram_bytes is not None:
            parts.append(f"{_format_gib(self.ram_bytes)} GiB RAM")
        if self.disk_free_bytes is not None:
            parts.append(f"{_format_gib(self.disk_free_bytes)} GiB free")
        parts.append(f"Python {self.python_version}")
        parts.append(self.gpu.summary)
        return ", ".join(parts)


def probe_system() -> SystemProbe:
    """Detect the current local system in a concise, hardware-aware way."""

    operating_system = platform.system() or "Unknown"
    architecture = _normalize_architecture(platform.machine())
    gpu = _detect_gpu(operating_system, architecture)
    return SystemProbe(
        operating_system=operating_system,
        architecture=architecture,
        cpu_logical_cores=os.cpu_count(),
        ram_bytes=_detect_ram_bytes(operating_system),
        disk_free_bytes=_detect_disk_free_bytes(),
        python_version=platform.python_version(),
        gpu=gpu,
    )


def _detect_gpu(operating_system: str, architecture: str) -> GpuProbeState:
    if operating_system == "Darwin" and architecture == "arm64":
        return _detect_mps_state()
    if operating_system in {"Linux", "Windows"}:
        return _detect_cuda_state()
    return GpuProbeState(
        backend="cpu",
        summary="CPU-only execution detected.",
        cuda_capable=False,
        cuda_available=False,
        mps_capable=False,
        mps_available=False,
    )


def _detect_mps_state() -> GpuProbeState:
    torch_module = _load_torch_module()
    mps_available = False
    if torch_module is not None:
        try:
            backend = getattr(torch_module.backends, "mps", None)
            if backend is not None and callable(getattr(backend, "is_available", None)):
                mps_available = bool(backend.is_available())
        except Exception:
            mps_available = False

    summary = "Apple Silicon detected; MPS available." if mps_available else "Apple Silicon detected; use CPU or MPS when available."
    return GpuProbeState(
        backend="mps",
        summary=summary,
        cuda_capable=False,
        cuda_available=False,
        mps_capable=True,
        mps_available=mps_available,
    )


def _detect_cuda_state() -> GpuProbeState:
    torch_module = _load_torch_module()
    torch_cuda_available = False
    torch_cuda_built = False
    if torch_module is not None:
        try:
            torch_cuda_available = bool(torch_module.cuda.is_available())
            torch_cuda_built = getattr(torch_module.version, "cuda", None) is not None
        except Exception:
            torch_cuda_available = False
            torch_cuda_built = False

    nvidia_gpu_names = _read_nvidia_gpu_names()
    cuda_capable = bool(nvidia_gpu_names) or torch_cuda_built

    if torch_cuda_available:
        summary = "CUDA available in Python."
        if nvidia_gpu_names:
            summary = f"CUDA available in Python ({nvidia_gpu_names[0]})."
        return GpuProbeState(
            backend="cuda",
            summary=summary,
            cuda_capable=True,
            cuda_available=True,
            mps_capable=False,
            mps_available=False,
        )

    if cuda_capable:
        summary = "NVIDIA GPU detected, but CUDA is not ready in Python."
        if nvidia_gpu_names:
            summary = f"NVIDIA GPU detected ({nvidia_gpu_names[0]}), but CUDA is not ready in Python."
        return GpuProbeState(
            backend="cuda",
            summary=summary,
            cuda_capable=True,
            cuda_available=False,
            mps_capable=False,
            mps_available=False,
        )

    return GpuProbeState(
        backend="cpu",
        summary="No CUDA-capable GPU detected.",
        cuda_capable=False,
        cuda_available=False,
        mps_capable=False,
        mps_available=False,
    )


def _load_torch_module() -> Any | None:
    if importlib.util.find_spec("torch") is None:
        return None
    try:
        return importlib.import_module("torch")
    except Exception:
        return None


def _read_nvidia_gpu_names() -> tuple[str, ...]:
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return ()

    try:
        completed = subprocess.run(
            [nvidia_smi, "--query-gpu=name", "--format=csv,noheader"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return ()

    if completed.returncode != 0:
        return ()

    names = tuple(line.strip() for line in completed.stdout.splitlines() if line.strip())
    return names[:2]


def _detect_ram_bytes(operating_system: str) -> int | None:
    if operating_system == "Windows":
        return _detect_windows_ram_bytes()

    if hasattr(os, "sysconf"):
        try:
            page_size = int(os.sysconf("SC_PAGE_SIZE"))
            page_count = int(os.sysconf("SC_PHYS_PAGES"))
            if page_size > 0 and page_count > 0:
                return page_size * page_count
        except (OSError, ValueError, TypeError):
            return None
    return None


def _detect_windows_ram_bytes() -> int | None:
    if sys.platform != "win32":
        return None

    try:
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)) == 0:
            return None
        return int(status.ullTotalPhys)
    except Exception:
        return None


def _detect_disk_free_bytes() -> int | None:
    try:
        usage = shutil.disk_usage(Path.home())
    except OSError:
        return None
    return int(usage.free)


def _normalize_architecture(machine: str) -> str:
    normalized = machine.strip().lower()
    if normalized in {"arm64", "aarch64"}:
        return "arm64"
    if normalized in {"x86_64", "amd64"}:
        return "x86_64"
    if normalized in {"i386", "i686", "x86"}:
        return "x86"
    return normalized or "unknown"


def _format_gib(value: int) -> str:
    return f"{value / GIB:.1f}"


def _display_os(value: str) -> str:
    if value == "Darwin":
        return "macOS"
    return value
