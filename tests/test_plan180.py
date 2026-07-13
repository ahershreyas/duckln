"""Plan 180 — close the disclosed loose ends from Plans 178/179:
F1 remove the non-functional WEB_READER routing role; F2 prove the MCP execution path
end-to-end against a fake stdio MCP server; F3 the Specialized picker uses the live model list."""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


_FAKE_MCP_SERVER = '''\
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except Exception:
        continue
    mid = msg.get("id")
    method = msg.get("method")
    if method == "initialize":
        print(json.dumps({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": "2024-11-05", "capabilities": {}}}), flush=True)
    elif method == "tools/call":
        args = msg.get("params", {}).get("arguments", {})
        print(json.dumps({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": "echo:" + str(args.get("q", ""))}]}}), flush=True)
'''


class F1RoutableRoles(unittest.TestCase):
    # Plan 181 reversed Plan 180 F1's removal: WEB_READER is back BECAUSE it now has a real
    # consumer (the web-reader digest step). It is a genuine routable role again.
    def test_web_reader_is_a_routable_role_again(self):
        from duckln.ai_client import AGENT_ROLES

        self.assertIn("WEB_READER", AGENT_ROLES)
        self.assertEqual(AGENT_ROLES, ("SUPERVISOR", "REPO_AGENT", "ERROR_AGENT", "WEB_READER"))

    def test_status_shows_web_reader_with_token_note(self):
        from duckln.main import _render_agent_routing_status

        with TemporaryDirectory() as d:
            cfg = types.SimpleNamespace(model="m1", provider=types.SimpleNamespace(value="ollama"))
            text = _render_agent_routing_status(Path(d), cfg)
            self.assertIn("Web reader", text)
            self.assertIn("save tokens", text)


class F2McpExecutionEndToEnd(unittest.TestCase):
    def test_tools_call_round_trip_against_fake_server(self):
        from duckln.harness.tools import _call_mcp_stdio_tool

        with TemporaryDirectory() as d:
            server = Path(d) / "fake_mcp_server.py"
            server.write_text(_FAKE_MCP_SERVER, encoding="utf-8")
            cfg = {"command": sys.executable, "args": [str(server)]}
            res = _call_mcp_stdio_tool(cfg, tool_name="echo", arguments={"q": "hi"})
            self.assertTrue(res.ok, res.error_message)
            self.assertEqual(res.payload["result"]["content"][0]["text"], "echo:hi")
            self.assertEqual(res.payload["tool"], "echo")

    def test_spawn_failure_is_graceful(self):
        from duckln.harness.tools import _call_mcp_stdio_tool

        res = _call_mcp_stdio_tool(
            {"command": "/nonexistent/duckln-mcp-binary-xyz"}, tool_name="x", arguments={}
        )
        self.assertFalse(res.ok)
        self.assertEqual(res.error_code, "mcp_spawn_failed")


class F3SpecializedUsesLiveModelList(unittest.TestCase):
    def test_picker_offers_fetched_models(self):
        import duckln.main as m
        from duckln.ai_client import ProviderModel, read_active_routing

        class _Validation:
            ok = True
            models = (ProviderModel(id="m-a", display_name="A"), ProviderModel(id="m-b", display_name="B"))

        class _Adapter:
            def validate_api_key(self, _key):
                return _Validation()

        orig = m.get_provider_adapter_for_base_url
        m.get_provider_adapter_for_base_url = lambda *_a, **_k: _Adapter()
        try:
            with TemporaryDirectory() as d:
                cfg = types.SimpleNamespace(
                    model="m-a", provider=types.SimpleNamespace(value="ollama"),
                    api_key=None, base_url=None,
                )
                paths = types.SimpleNamespace(config_dir=Path(d))
                seen: list[tuple[str, tuple[str, ...]]] = []

                def _select(prompt, options):
                    seen.append((prompt, tuple(options)))
                    if prompt.startswith("Model configuration mode"):
                        return "Specialized — assign a model per agent"
                    return "m-b"  # pick the non-default model for every role

                m._handle_agent_routing_command(
                    cfg, paths, select=_select, text_prompt=None, display=lambda _t: None,
                )
                routing = read_active_routing(d)
                self.assertEqual(
                    routing,
                    {"SUPERVISOR": "m-b", "REPO_AGENT": "m-b", "ERROR_AGENT": "m-b", "WEB_READER": "m-b"},
                )
                # The live model list (m-b) was offered as a select option (arrow-key UX).
                self.assertTrue(any("m-b" in opts for _p, opts in seen))
        finally:
            m.get_provider_adapter_for_base_url = orig


if __name__ == "__main__":
    unittest.main()
