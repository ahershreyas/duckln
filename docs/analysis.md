# Duckln Specification Analysis

## Coverage Check

### Requirements → Plan
- R1 covered by Plan 1,2,15
- R2 covered by Plan 3
- R3 covered by Plan 4,14
- R4 covered by Plan 5,14
- R5 covered by Plan 6,12
- R6 covered by Plan 7,13
- R7 covered by Plan 8,13
- R8 covered by Plan 9,16
- R9 covered by Plan 10,11
- R10 covered by Plan 1,12
- R14 covered by Plan 50 (REQ-CLOUD-LOOP-1, REQ-CLOUD-LOOP-2, REQ-CLOUD-LOOP-3, REQ-CLOUD-LOOP-4, REQ-CLOUD-LOOP-5, REQ-CLOUD-LOOP-6, REQ-CLOUD-LOOP-7)
- R15 covered by Plan 51 (REQ-UX-CANCEL-1)
- R16 covered by Plan 51 (REQ-EXPLORE-1, REQ-EXPLORE-2, REQ-EXPLORE-3, REQ-EXPLORE-4)
- R17 covered by Plan 52 (REQ-BRINGUP-LOOP-1, REQ-BRINGUP-LOOP-2, REQ-BRINGUP-README-1, REQ-RUST-1)
- R18 covered by Plan 53 (REQ-SKILL-SELF-1, REQ-SKILL-SELF-2, REQ-SKILL-SELF-3, REQ-SKILL-SELF-4)
- R19 covered by Plan 54 (REQ-BRINGUP-NOLOOP-1, REQ-BRINGUP-NOLOOP-2, REQ-BRINGUP-NOLOOP-3, REQ-BRINGUP-NOLOOP-4, REQ-BRINGUP-NOLOOP-5)
- R20 covered by Plan 55 (REQ-RUNTIME-NOLOOP-1..6) and Plan 56 (REQ-RUNTIME-NOLOOP-7, REQ-RUNTIME-NOLOOP-8)
- R21 covered by Plan 57 (REQ-README-SETUP-1, REQ-README-SETUP-2, REQ-README-RUNTIME-1, REQ-README-USAGE-1, REQ-README-USAGE-2)
- R22 covered by Plan 58 (REQ-PLAN58-1, REQ-PLAN58-2, REQ-PLAN58-3, REQ-PLAN58-4, REQ-PLAN58-5, REQ-PLAN58-6, REQ-PLAN58-7)
- R23 covered by Plan 59 (REQ-PLAN59-1, REQ-PLAN59-2, REQ-PLAN59-3, REQ-PLAN59-4, REQ-PLAN59-5, REQ-PLAN59-6)
- R24 covered by Plan 60 (REQ-PLAN60-1, REQ-PLAN60-2, REQ-PLAN60-3)
- R25 covered by Plan 61 (REQ-PLAN61-A, REQ-PLAN61-B, REQ-PLAN61-C, REQ-PLAN61-D, REQ-PLAN61-E, REQ-PLAN61-F, REQ-PLAN61-G)
- R26 covered by Plan 63 (REQ-PLAN63-1, REQ-PLAN63-2, REQ-PLAN63-3, REQ-PLAN63-4, REQ-PLAN63-5, REQ-PLAN63-6, REQ-PLAN63-7)
- R27 covered by Plan 65 (REQ-PLAN65-1, REQ-PLAN65-2, REQ-PLAN65-3, REQ-PLAN65-4, REQ-PLAN65-5, REQ-PLAN65-6)

### Plan → Tasks
All plan items have at least one linked task.

## Inconsistencies / Watchouts

1. **HOOTLWO wording risk**  
   The product name suggests high autonomy, but v1 must stay whitelist-only.  
   Action: keep marketing honest.

2. **Three provider support adds complexity**  
   This increases testing surface.  
   Action: normalize adapters behind one interface and run identical contract tests.

3. **Launch excitement vs safety tension**  
   A flashy autonomous promise can undermine trust if behavior is conservative.  
   Action: market Duckln as diagnose + verify + teach, not “magic autopilot.”

4. **Banner rendering across terminals**  
   Pixel identity is good, but narrow terminals may distort output.  
   Action: add compact fallback.

5. **Teaching layer can become too verbose**  
   Too much explanation will slow expert users.  
   Action: make explanations mode-aware and bounded.

6. **Runtime configuration changes**
   Slash commands for mode/provider/model/config must preserve a valid working session state and must not overwrite config with invalid selections.

7. **Healthcheck coverage**
   Ensure `/healthcheck` does not expose sensitive information and only reports necessary diagnostics.

8. **Agentic expansion risks**
- VM setup may fail due to OS differences (macOS vs Linux vs Windows)
- Apple Silicon vs CUDA mismatch must be handled explicitly
- GitHub repos may be incomplete or outdated
- Memory must remain high-signal to avoid degradation

## English Tests

- [ ] R1: Can a first-time user complete onboarding without editing files manually?
- [ ] R1: Does setup reject invalid provider keys cleanly?
- [ ] R2: Does branding render acceptably on narrow terminals?
- [ ] R3: Does HITL avoid all AI-triggered execution?
- [ ] R3: Does HOTL require approval every time?
- [ ] R3: Does HOOTLWO only auto-run whitelisted safe commands?
- [ ] R4: Are destructive commands blocked?
- [ ] R4: Are hung commands timed out cleanly?
- [ ] R5: Are secrets redacted before sending payloads?
- [ ] R5: Can the user inspect what context is being sent?
- [ ] R6: Does Duckln correctly distinguish missing package vs wrong environment?
- [ ] R6: Does Duckln identify common CUDA/torch mismatch states?
- [ ] R7: Are suggestions bounded to 1–3 exact commands?
- [ ] R7: Are commands labeled with purpose?
- [ ] R8: Does Duckln verify whether a suggested fix worked?
- [ ] R8: If verification fails, does Duckln adapt instead of repeating itself?
- [ ] R9: Are explanations short, helpful, and not generic?
- [ ] R10: Can a production incident be traced without exposing private data?

## Release Gate Recommendation

A feature or release is not ready unless:
- all linked tasks are complete
- all English tests are answered positively
- no unresolved privacy gap remains
- no safety class escalation is undocumented

9. **Repo catalog strategy risks**
- Static cache may become stale without user refresh
- GitHub API rate limits during refresh
- Repo metadata inconsistencies across topics

10. **Agent contract risk**
- If `AGENTS.md` is not treated as authoritative, implementation may drift from the intended terminal-agent behavior.

11. **Memory quality risk**
- The main memory risk is not storage exhaustion but degraded usefulness from noisy or oversized memory files.

12. **Cross-hardware behavior risk**
- Apple Silicon, CPU-only systems, and NVIDIA/CUDA systems must be handled explicitly to avoid invalid setup guidance.

13. **VM configuration UX risk**
- Incorrect CPU/memory defaults may lead to poor performance or VM failure
- User input validation required for VM naming

14. **Virtual memory consistency risk**
- SQLite-backed memory and materialized filesystem views must stay synchronized to avoid stale or conflicting agent state.

15. **Memory authority risk**
- Duckln must define clearly whether SQLite or local files are authoritative; mixed authority would create drift and unreliable behavior.

16. **Plan Mode reasoning risk (Plan 67)**
- A single LLM call producing a plan can hallucinate steps that look correct but don't apply to the actual repo. Mitigation: the pipeline is broken into FIVE stages, with Stage 1 deterministic (no LLM) gathering ground-truth facts from `package.json`, `pyproject.toml`, README, prior failure memory; Stage 3's critic explicitly reasons about each candidate before commit; Stage 4 is deterministic safety classification (no LLM) refusing S4 commands; Stage 5 is a hard-capped clarification gate (≤3 questions).
- Strict adherence to the approved plan prevents the executor from improvising recovery commands the user never saw. A failed step triggers visible attribution (`{cause, fix}`) + amendment insertion before the failed step; amendments are capped at 3 to bound runaway loops.
- The supervisor agent is wired via `run_plan_supervisor` so the multi-agent pipeline is observable in `/agents trace <session>`, not opaque.

## Plan 67 Traceability

- R29 (Plan Mode) ↔ Plan 67 ↔ Phase 29 ↔ `src/duckln/plan_mode.py`, `src/duckln/harness/plan_supervisor.py`, `src/duckln/harness/agents/{supervisor,repo_inspector,planner,critic}.md`, `src/duckln/main.py::_handle_plan_command`, `src/duckln/repo_bringup.py::_generate_plan_for_bringup`, `_attempt_amendment_and_halt`, `resume_with_approved_plan`, `src/duckln/conversation_agent.py::_plan_mode_nudge_for_message`, `src/duckln/textual_ui.py::_plan_mode_header_segment`, `src/state/access.py::{write,read,clear}_pending_plan`, `{append,list,clear}_plan_history`.
- REQ-PLAN67-1 (toggle persistence) ↔ `AppConfig.plan_mode_enabled`, `CONFIG_STATE_KEYS`, `_state_snapshot_from_payload` ↔ `tests/test_plan_mode_command.py`, `tests/test_state_store.py`, `tests/test_onboarding.py`.
- REQ-PLAN67-2 (pipeline) ↔ `gather_repo_understanding`, `_propose_candidates`, `_critique_and_order`, `_classify_and_verify`, `_collect_clarifications`, `generate_plan` ↔ `tests/test_plan_mode_pipeline.py`, `tests/test_plan_mode_understanding.py`.
- REQ-PLAN67-3 (targeted clarification, cap=3) ↔ `_collect_clarifications`, `MAX_CLARIFICATION_QUESTIONS=3`, `LOW_CONFIDENCE_THRESHOLD=0.6` ↔ `tests/test_plan_mode_pipeline.py::ClarificationTest`.
- REQ-PLAN67-4 (strict adherence) ↔ `resume_with_approved_plan`, `_step_allowed_in_mode` ↔ `tests/test_plan_mode_integration.py`.
- REQ-PLAN67-5 (attribution + cap=3 amendments) ↔ `attribute_failure`, `amend_plan`, `MAX_AMENDMENTS=3`, `_attempt_amendment_and_halt`, `render_error_attribution` ↔ `tests/test_plan_mode_attribution.py`.
- REQ-PLAN67-6 (supervisor wiring) ↔ `run_plan_supervisor`, `REQUIRED_AGENT_NAMES`, `_local_plan_pipeline` ↔ `tests/test_plan_mode_supervisor.py`.
- REQ-PLAN67-7 (UI surface) ↔ `glyphs.PLAN_NOTE`/`PLAN_APPROVED`/`PLAN_REJECTED`/`PLAN_PENDING`/`PLAN_EDIT`, `ui.render_plan_panel`/`render_plan_oneline`/`render_clarification_prompt`/`render_error_attribution`, `textual_ui._plan_mode_header_segment` ↔ `tests/test_plan_mode_render.py`.
- REQ-PLAN67-8 (slash commands) ↔ `get_slash_command_descriptors` Plan Mode entries, `_handle_plan_command`, `handle_session_command` dispatch ↔ `tests/test_plan_mode_command.py`, `tests/test_main.py::test_command_palette_lists_available_commands`.
- REQ-PLAN67-9 (privacy + history cap) ↔ `write_pending_plan`/`read_pending_plan`/`clear_pending_plan`, `append_plan_history`/`list_plan_history`, `PLAN_MODE_HISTORY_LIMIT=50`, `plan_markdown_path`, `redact_sensitive_data` (reused) ↔ `tests/test_plan_mode_state.py`.

## Plan 68 Traceability (Plan Mode bugfixes)

- REQ-PLAN68-1 (initial-setup routing) ↔ `main._orchestrate_repo_to_running` plan-mode short-circuit; `plan_mode_enabled` passed at the `/repos` VM-warning branch and `/explore` setup; `_handle_plan_command` `/plan approve` ↔ `tests/test_plan_mode_targets.py::OrchestrateRoutingTest`.
- REQ-PLAN68-2 (UI toggle) ↔ `textual_ui._compose_plan_mode_label`, `+` dropdown option, `_handle_attach_selection` `plan_mode` branch, `_toggle_plan_mode` ↔ `tests/test_plan_mode_targets.py::ToggleConfigContractTest` (config-flip data path; UI method exercised manually).
- REQ-PLAN68-3 (target-agnostic) ↔ `plan_mode.gather_repo_understanding(override_detected_files=…)`, `repo_bringup._generate_plan_for_bringup` (remote `inspect_remote_repo_setup_files`), `resume_with_approved_plan` (live `execution_target` + `_wrap_command_for_execution_target` per step) ↔ `tests/test_plan_mode_targets.py::TargetWrappingTest`.

Note: Plan 67 wired the `plan_mode_enabled` flag and the plan-generation branch but no caller passed the flag, so `/repos` ran legacy setup despite Plan Mode being on; Plan 68 closes that gap, adds the `+` toggle, and makes generation + execution work across local/container/VM/AWS/GCP.

## Plan 69 Traceability (Plan Mode hardening)

- REQ-PLAN69-1 (generation timeout) ↔ `ai_client.CONVERSATION_TIMEOUT_SECONDS`, `OLLAMA_CONVERSATION_TIMEOUT_SECONDS`, `ProviderAdapter.conversation_timeout_seconds`, `generate_reply` retry loop ↔ `tests/test_ai_client.py::ConversationTimeoutPlan69Test`.
- REQ-PLAN69-2 (no pre-approval execution) ↔ `repo_bringup._generate_plan_for_bringup` (removed remote `inspect_remote_repo_setup_files`), `plan_mode.gather_repo_understanding(family_hint=…)`, `plan_mode.family_hint_from_metadata` ↔ `tests/test_plan_mode_targets.py::NoPreApprovalExecutionTest`.
- REQ-PLAN69-3 (editable without TTY) ↔ `main._open_path_non_blocking`, `main._reload_pending_plan_from_disk`, `/plan edit` non-TTY branch, `/plan reload` descriptor ↔ `tests/test_plan_mode_command.py::test_edit_in_textual_ui_does_not_block`/`test_reload_reparses_edited_markdown`.
- REQ-PLAN69-4 (branded activity + numbering) ↔ `main._activity_message_for_slash_command` `/plan*` branches, `plan_mode._renumber_steps`/`_classify_and_verify` ↔ `tests/test_plan_mode_targets.py::BrandedActivityLabelTest`, `tests/test_plan_mode_pipeline.py::test_steps_numbered_sequentially_even_with_messy_indices`.

Root causes fixed in Plan 69: (1) the 2 s Ollama timeout was applied to generation, not just the tags GET, so every plan failed at the proposal stage even though `/healthcheck` passed; (2) `/plan edit` spawned a TTY editor inside the Textual UI and hung; (3) Plan 68's remote file-listing ran `cd … && ls -1A` in the VM during generation — before approval. All three are now closed; plan generation touches nothing on the target until `/plan approve`.

- REQ-PLAN69-5 (header not stuck on failed) ↔ `textual_ui._plan_mode_header_segment` (terminal statuses → green "Plan: on"), `repo_bringup._generate_plan_for_bringup` (clears pending on failure) ↔ `tests/test_plan_mode_targets.py::HeaderStatusPlan69Test`.
- REQ-PLAN69-6 (consistent target label) ↔ `main._orchestrate`-startup `initial_connection_type` now from `_terminal_connection_context`; matches `_footer_execution_label` ↔ `tests/test_plan_mode_targets.py::HeaderStatusPlan69Test::test_stale_vm_runtime_resolves_to_local_label`.

Root causes fixed in the Plan 69 follow-up: (4) a failed plan was written as "pending" and never cleared, so the header stuck on red "Plan: failed" across sessions and blocked retries; (5) the header read the raw stale `active_runtime_execution_target=vm` while the footer used `_terminal_connection_context` (which maps vm→local), so the two disagreed and the header wrongly showed "Ubuntu VM" on a fresh local session.

- REQ-PLAN70-1 (no stuck failed plan) ↔ `repo_bringup._generate_plan_for_bringup` guard (actionable-only) + stale-clear ↔ `tests/test_plan_mode_targets.py::StalePlanGuardPlan70Test`.
- REQ-PLAN70-2 (reliable single-call generation) ↔ `plan_mode._steps_from_candidates`, `_assemble_ordered_steps`, `generate_plan` (critique best-effort), `plan_supervisor.run_plan_supervisor` ↔ `tests/test_plan_mode_pipeline.py::test_critique_failure_falls_back_to_candidates`/`test_propose_failure_still_yields_failed`.

Root causes fixed in Plan 70: (6) the `/repos` guard treated a same-repo *failed* plan as blocking, so one failed generation pinned the user to it permanently — `/repos` always replied "a plan is already pending" and never regenerated; (7) the pipeline required two mandatory LLM calls (propose + critique), so a small local model that failed the critique JSON discarded the good proposed steps and marked the whole plan failed. The guard now blocks only actionable plans, and critique is best-effort so one successful propose call reliably yields a numbered, reviewable plan.
## Plan 72 Traceability (Full Proper Plan Mode PRD)

- REQ-PLAN72-1 ↔ `repo_bringup._grounded_setup_plan`/`_assemble_plansteps_with_clone_and_run`/`_infer_run_command`, `plan_mode.finalize_plan_from_steps`, `ui.render_plan_panel`/`_plan_field_lines`/`_draw_plan_panel` ↔ tests/test_plan_mode_grounding.py.
- REQ-PLAN72-2 ↔ `plan_lifecycle.PlanLifecycle`/`TERMINAL_STATES` ↔ tests/test_plan_lifecycle.py, tests/test_plan_mode_activity_state.py.
- REQ-PLAN72-3 ↔ `plan_mode.critic_review`/`CriticVerdict`, `prompts.SYSTEM_PROMPT_PLAN_VERDICT`, `agents/critic.md` ↔ tests/test_plan_mode_critic.py.
- REQ-PLAN72-4 ↔ `config._parse_optional_bool_default_true` + onboarding default, `plan_mode.plan_first_required`/`require_plan_for_mutation` ↔ tests/test_plan_first_enforcement.py.
- REQ-PLAN72-5 ↔ `plan_mode._redacted` on all LLM `user_message` + learned skills ↔ tests/test_plan_mode_redaction.py.
- REQ-PLAN72-6 ↔ `repo_bringup._step_allowed_in_mode` ↔ tests/test_plan_mode_hootlwo.py.
- REQ-PLAN72-7 ↔ `plan_mode.record_plan_skill`/`plan_skill_signature` (called on verified success in `resume_with_approved_plan`) ↔ tests/test_plan_mode_learning.py.
- REQ-PLAN72-8 ↔ `plan_mode.filter_repeat_clarifications`/`clarification_signature` ↔ tests/test_plan_mode_clarification.py.

Known partials: VM/cloud/Docker interactive handlers + runtime-repair lifecycle rerouting and the Textual hard spinner ceiling are follow-ups; the plan-first chokepoint is in place and repo bring-up is fully plan-gated.

## Plan 73 Traceability (partials + self-learning loop)

- REQ-PLAN73-1 ↔ `repo_bringup.gate_mutation_or_draft`; `/vm` + `_handle_cloud_create_command` guards + `_run_runtime_repair_workflow` entry guard (main.py) ↔ tests/test_plan73_partials.py.
- REQ-PLAN73-2 ↔ `textual_ui._activity_bar_segments` + `_ACTIVITY_HARD_CEILING_SECONDS` ↔ tests/test_plan73_partials.py::SpinnerCeilingTest.
- REQ-PLAN73-3 ↔ `main._render_skills_summary` (learned grouping), `_render_skill_detail`, `_clear_learned_skills`, `_render_learning_status` ↔ smoke + palette test.
- REQ-PLAN73-4 (Inject) ↔ `repo_bringup._generate_plan_for_bringup` skill-backbone injection + `plan_mode.find_matching_skill`/`load_skill_commands` ↔ tests/test_learning_loop.py::InjectTest.
- REQ-PLAN73-5 (Reflect) ↔ `plan_mode.reflect_on_run`/`ReflectionNote`/`persist_reflection` ↔ tests/test_learning_loop.py::ReflectTest.
- REQ-PLAN73-6 (Extract) ↔ `plan_mode.record_failure_skill` (irrecoverable) + `record_plan_skill` (success) in `resume_with_approved_plan` ↔ tests/test_learning_loop.py::ExtractTest.
- REQ-PLAN73-7 (Loop) ↔ `/plan retry` (main `_handle_plan_command`) ↔ tests/test_learning_loop.py::LoopRetryTest.

Self-learning loop now closed: a verified run extracts a family·os·target skill → the next plan draft for the same signature injects that skill's command backbone; a failed run records a redacted reflection + `…-avoid` skill so the retry differs. Known partial: deep cloud auth-wizard rerouting beyond create-entry gating.

### R32 (Plan 76) traceability

- REQ-PLAN76-1 (Preparing-plan + rotating words) ↔ `repo_bringup._generate_plan_for_bringup` staged `display(...)` + `textual_ui._PROCESSING_VERBS`/`_processing_verb`/`_activity_bar_segments(spinner_index=…)` ↔ tests/test_processing_words.py.
- REQ-PLAN76-2 (Supervisor never fails) ↔ `plan_mode._deterministic_supervisor_review` + reworked `plan_mode.critic_review` (LLM verdict when clean, deterministic fallback, never `skipped`) ↔ tests/test_supervisor_review.py + updated tests/test_plan_mode_critic.py.
- REQ-PLAN76-3 (Opt-in pre-check) ↔ `config.plan_precheck` (+ `_parse_plan_precheck`, CONFIG_STATE_KEYS), `repo_bringup._plan_precheck_probe`/`_drop_present_prereqs`/`_assemble_plansteps_with_clone_and_run(skip_clone=…)`, `main._handle_plan_command` `/plan precheck` ↔ tests/test_plan_precheck.py.
- REQ-PLAN76-4 (Quote-safe prereq) ↔ `repo_bringup._node_npm_prerequisite_check`/`_node_package_manager_prerequisite_check` ↔ tests/test_node_prereq_check.py.
- REQ-PLAN76-5 (Simplified panel) ↔ `ui.render_plan_panel` (no S0/S1/S2 chips; ETA + step count + approval note).
- REQ-PLAN76-6 (Thinking box) ↔ `textual_ui` `#thoughts-panel` + `add_thought`/`clear_thoughts`/`toggle_thoughts` + `main._emit_thought`/`_chat_supports_thoughts` (redacted reasoning routing, dim-line CLI fallback), reasoning emitted from `_generate_plan_for_bringup` via threaded `emit_thought` ↔ tests/test_thoughts_box.py.

### R33 (Plan 77) traceability

- REQ-PLAN77-1 (Version-aware planning) ↔ `repo_bringup._detect_required_node_version`/`_fetch_github_raw_file`/`_node_major_from_spec`/`_int_or_zero`, `_plan_precheck_probe` (`present_versions`), `_node_version_install_step`, and the compare+inject block in `_generate_plan_for_bringup` ↔ tests/test_runtime_version_detect.py.
- REQ-PLAN77-2 (Plan approval authorizes steps) ↔ `repo_bringup._step_allowed_in_mode(plan_approved, step_origin)` + `resume_with_approved_plan` call site ↔ tests/test_plan_step_authorization.py.
- REQ-PLAN77-3 (Live thinking) ↔ `_think(...)` calls emitted before `_grounded_setup_plan`, version detection, and `critic_review` in `_generate_plan_for_bringup` (manual UI verification).
- REQ-PLAN77-4 (Web self-repair) ↔ `repo_bringup._web_evidence_for_failed_step` + `plan_mode.attribute_failure(web_evidence=…)` second pass in `_attempt_amendment_and_halt`, gated by `internet_skill.is_internet_enabled`, reusing `web_runtime.search_runtime_issue` ↔ tests/test_web_self_repair.py.

### R34 (Plan 78) traceability

- REQ-PLAN78-1 (Honest connection) ↔ `plan_mode._model_connection_error`/`MODEL_UNREACHABLE_*` + `critic_review` block signal + `repo_bringup._generate_plan_for_bringup` surface ↔ tests/test_model_connection.py.
- REQ-PLAN78-2 (Evidence fields) ↔ `plan_mode.PlanStep` (target/source/evidence_excerpt/cwd) + `repo_bringup._assemble_plansteps_with_clone_and_run` + `ui.render_plan_panel` "Where:" ↔ tests/test_planstep_evidence.py.
- REQ-PLAN78-3 (Deeper checks) ↔ `plan_mode._deterministic_supervisor_review` (verify/target/wrong-OS/duplicate) + `prompts.SYSTEM_PROMPT_PLAN_VERDICT` ↔ tests/test_supervisor_review.py.
- REQ-PLAN78-4 (Replan loop) ↔ `repo_bringup._repair_plan_for_revise` + one re-review in `_generate_plan_for_bringup` ↔ tests/test_replan_loop.py.
- REQ-PLAN78-5 (Re-reviewed recovery) ↔ `plan_mode.attribute_failure` (target/cwd/verify) + `repo_bringup._attempt_amendment_and_halt` `critic_review` of the amendment ↔ tests/test_amendment_review.py.
- REQ-PLAN78-6 (Internal-bug surfacing) ↔ `repo_bringup._generate_plan_for_bringup` guard → `duckln_internal_bug_reported` + `main._orchestrate_repo_to_running` plan-mode `finally` clears the spinner ↔ tests/test_plan_lifecycle.py.
- REQ-PLAN78-7 (Redaction additions) ↔ `diagnostics.redact_sensitive_data` Authorization + OAuth patterns ↔ tests/test_diagnostics.py.

### R35 (Plan 79) traceability

- REQ-PLAN79-1 (No garbage response) ↔ `plan_mode.critic_review` retry-then-discard loop with the deterministic review authoritative ↔ tests/test_model_connection.py.
- REQ-PLAN79-2 (Resume on retry) ↔ `main._looks_like_runtime_repair_request` resume phrases + `main._handle_pending_repo_followup` plan-mode-aware `set_up_repo` resume + `/repos` `pending_next_action` ↔ tests/test_resume_operation.py.

### R36 (Plan 79b) traceability

- REQ-PLAN79-3 (PM correctness) ↔ `plan_mode._deterministic_supervisor_review` lockfile check + `_is_run` PM-aware ↔ tests/test_smart_planning.py::TestLockfileMismatch.
- REQ-PLAN79-4 (Logical run cmd) ↔ `repo_bringup._infer_run_command`/`_read_repo_package_json`/`_run_command_from_scripts`/`_package_manager_for`/`_RUN_SCRIPT_PREFERENCE` ↔ tests/test_smart_planning.py::TestRunCommandRanking.
- REQ-PLAN79-5 (Env file) ↔ `repo_bringup._env_example_file` + assembler copy-env step ↔ tests/test_smart_planning.py::TestEnvAndEvidence.
- REQ-PLAN79-6 (README evidence) ↔ `repo_bringup._evidence_line_for` + `ui.render_plan_panel` "Source:" ↔ tests/test_smart_planning.py.
- REQ-PLAN79-7 (Verification) ↔ `repo_bringup._verification_for_run_command` ↔ tests/test_smart_planning.py::TestVerificationAndUrl.
- REQ-PLAN79-8 (Surface URL) ↔ `repo_bringup._extract_served_url`/`_vm_ipv4` + `resume_with_approved_plan` ↔ tests/test_smart_planning.py.
- REQ-PLAN79-9 (Per-repo learning) ↔ `state.access.write_repo_facts`/`read_repo_facts` + `repo_bringup.resume_with_approved_plan` record + `_generate_plan_for_bringup` apply ↔ tests/test_smart_planning.py::TestPerRepoFacts.

### R37 (Plan 80) traceability

- REQ-PLAN80-1 ↔ `repo_bringup._framework_node_requirement` + `_detect_required_node_version` ↔ tests/test_plan80_robustness.py::TestFrameworkNodeRequirement.
- REQ-PLAN80-2 ↔ `repo_bringup._detect_required_python/go/rust_version`/`_tool_versions_map`/`_xy_floor`/`_xy_below` + `_python/_go/_rust_version_install_step` + `SETUP_FILE_ORDER` + probe/injection ↔ tests/test_plan80_robustness.py::TestGenericRequirements.
- REQ-PLAN80-3 ↔ `diagnostics.match_deterministic_fix`/`DeterministicFix` + `_attempt_amendment_and_halt` ↔ tests/test_plan80_robustness.py::TestDeterministicFixes.
- REQ-PLAN80-4 ↔ `state.access.write_common_lesson`/`read_common_lessons` + `repo_bringup._common_lesson_fix`/`_record_common_lesson_from_fix` ↔ tests/test_plan80_robustness.py::TestCommonLessons.
- REQ-PLAN80-5 ↔ `web_runtime.gather_repair_fix` + `repo_bringup._web_evidence_for_failed_step` ↔ tests/test_plan80_robustness.py::TestWebRepairLive.
- REQ-PLAN80-6 ↔ `repo_bringup.resume_with_approved_plan` (checkbox/celebration/emit_thought) + `main._handle_plan_command` ↔ tests/test_plan_execution_ux.py.
- REQ-PLAN80-7 ↔ `config.duckln_ui`/`_parse_duckln_ui` + `ui.build_chat_interface(ui_mode)` + `main` `/ui` ↔ tests/test_ui_mode.py.
- REQ-PLAN80-8 ↔ `ui.format_thought_for_seconds`/`render_main_task_header`/`render_step_line(indent)` + `main` free-text Q&A timing + `textual_ui` objective header ↔ tests/test_ui_mode.py.

### R38 (Plan 81) traceability

- REQ-PLAN81-1 ↔ `repo_bringup._parse_env_example_keys`/`_required_env_vars` + env-copy step ↔ tests/test_plan81_any_repo.py::TestEnvRequirements.
- REQ-PLAN81-2 ↔ `repo_bringup._detect_project_subdir` + `_assemble_plansteps_with_clone_and_run(project_subdir)` + `SETUP_FILE_ORDER` ↔ tests/test_plan81_any_repo.py::TestMonorepo.
- REQ-PLAN81-3 ↔ `repo_bringup._compose_services` + assembler `docker compose up -d` / `docker_available` ↔ tests/test_plan81_any_repo.py::TestComposeServices.
- REQ-PLAN81-4 ↔ `repo_bringup._codegen_steps` + assembler codegen insertion ↔ tests/test_plan81_any_repo.py::TestCodegen.
- REQ-PLAN81-5 ↔ `diagnostics.match_deterministic_fix` (prisma/native/playwright/service) ↔ tests/test_plan81_any_repo.py::TestDeterministicFixesPlan81.
- REQ-PLAN81-6 ↔ `repo_bringup._vm_port_reachable`/`_port_from_url` + `resume_with_approved_plan` ↔ tests/test_plan81_any_repo.py::TestVmPort.
- REQ-PLAN81-7 ↔ `diagnostics.match_deterministic_fix` PRIVATE_REPO block + `repo_bringup._attempt_amendment_and_halt` ↔ tests/test_plan81_any_repo.py::TestDeterministicFixesPlan81.
- REQ-PLAN81-8 ↔ `ui.build_chat_interface(ui_mode)`/`render_plan_panel` ↔ tests/test_ui_smoke.py.

### R39 (Plan 82) traceability

- REQ-PLAN82-1 ↔ `repo_bringup._plan_precheck_probe` (single `_build_precheck_script` run) ↔ tests/test_precheck_probe.py::TestProbeSingleRoundTrip.
- REQ-PLAN82-2 ↔ `repo_bringup._parse_precheck_block`/`_runtime_major` + hardened version-compare ↔ tests/test_precheck_probe.py::TestParseBlock.
- REQ-PLAN82-3 ↔ `repo_bringup._plan_precheck_probe(emit_thought=…)` + role-labelled `_think` lines in `_generate_plan_for_bringup_impl` ↔ tests/test_precheck_probe.py::TestProbeSingleRoundTrip.
- REQ-PLAN82-4 ↔ `repo_bringup._PROBE_TIMEOUT_SECONDS` + timeout narration in `_plan_precheck_probe`.

### R40 (Plan 83) traceability

- REQ-PLAN83-1 ↔ `repo_bringup._drop_satisfied_prereqs`/`_verify_step_referenced_tools` + satisfied-set drop in `_generate_plan_for_bringup_impl` ↔ tests/test_plan83_lean.py::TestLeanPlan.
- REQ-PLAN83-2 ↔ `plan_mode.PlanRecord` (precheck_summary/target_label/required_env_keys) + `repo_bringup._format_precheck_summary`/`_target_label` + `ui.render_plan_panel` ↔ tests/test_plan83_lean.py::TestPanelFields.
- REQ-PLAN83-3 ↔ `repo_bringup._classify_required_env`/`offer_env_key_setup` + `main._handle_plan_command` post-setup call ↔ tests/test_plan83_lean.py::TestEnvKeyClassify/TestOfferEnvKeySetup.

### R41 (Plan 85) traceability

- REQ-PLAN85-1 ↔ `repo_bringup._plan_precheck_probe` fallback clone check + self-guarding clone step in `_assemble_plansteps_with_clone_and_run` ↔ tests/test_plan85_clone_and_diagnosis.py::TestCloneSkipAndIdempotency/TestPrecheckClonedFallback.
- REQ-PLAN85-2 ↔ `diagnostics.match_deterministic_fix` (ALREADY_PRESENT + stderr-driven PRIVATE_REPO, publickey defer) ↔ tests/test_plan85_clone_and_diagnosis.py::TestDiagnosisNotPrivate.
- REQ-PLAN85-3 ↔ progress `display`/`_think` lines before grounding + critic in `_generate_plan_for_bringup_impl`.

### R42 (Plan 86) traceability

- REQ-PLAN86-1 ↔ `repo_bringup._launch_and_await_server`/`_looks_server_ready` + run-step branch in `resume_with_approved_plan` ↔ tests/test_plan86_run_server.py::TestRunServerSuccess.
- REQ-PLAN86-2 ↔ `repo_bringup._ensure_host_bind`/`_host_reachable_url`/`_open_in_local_browser` ↔ tests/test_plan86_run_server.py::TestHelpers/TestRunServerSuccess.
- REQ-PLAN86-3 ↔ `ui.render_plan_panel` (`DUCKLN_ORANGE`) ↔ tests/test_plan86_run_server.py::TestAmendmentPanelRenders.
- REQ-PLAN86-4 ↔ `textual_ui._capture_terminal_urls`/`_is_infra_url` ↔ tests/test_link_capture_filter.py.

### R43 (Plan 87) traceability

- REQ-PLAN87-0 ↔ `repo_bringup.RequirementsSpec`/`extract_requirements_spec`/`_enrich_requirements_spec_with_llm` + read→understand→probe→plan order in `_generate_plan_for_bringup_impl` + `state.access.write_repo_facts` (archetype/services) ↔ tests/test_requirements_spec.py, tests/test_agent_thoughts.py.
- REQ-PLAN87-1 ↔ `repo_bringup._detect_app_archetype`/`_run_command_from_scripts`/`_desktop_run_command_from_scripts`/`_infer_run_command` ↔ tests/test_app_archetype.py, tests/test_agent_thoughts.py::test_desktop_run_command_chosen_not_frontend.
- REQ-PLAN87-2 ↔ `repo_bringup._desktop_stream_steps`/`_desktop_stream_install_command`/`_launch_desktop_stream`/`_novnc_url`/`_looks_like_desktop_run_command` + desktop branch in `resume_with_approved_plan` + draft-time stream-install injection ↔ tests/test_desktop_stream.py, tests/test_agent_thoughts.py::test_vm_target_injects_streaming_stack.
- REQ-PLAN87-3 ↔ `repo_bringup._detect_prose_services`/`_db_provision_steps`/`_canonical_engine` + draft injection ↔ tests/test_requirements_spec.py.
- REQ-PLAN87-4 ↔ `textual_ui._release_due_thoughts`/`add_thought`/`_release_pending_thoughts` (pacing), `_VERB_DWELL_TICKS`/`_update_activity_bar` (slow verb + keep-alive), `_THOUGHTS_MAX`=40, agent-handoff `_think` lines in `_generate_plan_for_bringup_impl`/`resume_with_approved_plan` ↔ tests/test_thought_pacing.py, tests/test_agent_thoughts.py.

### R44 (Plan 88) traceability

- REQ-PLAN88-1 ↔ `repo_bringup._desktop_stream_packages`/`_desktop_stream_install_command` (full Tauri set + 4.0 fallback) + `_tauri_toolchain_steps` (rustup + Tauri CLI) folded into `_desktop_stream_steps` + draft injection ↔ tests/test_desktop_stream.py::TestDesktopStreamPackages/TestTauriToolchain.
- REQ-PLAN88-2 ↔ `repo_bringup._desktop_launch_command` (WEBKIT_DISABLE_* + cargo env) + `_launch_desktop_stream` (noVNC-only) ↔ tests/test_desktop_stream.py::TestDesktopLaunchCommand.
- REQ-PLAN88-3 ↔ `repo_bringup._build_precheck_script`/`_parse_precheck_block` (DESKTOP marker) + `_generate_plan_for_bringup_impl` reclassify + run-step rewrite ↔ tests/test_precheck_probe.py, tests/test_agent_thoughts.py::test_vm_run_step_is_tauri_not_frontend.
- REQ-PLAN88-4 ↔ `repo_bringup._launch_desktop_stream` (300s Tauri window + compiling note) ↔ tests/test_desktop_stream.py.

### R45 (Plan 89) traceability

- REQ-PLAN89-1 ↔ `diagnostics.ErrorCategory.OUT_OF_MEMORY` + `match_deterministic_fix` OOM rule ↔ tests/test_oom_fix.py.
- REQ-PLAN89-2 ↔ `repo_bringup._build_precheck_script`/`_parse_precheck_block` (MEMKB/SWAP) + `_needs_low_memory_guard`/`_low_memory_swap_command` + draft injection ↔ tests/test_precheck_probe.py, tests/test_plan89_helpers.py, tests/test_agent_thoughts.py::test_low_ram_vm_injects_swap_step.
- REQ-PLAN89-3 ↔ `repo_bringup._desktop_launch_command` (PATH prefix, no `;`) + `textual_ui._release_pending_thoughts`/`_THOUGHT_IDLE_CLEAR_SECONDS` ↔ tests/test_desktop_stream.py::TestDesktopLaunchCommand.
- REQ-PLAN89-4 ↔ `repo_bringup._target_is_headless` + run-step `desktop_headless` routing ↔ tests/test_plan89_helpers.py::TestHeadlessProbe.
- REQ-PLAN89-5 ↔ `repo_bringup._idempotent_guard` + draft step post-process ↔ tests/test_plan89_helpers.py::TestIdempotentGuard, tests/test_agent_thoughts.py::test_idempotent_guards_present.

### R46 (Plan 90 Phase 1 / Plan 91) traceability

- REQ-PLAN91-1 ↔ `repo_agent.run_repo_agent` → `harness/loop.run_agent` + `harness/tools.build_default_registry`; `harness/agents/repo_qa.md`; `/ask` in `main.handle_session_command`/`_handle_ask_command` ↔ tests/test_repo_agent.py::TestRunRepoAgent/TestRepoQaDefinition.
- REQ-PLAN91-2 ↔ `repo_agent._synthesize_answer`/`_observation_digest` ↔ tests/test_repo_agent.py::test_reads_files_and_synthesizes_answer.
- REQ-PLAN91-3 ↔ `harness/tools._resolve_safe_project_path` (reused) ↔ tests/test_repo_agent.py::test_path_traversal_is_blocked.
- REQ-PLAN91-4 ↔ `repo_agent.run_repo_agent` no-llm branch + `_handle_ask_command` guards ↔ tests/test_repo_agent.py::test_no_llm_configured_is_honest.
- REQ-PLAN91-5 ↔ `repo_agent.run_repo_agent` `_turn_cb` → emit_thought ↔ tests/test_repo_agent.py::test_reads_files_and_synthesizes_answer.

### R47–R50 (Plan 90 Phases 2–5 / Plans 92–95) traceability

- REQ-PLAN92-1 ↔ `harness/tools.py` `_fs_search_handler`/`_fs_glob_handler`/`_fs_write_handler`/`_fs_edit_handler` + specs ↔ tests/test_repo_phases.py::TestFilesystemTools.
- REQ-PLAN92-2 ↔ `harness/tools._maybe_offload` (used in fs.read_file/fs.search/repo.run) ↔ tests/test_repo_phases.py::test_large_result_offloads_to_file.
- REQ-PLAN93-1 ↔ `harness/tools._repo_run_handler`/`_git_handler` (`_GIT_BLOCKED`) + `harness/agents/repo_engineer.md` ↔ tests/test_repo_phases.py::test_git_blocks_push / TestOrchestratedTask.
- REQ-PLAN93-2 ↔ `repo_agent._synthesize_answer` kind="action" + `/do`/`_handle_do_command` ↔ tests/test_repo_phases.py::test_delegate_explorer_then_engineer_edits.
- REQ-PLAN94-1 ↔ `repo_agent.run_repo_task` (Explorer→Engineer, separate run_agent contexts, `extra_hints`) ↔ tests/test_repo_phases.py::test_delegate_explorer_then_engineer_edits.
- REQ-PLAN95-1 ↔ `harness/tools.ToolRegistry.dispatch` approval gate + `_tool_policy_decision`/`_approval_summary`; `access.write_tool_policy`/`read_tool_policy`; `/policy`/`_handle_policy_command` ↔ tests/test_repo_phases.py::TestApprovalGate, test_tool_policy_roundtrip.
- REQ-PLAN95-2 ↔ `state/store.MemoryStore`/`set_state_store_factory`/`initialize_state_store` ↔ tests/test_repo_phases.py::test_pluggable_store_factory.
- REQ-PLAN95-3 ↔ `access.write_repo_memory`/`read_repo_memory` (recalled as hints in `_handle_ask_command`), `write_user_preference`/`read_user_preferences`, `/prefs` ↔ tests/test_repo_phases.py::test_repo_memory_persists_and_trims, test_user_prefs_roundtrip.

### R51–R56 (Plan 96 / Plans 97–102) traceability
- REQ-PLAN97-1/2 ↔ repo_bringup._wrap_command_for_execution_target (container) + _active_container_name; harness/tools._remote_fs/_remote_*_command/_remote_safe_join/_run_on_target + handler branches; main._resolve_repo_agent_target ↔ tests/test_repo_phases.py::TestRemoteFsCommands (+ live duckln-vm).
- REQ-PLAN98-1 ↔ repo_bringup.expose_app_and_open/_container_published_url/_start_cloud_tunnel_url, cloud_runtime.build_cloud_tunnel_command, harness/tools._app_serve_handler ↔ tests/test_repo_phases.py::TestExposeAppAndOpen.
- REQ-PLAN99-1 ↔ harness/native_tools.* + harness/loop.run_agent tool_decider ↔ tests/test_native_tools.py.
- REQ-PLAN100-1 ↔ repo_agent.run_parallel_explorers ↔ tests/test_repo_phases456.py::TestParallelExplorers.
- REQ-PLAN101-1 ↔ access.read_repo_memory_ranked/_tokenize, store.FileMemoryStore ↔ tests/test_repo_phases456.py::TestRankedMemory.
- REQ-PLAN102-1 ↔ repo_agent.classify_repo_agent_intent + main._maybe_autoroute_repo_agent ↔ tests/test_repo_phases456.py::TestIntentClassifier.
- REQ-PLAN102-2 ↔ harness/tools._git_handler default-branch guard ↔ tests/test_repo_phases.py::test_git_blocks_push.

### R57–R62 (Plan 103 / Plans 104–109) traceability
- REQ-PLAN104-1 ↔ harness/context_budget.assemble_observation_block + loop.render_state_for_llm ↔ tests/test_context_budget.py::TestCompaction.
- REQ-PLAN105-1 ↔ context_budget.estimate_tokens/context_window_for/usable_context_tokens/_dedup_observations ↔ tests/test_context_budget.py::TestTokenBudget/test_dedup_consecutive.
- REQ-PLAN106-1 ↔ context_budget.map_reduce_summarize ↔ tests/test_context_budget.py::TestMapReduce.
- REQ-PLAN107-1 ↔ repo_agent._draft_todo + run_repo_task ↔ tests/test_repo_plan103.py::TestPlanFirstTodo.
- REQ-PLAN108-1 ↔ repo_agent.run_repo_task + harness/bus.MessageBus ↔ tests/test_repo_plan103.py::TestLiveA2A.
- REQ-PLAN109-1 ↔ repo_agent._distill_repo_skill/_recall_repo_skill + access.write_repo_skill/read_repo_skills_ranked ↔ tests/test_repo_plan103.py::TestSkillDistillation.

### R63 (Plan 110) traceability
- REQ-PLAN110-1 ↔ repo_bringup._launch_and_await_server/_launch_desktop_stream (log_tail) + resume_with_approved_plan death branches + harness/tools._app_serve_handler ↔ tests/test_plan110.py::TestRealErrorAttribution.
- REQ-PLAN110-2 ↔ repo_bringup._is_heavy_build + draft swap injection ↔ tests/test_plan110.py::TestHeavyBuildDetection, tests/test_agent_thoughts.py.
- REQ-PLAN110-3 ↔ repo_bringup.composite_target_label + textual_ui.build_connection_context_label/update_connection ↔ tests/test_plan110.py::TestCompositeLabel, tests/test_textual_ui.py.
- REQ-PLAN110-4 ↔ repo_bringup._amendment_fix_is_plausible in _attempt_amendment_and_halt ↔ tests/test_plan110.py::TestPlausibilityGuard.
- REQ-PLAN110-5 ↔ repo_bringup draft low-RAM resize recommendation.

### R64 (Plan 111) traceability
- REQ-PLAN111-1 ↔ resource_manager.probe_target_resources/probe_host_resources/parse_resource_block ↔ tests/test_resource_manager.py::TestProbeParsing (+ live duckln-vm).
- REQ-PLAN111-2 ↔ resource_manager.estimate_requirement/recommend ↔ tests/test_resource_manager.py::TestDerivedNotHardcoded.
- REQ-PLAN111-3 ↔ resource_manager.recommend (host cap + cost note) ↔ tests/test_resource_manager.py::TestHostBound.
- REQ-PLAN111-4 ↔ resource_manager.apply_resize ↔ tests/test_resource_manager.py::TestApplyResizeGuards.
- REQ-PLAN111-5 ↔ repo_bringup._handle_resource_crunch + access.write_resource_event/read_resource_log + main._handle_resources_command ↔ tests/test_resource_manager.py::TestResourceLog.

### R65 (Plan 112) traceability
- REQ-PLAN113-1 ↔ repo_bringup._tauri_prebuild_steps/_declared_prebuild_script_steps + draft codegen injection ↔ tests/test_plan112.py::TestPrebuildSequence.
- REQ-PLAN114-1 ↔ resource_manager.resize_commands_for_target (container) ↔ tests/test_plan112.py::TestContainerResize.
- REQ-PLAN115-1 ↔ cloud_runtime.build_cloud_resize_commands ↔ tests/test_plan112.py::TestCloudResize.
- REQ-PLAN117-1 ↔ repo_bringup draft proactive probe via resource_manager.probe_target_resources.
- REQ-PLAN118-1 ↔ tests/integration/matrix.py + run.py (opt-in gate).

### R66 (Plan 119) traceability
- REQ-PLAN119-1 ↔ connection_status.probe_provider_status (validate_api_key) ↔ test_plan119.py::TruthfulProviderStatus.
- REQ-PLAN119-2 ↔ connection_status.ensure_provider_connected + main.py REPL/`/ask`/`/do` gates ↔ test_plan119.py::ProviderGate.
- REQ-PLAN119-3 ↔ plan_mode.attribute_failure/_attribution_llm_call/parse_plan_json + ai_client json_mode/max_tokens threading ↔ test_plan119.py::{TruncationRepair,AttributionCall,JsonModePayload}.
- REQ-PLAN119-4 ↔ repo_bringup._secondary_python_setup_steps + diagnostics.MISSING_VENV ↔ test_plan119.py::{DeterministicVenvFix,SecondaryEcosystemOrdering}.
- REQ-PLAN119-5 ↔ textual_ui._transient_activity_update/_is_ollama_pull_noise ↔ test_plan119.py::OllamaProgress.

### R67 (Plan 120) traceability
- REQ-PLAN120-1 ↔ repo_bringup._secondary_python_setup_steps/_SECONDARY_PY_SETUP_CMD + call-site injection (target=execution_target) ↔ test_plan119.py::SecondaryEcosystemOrdering; fallback diagnostics.MISSING_VENV (R66).

### R68 (Plan 121) traceability
- REQ-PLAN121-1 ↔ connection_status.probe_provider_status (adapter base_url fallback + cache key) ↔ test_plan119::TruthfulProviderStatus + test_plan121::CacheKeyBustsOnCredentialChange.
- REQ-PLAN121-2 ↔ textual_ui.show_text_prompt_overlay(mode=secret)/prompt_secret + main._build_chat_secret_prompt ↔ test_plan121::MaskedSecretPrompt.
- REQ-PLAN121-3 ↔ config.verify_live_reply in run_onboarding + update_runtime_provider ↔ test_plan121::VerifyLiveReply.

### R69 (Plan 122) traceability
- REQ-PLAN122-1 ↔ diagnostics.match_deterministic_fix rule 4 (venv-aware) ↔ test_plan122::VenvAwareMissingModule.
- REQ-PLAN122-2 ↔ ai_client.generate_reply 429/503 backoff + _backoff_seconds/_retry_after_seconds ↔ test_plan122::RateLimitBackoff.
- REQ-PLAN122-3 ↔ repo_bringup._SECONDARY_PY_SETUP_CMD dev/build reqs ↔ test_plan122::BackendBuildRequirements.

### R70 (Plan 123) traceability
- REQ-PLAN123-1 ↔ tests/integration/driver.run_spec (bring_up_selected_repo) + run.py + matrix.py ↔ test_plan123::{DriverPassFail,MatrixSanity}; live: flask-hello/fastapi-min PASS local.
- REQ-PLAN123-2 ↔ repo_bringup.py dead-line removal + .gitignore/untracked .pyc ↔ test_plan123::CleanupRegression; AST sweep (1 issue, fixed).

### R71 (Plan 124) traceability
- REQ-PLAN124-1 ↔ diagnostics TORCH_WHEEL_MISMATCH + GPU_UNAVAILABLE ↔ test_plan124::{MacTorchWheel,MacCudaBlock}.
- REQ-PLAN124-2 ↔ diagnostics MODEL_AUTH_REQUIRED + PACKAGE_MANAGER_MISSING + repo_bringup pyenv fallback ↔ test_plan124::{GatedHuggingFace,MacToolchain,MacPyenvFallback}.
- REQ-PLAN124-3 ↔ tests/integration/driver real probe + matrix streamlit-demo ↔ live local PASS (flask/fastapi/streamlit); playbooks/ml_python.md.

### R72 (Plan 125) traceability
- REQ-PLAN125-1 ↔ diagnostics.TF_MACOS_REQUIRED + ml_python.md (framework-agnostic) ↔ test_plan125::TensorFlowAppleSilicon.
- REQ-PLAN125-2 ↔ tests/integration mlx-smoke (driver.run_smoke + matrix) ↔ test_plan125::MlxSmokeRow; live: mlx-smoke PASS on Apple Silicon.

### R73 (Plan 126) traceability
- REQ-PLAN126-1 ↔ signal-keyed class machinery (repo_bringup `_detect_app_archetype`/`_tauri_prebuild_steps`/`_secondary_python_setup_steps`/`_desktop_stream_*`, diagnostics venv-aware missing-module) ↔ test_plan126 (name-independence); grep confirms 0 repo-name branches.
- REQ-PLAN126-2 ↔ tests/integration/matrix.py class rows (tauri-py-sidecar, electron-quickstart) ↔ pending VM run (≥2 instances reach running desktop).

### R74 (Plan 127) traceability
- REQ-PLAN127-1 ↔ repo_bringup._idempotent_guard (completion marker) + _SECONDARY_PY_SETUP_CMD (.duckln-deps-ok sentinel) ↔ test_plan127::{CompletionAwareNpmGuard,CompletionAwareVenv}.
- REQ-PLAN127-2 ↔ resource_manager.reclaim_commands + disk-crunch injection in repo_bringup ↔ test_plan127::DiskReclamation.

### R75 (Plan 129) traceability
- REQ-PLAN129-1 ↔ recovery.recover_failed_step_with_agent + repo_bringup._attempt_amendment_and_halt wiring ↔ test_plan129::{DecisionParsing,RecoveryAgentWrapper}; live multi-turn loop VM-staged.
- REQ-PLAN129-2 ↔ recovery.classify_blast_radius/recovery_autonomy ↔ test_plan129::{BlastRadius,Autonomy}.
- REQ-PLAN129-3 ↔ recovery.append_thinking_log + repo_bringup._declared_prebuild_script_steps(--if-present) ↔ test_plan129::{ThinkingLog,AbsentScriptIfPresent}.

### R76 (Plan 132) traceability
- REQ-PLAN132-1 ↔ recovery.{reasoning_enabled,run_bounded_agent,ReasoningBudget} + repo_bringup recovery wiring ↔ test_plan132::{ReasoningGate,BoundedAgent,Budget}, test_plan129::ActVerify.
- REQ-PLAN132-2 ↔ recovery.reason_about_plan + _generate_plan_for_bringup trigger ↔ test_plan132::ReasoningPlanningPass.
- REQ-PLAN132-3 ↔ recovery.model_is_reasoning_capable + diagnostics.run_healthcheck warning + verified-fix lesson/telemetry ↔ test_plan132::ModelCapabilityGate.

### R77 (Plan 133) traceability
- REQ-PLAN133-1 ↔ repo_bringup text_prompt thread + resource_manager.apply_resize(prompt_value).
- REQ-PLAN133-2 ↔ repo_bringup `_load/_mark/_clear_done_steps` + resume skip + resource_manager.reclaim_commands scratch ↔ test_plan133::{ResumeState,BuildScratchReclaim}.
- REQ-PLAN133-3 ↔ repo_bringup `_generate_plan_for_bringup` runtime-dir to reason_about_plan; brief in understanding→critic.
- REQ-PLAN133-4 ↔ textual_ui._message_dot_color + recovery.append_thinking_log/surface_thinking_link ↔ test_plan133::{DotRecolor,ThinkingLogFile}.

### R87 (Plan 144) traceability
- REQ-PLAN144-1 ↔ recovery.apply_fix_and_verify(next_fix, max_stages) + repo_bringup._auto_apply_recovery_fix `_next_stage_fix` (match_deterministic_fix on the new error, autonomy-gated) ↔ test_plan144::ProgressAwareVerify.
- REQ-PLAN144-2 ↔ repo_bringup._partial_cleanup_command(aggressive) + reclaim_partial_before_retry(aggressive=reclaim_caches) + diagnostics MISSING_VENV fix (touch .duckln-deps-ok + spec→pyinstaller) ↔ test_plan144::{CleanupKeepsTheVenvOnAFixReRun,MissingVenvFixIsComplete}.
- REQ-PLAN144-3 ↔ repo_bringup._is_marker_guarded_setup + resume done-skip guard ↔ test_plan144::MarkerGuardedSetupRerunsOnResume.

### R88 (Plan 145) traceability — partial (F2/F3/F5 done; F0/F1/F6/F7 pending)
- REQ-PLAN145-2 ↔ recovery.RecoveryReport.evidence + _report_from_payload/_from_prose + tool-chain fallback + _RECOVERY_SYSTEM ↔ test_plan145::EvidenceField.
- REQ-PLAN145-3 ↔ recovery.supervise_recovery_conclusion + _llm_supervisor_review + _SUPERVISOR_SYSTEM + recover_failed_step_with_agent review loop ↔ test_plan145::{SupervisorGate,SupervisorWiredIntoRecovery}.
- REQ-PLAN145-5 ↔ repo_bringup._auto_apply_recovery_fix episode + _attempt_amendment_and_halt _log_episode (block/skip/halt/amend) + removed pre-diagnosis stub.
- REQ-PLAN145-1 ↔ repo_bringup._attempt_amendment_and_halt (`_model_is_reasoner` gates the deterministic decider off in production; `_det_hint` passed to the agent; production honest-stop replaces the one-shot attribute fallback) + recovery.recover_failed_step_with_agent `hint` ↔ test_plan145::DeterministicHintNotDecider.
- REQ-PLAN145-0 ↔ repo_bringup._generate_plan_for_bringup_impl (`_nontrivial`/`_build_surface` trigger) + recovery._PLANNING_SYSTEM (read script bodies + referenced files).
- REQ-PLAN145-6 ↔ repo_bringup._auto_apply_recovery_fix → _record_common_lesson_from_fix (reasoned path) + the success learning loop (record_plan_skill/persist_reflection) + F7 reasoning intent. PARTIAL: a deep "ask arbitrary questions answered from the thinking log" is not wired into run_repo_agent (the log can be opened, but the Q&A agent reads the repo, not the log).
- REQ-PLAN145-7 ↔ repo_agent.classify_repo_agent_intent (reasoning/recheck first) + main._maybe_autoroute_repo_agent (open reasoning log / route recheck) + softened /ask//do prompts ↔ test_plan145::NaturalLanguageIntent.
