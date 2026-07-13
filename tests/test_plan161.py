"""Plan 161 — /loop parity: (PR1) forced expiry, (PR2) LLM-driven stub types,
(PR3) bounded diagnose-fix-retest. Mirrors `.sdc/loop_doc.md`, reconciled to Duckln's
real APIs (frozen records, instance-method store, run_spec, real read-only tools)."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import duckln.loop_runtime as lr
from state.store import initialize_state_store, resolve_state_store_paths


def _make_loop(cd: Path, loop_type: str, *, auto_fix: bool = True, max_fix: int = 3,
               tool_scope=None, expires_at=None, expiry_days: int = 30):
    store = initialize_state_store(cd)
    loop_id = store.next_loop_id()
    store.upsert_loop(
        loop_id=loop_id,
        name=f"{loop_type} loop",
        loop_type=loop_type,
        schedule='{"kind":"interval","minutes":5}',
        task_description="watch the thing",
        tool_scope=tool_scope if tool_scope is not None else lr.LOOP_TYPES[loop_type],
        os_type="Linux",
        auto_fix=auto_fix,
        max_fix_attempts=max_fix,
        notify_on="failure",
        safety_class="S1",
        active=True,
        expires_at=expires_at,
        expiry_days=expiry_days,
    )
    loop = store.get_loop(loop_id)
    assert loop is not None
    return loop


def _spec(ok: bool = True, **payload):
    return SimpleNamespace(
        ok=ok,
        parsed=payload if ok else None,
        raw="{}",
        errors=() if ok else ("not valid JSON",),
        missing_input_keys=(),
    )


# --- PR1: forced expiry -------------------------------------------------------


class PR1Expiry(unittest.TestCase):
    def test_per_type_expiry_defaults(self):
        self.assertEqual(7, lr.infer_loop_spec("watch the CI pipeline build", "hourly", "notify").expiry_days)
        self.assertEqual(90, lr.infer_loop_spec("watch aws cloud cost spend", "hourly", "notify").expiry_days)
        self.assertEqual(30, lr.infer_loop_spec("watch this repo for releases", "hourly", "notify").expiry_days)

    def test_user_override_expiry(self):
        spec = lr.infer_loop_spec("watch the CI build", "hourly", "notify", expiry_days=14)
        self.assertEqual(14, spec.expiry_days)

    def test_create_sets_expires_at_and_columns_persist(self):
        with tempfile.TemporaryDirectory() as td:
            cd = Path(td)
            loop = lr.create_loop_from_answers(cd, task="watch this repo for releases",
                                               cadence="hourly", failure_policy="notify")
            self.assertEqual(30, loop.expiry_days)
            self.assertIsNotNone(loop.expires_at)
            self.assertIn(lr._days_remaining(loop), (29, 30))

    def test_migration_adds_columns_to_existing_db(self):
        with tempfile.TemporaryDirectory() as td:
            cd = Path(td)
            paths = resolve_state_store_paths(cd)
            paths.state_dir.mkdir(parents=True, exist_ok=True)
            # Simulate a pre-Plan-161 loops table with no expiry columns.
            conn = sqlite3.connect(paths.database_file)
            conn.execute(
                "CREATE TABLE loops (id TEXT PRIMARY KEY, name TEXT NOT NULL, type TEXT NOT NULL, "
                "schedule TEXT NOT NULL, task_description TEXT NOT NULL, tool_scope TEXT NOT NULL, "
                "auto_fix INTEGER NOT NULL DEFAULT 1, max_fix_attempts INTEGER NOT NULL DEFAULT 3, "
                "notify_on TEXT NOT NULL DEFAULT 'failure', safety_class TEXT NOT NULL DEFAULT 'S1', "
                "active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, last_run TEXT, "
                "last_status TEXT, last_summary TEXT, os_type TEXT NOT NULL)"
            )
            conn.commit()
            conn.close()
            initialize_state_store(cd)  # runs migrations
            verify = sqlite3.connect(paths.database_file)
            verify.row_factory = sqlite3.Row
            cols = {row["name"] for row in verify.execute("PRAGMA table_info(loops)")}
            verify.close()
            self.assertIn("expires_at", cols)
            self.assertIn("expiry_days", cols)

    def test_expired_loop_deactivates_and_notifies(self):
        with tempfile.TemporaryDirectory() as td:
            cd = Path(td)
            past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
            loop = _make_loop(cd, "repo_watcher", expires_at=past, expiry_days=1)
            captured = []
            orig = lr.send_loop_notification
            lr.send_loop_notification = lambda title, message, **kw: captured.append((title, message))
            try:
                result = lr.run_loop_now(cd, loop.id)
            finally:
                lr.send_loop_notification = orig
            self.assertEqual("expired", result.status)
            self.assertFalse(initialize_state_store(cd).get_loop(loop.id).active)
            self.assertTrue(captured)
            self.assertIn("expired", captured[0][0].lower())

    def test_fresh_loop_runs_not_expired(self):
        with tempfile.TemporaryDirectory() as td:
            cd = Path(td)
            loop = _make_loop(cd, "cost_monitor", expires_at=lr.next_expires_at(90))
            self.assertFalse(lr._loop_is_expired(loop))

    def test_render_loops_shows_days_remaining(self):
        with tempfile.TemporaryDirectory() as td:
            cd = Path(td)
            lr.create_loop_from_answers(cd, task="watch this repo", cadence="hourly", failure_policy="notify")
            self.assertIn("expires in", lr.render_loops(cd))


# --- PR2: LLM-driven execution ------------------------------------------------


class PR2LLMDriven(unittest.TestCase):
    def test_no_usable_model_reports_honestly_never_fake_ok(self):
        with tempfile.TemporaryDirectory() as td:
            cd = Path(td)
            for loop_type in ("repo_watcher", "model_quality", "ci_monitor", "custom"):
                loop = _make_loop(cd, loop_type)
                result = lr.LoopSubAgent(config_dir=cd, llm_client=None).execute(loop)
                self.assertNotEqual("ok", result.status)
                self.assertTrue(result.should_notify)
                self.assertIn("no usable model", result.summary.lower())

    def test_stub_types_route_through_llm_not_silent_ok(self):
        with tempfile.TemporaryDirectory() as td:
            cd = Path(td)
            seen = []
            runner = lambda d, u, c, s: seen.append(d.name) or _spec(status="ok", summary="nothing notable", should_notify=False)
            for loop_type in ("repo_watcher", "model_quality", "ci_monitor", "custom"):
                loop = _make_loop(cd, loop_type, auto_fix=False)
                result = lr.LoopSubAgent(config_dir=cd, llm_client=object(), spec_runner=runner).execute(loop)
                self.assertIn(result.status, {"ok", "attention_needed", "failed"})
                self.assertNotEqual("", result.summary)
            self.assertEqual(["loop_executor"] * 4, seen)

    def test_tool_outside_scope_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            cd = Path(td)
            loop = _make_loop(cd, "repo_watcher", tool_scope=("not_a_real_tool",))
            result = lr.LoopSubAgent(config_dir=cd, llm_client=object()).execute(loop)
            self.assertEqual("failed", result.status)
            self.assertIn("outside its allowed scope", result.summary.lower())

    def test_invalid_structured_output_is_honest_not_ok(self):
        with tempfile.TemporaryDirectory() as td:
            cd = Path(td)
            loop = _make_loop(cd, "ci_monitor", auto_fix=False)
            runner = lambda *a: _spec(ok=False)
            result = lr.LoopSubAgent(config_dir=cd, llm_client=object(), spec_runner=runner).execute(loop)
            self.assertEqual("attention_needed", result.status)
            self.assertTrue(result.should_notify)

    def test_deterministic_types_work_without_a_model(self):
        with tempfile.TemporaryDirectory() as td:
            cd = Path(td)
            result = lr.LoopSubAgent(config_dir=cd, llm_client=None).execute(_make_loop(cd, "cost_monitor"))
            self.assertEqual("ok", result.status)

    def test_loop_executor_spec_forbids_irreversible_action(self):
        # The safety floor: the spec itself forbids irreversible actions, and the
        # deterministic blocklist still rejects destructive commands.
        from duckln.harness.agent_def import builtin_agents_directory, load_agent_definition_from_path

        body = load_agent_definition_from_path(builtin_agents_directory() / "loop_executor.md").system_prompt.lower()
        self.assertIn("never take an irreversible action", body)
        self.assertFalse(lr._safe_loop_command("git push --force origin main"))
        self.assertFalse(lr._safe_loop_command("rm -rf /var/data"))


# --- PR3: bounded diagnose-fix-retest -----------------------------------------


class PR3FixRetest(unittest.TestCase):
    def test_resolves_within_attempts(self):
        with tempfile.TemporaryDirectory() as td:
            cd = Path(td)
            seq = iter([
                _spec(status="attention_needed", summary="port closed", should_notify=True),
                _spec(diagnosis="restarted", action_taken="restart svc", retest_result="unresolved"),
                _spec(diagnosis="service up", action_taken=None, retest_result="resolved"),
            ])
            result = lr.LoopSubAgent(config_dir=cd, llm_client=object(),
                                     spec_runner=lambda *a: next(seq)).execute(_make_loop(cd, "ci_monitor"))
            self.assertEqual("fixed", result.status)
            self.assertEqual(2, len(result.attempts))
            self.assertTrue(result.should_notify)

    def test_honest_stops_after_max_attempts(self):
        with tempfile.TemporaryDirectory() as td:
            cd = Path(td)

            def runner(d, u, c, s):
                n = s.get("loop.attempt_number", 0)
                if n == 0:
                    return _spec(status="attention_needed", summary="broken")
                return _spec(diagnosis=f"attempt {n} failed", action_taken="tried X", retest_result="unresolved")

            result = lr.LoopSubAgent(config_dir=cd, llm_client=object(),
                                     spec_runner=runner).execute(_make_loop(cd, "ci_monitor", max_fix=3))
            self.assertEqual("failed", result.status)
            self.assertEqual(3, len(result.attempts))
            self.assertTrue(result.should_notify)

    def test_no_escalation_when_auto_fix_disabled(self):
        with tempfile.TemporaryDirectory() as td:
            cd = Path(td)
            calls = {"n": 0}

            def runner(d, u, c, s):
                calls["n"] += 1
                return _spec(status="attention_needed", summary="needs eyes", should_notify=True)

            result = lr.LoopSubAgent(config_dir=cd, llm_client=object(),
                                     spec_runner=runner).execute(_make_loop(cd, "ci_monitor", auto_fix=False))
            self.assertEqual("attention_needed", result.status)
            self.assertEqual(1, calls["n"])  # only the single diagnostic pass

    def test_each_attempt_sees_prior_attempts(self):
        with tempfile.TemporaryDirectory() as td:
            cd = Path(td)
            seen = []

            def runner(d, u, c, s):
                n = s.get("loop.attempt_number", 0)
                if n == 0:
                    return _spec(status="attention_needed", summary="bad")
                seen.append(len(s.get("loop.prior_attempts", [])))
                return _spec(diagnosis="d", action_taken="a", retest_result="unresolved")

            lr.LoopSubAgent(config_dir=cd, llm_client=object(),
                            spec_runner=runner).execute(_make_loop(cd, "custom", max_fix=2))
            self.assertEqual([0, 1], seen)

    def test_deterministic_type_escalates_to_llm_on_fixed_logic_failure(self):
        with tempfile.TemporaryDirectory() as td:
            cd = Path(td)
            calls = {"n": 0}

            def runner(d, u, c, s):
                calls["n"] += 1
                return _spec(diagnosis="fixed it", action_taken="restart", retest_result="resolved")

            agent = lr.LoopSubAgent(config_dir=cd, llm_client=object(), spec_runner=runner)
            # Force the deterministic fast path to fail so escalation must kick in.
            agent._execute_deterministic = lambda loop: lr.LoopExecutionResult(
                status="attention_needed", summary="health fixed-logic failed"
            )
            result = agent.execute(_make_loop(cd, "health_monitor", auto_fix=True))
            self.assertGreaterEqual(calls["n"], 1)  # the LLM was invoked (escalation happened)
            self.assertEqual("fixed", result.status)
            self.assertTrue(result.escalated)


if __name__ == "__main__":
    unittest.main()
