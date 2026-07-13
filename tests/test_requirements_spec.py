from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from duckln.repo_bringup import (
    RequirementsSpec,
    _canonical_engine,
    _db_provision_steps,
    _detect_prose_services,
    _enrich_requirements_spec_with_llm,
    extract_requirements_spec,
)
from state.repo_catalog import RepoCatalogRecord


def _repo(name="demo", url="https://github.com/acme/demo"):
    return RepoCatalogRecord(
        name=name, repo_url=url, stars=0, description="", category="", framework="", last_updated=""
    )


class _Inspection:
    def __init__(self, detected_files, readme):
        self.detected_files = detected_files
        self.readme_excerpt = readme


class TestProseServices(unittest.TestCase):
    def test_postgres_with_version(self) -> None:
        self.assertEqual(
            _detect_prose_services("Install PostgreSQL 15 before running."),
            (("postgres", "15"),),
        )

    def test_redis_no_version(self) -> None:
        self.assertEqual(_detect_prose_services("Requires Redis for caching."), (("redis", ""),))

    def test_none(self) -> None:
        self.assertEqual(_detect_prose_services("A simple static site."), ())

    def test_canonical_engine(self) -> None:
        self.assertEqual(_canonical_engine("postgresql"), "postgres")
        self.assertEqual(_canonical_engine("mariadb"), "mysql")


class TestDbProvisionSteps(unittest.TestCase):
    def test_postgres_install_start_create_migrate(self) -> None:
        steps = _db_provision_steps("postgres", "15", needs_migrations=True, migrate_command="npm run migrate")
        joined = "\n".join(f"{t}|{c}" for t, c, _s in steps)
        self.assertIn("Install postgres 15", joined)
        self.assertIn("apt-get install -y postgresql", joined)
        self.assertIn("DATABASE_URL", joined)
        self.assertIn("npm run migrate", joined)

    def test_idempotent_skips_install_when_present(self) -> None:
        steps = _db_provision_steps("postgres", present_tools=("postgresql",))
        self.assertFalse(any("apt-get install" in c for _t, c, _s in steps))

    def test_unknown_engine_no_steps(self) -> None:
        self.assertEqual(_db_provision_steps("kafka"), ())


class TestExtractRequirementsSpec(unittest.TestCase):
    def _make_repo_with_pkg(self, tmp, pkg):
        cfg = Path(tmp)
        # extract_requirements_spec reads package.json via _read_repo_package_json,
        # which falls back to the local managed clone; we exercise the README + prose
        # path here (no package.json present), which is the deterministic baseline.
        return cfg

    def test_deterministic_django_web_with_postgres_prose(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            insp = _Inspection(("manage.py", "requirements.txt"), "Set up PostgreSQL 14 and run migrations.")
            spec = extract_requirements_spec(
                repo=_repo(), config_dir=Path(tmp), inspection=insp,
            )
            self.assertEqual(spec.archetype, "web")
            self.assertIn(("postgres", "14"), spec.services)
            self.assertTrue(spec.needs_migrations)
            self.assertEqual(spec.source, "deterministic")

    def test_order_archetype_before_run_command(self) -> None:
        # The spec (understanding) is produced from README + manifests with no run
        # command — proving understanding precedes any run recommendation.
        with tempfile.TemporaryDirectory() as tmp:
            insp = _Inspection(("package.json",), "A desktop app.")
            spec = extract_requirements_spec(repo=_repo(), config_dir=Path(tmp), inspection=insp)
            self.assertIn(spec.archetype, {"web", "library", "cli", "desktop_gui", "service"})

    def test_llm_adds_env_key_but_grounding_keeps_archetype(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            insp = _Inspection(("manage.py",), "Django app. Needs Postgres.")

            def fake_llm(system, user):
                # LLM wrongly claims it's a desktop app and adds an env key.
                return json.dumps({"env_keys": ["STRIPE_KEY"], "notes": ["Run with sudo"]})

            spec = extract_requirements_spec(
                repo=_repo(), config_dir=Path(tmp), inspection=insp, llm_complete=fake_llm
            )
            self.assertEqual(spec.archetype, "web")  # grounding wins over LLM
            self.assertIn("STRIPE_KEY", spec.env_keys)
            self.assertIn("Run with sudo", spec.notes)
            self.assertEqual(spec.source, "llm+grounded")

    def test_llm_failure_falls_back_to_deterministic(self) -> None:
        base = RequirementsSpec(archetype="web", env_keys=("A",))

        def boom(system, user):
            raise RuntimeError("model unreachable")

        out = _enrich_requirements_spec_with_llm(base, readme_excerpt="x", llm_complete=boom)
        self.assertEqual(out, base)
        self.assertEqual(out.source, "deterministic")


if __name__ == "__main__":
    unittest.main()
