# Duckln First-Launch Demo Script

**Scope**
- Task: `Record first-launch demo script`
- Requirements: `R1`, `R2`, `R3`, `R7`, `R8`, `R9`
- Plan: `2`, `3`, `4`, `8`, `9`, `10`

This script is written against the current repository behavior only.

## Demo Goal

Show a first-time user flow that covers:
- Duckln launch branding
- provider setup
- model selection
- mode selection
- one real command failure
- exact next-step suggestion
- verification guidance
- concise teaching

## Demo Setup

- Platform: macOS or Ubuntu
- Shell: `zsh` or `bash`
- Python: `3.11+`
- Dependencies installed from `pyproject.toml`
- One valid provider API key ready for:
  - OpenRouter, or
  - OpenAI, or
  - Anthropic
- Start with no existing Duckln config file

## Demo Flow

### 1. Launch Duckln Without Config

**Narration**
- “Duckln starts with a branded first-run experience instead of asking for manual config edits.”

**Expected On-Screen Behavior**
- Pixel Duckln banner renders.
- Provider selection UI appears.

### 2. Select Provider

**Suggested Demo Choice**
- Select `OpenAI`

**Narration**
- “Duckln supports OpenRouter, OpenAI, and Anthropic from the same onboarding flow.”

**Expected On-Screen Behavior**
- Provider list is shown in arrow-key selection UI.

### 3. Enter API Key and Validate

**Action**
- Enter the provider API key.

**Narration**
- “Duckln validates the key before saving anything, so broken config is not written.”

**Expected On-Screen Behavior**
- Entered key is shown back for confirmation.
- Validation happens before config is saved.
- If validation fails, a retryable error is shown.

### 4. Select Model

**Action**
- Choose one model from the fetched provider model list.

**Narration**
- “The model list comes from the selected provider after authentication, then Duckln sorts it for selection.”

**Expected On-Screen Behavior**
- Models are fetched live from the provider.
- Model list is sorted alphabetically.
- Selection uses arrow-key UI.

### 5. Select Mode

**Suggested Demo Choice**
- Select `HOTL`

**Narration**
- “Duckln lets the user choose how much control to keep. HOTL is a good demo mode because it shows suggested execution with approval.”

**Expected On-Screen Behavior**
- Mode list shows:
  - full mode name
  - one-line beginner-friendly explanation
- Config is saved after valid provider, model, and mode selection.

### 6. Trigger a Real Failure

**Action**
- Run a real failing command:

```bash
cat ./does-not-exist.txt
```

**Narration**
- “Here the failure is concrete and easy to understand: the file is missing.”

**Expected On-Screen Behavior**
- Duckln captures stderr safely.
- Duckln classifies the issue as a file/path problem.
- Duckln keeps context minimal and privacy-first.

### 7. Show Suggested Next Steps

**Narration**
- “Duckln responds with a short explanation and only a few exact next commands.”

**Expected On-Screen Behavior**
- Duckln returns `1–3` exact next commands.
- Each command has a short purpose label.
- Commands are safe/high-signal first.

### 8. Approve a Fix in HOTL

**Suggested Demo Fix**
- Approve a command equivalent to:

```bash
touch ./does-not-exist.txt
```

**Narration**
- “In HOTL, Duckln never runs the fix until the user approves it.”

**Expected On-Screen Behavior**
- Approval is required before execution.
- Command runs through the controlled runner.
- Execution result is logged without full raw output.

### 9. Show Verification

**Narration**
- “Duckln does not stop at the fix suggestion. It verifies the result.”

**Expected On-Screen Behavior**
- Duckln suggests a narrow verification first, such as checking the path.
- Duckln then offers rerun verification of the original command when safe.

### 10. Show Brief Teaching

**Narration**
- “Duckln teaches briefly without turning the terminal into a long tutorial.”

**Expected On-Screen Behavior**
- Explanation stays concise.
- Teaching is action-first and beginner-friendly.

## Demo Close

Close on these points:
- onboarding is guided
- provider/model selection is validated
- mode behavior is explicit
- suggestions are exact and bounded
- verification is part of the loop
- privacy and safety are preserved

## Demo Risks

- Provider validation requires a working API key and network connectivity.
- Model list contents vary by provider account.
- The active session prompt does not yet visibly reflect mode state in a dedicated prompt renderer.
- Packaging/install demo is intentionally excluded from this script because packaging is still an open Phase 8 task.
