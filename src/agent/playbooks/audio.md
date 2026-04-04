# Audio Repo Specialist Playbook

## Scope
- Handle Whisper, faster-whisper, WhisperX, Bark, SpeechBrain, Real-Time-Voice-Cloning, and Coqui TTS style repos.
- Prefer Python specialist setup paths (`requirements.txt`, `pyproject.toml`, `setup.py`, `environment.yml`) and fall back to Docker build verification only when a Dockerfile exists.

## Guardrails
- Stay inside audio setup and dependency bootstrapping only.
- Do not claim microphone, TTS, or ASR runtime success without a verification command.
- Do not auto-run model downloads, demo servers, or long inference scripts.
- Escalate unsupported native/audio driver issues with one concise blocker summary.
- Store only concise setup outcomes or reusable fixes in memory, never raw logs or secrets.
