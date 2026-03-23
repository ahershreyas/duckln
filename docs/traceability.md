# Duckln Traceability Map

This map links existing items only from:
- `docs/requirements.md`
- `docs/plan.md`
- `docs/tasks.md`
- `tests/`

Status labels:
- `Covered` means at least one existing checked task and test link exists.
- `Partial` means a requirement has implementation/task coverage but incomplete or indirect test/release coverage.
- `Missing Link` means a requirement lacks a direct checked task and/or direct test artifact in the current repository.

## Requirement Coverage

| Requirement | Plan Item(s) | Checked Task(s) | Test Link(s) | Status | Missing Link Notes |
| --- | --- | --- | --- | --- | --- |
| `R1 — First Run Onboarding` | `1`, `2`, `11`, `15` | Provider enum and shared config schema; OpenRouter adapter; OpenAI adapter; Anthropic adapter; provider key validation and model validation flow; onboarding wizard with mode selection and config save; slash commands `/mode`, `/provider`, `/model`, `/config`; `/` command palette; prompt for provider API key and validate before saving config; fetch provider model list; sort models alphabetically and present via arrow-key selection UI; save selected model in config; display retryable provider/model errors | `tests/test_ai_client.py`; `tests/test_onboarding.py`; `tests/test_main.py`; `tests/test_config.py` | `Covered` | `Plan 15` is not separately implemented as a launch-polish artifact. |
| `R2 — Pixel Brand Presence` | `3` | Add pixel banner asset and narrow-terminal fallback rendering | `tests/test_ui.py` | `Covered` | None. |
| `R3 — Three Mode System` | `4`, `11`, `14` | Onboarding wizard with mode selection and config save; display mode selection with full names and one-line explanations during onboarding; implement mode model for HITL/HOTL/HOOTLWO; implement safety classes `S0–S4`; implement HOOTLWO whitelist for safe diagnostics and installs only; slash commands `/mode`, `/provider`, `/model`, `/config`; `/` command palette; implement HITL behavior: suggest only; implement HOTL behavior: ask approval before run | `tests/test_modes.py`; `tests/test_onboarding.py`; `tests/test_main.py`; `tests/test_prompts.py`; `tests/test_safety.py` | `Covered` | Requirement text says the terminal prompt must reflect active mode clearly; there is no direct prompt-render test/artifact for that behavior. |
| `R4 — Safe Command Execution` | `4`, `5`, `14` | Implement blocked command matcher; implement safety classes `S0–S4`; implement timeout and controlled subprocess execution; implement HOOTLWO whitelist for safe diagnostics and installs only; implement mode model for HITL/HOTL/HOOTLWO | `tests/test_safety.py`; `tests/test_shell.py`; `tests/test_modes.py` | `Covered` | No end-to-end diagnosis-flow test after non-zero exit yet. |
| `R5 — Minimal Privacy Payload` | `4`, `6` | Implement redaction rules for tokens, keys, and obvious secrets; implement minimal payload builder by mode; implement structured redaction-safe logs | `tests/test_diagnostics.py`; `tests/test_prompts.py` | `Covered` | Requirement says the user can inspect full outbound context before send; no direct task or test covers that interaction. |
| `R6 — Python/AI Error Classification` | `7`, `12`, `13` | Implement classification for missing module, pip/python mismatch, file/path not found, permission denied, CUDA/torch mismatch; implement minimal context gatherer for Python/AI issues; implement `/healthcheck` command | `tests/test_diagnostics.py`; `tests/test_main.py` | `Covered` | Healthcheck acceptance mentions venv/key-package validation and clear issue listing; current coverage is indirect and not scenario-complete. |
| `R7 — Exact Next-Step Suggestions` | `8`, `13` | Implement bounded prompt templates with concise explanation rules; return `1–3` exact next commands with purpose labels; implement HITL behavior: suggest only; implement HOTL behavior: ask approval before run | `tests/test_prompts.py`; `tests/test_modes.py` | `Covered` | No direct test yet for ordering commands from safest/highest-signal to lowest-signal. |
| `R8 — Fix Verification` | `9`, `16` | Implement verification checks after suggested or auto-run fixes | `tests/test_prompts.py` | `Partial` | No end-to-end verification-loop integration test; `Plan 16` session summaries are not implemented. |
| `R9 — Teaching Layer` | `10`, `11`, `16` | Implement bounded prompt templates with concise explanation rules; implement short teaching snippets for venv, pip, CUDA, torch mismatch; display mode selection with full names and one-line explanations during onboarding | `tests/test_prompts.py`; `tests/test_onboarding.py` | `Covered` | No direct test for adapting explanation length when the user wants commands only. |
| `R10 — Production Logging and Traceability` | `1`, `6`, `12` | Implement structured redaction-safe logs; add provider failure logging without secret leakage; add command blocked-event logging; add traceability map from requirement → plan → task → test | `tests/test_diagnostics.py`; `tests/test_ai_client.py`; `tests/test_safety.py`; `tests/test_shell.py`; `docs/traceability.md` | `Covered` | `Analytics enabled` behavior is not separately implemented as a toggle or tested. |

## Plan Item Coverage

| Plan Item | Covers | Existing Task Link(s) | Existing Test Link(s) | Status | Missing Link Notes |
| --- | --- | --- | --- | --- | --- |
| `1. Create provider abstraction for OpenRouter, OpenAI, Anthropic` | `R1`, `R10` | Provider enum and shared config schema; OpenRouter adapter; OpenAI adapter; Anthropic adapter | `tests/test_ai_client.py`; `tests/test_onboarding.py` | `Covered` | Provider adapter tests are mocked, not labeled as integration tests. |
| `2. Implement first-run onboarding and local config` | `R1` | Add local config path helpers and environment defaults; provider key validation and model validation flow; onboarding wizard with mode selection and config save; provider/model selection tasks | `tests/test_config.py`; `tests/test_onboarding.py` | `Covered` | None. |
| `3. Add pixel banner and duck headshot fallback` | `R2` | Add pixel banner asset and narrow-terminal fallback rendering | `tests/test_ui.py` | `Covered` | None. |
| `4. Implement mode policy engine` | `R3`, `R4`, `R5` | Mode model for HITL/HOTL/HOOTLWO; onboarding mode display; HITL suggest-only; HOTL approval before run | `tests/test_modes.py`; `tests/test_onboarding.py`; `tests/test_prompts.py` | `Covered` | Prompt reflection of active mode is not directly tested. |
| `5. Build safe shell execution layer` | `R4` | Blocked command matcher; safety classes `S0–S4`; timeout and controlled subprocess execution | `tests/test_safety.py`; `tests/test_shell.py` | `Covered` | No end-to-end non-zero-exit diagnosis test yet. |
| `6. Build privacy-first redaction and payload shaping` | `R5`, `R10` | Redaction rules; minimal payload builder by mode; structured redaction-safe logs | `tests/test_diagnostics.py`; `tests/test_prompts.py` | `Covered` | No explicit outbound-context inspection flow. |
| `7. Implement error classification for Python/AI environment issues` | `R6` | Missing module; pip/python mismatch; file/path not found; permission denied; CUDA/torch mismatch; minimal context gatherer | `tests/test_diagnostics.py` | `Covered` | Manual scenario artifacts still missing. |
| `8. Implement exact next-step suggestion loop` | `R7` | Bounded prompt templates; `1–3` exact next commands with purpose labels | `tests/test_prompts.py` | `Covered` | No direct safest-first ordering test. |
| `9. Implement verification loop after fixes` | `R8` | Verification checks after suggested or auto-run fixes | `tests/test_prompts.py` | `Partial` | Integration verification-loop task remains unchecked. |
| `10. Implement concise teaching layer` | `R9` | Bounded prompt templates with concise explanation rules; short teaching snippets | `tests/test_prompts.py` | `Covered` | No direct command-only verbosity preference test. |
| `11. Implement runtime command palette and slash command configuration flow` | `R1`, `R3` | Slash commands `/mode`, `/provider`, `/model`, `/config`; `/` command palette | `tests/test_main.py` | `Covered` | None. |
| `12. Implement environment healthcheck command` | `R6` | `/healthcheck` command; structured redaction-safe logs; provider failure logging; blocked-command logging; traceability map | `tests/test_diagnostics.py`; `tests/test_main.py`; `tests/test_ai_client.py`; `tests/test_safety.py`; `tests/test_shell.py`; `docs/traceability.md` | `Covered` | `Plan 12` includes broader production logging; analytics toggle is not present. |
| `13. Create deterministic heuristics before LLM calls` | `R6`, `R7` | Error classification tasks; bounded prompt templates; exact next commands | `tests/test_diagnostics.py`; `tests/test_prompts.py` | `Covered` | None. |
| `14. Create whitelisted safe auto-run set for HOOTLWO` | `R3`, `R4` | Safety classes `S0–S4`; HOOTLWO whitelist for safe diagnostics and installs only | `tests/test_safety.py`; `tests/test_modes.py` | `Covered` | None. |
| `15. Add richer onboarding polish and provider docs links` | `R1` | None | None | `Missing Link` | No checked task or test artifact exists for provider docs links/onboarding polish. |
| `16. Add session summaries for repeated failures` | `R8`, `R9` | None | None | `Missing Link` | No checked task or test artifact exists for repeated-failure summaries. |

## Task to Test Map

Only tasks already present in `docs/tasks.md` are listed below. Unchecked tasks are included when a partial or indirect test already exists.

| Task | Req | Plan | Test Link(s) | Status |
| --- | --- | --- | --- | --- |
| Create repo skeleton with `docs/`, `.sdc/`, `src/`, `tests/` | — | `1`, `2` | `tests/test_project_skeleton.py` | `Covered` |
| Add Python project metadata and dependency management | — | `1`, `2` | `tests/test_project_metadata.py` | `Covered` |
| Add local config path helpers and environment defaults | — | `2` | `tests/test_config.py` | `Covered` |
| Add pixel banner asset and narrow-terminal fallback rendering | `R2` | `3` | `tests/test_ui.py` | `Covered` |
| Implement provider enum and shared config schema | `R1` | `1`, `2` | `tests/test_ai_client.py`; `tests/test_onboarding.py` | `Covered` |
| Implement OpenRouter adapter | `R1` | `1` | `tests/test_ai_client.py` | `Covered` |
| Implement OpenAI adapter | `R1` | `1` | `tests/test_ai_client.py` | `Covered` |
| Implement Anthropic adapter | `R1` | `1` | `tests/test_ai_client.py` | `Covered` |
| Implement provider key validation and model validation flow | `R1` | `2` | `tests/test_ai_client.py`; `tests/test_onboarding.py`; `tests/test_main.py` | `Covered` |
| Implement onboarding wizard with mode selection and config save | `R1`, `R3` | `2` | `tests/test_onboarding.py` | `Covered` |
| Implement slash commands `/mode`, `/provider`, `/model`, and `/config` to update runtime configuration safely during an active session | `R1`, `R3` | `11` | `tests/test_main.py` | `Covered` |
| Implement `/` command palette to display available slash commands with short descriptions and allow arrow-key selection | `R1`, `R3` | `11` | `tests/test_main.py` | `Covered` |
| Prompt user to enter provider API key during onboarding and validate it before saving config | `R1` | `2` | `tests/test_onboarding.py` | `Covered` |
| Fetch available model list from the selected provider after successful API key validation | `R1` | `2` | `tests/test_onboarding.py`; `tests/test_ai_client.py` | `Covered` |
| Sort fetched models alphabetically and present them via arrow-key selection UI | `R1` | `2` | `tests/test_onboarding.py` | `Covered` |
| Save the selected model in config after validation | `R1` | `2` | `tests/test_onboarding.py`; `tests/test_main.py` | `Covered` |
| Display clear error messages for invalid API keys, failed connections, or empty model lists, and allow user to retry without crashing | `R1` | `2` | `tests/test_onboarding.py`; `tests/test_ai_client.py` | `Covered` |
| Display mode selection with full names and one-line explanations during onboarding | `R3` | `4` | `tests/test_onboarding.py` | `Covered` |
| Implement mode model for HITL/HOTL/HOOTLWO | `R3` | `4` | `tests/test_modes.py` | `Covered` |
| Implement blocked command matcher | `R4` | `5` | `tests/test_safety.py` | `Covered` |
| Implement safety classes `S0–S4` | `R3`, `R4` | `5`, `14` | `tests/test_safety.py`; `tests/test_modes.py` | `Covered` |
| Implement timeout and controlled subprocess execution | `R4` | `5` | `tests/test_shell.py` | `Covered` |
| Implement HOOTLWO whitelist for safe diagnostics and installs only | `R3`, `R4` | `14` | `tests/test_safety.py`; `tests/test_modes.py` | `Covered` |
| Implement redaction rules for tokens, keys, and obvious secrets | `R5` | `6` | `tests/test_diagnostics.py` | `Covered` |
| Implement minimal payload builder by mode | `R5` | `6` | `tests/test_diagnostics.py` | `Covered` |
| Implement classification for missing module / pip-python mismatch / file-path not found / permission denied / CUDA-torch mismatch | `R6` | `7`, `13` | `tests/test_diagnostics.py` | `Covered` |
| Implement minimal context gatherer for Python/AI issues | `R6` | `7`, `13` | `tests/test_diagnostics.py` | `Covered` |
| Implement `/healthcheck` command to validate environment, dependencies, and provider connectivity with clear pass/fail output | `R6` | `12` | `tests/test_diagnostics.py`; `tests/test_main.py` | `Covered` |
| Implement bounded prompt templates with concise explanation rules | `R7`, `R9` | `8`, `10`, `13` | `tests/test_prompts.py` | `Covered` |
| Return `1–3` exact next commands with purpose labels | `R7` | `8` | `tests/test_prompts.py` | `Covered` |
| Implement HITL behavior: suggest only | `R3`, `R7` | `4`, `8` | `tests/test_prompts.py`; `tests/test_modes.py` | `Covered` |
| Implement HOTL behavior: ask approval before run | `R3`, `R7` | `4`, `8` | `tests/test_prompts.py`; `tests/test_modes.py` | `Covered` |
| Implement verification checks after suggested or auto-run fixes | `R8` | `9` | `tests/test_prompts.py` | `Partial` |
| Implement short teaching snippets for venv, pip, CUDA, torch mismatch | `R9` | `10` | `tests/test_prompts.py` | `Covered` |
| Implement structured redaction-safe logs | `R10` | `12` | `tests/test_diagnostics.py` | `Covered` |
| Add provider failure logging without secret leakage | `R10` | `12` | `tests/test_ai_client.py`; `tests/test_diagnostics.py` | `Covered` |
| Add command blocked-event logging | `R10` | `12` | `tests/test_safety.py`; `tests/test_diagnostics.py` | `Covered` |
| Add traceability map from requirement → plan → task → test | `R10` | `12` | `docs/traceability.md` | `Covered` |
| Unit tests for safety rules | `R3`, `R4` | `5`, `14` | `tests/test_safety.py`; `tests/test_modes.py`; `tests/test_shell.py` | `Partial` |
| Unit tests for classifier rules | `R6` | `7`, `13` | `tests/test_diagnostics.py` | `Partial` |
| Unit tests for payload redaction | `R5` | `6` | `tests/test_diagnostics.py` | `Partial` |
| Integration tests for provider adapters | `R1` | `1` | `tests/test_ai_client.py` | `Partial` |
| Integration tests for verification loop | `R8` | `9` | `tests/test_prompts.py` | `Partial` |
| Manual scenario tests: missing torch in wrong environment | `R6`, `R7`, `R8` | `7`, `8`, `9` | None | `Missing Link` |
| Manual scenario tests: broken venv activation | `R6`, `R7`, `R8` | `7`, `8`, `9` | None | `Missing Link` |
| Manual scenario tests: file not found | `R6`, `R7`, `R8` | `7`, `8`, `9` | None | `Missing Link` |
| Manual scenario tests: permission denied | `R6`, `R7`, `R8` | `7`, `8`, `9` | None | `Missing Link` |
| Manual scenario tests: torch compiled without CUDA | `R6`, `R7`, `R8` | `7`, `8`, `9` | None | `Missing Link` |
| Record first-launch demo script | — | `3`, `8`, `10` | None | `Missing Link` |
| Prepare known limitations list | — | `15`, `16` | None | `Missing Link` |
| Prepare rollout checklist and rollback notes | — | `12` | None | `Missing Link` |
| Mark all linked tasks complete before release sign-off | — | — | None | `Missing Link` |
| Package Duckln as standalone executables for macOS and Ubuntu; include install instructions | — | `12`, `Release Readiness` | None | `Missing Link` |

## Missing Link Summary

- `Plan 15` has no current checked task or test artifact.
- `Plan 16` has no current checked task or test artifact.
- `R8` is only partially linked because verification is covered at planning/unit level in `tests/test_prompts.py`, but the integration-test task remains unchecked.
- Phase 7 unchecked test tasks already have partial evidence in existing tests, but they do not yet have dedicated completion artifacts.
- All five Phase 7 manual scenario tasks currently have no test or documentation artifact.
- All Phase 8 tasks currently have no implementation or test artifact.
