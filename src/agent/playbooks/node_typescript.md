# Node / TypeScript Specialist Playbook

## Allowed Setup Paths
- `pnpm-lock.yaml`: run `pnpm install --frozen-lockfile`.
- `yarn.lock`: run `yarn install --frozen-lockfile`.
- `package-lock.json`: run `npm ci`.
- `package.json` without a lockfile: run `npm install`.
- `Dockerfile` without a package manifest: build a local image with `docker build -t <repo-slug> .`.
- `Makefile`: run only a known bounded target (`setup`, `install`, `init`, or `bootstrap`).

## Multi-service Handling
- If a compose file exists, verify the compose definition file is present.
- Do not auto-run `docker compose up` in this stage.

## Verification
- Verify the relevant lockfile or `package.json` exists after dependency install.
- Verify `Dockerfile` exists after Docker build planning.

## Desktop apps (Tauri / Electron) — Plan 87
- Detect via `@tauri-apps/*`/`tauri` dep, `tauri.conf.json`/`src-tauri`, or an `electron` dep/script. These are DESKTOP apps with two halves: a web frontend (Vite) and a native shell (where Tauri/Electron APIs live). The frontend-only `dev` script is NOT the app — run the native shell (`tauri dev` / `electron .`).
- A desktop app opened as a plain web URL fails with `window.__TAURI_INTERNALS__ is undefined`. Never surface the frontend URL as "the app".
- On a remote headless target (VM/cloud/Docker): run the real app under a virtual display (`Xvfb :99`) with the webview libs (Tauri → `libwebkit2gtk`/`libgtk-3`; Electron → `libnss3`/`libgbm1`/…) and stream it to the user's local browser via `x11vnc` + `noVNC` (port 6080), opening the host-reachable `vnc.html` URL. On local: launch the native window directly. Streaming-stack installs are bounded, idempotent, and approved.
- **Tauri's backend is Rust (Plan 88).** Before `tauri dev`, install the FULL Tauri prerequisites — `build-essential`, `libssl-dev`, `libwebkit2gtk-4.1-dev` (fall back to `4.0`), `librsvg2-dev`, `libayatana-appindicator3-dev`, `patchelf` — AND the Rust toolchain (rustup) AND the Tauri CLI, or the bundled backend never compiles (the `__TAURI_INTERNALS__ undefined` symptom). Launch with `$HOME/.cargo/bin` on PATH (a `PATH=` prefix, never a `;`/`. source` prefix — that breaks the detached `nohup`/`$!` capture) and `WEBKIT_DISABLE_COMPOSITING_MODE=1`/`WEBKIT_DISABLE_DMABUF_RENDERER=1` so the webview renders under Xvfb. The first Rust compile can take minutes — allow for it, don't false-fail. On a VM/private repo, the pre-check probe detects `src-tauri`/`tauri.conf.json` so the app is classified desktop even with no host clone.
- **Heavy Rust builds OOM on small VMs (Plan 89).** Compiling Tauri/GTK crates can exceed a small VM's RAM → the kernel SIGKILLs the compiler (`could not compile … (lib)` / `signal: 9`). This is `OUT_OF_MEMORY`, a deterministic resource fix (add swap + `~/.cargo/config.toml jobs=1`), not a web-searchable bug. Pre-empt it: when the pre-check shows < ~6 GB RAM and no swap, add swap + a single-threaded build BEFORE `tauri dev`. Route desktop apps through Xvfb+noVNC whenever there's no `$DISPLAY` (even a "local" headless VM). Guard re-runnable steps (`[ -d node_modules ] || npm ci`, `[ -f .env ] || cp …`, `command -v <tool> ||`) so a re-run doesn't redo finished work.

## Guardrails
- Do not infer service start commands, destructive cleanup, or global npm installs.
- Preserve HITL/HOTL/HOOTLWO approval rules.
