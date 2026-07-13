"""Plan 99 (Plan 96 Phase 3) — native provider function-calling.

The harness loop is provider-agnostic via a JSON decision contract (works with any
model, including local Ollama). For providers that support NATIVE tool-use
(Anthropic, OpenAI), this module converts the tool registry into the provider's
tool schema and parses the provider's tool-call response into a uniform decision —
improving reliability over prompt-coaxed JSON.

Pure functions only (no network); the live HTTP call lives in ``ai_client`` and the
loop integration is the optional ``tool_decider`` hook in ``harness/loop.py``.
"""

from __future__ import annotations

from typing import Any


_TOOL_USE_PROVIDERS = {"anthropic", "openai"}


def provider_supports_tool_use(provider: str) -> bool:
    return str(provider or "").lower() in _TOOL_USE_PROVIDERS


def _json_type(t: str) -> str:
    return {"str": "string", "int": "integer", "float": "number", "bool": "boolean",
            "dict": "object", "list": "array"}.get(t, "string")


def to_provider_tool_schemas(specs, provider: str) -> list[dict]:
    """Convert ToolSpec objects into the provider's tool-schema list.

    Anthropic: ``{name, description, input_schema}``.
    OpenAI:    ``{type:'function', function:{name, description, parameters}}``.
    """
    provider = str(provider or "").lower()
    out: list[dict] = []
    for spec in specs:
        props: dict[str, Any] = {}
        required: list[str] = []
        for arg, meta in (spec.args_schema or {}).items():
            props[arg] = {"type": _json_type(str(meta.get("type", "str")))}
            if meta.get("required"):
                required.append(arg)
        json_schema = {"type": "object", "properties": props, "required": required}
        if provider == "anthropic":
            out.append({"name": spec.name, "description": spec.description, "input_schema": json_schema})
        else:  # openai
            out.append({
                "type": "function",
                "function": {"name": spec.name, "description": spec.description, "parameters": json_schema},
            })
    return out


def parse_native_tool_call(provider: str, response: dict) -> dict | None:
    """Parse a provider response into ``{"tool", "args", "reason"}`` or ``{"stop": True}``.

    Returns None when the response shape is unrecognized (caller falls back to the
    JSON-text contract). Never raises."""
    provider = str(provider or "").lower()
    try:
        if provider == "anthropic":
            content = response.get("content") or []
            text_reason = ""
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    return {"tool": block.get("name"), "args": block.get("input") or {}, "reason": text_reason}
                if isinstance(block, dict) and block.get("type") == "text":
                    text_reason = (block.get("text") or "")[:200]
            # No tool_use → the model produced a final answer → stop.
            return {"stop": True, "reason": text_reason or "no_tool_use"}
        if provider == "openai":
            msg = (response.get("choices") or [{}])[0].get("message") or {}
            calls = msg.get("tool_calls") or []
            if calls:
                fn = (calls[0] or {}).get("function") or {}
                import json as _json
                raw_args = fn.get("arguments") or "{}"
                args = _json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                return {"tool": fn.get("name"), "args": args if isinstance(args, dict) else {}, "reason": ""}
            return {"stop": True, "reason": (msg.get("content") or "")[:200] or "no_tool_call"}
    except Exception:
        return None
    return None
