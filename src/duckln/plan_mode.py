"""Plan 67 — Plan Mode core module.

This module owns the five-stage plan-generation pipeline that runs when
`AppConfig.plan_mode_enabled` is True. The pipeline is deterministic where it
can be and LLM-driven only where reasoning is genuinely required:

Stage 1 — Repo understanding (deterministic, no LLM)
Stage 2 — Candidate step proposal (one LLM call)
Stage 3 — Critique + ordering (one LLM call)
Stage 4 — Safety classification + verification (deterministic)
Stage 5 — Optional clarification gate (one LLM call, capped at 3 questions)

The resulting `PlanRecord` is the only thing the strict-adherence executor
reads. When a step fails during execution, `attribute_failure()` produces a
cause + fix that is inserted into the plan as a visible amendment.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

from duckln.modes import ControlMode
from duckln.safety import SafetyClass, assess_command

# --- Plan 157 P3: the planning agents are SPEC-DRIVEN ------------------------------------
# The system prompt for each planning stage is sourced from its agent .md spec body (+ the
# spec's output_contract), via `_spec_prompt(...)` — NOT from a hardcoded `SYSTEM_PROMPT_PLAN_*`
# constant (those were retired from prompts.py). Editing an agent's behavior is now a spec edit.
#
# P3-2 NAMING RESOLUTION (verified in code before writing specs):
#   - `run_plan_supervisor` makes NO LLM call of its own — it only orchestrates the stages
#     (propose → critique → classify → clarify). So `supervisor.md` is an orchestration/
#     coordinator spec with no executed body prompt.
#   - `critic_review`'s verdict (approve/revise/block) IS a separate LLM call from the critique
#     (ordering) call. Therefore the verdict gets its OWN spec: `verdict.md` (distinct from
#     `critic.md` = critique/order, and from `supervisor.md` = orchestration).
# Stage → spec: propose→planner, critique→critic, clarify→clarifier, verdict→verdict,
# attribute→attributor. Each stage keeps its specialized parse/validate/fallback control flow;
# `harness.loop.run_spec` (the contract-enforcing executor) is used by structured-output specs
# and is available, while these in-process stages retain their battle-tested validation.
from functools import lru_cache


@lru_cache(maxsize=None)
def _spec_prompt(agent_name: str) -> str:
    """Plan 157 P3: the composed system prompt (spec body + output_contract text) for a
    planning agent, loaded once from its `.md` spec. Required planning specs always exist
    (the registry enforces REQUIRED_AGENTS at startup)."""
    from duckln.harness.agent_def import (
        builtin_agents_directory,
        load_agent_definition_from_path,
        render_output_contract_text,
    )

    definition = load_agent_definition_from_path(builtin_agents_directory() / f"{agent_name}.md")
    contract = render_output_contract_text(definition)
    return definition.system_prompt + (f"\n\n{contract}" if contract else "")


def _reference_section(key: str) -> str:
    """Plan 159 F3: read ONE anchored section from the planner stack-reference file
    (`agents/reference/planner_stack_guides.md`), delimited by `<!-- ref:KEY -->`."""
    from duckln.harness.agent_def import builtin_agents_directory

    try:
        text = (builtin_agents_directory() / "reference" / "planner_stack_guides.md").read_text(encoding="utf-8")
    except Exception:
        return ""
    marker = f"<!-- ref:{key} -->"
    start = text.find(marker)
    if start < 0:
        return ""
    start += len(marker)
    nxt = text.find("<!-- ref:", start)
    return text[start:(nxt if nxt >= 0 else len(text))].strip()


def stack_reference_snippet(repo_family: str | None, detected_files) -> str:
    """Plan 159 F3: the JUST-IN-TIME stack detail to append to the planner's USER message —
    only the section(s) matching the detected stack, so the always-loaded planner.md stays
    lean. Returns '' when nothing matches. (Node + AI/ML are anchored in planner.md's core, so
    the inference table is added only for OTHER build stacks that the core examples don't cover.)"""
    files = {str(f).strip().lower() for f in (detected_files or ())}
    fam = str(repo_family or "").strip().lower()
    parts: list[str] = []
    if "manage.py" in files or "django" in fam:
        parts.append(_reference_section("django"))
    if files & {"turbo.json", "pnpm-workspace.yaml", "lerna.json", "nx.json"}:
        parts.append(_reference_section("monorepo"))
    if files & {"dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yaml", "compose.yml"}:
        parts.append(_reference_section("docker"))
    # The package→command inference table helps stacks the two core examples don't cover
    # (go/rust/ruby/php/java/…). Node + Python/ML are already anchored in the core prompt.
    _core_covered = fam in ("node_typescript", "python", "diffusion_heavy") or "package.json" in files
    if not _core_covered:
        parts.append(_reference_section("inference-table"))
    snippet = "\n\n".join(p for p in parts if p)
    if not snippet:
        return ""
    return (
        "\n\n## Stack-specific reference (matched to this repo)\n"
        "Use these idioms/examples for THIS repo's detected stack:\n\n" + snippet
    )


# --- Public dataclasses ------------------------------------------------------


@dataclass(frozen=True)
class PlanStep:
    """A single step in a Plan Mode plan."""

    index: int
    title: str
    description: str
    command: str | None
    safety_class: str
    verification: str | None
    rationale: str
    estimated_seconds: int
    confidence: float = 1.0
    depends_on: tuple[int, ...] = ()
    origin: str = "planner"  # "planner" | "amendment" | "user"
    # Plan 78 Fix B: per-step evidence so a reviewer can see WHERE the command
    # runs and WHY it was chosen. All default to "" for backward compatibility.
    target: str = ""  # local | vm | container | aws | gcp
    source: str = ""  # e.g. "README.md", "package.json", a URL, "duckln-deterministic"
    evidence_excerpt: str = ""  # redacted snippet the command came from
    cwd: str = ""  # working directory the command runs in

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "title": self.title,
            "description": self.description,
            "command": self.command,
            "safety_class": self.safety_class,
            "verification": self.verification,
            "rationale": self.rationale,
            "estimated_seconds": self.estimated_seconds,
            "confidence": self.confidence,
            "depends_on": list(self.depends_on),
            "origin": self.origin,
            "target": self.target,
            "source": self.source,
            "evidence_excerpt": self.evidence_excerpt,
            "cwd": self.cwd,
        }


@dataclass(frozen=True)
class ClarificationQuestion:
    """One clarification question Duckln asks the user."""

    text: str
    options: tuple[str, ...]
    default: str | None
    applies_to_step_indices: tuple[int, ...]


@dataclass(frozen=True)
class PlanRecord:
    """A complete plan: steps, risks, rollback, status."""

    plan_id: str
    objective: str
    context_summary: str
    steps: tuple[PlanStep, ...]
    risks: tuple[str, ...]
    rollback: str
    estimated_seconds: int
    created_at: str
    status: str
    repo_slug: str | None
    mode_at_creation: str
    clarifications: tuple[ClarificationQuestion, ...] = ()
    dropped_candidates: tuple[str, ...] = ()
    critic_reasoning: str = ""
    amendment_count: int = 0
    history: tuple[str, ...] = ()  # ordered "decision: detail" lines
    # Plan 83: what the pre-check found, the target machine, and the env keys the
    # user must set after setup (defaults keep persisted plans backward-compatible).
    precheck_summary: str = ""
    target_label: str = ""
    required_env_keys: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "objective": self.objective,
            "context_summary": self.context_summary,
            "steps": [s.to_dict() for s in self.steps],
            "risks": list(self.risks),
            "rollback": self.rollback,
            "estimated_seconds": self.estimated_seconds,
            "created_at": self.created_at,
            "status": self.status,
            "repo_slug": self.repo_slug,
            "mode_at_creation": self.mode_at_creation,
            "clarifications": [
                {
                    "text": c.text,
                    "options": list(c.options),
                    "default": c.default,
                    "applies_to_step_indices": list(c.applies_to_step_indices),
                }
                for c in self.clarifications
            ],
            "dropped_candidates": list(self.dropped_candidates),
            "critic_reasoning": self.critic_reasoning,
            "amendment_count": self.amendment_count,
            "history": list(self.history),
            "precheck_summary": self.precheck_summary,
            "target_label": self.target_label,
            "required_env_keys": list(self.required_env_keys),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PlanRecord":
        steps_raw = payload.get("steps") or []
        steps = tuple(
            PlanStep(
                index=int(s["index"]),
                title=str(s["title"]),
                description=str(s.get("description") or ""),
                command=(None if s.get("command") in (None, "") else str(s["command"])),
                safety_class=str(s.get("safety_class") or "S2"),
                verification=(None if s.get("verification") in (None, "") else str(s["verification"])),
                rationale=str(s.get("rationale") or ""),
                estimated_seconds=int(s.get("estimated_seconds") or 30),
                confidence=float(s.get("confidence") or 1.0),
                depends_on=tuple(int(x) for x in (s.get("depends_on") or [])),
                origin=str(s.get("origin") or "planner"),
                target=str(s.get("target") or ""),
                source=str(s.get("source") or ""),
                evidence_excerpt=str(s.get("evidence_excerpt") or ""),
                cwd=str(s.get("cwd") or ""),
            )
            for s in steps_raw
        )
        clarifications = tuple(
            ClarificationQuestion(
                text=str(q.get("text") or ""),
                options=tuple(str(o) for o in (q.get("options") or ())),
                default=q.get("default"),
                applies_to_step_indices=tuple(int(x) for x in (q.get("applies_to_step_indices") or ())),
            )
            for q in (payload.get("clarifications") or ())
        )
        return cls(
            plan_id=str(payload["plan_id"]),
            objective=str(payload.get("objective") or ""),
            context_summary=str(payload.get("context_summary") or ""),
            steps=steps,
            risks=tuple(str(r) for r in (payload.get("risks") or ())),
            rollback=str(payload.get("rollback") or ""),
            estimated_seconds=int(payload.get("estimated_seconds") or 0),
            created_at=str(payload.get("created_at") or ""),
            status=str(payload.get("status") or "pending"),
            repo_slug=payload.get("repo_slug"),
            mode_at_creation=str(payload.get("mode_at_creation") or "hitl"),
            clarifications=clarifications,
            dropped_candidates=tuple(str(d) for d in (payload.get("dropped_candidates") or ())),
            critic_reasoning=str(payload.get("critic_reasoning") or ""),
            amendment_count=int(payload.get("amendment_count") or 0),
            history=tuple(str(h) for h in (payload.get("history") or ())),
            precheck_summary=str(payload.get("precheck_summary") or ""),
            target_label=str(payload.get("target_label") or ""),
            required_env_keys=tuple(str(k) for k in (payload.get("required_env_keys") or ())),
        )


@dataclass(frozen=True)
class RepoUnderstanding:
    """Stage-1 output: what Duckln knows about the repo BEFORE any LLM call."""

    repo_slug: str | None
    repo_path: str | None
    objective: str
    os_name: str
    arch: str
    detected_runtimes: tuple[str, ...]  # e.g. ("node", "python", "cargo")
    detected_files: tuple[str, ...]
    repo_family: str  # "python" | "node_typescript" | "cpp_native" | "go" | "rust" | "unknown"
    readme_excerpt: str
    config_excerpts: dict[str, str]  # filename -> first 4KB
    recent_failures: tuple[str, ...]  # last 3 failure summaries
    recent_skills: tuple[str, ...]  # last 3 successful skill summaries
    execution_target: str
    control_mode: str
    needs_clarification: bool
    clarification_seed: str | None  # initial question text if family unknown

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_slug": self.repo_slug,
            "repo_path": self.repo_path,
            "objective": self.objective,
            "os_name": self.os_name,
            "arch": self.arch,
            "detected_runtimes": list(self.detected_runtimes),
            "detected_files": list(self.detected_files),
            "repo_family": self.repo_family,
            "readme_excerpt": self.readme_excerpt,
            "config_excerpts": dict(self.config_excerpts),
            "recent_failures": list(self.recent_failures),
            "recent_skills": list(self.recent_skills),
            "execution_target": self.execution_target,
            "control_mode": self.control_mode,
            "needs_clarification": self.needs_clarification,
            "clarification_seed": self.clarification_seed,
        }


@dataclass(frozen=True)
class CriticVerdict:
    """Plan 72 Phase 3: structured supervisor verdict on a drafted plan."""

    verdict: str  # "approve" | "revise" | "block" | "skipped"
    reason: str
    missing_question: str | None = None
    external_blocker: str | None = None


@dataclass(frozen=True)
class AttributionResult:
    """Output of attribute_failure()."""

    cause: str
    fix_step: PlanStep | None
    confidence: float


class LLMClient(Protocol):
    """Matches duckln.harness.loop.LLMClient — accepted via Protocol so Plan Mode
    can be tested with a tiny in-memory fake without importing the harness."""

    def __call__(self, *, system_prompt: str, user_message: str) -> str: ...


# --- Public constants --------------------------------------------------------


PLAN_HISTORY_FILE_NAME = "history.md"
PLAN_DIR_NAME = "plans"
MAX_CLARIFICATION_QUESTIONS = 3
LOW_CONFIDENCE_THRESHOLD = 0.6
MAX_AMENDMENTS = 3
README_EXCERPT_BYTES = 4096
CONFIG_EXCERPT_BYTES = 4096
DEFAULT_STEP_SECONDS = 30
PLAN_STATUS_PENDING = "pending"
PLAN_STATUS_APPROVED = "approved"
PLAN_STATUS_REJECTED = "rejected"
PLAN_STATUS_COMPLETED = "completed"
PLAN_STATUS_FAILED = "failed"
PLAN_STATUS_EDITED = "edited"
PLAN_STATUS_AMENDED = "amended"
PLAN_STATUS_IRRECOVERABLE = "irrecoverable"


# --- Stage 1: Repo understanding (deterministic) -----------------------------


_REPO_CONFIG_FILES: tuple[str, ...] = (
    "README.md",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "setup.py",
    "Cargo.toml",
    "Makefile",
    "Dockerfile",
    "go.mod",
    "build.gradle",
    "pom.xml",
    "CMakeLists.txt",
)


def gather_repo_understanding(
    *,
    objective: str,
    project_dir: Path | None,
    repo_slug: str | None,
    config_dir: Path | None,
    os_name: str = "",
    arch: str = "",
    execution_target: str = "local",
    control_mode: str = "hitl",
    detected_runtimes: Iterable[str] = (),
    override_detected_files: tuple[str, ...] | None = None,
    family_hint: str | None = None,
    override_readme_excerpt: str | None = None,
    override_family: str | None = None,
) -> RepoUnderstanding:
    """Stage 1: gather everything Duckln can determine without an LLM call.

    Reads only local / read-only sources — it must NEVER execute a command on
    the execution target. For LOCAL clones, files are scanned from
    ``project_dir``. When the repo is inspected read-only (e.g. README +
    manifests fetched over HTTP for a VM/cloud target before approval), the
    caller passes ``override_detected_files`` / ``override_readme_excerpt`` /
    ``override_family`` so understanding is accurate without touching the target.
    ``family_hint`` is a weaker metadata-only fallback used when nothing else
    resolves the family.
    """

    detected_files: list[str] = []
    config_excerpts: dict[str, str] = {}
    readme_excerpt = override_readme_excerpt or ""

    if override_detected_files is not None:
        detected_files = [f for f in override_detected_files if f]
    elif project_dir is not None and project_dir.exists() and project_dir.is_dir():
        for name in _REPO_CONFIG_FILES:
            candidate = project_dir / name
            if not candidate.exists() or not candidate.is_file():
                continue
            detected_files.append(name)
            try:
                text = candidate.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            snippet = text[:CONFIG_EXCERPT_BYTES]
            config_excerpts[name] = snippet
            if name == "README.md":
                readme_excerpt = snippet[:README_EXCERPT_BYTES]

    if override_family:
        family = override_family
    else:
        family = _classify_family_from_files(tuple(detected_files), readme_excerpt)
        if family == "unknown" and family_hint:
            family = family_hint

    recent_failures = _recent_failures_for(config_dir, repo_slug) if config_dir else ()
    recent_skills = _recent_skills(config_dir) if config_dir else ()

    needs_clarification = False
    clarification_seed: str | None = None
    if family == "unknown" and len(readme_excerpt) < 200:
        needs_clarification = True
        clarification_seed = (
            "What kind of project is this? (e.g. Python web service, Node CLI, "
            "C++ library, Go service…)"
        )

    return RepoUnderstanding(
        repo_slug=repo_slug,
        repo_path=str(project_dir) if project_dir is not None else None,
        objective=objective,
        os_name=os_name or "",
        arch=arch or "",
        detected_runtimes=tuple(detected_runtimes),
        detected_files=tuple(detected_files),
        repo_family=family,
        readme_excerpt=readme_excerpt,
        config_excerpts=config_excerpts,
        recent_failures=recent_failures,
        recent_skills=recent_skills,
        execution_target=execution_target or "local",
        control_mode=control_mode or "hitl",
        needs_clarification=needs_clarification,
        clarification_seed=clarification_seed,
    )


def _classify_family_from_files(files: tuple[str, ...], readme: str) -> str:
    """Lightweight family classifier — no external dependencies."""
    fset = set(files)
    if {"package.json"} & fset:
        return "node_typescript"
    if {"pyproject.toml", "requirements.txt", "setup.py"} & fset:
        return "python"
    if {"Cargo.toml"} & fset:
        return "rust"
    if {"go.mod"} & fset:
        return "go"
    if {"CMakeLists.txt", "Makefile"} & fset:
        return "cpp_native"
    if {"build.gradle", "pom.xml"} & fset:
        return "java"
    readme_lower = readme.lower()
    if "npm install" in readme_lower or "yarn install" in readme_lower:
        return "node_typescript"
    if "pip install" in readme_lower:
        return "python"
    return "unknown"


def family_hint_from_metadata(
    *,
    framework: str = "",
    category: str = "",
    description: str = "",
) -> str | None:
    """Derive a repo family from catalog metadata WITHOUT touching the target.

    Used when a repo isn't cloned locally (e.g. a VM/cloud target before
    approval) so Stage-1 understanding stays accurate without executing
    anything remotely. Returns None when nothing matches.
    """
    blob = " ".join((framework or "", category or "", description or "")).lower()
    if not blob.strip():
        return None
    if any(k in blob for k in ("node", "typescript", "javascript", "react", "next", "vue", "svelte", "vite", "npm", "pnpm", "yarn")):
        return "node_typescript"
    if any(k in blob for k in ("python", "django", "flask", "fastapi", "pytorch", "tensorflow", "pip", "conda")):
        return "python"
    if "rust" in blob or "cargo" in blob:
        return "rust"
    if any(k in blob for k in ("golang", "go ")):
        return "go"
    if any(k in blob for k in ("c++", "cpp", "cmake", "native")):
        return "cpp_native"
    if any(k in blob for k in ("java", "kotlin", "gradle", "maven")):
        return "java"
    return None


def _recent_failures_for(config_dir: Path, repo_slug: str | None) -> tuple[str, ...]:
    """Read up to 3 recent failure summaries from memory/failures/<slug>.md."""
    if config_dir is None or repo_slug is None:
        return ()
    failures_dir = config_dir / "memory" / "failures"
    if not failures_dir.exists():
        return ()
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", repo_slug)[:80]
    candidates = (
        failures_dir / f"{safe}.md",
        failures_dir / "_global.md",
    )
    summaries: list[str] = []
    for path in candidates:
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for chunk in re.split(r"\n## ", text):
            chunk = chunk.strip()
            if not chunk:
                continue
            first_lines = chunk.splitlines()[:3]
            summaries.append(" ".join(line.strip() for line in first_lines))
            if len(summaries) >= 3:
                return tuple(summaries[:3])
    return tuple(summaries[:3])


def _recent_skills(config_dir: Path) -> tuple[str, ...]:
    """Read up to 3 recent skill summaries from memory/skills/*.md."""
    if config_dir is None:
        return ()
    skills_dir = config_dir / "memory" / "skills"
    if not skills_dir.exists():
        return ()
    try:
        files = sorted(skills_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:3]
    except OSError:
        return ()
    summaries: list[str] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        first_para = re.split(r"\n\s*\n", text.strip(), maxsplit=1)[0]
        summaries.append(first_para[:240])
    return tuple(summaries)


# --- JSON parsing helpers ----------------------------------------------------


def _redacted(text: str) -> str:
    """Plan 72 Phase 5: strip secrets/PII before any payload leaves Duckln to
    an LLM, web search, or stored memory. Best-effort — never raises."""
    try:
        from duckln.diagnostics import redact_sensitive_data

        return redact_sensitive_data(text)
    except Exception:
        return text


_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL)


def parse_plan_json(raw: str) -> Any:
    """Robust JSON parser: strips ```json fences, trailing prose, leading prose.

    Returns the parsed object. Raises ``ValueError`` if no JSON found.
    """
    if raw is None:
        raise ValueError("empty LLM response")
    text = raw.strip()
    if not text:
        raise ValueError("empty LLM response")
    fence_match = _FENCE_RE.search(text)
    if fence_match:
        text = fence_match.group(1).strip()
    # Try direct parse.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Extract the first JSON-looking substring.
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start < 0:
            continue
        depth = 0
        in_str: str | None = None
        i = start
        while i < len(text):
            ch = text[i]
            if in_str:
                if ch == "\\":
                    i += 2
                    continue
                if ch == in_str:
                    in_str = None
            elif ch in ('"', "'"):
                in_str = ch
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break
            i += 1
    # Plan 119: best-effort repair of a TRUNCATED object (the model was cut off
    # by a too-small token budget — the screenshots' `…env'`). Take the first
    # opener, drop a trailing partial token, and close the open string/brackets.
    repaired = _repair_truncated_json(text)
    if repaired is not None:
        return repaired
    raise ValueError(f"could not parse JSON from LLM response: {raw[:200]!r}")


def _json_close_suffix(prefix: str) -> str | None:
    """Return the suffix that closes any open string + containers in ``prefix``,
    or ``None`` if ``prefix`` ends mid-escape (caller should shrink and retry)."""
    in_str = False
    escape = False
    stack: list[str] = []
    for ch in prefix:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if in_str:
            if ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in "}]":
            if stack:
                stack.pop()
    if escape:
        return None
    return ('"' if in_str else "") + "".join(reversed(stack))


def _repair_truncated_json(text: str) -> Any | None:
    """Recover a value from a JSON object/array truncated mid-emit.

    Walks the longest-to-shortest prefix from the first ``{``/``[``; at each
    length it closes the open string/containers and tries to parse. The largest
    prefix that parses wins — so a response cut off after a too-small token
    budget (the screenshots' `…env'`) still yields a usable object.
    """
    candidates = [p for p in (text.find("{"), text.find("[")) if p >= 0]
    if not candidates:
        return None
    body = text[min(candidates):].rstrip()
    for end in range(len(body), 0, -1):
        suffix = _json_close_suffix(body[:end])
        if suffix is None:
            continue
        try:
            return json.loads(body[:end] + suffix)
        except json.JSONDecodeError:
            continue
    return None


# --- Stage 2: candidate proposal ---------------------------------------------


@dataclass(frozen=True)
class _CandidateStep:
    title: str
    command: str | None
    confidence: float
    estimated_seconds: int


def _propose_candidates(
    *,
    understanding: RepoUnderstanding,
    llm_client: LLMClient,
) -> tuple[_CandidateStep, ...]:
    user_message = json.dumps(
        {"objective": understanding.objective, "understanding": understanding.to_dict()},
        indent=2,
    )
    # Plan 159 F3: append ONLY the stack-relevant reference detail (Django/Monorepo/Docker
    # example or the inference table) just-in-time, keeping the always-loaded planner.md lean.
    user_message = _redacted(user_message) + stack_reference_snippet(
        understanding.repo_family, understanding.detected_files,
    )
    raw = llm_client(
        system_prompt=_spec_prompt("planner"),
        user_message=user_message,
    )
    payload = parse_plan_json(raw)
    if not isinstance(payload, list):
        raise ValueError("planner: expected a JSON array of candidates")
    candidates: list[_CandidateStep] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        cmd_raw = item.get("command")
        command = None if cmd_raw in (None, "", "null") else str(cmd_raw).strip()
        if command and _is_blocked_command(command):
            continue
        confidence = _coerce_float(item.get("confidence"), default=0.7)
        eta = _coerce_int(item.get("estimated_seconds"), default=DEFAULT_STEP_SECONDS)
        candidates.append(
            _CandidateStep(
                title=title[:160],
                command=command,
                confidence=max(0.0, min(1.0, confidence)),
                estimated_seconds=max(1, eta),
            )
        )
    if not candidates:
        raise ValueError("planner: no usable candidates returned")
    return tuple(candidates)


def _is_blocked_command(command: str) -> bool:
    assessment = assess_command(command)
    return bool(assessment.blocked) or assessment.safety_class == SafetyClass.S4


def _coerce_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: Any, *, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


# --- Stage 3: critique + ordering --------------------------------------------


def _critique_and_order(
    *,
    candidates: tuple[_CandidateStep, ...],
    understanding: RepoUnderstanding,
    llm_client: LLMClient,
) -> tuple[tuple[PlanStep, ...], tuple[str, ...], str]:
    user_message = json.dumps(
        {
            "objective": understanding.objective,
            "understanding": understanding.to_dict(),
            "candidates": [
                {
                    "title": c.title,
                    "command": c.command,
                    "confidence": c.confidence,
                    "estimated_seconds": c.estimated_seconds,
                }
                for c in candidates
            ],
        },
        indent=2,
    )
    raw = llm_client(
        system_prompt=_spec_prompt("critic"),
        user_message=_redacted(user_message),
    )
    payload = parse_plan_json(raw)
    if not isinstance(payload, dict):
        raise ValueError("critic: expected JSON object")
    ordered_raw = payload.get("ordered_steps") or []
    if not isinstance(ordered_raw, list) or not ordered_raw:
        raise ValueError("critic: ordered_steps is empty")
    dropped_raw = payload.get("dropped") or []
    reasoning = str(payload.get("reasoning") or "")
    steps: list[PlanStep] = []
    seen_indices: set[int] = set()
    for raw_step in ordered_raw:
        if not isinstance(raw_step, dict):
            continue
        index = _coerce_int(raw_step.get("index"), default=len(steps) + 1)
        if index in seen_indices:
            index = len(steps) + 1
        seen_indices.add(index)
        title = str(raw_step.get("title") or "")[:160].strip()
        if not title:
            continue
        cmd_raw = raw_step.get("command")
        command = None if cmd_raw in (None, "", "null") else str(cmd_raw).strip()
        if command and _is_blocked_command(command):
            continue
        depends_raw = raw_step.get("depends_on") or []
        depends = tuple(int(x) for x in depends_raw if isinstance(x, (int, float)) and int(x) < index)
        steps.append(
            PlanStep(
                index=index,
                title=title,
                description=str(raw_step.get("description") or "").strip(),
                command=command,
                safety_class="S0",  # filled in Stage 4
                verification=(None if raw_step.get("verification") in (None, "") else str(raw_step["verification"])),
                rationale=str(raw_step.get("rationale") or "").strip(),
                estimated_seconds=_coerce_int(raw_step.get("estimated_seconds"), default=DEFAULT_STEP_SECONDS),
                confidence=_coerce_float(raw_step.get("confidence"), default=0.7),
                depends_on=depends,
                origin="planner",
            )
        )
    if not steps:
        raise ValueError("critic: no usable steps after parsing")
    steps = _renumber_steps(steps)
    dropped: list[str] = []
    for item in dropped_raw:
        if isinstance(item, dict):
            title = str(item.get("title") or "").strip()
            reason = str(item.get("reason") or "").strip()
            if title:
                dropped.append(f"{title} — {reason}" if reason else title)
        elif isinstance(item, str):
            dropped.append(item)
    return tuple(steps), tuple(dropped), reasoning


def _steps_from_candidates(candidates: tuple["_CandidateStep", ...]) -> tuple[PlanStep, ...]:
    """Plan 70: deterministic fallback ordering when the critique LLM call
    fails. Turns proposed candidates into numbered steps (1..N) with a linear
    dependency chain, so a single successful propose call still yields a plan.
    Stage 4 (`_classify_and_verify`) reclassifies the real safety class.
    """
    out: list[PlanStep] = []
    for i, c in enumerate(candidates, start=1):
        out.append(
            PlanStep(
                index=i,
                title=c.title,
                description="",
                command=c.command,
                safety_class="S0",  # reclassified in Stage 4
                verification=None,
                rationale="Proposed by the planner.",
                estimated_seconds=c.estimated_seconds,
                confidence=c.confidence,
                depends_on=((i - 1,) if i > 1 else ()),
                origin="planner",
            )
        )
    return tuple(_renumber_steps(out))


def _assemble_ordered_steps(
    *,
    candidates: tuple["_CandidateStep", ...],
    understanding: RepoUnderstanding,
    llm_client: LLMClient,
) -> tuple[tuple[PlanStep, ...], tuple[str, ...], str]:
    """Critique + order the candidates; on ANY failure, fall back to a
    deterministic linear ordering so a critique hiccup never discards a good
    propose result. Returns ``(ordered_steps, dropped, reasoning)``."""
    try:
        return _critique_and_order(
            candidates=candidates, understanding=understanding, llm_client=llm_client
        )
    except Exception:
        return (
            _steps_from_candidates(candidates),
            (),
            "Critique stage unavailable; using the proposed steps in order.",
        )


def _renumber_steps(steps: list[PlanStep]) -> list[PlanStep]:
    """Renumber steps 1..N preserving depends_on relationships by old index."""
    old_to_new: dict[int, int] = {}
    for new_idx, step in enumerate(steps, start=1):
        old_to_new[step.index] = new_idx
    renumbered: list[PlanStep] = []
    for new_idx, step in enumerate(steps, start=1):
        new_deps = tuple(
            old_to_new[d]
            for d in step.depends_on
            if d in old_to_new and old_to_new[d] < new_idx
        )
        renumbered.append(replace(step, index=new_idx, depends_on=new_deps))
    return renumbered


# --- Stage 4: safety + verification (deterministic) --------------------------


_VERIFICATION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^\s*(npm|pnpm|yarn)\s+install\b"), "exit code 0; node_modules/ populated"),
    (re.compile(r"^\s*pip(\d+(\.\d+)*)?\s+install\b"), "exit code 0; package importable"),
    (re.compile(r"^\s*python(\d+(\.\d+)*)?\s+-m\s+pip\s+install\b"), "exit code 0"),
    (re.compile(r"^\s*cargo\s+(build|run)\b"), "exit code 0; binary produced"),
    (re.compile(r"^\s*go\s+(build|run)\b"), "exit code 0"),
    (re.compile(r"^\s*make\b"), "exit code 0"),
    (re.compile(r"--version\s*$"), "version string matches expected major"),
    (re.compile(r"^\s*(npm|yarn|pnpm)\s+run\s+(dev|start)\b"), "process stays alive; port reachable within 30s"),
    (re.compile(r"^\s*(npm|yarn|pnpm)\s+(test|run\s+test)\b"), "exit code 0; tests reported"),
    (re.compile(r"^\s*pytest\b"), "exit code 0; tests reported"),
    (re.compile(r"^\s*git\s+clone\b"), "directory exists, .git present"),
)


def _classify_and_verify(steps: tuple[PlanStep, ...]) -> tuple[tuple[PlanStep, ...], tuple[str, ...]]:
    """Stage 4: deterministic safety + verification fill. Returns (steps, blocked_titles)."""
    out: list[PlanStep] = []
    blocked: list[str] = []
    for step in steps:
        if step.command is None:
            out.append(replace(step, safety_class="S0"))
            continue
        assessment = assess_command(step.command)
        if assessment.blocked or assessment.safety_class == SafetyClass.S4:
            blocked.append(step.title)
            continue
        verification = step.verification or _default_verification(step.command)
        out.append(
            replace(
                step,
                safety_class=assessment.safety_class.value,
                verification=verification,
            )
        )
    return _renumber_steps(out), tuple(blocked)


def _default_verification(command: str) -> str | None:
    for pattern, hint in _VERIFICATION_PATTERNS:
        if pattern.search(command):
            return hint
    return "exit code 0"


# --- Stage 5: clarification gate ---------------------------------------------


def _collect_clarifications(
    *,
    steps: tuple[PlanStep, ...],
    understanding: RepoUnderstanding,
    llm_client: LLMClient,
) -> tuple[ClarificationQuestion, ...]:
    flagged_indices: list[int] = []
    for step in steps:
        if step.confidence < LOW_CONFIDENCE_THRESHOLD:
            flagged_indices.append(step.index)
            continue
        if step.command and not _references_known_runtime(step.command, understanding):
            flagged_indices.append(step.index)
    if understanding.needs_clarification:
        flagged_indices = list(set([1, *flagged_indices]))
    if not flagged_indices:
        return ()
    user_message = json.dumps(
        {
            "understanding": understanding.to_dict(),
            "steps": [s.to_dict() for s in steps],
            "flagged_step_indices": flagged_indices,
        },
        indent=2,
    )
    raw = llm_client(
        system_prompt=_spec_prompt("clarifier"),
        user_message=_redacted(user_message),
    )
    try:
        payload = parse_plan_json(raw)
    except ValueError:
        # Clarifier failure is not fatal — proceed with no questions.
        return ()
    if not isinstance(payload, dict):
        return ()
    questions_raw = payload.get("questions") or []
    if not isinstance(questions_raw, list):
        return ()
    questions: list[ClarificationQuestion] = []
    for q in questions_raw[:MAX_CLARIFICATION_QUESTIONS]:
        if not isinstance(q, dict):
            continue
        text = str(q.get("text") or "").strip()
        if not text:
            continue
        options = tuple(str(o) for o in (q.get("options") or ()) if str(o).strip())[:4]
        default = q.get("default")
        applies = tuple(
            int(x)
            for x in (q.get("applies_to_step_indices") or ())
            if isinstance(x, (int, float))
        )
        questions.append(
            ClarificationQuestion(
                text=text,
                options=options,
                default=None if default in (None, "") else str(default),
                applies_to_step_indices=applies,
            )
        )
    return filter_repeat_clarifications(tuple(questions))


def _references_known_runtime(command: str, understanding: RepoUnderstanding) -> bool:
    """Best-effort: True if the command's leading token is a detected runtime
    or a universally-available command."""
    tok = command.strip().split(None, 1)[0] if command.strip() else ""
    if not tok:
        return True
    base = tok.rsplit("/", 1)[-1]
    universal = {
        "cd", "ls", "echo", "cat", "mkdir", "rm", "cp", "mv", "test",
        "git", "curl", "wget", "tar", "unzip", "which", "env", "export",
        "sudo", "apt", "apt-get", "brew", "make", "bash", "sh",
    }
    if base in universal:
        return True
    if base in understanding.detected_runtimes:
        return True
    runtime_synonyms = {
        "npm": "node", "pnpm": "node", "yarn": "node", "node": "node",
        "pip": "python", "pip3": "python", "python": "python", "python3": "python",
        "cargo": "cargo", "rustc": "cargo",
        "go": "go",
    }
    canonical = runtime_synonyms.get(base)
    if canonical and canonical in understanding.detected_runtimes:
        return True
    return False


# --- Top-level entry: generate_plan ------------------------------------------


def generate_plan(
    *,
    objective: str,
    understanding: RepoUnderstanding,
    llm_client: LLMClient,
    mode: ControlMode | str,
    plan_id: str | None = None,
) -> PlanRecord:
    """Run the five-stage pipeline. Returns a PlanRecord.

    On LLM failure the returned record has status=PLAN_STATUS_FAILED with the
    failure reason in the risks list — callers still render it so the user
    sees what went wrong.
    """
    mode_value = mode.value if isinstance(mode, ControlMode) else str(mode)
    plan_id = plan_id or uuid.uuid4().hex
    created_at = _iso_now()
    context_summary = _summarize_understanding(understanding)

    try:
        candidates = _propose_candidates(understanding=understanding, llm_client=llm_client)
    except Exception as exc:  # pragma: no cover - resilience path
        return PlanRecord(
            plan_id=plan_id,
            objective=objective,
            context_summary=context_summary,
            steps=(),
            risks=(f"plan generation failed at proposal stage: {exc}",),
            rollback="",
            estimated_seconds=0,
            created_at=created_at,
            status=PLAN_STATUS_FAILED,
            repo_slug=understanding.repo_slug,
            mode_at_creation=mode_value,
            history=(f"{created_at}: failed at propose ({exc})",),
        )

    # Plan 70: critique is best-effort. A single successful propose call must
    # yield a plan even when the (often-flaky on small local models) critique
    # JSON call fails — we fall back to a deterministic linear ordering.
    ordered, dropped, reasoning = _assemble_ordered_steps(
        candidates=candidates,
        understanding=understanding,
        llm_client=llm_client,
    )

    classified, blocked = _classify_and_verify(ordered)
    if not classified:
        return PlanRecord(
            plan_id=plan_id,
            objective=objective,
            context_summary=context_summary,
            steps=(),
            risks=("all proposed steps were blocked as S4 destructive",) + tuple(f"blocked: {t}" for t in blocked),
            rollback="",
            estimated_seconds=0,
            created_at=created_at,
            status=PLAN_STATUS_FAILED,
            repo_slug=understanding.repo_slug,
            mode_at_creation=mode_value,
            history=(f"{created_at}: failed at classify (all S4)",),
        )

    clarifications: tuple[ClarificationQuestion, ...] = ()
    try:
        clarifications = _collect_clarifications(
            steps=classified, understanding=understanding, llm_client=llm_client
        )
    except Exception:
        clarifications = ()

    estimated_seconds = sum(s.estimated_seconds for s in classified)
    risks = _derive_risks(classified, understanding, blocked)
    rollback = _derive_rollback(classified, understanding)

    history = (f"{created_at}: plan generated ({len(classified)} steps)",)
    return PlanRecord(
        plan_id=plan_id,
        objective=objective,
        context_summary=context_summary,
        steps=classified,
        risks=risks,
        rollback=rollback,
        estimated_seconds=estimated_seconds,
        created_at=created_at,
        status=PLAN_STATUS_PENDING,
        repo_slug=understanding.repo_slug,
        mode_at_creation=mode_value,
        clarifications=clarifications,
        dropped_candidates=dropped,
        critic_reasoning=reasoning,
        amendment_count=0,
        history=history,
    )


def finalize_plan_from_steps(
    *,
    objective: str,
    repo_slug: str | None,
    context_summary: str,
    steps: tuple[PlanStep, ...],
    mode: ControlMode | str,
    understanding: RepoUnderstanding | None = None,
    plan_id: str | None = None,
    critic_reasoning: str = "",
    precheck_summary: str = "",
    target_label: str = "",
    required_env_keys: tuple[str, ...] = (),
) -> PlanRecord:
    """Plan 72 Phase 1: build a PlanRecord from a deterministic step backbone.

    Steps come from the per-family setup specialists (clone → install → build →
    run), so the plan is correct and complete WITHOUT relying on an LLM. Stage 4
    (`_classify_and_verify`) assigns real safety classes and dense 1..N numbering.
    """
    mode_value = mode.value if isinstance(mode, ControlMode) else str(mode)
    plan_id = plan_id or uuid.uuid4().hex
    created_at = _iso_now()

    classified, blocked = _classify_and_verify(steps)
    if not classified:
        return PlanRecord(
            plan_id=plan_id,
            objective=objective,
            context_summary=context_summary,
            steps=(),
            risks=("no runnable steps could be derived for this repo",)
            + tuple(f"blocked (S4): {t}" for t in blocked),
            rollback="",
            estimated_seconds=0,
            created_at=created_at,
            status=PLAN_STATUS_FAILED,
            repo_slug=repo_slug,
            mode_at_creation=mode_value,
            history=(f"{created_at}: no steps after classify",),
        )

    if understanding is not None:
        risks = _derive_risks(classified, understanding, blocked)
    else:
        risks = tuple(f"blocked (S4): {t}" for t in blocked)
    # Always derive a concrete rollback (guarded against a None understanding).
    rollback = _derive_rollback(classified, understanding)

    return PlanRecord(
        plan_id=plan_id,
        objective=objective,
        context_summary=context_summary,
        steps=classified,
        risks=risks,
        rollback=rollback,
        estimated_seconds=sum(s.estimated_seconds for s in classified),
        created_at=created_at,
        status=PLAN_STATUS_PENDING,
        repo_slug=repo_slug,
        mode_at_creation=mode_value,
        critic_reasoning=critic_reasoning,
        amendment_count=0,
        history=(f"{created_at}: plan built from deterministic specialist backbone ({len(classified)} steps)",),
        precheck_summary=precheck_summary,
        target_label=target_label,
        required_env_keys=required_env_keys,
    )


def _deterministic_supervisor_review(
    plan: PlanRecord, understanding: RepoUnderstanding | None
) -> CriticVerdict:
    """Plan 76 Fix B: a checklist-based supervisor review that ALWAYS returns a
    verdict + a concise (≤5 line) rationale. Used as the authoritative fallback
    so the supervisor NEVER 'fails'. Reviewing/approving the plan is its core
    job, with or without a model."""
    steps = plan.steps
    if not steps:
        return CriticVerdict(verdict="block", reason="The plan has no runnable steps.")
    if any(s.safety_class == "S4" for s in steps):
        return CriticVerdict(verdict="block", reason="The plan contains a destructive step and was blocked.")

    commands = [(s.command or "") for s in steps]
    clones_first = "git clone" in commands[0] if commands else False
    has_clone = any("git clone" in c for c in commands)

    def _is_run(cmd: str) -> bool:
        c = cmd.lower()
        # Any package manager's dev/start/serve/preview script counts as a run step
        # (npm run dev, pnpm dev, yarn start, bun run serve, …).
        if re.search(r"\b(npm|pnpm|yarn|bun)\s+(run\s+)?(dev|start|serve|preview|develop)\b", c):
            return True
        return any(k in c for k in ("uvicorn", "gunicorn", "flask run", "streamlit run",
                                    "cargo run", "go run", "compose up", "manage.py runserver",
                                    " serve", "next dev", "next start", "vite"))

    has_run = any(_is_run(c) for c in commands)
    family = getattr(understanding, "repo_family", None) or (plan.context_summary.split(";")[0] if plan.context_summary else "this stack")
    family = str(family).replace("family=", "").strip() or "this stack"
    run_cmd = next((c for c in reversed(commands) if _is_run(c)), commands[-1] if commands else "")

    if not has_run:
        return CriticVerdict(
            verdict="revise",
            reason=(
                f"Checked a {family} plan: dependencies install but there is NO run/start step, "
                "so the repo would never actually start. Add a final run step (e.g. the README's "
                "dev/start command) before approving."
            ),
        )

    # Plan 78 Fix C: deeper checks on MUTATING (S2+) steps — these change the
    # system, so they must be verifiable, targeted, OS-correct, and not a repeat
    # of a known failure. Each returns `revise` with the specific offending step.
    from duckln.safety import assess_command as _assess

    def _is_mutating(s: PlanStep) -> bool:
        return str(s.safety_class or "").upper() in ("S2", "S3")

    recent_failures = tuple(getattr(understanding, "recent_failures", ()) or ())

    # Plan 79 L1: the install command MUST match the lockfile present in the repo
    # (e.g. don't run `npm install` when only `pnpm-lock.yaml` exists) — using the
    # wrong package manager ignores the locked dependency graph and often fails.
    detected_files = {str(f).lower() for f in (getattr(understanding, "detected_files", ()) or ())}
    lock_to_pm = {"pnpm-lock.yaml": "pnpm", "yarn.lock": "yarn", "package-lock.json": "npm", "bun.lockb": "bun"}
    present_locks = [lock for lock in lock_to_pm if lock in detected_files]
    if len(present_locks) == 1:
        expected_pm = lock_to_pm[present_locks[0]]
        other_pms = {"npm", "pnpm", "yarn", "bun"} - {expected_pm}
        for s in steps:
            c = (s.command or "").lower()
            is_install = any(k in c for k in (" install", " ci", " i ")) or c.endswith(" install") or c.endswith(" ci")
            if not is_install:
                continue
            used = next((pm for pm in other_pms if re.search(rf"\b{pm}\b", c)), None)
            if used and not re.search(rf"\b{expected_pm}\b", c):
                return CriticVerdict(
                    verdict="revise",
                    reason=(
                        f"Step {s.index} installs with `{used}` but the repo has a "
                        f"{present_locks[0]} lockfile — use `{expected_pm}` so the locked "
                        "dependency versions are honored."
                    ),
                )

    for s in steps:
        cmd = (s.command or "").strip()
        if not cmd:
            continue
        if _is_mutating(s):
            if not (s.verification or "").strip():
                return CriticVerdict(
                    verdict="revise",
                    reason=(
                        f"Step {s.index} (`{cmd[:50]}`) changes the system but has no verification "
                        "command — add a check that proves it worked before approving."
                    ),
                )
            if not (getattr(s, "target", "") or "").strip():
                return CriticVerdict(
                    verdict="revise",
                    reason=(
                        f"Step {s.index} (`{cmd[:50]}`) has no clear target (local/vm/container/cloud) — "
                        "set the target so the command runs where intended."
                    ),
                )
        # Wrong-OS command (e.g. brew on a Linux VM) — reuse the safety assessor.
        os_assessment = _assess(cmd, execution_target=(getattr(s, "target", "") or None))
        if os_assessment.blocked and "only" in os_assessment.reason.lower():
            return CriticVerdict(
                verdict="revise",
                reason=f"Step {s.index} uses a wrong-OS command: {os_assessment.reason}",
            )
        # Duplicate of a known failure — don't repeat a command that already failed.
        if recent_failures and any(cmd and cmd in f for f in recent_failures):
            return CriticVerdict(
                verdict="revise",
                reason=(
                    f"Step {s.index} (`{cmd[:50]}`) matches a command that already failed for this "
                    "repo — change the approach instead of repeating it."
                ),
            )

    # All key checks pass → approve with a concise rationale.
    checks = []
    checks.append("clones the repo first" if clones_first else ("clones the repo" if has_clone else "uses an existing checkout"))
    checks.append("installs dependencies before running")
    checks.append(f"ends by running it (`{run_cmd.strip()[:50]}`)")
    checks.append("no destructive commands")
    checks.append(f"prerequisites scoped to {family}")
    reason = "Checked: " + "; ".join(checks) + ". This is a sound, complete setup path."
    return CriticVerdict(verdict="approve", reason=reason)


MODEL_UNREACHABLE_BLOCKER = "model_unreachable"
# Plan 160 Phase B: the model is REACHABLE but couldn't deliver a valid decision within its
# retry budget (too slow / too small) — an honest-stop distinct from "can't connect".
MODEL_UNRESPONSIVE_BLOCKER = "model_unresponsive"
# Plan 158: provider-NEUTRAL default (no Ollama-only example). The user-facing display sites
# tailor the hint per provider via `model_unreachable_message(provider)`.
MODEL_UNREACHABLE_MESSAGE = (
    "Duckln is not able to connect to the model. Check your provider/model is configured "
    "correctly and reachable, then try again."
)


def model_unreachable_message(provider: str | None) -> str:
    """Plan 158: a model-unreachable hint TAILORED to the configured provider — so an
    OpenRouter/OpenAI/Anthropic user isn't told to run `ollama serve` (a cloud API has
    nothing to serve locally), and a free/rate-limited model surfaces 429 as the likely cause."""
    p = str(provider or "").strip().lower()
    if p == "ollama":
        return (
            "Duckln is not able to connect to the model. Ensure your local Ollama runtime is "
            "running (`ollama serve`) and the model is pulled and reachable, then try again."
        )
    if p in ("openrouter", "openai", "anthropic", "google", "gemini"):
        try:
            from duckln.ai_client import Provider

            label = Provider(p if p != "gemini" else "google").label
        except Exception:
            label = (provider or "your provider").strip() or "your provider"
        return (
            f"Duckln is not able to connect to the model. Check your {label} API key is valid "
            "and your network is reachable. A free or rate-limited model can be temporarily "
            "unavailable (HTTP 429) — wait and retry, or switch to an available model. Then try again."
        )
    return MODEL_UNREACHABLE_MESSAGE


def _model_connection_error(exc: BaseException) -> bool:
    """Plan 78 Fix A / Plan 159 F1: True ONLY when the model could not be REACHED
    (CONNECT failure: down / refused / DNS / no route). A READ/response TIMEOUT from a
    server that ACCEPTED the connection — a slow or weak model, or a prompt that overflows
    its context — is reachable-but-slow, NOT unreachable, so it must return False (the caller
    falls back to the deterministic review + an honest 'model too weak' note, never 'can't
    connect'). A reachable-but-garbage/HTTP-error response is also False."""
    import socket

    # Adapters wrap the original transport error (e.g. ProviderRequestError from httpx.ReadTimeout),
    # so inspect the whole __cause__/__context__ chain, not just the top exception.
    chain: list[BaseException] = []
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        chain.append(cur)
        cur = cur.__cause__ or cur.__context__

    # Plan 159/170: only a failure to ESTABLISH the connection is "unreachable". A READ/WRITE
    # timeout, OR a connection RESET/abort/broken-pipe MID-REQUEST, means the server WAS reached
    # and the request itself failed (slow/weak model, overloaded, crashed) — reachable-but-failed,
    # NOT "run ollama serve". Those return False (caller → honest "reachable but couldn't complete").
    try:
        import httpx

        for e in chain:
            if isinstance(e, (
                httpx.ReadTimeout, httpx.PoolTimeout, httpx.WriteTimeout,
                httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError,
            )):
                return False
            if isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout)):
                return True
    except Exception:
        pass
    for e in chain:
        # CONNECT-establishment failure → truly unreachable.
        if isinstance(e, ConnectionRefusedError):
            return True
        # Mid-request drop or a (read) timeout from a server we reached → reachable-but-failed.
        if isinstance(e, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, TimeoutError, socket.timeout)):
            return False
    text = " ".join(f"{type(e).__name__}: {e}" for e in chain).lower()
    # CONNECT-establishment indicators ONLY (couldn't reach the server at all).
    unreachable_needles = (
        "connection refused", "could not connect", "failed to connect",
        "max retries exceeded", "name or service not known",
        "nodename nor servname", "no route to host",
    )
    if any(n in text for n in unreachable_needles):
        return True
    # A drop on a server we reached → reachable-but-failed (NOT "unreachable").
    reachable_needles = (
        "connection reset", "connection aborted", "broken pipe",
        "server disconnected", "peer closed connection", "remotedisconnected",
    )
    if any(n in text for n in reachable_needles):
        return False
    # URLError or anything else unrecognized: a urllib connect failure carries a needle above;
    # otherwise assume reachable (the critic's contract check surfaces a real problem honestly).
    return False


def critic_review(
    *,
    plan: PlanRecord,
    understanding: RepoUnderstanding | None,
    llm_client: LLMClient | None,
    config_dir=None,
    model_id: str = "",
) -> CriticVerdict:
    """Plan 72/76/79 + Plan 160: the SUPERVISOR. The LLM is the decision-maker.

    Deterministic logic is only a SAFETY FLAG + the no-model structural floor — it never
    APPROVES a plan in the LLM's place (Plan 160). When a model is present:
    - a genuine CONNECT failure → honest-stop (`model_unreachable`);
    - a reachable model that times out or returns garbage AFTER its retry budget → honest-stop
      (`model_unresponsive`: "reachable but too slow/weak — retry or use a stronger model"),
      NEVER a silent deterministic approve.
    When `llm_client is None` it returns the deterministic STRUCTURAL verdict (a pure function
    used by tests + as the floor); the bring-up caller — gated by `reasoning_enabled()` —
    honest-stops when a model is required but absent (Plan 160 Phase C). `config_dir`/`model_id`
    drive the per-model retry budget (Plan 160 D1) + the provider-aware message."""
    # Deterministic SAFETY FLAGS only (never the decision): an empty plan can't run.
    if not plan.steps:
        return CriticVerdict(verdict="block", reason="The plan has no runnable steps.")
    # Plan 160 Phase A/B: a destructive (S4) step is NOT auto-blocked here — the USER is the
    # final authority and decides at execution (Yes/No/Approve-all-this-session). The critic
    # records the destructive step on the plan for the user to see, rather than refusing it.

    deterministic = _deterministic_supervisor_review(plan, understanding)

    if llm_client is None:
        return deterministic

    payload = {
        "objective": plan.objective,
        "understanding": understanding.to_dict() if understanding is not None else {},
        "steps": [s.to_dict() for s in plan.steps],
    }
    user_message = _redacted(json.dumps(payload, indent=2))

    # Plan 160 D1: the retry budget is the verdict spec's max_contract_retries, REDUCED for a
    # probe-weak model so it honest-stops fast instead of burning the full budget.
    attempts = 2
    try:
        from duckln.harness.agent_def import builtin_agents_directory, load_agent_definition_from_path
        from duckln.recovery import effective_contract_retries

        _vdef = load_agent_definition_from_path(builtin_agents_directory() / "verdict.md")
        attempts = 1 + effective_contract_retries(_vdef, model_id=model_id, config_dir=config_dir)
    except Exception:
        attempts = 2

    def _unreach_msg() -> str:
        try:
            if config_dir is not None:
                from state.access import read_config_snapshot

                return model_unreachable_message(read_config_snapshot(config_dir).get("provider"))
        except Exception:
            pass
        return MODEL_UNREACHABLE_MESSAGE

    data: dict | None = None
    for _attempt in range(max(1, attempts)):
        try:
            raw = llm_client(system_prompt=_spec_prompt("verdict"), user_message=user_message)
            parsed = parse_plan_json(raw)
        except Exception as exc:
            if _model_connection_error(exc):
                return CriticVerdict(verdict="block", reason=_unreach_msg(), external_blocker=MODEL_UNREACHABLE_BLOCKER)
            continue  # reachable-but-slow/garbage → retry within budget
        if isinstance(parsed, dict) and str(parsed.get("verdict") or "").strip().lower() in (
            "approve", "revise", "block"
        ):
            data = parsed
            break
        # malformed/garbage → retry within budget

    # Plan 162 F6 (Plan 160-D2): record a capability observation for this REACHABLE model —
    # a valid review is positive, an unusable one negative. N-of-M consecutive positives
    # promote a probe-`weak` model to `capable`; one lucky pass is not enough. (The
    # connection-error path returned early above, so unreachable is never counted as weak.)
    if config_dir is not None and model_id:
        try:
            from duckln.recovery import record_capability_observation

            record_capability_observation(config_dir, model_id, delivered_valid=(data is not None))
        except Exception:
            pass

    if data is None:
        # Plan 160 Phase B: the model was REACHABLE but couldn't deliver a valid review within
        # its budget → honest-stop. The LLM is the decision-maker; deterministic does NOT
        # approve in its place.
        return CriticVerdict(
            verdict="block",
            reason=(
                "The model is reachable but couldn't produce a usable plan review (it may be too "
                "slow or too small) — retry, or configure a stronger model."
            ),
            external_blocker=MODEL_UNRESPONSIVE_BLOCKER,
        )

    verdict = str(data.get("verdict") or "").strip().lower()
    reason = str(data.get("reason") or "").strip() or deterministic.reason
    return CriticVerdict(
        verdict=verdict,
        reason=reason,
        missing_question=(str(data["missing_question"]).strip() if data.get("missing_question") else None),
        external_blocker=(str(data["external_blocker"]).strip() if data.get("external_blocker") else None),
    )


def _summarize_understanding(u: RepoUnderstanding) -> str:
    parts = [
        f"family={u.repo_family}",
        f"os={u.os_name or 'unknown'}/{u.arch or 'unknown'}",
        f"runtimes={','.join(u.detected_runtimes) or 'none'}",
        f"files={len(u.detected_files)}",
        f"target={u.execution_target}",
        f"mode={u.control_mode}",
    ]
    if u.recent_failures:
        parts.append(f"prior_failures={len(u.recent_failures)}")
    return "; ".join(parts)


def _derive_risks(
    steps: tuple[PlanStep, ...],
    understanding: RepoUnderstanding,
    blocked: tuple[str, ...],
) -> tuple[str, ...]:
    out: list[str] = []
    s3 = [s for s in steps if s.safety_class == "S3"]
    if s3:
        out.append(f"{len(s3)} step(s) at S3 require approval before execution")
    if any(s.command and "sudo" in (s.command or "") for s in steps):
        out.append("one or more steps require sudo")
    if any(s.command and "apt" in (s.command or "") for s in steps):
        out.append("system package installations will modify the host")
    if blocked:
        out.append(f"{len(blocked)} candidate step(s) blocked as destructive — dropped from plan")
    if understanding.recent_failures:
        out.append(f"this repo has {len(understanding.recent_failures)} recorded prior failure(s) — see memory/failures/")
    return tuple(out)


def _derive_rollback(steps: tuple[PlanStep, ...], understanding: RepoUnderstanding) -> str:
    """Plan 73: produce a CONCRETE rollback for any repo. The baseline always
    removes whatever was cloned (which undoes the whole setup), plus targeted
    ecosystem cleanups. Never returns a vague 'no rollback'."""
    hints: list[str] = []
    commands_list = [s.command or "" for s in steps]
    commands = " ".join(commands_list).lower()

    # Baseline: removing the cloned project directory reverses the entire setup.
    clone_dir = None
    for cmd in commands_list:
        if "git clone" not in (cmd or ""):
            continue
        tokens = cmd.split()
        # `git clone [flags] <url> <dir>` — the destination is the last token,
        # but only when an explicit dir was given (more than just the URL).
        if len(tokens) >= 4 and not tokens[-1].startswith("-"):
            clone_dir = tokens[-1]
        break
    if clone_dir:
        hints.append(f"rm -rf {clone_dir}   # remove the cloned repo (undoes the whole setup)")
    elif understanding is not None and getattr(understanding, "repo_path", None):
        hints.append(f"rm -rf {understanding.repo_path}   # remove the project directory")

    # Targeted ecosystem cleanups.
    if any(k in commands for k in ("npm install", "npm ci", "pnpm install", "yarn install")):
        hints.append("rm -rf node_modules package-lock.json")
    if "venv" in commands or "pip install" in commands:
        hints.append("rm -rf .venv venv")
    if "cargo build" in commands or "cargo run" in commands:
        hints.append("cargo clean")
    if "make" in commands:
        hints.append("make clean   # if a clean target exists")
    if "docker build" in commands or "docker compose up" in commands or "docker run" in commands:
        slug = re.sub(r"[^a-z0-9_.-]+", "-", (getattr(understanding, "repo_slug", None) or "app").split("/")[-1].lower())
        hints.append(f"docker compose down -v   # or: docker rmi {slug}")

    if not hints:
        # Even with no recognised commands, give an actionable rollback.
        return "Stop the process (Ctrl-C) and remove the cloned project directory to revert."
    return " ; ".join(hints)


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


# --- Markdown round-trip (/plan edit) ----------------------------------------


def render_plan_markdown(plan: PlanRecord) -> str:
    """Render a PlanRecord as a self-contained markdown document.

    The format is round-trippable by `parse_plan_markdown` — used by
    `/plan edit` so the user can revise steps in $EDITOR and save back.
    """
    lines: list[str] = []
    lines.append(f"# Plan: {plan.objective}")
    lines.append("")
    lines.append("```yaml")
    lines.append(f"plan_id: {plan.plan_id}")
    lines.append(f"status: {plan.status}")
    lines.append(f"created_at: {plan.created_at}")
    lines.append(f"repo_slug: {plan.repo_slug or ''}")
    lines.append(f"mode_at_creation: {plan.mode_at_creation}")
    lines.append(f"estimated_seconds: {plan.estimated_seconds}")
    lines.append(f"amendment_count: {plan.amendment_count}")
    lines.append("```")
    lines.append("")
    lines.append("## Context")
    lines.append("")
    lines.append(plan.context_summary or "(none)")
    lines.append("")
    lines.append("## Steps")
    lines.append("")
    for step in plan.steps:
        lines.append(f"### Step {step.index} — {step.title}")
        lines.append("")
        lines.append(f"- **safety_class:** {step.safety_class}")
        lines.append(f"- **estimated_seconds:** {step.estimated_seconds}")
        lines.append(f"- **confidence:** {step.confidence:.2f}")
        lines.append(f"- **depends_on:** {list(step.depends_on)}")
        lines.append(f"- **origin:** {step.origin}")
        if step.command is not None:
            lines.append("- **command:**")
            lines.append("  ```sh")
            lines.append(f"  {step.command}")
            lines.append("  ```")
        else:
            lines.append("- **command:** (explanation only — no shell command)")
        if step.verification:
            lines.append(f"- **verification:** {step.verification}")
        lines.append("")
        if step.rationale:
            lines.append(f"_Rationale_: {step.rationale}")
            lines.append("")
        if step.description:
            lines.append(step.description)
            lines.append("")
    lines.append("## Risks")
    lines.append("")
    if plan.risks:
        for risk in plan.risks:
            lines.append(f"- {risk}")
    else:
        lines.append("- (none identified)")
    lines.append("")
    lines.append("## Rollback")
    lines.append("")
    lines.append(plan.rollback or "(none)")
    lines.append("")
    if plan.dropped_candidates:
        lines.append("## Considered & dropped")
        lines.append("")
        for d in plan.dropped_candidates:
            lines.append(f"- {d}")
        lines.append("")
    if plan.clarifications:
        lines.append("## Open clarifications")
        lines.append("")
        for q in plan.clarifications:
            lines.append(f"- {q.text}  (options: {', '.join(q.options) if q.options else 'free text'})")
        lines.append("")
    if plan.critic_reasoning:
        lines.append("## Reasoning")
        lines.append("")
        lines.append(plan.critic_reasoning)
        lines.append("")
    if plan.history:
        lines.append("## History")
        lines.append("")
        for h in plan.history:
            lines.append(f"- {h}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


_PLAN_HEADER_RE = re.compile(r"^# Plan: (.+)$", re.MULTILINE)
_PLAN_YAML_BLOCK_RE = re.compile(r"^```yaml\n(.*?)\n```", re.DOTALL | re.MULTILINE)
_PLAN_STEP_RE = re.compile(
    r"^### Step (\d+) — (.+?)\n(.*?)(?=^### Step |\Z)",
    re.DOTALL | re.MULTILINE,
)
_PLAN_STEP_COMMAND_RE = re.compile(r"^\s*-\s*\*\*command:\*\*\s*\n\s*```sh\n(.*?)\n\s*```", re.DOTALL | re.MULTILINE)


def parse_plan_markdown(text: str) -> PlanRecord:
    """Parse a markdown document produced by `render_plan_markdown` back to a PlanRecord.

    Used by `/plan edit` to read user revisions.
    """
    objective_match = _PLAN_HEADER_RE.search(text)
    if not objective_match:
        raise ValueError("plan markdown missing '# Plan: ...' header")
    objective = objective_match.group(1).strip()

    yaml_match = _PLAN_YAML_BLOCK_RE.search(text)
    if not yaml_match:
        raise ValueError("plan markdown missing yaml metadata block")
    meta = _parse_kv_yaml(yaml_match.group(1))

    steps: list[PlanStep] = []
    for step_match in _PLAN_STEP_RE.finditer(text):
        idx = int(step_match.group(1))
        title = step_match.group(2).strip()
        body = step_match.group(3)
        kv = _parse_step_kv(body)
        command_match = _PLAN_STEP_COMMAND_RE.search(body)
        command = command_match.group(1).strip() if command_match else None
        if command == "":
            command = None
        rationale = _extract_inline(body, r"_Rationale_:\s*(.+?)$")
        description = _extract_description(body)
        steps.append(
            PlanStep(
                index=idx,
                title=title,
                description=description,
                command=command,
                safety_class=str(kv.get("safety_class") or "S2"),
                verification=(None if not kv.get("verification") else str(kv["verification"])),
                rationale=rationale,
                estimated_seconds=_coerce_int(kv.get("estimated_seconds"), default=DEFAULT_STEP_SECONDS),
                confidence=_coerce_float(kv.get("confidence"), default=1.0),
                depends_on=tuple(_parse_depends_on(kv.get("depends_on"))),
                origin=str(kv.get("origin") or "user"),
            )
        )

    risks = tuple(_parse_bullet_list(text, "## Risks"))
    rollback = _parse_section_block(text, "## Rollback") or ""
    dropped = tuple(_parse_bullet_list(text, "## Considered & dropped"))
    reasoning = _parse_section_block(text, "## Reasoning") or ""
    history = tuple(_parse_bullet_list(text, "## History"))
    context_summary = _parse_section_block(text, "## Context") or ""

    plan = PlanRecord(
        plan_id=str(meta.get("plan_id") or uuid.uuid4().hex),
        objective=objective,
        context_summary=context_summary,
        steps=tuple(_renumber_steps(steps)),
        risks=risks,
        rollback=rollback,
        estimated_seconds=_coerce_int(meta.get("estimated_seconds"), default=sum(s.estimated_seconds for s in steps)),
        created_at=str(meta.get("created_at") or _iso_now()),
        status=PLAN_STATUS_EDITED,
        repo_slug=(str(meta.get("repo_slug")) if meta.get("repo_slug") else None),
        mode_at_creation=str(meta.get("mode_at_creation") or "hitl"),
        clarifications=(),
        dropped_candidates=dropped,
        critic_reasoning=reasoning,
        amendment_count=_coerce_int(meta.get("amendment_count"), default=0),
        history=history,
    )
    issues = validate_plan_dict(plan.to_dict())
    if issues:
        raise ValueError("edited plan failed validation: " + "; ".join(issues))
    return plan


def _parse_kv_yaml(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip()
    return out


def _parse_step_kv(body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in body.splitlines():
        m = re.match(r"\s*-\s*\*\*(\w+):\*\*\s*(.+)$", line)
        if not m:
            continue
        key = m.group(1).strip()
        value = m.group(2).strip()
        if key == "command":
            # command body is in the following ```sh block; skip the inline placeholder.
            continue
        out[key] = value
    return out


def _extract_inline(body: str, pattern: str) -> str:
    m = re.search(pattern, body, re.MULTILINE)
    if not m:
        return ""
    return m.group(1).strip()


def _extract_description(body: str) -> str:
    lines: list[str] = []
    capture = False
    for line in body.splitlines():
        if line.startswith("_Rationale_"):
            capture = True
            continue
        if capture:
            stripped = line.strip()
            if not stripped:
                if lines:
                    break
                continue
            lines.append(stripped)
    return " ".join(lines).strip()


def _parse_bullet_list(text: str, heading: str) -> list[str]:
    block = _parse_section_block(text, heading) or ""
    out: list[str] = []
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("- ") or stripped.startswith("* "):
            out.append(stripped[2:].strip())
    return out


def _parse_section_block(text: str, heading: str) -> str | None:
    escaped = re.escape(heading)
    pattern = re.compile(rf"^{escaped}\n(.*?)(?=^## |\Z)", re.DOTALL | re.MULTILINE)
    m = pattern.search(text)
    if not m:
        return None
    return m.group(1).strip()


def _parse_depends_on(raw: Any) -> list[int]:
    if raw is None:
        return []
    text = str(raw).strip().strip("[]")
    if not text:
        return []
    out: list[int] = []
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            out.append(int(item))
        except ValueError:
            continue
    return out


# --- Validation --------------------------------------------------------------


def validate_plan_dict(payload: dict[str, Any]) -> list[str]:
    """Return a list of issue strings; empty list means the plan is valid."""
    issues: list[str] = []
    if not payload.get("plan_id"):
        issues.append("missing plan_id")
    if not payload.get("objective"):
        issues.append("missing objective")
    steps = payload.get("steps") or []
    if not isinstance(steps, list) or not steps:
        issues.append("plan has no steps")
        return issues
    seen_indices: set[int] = set()
    for i, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            issues.append(f"step {i}: not an object")
            continue
        idx = step.get("index")
        if not isinstance(idx, int) or idx <= 0:
            issues.append(f"step {i}: missing/invalid index")
        elif idx in seen_indices:
            issues.append(f"step {i}: duplicate index {idx}")
        else:
            seen_indices.add(idx)
        if not step.get("title"):
            issues.append(f"step {i}: missing title")
        sclass = step.get("safety_class")
        if sclass not in ("S0", "S1", "S2", "S3"):
            issues.append(f"step {i}: invalid safety_class {sclass!r} (S4 not allowed)")
        cmd = step.get("command")
        if cmd and _is_blocked_command(str(cmd)):
            issues.append(f"step {i}: command blocked by safety policy")
        deps = step.get("depends_on") or []
        if not isinstance(deps, list):
            issues.append(f"step {i}: depends_on is not a list")
        else:
            for d in deps:
                if not isinstance(d, int) or d >= (idx if isinstance(idx, int) else i):
                    issues.append(f"step {i}: depends_on must reference earlier indices only")
                    break
    return issues


# --- Plan -> RepoBringUpStep adapter -----------------------------------------


def plan_to_bringup_steps(plan: PlanRecord) -> tuple[Any, ...]:
    """Convert PlanRecord.steps into a tuple of RepoBringUpStep instances.

    Imported lazily to avoid a circular import (repo_bringup imports plan_mode
    indirectly via main.py wiring).
    """
    from duckln.repo_bringup import RepoBringUpStep

    out: list[Any] = []
    for step in plan.steps:
        if step.command is None:
            continue
        out.append(
            RepoBringUpStep(
                purpose=step.title,
                command=step.command,
                verification_command=None,
                source="plan_mode",
            )
        )
    return tuple(out)


# --- Failure attribution + amendment -----------------------------------------


ATTRIBUTION_MAX_TOKENS = 1024
_STRICT_JSON_SUFFIX = (
    "\n\nReturn ONLY a single valid JSON object with keys \"cause\" (string) and "
    "\"fix\" (object or null). No markdown, no code fences, no prose before or after."
)


def _call_llm_flexible(llm_client, *, system_prompt: str, user_message: str, max_tokens: int, json_mode: bool) -> str:
    """Plan 119: call an LLMClient asking for JSON mode + a bigger budget, but
    degrade gracefully to the plain 2-kwarg call for fakes/clients that don't
    accept the extra controls (keeps tests and older clients working)."""
    try:
        return llm_client(
            system_prompt=system_prompt,
            user_message=user_message,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )
    except TypeError:
        return llm_client(system_prompt=system_prompt, user_message=user_message)


def _attribution_llm_call(llm_client, *, system: str, user_message: str) -> Any:
    """Get a parsed {cause, fix} object from the model — JSON mode, large budget,
    one strict reprompt on parse failure, then truncation repair via parse_plan_json."""
    raw = _call_llm_flexible(
        llm_client, system_prompt=system, user_message=user_message,
        max_tokens=ATTRIBUTION_MAX_TOKENS, json_mode=True,
    )
    try:
        return parse_plan_json(raw)
    except ValueError:
        # One strict retry — append an explicit "JSON only" instruction. This
        # rescues reasoning models that emitted prose before the JSON.
        retry_raw = _call_llm_flexible(
            llm_client, system_prompt=system, user_message=user_message + _STRICT_JSON_SUFFIX,
            max_tokens=ATTRIBUTION_MAX_TOKENS, json_mode=True,
        )
        return parse_plan_json(retry_raw)


def attribute_failure(
    *,
    plan: PlanRecord,
    failed_step: PlanStep,
    stderr: str,
    stdout: str,
    exit_code: int | None,
    understanding: RepoUnderstanding,
    llm_client: LLMClient,
    web_evidence: str | None = None,
) -> AttributionResult:
    """Ask the LLM to produce a cause + a fix for a failed step.

    Returns AttributionResult — caller decides whether to amend the plan.

    Plan 77 Fix 4: when ``web_evidence`` (a redacted excerpt from a public web
    search of the exact error) is supplied, it is passed to the model so it can
    propose a concrete fix grounded in that evidence after a first empty pass.
    """
    request: dict[str, object] = {
        "objective": plan.objective,
        "understanding": understanding.to_dict(),
        "failed_step": failed_step.to_dict(),
        "stderr_excerpt": (stderr or "")[-2000:],
        "stdout_excerpt": (stdout or "")[-500:],
        "exit_code": exit_code,
    }
    if web_evidence:
        request["web_evidence"] = web_evidence[:1500]
    user_message = json.dumps(request, indent=2)
    # Plan 119: the attribution answer is structured JSON, so request JSON mode +
    # a generous token budget (220 truncated it before) and RETRY once with a
    # strict reprompt before giving up. This is why Duckln previously "got stuck":
    # a single, budget-starved, free-text shot whose JSON couldn't be parsed.
    try:
        payload = _attribution_llm_call(llm_client, system=_spec_prompt("attributor"), user_message=_redacted(user_message))
    except Exception as exc:
        return AttributionResult(
            cause=f"Step failed; attribution call did not return a usable response ({exc}).",
            fix_step=None,
            confidence=0.0,
        )
    if not isinstance(payload, dict):
        return AttributionResult(cause="attribution: malformed response", fix_step=None, confidence=0.0)
    cause = str(payload.get("cause") or "").strip()
    if not cause:
        cause = "Step failed; root cause could not be determined from available output."
    fix_raw = payload.get("fix")
    if not isinstance(fix_raw, dict):
        return AttributionResult(cause=cause, fix_step=None, confidence=0.4)
    fix_command = fix_raw.get("command")
    fix_command = None if fix_command in (None, "", "null") else str(fix_command).strip()
    if fix_command and _is_blocked_command(fix_command):
        return AttributionResult(
            cause=cause + " (proposed fix was destructive and rejected)",
            fix_step=None,
            confidence=0.2,
        )
    sclass = str(fix_raw.get("safety_class") or "S2")
    if sclass not in ("S0", "S1", "S2", "S3"):
        sclass = "S2"
    # Plan 78 Fix E: carry target + cwd onto the amendment so it survives the
    # supervisor re-review (which checks mutating steps have a target/verify).
    fix_verification = None if not fix_raw.get("verification") else str(fix_raw["verification"])
    if fix_verification is None and sclass in ("S2", "S3"):
        fix_verification = "command exits 0"
    fix_step = PlanStep(
        index=0,
        title=str(fix_raw.get("title") or "Recovery step")[:160],
        description=str(fix_raw.get("description") or "").strip(),
        command=fix_command,
        safety_class=sclass,
        verification=fix_verification,
        rationale=str(fix_raw.get("rationale") or "").strip(),
        estimated_seconds=_coerce_int(fix_raw.get("estimated_seconds"), default=DEFAULT_STEP_SECONDS),
        confidence=0.8,
        depends_on=(),
        origin="amendment",
        target=str(getattr(understanding, "execution_target", "") or ""),
        source="duckln-recovery",
        cwd=str(getattr(failed_step, "cwd", "") or ""),
    )
    return AttributionResult(cause=cause, fix_step=fix_step, confidence=0.8)


def amend_plan(*, plan: PlanRecord, fix_step: PlanStep, before_index: int, cause: str) -> PlanRecord:
    """Produce a new PlanRecord with `fix_step` inserted before the given index.

    The amendment increments amendment_count and appends a history entry.
    Returns a fresh PlanRecord with status=PLAN_STATUS_AMENDED.
    """
    new_steps: list[PlanStep] = []
    for step in plan.steps:
        if step.index == before_index:
            new_steps.append(fix_step)
        new_steps.append(step)
    if before_index > len(plan.steps):
        new_steps.append(fix_step)
    renumbered = _renumber_steps(new_steps)
    history = plan.history + (f"{_iso_now()}: amended — {cause[:160]}",)
    return replace(
        plan,
        steps=tuple(renumbered),
        status=PLAN_STATUS_AMENDED,
        amendment_count=plan.amendment_count + 1,
        history=history,
        estimated_seconds=sum(s.estimated_seconds for s in renumbered),
    )


def mark_status(plan: PlanRecord, new_status: str, note: str = "") -> PlanRecord:
    """Return a new PlanRecord with status updated and a history line appended."""
    history = plan.history + (f"{_iso_now()}: status={new_status}{(' — ' + note) if note else ''}",)
    return replace(plan, status=new_status, history=history)


# --- Plan markdown directory helpers -----------------------------------------


def plan_markdown_path(config_dir: Path, plan_id: str) -> Path:
    """Resolve the on-disk markdown path for a plan."""
    return config_dir / "memory" / PLAN_DIR_NAME / f"{plan_id}.md"


def plan_history_path(config_dir: Path) -> Path:
    """Resolve the on-disk history.md path."""
    return config_dir / "memory" / PLAN_DIR_NAME / PLAN_HISTORY_FILE_NAME


def ensure_plan_dir(config_dir: Path) -> Path:
    """Create memory/plans/ if missing; return the path."""
    plan_dir = config_dir / "memory" / PLAN_DIR_NAME
    plan_dir.mkdir(parents=True, exist_ok=True)
    return plan_dir


# --- Conversation-loop hook --------------------------------------------------


_MULTISTEP_VERBS: tuple[str, ...] = (
    "set up", "setup", "install", "run", "deploy", "configure",
    "build", "launch", "start", "bring up", "bringup",
)


def clarification_signature(question: "ClarificationQuestion") -> str:
    """Plan 72 Phase 9: normalized identity of a clarification question so an
    identical one is never asked twice in a row."""
    text = re.sub(r"\s+", " ", (question.text or "").strip().lower())
    return text


def filter_repeat_clarifications(
    questions: tuple["ClarificationQuestion", ...],
    *,
    already_asked: Iterable[str] = (),
) -> tuple["ClarificationQuestion", ...]:
    """Drop questions whose signature was already asked (PRD: no repeated
    identical default question). Preserves order; de-dupes within the batch."""
    seen = {str(s).strip().lower() for s in already_asked}
    out: list[ClarificationQuestion] = []
    for q in questions:
        sig = clarification_signature(q)
        if not sig or sig in seen:
            continue
        seen.add(sig)
        out.append(q)
    return tuple(out)


def plan_skill_signature(*, repo_family: str, os_name: str, execution_target: str) -> str:
    """Plan 72 Phase 8: stable key for a learned setup skill."""
    parts = [
        (repo_family or "unknown").strip().lower(),
        (os_name or "unknown").strip().lower().split()[0] if os_name else "unknown",
        (execution_target or "local").strip().lower(),
    ]
    raw = "-".join(parts)
    return "setup-" + re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-")


def record_plan_skill(
    *,
    config_dir: Path,
    repo_family: str,
    os_name: str,
    execution_target: str,
    commands: tuple[str, ...],
) -> str | None:
    """Plan 72 Phase 8: persist a reusable setup skill AFTER a verified success.

    Keyed by family + OS + target. Content is redacted (no secrets/logs).
    Returns the skill slug, or None on failure. MUST only be called once the
    plan's steps verified successfully — never on failure."""
    if config_dir is None or not commands:
        return None
    slug = plan_skill_signature(repo_family=repo_family, os_name=os_name, execution_target=execution_target)
    title = f"Verified setup: {repo_family} on {execution_target}"
    body_lines = [
        f"Verified working setup sequence for a {repo_family} repo on {execution_target} ({os_name}):",
        "",
    ]
    for i, cmd in enumerate(commands, start=1):
        body_lines.append(f"{i}. {_redacted(cmd)}")
    summary = _redacted("\n".join(body_lines))
    try:
        from state.access import write_skill_memory_state

        write_skill_memory_state(config_dir, slug=slug, title=title, summary=summary)
        return slug
    except Exception:
        return None


def _skill_file_path(config_dir: Path, slug: str) -> Path:
    return Path(config_dir) / "memory" / "skills" / f"{slug}.md"


def record_failure_skill(
    *,
    config_dir: Path,
    repo_family: str,
    os_name: str,
    execution_target: str,
    failed_command: str,
    cause: str,
) -> str | None:
    """Plan 73 D3: persist a failure-pattern skill AFTER an irrecoverable run so
    the next plan for the same signature avoids the known dead-end. Redacted."""
    if config_dir is None or not (failed_command or cause):
        return None
    slug = plan_skill_signature(
        repo_family=repo_family, os_name=os_name, execution_target=execution_target
    ) + "-avoid"
    title = f"Known failure: {repo_family} on {execution_target}"
    summary = _redacted(
        f"On a {repo_family} repo on {execution_target} ({os_name}), this step failed and should be "
        f"approached differently:\n\nFailed command: {failed_command}\nCause: {cause}\n"
    )
    try:
        from state.access import write_skill_memory_state

        write_skill_memory_state(config_dir, slug=slug, title=title, summary=summary)
        return slug
    except Exception:
        return None


def find_matching_skill(config_dir: Path, *, repo_family: str, os_name: str, execution_target: str) -> str | None:
    """Plan 73 D1: return the slug of a verified skill matching this signature,
    or None. (The `-avoid` failure skill is not returned here — it's advisory.)"""
    if config_dir is None:
        return None
    slug = plan_skill_signature(repo_family=repo_family, os_name=os_name, execution_target=execution_target)
    return slug if _skill_file_path(Path(config_dir), slug).exists() else None


# Skill summaries are stored with whitespace collapsed (`_normalize_summary_text`),
# so the numbered command list lands on one line: "1. cmd 2. cmd 3. cmd".
_SKILL_CMD_SPLIT_RE = re.compile(r"(?:^|\s)\d+\.\s+(.+?)(?=\s+\d+\.\s+|$)")


def load_skill_commands(config_dir: Path, slug: str) -> tuple[str, ...]:
    """Plan 73 D1: parse the numbered command sequence out of a stored skill
    note (the format `record_plan_skill` writes, after whitespace collapse)."""
    path = _skill_file_path(Path(config_dir), slug)
    if not path.exists():
        return ()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ()
    # Drop the markdown title line; parse the body.
    body = text.split("\n", 1)[1] if "\n" in text else text
    commands = [m.strip() for m in _SKILL_CMD_SPLIT_RE.findall(body) if m.strip()]
    return tuple(commands)


@dataclass(frozen=True)
class ReflectionNote:
    """Plan 73 D2: a redacted post-run reflection."""

    succeeded: bool
    worked: tuple[str, ...]
    failed_step: str | None
    cause: str | None
    recommendation: str

    def render(self) -> str:
        head = "succeeded" if self.succeeded else "did not complete"
        lines = [f"Run reflection — {head}."]
        if self.worked:
            lines.append("Worked: " + "; ".join(self.worked[:5]))
        if self.failed_step:
            lines.append(f"Failed: {self.failed_step}")
        if self.cause:
            lines.append(f"Cause: {self.cause}")
        if self.recommendation:
            lines.append(f"Next time: {self.recommendation}")
        return "\n".join(lines)


def reflect_on_run(
    *,
    plan: PlanRecord,
    executed: tuple[str, ...],
    succeeded: bool,
    attribution_cause: str | None = None,
    failed_command: str | None = None,
) -> ReflectionNote:
    """Plan 73 D2: deterministic, redacted reflection over a run. No LLM needed;
    derives what worked / failed / a recommendation from the executed list and
    the (already-redacted) attribution cause."""
    worked = tuple(_redacted(c) for c in executed if c)
    if succeeded:
        rec = "Reuse the verified sequence; it is now stored as a skill."
        return ReflectionNote(succeeded=True, worked=worked, failed_step=None, cause=None, recommendation=rec)
    cause = _redacted(attribution_cause) if attribution_cause else None
    failed = _redacted(failed_command) if failed_command else None
    rec = "Address the cause before retrying; the failure is recorded so the next plan avoids it."
    return ReflectionNote(succeeded=False, worked=worked, failed_step=failed, cause=cause, recommendation=rec)


def persist_reflection(config_dir: Path, *, repo_slug: str | None, note: ReflectionNote) -> None:
    """Persist a redacted reflection summary keyed by the repo (best-effort)."""
    if config_dir is None:
        return
    try:
        from state.access import write_session_summary_state

        session_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", f"reflection-{repo_slug or 'task'}")[:80]
        write_session_summary_state(
            config_dir,
            session_id=session_id,
            summary=_redacted(note.render()),
        )
    except Exception:
        pass


def plan_first_required(*, plan_mode_enabled: bool, safety_class: str) -> bool:
    """Plan 72 Phase 4: True when a mutating action must go through an approved
    plan instead of running directly. S0 diagnostics always pass through; any
    S1+ action requires plan-first when Plan Mode is ON."""
    if not plan_mode_enabled:
        return False
    return str(safety_class or "").upper() not in ("", "S0")


def require_plan_for_mutation(
    *,
    plan_mode_enabled: bool,
    command: str,
    display: Callable[[str], None] | None = None,
    action_label: str = "this action",
) -> bool:
    """Chokepoint: returns True if the caller MAY execute `command` directly,
    or False if Plan Mode requires it to go through draft→approve first (and
    emits a one-line notice). S0 diagnostics always return True."""
    from duckln.safety import assess_command

    safety_class = assess_command(command).safety_class.value
    if not plan_first_required(plan_mode_enabled=plan_mode_enabled, safety_class=safety_class):
        return True
    if display is not None:
        display(
            f"Plan Mode is on — {action_label} ({safety_class}) needs an approved plan first. "
            "Generating a plan; review it, then run `/plan approve`. (Use `/plan off` to disable.)"
        )
    return False


def looks_like_multistep_request(message: str) -> bool:
    """Heuristic: True when a free-text user message asks for a multi-step task.

    Used by the conversation supervisor to decide whether Plan Mode should
    nudge the user toward the planning flow. Conservative — only fires on
    strong setup verbs paired with a noun-y object (length > a single word
    after the verb).
    """
    if not message:
        return False
    lowered = " " + message.lower().strip() + " "
    for verb in _MULTISTEP_VERBS:
        token = f" {verb} "
        if token in lowered:
            idx = lowered.index(token) + len(token)
            tail = lowered[idx:].strip()
            if len(tail) >= 3:
                return True
    return False
