"""Managed planning artifacts for bounded repo changes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from state.repo_catalog import RepoCatalogRecord


PLANS_DIR_NAME = ".duckln_plans"


@dataclass(frozen=True)
class PlanningRecord:
    """Planning artifact metadata for a repo thread."""

    repo_key: str
    execution_target: str
    relative_path: str
    content: str


def resolve_repo_plan_path(config_dir: Path, repo: RepoCatalogRecord, *, project_dir: Path | None = None) -> Path:
    """Return the bounded TODO path for a repo thread."""

    if project_dir is not None and project_dir.exists():
        return project_dir / "TODO.md"
    return config_dir / "projects" / PLANS_DIR_NAME / _slugify(repo.name) / "TODO.md"


def build_repo_plan_record(
    *,
    config_dir: Path,
    repo: RepoCatalogRecord,
    execution_target: str,
    understanding: str,
    inspect_items: tuple[str, ...],
    change_items: tuple[str, ...],
    completion_items: tuple[str, ...],
    project_dir: Path | None = None,
    specialist_name: str | None = None,
) -> PlanningRecord:
    """Build a concise TODO.md plan for a repo setup or repair thread."""

    path = resolve_repo_plan_path(config_dir, repo, project_dir=project_dir)
    header = f"# TODO for {repo.name}\n\n"
    specialist_line = f"- Specialist: {specialist_name}\n" if specialist_name else ""
    content = (
        header
        + "## What Duckln understands\n"
        + f"- {understanding.strip()}\n"
        + f"- Execution target: {execution_target}\n"
        + specialist_line
        + "\n## What Duckln will inspect\n"
        + "\n".join(f"- {item}" for item in inspect_items)
        + "\n\n## What Duckln will change\n"
        + "\n".join(f"- {item}" for item in change_items)
        + "\n\n## Completion looks like\n"
        + "\n".join(f"- {item}" for item in completion_items)
        + "\n"
    )
    return PlanningRecord(
        repo_key=repo.repo_url or repo.name,
        execution_target=execution_target,
        relative_path=str(path),
        content=content,
    )


def write_repo_plan(record: PlanningRecord) -> Path:
    """Write the TODO.md plan to disk."""

    destination = Path(record.relative_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(record.content, encoding="utf-8")
    return destination


def _slugify(value: str) -> str:
    return "".join(character if character.isalnum() or character in "._-" else "-" for character in value.strip()).strip(
        "-._"
    ) or "repo"
