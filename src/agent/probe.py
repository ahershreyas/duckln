"""System probe helpers for hardware-aware Duckln behavior."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys


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
        cpu_logical_cores=_detect_cpu_logical_cores(operating_system),
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
    """Apple Silicon hardware always exposes MPS; we don't import torch on the hot path."""

    return GpuProbeState(
        backend="mps",
        summary="Apple Silicon detected; MPS available.",
        cuda_capable=False,
        cuda_available=False,
        mps_capable=True,
        mps_available=True,
    )


def _detect_cuda_state() -> GpuProbeState:
    """Use nvidia-smi for hardware detection so we avoid importing torch at startup."""

    nvidia_gpu_names = _read_nvidia_gpu_names()
    if nvidia_gpu_names:
        summary = f"NVIDIA GPU detected ({nvidia_gpu_names[0]})."
        return GpuProbeState(
            backend="cuda",
            summary=summary,
            cuda_capable=True,
            cuda_available=True,
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
            pass
    if operating_system == "Darwin":
        return _detect_macos_ram_bytes()
    if operating_system == "Linux":
        return _detect_linux_ram_bytes()
    return None


def _detect_cpu_logical_cores(operating_system: str) -> int | None:
    cpu_count = os.cpu_count()
    if cpu_count is not None and cpu_count > 0:
        return int(cpu_count)
    if operating_system == "Darwin":
        return _read_positive_int_command(("sysctl", "-n", "hw.logicalcpu"))
    if operating_system == "Linux":
        return _detect_linux_cpu_count()
    if operating_system == "Windows":
        env_value = os.environ.get("NUMBER_OF_PROCESSORS", "").strip()
        if env_value.isdigit():
            cores = int(env_value)
            if cores > 0:
                return cores
    return None


def _detect_macos_ram_bytes() -> int | None:
    return _read_positive_int_command(("sysctl", "-n", "hw.memsize"))


def _detect_linux_ram_bytes() -> int | None:
    try:
        meminfo = Path("/proc/meminfo").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    match = next((line for line in meminfo.splitlines() if line.startswith("MemTotal:")), None)
    if not match:
        return None
    parts = match.split()
    if len(parts) < 2 or not parts[1].isdigit():
        return None
    kib = int(parts[1])
    return kib * 1024 if kib > 0 else None


def _detect_linux_cpu_count() -> int | None:
    nproc = shutil.which("nproc")
    if nproc:
        result = _read_positive_int_command((nproc,))
        if result is not None:
            return result
    try:
        cpuinfo = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    processor_lines = [line for line in cpuinfo.splitlines() if line.lower().startswith("processor")]
    return len(processor_lines) or None


def _read_positive_int_command(command: tuple[str, ...]) -> int | None:
    try:
        completed = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    if not value.isdigit():
        return None
    parsed = int(value)
    return parsed if parsed > 0 else None


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
