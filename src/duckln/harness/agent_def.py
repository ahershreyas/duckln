"""Plan 65 Phase 2 / Plan 157 — Agent Definition Schema (spec-driven harness).

Agents are DATA, not code. Each spec lives in
``src/duckln/harness/agents/<name>.md`` with YAML frontmatter for the
structured fields and the markdown body as the system prompt.

Plan 157: the frontmatter is parsed with PyYAML (`yaml.safe_load`) so the
reference format (folded `>` scalars, nested `input_contract`/`output_contract`)
parses correctly. Specs declare a `version`; a `version` newer than
``SUPPORTED_SPEC_VERSION`` is a HARD error (a newer spec on an older harness
would misbehave silently). The registry loads robustly: a malformed OPTIONAL
spec is skipped + logged, but a missing REQUIRED agent raises.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_LOG = logging.getLogger("duckln.harness.agent_def")

# Plan 157 P1-4: the spec schema version this harness understands. A spec that
# declares a HIGHER version is rejected at load time (don't run a newer contract
# on an older executor). Bump this when the spec schema changes.
SUPPORTED_SPEC_VERSION = "2.0"

# Plan 157 P1-3: agents that MUST be present for the harness to function. A
# malformed optional spec is skipped + logged; a missing required agent raises.
REQUIRED_AGENTS: frozenset[str] = frozenset(
    {"planner", "critic", "clarifier", "supervisor", "attributor", "recovery_agent"}
)


# --- AgentDefinition dataclass -----------------------------------------------


@dataclass(frozen=True)
class AgentDefinition:
    """Declarative spec for a harness agent."""

    name: str
    role: str
    system_prompt: str
    tools: tuple[str, ...]
    max_turns: int = 8
    budget_seconds: float = 300.0
    budget_llm_calls: int = 8
    can_spawn: tuple[str, ...] = ()
    is_coordinator: bool = False
    source_path: Path | None = None
    # Plan 157 P1-2: spec-driven contract fields.
    version: str = ""
    input_contract: dict[str, Any] = field(default_factory=dict)
    output_contract: dict[str, Any] = field(default_factory=dict)
    # Plan 157 P1-2/P2-2: spec-declared bounded re-ask limit for output-contract
    # validation. Per-agent so a latency-sensitive spec can declare 1.
    max_contract_retries: int = 2
    # Plan 187 F4: optional per-verdict budget tiers ({"capable": {...}, "weak": {...}}),
    # selected at run time from the Plan-156 capability probe verdict (observed behavior,
    # not model name). Empty = always use the flat default fields above. Only turns/seconds/
    # calls/retries ever flex — the safety floor (tools/scope/contract) is identical.
    budget_profiles: dict[str, dict] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("AgentDefinition.name cannot be empty")
        if not self.role:
            raise ValueError("AgentDefinition.role cannot be empty")
        if not self.system_prompt.strip():
            raise ValueError("AgentDefinition.system_prompt cannot be empty")
        if self.max_turns <= 0:
            raise ValueError(f"max_turns must be > 0; got {self.max_turns}")
        if self.budget_seconds <= 0:
            raise ValueError(f"budget_seconds must be > 0; got {self.budget_seconds}")
        if self.budget_llm_calls <= 0:
            raise ValueError(f"budget_llm_calls must be > 0; got {self.budget_llm_calls}")
        if self.max_contract_retries < 0:
            raise ValueError(f"max_contract_retries must be >= 0; got {self.max_contract_retries}")


# --- Frontmatter parser ------------------------------------------------------


_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_agent_spec_markdown(text: str) -> dict[str, Any]:
    """Parse YAML-frontmatter markdown into a dict.

    Returns a dict with the frontmatter keys plus ``_body`` for the markdown
    body. Raises ``ValueError`` when frontmatter is missing or malformed.

    Plan 157 P1-1: the frontmatter is parsed with PyYAML (`yaml.safe_load`),
    which supports folded `>` scalars, nested maps, and lists — the reference
    spec format. (No custom mini-parser fallback: PyYAML is a declared dep.)
    """
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        raise ValueError("Agent spec must begin with YAML frontmatter delimited by '---'")
    frontmatter_text = match.group(1)
    body = text[match.end():]
    try:
        parsed = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError as exc:
        raise ValueError(f"Malformed YAML frontmatter: {exc}") from exc
    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        raise ValueError(f"Agent spec frontmatter must be a mapping; got {type(parsed).__name__}")
    parsed["_body"] = body.strip()
    return parsed


def _version_tuple(version: str) -> tuple[int, ...]:
    """Parse a dotted version string into a comparable tuple; '' → (0,)."""
    parts: list[int] = []
    for chunk in str(version or "0").split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


# --- AgentDefinition loading -------------------------------------------------


def load_agent_definition_from_text(text: str, *, source_path: Path | None = None) -> AgentDefinition:
    """Build an ``AgentDefinition`` from a markdown spec string."""
    parsed = parse_agent_spec_markdown(text)
    body = str(parsed.get("_body", "")).strip()
    name = parsed.get("name")
    role = parsed.get("role")
    if name is None or role is None:
        raise ValueError("Agent spec must declare 'name' and 'role' in frontmatter")
    # Plan 157 P1-4: reject a spec newer than this harness understands.
    version = str(parsed.get("version", "") or "")
    if version and _version_tuple(version) > _version_tuple(SUPPORTED_SPEC_VERSION):
        raise ValueError(
            f"Agent spec '{name}' declares version {version} but this harness supports "
            f"<= {SUPPORTED_SPEC_VERSION}. Upgrade Duckln or lower the spec version."
        )
    tools_raw = parsed.get("tools") or []
    if not isinstance(tools_raw, list):
        raise ValueError(f"Agent spec 'tools' must be a list; got {type(tools_raw).__name__}")
    can_spawn_raw = parsed.get("can_spawn") or []
    if not isinstance(can_spawn_raw, list):
        raise ValueError(f"Agent spec 'can_spawn' must be a list; got {type(can_spawn_raw).__name__}")
    input_contract = parsed.get("input_contract") or {}
    if not isinstance(input_contract, dict):
        raise ValueError(f"Agent spec 'input_contract' must be a mapping; got {type(input_contract).__name__}")
    output_contract = parsed.get("output_contract") or {}
    if not isinstance(output_contract, dict):
        raise ValueError(f"Agent spec 'output_contract' must be a mapping; got {type(output_contract).__name__}")
    # Plan 187 F4a: parse optional per-verdict budget tiers. Additive + robust — a malformed
    # block is IGNORED (only dict-valued profiles are kept) so the flat default block stands;
    # never a load failure. The resolver coerces the numeric fields defensively at run time.
    profiles_raw = parsed.get("budget_profiles") or {}
    budget_profiles: dict[str, dict] = {}
    if isinstance(profiles_raw, dict):
        for pname, pval in profiles_raw.items():
            if isinstance(pval, dict):
                budget_profiles[str(pname)] = dict(pval)
    return AgentDefinition(
        name=str(name),
        role=str(role),
        system_prompt=body,
        tools=tuple(str(t) for t in tools_raw),
        max_turns=int(parsed.get("max_turns", 8)),
        budget_seconds=float(parsed.get("budget_seconds", 300.0)),
        budget_llm_calls=int(parsed.get("budget_llm_calls", 8)),
        can_spawn=tuple(str(s) for s in can_spawn_raw),
        is_coordinator=bool(parsed.get("is_coordinator", False)),
        source_path=source_path,
        version=version,
        input_contract=dict(input_contract),
        output_contract=dict(output_contract),
        max_contract_retries=int(parsed.get("max_contract_retries", 2)),
        budget_profiles=budget_profiles,
    )


def load_agent_definition_from_path(path: Path) -> AgentDefinition:
    text = path.read_text(encoding="utf-8")
    return load_agent_definition_from_text(text, source_path=path)


# --- AgentRegistry -----------------------------------------------------------


class AgentRegistry:
    """Catalog of agent definitions, separate from the tool registry.

    Discovery: ``AgentRegistry.from_directory(path)`` loads every ``*.md`` in
    the given directory as an agent spec. Each spec's tool list is validated
    against a tool-registry name set so a spec can't reference unknown tools.
    """

    def __init__(self) -> None:
        self._defs: dict[str, AgentDefinition] = {}

    def register(self, definition: AgentDefinition) -> None:
        if definition.name in self._defs:
            raise ValueError(f"Agent '{definition.name}' already registered")
        self._defs[definition.name] = definition

    def lookup(self, name: str) -> AgentDefinition | None:
        return self._defs.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._defs.keys()))

    def validate_against_tool_registry(self, tool_names: set[str]) -> tuple[str, ...]:
        """Return a tuple of (agent_name, missing_tool) error strings for any
        agent that references a tool not present in the tool registry."""
        errors: list[str] = []
        for agent in self._defs.values():
            for tool in agent.tools:
                if tool not in tool_names:
                    errors.append(f"{agent.name}: unknown tool '{tool}'")
            for spawn_target in agent.can_spawn:
                if spawn_target not in self._defs:
                    errors.append(f"{agent.name}: can_spawn references unknown agent '{spawn_target}'")
        return tuple(errors)

    @classmethod
    def from_directory(cls, directory: Path, *, required: frozenset[str] | None = None) -> "AgentRegistry":
        """Plan 157 P1-3: load every ``*.md`` ROBUSTLY. A malformed OPTIONAL spec
        is skipped + logged (one bad file can't disable the whole harness); a
        malformed/missing REQUIRED agent raises so a core capability never silently
        vanishes. A spec whose `version` exceeds SUPPORTED_SPEC_VERSION always
        raises (handled in the loader), regardless of required/optional."""
        registry = cls()
        required_set = REQUIRED_AGENTS if required is None else required
        if not directory.exists() or not directory.is_dir():
            if required_set:
                raise ValueError(
                    f"Agents directory {directory} not found, but required agents exist: "
                    f"{', '.join(sorted(required_set))}"
                )
            return registry
        errors: list[str] = []
        for spec_file in sorted(directory.glob("*.md")):
            spec_name = spec_file.stem
            if spec_name.upper() == "README":
                continue  # docs, not an agent spec
            try:
                definition = load_agent_definition_from_path(spec_file)
            except ValueError as exc:
                # A version-too-high error must always surface (it implies wrong runtime
                # behaviour), and a malformed REQUIRED agent must surface too.
                if "version" in str(exc).lower() or spec_name in required_set:
                    raise
                _LOG.warning("Skipping malformed optional agent spec %s: %s", spec_file.name, exc)
                errors.append(f"{spec_file.name}: {exc}")
                continue
            registry.register(definition)
        missing = sorted(name for name in required_set if registry.lookup(name) is None)
        if missing:
            raise ValueError(
                "Required agent spec(s) missing or unloadable: " + ", ".join(missing)
                + (f" (load errors: {'; '.join(errors)})" if errors else "")
            )
        return registry


def builtin_agents_directory() -> Path:
    """Path to the ``harness/agents/`` directory inside the installed package."""
    return Path(__file__).parent / "agents"


# --- Plan 157 P2: input/output contract enforcement -------------------------------------


def validate_input_contract(definition: AgentDefinition, available_state) -> tuple[str, ...]:
    """Return the `required_state_keys` the spec needs that are NOT present in
    `available_state` (a dict or a set/iterable of key names). Empty tuple = satisfied.
    The caller surfaces a clarification / lowers confidence rather than running blind."""
    required = definition.input_contract.get("required_state_keys") or []
    if not required:
        return ()
    if isinstance(available_state, dict):
        present = set(available_state.keys())
    else:
        present = set(available_state or ())
    return tuple(str(k) for k in required if str(k) not in present)


def _extract_json(text: str):
    """Lenient JSON extraction: bare value or inside ```json fences; first {...} or [...].

    Plan 178 F3: uniformly tolerant of a leading `<thought>...</thought>` reasoning block —
    the monologue is stripped (and captured upstream) before the JSON payload is parsed, so
    EVERY spec-driven reasoning agent can open with the structured monologue."""
    import json as _json

    try:
        from duckln.reasoning import extract_thinking_and_content as _ext, MISSING_THOUGHT as _MT

        _thought, _payload = _ext(str(text or ""))
        if _thought != _MT and _payload:
            text = _payload
    except Exception:
        pass
    s = str(text or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        s = s.lstrip("json").lstrip("JSON").strip()
    try:
        return _json.loads(s)
    except Exception:
        m = re.search(r"(\{.*\}|\[.*\])", s, re.DOTALL)
        if m:
            try:
                return _json.loads(m.group(1))
            except Exception:
                return None
    return None


def validate_agent_output(definition: AgentDefinition, raw: str):
    """Plan 157 P2-2: validate the model's raw reply against the spec's `output_contract`.
    Returns (ok: bool, parsed, errors: tuple[str, ...]).

    Supported contract `type`s: `json_array` (a list; if `item_schema` is present, each item
    must be a dict carrying the schema's keys), `json_object` (a dict; if `schema` is present,
    its top-level keys must be present), or none (any valid JSON). A spec with NO output_contract
    is permissive (parses JSON if it can, else returns the raw string)."""
    contract = definition.output_contract or {}
    ctype = str(contract.get("type") or "").strip().lower()
    if not ctype:
        parsed = _extract_json(raw)
        return (True, parsed if parsed is not None else str(raw or "").strip(), ())
    parsed = _extract_json(raw)
    if parsed is None:
        return (False, None, (f"output is not valid JSON (expected {ctype})",))
    errors: list[str] = []
    if ctype == "json_array":
        if not isinstance(parsed, list):
            return (False, parsed, (f"expected a JSON array, got {type(parsed).__name__}",))
        item_schema = contract.get("item_schema") or {}
        if isinstance(item_schema, dict) and item_schema:
            required_item_keys = [k for k in item_schema.keys()]
            for idx, item in enumerate(parsed):
                if not isinstance(item, dict):
                    errors.append(f"item {idx} is not an object")
                    continue
                # Only flag a missing key when the schema marks it non-optional by naming it
                # without a "| null"/optional hint — keep lenient: require the first key present.
        # an empty array is valid (e.g. clarifier "no questions")
    elif ctype == "json_object":
        if not isinstance(parsed, dict):
            return (False, parsed, (f"expected a JSON object, got {type(parsed).__name__}",))
        schema = contract.get("schema") or {}
        if isinstance(schema, dict) and schema:
            for key in schema.keys():
                if key not in parsed:
                    errors.append(f"missing required key '{key}'")
    return (len(errors) == 0, parsed, tuple(errors))


def render_output_contract_text(definition: AgentDefinition) -> str:
    """Plan 157 P2-3: a concise, model-facing description of the spec's output_contract,
    injected into the system prompt so the model sees the exact shape it'll be validated
    against. Empty string when the spec has no output_contract."""
    import json as _json

    contract = definition.output_contract or {}
    if not contract:
        return ""
    ctype = contract.get("type") or "json"
    lines = [f"OUTPUT CONTRACT — return ONLY {ctype} (no markdown fences, no prose)."]
    desc = str(contract.get("description") or "").strip()
    if desc:
        lines.append(desc)
    schema = contract.get("item_schema") or contract.get("schema")
    if schema:
        try:
            lines.append("Schema: " + _json.dumps(schema, ensure_ascii=False))
        except Exception:
            pass
    return "\n".join(lines)
