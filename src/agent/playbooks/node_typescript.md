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

## Guardrails
- Do not infer service start commands, destructive cleanup, or global npm installs.
- Preserve HITL/HOTL/HOOTLWO approval rules.
