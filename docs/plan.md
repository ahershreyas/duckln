# Duckln Implementation Plan

Analyzed from `requirements.md`.

## Architecture Direction

Duckln v1 should be implemented as a terminal-native Python CLI with modular boundaries:

- `main.py` — app entrypoint and loop
- `config.py` — provider and mode onboarding, config persistence
- `ui.py` — banner, prompt, output sections
- `shell.py` — safe command runner
- `safety.py` — blocked patterns, safety classes, mode enforcement
- `diagnostics.py` — error classification and minimal context gathering
- `prompts.py` — provider-neutral system and task prompt builders
- `ai_client.py` — OpenRouter/OpenAI/Anthropic adapters
- `modes.py` — HITL/HOTL/HOOTLWO behavior policy

## Plan Items

1. **Create provider abstraction for OpenRouter, OpenAI, Anthropic**  
   Covers: R1, R10  
   Why: provider flexibility is a launch requirement.

2. **Implement first-run onboarding and local config**  
   Covers: R1  
   Why: the product must feel polished from first launch.

3. **Add pixel banner and duck headshot fallback**  
   Covers: R2  
   Why: launch excitement and memorable identity.

4. **Implement mode policy engine**  
   Covers: R3, R4, R5  
   Why: mode behavior must be deterministic and production-safe.

5. **Build safe shell execution layer**  
   Covers: R4  
   Why: this is the operational core.

6. **Build privacy-first redaction and payload shaping**  
   Covers: R5, R10  
   Why: must be non-negotiable for production.

7. **Implement error classification for Python/AI environment issues**  
   Covers: R6  
   Why: this is the first-release wedge.

8. **Implement exact next-step suggestion loop**  
   Covers: R7  
   Why: user value comes from moving to the next action quickly.

9. **Implement verification loop after fixes**  
   Covers: R8  
   Why: trust depends on verifying fixes.

10. **Implement concise teaching layer**  
    Covers: R9  
    Why: Duckln must feel smarter than a plain error explainer.

11. **Implement runtime command palette and slash command configuration flow**
    Covers: R1, R3
    Why: users must be able to change mode/provider/model safely during an active session without restarting Duckln.

12. **Implement environment healthcheck command**
    Covers: R6
    Why: allows users to quickly verify setup and diagnose environment issues proactively.

11. **Add mode-aware output verbosity tuning**  
    Covers: R3, R9  
    Why: support learners without slowing experts.

12. **Add structured production logging**  
    Covers: R10  
    Why: needed for production hardening and incident response.

13. **Create deterministic heuristics before LLM calls**  
    Covers: R6, R7  
    Why: reduce cost and improve trust.

14. **Create whitelisted safe auto-run set for HOOTLWO**  
    Covers: R3, R4  
    Why: partial autonomy without irresponsible execution.

15. **Add richer onboarding polish and provider docs links**  
    Covers: R1  
    Why: useful, but not launch-critical.

16. **Add session summaries for repeated failures**  
    Covers: R8, R9  
    Why: valuable after core loop is proven.

17. **Implement repo catalog loader with static cache + manual refresh**
    Covers: R11
    Why: ensures fast startup and user-controlled freshness without API dependency
    
18. **Implement repo bring-up engine**
    Covers: R11
    Why: core product value — clone, setup, verify

19. **Implement VM orchestration (Multipass Ubuntu) with optional fresh Duckln install inside the VM Includes `/vm` command to trigger VM setup outside onboarding.**
    Covers: R11
    Why: gives users a clean isolated environment while keeping credentials and config under explicit user control Includes: `/vm` command to trigger VM setup outside onboarding.

20. **Implement manual provider/model/API configuration flow inside the VM**
    Covers: R11
    Why: avoids automatic credential transfer and keeps VM setup explicit, safe, and user-controlled

21. **Implement agent memory system (filesystem + SQLite hybrid)**
    Covers: R11
    Why: enables agent learning and state persistence

22. **Implement memory control commands**
    Covers: R11
    Why: user trust and control over stored data

23. **Adopt AGENTS.md as the primary agent behavior contract**
    Covers: R12
    Why: Codex and future runtime agent behavior need a single authoritative contract

24. **Implement filesystem-facing memory with SQLite-backed state**
    Covers: R11, R12
    Why: gives the agent structured memory while preserving performance and reliability

25. **Implement hardware-aware behavior across Apple Silicon, non-Apple, and NVIDIA systems**
    Covers: R11, R12
    Why: setup and guidance must adapt to real hardware constraints

26. **Implement VM configuration UX (CPU, memory, naming)**
    Covers: R11
    Why: gives user control over VM resources and identification

27. **Implement post-setup guidance and completion feedback**
    Covers: R11
    Why: improves usability and clarity after environment setup

28. **Implement cross-hardware detection and adaptation (Apple Silicon, CPU-only, NVIDIA CUDA)**
    Covers: R12
    Why: prevents incorrect setup guidance across systems

29. **Implement VM naming conflict resolution with auto-increment suffix**
    Covers: R11
    Why: prevents VM creation failure due to duplicate names

30. **Implement SQLite-backed virtual memory contract**
    Covers: R12
    Why: makes SQLite the source of truth while preserving a filesystem-shaped interface for the agent

31. **Implement memory materialization and sync layer**
    Covers: R12
    Why: allows Duckln to expose agent-readable files from SQLite-backed memory safely and consistently
    
32. **Implement curated launch-catalog overrides for bundled repo generation**
    Covers: R11
    Why: launch repo quality requires product-level curation beyond raw GitHub topic filtering

33. **Implement supervisor-agent routing for repo bring-up**
    Covers: R11
    Why: repo setup requires classification and specialist routing rather than one generic setup path

34. **Implement repo-family classification before setup**
    Covers: R11
    Why: Python, C++/native, Node, audio, and diffusion repos need different bring-up strategies

35. **Implement specialist bring-up agents and playbooks**
    Covers: R11
    Why: Duckln needs domain-specific setup logic for different repo families to become a trustworthy agent

36. **Implement Ollama as a local provider option**
    Covers: R11
    Why: users need a local-model path without cloud API keys for privacy and offline workflows

37. **Implement detailed agent guardrails and playbook-backed decision rules**
    Covers: R11,R12
    Why: stronger agent behavior requires explicit boundaries, routing, and verification logic

38. **Preserve curated launch catalog expansion including Coqui TTS**
    Covers: R11
    Why: launch catalog quality must reflect practical local projects, not only topic-search results

39. **Implement Debug / Recovery specialist agent for failed bring-up attempts**
    Covers: R11,R12
    Why: first-pass setup specialists are not enough for real-world repo failures and mixed-stack recovery

40. **Implement failure classification and bounded recovery routing**
    Covers: R11,R12
    Why: setup failures must be analyzed and resolved through retry, reroute, prerequisite requests, or explicit unsupported-case handling

41. **Implement supervisor confidence, escalation, and post-failure memory reflection**
    Covers: R11,R12
    Why: Duckln must behave like a real decision-making agent, not just a fixed setup runner

42. **Implement first-run trust and runtime UX improvements**
    Covers: R12
    Why: users need clear safety, identity, state visibility, and consistent interaction behavior to trust Duckln

43. **Implement bounded uninstall flow with OS-aware removal**
    Covers: R12
    Why: users must be able to remove Duckln and related data safely and predictably across platforms

49. **Fix VM auto-trigger repo bring-up path**
    Covers: R11
    Why: Two bugs block repo setup after VM creation. (1) `choice_prompt` is only assigned in the `else` branch of the auto-trigger check, causing a `NameError` before `bring_up_selected_repo` is ever called. (2) `ControlledCommandRunner` ignores `vm_name` and runs all commands locally via `subprocess.Popen`, so repo setup commands land on the host instead of inside the VM. Fix: initialize `choice_prompt = None` before the branch; add `vm_name` to `ControlledCommandRunner` and wrap commands with `multipass exec` when targeting a VM; thread `vm_name` through `bring_up_selected_repo`.

## Technical Decisions

- Language: Python 3.11+
- OS target: macOS + Ubuntu
- Shell target: zsh + bash
- HTTP client: `requests` or `httpx`
- Interactive selection: `InquirerPy`
- Config file: local user config file in home directory
- Logs: JSONL or structured plain text with redaction
- Tests:
  - unit tests for safety/classification
  - integration tests for provider adapters and fix verification
  - manual scenario tests for venv / pip / torch / CUDA

## Release Strategy

### Release 0.1
- working loop
- onboarding
- HITL
- OpenRouter only
- missing package diagnosis

### Release 0.2
- OpenAI + Anthropic
- HOTL
- verification
- privacy redaction
- path and permission diagnosis

### Release 0.3
- HOOTLWO whitelist
- CUDA / torch mismatch handling
- production logging
- launch-ready polish

## Risks and Mitigation

- **Risk: generic LLM answers**
  - Mitigation: deterministic classification first, bounded prompts, verification loop.

- **Risk: unsafe command execution**
  - Mitigation: safety classes, blocked patterns, mode enforcement, whitelist auto-run only.

- **Risk: provider differences**
  - Mitigation: normalize prompt and response schema in adapter layer.

- **Risk: product feels like existing tools**
  - Mitigation: prioritize diagnosis + verification + teaching, not just explanation.

## Plan Item 50 — Cloud Setup Agent Loop

Covers: R14 (REQ-CLOUD-LOOP-1, REQ-CLOUD-LOOP-2)

Components:
- `src/agent/agent_loop.py` — reusable observe-decide-act loop driver (`run_agent_loop`, `LoopStep`, `LoopChoice`, `Remediation`, `IssueClassification`, `ActionResult`). Mirrors Anthropic's agent-loop pattern (receive → evaluate → tool call → observe → repeat) reduced to deterministic step lists with classifier-driven branching.
- `src/duckln/cloud_remediation.py` — GCP + AWS classifiers (`classify_cloud_failure`) and remediations (`resolve_cloud_remediation`).
- `src/duckln/cloud_runtime.py` — `discover_gcp_zones_detailed` and `discover_aws_regions_detailed` expose the failed `CommandResult` instead of silently returning `()`, so the classifier can see stderr.
- `src/duckln/main.py` — `_run_gcp_zone_configuration_loop`, `_run_aws_region_configuration_loop`, `_surface_cloud_remediation_choice`, `_persist_pending_cloud_remediation`, `_surface_pending_cloud_remediation_on_login`. `/cloud` enters the loop instead of single-shotting.
- `src/state/access.py` — `pending_cloud_remediation` followup field plus `write_pending_cloud_remediation` / `read_pending_cloud_remediation` / `clear_pending_cloud_remediation` helpers.

Why: `/cloud` previously exited silently when `gcloud compute zones list` failed because `compute.googleapis.com` was disabled, leaving the user with the empty picker + "Duckln paused GCP VM creation" message and no path forward. With the loop, the same situation surfaces a classified Fix-now action that enables the API and retries automatically.

### Plan Item 50 extensions (REQ-CLOUD-LOOP-3, REQ-CLOUD-LOOP-4, REQ-CLOUD-LOOP-5)

- `_confirm_or_switch_gcp_project` in [src/duckln/main.py](src/duckln/main.py) — shows "Continue with project X?" Yes/No on every cloud auth check; on No, runs the existing `_select_or_create_gcp_project` picker and persists the choice via `gcloud config set project`.
- `audit_gcp_api_keys`, `classify_gcp_api_key_findings`, `resolve_gcp_api_key_remediation`, `ApiKeyAuditFinding` in [src/duckln/cloud_remediation.py](src/duckln/cloud_remediation.py) — list project API keys, flag any without restrictions (especially keys targeting `generativelanguage.googleapis.com`), and offer to open the GCP credentials console.
- `_run_gcp_api_key_audit_step` in [src/duckln/main.py](src/duckln/main.py) — runs after the zone loop completes; uses `run_agent_loop` with a single step. Fix now opens `console.cloud.google.com/apis/credentials?project=…` in the system browser; Fix later persists via `pending_cloud_remediation`; Ignore exits silently.
- Header behaviour is already wired: `_mark_cloud_provider_target` at [src/duckln/main.py](src/duckln/main.py) writes `active_runtime_execution_target` and calls `terminal_interface.update_connection`; `build_connection_context_label` at [src/duckln/textual_ui.py](src/duckln/textual_ui.py) already maps `gcp → "Google Cloud" (blue)` and `aws → "AWS Cloud" (orange)`. A unit test in `tests/test_main_cloud_loop.py` locks this contract.

### Plan Item 50 extensions (REQ-CLOUD-LOOP-6, REQ-CLOUD-LOOP-7)

- New module [src/duckln/cloud_shapes.py](src/duckln/cloud_shapes.py) — `DiscoveredCloudShape` dataclass plus `discover_all_gcp_shapes` / `discover_all_aws_shapes` parsers that return the full provider catalogue (no allowlist intersection), `group_shapes_into_categories` (CPU General/Compute/Memory + per-GPU buckets), `derived_default_disk_gb`, and `render_shape_label`.
- `_pick_cloud_shape_two_tier` in [src/duckln/main.py](src/duckln/main.py) — two-tier picker (category → shape) used by `_handle_cloud_create_command`. Replaces the previous approved-list intersection.
- `_reassert_cloud_terminal_target_from_state` in [src/duckln/main.py](src/duckln/main.py) — reads workflow state and calls `terminal_interface.update_connection`. Called at session startup (right after `chat.start()`) AND at the top of `_handle_cloud_command` so the header can never drift from the persisted target.
- `SplitPaneChatInterface.__init__` in [src/duckln/textual_ui.py](src/duckln/textual_ui.py) accepts `initial_connection_type`; `build_chat_interface` in [src/duckln/ui.py](src/duckln/ui.py) plumbs it through. `TerminalChatInterface` (fallback) gains a `update_connection` no-op that records the hint so future header reprints reflect it.
- `discover_available_cloud_shapes` in [src/duckln/cloud_runtime.py](src/duckln/cloud_runtime.py) drops the approved-shortlist filter and now delegates to `discover_all_cloud_shapes`, returning every shape name the provider lists.

## Plan Item 51 — `/explore` GitHub trending browser + Q/Esc cancel for cloud pickers

Covers: R15, R16 (REQ-UX-CANCEL-1, REQ-EXPLORE-1, REQ-EXPLORE-2, REQ-EXPLORE-3, REQ-EXPLORE-4)

- New module [src/duckln/explore_trending.py](src/duckln/explore_trending.py) — `TrendingRepo`, `TrendingPeriod`, `build_trending_url`, `parse_trending_html` (BeautifulSoup), `sort_for_display`, `fetch_trending` with injectable httpx client. Pure scraper logic; no UI.
- New module [src/duckln/explore_cache.py](src/duckln/explore_cache.py) — `cache_put/get/age/clear` under the `conversation.explore_cache.` SQLite prefix; 15-minute TTL default.
- New module [src/duckln/explore_runtime.py](src/duckln/explore_runtime.py) — `load_explore_view` (cache-aware), `refresh_explore_view`, `filter_repos_by_language`, `render_repo_label`, `render_explore_header`, and `run_explore_loop` that drives an interactive period/filter/refresh/repo picker on top of the existing `select_prompt` primitive.
- `_handle_explore_command` in [src/duckln/main.py](src/duckln/main.py) — opens the loop, then calls `resolve_public_github_repo_record(url)` + `bring_up_selected_repo(record, current.mode, paths, …)` with no extra confirmation. CLI `duckln explore` is sugar that queues `/explore` at the REPL start.
- `_with_cancel_hint` + `_wrap_select_with_cancel_hint` + `_wrap_text_with_cancel_hint` in [src/duckln/main.py](src/duckln/main.py) — decorate cloud-flow prompts so every picker advertises `(Press Q or Esc to cancel)`. Applied once at `_handle_cloud_command` and `_handle_cloud_create_command` so every nested helper inherits.
- Textual choice overlay `on_key` in [src/duckln/textual_ui.py](src/duckln/textual_ui.py) gains `escape`/`q`/`Q` handling — cancelling any open choice overlay is now a single keystroke.
- Slash palette: `"/explore"` added to [SLASH_COMMANDS](src/duckln/constants.py) and a one-liner help entry in [SLASH_COMMANDS_HELP](src/duckln/textual_ui.py).
- New dependency: `beautifulsoup4>=4.12,<5.0` in [pyproject.toml](pyproject.toml).

Why: closes the loop between "find an interesting trending repo" and "let Duckln set it up" — the user clicks one key and the existing repo bring-up agent takes over, same as `/repos`. The cancel hint makes the multi-step cloud flow forgiving without changing existing behaviour.

## Plan Item 52 — Bring-up resilience and Rust specialist

Covers: R17 (REQ-BRINGUP-LOOP-1, REQ-BRINGUP-LOOP-2, REQ-BRINGUP-README-1, REQ-RUST-1)

Components:
- `src/duckln/repair_intake.py` — adds `DUCKLN_SYNTHETIC_LINE_MARKERS`, `is_duckln_synthetic_line`, `is_shell_prompt_line`, `strip_synthetic_and_prompt_lines`. Single source of truth for what counts as "Duckln's own log narrative" vs real command output. Reused by the recovery classifier and the search-query builder so we never duplicate filter rules.
- `src/duckln/repo_bringup.py` — (1) `_execute_plan_with_bounded_recovery` no longer falls back to the formatted `failure_message` when stderr/stdout are empty; `raw_failure_output` is `""` instead. (2) `_apply_recovery_decision` no longer ORs `failure_message` into `error_output` for `assess_failed_bringup`. (3) `DebugRecoverySpecialist._classify_failure` scrubs synthetic and shell-prompt lines before substring-matching tokens like `cargo` or `rust`. (4) `infer_readme_workflow_steps` now always consults the LLM classifier when a README exists and prefers the LLM result; the heuristic only wins when the LLM returns nothing. (5) `_readme_workflow_snippet` sends the full README (truncated only at 16k with a tail slice) so commands documented outside Install/Quick-start headings are still reachable. (6) `_is_valid_llm_readme_command` allows `&&`, `;`, `||` chains when each segment passes the safety assessor and is not piped into `sh`/`bash`. (7) New `RustRepoSetupSpecialist` plans `cargo --version` → `cargo fetch` → `cargo build --release` and is selected for `RepoFamily.RUST`. (8) `classify_repo_family` routes `Cargo.toml` / `rust-toolchain.toml` to `RepoFamily.RUST` ahead of the generic CPP_NATIVE fallback. `_suggest_alternate_repo_family` adds Rust as a reroute target.
- `src/duckln/subagents.py` — registers the Rust subagent runtime descriptor and the contract definition so `select_subagent_descriptor` picks the Rust playbook for Cargo repos.
- `src/agent/playbooks/rust.md` — new playbook covering allowed setup paths (`Cargo.toml`, `rust-toolchain.toml`), run verification (`cargo run --release` vs `target/release/<bin>`), guardrails, and cross-platform `rustup` notes.
- `src/duckln/web_runtime.py` — new `_sanitize_error_lines` helper (shared by `_exact_error_query_fragment` and `_build_search_query`) drops shell-prompt and Duckln-narrative lines. `_search_ddg_candidates` and `_search_bing_candidates` now retry once on transient connection errors, record per-provider diagnostics, and fall back to Bing not just on empty results but also on DuckDuckGo connection failures. New `describe_search_failure()` exposes the diagnostic to the caller in `main.py`, replacing the generic "could not reach DuckDuckGo" line with the actual reason.
- Tests: `tests/test_repo_bringup_recovery_input.py`, `tests/test_web_runtime_query_sanitization.py`, `tests/test_repo_bringup_readme_llm.py`, `tests/test_repo_bringup_rust.py` (18 new tests).

Why: the JustHireMe screenshot showed Duckln looping on a Node repo because every failure cycle re-fed Duckln's own narrative ("Specialist route: rust. Toolchain: cargo. Fatal line: …") into the classifier, which substring-matched "cargo" and rerouted to a non-existent Rust specialist; the resulting "search query" was the narrative text itself, so DuckDuckGo returned nothing. Plus the system never asked the LLM to actually read the README — meaning Duckln was running generic prereq probes instead of the install steps the README documents. After this plan item, the classifier sees only real stderr, the LLM always extracts README commands when a README exists, and `Cargo.toml` repos finally have their own specialist.

## Plan Item 53 — Self-skill-acquisition after successful repair

Covers: R18 (REQ-SKILL-SELF-1, REQ-SKILL-SELF-2, REQ-SKILL-SELF-3, REQ-SKILL-SELF-4)

Components:
- `src/duckln/prompts.py` — new `build_repair_skill_prompt(*, failed_command, error_output, recovery_command, repo_family, repo_name, framework) -> str`. Asks the LLM to return a JSON object with keys: `slug`, `title`, `trigger_pattern`, `fix_sequence` (list), `verification`, `notes`. Strips synthetic/prompt lines from `error_output` via `strip_synthetic_and_prompt_lines` from `repair_intake.py` before including it. Total prompt kept under 2000 tokens.
- `src/duckln/repo_bringup.py` — new `_generate_repair_skill_note(*, failed_command, error_output, recovery_command, repo_family, repo_name, framework, llm_call) -> tuple[str, str, str] | None`. Called inside `_apply_recovery_decision` after verification passes on a recovery attempt (not the first attempt). Returns `(slug, title, summary_markdown)` or `None` on malformed LLM JSON. Does NOT persist — returns content only so the caller controls approval. Also extends `inspect_repo_for_bringup()` to scan `memory/skills/` for slugs matching the repo family and pass the first match as `prior_skill_hint: str | None` to the specialist's `infer_steps()`.
- `src/duckln/main.py` — new `_offer_skill_approval(mode, *, slug, title, summary, config_dir, approve) -> bool`. In HITL/HOTL: displays the skill note and prompts for explicit yes/no approval before calling `write_skill_memory_state`. In HOOTLWO: calls `write_skill_memory_state` directly and prints `"Skill saved: {title}"`. Returns whether the skill was persisted. Wired into the bring-up call chain immediately after `_generate_repair_skill_note` returns a non-None result. Also enriches the existing post-deploy skill write at line 5945 to include the executed command sequence and verification command (not just metadata).
- Tests: `tests/test_repair_skill_generation.py`, `tests/test_repair_skill_approval.py`, `tests/test_skill_prior_hint.py` (~12 new tests).

Why: every successful repair currently teaches Duckln nothing. The learning dies with the session. This plan item closes the loop: successful repair → LLM generates structured skill → mode-appropriate approval → persisted to `memory/skills/` → loaded as a prior hint on the next bring-up of the same repo family. HITL/HOTL users stay in control; HOOTLWO users get silent autonomous improvement.

## Plan Item 54 — Break the bring-up infinite loop

Covers: R19 (REQ-BRINGUP-NOLOOP-1, REQ-BRINGUP-NOLOOP-2, REQ-BRINGUP-NOLOOP-3, REQ-BRINGUP-NOLOOP-4, REQ-BRINGUP-NOLOOP-5)

Components:
- `src/duckln/repair_intake.py` — new `parse_install_hint(error_text) -> tuple[str, str] | None` returning `(package_manager, install_command)` for apt/dnf/yum/brew hints in stderr; new `fingerprint_stderr(text)` reusing `strip_synthetic_and_prompt_lines` to compute a stable 200-char signature. Both functions are pure — no side effects.
- `src/duckln/repo_bringup.py` — (1) `_prerequisite_recovery_plan` consults `parse_install_hint` first; when the OS volunteers an install command, the recovery step uses it (and is validated via `assess_command`) instead of the hardcoded `_node_runtime_install_command` / `_python_runtime_install_command` script. (2) `_execute_plan_with_bounded_recovery` tracks `seen_failures: dict[tuple[str, int], str]`; on the second matching `(command, exit_code, fingerprint)` the loop returns a `setup_failed` `RepoBringUpResult` with a one-sentence message naming the command and the first line of stderr — no further recovery is attempted. (3) `_infer_start_command` surfaces `package.json` scripts (`dev`, `start`, `serve`, `preview`) when no run command was inferred elsewhere, before the caller falls back to web-search discovery.
- `src/duckln/main.py` — (1) `_run_runtime_repair_workflow` records `started_at` at entry, defines `_RUNTIME_REPAIR_BUDGET_SECONDS = 90`, and bails out before launching any new web tool call once the budget is exceeded (persists `status="repair_timeout"` and displays the elapsed seconds). (2) `_discover_repo_run_command` wraps its three sequential web calls (exact-error evidence, run-command hint, install-command hint) in a single 30-second aggregate cap; remaining calls are skipped once the cap is hit. (3) After reading `workflow.active_runtime_execution_target`, the function reconciles against `_session_execution_target(paths.config_dir)`; if they disagree, the live session target wins and a one-line override notice is displayed.
- Tests: `tests/test_install_hint_parser.py`, `tests/test_prereq_uses_os_hint.py`, `tests/test_repo_bringup_no_repeat.py`, `tests/test_runtime_repair_budget.py`, `tests/test_stale_runtime_target.py`, `tests/test_package_json_scripts_surfacing.py` (~25 new tests).

Why: the JustHireMe screenshot showed Duckln stuck for 2m 46s on a Node repo with no Node installed. The OS literally suggested `sudo apt install nodejs` in stderr; Duckln ignored that hint and ran a curl-to-bash NodeSource pipeline that needs sudo password, then retried the same probe 5+ times because nothing dedupes identical failures, then spun on web searches because nothing budgets the runtime repair workflow, while injecting `"Google Cloud Linux VM"` into search queries because a stale `active_runtime_execution_target=gcp` leaked from a prior session. After this plan item: parse the OS hint, deduplicate, enforce time budgets, reconcile stale targets, and surface `package.json` scripts directly. Any future failure mode is bounded by the same guards.

## Plan Item 55 — Fix the runtime repair loop and universal repo launch

Covers: R20 (REQ-RUNTIME-NOLOOP-1, REQ-RUNTIME-NOLOOP-2, REQ-RUNTIME-NOLOOP-3, REQ-RUNTIME-NOLOOP-4, REQ-RUNTIME-NOLOOP-5, REQ-RUNTIME-NOLOOP-6)

Components:
- `src/duckln/repair_intake.py` — extend `DUCKLN_SYNTHETIC_LINE_MARKERS` with the Plan 54/55 narrative prefixes (`"duckln tried"`, `"stopping the repair loop"`, `"duckln using the os install hint"`, `"duckln spent"`, `"duckln noticed a stale"`, `"duckln running:"`, `"duckln installing prerequisite:"`, `"duckln wants to run:"`) so they are stripped before any captured stderr seeds a search query.
- `src/duckln/main.py` — (1) `_build_runtime_prerequisite_plan` now consults `parse_install_hint` on the incident's raw fatal_line + relevant_lines; when the OS volunteers an install command and `assess_command` does not block it, the prerequisite plan's `install_command` is replaced with the parsed hint and the installer source is tagged `os-hint:<package_manager>`. (2) Module-level `_RUNTIME_PREREQUISITE_FAILED: dict[tuple[str, str], str]` tracks the last stderr fingerprint per `(repo_url, install_command)` pair; `_execute_runtime_prerequisite_install` calls `_runtime_install_recently_failed_same` after each install/verification failure and short-circuits with status `stopped_repeated_failure` and a one-line message when the same `(command, fingerprint)` repeats. The state is cleared on successful install. (3) `_execute_runtime_prerequisite_install` emits `Duckln installing prerequisite: {command}` before running the install so the user always sees forward motion. (4) The top-level `Do you want Duckln to solve the {repo} issue?` prompt is replaced with `Duckln wants to run: \`{action}\`\nReason: {first stderr line}\nApprove repair of {repo}? [y/n]`. (5) The orchestrator handles the new `stopped_repeated_failure` status as a terminal `runtime_prerequisite_repair_loop_stopped` phase, persisting it through `write_workflow_state` and surfacing the inline escalation chooser.
- `src/duckln/repo_bringup.py` — `_infer_start_command_from_files` is extended with: `pyproject.toml` PEP 621 `[project.scripts]` and `[tool.poetry.scripts]` entries; Go `cmd/` sub-modules → `go run ./cmd/<name>` (single sub-dir) or `go run ./cmd/...` (multiple); C++ `CMakeLists.txt` + `build/` → first executable in `build/`. New helper `_infer_pyproject_script_command(pyproject_path)` reads PEP 621 / Poetry scripts via `tomllib`.
- Tests: `tests/test_synthetic_markers.py`, `tests/test_runtime_dedup.py`, `tests/test_runtime_os_hint.py`, `tests/test_runtime_approval_prompt.py`, `tests/test_run_command_inference.py` (~20 new tests).

Why: Plan 54 fixed the SETUP phase loop in `repo_bringup.py`. The RUNTIME phase in `main.py` is a completely separate code path that re-ran the same `node -e "..."` verification probe and the same hardcoded NodeSource curl pipeline indefinitely, gave the user a vague "Do you want Duckln to solve the JustHireMe issue?" prompt with no command names, and showed only "Working... triaging the runtime blocker" for 4+ minutes. Plan 55 ports Plan 54's three guardrails (OS install hint, dedup, status streaming) into the runtime path, makes the approval prompt actionable, prevents Plan 54's own dedup messages from leaking into web search queries, and extends run-command inference so Duckln can launch any supported repo family from local files alone.

## Plan 56 — Fix the three root causes of the 15-minute runtime hang

- `src/duckln/repo_bringup.py` — `_execute_plan_with_bounded_recovery` dedup short-circuit return now explicitly sets `should_offer_repair=False`. This prevents the outer orchestrator from calling `_run_runtime_repair_workflow` and the `for attempt` run loop when a prerequisite failed with a repeated identical error — the exact cause of the false `INSTALLED_HEALTHY` narration and the 15-minute spin.
- `src/duckln/main.py` — (1) `_DRAIN_TOTAL_BUDGET_SECONDS = 180` constant added; `_drain_terminal_runtime_incidents` records `drain_started_at = time.monotonic()` before the incident loop and breaks with a "Manual action needed" message if the cumulative elapsed time exceeds 180 s — previously each call to `_run_runtime_repair_workflow` got a fresh 90 s window, allowing 90s × N incidents = 15+ minutes. (2) Budget guard added immediately before `_attempt_runtime_prerequisite_install`: if `_budget_exceeded()` is already True when we reach the install step, we emit a skip message and return early rather than starting a potentially-slow install + approval round outside the budget window.
- Tests: `test_bringup_stopped_repeat_no_repair.py`, `test_drain_total_budget.py`, `test_budget_before_prereq_install.py` (7 new tests).

Why: After Plans 54 + 55, Duckln still hung for 15+ minutes on JustHireMe (and any repo with a missing runtime prerequisite). Three stacked bugs caused this: (1) `stopped_repeated_failure` from the setup dedup still had `should_offer_repair=True`, so the outer orchestrator tried to run the app anyway and entered the runtime repair loop. (2) The runtime repair loop in `_drain_terminal_runtime_incidents` called `_run_runtime_repair_workflow` once per incident, each call resetting the 90 s timer — so N incidents × 90 s = uncapped total. (3) The budget was not checked before the prerequisite install step inside `_run_runtime_repair_workflow`, allowing a slow install to silently exceed the per-call budget.

## Plan 57 — End-to-end README-driven setup, run, and usage display

- `src/duckln/readme_skill.py` (NEW): Heuristic README extractor — finds Requirements/Prerequisites sections, identifies known tools (node, npm, python, rust, uv, cargo, go, docker, pnpm, yarn, poetry, etc.), extracts version constraints, returns `ReadmeMetadata` with prerequisites + usage_summary + usage_example + confidence. Reuses the existing `_KNOWN_PREREQUISITES` table for install paths per package manager.
- `src/duckln/repo_bringup.py`: `RepoSetupSpecialist.build_plan` now prepends probe+install steps for every README-declared prerequisite (Plan 57 Phase 2). New helper `_build_readme_prereq_preflight_steps` reads the README and produces `RepoBringUpStep` instances with `source="readme-prereq"`. Playbook validator updated to accept the new source.
- `src/duckln/main.py`: (1) `_build_runtime_prerequisite_plan` (Phase 4) now calls `_runtime_readme_declared_extras` to extract additional declared prerequisites and appends them to the plan's `reason` line so the approval prompt names the full toolchain. (2) After successful run, `_emit_repo_usage_summary_and_offer_demo` (Phase 5) extracts usage info from the README, displays the summary, asks the user whether to surface the example, and persists usage info to the existing skill memory.
- Tests: `test_readme_skill.py` (18 tests), `test_readme_prereq_in_build_plan.py` (4 tests), `test_runtime_repair_readme_extras.py` (3 tests) — 25 new tests.

Why: Even after Plans 54-56, Duckln failed to run JustHireMe (and any repo with multiple system prerequisites) because the setup phase parsed README commands but ignored the Requirements table. Phase 2 installs ALL declared prereqs sequentially before install commands run, removing the reactive one-at-a-time prereq loop. Phase 4 makes the runtime repair approval prompt explicit about what else the README requires. Phase 5 closes the loop by telling the user how to actually USE the running repo with a concrete example.

## Plan 58 — Target-aware install paths + cross-session failure memory

- `src/duckln/repo_bringup.py`: `_build_readme_prereq_preflight_steps` derives `effective_os` from `execution_target` (Bug A); filters out code-block-discovered tools that have no install path on the active target (Bug B integration); consults `lookup_recent_failure` before adding each step and persists failures via `write_failure_memory_state` when setup dedup fires (Bug D).
- `src/duckln/readme_skill.py`: New `_scan_code_blocks_for_tools` always-on pass scans fenced bash/sh/console code blocks; `_strip_prefixes_and_first_token` removes sudo, env-var assignments, and wrapper prefixes; shell builtins are skipped; unknown tools register as probe-only (Bug B).
- `src/duckln/safety.py`: `assess_command` gains optional `execution_target` parameter; blocks `(sudo )?brew` for `vm`/`aws`/`gcp`/`ssh` targets (Bug C).
- `src/state/access.py`: New `write_failure_memory_state`, `read_failure_memory_state`, `lookup_recent_failure` functions write/read JSON-in-markdown failure records under `memory/failures/<slug>.md` (Bug D).
- `src/agent/memory.py`: New `FAILURES_DIR_NAME = "failures"` constant.
- `src/duckln/main.py`: `_record_runtime_install_failure` now takes `config_dir`/`execution_target`/`exit_code` and dual-writes to persistent log; `_runtime_install_recently_failed_same` also consults persistent log on cache miss (Bug D).
- Tests: `test_readme_prereq_target_aware.py` (4), `test_readme_code_block_scan.py` (7), `test_safety_blocks_brew_on_linux_vm.py` (7), `test_persistent_failure_memory.py` (7) — 25 new tests.
- Test update: `test_repo_bringup.ScanReadmeForRunCommandsTest.test_readme_workflow_prefers_recommended_install_sequence` updated to acknowledge prepended readme-prereq steps.

Why: After Plan 57, Duckln still failed on JustHireMe-on-Ubuntu-VM because (a) the local Mac's OS was used to pick the package manager → brew on Ubuntu, (b) `npm` wasn't extracted because it lived only in code blocks (Requirements table only listed Node.js), (c) failed commands kept getting re-proposed on every Duckln restart because failure tracking was in-memory only. Plan 58 closes all four gaps.

## Plan 59 — Close the three Plan 58 limitations

- `src/state/access.py`: Add `_GLOBAL_FAILURE_SLUG = "_global"`; add `_is_environmental_failure` classifier; refactor `write_failure_memory_state` to dual-write to global on environmental failures; refactor `lookup_recent_failure` to fall back to global after per-repo miss. Internal helper `_write_single_failure_file` extracted for reuse.
- `src/duckln/repo_bringup.py`: Add `write_failure_memory_state` calls at two sites in `_execute_plan_with_bounded_recovery` — first-failure (before `seen_failures[dup_key] = fingerprint`) and recovery-exhausted (inside the `recovery_count >= max_recovery_attempts` branch). Add `_llm_refine_readme_prereqs`, `_parse_llm_refinement_json`, `_apply_refinement_to_heuristic`, `_read_llm_refinement_cache`, `_write_llm_refinement_cache`, `_url_slug`, `_resolve_config_dir_from_inspection`. Wire LLM refinement into `_build_readme_prereq_preflight_steps` after heuristic extraction with fail-open semantics.
- Tests: `test_global_failure_memory.py` (11), `test_recovery_exhausted_persistence.py` (3), `test_llm_prereq_refinement.py` (11) — 25 new tests.

Why: Plan 58 fixed Bug A/B/C/D for JustHireMe but left three gaps. Plan 59 fills them. Now Duckln learns environmental failures across repos (Fix 1), records every persistent setup failure not just dedup-fires (Fix 2), and optionally refines heuristic prereqs with an LLM audit constrained to a safety allow-list (Fix 3).

## Plan 60 — User-local installer PATH gap + None exit_code crash

- `src/duckln/readme_skill.py`: 4 probe strings updated. `rust`, `cargo`, `uv`, `poetry` probes now use `command -v <tool> >/dev/null 2>&1 && <tool> --version || "$HOME/<path>/<tool>" --version`. Catches freshly-installed binaries even when PATH wasn't sourced.
- `src/duckln/repo_bringup.py`: 4 call sites (dup_key tuple + 3 write_failure_memory_state calls) updated to `int(result.exit_code) if result.exit_code is not None else -1`. Eliminates the `TypeError: int() argument must be a string, a bytes-like object or a real number, not 'NoneType'` retryable error.
- Tests: `test_userlocal_install_probes.py` (5 tests), `test_int_exit_code_none_safe.py` (3 tests). Plus one Plan 59 test's char window widened to accommodate the longer None-safe line.

Why: After Plan 59 the JustHireMe Ubuntu VM screenshot still showed setup stuck at step 3/8 ("Ensure rust is installed"). Rustup installed cargo successfully (DUCKLN-DONE:0) but Duckln's next probe failed because cargo is at `~/.cargo/bin/cargo` not on PATH. Concurrently a `TypeError` was firing inside Plan 58/59's dedup code path. Plan 60 closes both.

## Plan 61 — Production-ready bring-up across OSes (six gaps closed)

- `src/duckln/readme_skill.py`: `_KNOWN_PREREQUISITES` table extended with 5th `install_winget` slot for all installable tools (node, python, rust, uv, go, docker, git, cmake). `ReadmePrerequisite` dataclass gains `install_winget` field. `install_command_for("winget")` returns `winget install --id ... --silent ...`. `detect_package_manager` now returns `"winget"` for Windows.
- `src/duckln/repo_bringup.py`: `_build_readme_prereq_preflight_steps` filter accepts `install_winget`. Three `ReadmePrerequisite(...)` constructors thread `install_winget` through. LLM classifier schema in `_default_llm_readme_classifier` extended with `prerequisites + usage_summary + usage_example`. New `_parse_llm_readme_extras` parser. Unknown-tool code path now calls `consult_authoritative_source` and persists guidance via new `_persist_unknown_tool_guidance`. Helper `repo_slug_safe` exposes a stable per-repo slug.
- `src/duckln/safety.py`: `assess_command` rejects `winget`/`choco` on Linux VM targets and `apt` on `windows_local`.
- `src/duckln/repair_intake.py`: `classify_apt_failure(stderr)` and `apt_failure_guidance(category)`. Four categories: sudo_password, network, locale, lock_file. Each gets actionable OS-specific guidance.
- `src/duckln/main.py`: `/failures` slash command + dispatcher + `_handle_failures_command` with show/clear/window subcommands. Runtime install failure path displays apt guidance before recording the failure.
- `src/duckln/config.py`: New `failure_window_hours: float = 24.0` field on AppConfig. Added to CONFIG_STATE_KEYS + serializer + deserializer with bounds [0.5, 168]. New `_parse_failure_window_hours` helper.
- `src/state/access.py`: `lookup_recent_failure` reads window from AppConfig when `window_hours` is None. New `_failure_window_hours_from_config` helper.
- `src/duckln/clarify_prompts.py` (NEW): CLI + Textual adapters for multi-choice clarification. `ClarifyPrompt` callable type. `resolve_clarify_prompt` picks the best available.
- `src/duckln/authoritative_sources.py` (NEW): `_AUTHORITATIVE_PUBLISHERS` allow-list of 25 trusted technical domains. `consult_authoritative_source` does a single bounded search, filters results, caches 24h. `is_authoritative_url`, `format_user_guidance`.
- `src/duckln/readme_link_follower.py` (NEW): `extract_install_links`, `follow_readme_link`, `is_link_follow_allowed_for_repo`, `record_link_follow`. Same-domain or allow-list filter, 2-link/24h cap per repo.
- Tests: 7 new test files (`test_windows_package_manager.py`, `test_apt_failure_classifier.py`, `test_failures_slash_command.py`, `test_multi_choice_clarify.py`, `test_authoritative_source_consult.py`, `test_readme_link_following.py`, `test_llm_classifier_returns_prereqs.py`) — 59 new tests. Plus 5 existing tests updated for the new schema (test_readme_skill, test_llm_prereq_refinement, test_onboarding, test_state_store, test_main).

Why: After Plan 60, Duckln still left six honest gaps: Windows uncovered, apt failures unclassified, 24h window untunable, no doc-link following, no multi-choice clarification, heuristic-only README extraction, and silent fall-through on unknown tools. Plan 61 closes all six using existing infrastructure where it exists (slash dispatcher, AppConfig persistence, duckln_select, show_choice_overlay, fetch_web_reference_summary, internet_search_summary).

## Plan 63 — Live indicators render correctly; bundled Nerd Font; `+` dropdown

- `src/duckln/glyphs.py` (NEW): nerdfont codepoint constants resolved via `nerdfonts.icons` dict.
- `src/duckln/font_setup.py` (NEW): per-OS auto-installer + VS Code env detection + settings.json guidance message.
- `src/duckln/assets/fonts/SymbolsNerdFont-Regular.ttf` (NEW, ~2MB, SIL OFL 1.1).
- `src/duckln/assets/fonts/LICENSE-SymbolsNerdFont.txt` (NEW): OFL license text.
- `src/duckln/connection_status.py`: new `status_text_segment(status) -> (glyph, rich-style)` helper using `DOT_SOLID` and one of `green`/`orange1`/`red`.
- `src/duckln/textual_ui.py`: `_status_text` now returns a Rich `Text` (built via `.append`) so dots render with actual colour. `_render_status_bar` passes a `Text` through unchanged. `#internet-icon` removed from `#input-bar` (chat input now visible again). New `#attach-overlay` Vertical containing an `OptionList(id="attach-list")` with three options. `show_plus_dropdown_overlay`, `_compose_browse_web_label`, `_hide_attach_overlay`, `_handle_attach_selection`, `_toggle_internet_skill` methods added. `DucklnSplitPaneApp.__init__` gains an optional `config_dir` parameter; the `+` dropdown uses it to call `internet_skill.set_internet_enabled`.
- `src/duckln/config.py`: new `font_setup_acknowledged: bool = False` field; CONFIG_STATE_KEYS / serialize / deserialize updated.
- `src/duckln/main.py`: calls `ensure_nerd_font_installed()` before `build_chat_interface`. After `chat.start()`, if `is_vscode_terminal()` AND `not font_setup_acknowledged`, displays the settings.json guidance and persists acknowledgement.
- `pyproject.toml`: adds `nerdfonts>=1.0,<2.0` dependency and `[tool.setuptools.package-data]` entries for the bundled font + license.
- Tests: `test_glyphs.py` (3), `test_font_setup.py` (12), `test_status_text_segments.py` (4), `test_header_renders_text_not_markup.py` (3), `test_input_bar_layout.py` (2) — 25 new tests. Plus 2 existing exact-dict assertions updated for the new `font_setup_acknowledged` field.

Why: The Plan 62 header showed literal bracket markup because `_render_status_bar` stripped ANSI and rebuilt segments without parsing Rich markup. The chat input vanished because the new `#internet-icon` Static had no width constraint and squeezed `#chat-input` off the visible area. Plan 63 fixes both with a Text-object header path, moves the internet icon into a proper `+` dropdown, and ships a bundled Symbols Nerd Font so the icons (status dots, globe, slash, plus, upload, document) render with the user's actual terminal font fallback.

## Plan 65 — Multi-Agent Harness Foundation

- `src/duckln/harness/__init__.py`: package entry, exports.
- `src/duckln/harness/tools.py`: `ToolSpec`, `ToolResult`, `ToolRegistry`, 11 default tools (Phase 1, ~420 LOC).
- `src/duckln/harness/agent_def.py`: `AgentDefinition` dataclass + YAML-frontmatter loader (Phase 2).
- `src/duckln/harness/agents/*.md`: 6 pilot agent specs (supervisor, node_typescript_specialist, recovery_coordinator, investigate_agent, search_agent, memory_agent).
- `src/duckln/harness/state.py`: `AgentState`, `Observation`, `ProposalAttempt`, `AgentBudgets`, `AgentResult` (Phase 3).
- `src/duckln/harness/loop.py`: `run_agent` reasoning loop + state-render + decision parser (Phase 3, ~280 LOC).
- `src/duckln/harness/bus.py`: in-memory pub/sub MessageBus with bounded history (Phase 4).
- `src/duckln/harness/coordinator.py`: `run_coordinator`, `SpawnRequest`, `ConcurrencyGate`, `make_gated_llm_client` (Phase 4).
- `src/duckln/harness/trace.py`: TraceLogger + redaction + slash-command renderers (Phase 5).
- `src/duckln/harness/recovery_flow.py`: `run_multi_agent_recovery` entry point with `DUCKLN_HARNESS=1` feature flag (Phase 6).
- `src/duckln/ai_client.py`: `build_default_llm_client_or_none` wraps configured provider as a harness LLM client.
- `src/duckln/repo_bringup.py`: exhausted-recovery branch escalates to harness when feature flag set.
- `src/duckln/main.py`: `/agents`, `/agents trace`, `/agents costs` slash commands.
- Tests: `test_harness_tools.py` (33), `test_harness_agent_def.py` (31), `test_harness_loop.py` (25), `test_harness_bus.py` (12), `test_harness_coordinator.py` (13), `test_harness_trace.py` (17), `test_harness_recovery_flow.py` (31) — 162 new tests.

Why: Duckln was nominally multi-agent but architecturally a sequential pipeline. Plan 65 builds the harness substrate so future capabilities become declarative configs (~200 LOC each) instead of bespoke Python modules. Behind `DUCKLN_HARNESS=1` so legacy paths preserve byte-identical behaviour.

## Plan 67 — Plan Mode (Codex/Claude-style upfront planning + reasoning)

- `src/duckln/plan_mode.py` (NEW, ~1100 LOC): `PlanStep`, `PlanRecord`, `RepoUnderstanding`, `ClarificationQuestion`, `AttributionResult` dataclasses + the five-stage pipeline (`gather_repo_understanding`, `_propose_candidates`, `_critique_and_order`, `_classify_and_verify`, `_collect_clarifications`), `generate_plan`, `parse_plan_json`, `validate_plan_dict`, `render_plan_markdown`, `parse_plan_markdown`, `plan_to_bringup_steps`, `attribute_failure`, `amend_plan`, `mark_status`, `looks_like_multistep_request`.
- `src/duckln/glyphs.py`: 5 new Plan Mode glyphs (`PLAN_NOTE`, `PLAN_APPROVED`, `PLAN_REJECTED`, `PLAN_PENDING`, `PLAN_EDIT`) — all map to nerdfonts 1.0.1 codepoints.
- `src/duckln/config.py`: `AppConfig.plan_mode_enabled: bool` + SQLite snapshot round-trip via `CONFIG_STATE_KEYS`.
- `src/duckln/prompts.py`: 4 system prompts (`SYSTEM_PROMPT_PLAN_PROPOSE`, `..._CRITIQUE`, `..._CLARIFY`, `..._ATTRIBUTE`).
- `src/state/access.py`: `write_pending_plan`, `read_pending_plan`, `clear_pending_plan`, `append_plan_history`, `list_plan_history`, `clear_plan_history`; `PLAN_MODE_HISTORY_LIMIT=50`.
- `src/duckln/ui.py`: `render_plan_panel`, `render_plan_oneline`, `render_clarification_prompt`, `render_error_attribution`.
- `src/duckln/textual_ui.py`: `_plan_mode_header_segment` + header injection so the clipboard glyph appears in the live status row.
- `src/duckln/harness/plan_supervisor.py` (NEW, ~250 LOC): `run_plan_supervisor`, `PlanSupervisorResult`, `REQUIRED_AGENT_NAMES`, `_local_plan_pipeline` fallback.
- `src/duckln/harness/agents/supervisor.md`: updated to coordinate `repo_inspector` → `planner` → `critic`.
- `src/duckln/harness/agents/repo_inspector.md` (NEW): read-only repo + system inspection.
- `src/duckln/harness/agents/planner.md` (NEW): candidate-step proposer.
- `src/duckln/harness/agents/critic.md` (NEW): critique + ordering + DAG.
- `src/duckln/harness/__init__.py`: exports `run_plan_supervisor`, `PlanSupervisorResult`, `REQUIRED_AGENT_NAMES`.
- `src/duckln/main.py`: `/plan`, `/plan on`, `/plan off`, `/plan show`, `/plan approve`, `/plan reject`, `/plan edit`, `/plan history`; `_handle_plan_command`.
- `src/duckln/repo_bringup.py`: `bring_up_selected_repo` gains `plan_mode_enabled` kwarg; `_generate_plan_for_bringup` short-circuits before any cloning; `resume_with_approved_plan` is the strict-adherence executor with `_attempt_amendment_and_halt`.
- `src/duckln/conversation_agent.py`: `_plan_mode_nudge_for_message` redirects multi-step free-text requests to the planning flow when Plan Mode is on.
- Tests: `test_plan_mode_understanding.py` (5), `test_plan_mode_pipeline.py` (12), `test_plan_mode_state.py` (4), `test_plan_mode_render.py` (5), `test_plan_mode_command.py` (8), `test_plan_mode_supervisor.py` (3), `test_plan_mode_attribution.py` (5), `test_plan_mode_integration.py` (4) — 45 new tests added on top of Plan 65's 1378 baseline = 1423 passing.

Why: the user wanted Codex/Claude-style upfront planning where Duckln "understands the repo, reasons if the step is correct before adding to plan in correct sequence", asks the user "only when it genuinely needs to" (cap of 3 clarifications), and "follows only the plan, telling the user why an error occurred and the fix it is implementing" (visible attribution + amendments, capped at 3). Plan 67 also wires the supervisor agent that Plan 65 shipped as a markdown spec but never invoked from Python.

## Plan 68 — Plan Mode bugfixes: wire into `/repos`, `+` toggle, all targets

- `src/duckln/main.py`: `_orchestrate_repo_to_running` short-circuits to the plan-mode branch when `plan_mode_enabled`; the `/repos` VM-warning setup (line ~1699) and `/explore` setup (line ~2532) now pass the flag; `/plan` dispatch threads `terminal_interface`; `_handle_plan_command` `/plan approve` reads `_session_execution_target`/`_session_vm_name` and threads `vm_name`/`pane_executor` into `resume_with_approved_plan`, and treats a pending amendment as a pause (not a terminal failure).
- `src/duckln/textual_ui.py`: `_compose_plan_mode_label` + new "Plan Mode — currently ON/OFF" entry in the `+` dropdown + `_handle_attach_selection` `plan_mode` branch + `_toggle_plan_mode` (load→flip→save), mirroring the internet toggle.
- `src/duckln/plan_mode.py`: `gather_repo_understanding` gains `override_detected_files` so remote-target file lists drive Stage-1 understanding.
- `src/duckln/repo_bringup.py`: `_generate_plan_for_bringup` trusts the live `execution_target` and, for vm/aws/gcp, lists repo files inside the target via `inspect_remote_repo_setup_files`; `resume_with_approved_plan` reads the live target, accepts `vm_name`/`pane_executor`, resolves the runtime cwd, and wraps every step via `_wrap_command_for_execution_target` (local→raw, vm→`multipass exec`, aws/gcp→cloud remote-exec), halting clearly when the target is unresolvable.
- Tests: `test_plan_mode_targets.py` (5) — orchestrate routes to plan mode (no clone/install), local runs raw command, vm wraps with `multipass exec`, config-flip round-trip, glyph resolves. = 1428 passing.

Why: Plan 67 added the `plan_mode_enabled` kwarg + branch inside `bring_up_selected_repo` but **no caller passed it**, so `/repos` always ran the legacy setup despite Plan Mode being on. There was also no UI toggle, and the approved-plan executor hardcoded local execution. Plan 68 wires the flag through the initial-setup entry points, adds the `+` toggle button, and makes both generation and execution work on local/container/VM/AWS/GCP.

## Plan 69 — Plan Mode hardening: LLM timeout, no pre-approval execution, editable plans, numbering

- `src/duckln/ai_client.py`: `CONVERSATION_TIMEOUT_SECONDS=120` + `OLLAMA_CONVERSATION_TIMEOUT_SECONDS=300`; new `conversation_timeout_seconds` adapter attr used for the conversation POST (the 2 s Ollama / 10 s cloud timeout was only ever right for the model-list GET); one bounded retry on a transport error so cold-loading local models don't fail the first call.
- `src/duckln/main.py`: `/plan edit` no longer spawns a TTY editor inside the Textual UI (it garbled + hung) — it writes the markdown, opens it non-blocking via `_open_path_non_blocking` (`code -r`/`open`/`xdg-open`), and `/plan reload` (alias `/plan edit apply`) re-parses via `_reload_pending_plan_from_disk`; `_activity_message_for_slash_command` gains branded `PLAN_NOTE` labels for `/plan*` (no more false "Processing /plan edit • 23m"); `/plan reload` registered in descriptors.
- `src/duckln/plan_mode.py`: `gather_repo_understanding(family_hint=…)` + `family_hint_from_metadata(framework/category/description)` so a not-yet-cloned remote repo gets an accurate family without touching the target.
- `src/duckln/repo_bringup.py`: `_generate_plan_for_bringup` no longer runs `inspect_remote_repo_setup_files` (which executed `cd … && ls -1A` in the VM during generation — the command the user saw before approval); it now reads only the local clone + catalog metadata. The clone becomes the plan's first step, executed only after `/plan approve`.
- Tests: `test_ai_client.py` (3 — conversation timeout cloud/ollama, retry-once), `test_plan_mode_targets.py` (4 — no remote exec during generation, family from metadata, branded label ×2), `test_plan_mode_command.py` (2 — `/plan edit` non-blocking, `/plan reload`), `test_plan_mode_pipeline.py` (1 — dense 1..N numbering), palette tuple updated. = 1437 passing.

Why: with a local Ollama model every plan failed at the proposal stage because the 2 s timeout killed generation; `/plan edit` opened `nano` inside the Textual split-pane and hung (mouse escape codes, "Processing • 23m"); and Plan 68's remote file-listing executed a command in the VM before the user approved anything. Plan 69 fixes all of it and guarantees steps are numbered 1, 2, 3 ….

## Plan 70 — Unblock stuck failed plans + reliable single-call generation

- `src/duckln/repo_bringup.py`: the `_generate_plan_for_bringup` guard now blocks only on an *actionable* pending plan (`pending`/`amended`/`approved`/`edited`); a stale terminal/failed plan is cleared and regeneration proceeds. (Previously a failed plan with the same repo_slug pinned the user to it forever — `/repos` always replied "a plan is already pending".)
- `src/duckln/plan_mode.py`: `_steps_from_candidates` + `_assemble_ordered_steps` make the critique stage best-effort — `generate_plan` falls back to a deterministic linear ordering of the proposed candidates when critique fails, so a single successful propose call yields a numbered `pending` plan. Only a propose failure (or all-S4) yields `failed`.
- `src/duckln/harness/plan_supervisor.py`: uses `_assemble_ordered_steps` so the harness path keeps the proposed steps on a critique failure (no wasteful re-propose).
- Tests: `test_plan_mode_pipeline.py` (2 — critique-failure→pending fallback, propose-failure→failed), `test_plan_mode_targets.py` (2 — failed plan cleared + regenerates, actionable plan still short-circuits). = 1446 passing.

Why: even after the timeout fix, `gemma2:2b` often failed the second (critique) JSON call, which discarded the good proposed steps and marked the whole plan failed; and that failed plan then blocked every future `/repos`. Plan 70 makes generation reliable from one good LLM call and ensures a failure never traps the user.

## Plan 72 — Full Proper Plan Mode (PRD): plan-first spine (phased)

- Phase 1 — `repo_bringup._generate_plan_for_bringup` now grounds understanding via `inspect_repo_for_bringup` (read-only HTTP) + `classify_repo_family`, and builds a deterministic clone→install→build→run backbone via the per-family `build_plan` specialists (`_grounded_setup_plan`, `_assemble_plansteps_with_clone_and_run`, `_infer_run_command`); `plan_mode.finalize_plan_from_steps`; rewritten `ui.render_plan_panel`/`_plan_field_lines`/`_draw_plan_panel` (wrapped, aligned, coloured). Works with no LLM.
- Phase 2 — new `src/duckln/plan_lifecycle.py`: `PlanLifecycle` state machine + 4 terminal states; clears activity on exit/exception.
- Phase 3 — `plan_mode.critic_review` + `CriticVerdict` + `SYSTEM_PROMPT_PLAN_VERDICT` + `critic.md` verdict mode; mandatory verdict, disclosed `skipped`, deterministic S4/run-less block; wired into `_generate_plan_for_bringup`.
- Phase 4 — `config`: `plan_mode_enabled` defaults ON on load/onboarding (`_parse_optional_bool_default_true`); `plan_mode.plan_first_required`/`require_plan_for_mutation` chokepoint.
- Phase 5 — `plan_mode._redacted` wraps every LLM `user_message` (propose/critique/clarify/attribute/verdict) and learned skills.
- Phase 6 — `repo_bringup._step_allowed_in_mode` tightened: HOOTLWO no longer auto-runs S2/S3; S4 blocked everywhere.
- Phase 7 — activity clears on terminal/exception (lifecycle `on_clear_activity` + existing `_chat_activity_scope` finally).
- Phase 8 — `plan_mode.record_plan_skill`/`plan_skill_signature`; skill written only on verified success in `resume_with_approved_plan`, redacted, keyed by family+os+target.
- Phase 9 — `plan_mode.filter_repeat_clarifications`/`clarification_signature`; `_collect_clarifications` de-dupes; no repeated identical question.
- Phase 10 — consolidated invariant gate + per-family backbone fixtures.
- Tests: test_plan_mode_grounding, test_plan_lifecycle, test_plan_mode_critic, test_plan_first_enforcement, test_plan_mode_redaction, test_plan_mode_hootlwo, test_plan_mode_activity_state, test_plan_mode_learning, test_plan_mode_clarification, test_plan_mode_prd_invariants.

Why: the PRD requires Plan Mode to be the mandatory plan-first gate for all mutating work, with grounded/complete plans, a real critic, redaction, tightened safety, clean terminal states, and a learning loop. Delivered phased; toggle defaults ON, OFF preserves legacy direct behavior.

Partial / follow-up: VM/`/cloud`/Docker interactive handlers expose the chokepoint but are not yet fully rerouted through the lifecycle (repo bring-up is); runtime-repair lifecycle rerouting and the hard spinner ceiling in the Textual render loop are follow-ups.

## Plan 73 — Finish Plan 72 partials + /skills upgrade + self-learning loop

- Phase A (gating): new `repo_bringup.gate_mutation_or_draft` builds an approvable PlanRecord from intended provisioning steps (critic + persist + render, runs nothing). Wired into `/vm` (main.py), `_handle_cloud_create_command` (main.py), and an entry guard in `_run_runtime_repair_workflow` (no auto-repair when plan mode ON).
- Phase B (spinner ceiling): `textual_ui._activity_bar_segments` pure helper + `_ACTIVITY_HARD_CEILING_SECONDS=600`; `_update_activity_bar` stops the spinner and shows a static blocked-notice past the ceiling.
- Phase C (/skills + /learn + /plan retry): `_render_skills_summary` groups auto-learned skills (family·os·target + ✓/⚠ source); new `_render_skill_detail` (`/skills show`), `_clear_learned_skills` (`/skills clear`), `_render_learning_status` (`/learn`), and `/plan retry`. Descriptors + palette test updated.
- Phase D (self-learning loop): `plan_mode` adds `find_matching_skill`, `load_skill_commands`, `record_failure_skill`, `ReflectionNote`, `reflect_on_run`, `persist_reflection`. INJECT: `_generate_plan_for_bringup` uses a matching verified skill's command sequence as the backbone (closes the previously-broken link). REFLECT+EXTRACT: `resume_with_approved_plan` persists a redacted reflection on success (with verified-skill extraction) and writes a `…-avoid` failure skill + reflection on irrecoverable. LOOP: `/plan retry` clears pending so the next `/repos` regenerates with injection.
- Tests: test_plan73_partials.py (gate/runtime-repair/spinner), test_learning_loop.py (inject/reflect/extract/loop), palette + skills smoke.

Why: Plan 72 left VM/cloud/runtime-repair ungated, the spinner could hang, and the learning loop was incomplete (Extract worked but Inject was a dead link, Reflect/Loop missing). Plan 73 finishes the spine and makes Duckln learn from each run — verified runs become injected skills; failed runs become avoid-skills + reflections.

## Plan 74 — Critical fixes: stale-plan pin, system_probe NameError, grounded plan quality

- **Root cause (grounding never ran):** `_generate_plan_for_bringup` referenced `system_probe` which was never a parameter → `NameError` → the deterministic backbone always fell into its except branch ("could not inspect the repo"). The pre-existing pending-plan guard masked it. Fixed: added `system_probe` param + threaded it from `bring_up_selected_repo`.
- **Stale-plan pin:** `/repos` short-circuited with "a plan is already pending", pinning the user to a days-old, wrong plan. Fixed: `/repos` now ALWAYS clears any pending plan and regenerates a fresh grounded plan.
- **Family robustness:** `_grounded_setup_plan` falls back to repo-catalog `family_hint_from_metadata` when the read-only fetch is low-confidence, so the right specialist is chosen (not defaulting to Python).
- **Real rollback for any repo:** `_derive_rollback` always returns concrete steps — baseline `rm -rf <clone dir>` (undoes the whole setup) + ecosystem cleanups (node_modules/.venv/cargo clean/docker compose down); never "no automatic rollback".
- **Command-prominent rendering:** each step now shows the executable command as a `$ ...` headline (cyan), with safety class + ETA demoted to a small trailing annotation.
- Tests updated: `/repos` regenerates (no "already pending"); a stale failed plan is replaced by a fresh clone-first plan.

Why: the user's screenshots showed a stale, Python-flavored plan for a Node repo with no clone/run and "no rollback" — because grounding silently errored on every run and the stale plan blocked regeneration. Plan 74 makes the grounded clone→install→build→run backbone actually run for any repo, with a real rollback.

## Plan 75 — Make Duckln THINK about the repo's stack (scoped prereqs, clone fix, no auto-triage)

- **Reasoning fix (headline):** `_scope_prereqs_to_family` drops prerequisite-install steps for tools that aren't part of the repo's actual stack. A prereq is kept only if it's the detected family's runtime, `git`, a tool referenced by a real install/build/run command, or `docker` when a Dockerfile/compose file is present. So a Node repo no longer installs python/rust/uv/go/docker just because the README prose mentions them. Applied in `_grounded_setup_plan`.
- **Clone failure fix:** `resume_with_approved_plan` now derives a CLEAN repo name (last URL path segment) so the resolved project dir matches the clone target (was slugifying the whole URL → bogus `https---github.com-…` dir), and the clone step runs from the parent (no `cd` into a not-yet-existing dir) while later steps run inside the cloned dir.
- **No auto-triage under Plan Mode:** `_drain_terminal_runtime_incidents` now returns early (draining the queue) when Plan Mode is ON, so the "Working… triaging the runtime blocker" loop no longer fires — a failed approved step already routes through the visible attribution → amendment flow.
- Tests: test_plan_prereq_scoping.py (Node drops unrelated runtimes; Python keeps python/drops node; docker kept with Dockerfile; command-referenced tool kept).

Why: Duckln was blindly installing every tool in the README without reasoning about the target environment or the repo's real stack, and the clone step failed on a malformed cwd. Plan 75 makes the plan reflect the repo's actual stack and fixes the clone so the plan can actually run.

## Plan 76 — Preparing-plan feedback, supervisor never fails, opt-in pre-check, quote-safe prereqs, thinking box

- **Fix A — Preparing-plan + rotating words (R32, REQ-PLAN76-1):** `_generate_plan_for_bringup` emits staged progress ("Preparing the plan for `<repo>`…", "Supervisor is reviewing the plan…"). `textual_ui._activity_bar_segments` appends a rotating verb (`_PROCESSING_VERBS` via `_processing_verb(spinner_index)`) while spinning; the Plan 73 600s hard ceiling is preserved.
- **Fix B — Supervisor never fails (R32, REQ-PLAN76-2):** new `plan_mode._deterministic_supervisor_review` runs a checklist (clones first, installs before run, ends with a run step, no S4, prereqs scoped) and ALWAYS returns `approve`/`revise`/`block` with a ≤5-line rationale. `critic_review` uses the LLM verdict when clean (backfilling an empty reason) and otherwise falls back to the deterministic review — it never returns `skipped`. The panel banner is "Supervisor approved: …" / "Supervisor suggests: …".
- **Fix C — Opt-in pre-check before drafting (R32, REQ-PLAN76-3):** `plan_precheck` config (on|off|ask, default ask). `_plan_precheck_probe` runs read-only `command -v <tool>` + `test -d <dir>/.git` on the active target; `_drop_present_prereqs` removes "Ensure X installed" steps for present tools; `_assemble_plansteps_with_clone_and_run(skip_clone=…)` omits the clone when already cloned. `/plan precheck on|off|ask` persists the choice; `ask` prompts via the approve callback.
- **Fix D — Quote-safe Node prereq (R32, REQ-PLAN76-4):** `_node_npm_prerequisite_check`/`_node_package_manager_prerequisite_check` now return plain `node --version && npm|pnpm|yarn --version` instead of a nested `node -e "…'…'"` one-liner that broke under VM `bash -lc '…'` wrapping.
- **Fix (panel) — Simplified plan display (R32, REQ-PLAN76-5):** `ui.render_plan_panel` drops S0/S1/S2 chips; shows a plain ETA + step count + "N need approval" and renders each command prominently.
- **Fix E — Collapsible "Duckln's thinking" box (R32, REQ-PLAN76-6):** `textual_ui` mounts a `#thoughts-panel` in `#chat-pane` (never the input) with `add_thought`/`clear_thoughts`/`toggle_thoughts`; clicking the header expands/collapses. `main._emit_thought`/`_chat_supports_thoughts` route REDACTED reasoning (detected family, pre-check findings, run command, supervisor rationale) into the box, falling back to dim `· <thought>` lines in the plain CLI.
- Tests: test_supervisor_review.py, test_plan_precheck.py, test_processing_words.py, test_node_prereq_check.py, test_thoughts_box.py.

Why: the supervisor was rendering "⚠ could not complete" (failing its core job), plans were drafted speculatively without checking the target, Step 5's Node check errored under VM wrapping, and Duckln gave no "preparing the plan" feedback or visible reasoning. Plan 76 makes planning legible (progress words + thinking box), trustworthy (supervisor always reviews), and grounded (opt-in pre-check), with quote-safe prereq checks.

## Plan 77 — Version-aware pre-check, approve once, no per-step re-prompts, live thinking, web self-repair

- **Fix 1 — Version-aware planning (R33, REQ-PLAN77-1):** `_detect_required_node_version` reads `package.json` `engines.node` / `.nvmrc` / `.node-version` (local clone or HTTP via `_fetch_github_raw_file`) plus a README hint, returning the required MAJOR (`_node_major_from_spec` takes each OR-clause's leading number). `_plan_precheck_probe` now also runs `node --version` and returns `present_versions`. `_generate_plan_for_bringup` compares them; when the target is lower/unknown it injects `_node_version_install_step` (NodeSource on vm/aws/gcp, Homebrew on local macOS) before install and names the version in the plan context + thinking box.
- **Fix 2 — Plan approval authorizes its steps (R33, REQ-PLAN77-2):** `_step_allowed_in_mode(..., plan_approved, step_origin)` lets approved `planner` steps run without a per-step prompt; `resume_with_approved_plan` passes `plan_approved=True, step_origin=step.origin`. S4 still blocked everywhere; `amendment`/`user` steps still prompt.
- **Fix 3 — Live thinking (R33, REQ-PLAN77-3):** `_generate_plan_for_bringup` emits `_think(...)` BEFORE each blocking call (reading repo, checking version, supervisor review), so the box fills during the wait.
- **Fix 4 — Web self-repair (R33, REQ-PLAN77-4):** in `_attempt_amendment_and_halt`, when the first `attribute_failure` yields no fix and `is_internet_enabled`, `_web_evidence_for_failed_step` runs `search_runtime_issue` (redacted) and a second `attribute_failure(..., web_evidence=…)` pass proposes a grounded fix. Internet off → guidance to run `/internet on`, no silent calls.
- Tests: test_runtime_version_detect.py, test_plan_step_authorization.py, test_web_self_repair.py.

Why: a Vite repo needing Node ≥20 was set up against Node 18 (no version detection), the user was re-prompted per step after approving the whole plan, the thinking box filled only at the end, and a failed step said "no automatic fix available" despite internet access. Plan 77 detects the version up front, treats plan approval as batch authorization, streams reasoning live, and wires the existing web search into the failure-repair path.

## Plan 78 — Enforced supervisor reasoning: evidence-rich steps, deeper critic, one replan loop, re-reviewed recovery, honest model-connection failures

- **Fix A — Honest model-connection failures (R34, REQ-PLAN78-1):** `plan_mode._model_connection_error` distinguishes an unreachable model from a junk response; `critic_review` returns `block` + `external_blocker="model_unreachable"` on connection errors; `_generate_plan_for_bringup` surfaces `MODEL_UNREACHABLE_MESSAGE` and returns without a plan (`waiting_on_user`). Reachable-but-junk still uses the deterministic verdict (Plan 76 preserved).
- **Fix B — Evidence-rich PlanStep (R34, REQ-PLAN78-2):** `PlanStep` gains `target`/`source`/`evidence_excerpt`/`cwd` (defaults "", backward-compatible to_dict/from_dict); `_assemble_plansteps_with_clone_and_run(execution_target=…)` populates target+cwd+source; `render_plan_panel` shows a "Where:" line.
- **Fix C — Deeper critic checks (R34, REQ-PLAN78-3):** `_deterministic_supervisor_review` now revises on a mutating step missing verify/target, a wrong-OS command (via `safety.assess_command`), or a command in `understanding.recent_failures`; `SYSTEM_PROMPT_PLAN_VERDICT` asks the LLM for the same.
- **Fix D — One replan loop (R34, REQ-PLAN78-4):** on `revise`, `_repair_plan_for_revise` deterministically fills missing target/verify on mutating steps and `_generate_plan_for_bringup` re-reviews exactly once.
- **Fix E — Re-reviewed recovery (R34, REQ-PLAN78-5):** `attribute_failure` stamps the fix step with target/cwd/default-verify; `_attempt_amendment_and_halt` runs the amended plan through `critic_review` before offering it (blocks surface the blocker); the failed step's `cwd` rides in the payload.
- **Fix F — Internal-bug surfacing + spinner clear (R34, REQ-PLAN78-6):** `_generate_plan_for_bringup` wraps an impl in a guard that, on an unexpected exception, persists `duckln_internal_bug_reported` + shows a redacted diagnostic; the `/repos` plan-mode branch clears the activity bar in a `finally`.
- **Fix G — Redaction additions (R34, REQ-PLAN78-7):** `redact_sensitive_data` masks `Authorization:` headers and OAuth code/state/access_token/id_token/refresh_token/client_secret values.
- Tests: test_model_connection.py, test_planstep_evidence.py, test_replan_loop.py, test_amendment_review.py, extended test_supervisor_review.py + test_diagnostics.py.

Why: an audit showed the foundations (mandatory verdict, plan-first gating, 4 terminal states, redaction, plan-first recovery) already existed; Plan 78 closes the enforcement gaps so a shallow or unreviewed plan can never reach the user, recovery steps are themselves reviewed, and an unreachable model is reported honestly instead of silently best-efforted.

## Plan 79 — No garbage response ever; resume the last operation on "retry"

- **Fix 1 — Never act on garbage (R35, REQ-PLAN79-1):** `critic_review` now treats the deterministic structural review as the authoritative supervisor and only ENRICHES it with the model. A reachable model that returns an unreadable response is retried once, then discarded — a garbage response is never accepted as a verdict. (Unreachable still surfaces the Plan 78 connection message.)
- **Fix 2 — Resume on retry/try again/check now (R35, REQ-PLAN79-2):** `_looks_like_runtime_repair_request` recognizes resume phrases (retry/try again/try now/check now/check again/resume); `_handle_pending_repo_followup`'s `set_up_repo` branch now resumes in the SAME mode (Plan Mode → re-draft via `bring_up_selected_repo(plan_mode_enabled=True, pane_executor, emit_thought)`); the `/repos` handler persists `pending_next_action="set_up_repo"` in Plan Mode so an interrupted draft (e.g. model unreachable) is resumable.
- Tests: extended test_model_connection.py (retry/discard), test_resume_operation.py (phrases).

Why: the user wanted Duckln to never proceed on a garbage model response, and to pick up the same operation where it left off when they say "retry/check now" (e.g. after starting a model that was unreachable) — instead of starting over or stalling.

## Plan 79b — Smarter, evidence-grounded planning (loopholes L1–L8)

- **L1 — Package-manager correctness (R36, REQ-PLAN79-3):** `_deterministic_supervisor_review` revises when the install command's package manager conflicts with the present lockfile; `_is_run` now recognizes `pnpm/yarn/bun dev|start|serve|preview`.
- **L3/L7 — Logical run command (R36, REQ-PLAN79-4):** `_infer_run_command` reads `package.json` scripts via `_read_repo_package_json` + `_run_command_from_scripts` (`_RUN_SCRIPT_PREFERENCE` prefers dev) formatted for `_package_manager_for(lockfile)`, before repo-knowledge/README.
- **L2 — Environment file (R36, REQ-PLAN79-5):** `_env_example_file` detection → the assembler injects `cp .env.example .env` before the run step.
- **L5 — README evidence (R36, REQ-PLAN79-6):** `_evidence_line_for` attaches the exact (redacted) README line to README-sourced steps; `render_plan_panel` shows a "Source:" line.
- **L6 — Actionable verification (R36, REQ-PLAN79-7):** `_verification_for_run_command` — web servers verify by URL response, others by staying alive.
- **L4 — Surface the URL (R36, REQ-PLAN79-8):** `_extract_served_url` (+ `_vm_ipv4`) parses the served URL from run output and rewrites localhost→VM IP on VM targets; `resume_with_approved_plan` displays it after a run step.
- **L8 — Per-repo learning (R36, REQ-PLAN79-9):** `state.access.write_repo_facts`/`read_repo_facts`; `resume_with_approved_plan` records the working run command + Node version on success, and `_generate_plan_for_bringup` prefers the learned run command + falls back to the learned Node version.
- Tests: tests/test_smart_planning.py.

Why: to make Duckln genuinely reason about how a repo installs and runs — grounded in the repo's lockfile, package.json scripts, env template, and README — surface the running URL, and get smarter per-repo over time.

## Plan 80 — Robust & legible (detect generically, fix deterministically, browse live, show thinking/tasks)

- **Fix 1 (R37, REQ-PLAN80-1):** `_framework_node_requirement` + wired into `_detect_required_node_version` (vite≥6→20, next≥15→20, angular≥19→20…).
- **Fix 5 (R37, REQ-PLAN80-2):** `_detect_required_python/go/rust_version` + `_tool_versions_map` + `_xy_floor`/`_xy_below`; `_python/_go/_rust_version_install_step`; probe records python/go/rust versions; generic injection loop in `_generate_plan_for_bringup_impl`; `.tool-versions`/`.python-version`/`rust-toolchain`/`.node-version` added to `SETUP_FILE_ORDER`.
- **Fix 6 (R37, REQ-PLAN80-3):** `diagnostics.match_deterministic_fix` + `DeterministicFix` + new `ErrorCategory` values; consulted first in `_attempt_amendment_and_halt` (works without an LLM).
- **Fix 9 (R37, REQ-PLAN80-4):** `state.access.write_common_lesson`/`read_common_lessons`; `_common_lesson_fix`/`_record_common_lesson_from_fix` + seeded baseline; consulted at repair and surfaced at draft.
- **Fix 7 (R37, REQ-PLAN80-5):** `web_runtime.gather_repair_fix` browses LIVE (`Browsing <url>…`), fetches real page content via `_fetch_web_reference_summary`, re-ranks, bounded; `_web_evidence_for_failed_step` uses it.
- **Fix 2 (R37, REQ-PLAN80-6):** `☑ Step i — done` per step + "✅ Plan executed — your repo is ready! 🎉"; `/plan approve` shows `outcome.message`.
- **Fix 3 (R37, REQ-PLAN80-6):** `resume_with_approved_plan(emit_thought=…)` streams per-step reasoning; `/plan approve` threads it.
- **Fix 4 (R37, REQ-PLAN80-7):** `config.duckln_ui` (auto|inline|full) + `_parse_duckln_ui`; `build_chat_interface(ui_mode=…)` → inline uses `TerminalChatInterface`; `/ui` command.
- **Fix 8 (R37, REQ-PLAN80-8):** `ui.format_thought_for_seconds` shown before free-text answers; `render_main_task_header` for the objective; `render_step_line(indent=…)` for nested sub-steps.
- Tests: test_plan80_robustness.py, test_ui_mode.py, test_plan_execution_ux.py.

Why: stop erroring on detectable requirements (any ecosystem), fix common failures without a weak LLM, learn pitfalls across all repos, browse the web for real and visibly, and render legibly inline like Claude/Codex.

## Plan 81 — Robust setup for ANY repo (close remaining gaps) + UI verification

- **Fix 1 (R38, REQ-PLAN81-1):** `_parse_env_example_keys` + `_required_env_vars`; the env-copy step names required secret keys.
- **Fix 2 (R38, REQ-PLAN81-2):** `_detect_project_subdir` (workspaces/turbo/nx/lerna) → `_assemble_plansteps_with_clone_and_run(project_subdir=…)` sets env/codegen/run cwd to the app subdir; clone+install stay at root; workspace markers added to `SETUP_FILE_ORDER`.
- **Fix 3 (R38, REQ-PLAN81-3):** `_compose_services` parses `services:`; assembler injects `docker compose up -d <svc>` before run when docker present (`docker_available`), else the gap is surfaced.
- **Fix 4 (R38, REQ-PLAN81-4):** `_codegen_steps` (prisma generate / graphql codegen / build-before-start) inserted after install, before run.
- **Fix 5 (R38, REQ-PLAN81-5):** `diagnostics.match_deterministic_fix` extended — Prisma, native libs (libpq/Python.h/openssl/ffi), Playwright deps, service-unreachable (5432/6379); new `ErrorCategory` values.
- **Fix 7 (R38, REQ-PLAN81-7):** private/unreachable clone (auth error / exit 128) → `DeterministicFix(block=True, block_question=…)`; `_attempt_amendment_and_halt` halts with the question instead of retrying/web-searching.
- **Fix 6 (R38, REQ-PLAN81-6):** `_vm_port_reachable`/`_port_from_url`; `resume_with_approved_plan` verifies the VM-served URL from the host and gives `--host 0.0.0.0`/`ufw allow` guidance when unreachable.
- **Fix 8 (R38, REQ-PLAN81-8):** UI smoke tests — inline build + rich plan-panel render across the new step types.
- Tests: test_plan81_any_repo.py, test_ui_smoke.py.

Why: push first-go success well beyond one repo shape — env, monorepo, services, codegen, native deps, VM port reachability, private repos — with honest one-question blocks only where a human must act.

## Plan 82 — Correct, fast, legible pre-check

- **Fix A+B (R39, REQ-PLAN82-1/2):** `_plan_precheck_probe` rewritten to run ONE delimited script (`_build_precheck_script`) and parse it with `_parse_precheck_block` + `_runtime_major` — strips `DUCKLN-DONE` marker noise, so no output bleed, no false "go present", and `node v18.19.1 → "18"` (never "0"). 16 round-trips → 1.
- **Fix B (R39, REQ-PLAN82-2):** version-compare hardened — `"0"`/unparseable target → "unknown", never "Node 0".
- **Fix C (R39, REQ-PLAN82-3):** `emit_thought` threaded into the probe; staged role-labelled live thoughts (Inspector → Pre-check w/ real values → Planner → Supervisor).
- **Fix D (R39, REQ-PLAN82-4):** probe `timeout_seconds=_PROBE_TIMEOUT_SECONDS`; on timeout it narrates + drafts from repo evidence instead of hanging.
- Tests: test_precheck_probe.py.

Why: the screenshots showed the probe ran but mis-parsed (target "Node 0", "go" falsely present) and felt stuck (16 VM round-trips, one late summary). One clean probe + robust parsing + live narration makes the system-check correct, fast, and trustworthy.

## Plan 83 — Lean intelligent plans + pre-check/target visibility + smart API-key entry

- **Fix 1 (R40, REQ-PLAN83-1):** `_drop_present_prereqs`→`_drop_satisfied_prereqs` now drops "Verify README prerequisite: …" steps (via `_verify_step_referenced_tools` name→tool map) AND "Ensure X installed" steps for any SATISFIED tool; satisfied = present_tools ∪ version-managed runtimes (a version-install step marks its runtime + npm satisfied). Drop runs once after the version blocks. Kills the "Ensure node ≥24 / Ensure git/npm / Verify Node+npm" noise.
- **Fix 2 (R40, REQ-PLAN83-2):** `PlanRecord.precheck_summary`/`target_label`/`required_env_keys` (+ to_dict/from_dict + `finalize_plan_from_steps`); populated via `_format_precheck_summary`/`_target_label`; `render_plan_panel` shows "Target:" + "Pre-check:" lines.
- **Fix 4 (R40, REQ-PLAN83-3):** `_classify_required_env` (alternative AI-provider keys vs individual) + `offer_env_key_setup` — after a successful `/plan approve`, asks which provider (only when alternatives), prompts per key, writes `KEY='value'` into `.env` on the target (update-or-append), value redacted. `_handle_plan_command` threads `text_prompt`/`select_prompt`.
- Tests: test_plan83_lean.py.

Why: the plan re-checked tools the pre-check already found, didn't show findings/target, and left API keys unhandled. Now Duckln drafts a lean per-repo plan, shows what it found + where, and intelligently helps set keys — generalizing to all repos.

## Plan 85 — Idempotent clone, accurate clone diagnosis, continuous progress

- **Fix 0 (R41, REQ-PLAN85-1):** `_plan_precheck_probe` adds a single explicit `test -d {dir}/.git` fallback so a partial capture can't flip `already_cloned` to False (skip stays reliable); `skip_clone=already_cloned` already suppresses the clone step.
- **Fix 1 (R41, REQ-PLAN85-1):** the injected clone step is now self-guarding: `if [ -d {dir}/.git ]; then echo duckln-already-cloned; else git clone --depth 1 {url} {dir}; fi` — never re-clones or 128-fails on an existing checkout.
- **Fix 2 (R41, REQ-PLAN85-2):** `match_deterministic_fix` no longer treats bare `git clone` exit 128 as private; adds `ALREADY_PRESENT` for "already exists" (continue, no block); flags `PRIVATE_REPO` only on real auth/not-found/DNS stderr; the generic permission rule defers SSH "publickey" denials to the private rule.
- **Fix 3 (R41, REQ-PLAN85-3):** progress lines before the network grounding fetch and before the (possibly slow) critic review, so the thinking box always shows the current stage.
- Tests: test_plan85_clone_and_diagnosis.py.

Why: a public, already-cloned repo was re-cloned and then mislabelled "private/unreachable" (bare exit-128 rule), and the planning phase looked frozen during slow network/LLM steps. Now an already-present repo is a no-op, only genuine auth failures block as private, and planning narrates continuously.

## Plan 86 — Dev server = success when serving + open in local browser; crash/spam fixes

- **Fix 1 (R42, REQ-PLAN86-3):** `DUCKLN_WARNING` → `DUCKLN_ORANGE` in `render_plan_panel` (removes the amendment-panel NameError crash).
- **Fix 2 (R42, REQ-PLAN86-1):** `resume_with_approved_plan` run/serve steps go through `_launch_and_await_server` — detached `nohup` launch + bounded readiness poll (`_looks_server_ready`/served URL); serving = success (left running), crash/DUCKLN_DEAD = amendment, unconfirmed = success-with-note. No more timeout-fail of dev servers.
- **Fix 5 (R42, REQ-PLAN86-2):** `_ensure_host_bind` binds the server to 0.0.0.0 per resolved tool (vite `--host`, next `-H`, react-scripts `HOST=`, flask/uvicorn/gunicorn/rails/django) on remote targets; `_host_reachable_url` rewrites to the VM IP; `_open_in_local_browser` opens it on the host; unreachable → port-forward/`ufw`/`--host` remedy.
- **Fix 4 (R42, REQ-PLAN86-1):** a confirming thought ("✓ <app> is now serving at <url> — left running") closes the recovery loop.
- **Fix 3 (R42, REQ-PLAN86-4):** `_capture_terminal_urls` dedupes via a seen-set and skips package-mirror/doc hosts (`_is_infra_url`) — no apt-install link spam.
- Tests: test_plan86_run_server.py, test_link_capture_filter.py.

Why: the app actually started (VITE serving) but Duckln false-failed it as a timeout, crashed on the amendment panel, and spammed mirror links; and a headless VM server's localhost URL wasn't openable. Now a running dev server is a success that opens in the user's browser.

## Plan 87 — Understand the README first; archetype-correct run + cloud GUI streaming; legible agent thinking

- **Fix 0 (R43, REQ-PLAN87-0):** `extract_requirements_spec` builds a grounded, frozen `RequirementsSpec` (archetype, runtimes, services, env keys, migrations) from the whole README + manifests; optional LLM enrichment (`_enrich_requirements_spec_with_llm`) only ADDS items and never overrides file-grounded fields, with a deterministic fallback on model failure. Drafted from in `_generate_plan_for_bringup_impl` (read → understand → probe → plan); persisted via `write_repo_facts` (new `archetype`/`services` keys).
- **Fix 0b (R43, REQ-PLAN87-3):** `_detect_prose_services` + `_db_provision_steps` provision README-prose DBs (Postgres/MySQL/Redis/Mongo) when no compose provides them — install + start + create DB + `DATABASE_URL` + migrations, idempotent; injected before the run step.
- **Fix 1 (R43, REQ-PLAN87-1):** `_detect_app_archetype` (tauri/electron/web/cli/service/library) + archetype-aware `_run_command_from_scripts`/`_desktop_run_command_from_scripts`/`_infer_run_command` — a desktop app runs its native shell, never the frontend `dev`.
- **Fix 1b (R43, REQ-PLAN87-2):** `_desktop_stream_steps`/`_desktop_stream_install_command`/`_launch_desktop_stream`/`_novnc_url` + a desktop branch in `resume_with_approved_plan`: on a remote target a desktop app runs under Xvfb and streams to the local browser via x11vnc+noVNC; on local it opens the native window. Streaming-stack install step injected into the plan for remote desktop apps.
- **Fix 2 (R43, REQ-PLAN87-4):** agent-handoff thoughts (Inspector→Classifier→Planner→Supervisor→Executor) in draft/execute; `_THOUGHTS_MAX` 12→40.
- **Fix 3 (R43, REQ-PLAN87-4):** activity one-liner kept alive while thinking (`add_thought` sets it when empty); the rotating verb dwells `_VERB_DWELL_TICKS` (~2.4s) instead of flickering each 0.1s.
- **Fix 4 (R43, REQ-PLAN87-4):** `_release_due_thoughts` paces a rapid burst (≤1 per `_THOUGHT_MIN_INTERVAL`) into the visible box, drained on a 0.15s interval; no thought lost.
- Tests: test_app_archetype.py, test_desktop_stream.py, test_requirements_spec.py, test_agent_thoughts.py, test_thought_pacing.py.

Why: JustHireMe is a Tauri DESKTOP app — Duckln read the README but never understood the app type, ran only the Vite frontend, and opened a browser URL that errored with `window.__TAURI_INTERNALS__ is undefined`; the thinking was sparse/too-fast and the activity one-liner was empty. Now Duckln understands the README first, runs the right app, streams desktop apps from the cloud, and narrates legibly.

## Plan 88 — Make a Tauri/desktop app actually BUILD & RUN on the VM

- **Fix 1 (R44, REQ-PLAN88-1):** `_desktop_stream_packages("tauri")` now the FULL Tauri build set (`build-essential`/`libssl-dev`/`libwebkit2gtk-4.1-dev`(+4.0 fallback in `_desktop_stream_install_command`)/`librsvg2`/`libayatana-appindicator3`/`patchelf`); new `_tauri_toolchain_steps` installs rustup (when `cargo` absent) + ensures the Tauri CLI; folded into `_desktop_stream_steps` and the draft desktop injection; local desktop path sources cargo too. Electron unchanged (no Rust).
- **Fix 2 (R44, REQ-PLAN88-2):** `_desktop_launch_command` prefixes a Tauri run with `. ~/.cargo/env; DISPLAY=:99 WEBKIT_DISABLE_COMPOSITING_MODE=1 WEBKIT_DISABLE_DMABUF_RENDERER=1`; `_launch_desktop_stream` surfaces only the noVNC URL.
- **Fix 3 (R44, REQ-PLAN88-3):** `_build_precheck_script`/`_parse_precheck_block` emit/parse `DESKTOP:tauri|electron` (stashed in `versions["desktop"]`); `_generate_plan_for_bringup_impl` reclassifies to `desktop_gui` from that probe signal and rewrites the run step to the native shell (`tauri dev`/`electron .`) — even for private repos with no host clone.
- **Fix 4 (R44, REQ-PLAN88-4):** Tauri readiness window 60s→300s + a "building the Rust backend — first compile can take minutes" thought; never surfaces the frontend (1420) URL.
- **Fix 5 (R44):** an install blocked by mode/permissions returns "dead" → the honest amendment flow (no dead URL).
- Tests: test_desktop_stream.py (+toolchain/launch/pkg), test_precheck_probe.py (desktop marker), test_agent_thoughts.py (VM run step = tauri).

Why: even after Plan 87 detected the Tauri app, it ran `tauri dev` without Rust/webkit build deps → the bundled backend never compiled → the frontend still showed `__TAURI_INTERNALS__ undefined`. Now the VM installs the whole toolchain, builds the backend, and streams the real app.

## Plan 89 — Survive OOM, stop honestly, route headless GUIs, skip finished work

- **Fix 1 (R45, REQ-PLAN89-1):** `diagnostics.ErrorCategory.OUT_OF_MEMORY` + a `match_deterministic_fix` rule for real OOM/kill signatures (`signal: 9`/`SIGKILL`/build-context `Killed`/`cc1plus: out of memory`) → remedy adds swap + writes `~/.cargo/config.toml jobs=1`. Deterministic-first, no LLM. Guards against benign "memory" mentions.
- **Fix 2 (R45, REQ-PLAN89-2):** pre-check probe reports `MEMKB`/`SWAP` (no single quotes); `_needs_low_memory_guard`/`_low_memory_swap_command` + a draft injection add swap + single-job build before a Tauri run on a low-RAM (<6 GB) swap-less VM.
- **Fix 3 (R45, REQ-PLAN89-3):** `_desktop_launch_command` now uses a `PATH="$HOME/.cargo/bin:$PATH"` prefix (no `;`/`.`) so the detached `nohup`/`$!` capture is correct (fixes the false "failed while still building" desync); `textual_ui._release_pending_thoughts` auto-clears the generic "Duckln is working" keep-alive after `_THOUGHT_IDLE_CLEAR_SECONDS` idle.
- **Fix 4 (R45, REQ-PLAN89-4):** `_target_is_headless` ($DISPLAY/uname probe) routes a desktop app through Xvfb+noVNC whenever there's no display — covers a multipass VM mislabelled "Local".
- **Fix 5 (R45, REQ-PLAN89-5):** `_idempotent_guard` wraps `npm ci`/`cp .env…`/`apt install <tool>` so a re-run skips finished work; applied over assembled steps in the draft.
- Tests: test_oom_fix.py, test_plan89_helpers.py, test_precheck_probe.py (RAM/swap), test_agent_thoughts.py (low-RAM swap inject + idempotent guards), test_desktop_stream.py (PATH prefix, no `;`).

Why: the Tauri/GTK Rust build OOMed (SIGKILL) on the small VM; Duckln had no OOM rule so the tiny `gemma2:2b` model returned garbage → "no fix", declared failure while the build was still running, leaked the build, and kept showing "Working…". Now OOM is diagnosed + pre-empted deterministically, the lifecycle is honest, headless GUIs stream, and re-runs don't redo finished work.

## Plan 91 (Plan 90 Phase 1) — Repo agent: answer questions about the active repo by reading its code

Foundation for the repo-agent platform (Plan 90 roadmap). Wires the dormant harness tool-use loop into the live product as a repo-scoped, read-only Q&A agent. Audit finding it addresses: `run_agent` had zero call sites; `ai_client` is text-only; the live agent could never read repo code to answer a question.

- **Runtime (R46, REQ-PLAN91-1/2):** new `repo_agent.run_repo_agent` builds an `AgentContext` scoped to the active repo, runs `harness/loop.run_agent` with the read-only tool set, then synthesizes a grounded, file-citing answer from the gathered observations. Provider-agnostic via the JSON decision contract (no native function-calling needed → works with Ollama). New agent spec `harness/agents/repo_qa.md`. LLM via the existing `build_default_llm_client_or_none`.
- **Confinement (R46, REQ-PLAN91-3):** filesystem tools reuse the existing path-traversal guard in `harness/tools.py` (`_resolve_safe_project_path`); `../` escapes are rejected.
- **Wiring (R46, REQ-PLAN91-1/4):** `/ask <question>` in `handle_session_command` resolves the active repo + local workspace, runs the agent, and prints the answer; honest message when no provider / no local clone / remote target.
- **Legible trace (R46, REQ-PLAN91-5):** each `TurnEvent` streams into the thinking box as an Executor→ line.
- Tests: test_repo_agent.py (loop reads files + synthesizes answer; path-traversal blocked; no-LLM honest; spec is read-only).

Why: Duckln could set up a repo but not answer questions about its code. Now `/ask` reads/searches the actual files and answers — the first capability of the full repo agent (next phases: filesystem write/edit + repo actions, subagents, configurable HITL, pluggable memory).

## Plan 92 (Phase 2) — Filesystem write/edit/search + offload
- `harness/tools.py`: `fs.search` (regex grep→file:line), `fs.glob`, `fs.write` (S2), `fs.edit` (S2, unique-or-replace_all); all confined by `_resolve_safe_project_path`. `_maybe_offload` spills >8000-char results to `workspace/results/…` and returns a path+head/tail (applied to `fs.read_file`, `fs.search`, `repo.run`). (R47)

## Plan 93 (Phase 3) — Repo actions + engineer agent
- `harness/tools.py`: `repo.run` (run tests/scripts via `ControlledCommandRunner`), `git.run` (push/force/reset --hard blocked). New `harness/agents/repo_engineer.md` (read→search→edit→verify). `repo_agent.run_repo_agent` gains `task_kind="action"` (engineering-summary synthesis); `/do <task>` wired in `main.handle_session_command`/`_handle_do_command`. (R48)

## Plan 94 (Phase 4) — Subagent delegation with isolated contexts
- `repo_agent.run_repo_task(delegate=True)` orchestrates an Explorer (`repo_qa`, own `run_agent` context) → Engineer (`repo_engineer`, fresh context seeded with the Explorer's findings via `extra_hints`). Each `run_agent` = a separate `AgentState`/context window; the main flow orchestrates + merges. (Parallel asyncio coordinator exists in `harness/coordinator.py` but is intentionally not the default path — sequential is deterministic + lower-risk.) (R49)

## Plan 95 (Phase 5) — Configurable HITL + pluggable memory
- HITL: `ToolRegistry.dispatch` now routes side-effecting tools (`requires_approval`) through `ctx.approve` in HITL/HOTL (HOOTLWO auto), and honors a per-tool policy (`_tool_policy_decision`) that force-allows/denies regardless of mode. Persisted via `access.write_tool_policy`/`read_tool_policy`; `/policy` command. (R50/REQ-PLAN95-1)
- Memory: `state/store.py` adds a `MemoryStore` Protocol + `set_state_store_factory` (SQLite default satisfies it). Per-repo Q&A memory (`access.write_repo_memory`/`read_repo_memory`, redacted+bounded) recalled as hints in `/ask`; user prefs (`write_user_preference`/`read_user_preferences`, `/prefs`). (R50/REQ-PLAN95-2/3)
- Tests: test_repo_phases.py (fs tools, offload, approval gate, policy, git-block, orchestrated edit, persistence, pluggable store); test_repo_agent.py.

Why: completes the Plan 90 roadmap — Duckln can now answer questions AND take actions on a repo (edit/fix/run/git), delegate to isolated subagents, gate any tool with per-tool approval, and persist what it learns per repo across conversations, behind a swappable memory backend.

## Plan 96 (Plans 97–102) — Robust everywhere + polish (production-grade)

- **P97 (R51):** `container` (docker exec) added to `_wrap_command_for_execution_target` + `_active_container_name`; harness `fs.*` handlers route through the canonical wrapper on vm/container/cloud (`_remote_fs`, `_remote_*_command`, `_remote_safe_join`, `_run_on_target`), confined to the in-target repo. `repo_agent`/`/ask`/`/do` resolve the per-target repo dir (`_resolve_repo_agent_target`). **Verified live on multipass `duckln-vm`** (read/search/glob/write/edit landed in the VM; traversal blocked).
- **P98 (R52):** `expose_app_and_open` (local/vm/container/cloud + noVNC) + `_container_published_url` (`docker port`) + `_start_cloud_tunnel_url`/`cloud_runtime.build_cloud_tunnel_command` (ssh -L); ready-hints broadened; local launch now `cd`s into the repo; new `app.serve` tool.
- **P99 (R53):** `harness/native_tools.py` (`to_provider_tool_schemas`, `parse_native_tool_call`, `provider_supports_tool_use`) + optional `tool_decider` hook in `run_agent` (native Anthropic/OpenAI path; JSON fallback otherwise).
- **P100 (R54):** `repo_agent.run_parallel_explorers` — concurrent read-only Explorer subagents, each its own `run_agent` context, merged.
- **P101 (R55):** `access.read_repo_memory_ranked` (keyword-overlap + recency tiebreak, stopword-filtered) used by `/ask`; `state.store.FileMemoryStore` portable backend satisfying `MemoryStore`.
- **P102 (R56):** `repo_agent.classify_repo_agent_intent` + `_maybe_autoroute_repo_agent` auto-route free-form repo questions/actions (gated on active repo + provider; non-repo chat unaffected); `git.run` refuses `commit` on the default branch.
- Tests: test_repo_phases.py, test_repo_phases456.py, test_native_tools.py, test_repo_agent.py, test_harness_tools.py. Real verification: live multipass VM (P97); local app launch (P98); rest unit-verified (cloud/native-FC pending real credentials/keys).

Why: makes the repo agent + run-the-app flow behave correctly on ANY environment (local/VM/container/cloud) with browser reach everywhere, and closes the polish list — production robustness.

## Plan 103 (Plans 104–109) — context engineering + token optimization + long-context + plan-first + live A2A + autonomous skills
- **P104 (R57):** `harness/context_budget.py` (`estimate_tokens`, `context_window_for`, `assemble_observation_block` with rolling-summary compaction) wired into `loop.render_state_for_llm` (recent verbatim + older summarized; LLM summarizer optional).
- **P105 (R58):** model→window map + `usable_context_tokens` + `_dedup_observations` (drop consecutive duplicate probes).
- **P106 (R59):** `map_reduce_summarize` (chunk→map→reduce, query-biased, summarizer-aware) for reasoning over large files/results.
- **P107 (R60):** `_draft_todo` + TODO checklist emitted in `run_repo_task` before acting (LLM or deterministic fallback).
- **P108 (R61):** `run_repo_task` uses `harness/bus.py` `MessageBus` — Explorer publishes `findings`, Engineer publishes `report` (bidirectional transcript).
- **P109 (R62):** `_distill_repo_skill`/`_recall_repo_skill` + `access.write_repo_skill`/`read_repo_skills_ranked` — distill on verified success, recall ranked on next similar task.
- Tests: test_context_budget.py, test_repo_plan103.py.

Why: closes the engineering-depth gaps (context engineering, token use, long context, plan-first surfacing, live agent-to-agent comms, self-improving skills) identified in the Plan 103 scorecard.

## Plan 110 — Real-error attribution + generalized OOM guard + correct target/label + weak-model guardrails (repo-agnostic)
- **Fix 1 (R63/REQ-PLAN110-1):** `_launch_and_await_server`/`_launch_desktop_stream` now return `log_tail`; the executor death branches + `_app_serve_handler` feed the REAL captured log to `_attempt_amendment_and_halt` (exit_code 137). The OOM rule now fires on the actual `signal: 9` error instead of a synthetic placeholder. Verified on the screenshot's exact log → `OUT_OF_MEMORY` swap fix (not "timeout").
- **Fix 2 (R63/REQ-PLAN110-2):** `_is_heavy_build` (compiler/bundler tokens) generalizes the Plan 89 swap injection to ANY heavy build on a low/unknown-RAM remote target.
- **Fix 4 (R63/REQ-PLAN110-3):** `composite_target_label` + `build_connection_context_label`/`_connection_hint` render `local-<vm>` / `<provider>-<name>` / `local-<container>`.
- **Fix 6 (R63/REQ-PLAN110-5):** honest "increase RAM" recommendation when the target is <2.5 GB.
- **Fix 7 (R63/REQ-PLAN110-4):** `_amendment_fix_is_plausible` rejects a timeout-for-crash hallucination; falls through to web/honest no-fix.
- **Fix 8:** amendment retry re-runs via resume-from-start + idempotent guards (Plan 89).
- Tests: test_plan110.py; updated test_textual_ui.py (composite labels).

Why: the screenshot showed an OOM mis-diagnosed as a "timeout" — because the real error never reached the (correct, unit-tested) OOM rule. All fixes are keyed on detected signals, never the repo.

## Plan 111 — Resource sub-agent (RAM/disk/CPU), derived + host-bounded + cost-aware + logged
- New `src/duckln/resource_manager.py`: `ResourceSnapshot`/`ResourceNeed`/`ResourceCrunch`/`ResourceRecommendation`; `probe_target_resources`/`probe_host_resources` (single-quote-free probe), `crunch_from_error` (disk/OOM), `detect_crunch`, `estimate_requirement` (DERIVED — relative to current/footprint/history, not hardcoded), `recommend` (host-capped, cloud cost-noted), `apply_resize` (approve/custom/decline; multipass stop/set/start), `multipass_resize_commands`.
- diagnostics: `ErrorCategory.DISK_FULL`/`RESOURCE_LIMIT`.
- repo_bringup `_handle_resource_crunch`: in `_attempt_amendment_and_halt`, a resource crunch routes to the Resource agent → explain + offer resize → `awaiting_user` PAUSE (does NOT consume the amendment cap, no "irrecoverable"), logged via `access.write_resource_event`.
- `state/access.write_resource_event`/`read_resource_log`; `main` `/resources` command (`_handle_resources_command`) shows live usage + crunch flag + log.
- Tests: test_resource_manager.py (13). Live: `probe_target_resources` on `duckln-vm` returned the real 96%-full disk + host capacity.

Why: the screenshot's real blocker was a 96%-full disk that Duckln saw (`df -h`) but never acted on, then hard-capped. Now any resource crunch is detected, explained with real numbers, and fixed on the user's approval (host-bounded, cost-aware), with a per-VM log.

## Plan 112 — to 99% production-grade (any repo + resource scaling, matrix-gated)
- **P113 (R65):** `_tauri_prebuild_steps` (reads tauri.conf beforeBuild/beforeDev) + `_declared_prebuild_script_steps` (prepare/prebuild/generate/build:*/sidecar) injected via the codegen hook in `_generate_plan_for_bringup_impl` — generic, deduped. Closes the prebuild-artifact/"sidecar" class for ANY repo.
- **P114 (R65):** `resource_manager.resize_commands_for_target` — container via `docker update` (RAM/CPU live), VM via multipass; container disk-in-place honestly unsupported. Fixes the prior multipass-for-container bug.
- **P115 (R65):** `cloud_runtime.build_cloud_resize_commands` (AWS modify-volume/instance-type; GCP disks resize/set-machine-type), cost-careful.
- **P117 (R65):** proactive disk-crunch warning before a heavy build (`resource_manager.probe_target_resources` in the draft).
- **P118 (R65):** `tests/integration/` matrix scaffold + opt-in runner (`DUCKLN_INTEGRATION=1`) + pass-rate gate (≥99%). JustHireMe is one row among many.
- Tests: test_plan112.py (prebuild scripts, tauri.conf parse, container/cloud resize). **Deferred (honest):** P116 native-FC live wiring (needs a real API key); full cloud-apply (needs instance-type catalog + live instance); matrix row-wiring/population (the ongoing work that MEASURES 99%).

Why: get any repo running by honoring its OWN full build instructions, complete resource scaling across targets, and prove it with a real matrix — not tuned to any single repo.

## Plan 119 — Real-agent reliability (R66)
- **P119.1 (R66):** `connection_status.probe_provider_status` uses `validate_api_key` (true connectivity), not `validate_model` (membership) — fixes the false-RED header.
- **P119.2 (R66):** `connection_status.ensure_provider_connected` gates free-text (live REPL), `/ask`, `/do`; connected providers never blocked.
- **P119.3 (R66):** `plan_mode.attribute_failure` → `_attribution_llm_call` (JSON mode + 1024-token budget + strict retry); `parse_plan_json` truncation repair; `ai_client` adapters thread `max_tokens`/`json_mode` (OpenAI/OpenRouter `response_format`, Ollama `format:json`, Anthropic larger budget).
- **P119.4 (R66):** `repo_bringup._secondary_python_setup_steps` (backend venv before prebuild) + `diagnostics.MISSING_VENV` deterministic fix.
- **P119.5 (R66):** `textual_ui._transient_activity_update`/`_is_ollama_pull_noise` collapse all pull lines into one activity bar.
- Tests: test_plan119.py (23). **Honest:** native JSON-mode live behavior is verified against a real key where available; the code-path is unit-tested here.

Why: the live runs showed Duckln planning while shown disconnected, a header that lied, an agent that got stuck because it couldn't parse its own model's fix, a polyglot build-order failure, and progress spam — this plan fixes each at the root.

## Plan 120 — Target-side polyglot ordering (R67)
- **P120 (R67):** `repo_bringup._secondary_python_setup_steps` now returns ONE shell-conditional codegen step (`_SECONDARY_PY_SETUP_CMD`, a `for d in */` loop that venv+installs any Python subdir) executed with `target=execution_target`/`cwd=project_cwd` — fixing the Plan 119 gap where the host-side scan missed VM/container/cloud repos. Gated on Node-primary + a declared prebuild/codegen. Deterministic `MISSING_VENV` recovery preserved as fallback.
- Tests: test_plan119.py::SecondaryEcosystemOrdering updated (asserts the target-side step, no local clone needed).

Why: prevent the polyglot build-order failure up front on every target instead of recovering after a failed step + manual approval.

## Plan 121 — Truthful dot + masked key + proven connection (R68)
- **P121.1 (R68):** `connection_status.probe_provider_status` falls back to `adapter.base_url` for the TCP pre-check (the REAL false-RED cause for OpenAI/OpenRouter/Anthropic) + cache key includes `bool(api_key)`+base_url.
- **P121.2 (R68):** `textual_ui` adds a masked secret overlay (`mode="secret"`, `Input.password=True`, value never echoed) + `prompt_secret`; `main._build_chat_secret_prompt` prefers it.
- **P121.3 (R68):** `config.verify_live_reply` sends a real message via `generate_provider_reply` and shows the model's reply; wired into `run_onboarding` + `update_runtime_provider` (finalize only on a real reply).
- Tests: test_plan121.py + test_plan119 base_url=None; test_onboarding fakes gained a conversation `post`.

Why: the header lied for OpenAI (real cause: empty config base_url short-circuited the reachability check), the key was visible in chat, and "verified" was asserted not proven.

## Plan 122 — Venv-aware missing-module + 429 resilience (R69)
- **P122.1 (R69):** `diagnostics.match_deterministic_fix` rule 4 matches quoted+unquoted "No module named", installs into the venv named in the error (`<venv>/bin/pip install`), verifies with `pip show`.
- **P122.2 (R69):** `ai_client.generate_reply` retries 429/503 with bounded backoff (`_backoff_seconds`/`_retry_after_seconds`, cap 5s, 4 attempts) + clear rate-limit message.
- **P122.3 (R69):** `repo_bringup._SECONDARY_PY_SETUP_CMD` also installs build/dev requirement files (best-effort).
- Tests: test_plan122.py.

Why: a trivially-fixable "No module named PyInstaller" in the backend venv became "no usable fix" because the rule required quotes, targeted system Python, and the LLM fallback hit a 429.

## Plan 123 — Empirical-first real matrix (R70)
- **P123.0:** removed dead repo_bringup.py line; untracked 42 `.pyc`; gitignore bytecode.
- **P123.1:** `tests/integration/driver.run_spec` drives `bring_up_selected_repo` (real clone→setup→run, auto-approve, bounded, temp-cleanup) → RowOutcome(pass/fail + real failure class); `run.py` rewritten to use it; `matrix.py` curated with real repos tagged by env/weight (local=pure-python host-safe).
- **Measured (local host):** flask-hello ✓, fastapi-min ✓ (2/2). node/go/rust/ml/desktop = vm-only, run by user.
- Tests: test_plan123.py (driver pass/fail/exception/running-signal, matrix sanity, cleanup regression).

Why: static audits + green unit tests missed every real bug; only real runs find them. This makes "any repo runs" measurable and turns each real failure into a fix + regression.

## Plan 124 — macOS + AI/ML readiness (R71)
- **P124.1 (R71):** diagnostics rules — `TORCH_WHEEL_MISMATCH` (Mac CPU/MPS wheel), `GPU_UNAVAILABLE` (CUDA-on-Mac honest block), `MODEL_AUTH_REQUIRED` (gated HF → HF_TOKEN), `PACKAGE_MANAGER_MISSING` (brew-missing block); `is_macos` host detection.
- **P124.2 (R71):** `repo_bringup._python_version_install_step` pyenv fallback on macOS.
- **P124.3 (R71):** integration driver passes real `probe_system()`; matrix adds host-safe `streamlit-demo`. Bounded-process archetype DEFERRED (empirically unproven — `python train.py` doesn't match the server patterns).
- **Measured (Mac, host-safe):** flask-hello ✓, fastapi-min ✓, streamlit-demo ✓ (3/3).
- Knowledge: `src/agent/playbooks/ml_python.md`. Tests: test_plan124.py.

Why: get the MacBook toward a measured 99% incl. AI/ML — Macs have no CUDA, so CPU/MPS wheel selection + honest GPU blockers are the core, plus brew/pyenv/HF gaps.

## Plan 125 — Framework-agnostic Mac ML + MLX smoke (R72)
- **P125.1 (R72):** diagnostics `TF_MACOS_REQUIRED` (plain tensorflow → tensorflow-macos/-metal) alongside `TORCH_WHEEL_MISMATCH`; ml_python.md rewritten framework-agnostic (MPS ≠ torch).
- **P125.2 (R72):** integration harness gains a synthetic torch-free `mlx-smoke` (temp venv + `pip install mlx` + MLX GPU op); host-safe, fits 8GB.
- **Measured (this Mac):** mlx-smoke ✓ (real Apple-Silicon GPU compute, torch-free) — plus prior flask/fastapi/streamlit ✓.
- Tests: test_plan125.py.

Why: user correctly noted MPS isn't torch-specific; Duckln runs the repo's declared framework. A tiny MLX op proves Apple-GPU works on an 8GB Mac without torch bloat; heavy ML stays a cloud concern by design.

## Plan 126 — Polyglot-desktop-sidecar as a CLASS (R73)
- **P126.1 (R73):** verified zero repo-name branches in src/ (only 2 illustrative comments); class machinery is signal-keyed (`_detect_app_archetype`, `_tauri_prebuild_steps`, `_secondary_python_setup_steps`, venv-aware missing-module, `_desktop_stream_*`).
- **P126.2 (R73):** signal-keyed/name-independence regression tests (test_plan126); matrix gains 2 public same-class rows (tauri-py-sidecar, electron-quickstart, vm-only) so future runs prove the CLASS with 2 instances.
- **Pending (your VM):** drive both class repos to a running desktop; each blocker → a generic signal-keyed fix + regression. Until both pass, the class stays an explicit known limitation.

Why: user's point — fixing one repo isn't the goal; the stack CLASS must work for any repo of that shape.

## Plan 127 — Resumable re-runs + disk reclamation (R74)
- **P127.1 (R74):** `_idempotent_guard` npm/yarn/pnpm/bun now gate on a completion marker (`node_modules/.package-lock.json`/`.pnpm`/`.yarn-integrity`/`.modules.yaml`) — partial `node_modules` → `rm -rf` + reinstall. `_SECONDARY_PY_SETUP_CMD` uses a `.venv/.duckln-deps-ok` success sentinel (recreate a broken venv).
- **P127.2 (R74):** `resource_manager.reclaim_commands` (npm/yarn/pnpm/pip/cargo-registry/apt caches + /tmp scratch, all `|| true`, never the repo); injected before a heavy build when disk ≥90% full (was warn-only).
- Tests: test_plan127.py.

Why: user saw the VM fill on re-runs. Not a re-clone — but existence-only guards mishandled half-installs and nothing reclaimed caches/build artifacts across repeated heavy runs.

## Plan 129 — Reasoning-based recovery (R75)
- **P129.1 (R75):** new `src/duckln/recovery.py` — `recover_failed_step_with_agent` drives the tool-using loop (`run_repo_agent`) in a recovery role, returns a structured `RecoveryReport{fixed|skip|block}`; wired into `repo_bringup._attempt_amendment_and_halt` BEFORE the blind one-shot (FIXED→fix_step, BLOCK→awaiting_user), degrading gracefully when no harness/LLM.
- **P129.2 (R75):** `classify_blast_radius` + `recovery_autonomy` (repo/system/destructive × target) — the policy that makes auto-recovery safe on Mac and fully autonomous on sandboxes.
- **P129.3 (R75):** `append_thinking_log` (Claude-style, redacted, memory/sessions); `_declared_prebuild_script_steps` emits npm/pnpm `--if-present`.
- Tests: test_plan129.py. **Staged for VM validation (can't run a live model/VM here):** the multi-turn act→verify→iterate loop, true auto-apply-without-approval, and skip-continue executor threading — proven on the matrix.

Why: hand-coding every micro-error can't generalize to any repo. Recovery must reason (investigate→decide→verify→iterate) like a real agent; deterministic rules remain only the cheap fast-path.

## Plan 130 — Act→verify→continue executor (R75 cont.)
- **P130 (R75):** `recovery.apply_fix_and_verify` (apply fix on target → re-run failed step → advance only on a VERIFIED pass); `_attempt_amendment_and_halt` (behind `DUCKLN_AGENT_RECOVERY=1`) now: SKIP → `_RECOVERY_CONTINUE`; FIXED+autonomy=auto → apply+verify → continue; else propose-for-approval. Executor (`resume_with_approved_plan`) continues the plan on the sentinel. Flag OFF ⇒ byte-identical to before (suite green).
- Tests: test_plan129::ActVerify. **VM-staged:** live multi-turn loop + auto-apply across targets.

## Plan 132 — Reasoning by default, production-complete (R76)
- **B (infra):** recovery.py `reasoning_enabled` (default-on/off-under-unittest), `run_bounded_agent` (wall-clock backstop), `ReasoningBudget` (global per-repo cap), reuse context_budget; live `emit_thought` + redacted thinking log.
- **A1:** recovery default-ON (folds Plan 131) via gate+bounds+budget. **A2:** triggered read-only reasoning PLANNING pass (`recovery.reason_about_plan`) wired into `_generate_plan_for_bringup` (complex/polyglot/low-confidence only) — brief enriches the understanding. **A3:** /ask //do already reason (unchanged).
- **C/D:** distill verified reasoned fixes → cross-repo lessons + telemetry; `model_is_reasoning_capable` warning in `run_healthcheck`.
- Tests: test_plan132.py (10) + test_plan129 act-verify. Default-off-under-unittest keeps the suite fast/green.
- **VM-staged (E2):** live multi-turn loop + measured matrix pass-rate (the production proof).

Why: hand-coding every case can't generalize; reasoning (bounded, fast-path-first, learns) is the default cognitive layer like Claude.

## Plan 133 — Live-run fixes (R77)
- **F1:** `text_prompt` threaded resume→amend→crunch→`apply_resize(prompt_value=…)` (was wired to a dead `AppConfig.text_prompt`); user can type a custom GB.
- **F2:** resume — `_load/_mark/_clear_done_steps` (command-keyed, survives amendment renumbering) + skip-completed in `resume_with_approved_plan` (never the run step). **F3:** `reclaim_commands` also clears `.codex-temp-sidecar`/`build_cache` scratch.
- **F4:** `reason_about_plan` now gets the runtime/target dir (live investigation, not None); critic already sees the brief via the understanding's README.
- **F5:** `textual_ui._message_dot_color` recolors the existing dot red(fail/interrupt)/green(done) at render. **F6:** `recovery.append_thinking_log` → maintained `logical-thinking.md` (create+append, redacted) + `surface_thinking_link` clickable on a major conclusion.
- Tests: test_plan133.py (5). **Pending (F7):** live "Tool interrupted" message emission on Ctrl-C/provider-switch + full collapsible step/diff cards (7b).
