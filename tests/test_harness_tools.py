"""Plan 65 Phase 1 — Tool Registry tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from duckln.harness.tools import (
    AgentContext,
    ToolCostHint,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    _safety_classes_for_mode,
    _validate_args,
    build_default_registry,
)
from duckln.modes import ControlMode
from duckln.safety import SafetyClass


# --- Test fixtures -----------------------------------------------------------


def _ctx(*, mode: ControlMode = ControlMode.HOTL, project_dir: Path | None = None) -> AgentContext:
    return AgentContext(
        agent_name="test-agent",
        mode=mode,
        config_dir=Path("/tmp/duckln-test"),
        project_dir=project_dir,
        execution_target="local",
    )


def _ok_handler(args, ctx):
    return ToolResult.success({"echoed": args})


def _raising_handler(args, ctx):
    raise RuntimeError("boom")


# --- ToolSpec name validation ------------------------------------------------


class ToolSpecTests(unittest.TestCase):
    def test_spec_requires_dotted_name(self) -> None:
        with self.assertRaises(ValueError):
            ToolSpec(
                name="noDot",
                description="x",
                args_schema={},
                safety_class=SafetyClass.S0,
                handler=_ok_handler,
            )

    def test_spec_accepts_dotted_name(self) -> None:
        spec = ToolSpec(
            name="ns.thing",
            description="x",
            args_schema={},
            safety_class=SafetyClass.S0,
            handler=_ok_handler,
        )
        self.assertEqual(spec.name, "ns.thing")
        self.assertIsInstance(spec.cost, ToolCostHint)


# --- _validate_args ----------------------------------------------------------


class ValidateArgsTests(unittest.TestCase):
    def test_root_must_be_dict(self) -> None:
        ok, msg = _validate_args("not a dict", {})
        self.assertFalse(ok)
        self.assertEqual(msg, "type:root:expected_dict")

    def test_missing_required_arg(self) -> None:
        ok, msg = _validate_args({}, {"cmd": {"type": "str", "required": True}})
        self.assertFalse(ok)
        self.assertEqual(msg, "missing:cmd")

    def test_missing_optional_arg_passes(self) -> None:
        ok, msg = _validate_args({}, {"cmd": {"type": "str", "required": False}})
        self.assertTrue(ok)
        self.assertEqual(msg, "")

    def test_wrong_type_detected(self) -> None:
        ok, msg = _validate_args({"n": "five"}, {"n": {"type": "int", "required": True}})
        self.assertFalse(ok)
        self.assertEqual(msg, "type:n:expected_int")

    def test_correct_types_pass(self) -> None:
        schema = {
            "s": {"type": "str", "required": True},
            "i": {"type": "int", "required": True},
            "b": {"type": "bool", "required": True},
            "lst": {"type": "list", "required": True},
            "obj": {"type": "dict", "required": True},
        }
        ok, msg = _validate_args(
            {"s": "x", "i": 1, "b": True, "lst": [1, 2], "obj": {"k": "v"}}, schema
        )
        self.assertTrue(ok, msg)


# --- Safety class → mode policy ---------------------------------------------


class SafetyByModeTests(unittest.TestCase):
    def test_hitl_only_s0(self) -> None:
        self.assertEqual(_safety_classes_for_mode(ControlMode.HITL), frozenset({SafetyClass.S0}))

    def test_hotl_s0_through_s2(self) -> None:
        allowed = _safety_classes_for_mode(ControlMode.HOTL)
        self.assertIn(SafetyClass.S0, allowed)
        self.assertIn(SafetyClass.S2, allowed)
        self.assertNotIn(SafetyClass.S3, allowed)
        self.assertNotIn(SafetyClass.S4, allowed)

    def test_hootlwo_includes_s3(self) -> None:
        allowed = _safety_classes_for_mode(ControlMode.HOOTLWO)
        self.assertIn(SafetyClass.S3, allowed)
        self.assertNotIn(SafetyClass.S4, allowed)


# --- Registry registration + lookup ------------------------------------------


class RegistryRegistrationTests(unittest.TestCase):
    def test_register_and_lookup(self) -> None:
        reg = ToolRegistry()
        spec = ToolSpec(
            name="x.y", description="d", args_schema={}, safety_class=SafetyClass.S0,
            handler=_ok_handler,
        )
        reg.register(spec)
        self.assertIs(reg.lookup("x.y"), spec)
        self.assertIsNone(reg.lookup("nope"))

    def test_duplicate_registration_raises(self) -> None:
        reg = ToolRegistry()
        spec = ToolSpec(
            name="x.y", description="d", args_schema={}, safety_class=SafetyClass.S0,
            handler=_ok_handler,
        )
        reg.register(spec)
        with self.assertRaises(ValueError):
            reg.register(spec)

    def test_names_are_sorted(self) -> None:
        reg = ToolRegistry()
        for n in ("z.b", "a.a", "m.m"):
            reg.register(ToolSpec(
                name=n, description="d", args_schema={}, safety_class=SafetyClass.S0,
                handler=_ok_handler,
            ))
        self.assertEqual(reg.names(), ("a.a", "m.m", "z.b"))


# --- Registry mode-aware availability ---------------------------------------


class RegistryAvailabilityTests(unittest.TestCase):
    def _build_mixed(self) -> ToolRegistry:
        reg = ToolRegistry()
        for name, cls in (
            ("a.s0", SafetyClass.S0),
            ("b.s1", SafetyClass.S1),
            ("c.s2", SafetyClass.S2),
            ("d.s3", SafetyClass.S3),
        ):
            reg.register(ToolSpec(
                name=name, description="d", args_schema={}, safety_class=cls,
                handler=_ok_handler,
            ))
        return reg

    def test_hitl_sees_only_s0(self) -> None:
        reg = self._build_mixed()
        names = [s.name for s in reg.available_for_mode(ControlMode.HITL)]
        self.assertEqual(names, ["a.s0"])

    def test_hotl_sees_s0_to_s2(self) -> None:
        reg = self._build_mixed()
        names = [s.name for s in reg.available_for_mode(ControlMode.HOTL)]
        self.assertEqual(set(names), {"a.s0", "b.s1", "c.s2"})

    def test_hootlwo_sees_s0_to_s3(self) -> None:
        reg = self._build_mixed()
        names = [s.name for s in reg.available_for_mode(ControlMode.HOOTLWO)]
        self.assertEqual(set(names), {"a.s0", "b.s1", "c.s2", "d.s3"})


# --- Registry dispatch -------------------------------------------------------


class RegistryDispatchTests(unittest.TestCase):
    def _reg_with(self, spec: ToolSpec) -> ToolRegistry:
        reg = ToolRegistry()
        reg.register(spec)
        return reg

    def test_unknown_tool_returns_failure(self) -> None:
        reg = ToolRegistry()
        result = reg.dispatch("does.not.exist", {}, _ctx())
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "unknown_tool")

    def test_schema_violation_returns_structured_error(self) -> None:
        spec = ToolSpec(
            name="x.y", description="d",
            args_schema={"cmd": {"type": "str", "required": True}},
            safety_class=SafetyClass.S0, handler=_ok_handler,
        )
        result = self._reg_with(spec).dispatch("x.y", {}, _ctx())
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "schema_violation")
        self.assertIn("missing:cmd", result.error_message)

    def test_needs_approval_when_safety_above_mode_ceiling(self) -> None:
        spec = ToolSpec(
            name="x.y", description="d", args_schema={},
            safety_class=SafetyClass.S3, handler=_ok_handler,
        )
        result = self._reg_with(spec).dispatch("x.y", {}, _ctx(mode=ControlMode.HITL))
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "needs_approval")

    def test_handler_exception_is_caught(self) -> None:
        spec = ToolSpec(
            name="x.y", description="d", args_schema={},
            safety_class=SafetyClass.S0, handler=_raising_handler,
        )
        result = self._reg_with(spec).dispatch("x.y", {}, _ctx())
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "handler_exception")
        self.assertIn("boom", result.error_message)

    def test_successful_dispatch_passes_args_and_ctx(self) -> None:
        captured = {}

        def _capture(args, ctx):
            captured["args"] = args
            captured["agent"] = ctx.agent_name
            return ToolResult.success({"ok": True})

        spec = ToolSpec(
            name="x.y", description="d",
            args_schema={"q": {"type": "str", "required": True}},
            safety_class=SafetyClass.S0, handler=_capture,
        )
        result = self._reg_with(spec).dispatch("x.y", {"q": "hi"}, _ctx())
        self.assertTrue(result.ok)
        self.assertEqual(captured["args"], {"q": "hi"})
        self.assertEqual(captured["agent"], "test-agent")


# --- Default registry --------------------------------------------------------


class DefaultRegistryTests(unittest.TestCase):
    def test_default_registry_contains_expected_tool_count(self) -> None:
        reg = build_default_registry(include_handlers=False)
        # 11 original (Plan 65) + 7 repo-agent tools (Plan 92/93/98).
        self.assertEqual(len(reg.names()), 18)

    def test_expected_tool_names_present(self) -> None:
        reg = build_default_registry(include_handlers=False)
        expected = {
            "shell.run", "shell.probe", "fs.read_file", "fs.list_dir",
            "web.search", "web.fetch", "state.read", "state.write_skill",
            "state.write_failure", "user.approve", "user.clarify",
            # Plan 92/93/98 repo-agent tools:
            "fs.search", "fs.glob", "fs.write", "fs.edit", "repo.run", "app.serve", "git.run",
        }
        self.assertEqual(set(reg.names()), expected)

    def test_safety_classes_assigned_correctly(self) -> None:
        reg = build_default_registry(include_handlers=False)
        self.assertEqual(reg.lookup("shell.probe").safety_class, SafetyClass.S0)
        self.assertEqual(reg.lookup("shell.run").safety_class, SafetyClass.S2)
        self.assertEqual(reg.lookup("state.write_skill").safety_class, SafetyClass.S1)


# --- Real tool handlers (fs traversal guard, probe S0 enforcement) -----------


class FsReadFileHandlerTests(unittest.TestCase):
    def test_path_outside_project_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = build_default_registry()  # real handlers
            result = reg.dispatch(
                "fs.read_file",
                {"path": "../../../etc/passwd"},
                _ctx(project_dir=Path(tmp)),
            )
            self.assertFalse(result.ok)
            self.assertEqual(result.error_code, "path_escape")

    def test_reads_file_inside_project_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "hello.txt").write_text("hi\n", encoding="utf-8")
            reg = build_default_registry()
            result = reg.dispatch(
                "fs.read_file", {"path": "hello.txt"}, _ctx(project_dir=project)
            )
            self.assertTrue(result.ok)
            self.assertEqual(result.payload["text"], "hi\n")

    def test_no_project_dir_rejects(self) -> None:
        reg = build_default_registry()
        result = reg.dispatch(
            "fs.read_file", {"path": "anything"}, _ctx(project_dir=None)
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "path_escape")


class FsListDirHandlerTests(unittest.TestCase):
    def test_lists_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "a.txt").write_text("a")
            (project / "b.txt").write_text("b")
            (project / "sub").mkdir()
            reg = build_default_registry()
            result = reg.dispatch("fs.list_dir", {"path": "."}, _ctx(project_dir=project))
            self.assertTrue(result.ok)
            entries = result.payload["entries"]
            self.assertIn("a.txt", entries)
            self.assertIn("sub", entries)

    def test_nonexistent_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = build_default_registry()
            result = reg.dispatch(
                "fs.list_dir", {"path": "nope"}, _ctx(project_dir=Path(tmp))
            )
            self.assertFalse(result.ok)
            self.assertEqual(result.error_code, "not_found")


class ShellProbeHandlerTests(unittest.TestCase):
    def test_rejects_non_s0_command(self) -> None:
        reg = build_default_registry()
        # sudo apt install is S2, not S0
        result = reg.dispatch(
            "shell.probe", {"command": "sudo apt install -y nodejs"}, _ctx()
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "not_a_probe")


class UserApproveHandlerTests(unittest.TestCase):
    def test_missing_approve_callback(self) -> None:
        reg = build_default_registry()
        result = reg.dispatch("user.approve", {"prompt": "ok?"}, _ctx())
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "no_approve_callback")

    def test_with_approve_callback(self) -> None:
        ctx = AgentContext(
            agent_name="t", mode=ControlMode.HOTL, config_dir=Path("/tmp"),
            approve=lambda _msg: True,
        )
        reg = build_default_registry()
        result = reg.dispatch("user.approve", {"prompt": "ok?"}, ctx)
        self.assertTrue(result.ok)
        self.assertTrue(result.payload["approved"])


class WebFetchHandlerTests(unittest.TestCase):
    def test_non_authoritative_url_rejected(self) -> None:
        reg = build_default_registry()
        result = reg.dispatch(
            "web.fetch", {"url": "https://random-blog.example/install"}, _ctx()
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "non_authoritative")


if __name__ == "__main__":
    unittest.main()
