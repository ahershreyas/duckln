"""Plan 146 — close the 145 partials to production grade.
A1 prove the MODEL-BACKED supervisor path (real `llm_client(system_prompt=, user_message=)` API),
   and that the deterministic match reaches the agent as a HINT (non-authoritative).
A2 the reasoning/recheck path can read Duckln's OWN reasoning log (Q&A-over-log).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import duckln.recovery as rec
from duckln.recovery import RecoveryDecision, RecoveryReport


class ModelBackedSupervisor(unittest.TestCase):
    """A1: `_llm_supervisor_review` uses the real llm_client API and drives accept/challenge."""

    def _client(self, verdict_json: str):
        captured = {}

        def client(*, system_prompt: str, user_message: str) -> str:
            captured["system"] = system_prompt
            captured["user"] = user_message
            return verdict_json

        return client, captured

    def test_model_accepts(self):
        client, cap = self._client('{"verdict":"accept","critique":""}')
        review = rec._llm_supervisor_review(client, failed_command="npm run build")
        self.assertIsNotNone(review)
        report = RecoveryReport(decision=RecoveryDecision.BLOCK, cause="needs a key")  # no evidence
        accepted, crit = review(report)
        self.assertTrue(accepted)              # the MODEL overrode the deterministic gate
        self.assertIn("supervisor", cap["system"].lower())  # used the supervisor system prompt
        self.assertIn("npm run build", cap["user"])  # reviewed the real failure

    def test_model_challenges(self):
        client, _ = self._client('{"verdict":"challenge","critique":"prove the block with the error line"}')
        review = rec._llm_supervisor_review(client, failed_command="x")
        accepted, crit = review(RecoveryReport(decision=RecoveryDecision.SKIP, cause="optional", evidence="some"))
        self.assertFalse(accepted)
        self.assertIn("prove", crit.lower())

    def test_unparseable_model_output_falls_back_to_deterministic_gate(self):
        client, _ = self._client("not json at all")
        review = rec._llm_supervisor_review(client, failed_command="x")
        # FIXED with no evidence → deterministic gate challenges.
        accepted, _ = review(RecoveryReport(decision=RecoveryDecision.FIXED, cause="c", command="pip install y"))
        self.assertFalse(accepted)

    def test_no_client_returns_none(self):
        self.assertIsNone(rec._llm_supervisor_review(None, failed_command="x"))


class DeterministicHintNonAuthoritative(unittest.TestCase):
    """A1: the deterministic match is offered to the agent as a HINT, never auto-applied."""

    def test_hint_is_passed_as_advisory_text(self):
        captured = {}

        def fake_runner(**kw):
            captured["q"] = kw.get("question", "")
            return SimpleNamespace(answer='{"decision":"fixed","cause":"c","command":"pip install y","evidence":"req lists y"}')

        with tempfile.TemporaryDirectory() as d:
            rec.recover_failed_step_with_agent(
                config_dir=Path(d), repo_name="demo", project_dir=Path(d),
                execution_target="vm", vm_name="box", failed_command="x",
                stderr="boom", stdout="", mode=None, approve=None,
                llm_client=lambda **k: "", agent_runner=fake_runner,
                hint="HINT (a known-pattern heuristic — verify against the REAL repo): fix=`pip install y`",
            )
        # the hint is advisory in the prompt (the model decides), and explicitly says "verify".
        self.assertIn("HINT", captured["q"])
        self.assertIn("verify against the REAL repo", captured["q"])


class ReasoningContextForQA(unittest.TestCase):
    """A2: a question about the SOLUTION/REASONING surfaces Duckln's own log + plan as context."""

    def test_reasoning_question_includes_log_and_plan(self):
        from duckln.main import _reasoning_context_hints
        from duckln.recovery import append_thinking_log
        from state.access import write_pending_plan

        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d)
            append_thinking_log(cfg, repo_slug="demo", title="Recovering: build",
                                lines=["Symptom: build failed", "Outcome: installed pyinstaller — verified"])
            write_pending_plan(cfg, {"status": "executing", "steps": [{"title": "Run build:sidecar"}]})
            hints = _reasoning_context_hints(cfg, "why did you do that? are you sure your solution is right?")
            blob = "\n".join(hints)
            self.assertIn("logical-thinking", blob.lower())
            self.assertIn("pyinstaller", blob.lower())          # it can cite its own reasoning
            self.assertIn("build:sidecar", blob)                 # and the active plan

    def test_ordinary_code_question_gets_no_reasoning_context(self):
        from duckln.main import _reasoning_context_hints
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(_reasoning_context_hints(Path(d), "where is the entry point?"), ())

    def test_secret_in_log_is_redacted_in_context(self):
        from duckln.main import _reasoning_context_hints
        from duckln.recovery import append_thinking_log
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d)
            append_thinking_log(cfg, repo_slug="demo", title="t", lines=["used token sk-ABCDEF1234567890SECRET"])
            blob = "\n".join(_reasoning_context_hints(cfg, "show your reasoning"))
            self.assertNotIn("sk-ABCDEF1234567890SECRET", blob)  # redaction holds


class OutcomeVerification(unittest.TestCase):
    """B2: 'verified' must mean the app actually works (HTTP-200 / the repo's own tests)."""

    def _mk(self, files: dict):
        d = tempfile.mkdtemp()
        p = Path(d)
        for name, content in files.items():
            fp = p / name
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content, encoding="utf-8")
        return p

    def test_python_pytest_surface(self):
        import duckln.repo_bringup as rb
        p = self._mk({"requirements.txt": "flask\n", "tests/test_app.py": "def test_x(): assert True\n"})
        cmd = rb._repo_test_command(p)
        self.assertIsNotNone(cmd)
        self.assertIn("pytest", cmd)

    def test_node_nontrivial_test_script(self):
        import duckln.repo_bringup as rb
        p = self._mk({"package.json": '{"scripts":{"test":"vitest run"}}'})
        self.assertEqual(rb._repo_test_command(p), "npm test")

    def test_node_default_test_script_is_skipped(self):
        import duckln.repo_bringup as rb
        p = self._mk({"package.json": '{"scripts":{"test":"echo \\"Error: no test specified\\" && exit 1"}}'})
        self.assertIsNone(rb._repo_test_command(p))

    def test_go_and_rust(self):
        import duckln.repo_bringup as rb
        self.assertEqual(rb._repo_test_command(self._mk({"go.mod": "module x\n"})), "go test ./...")
        self.assertIn("cargo test", rb._repo_test_command(self._mk({"Cargo.toml": "[package]\n"})))

    def test_http_probe_fails_on_non_2xx(self):
        import duckln.repo_bringup as rb
        cmd = rb._http_outcome_check_command("http://localhost:8000/")
        self.assertIn("curl -fsS", cmd)          # -f → fails on 500/refused
        self.assertIn("http://localhost:8000/", cmd)

    def test_outcome_prefers_http_when_served(self):
        import duckln.repo_bringup as rb
        p = self._mk({"go.mod": "module x\n"})
        self.assertIn("curl", rb._outcome_check_command(served_url="http://localhost:3000/", project_dir=p))
        self.assertEqual(rb._outcome_check_command(served_url=None, project_dir=p), "go test ./...")


class EnvAndServiceProvisioning(unittest.TestCase):
    """B1: populate .env defaults, wait for service readiness, seed when declared."""

    def test_db_steps_wait_for_readiness_before_migrate(self):
        import duckln.repo_bringup as rb
        steps = rb._db_provision_steps("postgres", "15", needs_migrations=True,
                                       migrate_command="alembic upgrade head")
        titles = [t for t, _c, _s in steps]
        self.assertIn("Wait for postgres to be ready", titles)
        # readiness must come BEFORE the migration
        self.assertLess(titles.index("Wait for postgres to be ready"), titles.index("Run database migrations"))
        ready_cmd = next(c for t, c, _s in steps if "ready" in t.lower())
        self.assertIn("pg_isready", ready_cmd)

    def test_seed_only_when_declared(self):
        import duckln.repo_bringup as rb
        no_seed = [t for t, _c, _s in rb._db_provision_steps("postgres")]
        self.assertNotIn("Seed the database", no_seed)
        seeded = [t for t, _c, _s in rb._db_provision_steps("postgres", seed_command="python manage.py loaddata seed.json")]
        self.assertIn("Seed the database", seeded)

    def test_env_defaults_fill_known_keys_not_external(self):
        import duckln.repo_bringup as rb
        cmd = rb._populate_env_defaults_command(("SECRET_KEY", "REDIS_URL", "STRIPE_API_KEY", "PORT"))
        self.assertIn("REDIS_URL=redis://localhost:6379", cmd)
        self.assertIn("SECRET_KEY=", cmd)
        self.assertIn("PORT=8000", cmd)
        self.assertNotIn("STRIPE_API_KEY", cmd)        # external paid key → honest ask, not invented
        self.assertIn("grep -q", cmd)                    # only sets when absent (idempotent)

    def test_env_defaults_none_when_all_external(self):
        import duckln.repo_bringup as rb
        self.assertIsNone(rb._populate_env_defaults_command(("OPENAI_API_KEY", "STRIPE_API_KEY")))

    def test_readiness_engines(self):
        import duckln.repo_bringup as rb
        self.assertIn("redis-cli ping", rb._service_readiness_command("redis"))
        self.assertIn("mysqladmin ping", rb._service_readiness_command("mysql"))


class ConcurrentSecretsLifecycle(unittest.TestCase):
    """C1 concurrent multi-process · C2 repo secrets · C5 teardown · C3 untrusted · C4 sizing."""

    def _mk(self, names):
        d = tempfile.mkdtemp()
        p = Path(d)
        for n in names:
            (p / n).mkdir(parents=True, exist_ok=True)
            (p / n / "package.json").write_text('{"scripts":{"dev":"vite"}}', encoding="utf-8")
        return p

    # C1
    def test_backend_frontend_topology_detected(self):
        import duckln.repo_bringup as rb
        p = self._mk(["backend", "frontend"])
        topo = rb._subdir_run_topology(p)
        self.assertEqual({n for n, _ in topo}, {"backend", "frontend"})

    def test_single_app_is_not_multiprocess(self):
        import duckln.repo_bringup as rb
        self.assertEqual(rb._subdir_run_topology(self._mk(["frontend"])), ())

    def test_concurrent_script_is_self_managed(self):
        import duckln.repo_bringup as rb
        self.assertTrue(rb._has_concurrent_run_script({"dev": "concurrently \"npm:server\" \"npm:client\""}))
        self.assertFalse(rb._has_concurrent_run_script({"dev": "vite"}))

    # C2
    def test_secret_key_classification(self):
        import duckln.repo_bringup as rb
        self.assertTrue(rb._is_secret_env_key("STRIPE_API_KEY"))
        self.assertTrue(rb._is_secret_env_key("DB_PASSWORD"))
        self.assertFalse(rb._is_secret_env_key("PORT"))

    def test_secret_intake_excludes_keys_with_safe_defaults(self):
        import duckln.repo_bringup as rb
        # SECRET_KEY has a safe random dev default → not an intake ask; STRIPE_API_KEY is.
        need = rb._secret_env_keys_needing_intake(("SECRET_KEY", "STRIPE_API_KEY", "PORT"))
        self.assertIn("STRIPE_API_KEY", need)
        self.assertNotIn("SECRET_KEY", need)
        self.assertNotIn("PORT", need)

    # C5
    def test_teardown_command(self):
        import duckln.repo_bringup as rb
        cmd = rb._teardown_command(compose=True, app_pids=("1234",), tunnel_pids=("5678",))
        self.assertIn("docker compose down", cmd)
        self.assertIn("kill 1234", cmd)
        self.assertIn("kill 5678", cmd)
        self.assertIsNone(rb._teardown_command())  # nothing started → nothing to tear down

    # C3
    def test_untrusted_local_notice(self):
        import duckln.repo_bringup as rb
        self.assertIsNotNone(rb._untrusted_local_notice(execution_target="local", repo_previously_trusted=False))
        self.assertIsNone(rb._untrusted_local_notice(execution_target="vm", repo_previously_trusted=False))
        self.assertIsNone(rb._untrusted_local_notice(execution_target="local", repo_previously_trusted=True))

    # C4
    def test_resource_heavy_detection(self):
        import duckln.repo_bringup as rb
        heavy_step = SimpleNamespace(command="cargo build --release")
        self.assertTrue(rb._repo_is_resource_heavy([heavy_step]))
        self.assertTrue(rb._repo_is_resource_heavy([], detected_files=("Cargo.toml",)))
        light_step = SimpleNamespace(command="npm run dev")
        self.assertFalse(rb._repo_is_resource_heavy([light_step], detected_files=("package.json",)))


class SelfExtension(unittest.TestCase):
    """B4: detect a capability gap; draft a registerable tool/MCP config."""

    def test_capability_gap_extracted(self):
        self.assertEqual(rec.capability_gap_from_text("To fetch issues I need the MCP server `github`."), "github")
        self.assertEqual(rec.capability_gap_from_text("I need a tool `aws-cli` to deploy."), "aws-cli")
        self.assertIsNone(rec.capability_gap_from_text("I fixed it by installing pyinstaller."))

    def test_draft_mcp_config_is_registerable_shape(self):
        from duckln.main import _draft_extension_config
        cfg = _draft_extension_config("mcp", "connect to GitHub issues")
        self.assertEqual(cfg["provider_type"], "mcp")
        self.assertEqual(cfg["transport"], "stdio")
        self.assertIn("command", cfg)               # user fills this before register
        self.assertEqual(cfg["status"], "draft")

    def test_draft_tool_config(self):
        from duckln.main import _draft_extension_config
        cfg = _draft_extension_config("tool", "run terraform plan safely")
        self.assertEqual(cfg["provider_type"], "shell")
        self.assertEqual(cfg["status"], "draft")


if __name__ == "__main__":
    unittest.main()
