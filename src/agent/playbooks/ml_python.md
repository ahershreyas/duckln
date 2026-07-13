# Playbook: Python AI/ML repos (framework-agnostic; incl. macOS / Apple Silicon)

Scope: ANY ML stack the repo declares — PyTorch, **Apple MLX**, TensorFlow,
JAX, transformers/diffusers, Core ML, ONNX Runtime, gradio/streamlit, training/
inference. Duckln installs the repo's OWN declared framework; it does NOT assume torch.

## Framework-agnostic install
- Install what the manifest declares (pip/uv/poetry/conda) into a venv. MLX, jax-metal,
  coremltools, onnxruntime pip-install cleanly on Apple Silicon — no special handling.
- **MPS ≠ torch:** MPS is Apple's native GPU framework; torch is one consumer. A repo may
  reach the Apple GPU via torch, MLX, or tensorflow-metal — set up whichever it uses.

## Apple MLX / Metal is NATIVE-macOS-ONLY (Plan 154)
- **MLX (and `mlx-lm`/`mlx-vlm`, `jax-metal`, `tensorflow-metal`) only work on the LOCAL
  macOS host.** Apple's GPU is NOT passed into a Linux guest — Apple's `container`/multipass/
  Docker/cloud all run Linux, so Metal/MLX there fails or falls back to CPU. The only place
  MLX uses the Apple GPU is a native macOS process (Duckln's `local` target).
- On `local` Apple Silicon: surface the accelerator as **"Apple Metal (MLX)"** and install
  via `pip install mlx` (+ declared `mlx-lm`/`mlx-vlm`).
- On a `vm`/`docker`/`aws`/`gcp` target: WARN + recommend `local` (`/target local`) — soft,
  not a hard block (some MLX repos have a CPU fallback). Symmetric to CUDA-needed→`/cloud`.

## Hardware-aware setup
- Read the probe: CUDA (Linux+NVIDIA), MPS (Apple Silicon), or CPU. NEVER promise GPU
  readiness without a verification command. On a remote target the resource probe senses
  the GPU directly (`nvidia-smi` → name / VRAM / CUDA version); local Apple Silicon = MPS.
- **PROACTIVE accelerator-matched install (don't wait for a failure).** When the repo
  declares a DL framework, install the build that MATCHES the detected accelerator at plan
  time — `framework_install_command(framework, accelerator, cuda_version)`:
  - CUDA target → the CUDA wheel: torch `--index-url …/whl/cu<NNN>` (nearest tag ≤ driver),
    `jax[cuda12]`, `tensorflow[and-cuda]`.
  - CPU target (Linux VM, no GPU) → the CPU wheel: torch `…/whl/cpu`, plain jax/tensorflow.
  - Apple Silicon → default torch (MPS), `tensorflow-macos`+`tensorflow-metal`, `jax-metal`.
  The repo's pinned VERSION is preserved; only the build/index is steered to the accelerator.
- **Honest GPU-need routing.** A GPU-required repo (`bitsandbytes`/`flash-attn`/`vllm`/
  diffusion family / "requires CUDA" in README) on a CPU-only target → recommend a GPU
  VM/cloud (`/cloud`) or honest-block; never silently install a doomed CPU build.
- **macOS has no CUDA.** Framework-specific recovery (the reactive backstop):
  - torch pinning a CUDA wheel (`torch==X+cuYYY`) → install the default CPU/MPS wheel
    (`TORCH_WHEEL_MISMATCH`).
  - plain `tensorflow` (no arm64 wheel) → `tensorflow-macos` (+ `tensorflow-metal`)
    (`TF_MACOS_REQUIRED`).
  - a repo that HARD-requires CUDA (`torch.cuda.is_available()` assert / "no CUDA-capable
    device") → STOP honestly (`GPU_UNAVAILABLE`): use CPU/MPS if supported, else a Linux
    GPU VM/cloud. The honest stop is a correct outcome, not a failure.

## Conda / mamba environments
- A repo shipping `environment.yml`/`environment.yaml` (or "conda"/"mamba" in the README)
  is a CONDA repo — set it up via conda/mamba, NOT pip-venv. Install Miniforge (mamba) if
  absent, then `mamba env create -f environment.yml` (fallback `conda env create`), reading
  the env `name:` from the file. Idempotent: skip if the env already exists (`conda env list`).
- Run/verify a conda repo THROUGH the env: `conda run -n <env> <cmd>` — not the venv path.
- A repo with BOTH `requirements.txt` and `environment.yml` → prefer conda; pip-install the
  extra requirements INSIDE the conda env. Non-conda repos keep the pip-venv default.

## Dependencies
- Prefer the repo's declared manager (pip/uv/poetry/conda); set up a venv first.
- A missing build/runtime module surfaces as "No module named X" (incl. the unquoted
  `python -m X` form) → install X into the SAME venv (`<venv>/bin/pip install X`).
- macOS toolchains install via Homebrew; if `brew` is missing, ask the user to install
  it (the official installer needs their password — Duckln can't do it unattended).

## Models & data
- Gated HuggingFace models need a token → ask for `HF_TOKEN` (don't hang on the prompt).
- Large weight downloads can fill disk (`~/.cache/huggingface`, `~/.cache/torch`) — the
  proactive disk-crunch check applies; never silently retry an ENOSPC.

## Running
- gradio→7860, streamlit→8501, jupyter→8888, uvicorn/gunicorn→served port: await the banner.
- A one-shot training/inference/CLI script is NOT a server — run it bounded and judge by
  exit code + logs, not a URL. Never auto-run an unbounded training job without the user.
