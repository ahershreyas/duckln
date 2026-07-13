"""Plan 65 Phase 5 — Observability tests (TraceLogger, redaction, slash renderers)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from duckln.harness.trace import (
    TraceEvent,
    TraceLogger,
    agent_log_path,
    new_session_id,
    render_agents_list,
    render_costs,
    render_trace_timeline,
    session_dir,
)


# --- session_id allocation ---------------------------------------------------


class SessionIdTests(unittest.TestCase):
    def test_session_id_is_12_hex_chars(self) -> None:
        sid = new_session_id()
        self.assertEqual(len(sid), 12)
        self.assertTrue(all(c in "0123456789abcdef" for c in sid))

    def test_session_ids_are_unique(self) -> None:
        ids = {new_session_id() for _ in range(50)}
        self.assertEqual(len(ids), 50)


# --- Path resolution ---------------------------------------------------------


class PathHelpersTests(unittest.TestCase):
    def test_session_dir_under_logs(self) -> None:
        d = session_dir(Path("/tmp/duckln"), "abc123def456")
        self.assertEqual(d, Path("/tmp/duckln/logs/sessions/abc123def456"))

    def test_agent_log_path_under_session(self) -> None:
        p = agent_log_path(Path("/tmp/duckln"), "abc", "supervisor")
        self.assertTrue(str(p).endswith("logs/sessions/abc/agents/supervisor.jsonl"))


# --- TraceLogger emit + read -------------------------------------------------


class TraceLoggerEmitTests(unittest.TestCase):
    def test_emit_creates_file_and_writes_one_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logger = TraceLogger(config_dir=Path(tmp), session_id="s1")
            event = TraceEvent(
                ts="2026-01-01T00:00:00Z",
                session_id="s1", agent="alice", turn=1,
                kind="tool_call", tool="shell.probe",
                args={"command": "node --version"},
                result_summary="ok", latency_ms=42.0,
            )
            logger.emit(event)
            path = agent_log_path(Path(tmp), "s1", "alice")
            self.assertTrue(path.exists())
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0])["tool"], "shell.probe")

    def test_emit_multiple_events_appends(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logger = TraceLogger(config_dir=Path(tmp), session_id="s2")
            for i in range(5):
                logger.emit(TraceEvent(
                    ts=f"2026-01-01T00:00:0{i}Z", session_id="s2",
                    agent="bob", turn=i, kind="tool_call",
                ))
            path = agent_log_path(Path(tmp), "s2", "bob")
            self.assertEqual(len(path.read_text().splitlines()), 5)

    def test_separate_agents_have_separate_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logger = TraceLogger(config_dir=Path(tmp), session_id="s3")
            logger.emit(TraceEvent(ts="t", session_id="s3", agent="a", turn=1, kind="tool_call"))
            logger.emit(TraceEvent(ts="t", session_id="s3", agent="b", turn=1, kind="tool_call"))
            self.assertTrue(agent_log_path(Path(tmp), "s3", "a").exists())
            self.assertTrue(agent_log_path(Path(tmp), "s3", "b").exists())


# --- Token accounting --------------------------------------------------------


class TokenAccountingTests(unittest.TestCase):
    def test_token_totals_accumulate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logger = TraceLogger(config_dir=Path(tmp), session_id="s4")
            for i in range(3):
                logger.emit(TraceEvent(
                    ts="t", session_id="s4", agent="alice", turn=i,
                    kind="tool_call", tokens_in=100, tokens_out=50,
                ))
            totals = logger.token_totals()
            self.assertEqual(totals["alice"]["in"], 300)
            self.assertEqual(totals["alice"]["out"], 150)

    def test_event_counts_increment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logger = TraceLogger(config_dir=Path(tmp), session_id="s5")
            for _ in range(7):
                logger.emit(TraceEvent(
                    ts="t", session_id="s5", agent="alice", turn=0, kind="tool_call",
                ))
            self.assertEqual(logger.event_counts()["alice"], 7)


# --- Redaction ---------------------------------------------------------------


class RedactionTests(unittest.TestCase):
    def test_redacts_sensitive_args(self) -> None:
        """When args contains an API-key-shaped string, it's redacted before writing."""
        with tempfile.TemporaryDirectory() as tmp:
            logger = TraceLogger(config_dir=Path(tmp), session_id="s6")
            logger.emit(TraceEvent(
                ts="t", session_id="s6", agent="alice", turn=1, kind="tool_call",
                tool="x.y",
                args={"key": "sk-abcdef1234567890abcdef1234567890abcdef12"},
            ))
            content = agent_log_path(Path(tmp), "s6", "alice").read_text()
            # The raw key should NOT appear verbatim.
            self.assertNotIn("sk-abcdef1234567890abcdef1234567890abcdef12", content)


# --- Read events back --------------------------------------------------------


class ReadEventsTests(unittest.TestCase):
    def test_read_back_events_in_time_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logger = TraceLogger(config_dir=Path(tmp), session_id="s7")
            logger.emit(TraceEvent(ts="2026-01-01T00:00:03Z", session_id="s7", agent="a", turn=3, kind="tool_call"))
            logger.emit(TraceEvent(ts="2026-01-01T00:00:01Z", session_id="s7", agent="b", turn=1, kind="tool_call"))
            logger.emit(TraceEvent(ts="2026-01-01T00:00:02Z", session_id="s7", agent="a", turn=2, kind="tool_call"))
            events = logger.read_events()
            timestamps = [e.ts for e in events]
            self.assertEqual(timestamps, sorted(timestamps))

    def test_read_events_filtered_by_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logger = TraceLogger(config_dir=Path(tmp), session_id="s8")
            logger.emit(TraceEvent(ts="t", session_id="s8", agent="a", turn=1, kind="tool_call"))
            logger.emit(TraceEvent(ts="t", session_id="s8", agent="b", turn=1, kind="tool_call"))
            a_events = logger.read_events(agent="a")
            self.assertEqual(len(a_events), 1)
            self.assertEqual(a_events[0].agent, "a")


# --- Slash renderers ---------------------------------------------------------


class RenderAgentsListTests(unittest.TestCase):
    def test_lists_pilot_agents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = render_agents_list(config_dir=Path(tmp))
            # The pilot agents (supervisor, node_typescript_specialist) come
            # from the package's builtin_agents_directory.
            self.assertIn("supervisor", output)
            self.assertIn("node_typescript_specialist", output)
            self.assertIn("coordinator", output.lower())


class RenderTraceTimelineTests(unittest.TestCase):
    def test_no_events_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = render_trace_timeline(config_dir=Path(tmp), session_id="missing")
            self.assertIn("No trace events", output)

    def test_renders_emitted_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logger = TraceLogger(config_dir=Path(tmp), session_id="s9")
            logger.emit(TraceEvent(
                ts="2026-01-01T00:00:00Z", session_id="s9", agent="alice",
                turn=1, kind="tool_call", tool="shell.probe", latency_ms=12.0,
            ))
            output = render_trace_timeline(config_dir=Path(tmp), session_id="s9")
            self.assertIn("alice", output)
            self.assertIn("shell.probe", output)
            self.assertIn("12ms", output)


class RenderCostsTests(unittest.TestCase):
    def test_costs_sum_correctly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logger = TraceLogger(config_dir=Path(tmp), session_id="s10")
            logger.emit(TraceEvent(ts="t", session_id="s10", agent="a", turn=1, kind="tool_call", tokens_in=100, tokens_out=50))
            logger.emit(TraceEvent(ts="t", session_id="s10", agent="a", turn=2, kind="tool_call", tokens_in=200, tokens_out=80))
            output = render_costs(config_dir=Path(tmp), session_id="s10")
            self.assertIn("300 tokens in", output)
            self.assertIn("130 tokens out", output)

    def test_costs_no_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = render_costs(config_dir=Path(tmp), session_id="empty")
            self.assertIn("No trace events", output)


if __name__ == "__main__":
    unittest.main()
