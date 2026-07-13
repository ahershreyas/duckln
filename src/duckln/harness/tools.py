"""Plan 65 Phase 1 — Tool Registry.

The Tool Registry is the central catalog of capabilities that harness agents
can call by name. Tools are thin wrappers around existing Duckln primitives
(`ControlledCommandRunner`, `internet_search_summary`, `assess_command`, ...)
exposed through a uniform schema so an agent's prompt can describe what's
available without the agent caring about implementation details.

Conventions:
- Tool names use dotted namespaces: ``shell.run``, ``fs.read_file``, ``web.search``.
- Every tool declares its `SafetyClass` (S0..S4) which constrains availability
  by `ControlMode` (HITL only sees S0; HOTL sees S0..S2; HOOTLWO sees S0..S2
  plus S3 when explicitly whitelisted).
- Tool args are validated against a minimal JSON-schema-style dict; bad args
  produce a structured `ToolResult(ok=False, error="schema_violation:...")`
  without ever reaching the handler.
- Handlers receive an `AgentContext` carrying the active mode, config_dir,
  project_dir, display callback, and approve callback, so they can route to
  the right runtime helpers (e.g. local runner vs. remote SSH wrapper).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from duckln.modes import ControlMode
from duckln.safety import SafetyClass


# --- AgentContext ------------------------------------------------------------


@dataclass(frozen=True)
class AgentContext:
    """Per-agent runtime context passed to every tool handler.

    Built once per agent run by the harness loop; tools read it but never
    mutate it. Use ``AgentState`` (Phase 3) for mutable per-run state.
    """

    agent_name: str
    mode: ControlMode
    config_dir: Path
    project_dir: Path | None = None
    execution_target: str = "local"
    display: Callable[[str], None] | None = None
    approve: Callable[[str], bool] | None = None
    extra: dict = field(default_factory=dict)


# --- ToolResult --------------------------------------------------------------


@dataclass(frozen=True)
class ToolResult:
    """Uniform result envelope returned by every tool handler.

    Carries either a structured payload (on success) or an error code + message
    (on failure). The agent observes these consistently regardless of which
    tool was dispatched.
    """

    ok: bool
    payload: Any = None
    error_code: str | None = None
    error_message: str | None = None
    latency_ms: float | None = None
    tokens_consumed: int = 0  # for tools that themselves call LLMs

    @classmethod
    def success(cls, payload: Any = None, *, latency_ms: float | None = None) -> "ToolResult":
        return cls(ok=True, payload=payload, latency_ms=latency_ms)

    @classmethod
    def failure(cls, code: str, message: str = "") -> "ToolResult":
        return cls(ok=False, error_code=code, error_message=message)


# --- ToolCostHint ------------------------------------------------------------


@dataclass(frozen=True)
class ToolCostHint:
    """Rough cost characterization so the harness can plan parallel dispatch."""

    typical_latency_ms: int = 50
    network: bool = False
    side_effects: bool = False  # mutates filesystem, runs commands, etc.
    requires_approval: bool = False


# --- ToolSpec ----------------------------------------------------------------


ToolHandler = Callable[[dict, AgentContext], ToolResult]


@dataclass(frozen=True)
class ToolSpec:
    """Declarative spec for a tool that agents can call."""

    name: str
    description: str
    args_schema: dict  # minimal {"<arg>": {"type": "str|int|bool|dict|list", "required": bool}}
    safety_class: SafetyClass
    handler: ToolHandler
    cost: ToolCostHint = field(default_factory=ToolCostHint)

    def __post_init__(self) -> None:
        if not self.name or "." not in self.name:
            raise ValueError(
                f"Tool name must be dotted (e.g. 'shell.run'); got: {self.name!r}"
            )


# --- Schema validation -------------------------------------------------------


_PYTHON_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "str": (str,),
    "int": (int,),
    "float": (float, int),
    "bool": (bool,),
    "dict": (dict,),
    "list": (list, tuple),
}


def _validate_args(args: dict, schema: dict) -> tuple[bool, str]:
    """Validate a tool's args against its declarative schema.

    Returns ``(ok, message)``. The message is empty on success or an
    error code on failure (e.g. ``"missing:foo"``, ``"type:foo:expected_str"``).
    """
    if not isinstance(args, dict):
        return False, "type:root:expected_dict"
    for key, spec in (schema or {}).items():
        required = bool(spec.get("required", False))
        if key not in args:
            if required:
                return False, f"missing:{key}"
            continue
        expected = spec.get("type")
        if expected and expected in _PYTHON_TYPE_MAP:
            allowed_types = _PYTHON_TYPE_MAP[expected]
            if not isinstance(args[key], allowed_types):
                return False, f"type:{key}:expected_{expected}"
    return True, ""


# --- Mode → SafetyClass policy -----------------------------------------------


def _safety_classes_for_mode(mode: ControlMode) -> frozenset[SafetyClass]:
    """Which safety classes a given mode can dispatch WITHOUT extra approval.

    Anything outside this set may still be dispatched but the tool's handler
    is responsible for going through the explicit approve flow (e.g.
    ``ctx.approve(...)``) before taking effect.
    """
    if mode == ControlMode.HITL:
        return frozenset({SafetyClass.S0})
    if mode == ControlMode.HOTL:
        return frozenset({SafetyClass.S0, SafetyClass.S1, SafetyClass.S2})
    if mode == ControlMode.HOOTLWO:
        return frozenset({SafetyClass.S0, SafetyClass.S1, SafetyClass.S2, SafetyClass.S3})
    return frozenset({SafetyClass.S0})


def _approval_summary(name: str, args: dict) -> str:
    """A concise human prompt for a side-effecting tool call."""
    if name == "fs.write":
        return f"Write file `{args.get('path')}` ({len(str(args.get('content','')))} chars)? [y/n]"
    if name == "fs.edit":
        return f"Edit file `{args.get('path')}` (replace a string)? [y/n]"
    if name == "repo.run":
        return f"Run in the repo: `{str(args.get('command',''))[:120]}`? [y/n]"
    if name == "git.run":
        return f"Run `git {str(args.get('args',''))[:120]}`? [y/n]"
    return f"Allow tool `{name}`? [y/n]"


def _tool_policy_decision(name: str, ctx: AgentContext) -> str:
    """Plan 95: consult the user's persisted per-tool policy. Returns
    'allow' | 'deny' | '' (no override). Read from ctx.extra['tool_policy'] (a
    dict {tool_name: 'allow'|'deny'}) so dispatch stays pure/testable; the live
    wiring loads it from config. Never raises."""
    policy = ctx.extra.get("tool_policy") if isinstance(ctx.extra, dict) else None
    if not isinstance(policy, dict):
        return ""
    decision = str(policy.get(name, "")).strip().lower()
    return decision if decision in ("allow", "deny") else ""


# --- ToolRegistry ------------------------------------------------------------


class ToolRegistry:
    """Central catalog of tools available to harness agents.

    Construct once at session start, register all tools, then pass to the
    harness loop. The registry is immutable in practice — re-registering a
    name raises ValueError so agents can trust a name resolves to one handler.
    """

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"Tool '{spec.name}' already registered")
        self._specs[spec.name] = spec

    def lookup(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._specs.keys()))

    def available_for_mode(self, mode: ControlMode) -> tuple[ToolSpec, ...]:
        """Tools that can be dispatched directly (without per-call approval)
        in the given control mode."""
        allowed = _safety_classes_for_mode(mode)
        return tuple(
            spec for name, spec in sorted(self._specs.items())
            if spec.safety_class in allowed
        )

    def dispatch(self, name: str, args: dict, ctx: AgentContext) -> ToolResult:
        """Look up the tool by name, validate args, enforce mode policy,
        and call the handler. All errors come back as `ToolResult.failure`."""
        spec = self._specs.get(name)
        if spec is None:
            return ToolResult.failure(
                "unknown_tool",
                f"No tool named '{name}'. Available: {', '.join(self.names()) or '(none)'}",
            )
        ok, schema_error = _validate_args(args, spec.args_schema)
        if not ok:
            return ToolResult.failure("schema_violation", schema_error)
        # Plan 95: a persisted per-tool policy can force-allow or force-deny a tool
        # regardless of mode (user-configurable HITL). Consulted before the mode gate.
        policy = _tool_policy_decision(name, ctx)
        if policy == "deny":
            return ToolResult.failure("policy_denied", f"Tool '{name}' is denied by the user's tool policy.")
        if spec.safety_class not in _safety_classes_for_mode(ctx.mode) and policy != "allow":
            # Caller must use the explicit approve pathway (e.g. via
            # `user.approve` tool) for tools above the mode's auto-dispatch
            # ceiling. We return a structured 'needs_approval' so the agent
            # loop can route to approval instead of executing blindly.
            return ToolResult.failure(
                "needs_approval",
                f"Tool '{name}' is {spec.safety_class.value}; mode {ctx.mode.value} "
                "requires explicit user approval first.",
            )
        # Plan 92/95: a side-effecting tool (write/edit/run/git) goes through the
        # user's approve callback in HITL/HOTL (HOOTLWO auto-runs). `policy == allow`
        # (an explicit user allowlist entry) skips the prompt.
        needs_prompt = (
            bool(spec.cost.side_effects)
            and spec.safety_class in (SafetyClass.S2, SafetyClass.S3, SafetyClass.S4)
            and ctx.mode in (ControlMode.HITL, ControlMode.HOTL)
            and policy != "allow"
        )
        if needs_prompt:
            if ctx.approve is None:
                return ToolResult.failure("needs_approval", f"Tool '{name}' needs approval but no approve callback is set.")
            summary = _approval_summary(name, args)
            if not bool(ctx.approve(summary)):
                return ToolResult.failure("user_denied", f"User declined `{name}`.")
        try:
            return spec.handler(args, ctx)
        except Exception as exc:  # defensive: tool bugs must not crash the loop
            return ToolResult.failure("handler_exception", f"{type(exc).__name__}: {exc}")


# --- Default tool registration helpers ---------------------------------------


# --- Plan 178 F5: execute user-registered SHELL / MCP extensions ------------
# A pulled/registered tool/MCP is VISIBLE in the manifest (registered_extension_entries);
# these handlers make it actually CALLABLE — a shell tool runs its configured command through
# the same approval + target + S-class path as shell.run; an MCP tool is invoked over stdio
# JSON-RPC (tools/call). Both are S2 (approval-gated). An unconfigured record fails gracefully.


def _make_registered_shell_handler(tool_id: str, command_template: str) -> Callable:
    def _h(args: dict, ctx: AgentContext) -> ToolResult:
        cmd = (command_template or "").strip()
        if not cmd:
            return ToolResult.failure(
                "not_configured",
                f"Registered tool '{tool_id}' has no command set yet. Configure it via /tools, then retry.",
            )
        user_input = str(args.get("input") or "").strip()
        if "{input}" in cmd:
            cmd = cmd.replace("{input}", user_input)
        elif user_input:
            cmd = f"{cmd} {user_input}"
        # Route through the SAME approval + target + S-class path as shell.run.
        return _shell_run_handler({"command": cmd, "timeout_seconds": float(args.get("timeout_seconds") or 60.0)}, ctx)

    return _h


def _call_mcp_stdio_tool(cfg: dict, *, tool_name: str, arguments: dict) -> ToolResult:
    """Invoke a tool on a user-registered MCP STDIO server (JSON-RPC: initialize →
    notifications/initialized → tools/call). Bounded + safe-failing. Exercised live against a
    real MCP server; an unconfigured/absent command fails gracefully."""
    import json as _json
    import subprocess

    command = str((cfg or {}).get("command") or "").strip()
    if not command:
        return ToolResult.failure(
            "not_configured",
            "This MCP server has no launch command configured. Set it via /mcp, then retry.",
        )
    argv = [command] + [str(a) for a in ((cfg or {}).get("args") or [])]
    env = {**os.environ, **{str(k): str(v) for k, v in ((cfg or {}).get("env") or {}).items()}}
    try:
        proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True,
        )
    except Exception as exc:  # noqa: BLE001 — surface a clean tool error, never crash the loop
        return ToolResult.failure("mcp_spawn_failed", f"Could not start the MCP server '{command}': {exc}")
    try:
        lines = [
            _json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "duckln", "version": "1.0"}}}),
            _json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            _json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                         "params": {"name": tool_name, "arguments": arguments or {}}}),
        ]
        out, err = proc.communicate("\n".join(lines) + "\n", timeout=30)
    except Exception as exc:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:
            pass
        return ToolResult.failure("mcp_error", f"MCP call to '{tool_name}' failed: {exc}")
    result_obj = None
    for line in (out or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = _json.loads(line)
        except Exception:
            continue
        if msg.get("id") == 2:
            result_obj = msg
    if result_obj is None:
        return ToolResult.failure("mcp_no_result", f"No tools/call result from the MCP server. stderr: {(err or '')[:400]}")
    if "error" in result_obj:
        return ToolResult.failure("mcp_tool_error", str(result_obj.get("error"))[:400])
    return ToolResult.success({"result": result_obj.get("result"), "tool": tool_name})


def _make_registered_mcp_handler(tool_id: str, cfg: dict) -> Callable:
    def _h(args: dict, ctx: AgentContext) -> ToolResult:
        tool_name = str(args.get("tool") or (cfg or {}).get("name") or tool_id)
        arguments = args.get("arguments") if isinstance(args.get("arguments"), dict) else {}
        return _call_mcp_stdio_tool(cfg or {}, tool_name=tool_name, arguments=arguments)

    return _h


def _registered_extension_specs(config_dir) -> list["ToolSpec"]:
    """Build EXECUTABLE ToolSpecs for the user's registered shell/MCP extensions so a pulled
    tool/MCP is CALLABLE (not merely visible). S2 (approval-gated). Best-effort."""
    if config_dir is None:
        return []
    try:
        import json as _json
        from pathlib import Path as _P
        from state.store import initialize_state_store

        records = initialize_state_store(_P(config_dir)).list_managed_memory_records()
    except Exception:
        return []
    specs: list[ToolSpec] = []
    for rec in records:
        kind = getattr(rec, "memory_kind", "")
        if kind not in ("registered_tool", "registered_mcp"):
            continue
        try:
            cfg = _json.loads(getattr(rec, "content", "") or "{}")
        except Exception:
            cfg = {}
        tool_id = str(cfg.get("tool_id") or getattr(rec, "memory_key", "")).strip()
        if not tool_id or "." not in tool_id:
            continue
        draft = cfg.get("draft_config") if isinstance(cfg.get("draft_config"), dict) else {}
        description = str(cfg.get("description") or tool_id)[:160]
        if kind == "registered_mcp":
            handler = _make_registered_mcp_handler(tool_id, draft)
            schema = {"tool": {"type": "str", "required": False}, "arguments": {"type": "dict", "required": False}}
            network = True
        else:
            handler = _make_registered_shell_handler(tool_id, str(draft.get("command") or ""))
            schema = {"input": {"type": "str", "required": False}, "timeout_seconds": {"type": "int", "required": False}}
            network = False
        specs.append(ToolSpec(
            name=tool_id,
            description=f"User-registered {('MCP' if network else 'shell')} tool: {description}",
            args_schema=schema,
            safety_class=SafetyClass.S2,
            handler=handler,
            cost=ToolCostHint(typical_latency_ms=1000, network=network, side_effects=True),
        ))
    return specs


def build_default_registry(*, include_handlers: bool = True, config_dir=None) -> ToolRegistry:
    """Construct a registry pre-populated with Duckln's built-in tools.

    When ``include_handlers=False`` the registry is populated with placeholder
    handlers that simply echo their args — useful for tests that exercise
    registry behaviour without the full dependency tree.

    Plan 178 F5: when ``config_dir`` is given (and handlers are included), the user's
    registered shell/MCP extensions are added as EXECUTABLE specs so a pulled tool/MCP is
    actually callable — under S2 + the approval gate.
    """
    registry = ToolRegistry()
    for spec in _default_tool_specs(echo_only=not include_handlers):
        registry.register(spec)
    if include_handlers and config_dir is not None:
        for spec in _registered_extension_specs(config_dir):
            try:
                registry.register(spec)
            except Exception:
                pass
    return registry


def _echo_handler(args: dict, ctx: AgentContext) -> ToolResult:
    return ToolResult.success({"echoed_args": args, "agent": ctx.agent_name})


def _default_tool_specs(*, echo_only: bool) -> Iterable[ToolSpec]:
    """The 11 initial tools from Plan 65 Phase 1.

    Each handler is a thin wrapper around an existing Duckln primitive. When
    `echo_only=True` we substitute `_echo_handler` so tests don't need the
    full dependency tree.
    """
    h_shell_run = _echo_handler if echo_only else _shell_run_handler
    h_shell_probe = _echo_handler if echo_only else _shell_probe_handler
    h_fs_read = _echo_handler if echo_only else _fs_read_file_handler
    h_fs_list = _echo_handler if echo_only else _fs_list_dir_handler
    h_fs_search = _echo_handler if echo_only else _fs_search_handler
    h_fs_glob = _echo_handler if echo_only else _fs_glob_handler
    h_fs_write = _echo_handler if echo_only else _fs_write_handler
    h_fs_edit = _echo_handler if echo_only else _fs_edit_handler
    h_repo_run = _echo_handler if echo_only else _repo_run_handler
    h_git = _echo_handler if echo_only else _git_handler
    h_app_serve = _echo_handler if echo_only else _app_serve_handler
    h_web_search = _echo_handler if echo_only else _web_search_handler
    h_web_fetch = _echo_handler if echo_only else _web_fetch_handler
    h_state_read = _echo_handler if echo_only else _state_read_handler
    h_state_skill = _echo_handler if echo_only else _state_write_skill_handler
    h_state_failure = _echo_handler if echo_only else _state_write_failure_handler
    h_user_approve = _echo_handler if echo_only else _user_approve_handler
    h_user_clarify = _echo_handler if echo_only else _user_clarify_handler

    return (
        ToolSpec(
            name="shell.run",
            description="Run a shell command on the active execution target. Side effects allowed; safety_class varies by command.",
            args_schema={
                "command": {"type": "str", "required": True},
                "timeout_seconds": {"type": "float", "required": False},
            },
            safety_class=SafetyClass.S2,
            handler=h_shell_run,
            cost=ToolCostHint(typical_latency_ms=1000, network=False, side_effects=True),
        ),
        ToolSpec(
            name="shell.probe",
            description="Run a read-only diagnostic shell command (no side effects). assess_command must classify as S0 or the call fails.",
            args_schema={"command": {"type": "str", "required": True}},
            safety_class=SafetyClass.S0,
            handler=h_shell_probe,
            cost=ToolCostHint(typical_latency_ms=500, network=False, side_effects=False),
        ),
        ToolSpec(
            name="fs.read_file",
            description=(
                "Read a file inside the active project directory (path-traversal guarded). "
                "Returns a line window: pass start_line/end_line to paginate, or focus_line to "
                "center on a crash line; a truncated read is marked with a [SYSTEM WARNING] header "
                "telling you how to fetch the next slice. Never errors on a large file."
            ),
            args_schema={
                "path": {"type": "str", "required": True},
                "max_bytes": {"type": "int", "required": False},
                "start_line": {"type": "int", "required": False},
                "end_line": {"type": "int", "required": False},
                "focus_line": {"type": "int", "required": False},
            },
            safety_class=SafetyClass.S0,
            handler=h_fs_read,
            cost=ToolCostHint(typical_latency_ms=10),
        ),
        ToolSpec(
            name="fs.list_dir",
            description="List entries in a directory inside the active project directory.",
            args_schema={"path": {"type": "str", "required": False}},
            safety_class=SafetyClass.S0,
            handler=h_fs_list,
            cost=ToolCostHint(typical_latency_ms=10),
        ),
        ToolSpec(
            name="fs.search",
            description="Search the repo for a regex pattern across text files (grep-like). Read-only. Returns file:line matches.",
            args_schema={
                "pattern": {"type": "str", "required": True},
                "path": {"type": "str", "required": False},
                "max_results": {"type": "int", "required": False},
            },
            safety_class=SafetyClass.S0,
            handler=h_fs_search,
            cost=ToolCostHint(typical_latency_ms=200),
        ),
        ToolSpec(
            name="fs.glob",
            description="Find files matching a glob (e.g. '**/*.py') inside the repo. Read-only.",
            args_schema={"pattern": {"type": "str", "required": True}, "max_results": {"type": "int", "required": False}},
            safety_class=SafetyClass.S0,
            handler=h_fs_glob,
            cost=ToolCostHint(typical_latency_ms=50),
        ),
        ToolSpec(
            name="fs.write",
            description="Create or overwrite a file inside the repo with the given content. Side effect; needs approval.",
            args_schema={"path": {"type": "str", "required": True}, "content": {"type": "str", "required": True}},
            safety_class=SafetyClass.S2,
            handler=h_fs_write,
            cost=ToolCostHint(typical_latency_ms=20, side_effects=True, requires_approval=True),
        ),
        ToolSpec(
            name="fs.edit",
            description="Replace an exact string in a repo file (old_string→new_string). Side effect; needs approval. old_string must be unique unless replace_all.",
            args_schema={
                "path": {"type": "str", "required": True},
                "old_string": {"type": "str", "required": True},
                "new_string": {"type": "str", "required": True},
                "replace_all": {"type": "bool", "required": False},
            },
            safety_class=SafetyClass.S2,
            handler=h_fs_edit,
            cost=ToolCostHint(typical_latency_ms=20, side_effects=True, requires_approval=True),
        ),
        ToolSpec(
            name="repo.run",
            description="Run a build/test/dev command inside the repo (e.g. 'npm test', 'pytest'). Side effect; safety varies by command.",
            args_schema={"command": {"type": "str", "required": True}, "timeout_seconds": {"type": "float", "required": False}},
            safety_class=SafetyClass.S2,
            handler=h_repo_run,
            cost=ToolCostHint(typical_latency_ms=2000, side_effects=True, requires_approval=True),
        ),
        ToolSpec(
            name="app.serve",
            description="Start the repo's app/server on the active target, wait until it's serving, and open it in the user's browser (works on local/VM/container/cloud; desktop apps stream via noVNC). Side effect; needs approval.",
            args_schema={"command": {"type": "str", "required": True}},
            safety_class=SafetyClass.S2,
            handler=h_app_serve,
            cost=ToolCostHint(typical_latency_ms=5000, side_effects=True, requires_approval=True),
        ),
        ToolSpec(
            name="git.run",
            description="Run a safe git subcommand inside the repo (status/diff/log/branch/add/commit/checkout -b). NEVER push or force; never commit to the default branch without approval.",
            args_schema={"args": {"type": "str", "required": True}},
            safety_class=SafetyClass.S2,
            handler=h_git,
            cost=ToolCostHint(typical_latency_ms=300, side_effects=True, requires_approval=True),
        ),
        ToolSpec(
            name="web.search",
            description="DuckDuckGo search via the internet skill. Results filtered to safe/authoritative hosts when possible.",
            args_schema={
                "query": {"type": "str", "required": True},
                "max_results": {"type": "int", "required": False},
            },
            safety_class=SafetyClass.S0,
            handler=h_web_search,
            cost=ToolCostHint(typical_latency_ms=3000, network=True),
        ),
        ToolSpec(
            name="web.fetch",
            description="Fetch a URL and return a redacted summary. Only allow-listed authoritative hosts (Plan 61).",
            args_schema={"url": {"type": "str", "required": True}},
            safety_class=SafetyClass.S0,
            handler=h_web_fetch,
            cost=ToolCostHint(typical_latency_ms=4000, network=True),
        ),
        ToolSpec(
            name="state.read",
            description="Read Duckln state (config snapshot, followup, workflow). Read-only.",
            args_schema={"key": {"type": "str", "required": False}},
            safety_class=SafetyClass.S0,
            handler=h_state_read,
            cost=ToolCostHint(typical_latency_ms=5),
        ),
        ToolSpec(
            name="state.write_skill",
            description="Persist a skill memory note. Side effect: writes to ~/.duckln/memory/skills/.",
            args_schema={
                "slug": {"type": "str", "required": True},
                "title": {"type": "str", "required": True},
                "summary": {"type": "str", "required": True},
            },
            safety_class=SafetyClass.S1,
            handler=h_state_skill,
            cost=ToolCostHint(typical_latency_ms=20, side_effects=True),
        ),
        ToolSpec(
            name="state.write_failure",
            description="Record a persistent failure memory record (Plan 58/59).",
            args_schema={
                "repo_slug": {"type": "str", "required": True},
                "command": {"type": "str", "required": True},
                "execution_target": {"type": "str", "required": True},
                "exit_code": {"type": "int", "required": True},
                "stderr_fingerprint": {"type": "str", "required": True},
            },
            safety_class=SafetyClass.S1,
            handler=h_state_failure,
            cost=ToolCostHint(typical_latency_ms=20, side_effects=True),
        ),
        ToolSpec(
            name="user.approve",
            description="Ask the user a yes/no question. Returns a structured choice.",
            args_schema={"prompt": {"type": "str", "required": True}},
            safety_class=SafetyClass.S0,
            handler=h_user_approve,
            cost=ToolCostHint(typical_latency_ms=15000, requires_approval=True),
        ),
        ToolSpec(
            name="user.clarify",
            description="Ask the user a multi-choice question (Plan 61 Fix E). Returns the picked option index.",
            args_schema={
                "question": {"type": "str", "required": True},
                "options": {"type": "list", "required": True},
            },
            safety_class=SafetyClass.S0,
            handler=h_user_clarify,
            cost=ToolCostHint(typical_latency_ms=15000, requires_approval=True),
        ),
    )


# --- Real handlers (live wrappers around existing primitives) ----------------


def _shell_run_handler(args: dict, ctx: AgentContext) -> ToolResult:
    from duckln.shell import ControlledCommandRunner
    cmd = str(args["command"])
    timeout = float(args.get("timeout_seconds") or 60.0)
    runner = ControlledCommandRunner(execution_target=ctx.execution_target)
    cwd = str(ctx.project_dir) if ctx.project_dir is not None else None
    result = runner.run(cmd, cwd=cwd, timeout_seconds=timeout)
    return ToolResult.success(
        {
            "exit_code": result.exit_code,
            "stdout": (result.stdout or "")[-4000:],
            "stderr": (result.stderr or "")[-4000:],
            "timed_out": bool(getattr(result, "timed_out", False)),
        }
    )


def _shell_probe_handler(args: dict, ctx: AgentContext) -> ToolResult:
    from duckln.safety import assess_command
    cmd = str(args["command"])
    assessment = assess_command(cmd, execution_target=ctx.execution_target)
    if assessment.safety_class != SafetyClass.S0:
        return ToolResult.failure(
            "not_a_probe",
            f"shell.probe accepts only S0 (read-only) commands; got {assessment.safety_class.value}.",
        )
    return _shell_run_handler({"command": cmd, "timeout_seconds": 10.0}, ctx)


def _resolve_safe_project_path(raw_path: str, project_dir: Path | None) -> Path | None:
    """Resolve a path inside `project_dir` with traversal guard. Returns None
    when the resolved path escapes the project root."""
    if project_dir is None:
        return None
    try:
        # Plan 147 F1: EXPAND a leading `~` first — `.resolve()` does NOT expand it, so a
        # `~/.duckln/...` project_dir (a local target with a home-relative clone path) would
        # otherwise resolve to a bogus literal-`~` dir and every read would fail (blindness).
        project_dir = Path(os.path.expanduser(str(project_dir)))
        candidate = (project_dir / os.path.expanduser(raw_path)).resolve()
        project_root = project_dir.resolve()
        candidate.relative_to(project_root)
        return candidate
    except (OSError, ValueError):
        return None


# --- Plan 97: environment-agnostic file ops (run on vm/container/cloud) --------
# Plan 155 F1/F6: canonical set + normalization (maps the `docker` alias → `container`).
from duckln.execution_targets import FS_REMOTE_TARGETS as _FS_REMOTE_TARGETS, is_fs_remote_target


def _is_remote(ctx: AgentContext) -> bool:
    return is_fs_remote_target(ctx.execution_target)


def _remote_repo_dir(ctx: AgentContext) -> str | None:
    return str(ctx.project_dir) if ctx.project_dir is not None else None


def _remote_safe_join(repo_dir: str, raw: str) -> str | None:
    """Confine `raw` inside the REMOTE repo dir (string posix-path guard). Returns
    the absolute remote path, or None if it escapes the repo root."""
    import posixpath

    base = (repo_dir or "").rstrip("/")
    if not base:
        return None
    candidate = posixpath.normpath(posixpath.join(base, raw))
    if candidate == base or candidate.startswith(base + "/"):
        return candidate
    return None


def _run_on_target(ctx: AgentContext, command: str):
    """Run a shell command on the active target (vm/container/cloud) via the canonical
    multi-target wrapper, executed by a local passthrough runner (no double-wrap).
    Returns the runner result or None when the target isn't resolvable."""
    from duckln.repo_bringup import _wrap_command_for_execution_target
    from duckln.shell import ControlledCommandRunner

    vm_name = ctx.extra.get("vm_name") if isinstance(ctx.extra, dict) else None
    wrapped, _meta = _wrap_command_for_execution_target(
        config_dir=Path(ctx.config_dir), execution_target=ctx.execution_target,
        command=command, cwd=None, preferred_vm_name=vm_name,
    )
    if wrapped is None:
        return None
    try:
        return ControlledCommandRunner(execution_target="local").run(wrapped, timeout_seconds=60.0)
    except Exception:
        return None


def _shq(s: str) -> str:
    import shlex as _shlex
    return _shlex.quote(s)


def _remote_path_expr(path: str) -> str:
    """Plan 147 F1 (keystone): render a repo PATH for a remote shell so a leading `~` EXPANDS
    (mirrors the executor's wrapper at repo_bringup.py:1207). `~/.duckln/x` →
    `"${HOME}"'/.duckln/x'` — bash expands `${HOME}` inside `bash -lc`, the rest stays safely
    single-quoted. A non-`~` path is shell-quoted as usual. WITHOUT this the fs tools quoted
    the `~` literally (`'~/.duckln/...'`), so it never expanded on the VM and EVERY read failed
    → the agent went blind and could gather no evidence to reason from."""
    p = path or ""
    if p == "~" or p.startswith("~/"):
        return '"${HOME}"' + _shq(p[1:])  # ${HOME} expands; the rest (incl. leading /) is quoted
    return _shq(p)


def _remote_read_command(abs_path: str, max_bytes: int) -> str:
    return f"head -c {int(max_bytes)} -- {_remote_path_expr(abs_path)}"


def _remote_list_command(abs_path: str) -> str:
    return f"ls -1A -- {_remote_path_expr(abs_path)}"


def _remote_search_command(pattern: str, abs_dir: str, max_results: int) -> str:
    # -rnI: recurse, line numbers, skip binary. Skip-dirs filtered on the host side.
    return f"grep -rnI -e {_shq(pattern)} -- {_remote_path_expr(abs_dir)} 2>/dev/null | head -n {int(max_results)}"


def _remote_glob_command(pattern: str, abs_dir: str, max_results: int) -> str:
    # Reuse Python's glob semantics on the target (Ubuntu/most images have python3).
    py = (
        "import glob,os,sys; "
        "base=sys.argv[1]; pat=sys.argv[2]; "
        "[print(os.path.relpath(p,base)) for p in glob.glob(os.path.join(base,pat),recursive=True)]"
    )
    return f"python3 -c {_shq(py)} {_remote_path_expr(abs_dir)} {_shq(pattern)} 2>/dev/null | head -n {int(max_results)}"


def _remote_write_command(abs_path: str, content: str) -> str:
    import base64
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    parent = abs_path.rsplit("/", 1)[0] or "/"
    return (
        f"mkdir -p {_remote_path_expr(parent)} && printf %s {_shq(b64)} | base64 -d > {_remote_path_expr(abs_path)} "
        f"&& echo DUCKLN_WROTE:$(wc -c < {_remote_path_expr(abs_path)})"
    )


def _remote_fs(op: str, args: dict, ctx: AgentContext) -> ToolResult:
    """Plan 97: execute a file op against the active REMOTE target's repo clone,
    confined to the repo dir. Mirrors the local handlers' semantics."""
    repo_dir = _remote_repo_dir(ctx)
    if not repo_dir:
        return ToolResult.failure("no_project_dir", "No remote repo directory resolved for this target.")

    if op in ("read", "list", "write", "edit"):
        abs_path = _remote_safe_join(repo_dir, str(args.get("path") or "."))
        if abs_path is None:
            return ToolResult.failure("path_escape", f"Path '{args.get('path')}' is outside the repo directory.")

    if op == "read":
        # Plan 179 A1: fetch enough bytes to slice by LINES (the local default was 64 KB).
        res = _run_on_target(ctx, _remote_read_command(abs_path, int(args.get("max_bytes") or 512 * 1024)))
        if res is None:
            return ToolResult.failure("target_unreachable", "Could not run on the target.")
        if (getattr(res, "exit_code", 1) or 0) != 0:
            return ToolResult.failure("not_found", f"File not found on target: {args.get('path')}")
        _name = str(args.get("path") or "")
        payload = _window_text(
            getattr(res, "stdout", "") or "", ceiling=_read_ceiling_for(_name), ctx=ctx,
            start_line=args.get("start_line"), end_line=args.get("end_line"), focus_line=args.get("focus_line"),
        )
        if _should_offload_window(payload):
            off = _maybe_offload(payload["text"], ctx, label=f"read-{_name.rsplit('/',1)[-1]}")
            off["lines_total"] = payload.get("lines_total")
            payload = off
        return ToolResult.success(payload)

    if op == "list":
        res = _run_on_target(ctx, _remote_list_command(abs_path))
        if res is None or (getattr(res, "exit_code", 1) or 0) != 0:
            return ToolResult.failure("not_found", f"Directory not found on target: {args.get('path')}")
        entries = [e for e in (getattr(res, "stdout", "") or "").splitlines() if e][:200]
        return ToolResult.success({"entries": entries, "truncated": len(entries) >= 200})

    if op == "search":
        max_results = int(args.get("max_results") or 100)
        base = _remote_safe_join(repo_dir, str(args.get("path") or "."))
        if base is None:
            return ToolResult.failure("path_escape", "search path escapes the repo directory.")
        res = _run_on_target(ctx, _remote_search_command(str(args["pattern"]), base, max_results))
        if res is None:
            return ToolResult.failure("target_unreachable", "Could not run on the target.")
        lines = [
            ln for ln in (getattr(res, "stdout", "") or "").splitlines()
            if not any(f"/{d}/" in ln for d in _FS_SEARCH_SKIP_DIRS)
        ][:max_results]
        # make matches repo-relative
        rel = [ln[len(repo_dir) + 1:] if ln.startswith(repo_dir + "/") else ln for ln in lines]
        payload = _maybe_offload("\n".join(rel), ctx, label="search")
        payload.update({"match_count": len(rel), "truncated_results": len(rel) >= max_results})
        return ToolResult.success(payload)

    if op == "glob":
        max_results = int(args.get("max_results") or 200)
        res = _run_on_target(ctx, _remote_glob_command(str(args["pattern"]), repo_dir, max_results))
        if res is None:
            return ToolResult.failure("target_unreachable", "Could not run on the target.")
        hits = [h for h in (getattr(res, "stdout", "") or "").splitlines() if h and not any(p in h.split("/") for p in _FS_SEARCH_SKIP_DIRS)][:max_results]
        return ToolResult.success({"matches": hits, "truncated": len(hits) >= max_results})

    if op == "write":
        res = _run_on_target(ctx, _remote_write_command(abs_path, str(args["content"])))
        if res is None or (getattr(res, "exit_code", 1) or 0) != 0 or "DUCKLN_WROTE:" not in (getattr(res, "stdout", "") or ""):
            return ToolResult.failure("io_error", f"Could not write {args.get('path')} on the target.")
        return ToolResult.success({"path": args.get("path"), "bytes_written": len(str(args["content"]))})

    if op == "edit":
        read_res = _run_on_target(ctx, f"cat -- {_remote_path_expr(abs_path)}")
        if read_res is None or (getattr(read_res, "exit_code", 1) or 0) != 0:
            return ToolResult.failure("not_found", f"File not found on target: {args.get('path')}")
        text = getattr(read_res, "stdout", "") or ""
        old, new = str(args["old_string"]), str(args["new_string"])
        count = text.count(old)
        if count == 0:
            return ToolResult.failure("no_match", f"old_string not found in {args.get('path')}.")
        if count > 1 and not bool(args.get("replace_all", False)):
            return ToolResult.failure("not_unique", f"old_string occurs {count}× in {args.get('path')}; pass replace_all.")
        updated = text.replace(old, new) if args.get("replace_all") else text.replace(old, new, 1)
        write_res = _run_on_target(ctx, _remote_write_command(abs_path, updated))
        if write_res is None or "DUCKLN_WROTE:" not in (getattr(write_res, "stdout", "") or ""):
            return ToolResult.failure("io_error", f"Could not write edit to {args.get('path')} on the target.")
        return ToolResult.success({"path": args.get("path"), "replacements": count if args.get("replace_all") else 1})

    return ToolResult.failure("unknown_op", op)


def _fs_read_file_handler(args: dict, ctx: AgentContext) -> ToolResult:
    if _is_remote(ctx):
        return _remote_fs("read", args, ctx)
    raw = str(args["path"])
    # Plan 179 A1: a generous byte cap so we can slice by LINES; never raise on a big file.
    max_bytes = int(args.get("max_bytes") or 2 * 1024 * 1024)
    safe = _resolve_safe_project_path(raw, ctx.project_dir)
    if safe is None:
        return ToolResult.failure("path_escape", f"Path '{raw}' is outside project directory.")
    if not safe.exists() or not safe.is_file():
        return ToolResult.failure("not_found", f"File not found: {raw}")
    try:
        data = safe.read_bytes()[:max_bytes]
    except OSError as exc:
        return ToolResult.failure("io_error", str(exc))
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return ToolResult.success({"text": data.decode("utf-8", errors="replace"), "bytes_read": len(data), "binary": True})
    # Plan 179 A1-A4: file-type line ceiling + pagination + center-on-crash + truncation marker.
    payload = _window_text(
        text, ceiling=_read_ceiling_for(raw), ctx=ctx,
        start_line=args.get("start_line"), end_line=args.get("end_line"), focus_line=args.get("focus_line"),
    )
    # A pathological huge / non-line-windowable blob still spills to a file (Plan 92).
    if _should_offload_window(payload):
        off = _maybe_offload(payload["text"], ctx, label=f"read-{Path(raw).name}")
        off["lines_total"] = payload.get("lines_total")
        payload = off
    payload["bytes_read"] = len(data)
    return ToolResult.success(payload)


def _fs_list_dir_handler(args: dict, ctx: AgentContext) -> ToolResult:
    if _is_remote(ctx):
        return _remote_fs("list", args, ctx)
    raw = str(args.get("path") or ".")
    safe = _resolve_safe_project_path(raw, ctx.project_dir)
    if safe is None:
        return ToolResult.failure("path_escape", f"Path '{raw}' is outside project directory.")
    if not safe.exists() or not safe.is_dir():
        return ToolResult.failure("not_found", f"Directory not found: {raw}")
    try:
        entries = sorted(p.name for p in safe.iterdir())[:200]
    except OSError as exc:
        return ToolResult.failure("io_error", str(exc))
    return ToolResult.success({"entries": entries, "truncated": len(entries) >= 200})


# --- Plan 92: offload-large-results-to-file (avoid context overflow) ----------

_OFFLOAD_THRESHOLD_CHARS = 8000


def _maybe_offload(text: str, ctx: AgentContext, *, label: str) -> dict:
    """When a result is too large for the context window, spill it to a file under
    the workspace results dir and return a path + head/tail summary instead of the
    full text. Small results pass through as ``{"text": ...}``."""
    text = text or ""
    if len(text) <= _OFFLOAD_THRESHOLD_CHARS:
        return {"text": text}
    try:
        import time as _t
        results_dir = Path(ctx.config_dir) / "workspace" / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        slug = "".join(c if c.isalnum() else "-" for c in label).strip("-") or "result"
        out = results_dir / f"{int(_t.time())}-{slug}.txt"
        out.write_text(text, encoding="utf-8")
        return {
            "offloaded_to": str(out),
            "bytes": len(text),
            "head": text[:1500],
            "tail": text[-500:],
            "note": "Result was large and saved to a file; read it with fs.read_file if you need more.",
        }
    except OSError:
        return {"text": text[:_OFFLOAD_THRESHOLD_CHARS], "truncated": True}


# --- Plan 179 Part A: Smart file-read windows --------------------------------
# A file is read as a LINE window sized by its type (so a 2000-line source file doesn't
# overflow the context and never raises). A truncated read carries a [SYSTEM WARNING] marker
# telling the model how to fetch the next slice; for a LOCAL model a strict "don't guess from a
# truncated snippet" footer is appended (the Runtime Capability Adapter, Plan 179 B).

_CODE_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs", ".go", ".rs", ".c", ".cc", ".cpp",
    ".cxx", ".h", ".hpp", ".java", ".rb", ".php", ".cs", ".swift", ".kt", ".scala", ".sh",
    ".lua", ".dart", ".m", ".mm", ".vue", ".svelte",
}
_DOC_MANIFEST_EXTS = {".md", ".cfg", ".ini", ".toml", ".yaml", ".yml", ".lock", ".json"}
_MANIFEST_NAMES = {
    "package.json", "requirements.txt", "pyproject.toml", "cargo.toml", "go.mod", "go.sum",
    "readme.md", "readme", "claude.md", "agents.md", "dockerfile", "makefile", "gemfile",
    "build.gradle", "pom.xml", "tsconfig.json", ".env.example",
}
_CODE_CEILING = 600
_LOG_CEILING = 150
_MANIFEST_CEILING = 150
_DEFAULT_CEILING = 400
_WINDOW_OFFLOAD_CHARS = 64_000  # a pathological single-line/minified blob still offloads


def _should_offload_window(payload: dict) -> bool:
    """A returned window spills to a file when it's genuinely huge OR when it's a non-line-
    windowable blob (very few lines but large — a minified/single-line file the line ceilings
    can't slice). A real multi-line source window stays inline."""
    text = payload.get("text", "") or ""
    if len(text) > _WINDOW_OFFLOAD_CHARS:
        return True
    return payload.get("lines_total", 0) <= 5 and len(text) > _OFFLOAD_THRESHOLD_CHARS


def _read_ceiling_for(path: str) -> int:
    """The default LINE ceiling for a read, by file type (spec §4-A): code 600, logs 150,
    manifests/docs 150, otherwise a moderate default."""
    name = (str(path).rsplit("/", 1)[-1] or "").lower()
    ext = ("." + name.rsplit(".", 1)[-1]) if "." in name else ""
    if ext in {".log", ".out", ".err"} or name.endswith(".txt"):
        return _LOG_CEILING
    if name in _MANIFEST_NAMES or ext in _DOC_MANIFEST_EXTS:
        return _MANIFEST_CEILING
    if ext in _CODE_EXTS:
        return _CODE_CEILING
    return _DEFAULT_CEILING


def _local_read_directive(ctx: "AgentContext") -> str:
    """The strict 'don't guess from a truncated snippet' footer — appended ONLY for a LOCAL
    model class (the Plan-179-B adapter). Cloud-reasoning models follow up on their own."""
    try:
        from state.access import read_config_snapshot
        from duckln.ai_client import LOCAL_MODEL, model_capability_class

        model = str(read_config_snapshot(ctx.config_dir).get("model") or "")
        if model and model_capability_class(model, config_dir=ctx.config_dir) == LOCAL_MODEL:
            return (
                "\n[CRITICAL LOCAL DIRECTIVE: you are looking at a truncated snippet — do NOT "
                "guess a patch if the target function/variable definition is cut off; call "
                "fs.read_file for the next line range first.]"
            )
    except Exception:
        pass
    return ""


def _window_text(
    text: str,
    *,
    ceiling: int,
    ctx: "AgentContext",
    start_line=None,
    end_line=None,
    focus_line=None,
) -> dict:
    """Return a line-window payload. When the window covers the whole file → the full text.
    When truncated → a [SYSTEM WARNING] marker + the slice (+ the local 'don't guess' footer),
    plus structured `start_line`/`end_line`/`lines_total` so the model can request the next slice."""
    lines = (text or "").splitlines()
    total = len(lines)
    if total == 0:
        return {"text": text or "", "lines_total": 0}

    def _int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    sl, el, fl = _int(start_line), _int(end_line), _int(focus_line)
    if sl is not None or el is not None:
        lo = max(1, sl or 1)
        hi = el if el is not None else (lo + ceiling - 1)
    elif fl is not None:
        half = max(1, ceiling // 2)
        lo, hi = fl - half, fl + half
    else:
        lo, hi = 1, ceiling
    lo = max(1, min(lo, total))
    hi = max(lo, min(hi, total))
    if hi - lo + 1 > ceiling:  # never exceed the ceiling even with an explicit range
        hi = lo + ceiling - 1
        hi = min(hi, total)
    if lo == 1 and hi >= total:
        # Whole file fits the window → return the ORIGINAL text untouched (preserve trailing
        # newline / exact bytes; don't lose fidelity to splitlines+rejoin).
        return {"text": text, "lines_total": total, "truncated": False}
    sliced = "\n".join(lines[lo - 1:hi])
    focus_note = f" The targeted line {fl} is centered." if fl is not None else ""
    marker = (
        f"[SYSTEM WARNING: file content truncated for efficiency. Showing lines {lo}-{hi} of "
        f"{total} total.{focus_note} If what you need isn't in this block, call fs.read_file again "
        f"with start_line={hi + 1} (or pass a focus_line) to read the next slice.]\n"
    )
    return {
        "text": marker + sliced + _local_read_directive(ctx),
        "lines_total": total,
        "start_line": lo,
        "end_line": hi,
        "truncated": True,
    }


# --- Plan 92: search / glob / write / edit (repo-confined) --------------------

_FS_SEARCH_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "target", "dist", "build", ".next"}


def _fs_search_handler(args: dict, ctx: AgentContext) -> ToolResult:
    if _is_remote(ctx):
        return _remote_fs("search", args, ctx)
    import re as _re
    pattern = str(args["pattern"])
    base_raw = str(args.get("path") or ".")
    max_results = int(args.get("max_results") or 100)
    base = _resolve_safe_project_path(base_raw, ctx.project_dir)
    if base is None:
        return ToolResult.failure("path_escape", f"Path '{base_raw}' is outside project directory.")
    try:
        rx = _re.compile(pattern)
    except _re.error as exc:
        return ToolResult.failure("bad_pattern", f"Invalid regex: {exc}")
    matches: list[str] = []
    roots = [base] if base.is_dir() else [base.parent]
    files_scanned = 0
    for root in roots:
        for path in sorted(root.rglob("*")):
            if len(matches) >= max_results or files_scanned >= 4000:
                break
            if not path.is_file() or any(part in _FS_SEARCH_SKIP_DIRS for part in path.parts):
                continue
            try:
                if path.stat().st_size > 1_000_000:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            files_scanned += 1
            for lineno, line in enumerate(text.splitlines(), start=1):
                if rx.search(line):
                    rel = path.relative_to(ctx.project_dir.resolve()) if ctx.project_dir else path
                    matches.append(f"{rel}:{lineno}: {line.strip()[:200]}")
                    if len(matches) >= max_results:
                        break
    payload = _maybe_offload("\n".join(matches), ctx, label=f"search-{pattern}")
    payload.update({"match_count": len(matches), "truncated_results": len(matches) >= max_results})
    return ToolResult.success(payload)


def _fs_glob_handler(args: dict, ctx: AgentContext) -> ToolResult:
    if _is_remote(ctx):
        return _remote_fs("glob", args, ctx)
    pattern = str(args["pattern"])
    max_results = int(args.get("max_results") or 200)
    if ctx.project_dir is None:
        return ToolResult.failure("no_project_dir", "No active project directory.")
    root = ctx.project_dir.resolve()
    try:
        hits = []
        for p in sorted(root.glob(pattern)):
            if any(part in _FS_SEARCH_SKIP_DIRS for part in p.parts):
                continue
            try:
                p.resolve().relative_to(root)
            except ValueError:
                continue
            hits.append(str(p.relative_to(root)))
            if len(hits) >= max_results:
                break
    except (OSError, ValueError) as exc:
        return ToolResult.failure("glob_error", str(exc))
    return ToolResult.success({"matches": hits, "truncated": len(hits) >= max_results})


def _edit_diff_summary(old_text: str, new_text: str, *, path: str, max_diff_lines: int = 80) -> dict:
    """Plan 133 F7b: a Claude-style edit summary — added/removed line counts plus a
    bounded unified-diff snippet — so the UI can render an expandable 'Edit … +N -M' card."""
    import difflib

    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm="", n=2))
    added = sum(1 for d in diff if d.startswith("+") and not d.startswith("+++"))
    removed = sum(1 for d in diff if d.startswith("-") and not d.startswith("---"))
    body = diff[2:] if len(diff) > 2 else diff  # drop the ---/+++ file headers
    if len(body) > max_diff_lines:
        body = body[:max_diff_lines] + [f"… (+{len(body) - max_diff_lines} more diff lines)"]
    return {"added": added, "removed": removed, "diff": "\n".join(body)}


def _fs_write_handler(args: dict, ctx: AgentContext) -> ToolResult:
    if _is_remote(ctx):
        return _remote_fs("write", args, ctx)
    raw = str(args["path"])
    content = str(args["content"])
    safe = _resolve_safe_project_path(raw, ctx.project_dir)
    if safe is None:
        return ToolResult.failure("path_escape", f"Path '{raw}' is outside project directory.")
    try:
        safe.parent.mkdir(parents=True, exist_ok=True)
        existed = safe.exists()
        before = safe.read_text(encoding="utf-8") if existed else ""
        safe.write_text(content, encoding="utf-8")
    except OSError as exc:
        return ToolResult.failure("io_error", str(exc))
    summary = _edit_diff_summary(before, content, path=raw)
    return ToolResult.success({
        "path": raw, "bytes_written": len(content), "created": not existed,
        "added": summary["added"], "removed": summary["removed"], "diff": summary["diff"],
    })


def _fs_edit_handler(args: dict, ctx: AgentContext) -> ToolResult:
    if _is_remote(ctx):
        return _remote_fs("edit", args, ctx)
    raw = str(args["path"])
    old = str(args["old_string"])
    new = str(args["new_string"])
    replace_all = bool(args.get("replace_all", False))
    safe = _resolve_safe_project_path(raw, ctx.project_dir)
    if safe is None:
        return ToolResult.failure("path_escape", f"Path '{raw}' is outside project directory.")
    if not safe.exists() or not safe.is_file():
        return ToolResult.failure("not_found", f"File not found: {raw}")
    try:
        text = safe.read_text(encoding="utf-8")
    except OSError as exc:
        return ToolResult.failure("io_error", str(exc))
    count = text.count(old)
    if count == 0:
        return ToolResult.failure("no_match", f"old_string not found in {raw}.")
    if count > 1 and not replace_all:
        return ToolResult.failure("not_unique", f"old_string occurs {count}× in {raw}; pass replace_all or add more context.")
    updated = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    try:
        safe.write_text(updated, encoding="utf-8")
    except OSError as exc:
        return ToolResult.failure("io_error", str(exc))
    summary = _edit_diff_summary(text, updated, path=raw)
    return ToolResult.success({
        "path": raw, "replacements": count if replace_all else 1,
        "added": summary["added"], "removed": summary["removed"], "diff": summary["diff"],
    })


def _repo_run_handler(args: dict, ctx: AgentContext) -> ToolResult:
    from duckln.shell import ControlledCommandRunner
    cmd = str(args["command"])
    timeout = float(args.get("timeout_seconds") or 600.0)
    runner = ControlledCommandRunner(execution_target=ctx.execution_target)
    cwd = str(ctx.project_dir) if ctx.project_dir is not None else None
    result = runner.run(cmd, cwd=cwd, timeout_seconds=timeout)
    out = _maybe_offload((result.stdout or "") + (("\n--- stderr ---\n" + result.stderr) if result.stderr else ""), ctx, label="run")
    out.update({"exit_code": result.exit_code, "timed_out": bool(getattr(result, "timed_out", False))})
    return ToolResult.success(out)


_GIT_BLOCKED = ("push", "reset --hard", "clean -", "filter-branch", "--force", "-f ", "remote set-url")


def _git_handler(args: dict, ctx: AgentContext) -> ToolResult:
    from duckln.shell import ControlledCommandRunner
    git_args = str(args["args"]).strip()
    low = git_args.lower()
    if any(b in low for b in _GIT_BLOCKED):
        return ToolResult.failure("git_blocked", f"Refusing unsafe git operation: `git {git_args}`.")
    runner = ControlledCommandRunner(execution_target=ctx.execution_target)
    cwd = str(ctx.project_dir) if ctx.project_dir is not None else None
    # Plan 102: never commit to the default branch — require a feature branch.
    if low.startswith("commit"):
        try:
            br = runner.run("git rev-parse --abbrev-ref HEAD", cwd=cwd, timeout_seconds=15.0)
            branch = (getattr(br, "stdout", "") or "").strip()
        except Exception:
            branch = ""
        if branch in ("main", "master", "HEAD", ""):
            return ToolResult.failure(
                "git_default_branch",
                f"Refusing to commit on '{branch or 'the default branch'}'. Create a feature branch first: "
                "`git.run` with `checkout -b duckln/<change>`.",
            )
    result = runner.run(f"git {git_args}", cwd=cwd, timeout_seconds=60.0)
    return ToolResult.success({
        "exit_code": result.exit_code,
        "stdout": (result.stdout or "")[-4000:],
        "stderr": (result.stderr or "")[-2000:],
    })


def _app_serve_handler(args: dict, ctx: AgentContext) -> ToolResult:
    """Plan 98: run the app on the active target and open it in the host browser."""
    from types import SimpleNamespace

    from duckln.repo_bringup import _launch_and_await_server, expose_app_and_open
    from duckln.shell import ControlledCommandRunner

    cmd = str(args["command"])
    disp = ctx.display or (lambda _m: None)
    vm_name = ctx.extra.get("vm_name") if isinstance(ctx.extra, dict) else None
    cloud_key = ctx.extra.get("cloud_resource_key") if isinstance(ctx.extra, dict) else None
    # Local passthrough runner: _launch_and_await_server wraps for the target itself.
    runner = ControlledCommandRunner(execution_target="local")
    try:
        status, served, log_tail = _launch_and_await_server(
            runner=runner, raw_command=cmd,
            project_cwd=str(ctx.project_dir) if ctx.project_dir is not None else None,
            execution_target=ctx.execution_target, vm_name=vm_name,
            config_dir=Path(ctx.config_dir), repo=SimpleNamespace(name=ctx.agent_name),
            display=disp, think=lambda _t: None,
        )
    except Exception as exc:
        return ToolResult.failure("serve_error", f"{type(exc).__name__}: {exc}")
    if status == "dead":
        # Plan 110: surface the REAL error so the agent/diagnosis can act on it.
        return ToolResult.failure("app_exited", (log_tail or "")[-2000:] or "The app exited before it started serving.")
    if not served:
        return ToolResult.success({"status": status, "served_url": None, "note": "started but no URL confirmed yet; it may still be booting."})
    opened, note = expose_app_and_open(
        served, execution_target=ctx.execution_target, vm_name=vm_name,
        config_dir=Path(ctx.config_dir), cloud_resource_key=cloud_key, display=disp,
    )
    return ToolResult.success({"status": status, "served_url": served, "opened_url": opened, "note": note})


def _web_search_handler(args: dict, ctx: AgentContext) -> ToolResult:
    try:
        from duckln.internet_skill import internet_search_summary
    except Exception as exc:
        return ToolResult.failure("import_error", str(exc))
    query = str(args["query"])
    max_results = int(args.get("max_results") or 5)
    try:
        # Plan 148 F1: `config_dir` is a REQUIRED keyword-only arg. It was MISSING, so every
        # call raised TypeError and `web.search` ALWAYS failed → Duckln could never search.
        results = internet_search_summary(
            query=query, config_dir=Path(ctx.config_dir), max_results=max_results, timeout_seconds=5.0
        )
    except Exception as exc:
        return ToolResult.failure("search_error", str(exc))
    # `internet_search_summary` returns () when the /internet toggle is OFF — say so plainly
    # (it's an actionable state, not a crash) so the agent can ask the user to enable it.
    if not results:
        return ToolResult.success({
            "summary": "(no results — the internet skill is off or the search returned nothing; "
                       "run `/internet on` to enable web search)",
            "results": [], "query": query, "result_count": 0,
        })
    # Format the result tuple into a readable, citable block (title — url + snippet) so the
    # model can reason over it AND knows which URL to web.fetch for full detail (F6).
    lines, items = [], []
    for i, r in enumerate(results, start=1):
        title = (getattr(r, "title", "") or "").strip()
        url = (getattr(r, "url", "") or "").strip()
        snippet = (getattr(r, "snippet", "") or "").strip()
        lines.append(f"{i}. {title} — {url}\n   {snippet}")
        items.append({"title": title, "url": url, "snippet": snippet})
    return ToolResult.success({"summary": "\n".join(lines), "results": items,
                               "query": query, "result_count": len(items)})


def _web_fetch_handler(args: dict, ctx: AgentContext) -> ToolResult:
    try:
        from duckln.web_runtime import fetch_web_reference_summary
        from duckln.authoritative_sources import is_authoritative_url
    except Exception as exc:
        return ToolResult.failure("import_error", str(exc))
    url = str(args["url"])
    if not is_authoritative_url(url):
        return ToolResult.failure(
            "non_authoritative",
            "URL is not in the authoritative-publishers allow-list (Plan 61).",
        )
    try:
        excerpt = fetch_web_reference_summary(url=url, timeout_seconds=4.0)
    except Exception as exc:
        return ToolResult.failure("fetch_error", str(exc))
    excerpt = excerpt or ""
    # Plan 181: distill a LARGE page into a compact structured digest (token win) via the
    # WEB_READER model — strictly ADDITIVE: only a high-confidence, relevant digest replaces the
    # raw excerpt in the payload; otherwise (small page / no reader model / low confidence / error)
    # the raw excerpt is returned unchanged, so the fix is never summarized away.
    try:
        from duckln.web_runtime import read_web_page

        digest = read_web_page(
            excerpt=excerpt, url=url, config_dir=ctx.config_dir,
            context=str((ctx.extra or {}).get("question") or ""),
        )
        if digest and digest.get("relevant") and digest.get("confidence") == "high" and (
            digest.get("fix_commands") or digest.get("key_excerpt")
        ):
            return ToolResult.success(
                {"url": url, "digest": digest, "excerpt": digest.get("key_excerpt") or excerpt[:1500]}
            )
    except Exception:
        pass
    return ToolResult.success({"excerpt": excerpt, "url": url})


def _state_read_handler(args: dict, ctx: AgentContext) -> ToolResult:
    try:
        from state.access import read_config_snapshot, read_followup_state, read_workflow_state
    except Exception as exc:
        return ToolResult.failure("import_error", str(exc))
    key = str(args.get("key") or "")
    snapshot = {
        "config": read_config_snapshot(ctx.config_dir),
        "followup": read_followup_state(ctx.config_dir),
        "workflow": read_workflow_state(ctx.config_dir),
    }
    if key in snapshot:
        return ToolResult.success(snapshot[key])
    return ToolResult.success(snapshot)


def _state_write_skill_handler(args: dict, ctx: AgentContext) -> ToolResult:
    try:
        from state.access import write_skill_memory_state
    except Exception as exc:
        return ToolResult.failure("import_error", str(exc))
    try:
        path = write_skill_memory_state(
            ctx.config_dir,
            slug=str(args["slug"]),
            title=str(args["title"]),
            summary=str(args["summary"]),
        )
    except Exception as exc:
        return ToolResult.failure("write_error", str(exc))
    return ToolResult.success({"path": str(path)})


def _state_write_failure_handler(args: dict, ctx: AgentContext) -> ToolResult:
    try:
        from state.access import write_failure_memory_state
    except Exception as exc:
        return ToolResult.failure("import_error", str(exc))
    try:
        path = write_failure_memory_state(
            ctx.config_dir,
            repo_slug=str(args["repo_slug"]),
            command=str(args["command"]),
            execution_target=str(args["execution_target"]),
            exit_code=int(args["exit_code"]),
            stderr_fingerprint=str(args["stderr_fingerprint"]),
        )
    except Exception as exc:
        return ToolResult.failure("write_error", str(exc))
    return ToolResult.success({"path": str(path)})


def _user_approve_handler(args: dict, ctx: AgentContext) -> ToolResult:
    if ctx.approve is None:
        return ToolResult.failure("no_approve_callback", "AgentContext has no approve callback.")
    decision = bool(ctx.approve(str(args["prompt"])))
    return ToolResult.success({"approved": decision})


def _user_clarify_handler(args: dict, ctx: AgentContext) -> ToolResult:
    try:
        from duckln.clarify_prompts import resolve_clarify_prompt
    except Exception as exc:
        return ToolResult.failure("import_error", str(exc))
    options = tuple(str(o) for o in args.get("options") or ())
    if not options:
        return ToolResult.failure("schema_violation", "user.clarify requires non-empty options list.")
    prompt = resolve_clarify_prompt(chat=ctx.extra.get("chat"))
    chosen = prompt(str(args["question"]), options)
    return ToolResult.success({"chosen_index": chosen})
