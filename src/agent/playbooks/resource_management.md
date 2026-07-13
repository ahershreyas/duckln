# Resource management — disk, RAM, CPU, GPU/CUDA, NPU, Apple MLX/Metal

Procedural knowledge (a SKILL) for reasoning about a resource shortfall on a target. It is
loaded by an agent to DECIDE; the actual actions (probe / reclaim / resize / route) are the
deterministic tools/floor, executed under user approval. Never fake "ready" — route or stop
honestly when a resource genuinely can't be provided.

Core truth: **reclaim cannot fit a workload whose footprint exceeds the disk.** A build
REGENERATES what you delete (`target/`, `dist/`, `build/`, `node_modules`); a download
RE-FETCHES a deleted cache. So when the failing step regenerates the reclaimable hogs, the
fix is a right-sized disk (or a smaller workload), not another reclaim. Size from the REAL
measured footprint (`du` of the build dirs), not a flat guess.

## Disk
- Signals: `ENOSPC` / "no space left on device", `df` ≥ ~90% used, the measured reclaimed/footprint size.
- Reason: if the failing step is a BUILD that regenerates `target/`/`dist/`, deleting them then
  rebuilding is circular on a too-small disk — keep the cache, resize.
- Options (ranked): safe cache reclaim (npm/pip/cargo-registry, stale scratch) → **right-sized
  resize** (grow to `disk_total + regenerated_footprint + headroom`, user-approved) → recreate
  the VM larger (`/vm`) → lighten (use a prebuilt binary, skip an optional artifact) → aggressive
  delete of `target/` only as a last resort if resize is impossible.
- AI/ML: model weights / datasets / CUDA wheels are multi-GB; reclaiming a partial HF/pip cache
  forces a re-download → prefer resize; or use a smaller model / streaming dataset.

## RAM
- Signals: OOM, "signal 9" / SIGKILL during compile, "cannot allocate memory", `MemoryError`.
- Options: add swap + cap the build to a single job (small builds) → grow RAM / a bigger target.
- AI/ML: batch size and model size drive RAM — reduce the batch or use a smaller model before resizing.

## CPU
- Signals: a very slow native build, few cores.
- Options: cap build parallelism to the core count (never oversubscribe a small VM) → a bigger
  target for a heavy compile. CPU is rarely a hard failure — it's a speed/right-sizing note.

## GPU (CUDA)
- Signals: `nvidia-smi`, `torch.cuda.is_available() == False`, "no CUDA-capable device", deps like
  `bitsandbytes` / `flash-attn` / `vllm` / `deepspeed` / `auto-gptq`, "requires CUDA" in the README.
- Options: install the framework wheel MATCHING the sensed accelerator (CUDA build vs CPU build);
  if the repo NEEDS a GPU and the target has none → route to a GPU VM / cloud (don't install a
  doomed CPU wheel); VRAM too small for the model → honest note + a bigger GPU or a smaller/quantized model.

## NPU
- NPUs (Apple Neural Engine, etc.) are not directly provisioned. On a Mac, CoreML uses the ANE
  automatically. Treat as an honest NOTE / route — never claim "NPU ready".

## Apple MLX / Metal (MacBook)
- Metal / MLX run ONLY on the native macOS host — Apple's GPU is NOT passed into a Linux
  VM/container. A repo declaring `mlx` / `mlx-lm` / `mlx-vlm` / `jax-metal` / `tensorflow-metal`
  (or torch-on-MPS) must run on the **`local`** target, with `pip install mlx` (or the metal wheel).
- If an MLX/Metal repo is aimed at a Linux VM/cloud → warn and recommend `/target local`. The same
  native-only rule applies to MPS.

## Honest limits
- A host that genuinely can't grow (host disk/RAM capped) → honest stop with the real numbers.
- A GPU-required repo on a no-GPU host → honest route, never a fake GPU.
- Resizing changes the user's machine / can cost → always user-approved, never automatic.

## Examples (follow this SHAPE; vary the wording — do NOT copy verbatim)

### Example 1 — disk full during a heavy build
Input: a Tauri/Rust `build:all` hit `No space left on device` on a 16 GB VM that was 100% full;
reclaim freed 7.4 GB of `target/`, but the build regenerates `target/`.
Correct recommendation:
> Disk is the blocker: the 16 GB VM filled up and this build regenerates ~7.4 GB of artifacts, so
> reclaiming isn't enough. Best approach: grow the disk to ~24 GB (or recreate the VM larger) — a
> bigger disk is the only thing that fits this build. Resize now (**yes**), keep the current size
> (**no**), or tell me a size.

### Example 2 — out of memory
Input: `cargo build` killed with `signal: 9`; 2 GB RAM, no swap.
Correct recommendation:
> RAM is the blocker: the compile was OOM-killed on a 2 GB VM with no swap. Best approach: add swap
> and build single-threaded so it fits, or grow the VM to ≥4 GB. Add swap and retry (**yes**), stop
> (**no**), or tell me what you'd prefer.
