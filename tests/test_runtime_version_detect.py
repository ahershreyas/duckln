from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from duckln.repo_bringup import (
    _detect_required_node_version,
    _int_or_zero,
    _node_major_from_spec,
    _node_version_install_step,
    resolve_runtime_project_dir,
)
from state.repo_catalog import RepoCatalogRecord


def _repo(url=""):
    return RepoCatalogRecord(
        "demo", url, 10, "Demo.", "Custom", "TypeScript", "2026-04-01",
    )


class TestNodeMajorParsing(unittest.TestCase):
    def test_spec_variants(self):
        self.assertEqual(_node_major_from_spec(">=20.19"), "20")
        self.assertEqual(_node_major_from_spec("^18"), "18")
        self.assertEqual(_node_major_from_spec("20.x"), "20")
        self.assertEqual(_node_major_from_spec("v22.12.0"), "22")
        self.assertEqual(_node_major_from_spec("18 || 20"), "18")  # floor
        self.assertIsNone(_node_major_from_spec(""))
        self.assertIsNone(_node_major_from_spec("latest"))

    def test_int_or_zero(self):
        self.assertEqual(_int_or_zero("20"), 20)
        self.assertEqual(_int_or_zero(None), 0)
        self.assertEqual(_int_or_zero("x"), 0)


class TestDetectRequiredNodeVersion(unittest.TestCase):
    def _write_local(self, config_dir, repo, name, content):
        proj = Path(resolve_runtime_project_dir(config_dir, repo, execution_target="local"))
        proj.mkdir(parents=True, exist_ok=True)
        (proj / name).write_text(content, encoding="utf-8")

    def test_reads_engines_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp)
            repo = _repo()
            self._write_local(cfg, repo, "package.json", json.dumps({"engines": {"node": ">=20.19"}}))
            v = _detect_required_node_version(
                config_dir=cfg, repo=repo, detected_files=("package.json",),
            )
            self.assertEqual(v, "20")

    def test_reads_nvmrc(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp)
            repo = _repo()
            self._write_local(cfg, repo, "package.json", json.dumps({"name": "x"}))
            self._write_local(cfg, repo, ".nvmrc", "18\n")
            v = _detect_required_node_version(
                config_dir=cfg, repo=repo, detected_files=("package.json",),
            )
            self.assertEqual(v, "18")

    def test_reads_readme_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp)
            repo = _repo()
            v = _detect_required_node_version(
                config_dir=cfg, repo=repo, detected_files=("package.json",),
                readme_excerpt="This project requires Node 20 or higher.",
            )
            self.assertEqual(v, "20")

    def test_none_when_no_node_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = _detect_required_node_version(
                config_dir=Path(tmp), repo=_repo(), detected_files=("requirements.txt",),
            )
            self.assertIsNone(v)

    def test_none_when_no_requirement(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp)
            repo = _repo()
            self._write_local(cfg, repo, "package.json", json.dumps({"name": "x"}))
            v = _detect_required_node_version(
                config_dir=cfg, repo=repo, detected_files=("package.json",),
            )
            self.assertIsNone(v)


class TestNodeInstallStep(unittest.TestCase):
    def test_linux_target_uses_nodesource(self):
        step = _node_version_install_step(required_major="20", execution_target="vm")
        self.assertIn("nodesource.com/setup_20.x", step.command)
        self.assertIn("apt-get install -y nodejs", step.command)
        self.assertEqual(step.source, "version-prereq")
        self.assertIn("20", step.purpose)


if __name__ == "__main__":
    unittest.main()
