# Diffusion-Heavy Repo Specialist Playbook

## Scope
- Handle Stable Diffusion, stable-diffusion-webui, ComfyUI, diffusers, and InvokeAI style repos.
- Prefer bounded Python install paths first; use Dockerfile build verification when that is the smallest reversible path.

## Guardrails
- Never promise CUDA/MPS readiness without checking environment signals and a verification command.
- On Apple Silicon, avoid CUDA setup advice and prefer CPU/MPS-safe setup notes.
- Do not auto-run model checkpoint downloads, web UI startup, or GPU benchmark commands.
- Escalate unsupported low-VRAM or backend mismatch cases instead of guessing.
- Keep persisted memory concise and high-signal only.
