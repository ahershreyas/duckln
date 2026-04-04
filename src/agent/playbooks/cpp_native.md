# C++ / Native Runtime Specialist Playbook

## Allowed Setup Paths
- `CMakeLists.txt`: run `cmake -S . -B build`, then `cmake --build build`.
- `Cargo.toml`: run `cargo build`.
- `configure`: run `./configure`, then a bounded Makefile setup target if one exists.
- `Makefile`: run only a known bounded target (`setup`, `install`, `init`, or `bootstrap`).

## Verification
- Verify `build/` exists after CMake configure/build.
- Verify `target/` exists after Cargo build.
- Verify `Makefile` exists before and after configure/Makefile steps.

## Guardrails
- Do not infer `sudo`, package-manager installs, or destructive rebuild commands.
- Do not auto-run long benchmark or model-download commands.
