"""Tests for system probing helpers."""

from __future__ import annotations

from collections import namedtuple
import unittest
from unittest.mock import patch

from agent.probe import GpuProbeState, probe_system


DiskUsage = namedtuple("DiskUsage", ["total", "used", "free"])


class AgentProbeTest(unittest.TestCase):
    def test_probe_system_reports_apple_silicon_and_mps(self) -> None:
        with (
            patch("agent.probe.platform.system", return_value="Darwin"),
            patch("agent.probe.platform.machine", return_value="arm64"),
            patch("agent.probe.platform.python_version", return_value="3.11.8"),
            patch("agent.probe.os.cpu_count", return_value=8),
            patch("agent.probe._detect_ram_bytes", return_value=16 * 1024**3),
            patch("agent.probe.shutil.disk_usage", return_value=DiskUsage(0, 0, 200 * 1024**3)),
            patch(
                "agent.probe._detect_mps_state",
                return_value=GpuProbeState(
                    backend="mps",
                    summary="Apple Silicon detected; MPS available.",
                    cuda_capable=False,
                    cuda_available=False,
                    mps_capable=True,
                    mps_available=True,
                ),
            ),
        ):
            probe = probe_system()

        self.assertTrue(probe.is_apple_silicon)
        self.assertEqual("mps", probe.gpu.backend)
        self.assertIn("macOS arm64", probe.summary())
        self.assertIn("MPS available", probe.summary())

    def test_probe_system_reports_linux_cuda_state(self) -> None:
        with (
            patch("agent.probe.platform.system", return_value="Linux"),
            patch("agent.probe.platform.machine", return_value="x86_64"),
            patch("agent.probe.platform.python_version", return_value="3.11.8"),
            patch("agent.probe.os.cpu_count", return_value=16),
            patch("agent.probe._detect_ram_bytes", return_value=64 * 1024**3),
            patch("agent.probe.shutil.disk_usage", return_value=DiskUsage(0, 0, 500 * 1024**3)),
            patch(
                "agent.probe._detect_cuda_state",
                return_value=GpuProbeState(
                    backend="cuda",
                    summary="NVIDIA GPU detected (RTX 4090), but CUDA is not ready in Python.",
                    cuda_capable=True,
                    cuda_available=False,
                    mps_capable=False,
                    mps_available=False,
                ),
            ),
        ):
            probe = probe_system()

        self.assertTrue(probe.is_x86)
        self.assertEqual("cuda", probe.gpu.backend)
        self.assertEqual("false", probe.to_state_values()["system.cuda_available"])
        self.assertIn("RTX 4090", probe.summary())


if __name__ == "__main__":
    unittest.main()
