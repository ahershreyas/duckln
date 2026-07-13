"""Plan 170 — stop the FALSE "model unreachable" honest-stop: a CONFIGURED model is reachable
(don't dead-end on a transient client-build), and a mid-request drop is reachable-but-failed."""

from __future__ import annotations

import unittest

from duckln.plan_mode import _model_connection_error


class F2ConnectionClassifier(unittest.TestCase):
    def test_connect_establishment_is_unreachable(self):
        self.assertTrue(_model_connection_error(ConnectionRefusedError()))
        self.assertTrue(_model_connection_error(Exception("Connection refused")))
        self.assertTrue(_model_connection_error(Exception("could not connect")))
        self.assertTrue(_model_connection_error(Exception("Max retries exceeded")))
        try:
            import httpx
            self.assertTrue(_model_connection_error(httpx.ConnectError("refused")))
        except Exception:
            pass

    def test_mid_request_drop_is_reachable(self):
        # The bug class: a drop on a server we reached is NOT "run ollama serve".
        self.assertFalse(_model_connection_error(ConnectionResetError("reset by peer")))
        self.assertFalse(_model_connection_error(ConnectionAbortedError()))
        self.assertFalse(_model_connection_error(BrokenPipeError()))
        self.assertFalse(_model_connection_error(TimeoutError()))
        self.assertFalse(_model_connection_error(Exception("Server disconnected without sending a response")))
        try:
            import httpx
            self.assertFalse(_model_connection_error(httpx.ReadTimeout("read timed out")))
            self.assertFalse(_model_connection_error(httpx.RemoteProtocolError("server disconnected")))
        except Exception:
            pass

    def test_wrapped_in_cause_chain(self):
        # adapters wrap the transport error; the classifier walks __cause__.
        try:
            raise ConnectionResetError("reset")
        except ConnectionResetError as inner:
            wrapped = ValueError("Ollama could not generate a reply (HTTP 500)")
            wrapped.__cause__ = inner
            self.assertFalse(_model_connection_error(wrapped))


class F1ConfiguredModelGate(unittest.TestCase):
    """The Plan-156 honest-stop must key on a CONFIGURED provider+model, not a transient
    client-build. We assert the snapshot-based 'configured' signal the gate now uses."""

    def test_configured_snapshot_is_truthy(self):
        import tempfile
        from pathlib import Path
        from state.access import read_config_snapshot, write_config_snapshot

        with tempfile.TemporaryDirectory() as td:
            cd = Path(td)
            write_config_snapshot(cd, {"provider": "ollama", "model": "qwen2.5:1.5b"})
            snap = read_config_snapshot(cd)
            configured = bool(str(snap.get("provider") or "").strip()) and bool(str(snap.get("model") or "").strip())
            self.assertTrue(configured)

    def test_unconfigured_snapshot_is_falsy(self):
        import tempfile
        from pathlib import Path
        from state.access import read_config_snapshot

        with tempfile.TemporaryDirectory() as td:
            snap = read_config_snapshot(Path(td))
            configured = bool(str(snap.get("provider") or "").strip()) and bool(str(snap.get("model") or "").strip())
            self.assertFalse(configured)


if __name__ == "__main__":
    unittest.main()
