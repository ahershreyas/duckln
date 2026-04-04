# Duckln UI Standards

## Purpose
Define the terminal UX standards for Duckln so output stays consistent, readable, trustworthy, and visually distinct across all flows.

## Design Principles
Duckln terminal UX should be:
- compact, not noisy
- readable at a glance
- trustworthy and explicit, not verbosity
- consistent across all commands
- Strong error readability
- visually distinct without becoming flashy
- practical for developers who reconnect often
- Short work-status updates instead of hidden behavior

## Rich Color System
Use Python `rich` for all terminal color rendering and panels.

Define these colors once in a shared UI module and never hardcode colors elsewhere.

```python
DUCKLN_GOLD        = "#F5A623"  # Duckln name, brand, key highlights
DUCKLN_COMMAND     = "#00FFFF"  # Slash commands and command hints
DUCKLN_USER_INPUT  = "#F0EAD6"  # User prompt indicator and entered input
DUCKLN_SYSTEM      = "#A9A9A9"  # Neutral system narration and prompts
DUCKLN_GREEN       = "#4CAF50"  # Success states
DUCKLN_RED         = "#E53935"  # Errors and hard failures
DUCKLN_ORANGE      = "#FF6D00"  # Warnings and caution states
DUCKLN_GREY        = "#616161"  # Metadata,hints, secondary labels

## Usage Rules
- Brand/tool name should always use the same accent color
- Slash commands should always use the same action color
- Success, warning, and error colors must never overlap in meaning
- User input prompt must be visually distinct from system output
- Metadata such as counts, or timestamps should use secondary grey except stars.


## Session Header
At session start, Duckln should show a compact header with:
- provider
- model
- mode
- user
- memory state

## Required format:
┌──────────────────────────────────────────────┐
│ ◆ Duckln                                     │
│                                              │
│ provider : <provider>    /provider to change │
│ model    : <model>       /model to change    │
│ mode     : <mode>        /mode to change     │
│ user     : <name>                            │
│ memory   : <state>                           │
└──────────────────────────────────────────────┘
Rules:
- maximum 10 lines
- compact
- no large banner repeated after onboarding unless explicitly requested
- must remain readable on narrow terminals
- must appear inside a box/panel
- maximum 8–10 lines
- must not waste vertical space
- command hints must use command color
- labels should use Duckln gold or neutral readable style
- values should remain plain and easy to scan
- shown at startup after onboarding is complete
- not repeated unnecessarily mid-session

## Command Discovery
Duckln should always make command discovery easy.

Rules:
- `/help` prints a short list of available commands with one-line descriptions
- `/` opens the interactive command palette
- all command descriptions must be concise and readable
- provider/model/mode lines in header should hint the relevant slash command

## Cancel vs Exit
- `cancel` returns to previous menu or prompt without changing state
- `exit` or `/exit` ends the Duckln session
- `Ctrl+C` acts as cancel inside menus and exit at the top-level prompt
- no flow should confuse cancel with exit

## Work-Status Updates
For non-trivial actions, Duckln should show short work updates.

Examples:
- Reading your README to understand the setup path...
- Checking which Python is active...
- Trying the lighter fix first...
- Pulling local models from Ollama...

Rules:
- one line at a time
- no full chain-of-thought
- useful, not decorative
- no terminal spam
- these may appear inside a lightweight status box if needed, but should stay compact

## Panels and Boxes
Use boxes only for:
- first-run safety screen
- startup session header
- important success summaries
- high-signal warnings

Do not overuse panels. Most output should remain plain terminal text.

## Error Style
Errors should always include:
1. what failed
2. what Duckln knows
3. the smallest next action

Bad:
- giant traceback dump
- opaque generic failure
- repeated retry loops without explanation

Good:
- “Ollama is not reachable at http://localhost:11434.”
- “Start it with: ollama serve”
- “Would you like to retry, use a custom URL, or choose a different provider?”

## Success Style
Success states should be explicit.

Examples:
- ✓ Provider set to Ollama — using llama3.2:3b
- ✓ Setup complete
- ✓ VM created and ready

Duckln should not imply success without verification.

## Repo Selector UX
Repo lists should:
- include Cancel / Return option
- show clean labels
- truncate long descriptions
- show stars and category/framework compactly
- avoid clutter

## Safety Screen Tone
Duckln’s safety tone should be:
- honest
- calm
- trustworthy
- not alarmist

## Personality Tone
Duckln should sound like:
- a sharp, calm senior engineer
- direct
- slightly witty when natural
- never robotic
- never corporate
- never fake-confident
