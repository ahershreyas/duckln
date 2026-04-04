# Provider-Routing Specialist Playbook

## Scope
- Detect provider-routing repos and runtime hints for OpenAI, OpenRouter, Anthropic, OpenAI-compatible endpoints, and local Ollama support.
- For local Ollama routes, perform bounded local detection with `which ollama` only.

## Guardrails
- Do not validate or expose raw provider secrets here.
- Do not claim provider/model availability without a bounded validation command or adapter result.
- Stay in routing and readiness checks only; do not rewrite onboarding or global provider config in this stage.
- If a provider path is unsupported or ambiguous, escalate with one concise next action.
- Persist only concise provider routing summaries, never tokens or full transcripts.
