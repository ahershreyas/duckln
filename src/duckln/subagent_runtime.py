"""Runtime helpers that connect subagent workspace files to dispatch and prompts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from duckln.prompts import build_subagent_prompt_template
from duckln.tool_registry import ToolVisibilityPolicy, filter_tool_registry_entries
from duckln.subagents import SubagentRuntimeDescriptor, default_subagent_runtime_descriptors
from state.access import read_subagent_workspace_sections


@dataclass(frozen=True)
class SubagentRuntimeProfile:
    """A scoped runtime view for one Duckln specialist."""

    descriptor: SubagentRuntimeDescriptor
    workspace_sections: dict[str, str]
    prompt_template: str
    allowed_tool_ids: tuple[str, ...]
    allowed_tool_labels: tuple[str, ...]


def descriptor_for_slug(slug: str) -> SubagentRuntimeDescriptor | None:
    return next((item for item in default_subagent_runtime_descriptors() if item.slug == slug), None)


def descriptor_for_specialist_name(specialist_name: str) -> SubagentRuntimeDescriptor | None:
    return next(
        (item for item in default_subagent_runtime_descriptors() if item.specialist_name == specialist_name),
        None,
    )


def select_subagent_descriptor(
    *,
    repo_family: str,
    execution_target: str,
    detected_files: tuple[str, ...],
) -> SubagentRuntimeDescriptor | None:
    descriptors = [
        descriptor
        for descriptor in default_subagent_runtime_descriptors()
        if execution_target in descriptor.execution_targets
    ]
    direct = next((item for item in descriptors if repo_family in item.repo_families), None)
    if direct is not None:
        return direct
    if repo_family == "multi_service":
        detected = set(detected_files)
        for descriptor in descriptors:
            if descriptor.signal_files and detected.intersection(descriptor.signal_files):
                return descriptor
    # Plan 156 P3: an UNMATCHED stack (Java/Gradle, Scala, an odd build) must NOT be mislabeled
    # as Python — route to the GENERALIST engineer (reads the real build files + reasons), so
    # "any repo" is real. A Python repo with a `requirements.txt`/`pyproject.toml` signal still
    # prefers the Python specialist.
    detected = set(detected_files)
    if detected & {"requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "Pipfile"}:
        py = next((item for item in descriptors if item.slug == "python_setup"), None)
        if py is not None:
            return py
    return next((item for item in descriptors if item.slug == "generalist"), None)


def build_subagent_runtime_profile(
    *,
    config_dir: Path | None,
    slug: str,
    execution_target: str,
) -> SubagentRuntimeProfile | None:
    descriptor = descriptor_for_slug(slug)
    if descriptor is None:
        return None
    sections = read_subagent_workspace_sections(config_dir, slug=slug) if config_dir is not None else {}
    visible_tools = filter_tool_registry_entries(
        policy=ToolVisibilityPolicy(
            execution_target=execution_target,
            include_supervisor_only=False,
            allowed_tool_ids=descriptor.allowed_tool_ids,
        )
    )
    tool_ids = tuple(entry.tool_id for entry in visible_tools)
    tool_labels = tuple(entry.label for entry in visible_tools)
    return SubagentRuntimeProfile(
        descriptor=descriptor,
        workspace_sections=sections,
        prompt_template=build_subagent_prompt_template(
            subagent_name=descriptor.title,
            execution_target=execution_target,
            workspace_sections=sections,
            allowed_tool_labels=tool_labels,
        ),
        allowed_tool_ids=tool_ids,
        allowed_tool_labels=tool_labels,
    )
