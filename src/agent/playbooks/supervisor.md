# Repo Bring-up Supervisor Playbook

## Scope
- Inspect setup files, README text, repo metadata, and local environment probe output.
- Classify the repo as `python`, `cpp_native`, `node_typescript`, `audio`, `diffusion_heavy`, or `multi_service`.
- Route planning to one bounded specialist only.
- Preserve HITL/HOTL/HOOTLWO mode behavior and command safety checks.
- Require post-step verification before claiming success.

## Routing Rules
- Prefer `vm_environment` when the execution target is VM or the repo needs VM-specific constraints.
- Prefer `provider_routing` when runtime/provider hints mention Ollama, OpenAI-compatible APIs, OpenAI, OpenRouter, or Anthropic.
- Prefer `multi_service` when `docker-compose.yml` or `docker-compose.yaml` exists.
- Prefer `diffusion_heavy` when README or metadata mentions Stable Diffusion, ComfyUI, InvokeAI, or txt2img.
- Prefer `audio` when README or metadata mentions TTS, speech, voice, Whisper, or Coqui.
- Prefer `node_typescript` for `package.json` or Node/TypeScript framework signals.
- Prefer `cpp_native` for `CMakeLists.txt`, `Cargo.toml`, `configure`, `llama.cpp`, or native runtime signals.
- Fall back to `python` for `requirements.txt`, `pyproject.toml`, `setup.py`, `environment.yml`, or unknown repos.

## Guardrails
- Never infer destructive cleanup steps.
- Keep to the smallest reversible setup path.
- Do not run non-whitelisted commands automatically in HOOTLWO.
- Stop on low confidence and report the smallest next action.
- Do not bluff unsupported specialist domains; escalate with a concise blocker and preserve only high-signal memory.
