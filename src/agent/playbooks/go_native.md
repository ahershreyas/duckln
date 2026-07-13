# Go Repo Specialist Playbook

## Allowed Setup Paths
- `go.mod`: resolve module dependencies with `go mod download`.
- `go.mod` or `main.go`: verify the bounded package graph with `go list ./...`.
- `Makefile`: run only a known bounded target (`setup`, `install`, `init`, or `bootstrap`) when no clearer Go-native path exists.

## Verification
- Verify `go.mod` exists before module resolution.
- Verify `go list ./...` succeeds after the setup path.

## Guardrails
- Do not auto-run destructive cleanup or arbitrary shell scripts.
- Do not claim a long-running Go service is ready unless Duckln has a real start command.
- Keep the setup path small, reversible, and verifiable.
