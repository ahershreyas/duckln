"""Plan 91 (Plan 90 Phase 1) — Repo agent runtime.

Wires the dormant harness tool-use loop (`harness/loop.run_agent`) into the live
product as a REPO-SCOPED agent: given a question about the active repository, it
reads and searches the repo's actual files (via the read-only tool set) and then
synthesizes a grounded answer. All tools are confined to the repo workspace by the
existing path-traversal guard in `harness/tools.py`.

This is the foundation the later phases build on (filesystem write/edit, repo
actions, subagents). Phase 1 ships the read-only Q&A path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from duckln.harness.agent_def import AgentDefinition, builtin_agents_directory, load_agent_definition_from_path
from duckln.harness.loop import TurnEvent, run_agent
from duckln.harness.tools import AgentContext, ToolRegistry, build_default_registry
from duckln.modes import ControlMode


@dataclass(frozen=True)
class RepoAgentOutcome:
    """Result of a repo-agent run: the synthesized answer plus a trace for tests/UX."""

    answer: str
    succeeded: bool
    turns: int
    tools_used: tuple[str, ...]
    stop_reason: str
    observations: tuple = field(default_factory=tuple)


def load_repo_qa_definition() -> AgentDefinition:
    """Load the read-only Repo Q&A agent spec from the bundled agents directory."""
    return load_agent_definition_from_path(builtin_agents_directory() / "repo_qa.md")


def load_repo_engineer_definition() -> AgentDefinition:
    """Load the read+write Repo Engineer agent spec (Plan 93)."""
    return load_agent_definition_from_path(builtin_agents_directory() / "repo_engineer.md")


def _observation_label(tool: str, target: str) -> str:
    """Plan 187 F2: label an observation by its SOURCE (a real file path, a directory,
    a command, or 'Known facts' for state) — never the raw internal tool name. This stops
    the synthesis model from echoing an internal tool (e.g. `state.read`) as a fake
    `file:line` citation (the garbled "(`path`: state.read, line 10)" answer)."""
    t = str(tool or "")
    if t in ("fs.read_file", "fs.read") and target:
        return f"File {target}:"
    if t == "fs.list_dir":
        return f"Directory {target or '.'}:"
    if t in ("fs.search", "fs.glob") and target:
        return f"Search {target}:"
    if t == "shell.probe" and target:
        return f"Command `{target}` output:"
    if t == "state.read":
        return "Known facts (from Duckln's session memory):"
    if t in ("web.search", "web.fetch"):
        return f"Web ({target}):" if target else "Web:"
    return f"{target}:" if target else "Observation:"


def _observation_digest(observations, *, max_chars: int = 6000) -> str:
    """Render the agent's tool observations into a compact, grounded evidence block
    for the final-answer synthesis call. Newest-relevant first; bounded. Labels each
    block by its real SOURCE, never the internal tool name (Plan 187 F2)."""
    lines: list[str] = []
    for obs in observations:
        args = getattr(obs, "args", {}) or {}
        target = args.get("path") or args.get("command") or args.get("query") or ""
        label = _observation_label(getattr(obs, "tool", ""), str(target))
        if getattr(obs, "ok", False):
            payload = getattr(obs, "payload", None)
            if isinstance(payload, dict):
                text = payload.get("text") or payload.get("stdout") or payload.get("summary") or payload.get("entries") or payload
            else:
                text = payload
            snippet = str(text)[:1200]
            lines.append(f"{label} {snippet}")
        else:
            lines.append(f"{label} (could not read — {getattr(obs, 'error_message', '') or getattr(obs, 'error_code', '')})")
    block = "\n\n".join(lines)
    return block[:max_chars]


_SYNTH_SYSTEM = {
    "answer": (
        "You are Duckln answering a question about a specific repository. Answer ONLY from "
        "the evidence below. Write in clean, natural, grammatical English — a direct answer, "
        "not a clumsy restatement of the question.\n"
        "Citations: cite a real repository FILE as `path` (add `:line` only when a line number "
        "is actually shown in the evidence — NEVER invent one). Cite a file ONLY for a claim "
        "about that file's code. For a fact from Duckln's session memory or a general/meta "
        "answer, just state it plainly with no citation.\n"
        "NEVER surface Duckln's internal machinery — do not mention tool names (e.g. state.read, "
        "fs.read_file, shell.probe, web.search) or a 'line N of <tool>'; those are internals, "
        "not sources. If the evidence is insufficient, say what's missing — do not invent. "
        "Be concise and concrete."
    ),
    "action": (
        "You are Duckln summarizing an engineering task you just performed on a repository. "
        "From the evidence below, report concisely in clean natural English: what you changed "
        "(cite the real FILE `path`), what you ran to verify, and whether verification PASSED or "
        "FAILED. Be honest — if a step failed or was denied, say so and what remains. NEVER "
        "mention Duckln's internal tool names or invent line numbers — cite real files only."
    ),
}


def _synthesize_answer(
    *, question: str, observations, llm_client, repo_name: str, kind: str = "answer"
) -> str:
    """One bounded LLM call that writes the final answer/summary from the gathered
    evidence. Falls back to a plain evidence dump if no model."""
    digest = _observation_digest(observations)
    # Plan 147 F3: distinguish BLINDNESS (the tools ran but couldn't ACCESS the repo — a
    # tool/path-access problem) from a genuinely missing repo or a no-op. Emitting "make
    # sure the repo is cloned locally" when the real issue is path resolution on the target
    # hid the actual bug (the `~`-quoting F1 fixes) and made it look like a reasoning failure.
    _obs = list(observations or ())
    if _obs and all(not getattr(o, "ok", False) for o in _obs):
        _codes = sorted({str(getattr(o, "error_code", "") or "") for o in _obs} - {""})
        return (
            f"I couldn't ACCESS {repo_name}'s files — every read/list failed"
            + (f" ({', '.join(_codes)})" if _codes else "")
            + ". This is a tool/path-access problem on the target, not a missing repo or a "
            "reasoning failure — the repo is present (the build ran against it)."
        )
    if not digest:
        if kind == "action":
            return f"I didn't make any changes to {repo_name} — the task produced no tool actions."
        return (
            f"I couldn't gather evidence in {repo_name} — the agent produced no readable tool "
            "observations. Re-run with a capable model, or rephrase the question."
        )
    if llm_client is None:
        return f"Evidence gathered from {repo_name} (no model configured to summarize):\n\n{digest}"
    system = _SYNTH_SYSTEM.get(kind, _SYNTH_SYSTEM["answer"])
    label = "Task" if kind == "action" else "Question"
    user = f"Repository: {repo_name}\n{label}: {question}\n\nEvidence:\n{digest}"
    try:
        return str(llm_client(system_prompt=system, user_message=user)).strip() or digest
    except Exception:
        return f"Evidence gathered from {repo_name}:\n\n{digest}"


def run_repo_agent(
    *,
    question: str,
    config_dir: Path,
    repo_name: str,
    project_dir: Path | None,
    execution_target: str = "local",
    mode: ControlMode = ControlMode.HOTL,
    display: Callable[[str], None] | None = None,
    approve: Callable[[str], bool] | None = None,
    emit_thought: Callable[[str], None] | None = None,
    chat: object | None = None,
    llm_client: Callable[..., str] | None = None,
    registry: ToolRegistry | None = None,
    definition: AgentDefinition | None = None,
    task_kind: str = "answer",
    tool_policy: dict | None = None,
    extra_hints: tuple[str, ...] = (),
    vm_name: str | None = None,
) -> RepoAgentOutcome:
    """Run the repo-scoped tool-use loop over the active repo, then synthesize a
    grounded result. `task_kind="answer"` (read-only Q&A) or `"action"` (engineering
    summary). `tool_policy` is a user allow/deny map honored by the dispatcher.

    `registry`/`definition`/`llm_client` are injectable for tests. The filesystem
    tools are confined to `project_dir` by the harness path-traversal guard."""

    def _think(text: str) -> None:
        if emit_thought is not None:
            try:
                emit_thought(text)
            except Exception:
                pass

    definition = definition or load_repo_qa_definition()
    # Plan 187 F4c: pick the budget TIER from the OBSERVED capability verdict of the model this
    # agent will actually use (the REPO_AGENT role model, else the global model) — capable → the
    # generous ceiling, weak → the tight one (convergence + latency + answer quality on small
    # local models). No-op when the spec has no budget_profiles or the verdict is unknown.
    try:
        from duckln.recovery import resolve_agent_budget
        from duckln.ai_client import read_active_routing
        from state.access import read_config_snapshot

        _agent_model = (read_active_routing(config_dir).get("REPO_AGENT") or "").strip() \
            or str(read_config_snapshot(config_dir).get("model") or "").strip()
        definition = resolve_agent_budget(definition, model_id=_agent_model, config_dir=config_dir)
    except Exception:
        pass
    # Plan 178 F5: pass config_dir so the user's registered shell/MCP tools are EXECUTABLE
    # (not just visible) for this agent run.
    registry = registry or build_default_registry(include_handlers=True, config_dir=config_dir)
    if llm_client is None:
        try:
            # Plan 179 C2: the repo agent runs under the REPO_AGENT role (its own model in
            # Specialized Mode; the global model otherwise).
            from duckln.ai_client import build_llm_client_for_role

            llm_client = build_llm_client_for_role(config_dir, "REPO_AGENT")
        except Exception:
            llm_client = None

    if llm_client is None:
        return RepoAgentOutcome(
            answer="No AI provider is configured, so Duckln can't reason over the repo yet. Set one with `/provider`.",
            succeeded=False, turns=0, tools_used=(), stop_reason="no_llm",
        )

    context = AgentContext(
        agent_name=definition.name,
        mode=mode,
        config_dir=Path(config_dir),
        project_dir=Path(project_dir) if project_dir is not None else None,
        execution_target=execution_target,
        display=display,
        approve=approve,
        extra={"chat": chat, "question": question, "tool_policy": tool_policy or {}, "vm_name": vm_name},
    )

    verb = "working on" if task_kind == "action" else "reading"
    _think(f"{'Engineer' if task_kind == 'action' else 'Inspector'} → {verb} {repo_name}: “{question[:80]}”")
    # Plan 178 F4: load the user's PULLED/learned skills (task-matched) into the agent context,
    # so a pulled skill is actually CONSULTED — not just stored/visible.
    try:
        from duckln.subagents import registered_skill_hints

        _skill_hints = registered_skill_hints(config_dir, task_text=question)
    except Exception:
        _skill_hints = ()
    if _skill_hints:
        extra_hints = tuple(extra_hints) + _skill_hints
    initial_input = {"question": question, "repo": repo_name}
    if extra_hints:
        initial_input["hints"] = list(extra_hints)

    def _turn_cb(event: TurnEvent) -> None:
        target = ""
        if isinstance(event.args, dict):
            target = str(event.args.get("path") or event.args.get("command") or event.args.get("query") or "")
        status = "" if event.ok else " (error)"
        _think(f"Executor → {event.tool} {target}{status}".rstrip())
        # Plan 133 F7b: when the agent EDITS a repo file, surface a Claude-style edit
        # card in the message window (path + +N/-M, diff body) so the user sees the edits.
        if event.ok and event.tool in ("fs.edit", "fs.write") and display is not None and isinstance(event.result, dict):
            try:
                from duckln.ui import render_edit_card

                res = event.result
                # Compact collapsed card (header only) keeps the chat scannable —
                # the full diff stays in event.result for the expandable detail view.
                display(render_edit_card(
                    str(res.get("path") or target or "file"),
                    added=int(res.get("added") or 0),
                    removed=int(res.get("removed") or 0),
                    created=bool(res.get("created")),
                ))
            except Exception:
                pass

    result = run_agent(
        definition,
        initial_input,
        context=context,
        tool_registry=registry,
        llm_client=llm_client,
        display=_turn_cb,
    )

    _think("Planner → summarizing what I did…" if task_kind == "action" else "Planner → synthesizing the answer from what I read…")
    answer = _synthesize_answer(
        question=question, observations=result.observations, llm_client=llm_client,
        repo_name=repo_name, kind=task_kind,
    )
    tools_used = tuple(dict.fromkeys(o.tool for o in result.observations))
    return RepoAgentOutcome(
        answer=answer,
        succeeded=bool(result.observations),
        turns=result.turn_count,
        tools_used=tools_used,
        stop_reason=result.stop_reason,
        observations=result.observations,
    )


_ACTION_VERBS = (
    "fix", "add", "implement", "refactor", "edit", "change", "update", "rename",
    "remove", "delete", "create a", "write a", "make it", "rewrite", "patch",
    "bump", "upgrade", "format", "lint", "run the test", "run tests", "run the app",
)
_QUESTION_CUES = (
    # Plan 174 F1 / Plan 189 F3: DEFINITIVE repo/code cues only — phrases that are unambiguously
    # about THIS repo's files/code. The AMBIGUOUS cues ("how does", "how do", "what does",
    # "why does", "why is", "explain", "describe") were REMOVED — they match general concepts too
    # ("how does recursion work"), so they defer to the LLM classifier, which distinguishes a
    # general TECHNICAL question from a repo question. Deterministic owns precision; the LLM owns
    # the ambiguous tail (repo_question vs technical vs social).
    "where is", "where's", "which file", "which function", "show me",
    "find the", "find where", "locate", "walk me through",
    # Plan 189: an EXPLICIT repo reference makes any question definitively about THIS repo,
    # model-independent (e.g. "how does auth work in the code" — a weak model may mis-tag it
    # `technical`, but "in the code" is an unambiguous repo signal).
    "in the code", "in the repo", "in this repo", "in the codebase",
    "in the file", "in this file", "in our repo", "in the project",
)


_REASONING_CUES = (
    "show your reasoning", "show me your reasoning", "show your thinking", "your reasoning",
    "your thinking", "why did you do that", "why did you", "open the reasoning",
    "open reasoning", "show the reasoning", "see your reasoning", "your logic", "reasoning log",
    "logical-thinking", "show your work", "explain your reasoning",
)
_RECHECK_CUES = (
    "that's wrong", "thats wrong", "that is wrong", "recheck", "re-check", "check again",
    "i don't think that", "i dont think that", "are you sure", "double check", "double-check",
    "that's not right", "thats not right", "look again", "reconsider",
)


def classify_repo_agent_intent(message: str) -> str:
    """Plan 102 + 145 F7: classify a free-form message (NO slash needed) as a repo 'ask'
    (question about the code), 'do' (action), 'reasoning' (show its thinking), 'recheck'
    (critique → re-investigate), or '' (not a repo-agent task → normal chat). The reasoning/
    recheck intents are checked FIRST so "show your reasoning" isn't misread as a code Q&A."""
    m = str(message or "").strip().lower()
    if not m or m.startswith("/"):
        return ""
    if any(cue in m for cue in _REASONING_CUES):
        return "reasoning"
    if any(cue in m for cue in _RECHECK_CUES):
        return "recheck"
    if any(m.startswith(v) or f" {v}" in m for v in _ACTION_VERBS):
        return "do"
    # Plan 174 F1: deterministic = PRECISION, not coverage. Only an explicit repo/code CUE makes
    # this an "ask"; a bare "?" (e.g. "how was your day?") is NOT a repo question — it falls through
    # to the conversation supervisor, where the LLM classifier (F2) decides repo-vs-general. The old
    # `m.endswith("?")` rule hijacked the open-ended tail into repo_qa + a web.search loop.
    if any(cue in m for cue in _QUESTION_CUES):
        return "ask"
    return ""


def _draft_todo(task: str, *, llm_client: Callable[..., str] | None) -> tuple[str, ...]:
    """Plan 107: a short, explicit TODO checklist drafted BEFORE acting. Uses the LLM
    when available, with a deterministic engineering-flow fallback."""
    if llm_client is not None:
        try:
            sys = ("Break the user's repo task into 3-5 short imperative TODO steps. "
                   "Return ONLY a JSON array of strings, no prose.")
            raw = str(llm_client(system_prompt=sys, user_message=task))
            import json as _json
            start, end = raw.find("["), raw.rfind("]")
            if start != -1 and end > start:
                items = _json.loads(raw[start:end + 1])
                steps = tuple(str(s).strip() for s in items if str(s).strip())[:6]
                if steps:
                    return steps
        except Exception:
            pass
    return (
        "Locate the relevant code (search/read)",
        "Make the smallest change that does it (edit)",
        "Verify by running the tests/app",
        "Report what changed and the result",
    )


def _recall_repo_skill(config_dir: Path, *, repo_slug: str, task: str) -> str:
    """Plan 109: recall a previously-distilled skill for this repo most relevant to
    the task (keyword-ranked over per-repo skill memory)."""
    try:
        from state.access import read_repo_skills_ranked

        skills = read_repo_skills_ranked(config_dir, repo_slug, query=task, top_k=1)
        return skills[0] if skills else ""
    except Exception:
        return ""


def _distill_repo_skill(config_dir: Path, *, repo_slug: str, task: str, summary: str) -> None:
    """Plan 109: after a verified success, persist a concise reusable skill so the next
    similar task on this repo reuses what worked. Redacted + bounded."""
    try:
        from duckln.diagnostics import redact_sensitive_data
        from state.access import write_repo_skill

        write_repo_skill(
            config_dir, repo_slug=repo_slug,
            task=redact_sensitive_data(task), lesson=redact_sensitive_data(summary),
        )
    except Exception:
        pass


def run_repo_task(
    *,
    task: str,
    config_dir: Path,
    repo_name: str,
    project_dir: Path | None,
    execution_target: str = "local",
    mode: ControlMode = ControlMode.HOTL,
    approve: Callable[[str], bool] | None = None,
    emit_thought: Callable[[str], None] | None = None,
    chat: object | None = None,
    llm_client: Callable[..., str] | None = None,
    tool_policy: dict | None = None,
    delegate: bool = True,
    registry: ToolRegistry | None = None,
    vm_name: str | None = None,
    bus: object | None = None,
) -> RepoAgentOutcome:
    """Plan 93/94/103: perform an ACTION on the active repo. With `delegate=True`,
    orchestrates ISOLATED-context subagents — an Explorer (read-only `repo_qa`) then an
    Engineer (`repo_engineer`) — communicating over a real `MessageBus` (Plan 108).
    Plan 107: a TODO checklist is drafted + shown before acting. Plan 109: a reusable
    skill is recalled before and distilled after a successful run."""
    from duckln.harness.bus import MessageBus

    def _think(text: str) -> None:
        if emit_thought is not None:
            try:
                emit_thought(text)
            except Exception:
                pass

    repo_slug = repo_name
    bus = bus if bus is not None else MessageBus()

    # Plan 107: plan-first — draft + show a TODO checklist BEFORE any action.
    todo = _draft_todo(task, llm_client=llm_client)
    for i, item in enumerate(todo, start=1):
        _think(f"Planner → TODO {i}/{len(todo)}: {item}")

    # Plan 109: recall a distilled skill for this kind of task on this repo.
    recalled = _recall_repo_skill(config_dir, repo_slug=repo_slug, task=task)
    hints: list[str] = []
    if recalled:
        _think("Planner → recalled a prior skill for this repo — reusing it.")
        hints.append(f"Prior skill: {recalled}")

    if delegate:
        _think("Supervisor → delegating to the Explorer subagent (its own context) to map the task first…")
        explore = run_repo_agent(
            question=f"Find the files and code relevant to this task: {task}",
            config_dir=config_dir, repo_name=repo_name, project_dir=project_dir,
            execution_target=execution_target, mode=mode, approve=approve,
            emit_thought=emit_thought, chat=chat, llm_client=llm_client,
            registry=registry, definition=load_repo_qa_definition(),
            task_kind="answer", tool_policy=tool_policy, vm_name=vm_name,
        )
        if explore.answer:
            bus.publish("findings", explore.answer[:1500], sender="explorer")  # Plan 108: A2A
            hints.append(f"Explorer findings: {explore.answer[:1500]}")
        _think("Supervisor → Engineer subagent received the Explorer's findings (via the bus); making + verifying the change…")

    outcome = run_repo_agent(
        question=task,
        config_dir=config_dir, repo_name=repo_name, project_dir=project_dir,
        execution_target=execution_target, mode=mode, approve=approve,
        emit_thought=emit_thought, chat=chat, llm_client=llm_client,
        registry=registry, definition=load_repo_engineer_definition(),
        task_kind="action", tool_policy=tool_policy, extra_hints=tuple(hints), vm_name=vm_name,
    )

    # Plan 108: the Engineer reports back over the bus (bidirectional A2A transcript).
    bus.publish("report", outcome.answer[:1500], sender="engineer")
    # Plan 109: on a verified success, distill a reusable skill for this repo/stack.
    if outcome.succeeded and "fail" not in (outcome.answer or "").lower():
        _distill_repo_skill(config_dir, repo_slug=repo_slug, task=task, summary=outcome.answer)
    return outcome
