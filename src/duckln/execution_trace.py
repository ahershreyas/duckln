"""Shared execution-trace and failure-formatting helpers for user-visible Duckln actions."""

from __future__ import annotations

from duckln.diagnostics import redact_sensitive_data
from duckln.repair_intake import compress_failure_for_remote
from duckln.render_blocks import join_blocks, paragraph_block, step_list_block
from duckln.tool_registry import ToolVisibilityPolicy, filter_tool_registry_entries
from typing import Callable


def render_execution_trace(title: str, items: tuple[str, ...] | list[str]) -> str:
    """Render a consistent user-visible execution trace block."""

    cleaned = tuple(item.strip() for item in items if item and item.strip())
    return join_blocks(
        paragraph_block(f"Duckln trace: {title}."),
        step_list_block(cleaned),
    )


def render_tool_invocation_trace(
    *,
    title: str,
    tool_id: str,
    action: str,
    detail_lines: tuple[str, ...] | list[str] = (),
    execution_target: str = "local",
    source_urls: tuple[str, ...] | list[str] = (),
    search_query: str | None = None,
) -> str:
    """Render a trace block that grounds the action in the declared tool registry."""

    entry = next(
        (
            item
            for item in filter_tool_registry_entries(policy=ToolVisibilityPolicy(execution_target=execution_target))
            if item.tool_id == tool_id
        ),
        None,
    )
    tool_label = entry.label if entry is not None else tool_id
    approval_line = (
        "Approval: required by Duckln policy before execution."
        if entry is not None and entry.approval_required
        else "Approval: not required by default for this tool."
    )
    items = [
        f"Tool: {tool_label} ({tool_id})",
        f"Action: {action}",
    ]
    if entry is not None:
        items.append(f"Safety class: {entry.safety_class.value}")
        items.append(approval_line)
    items.extend(line.strip() for line in detail_lines if line and line.strip())
    cleaned_sources = tuple(source.strip() for source in source_urls if source and source.strip())
    if cleaned_sources:
        rendered_sources = ", ".join(f"[{index}] {source}" for index, source in enumerate(cleaned_sources, start=1))
        items.append(f"Sources: {rendered_sources}")
    if search_query and search_query.strip():
        items.append(f"Search query: {search_query.strip()}")
    return render_execution_trace(title, tuple(items))


def build_failure_message(
    prefix: str,
    stderr: str,
    *,
    command: str | None = None,
    trace: Callable[[str], None] | None = None,
    execution_target: str = "local",
) -> str:
    """Render a consistent failure message with official-doc and bounded web-search context."""

    from duckln.web_runtime import build_runtime_search_query, build_runtime_web_reference_note

    detail = compress_failure_for_remote(command=command, stderr=stderr)
    detail = redact_sensitive_data(detail or ("No stderr output." if not stderr.strip() else stderr))
    reference_note = build_runtime_web_reference_note(
        command=command,
        error_text=detail,
        fetch_live=True,
        trace=trace,
        execution_target=execution_target,
    )
    if reference_note:
        query = build_runtime_search_query(command=command, error_text=detail)
        if "broadened the search on the web" in reference_note and query:
            return f"{prefix}: {detail} Duckln search query: `{query}`. {reference_note}"
        return f"{prefix}: {detail} {reference_note}"
    return f"{prefix}: {detail}"
