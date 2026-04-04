# Duckln Onboarding Flow

## Purpose
Define the first-run experience so users trust Duckln quickly and know exactly what state they are in.

## First-Run Sequence
Duckln first-run onboarding should happen in this order:

1. Safety and permissions screen
2. User name capture
3. Provider selection
4. Provider-specific setup
5. Model selection
6. Mode selection
7. Final confirmation
8. Session header + next-step hint

## Step 1 — Safety Screen
Show once on first run only.

Recommended copy:

◆ Safety & Permissions

Duckln can run terminal commands, read project files,
and install dependencies on your machine.

It will always ask before executing in HOTL mode.
In HOOTLWO mode, only pre-approved safe commands run.
Destructive commands are always blocked.

I understand and want to continue. (y/n)

Rules:
- store acceptance with timestamp
- do not show again unless user resets onboarding or runs a future `/safety` flow

## Step 2 — Name Capture
Immediately after safety acceptance, ask:

◆ Before we start —

What would you like me to call you?
>

Store this in user preferences and use it in future sessions.

## Step 3 — Provider Selection
Show provider list including:
- OpenRouter
- OpenAI
- Anthropic
- Ollama

Rules:
- provider menu must include Cancel / Exit / Back behavior
- preserve current state if cancelled
- Ollama must have a dedicated local-provider flow, not API-key flow

## Step 4 — Cloud Provider Flow
For cloud providers:
1. ask for API key
2. validate provider connection
3. fetch models
4. show model list
5. confirm success clearly

After model selection, show a clear success line such as:
✓ Provider set to OpenRouter — using <model>

## Step 5 — Ollama Flow
When Ollama is selected:
1. detect default or configured base URL
2. if reachable:
   - show success
   - list pulled local models
   - allow select existing or pull new
3. if installed but not running:
   - offer to start it
4. if not installed:
   - show OS-specific guidance
   - optionally offer install according to mode/safety rules
5. if default URL fails:
   - allow custom URL entry
6. never ask for API key
7. store `api_key: null`

## Step 6 — Model Selection
Model selection must:
- include Cancel / Back
- preserve current state on cancel
- show readable names
- work for both cloud and Ollama paths

## Step 7 — Mode Selection
Modes:
- HITL
- HOTL
- HOOTLWO

Each should include a short explanation.

If HOOTLWO is selected, show a concise warning that only safe whitelisted commands can auto-run.

## Step 8 — Final Confirmation
After onboarding completes successfully, show:
- provider
- model
- mode
- user name

Then show:
Duckln is ready. Type /help to explore commands.

## Post-Onboarding Session Start
At the next startup, Duckln should:
- load user preferences
- show compact session header
- not repeat onboarding

## Failure Rules
- never fail silently
- never trap user in one flow
- always offer retry / back / cancel
- preserve last good config when setup is cancelled or fails

## Special Provider Rules
- Ollama never asks for API key
- cloud providers always validate before saving
- if model validation fails, do not overwrite working config

## Persistence
Store:
- user name
- safety acceptance
- onboarding completion
- preferred mode
- provider
- model
- memory state
- Ollama base URL if applicable