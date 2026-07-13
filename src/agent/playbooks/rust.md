# Rust Specialist Playbook

## Allowed Setup Paths
- `Cargo.toml`: verify `cargo --version`, then run `cargo fetch`, then `cargo build --release`.
- `rust-toolchain.toml` or `rust-toolchain` file: run `rustup show active-toolchain` so the pinned channel is surfaced before build.
- Cargo workspace (top-level `Cargo.toml` with `[workspace]` and member crates): the same three steps apply at the workspace root.
- `Dockerfile` without `Cargo.toml`: build a local image with `docker build -t <repo-slug> .`.
- `Makefile`: run only a known bounded target (`setup`, `install`, `init`, or `bootstrap`).

## Run Verification
- Prefer the binary at `target/release/<bin-name>` (parsed from `Cargo.toml [[bin]]` or `name = …`) for the run step.
- Fall back to `cargo run --release` only when no built binary is available.
- Do not run `cargo install` against the user's global cargo prefix without explicit approval.

## Multi-service Handling
- If the crate also has a compose file, verify the compose definition is present but do not auto-run `docker compose up`.

## Guardrails
- Never modify `Cargo.toml` or `Cargo.lock` automatically.
- Never invoke `rustup install <toolchain>` without explicit approval — surface the pinned channel instead and ask.
- Preserve HITL/HOTL/HOOTLWO approval rules. `cargo build --release` and `cargo fetch` are S1 (non-destructive env setup). `cargo run` is S1 only when the README documents it as the run command; otherwise S2 (ask).

## Cross-platform Notes
- macOS / Linux: `rustup` is the canonical installer; `brew install rustup-init` on macOS, `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh` on Linux when explicitly approved.
- Windows: defer to MSVC + `rustup-init.exe`; do not auto-bootstrap.
- Apple Silicon: `aarch64-apple-darwin` is the default target; surface `cargo build --target` mismatches as a setup blocker rather than working around them.
