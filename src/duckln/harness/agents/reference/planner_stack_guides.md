# Planner stack reference — just-in-time detail (NOT an agent spec)

The planner loads ONLY the section(s) matching the detected stack (appended to its user
message), keeping the always-loaded planner.md prompt lean (Plan 159 F3).

<!-- ref:django -->
### Example 2 — Python / Django Web App

**Inspector state**
```yaml
repo.detected_files:
  - requirements.txt
  - manage.py
  - myapp/settings/base.py
  - myapp/settings/local.py.example
  - myapp/models.py
  - myapp/views.py
  - myapp/urls.py
  - tests/test_models.py
  - .python-version          # contains "3.11.7"
repo.runtimes:       ["python@3.11.7"]
repo.os:             "linux"
repo.readme_excerpt: "Requires PostgreSQL 15. Set DJANGO_SETTINGS_MODULE to myapp.settings.local."
repo.prior_failures: ["pip install -r requirements.txt"]
repo.package_manager: "pip"
repo.entry_point:    "manage.py"
repo.env_vars_present: ["VIRTUAL_ENV", "DJANGO_SETTINGS_MODULE"]
```

**User objective:** "Run the Django dev server"

**Correct planner output**
```json
[
  {
    "title": "Upgrade pip before retrying install",
    "command": "pip install --upgrade pip",
    "confidence": 0.90,
    "estimated_seconds": 8
  },
  {
    "title": "Install Python dependencies (retry with no-cache)",
    "command": "pip install --no-cache-dir -r requirements.txt",
    "confidence": 0.65,
    "estimated_seconds": 60,
    "clarification_needed": "Previous `pip install -r requirements.txt` failed. Retrying with --no-cache-dir. If this also fails, share the error — a compiled extension may need system build tools."
  },
  {
    "title": "Copy local settings file from example",
    "command": "cp myapp/settings/local.py.example myapp/settings/local.py",
    "confidence": 0.92,
    "estimated_seconds": 2
  },
  {
    "title": "Verify PostgreSQL connection",
    "command": "python manage.py dbshell --command='\\conninfo'",
    "confidence": 0.70,
    "estimated_seconds": 5
  },
  {
    "title": "Apply database migrations",
    "command": "python manage.py migrate",
    "confidence": 0.88,
    "estimated_seconds": 20
  },
  {
    "title": "Create Django superuser",
    "command": "python manage.py createsuperuser",
    "confidence": 0.80,
    "estimated_seconds": 30
  },
  {
    "title": "Start Django development server",
    "command": "python manage.py runserver",
    "confidence": 0.91,
    "estimated_seconds": 5
  }
]
```

**Why these scores:**
- Install retry is 0.65 — the original failed; `--no-cache-dir` is the most common fix but not guaranteed.
- `dbshell` is 0.70 — README mentions PostgreSQL 15, but `DATABASE_URL` is not in `repo.env_vars_present`, so the connection might fail.
- `migrate` is 0.88 not 0.97 — depends on the DB connection succeeding first.

---

<!-- ref:monorepo -->
### Example 4 — Monorepo (pnpm Workspaces + Turborepo)

Monorepos contain multiple apps and packages in one repo. The planner must
**never** propose installing or building each workspace package individually
— the workspace tooling handles hoisting and ordering. The key failure mode
is running app-level commands before the workspace root is bootstrapped.

**Inspector state**
```yaml
repo.detected_files:
  - package.json                      # contains: "workspaces": ["apps/*","packages/*"]
  - pnpm-lock.yaml
  - turbo.json                        # pipeline: build -> test -> lint
  - apps/web/package.json             # next@14.2.0, react, react-dom
  - apps/web/next.config.js
  - apps/api/package.json             # express@4, prisma@5.13.0, zod
  - apps/api/prisma/schema.prisma
  - apps/api/prisma/migrations/       # directory with migration files
  - packages/ui/package.json          # shared component library
  - packages/config/package.json      # shared tsconfig, eslint config
  - packages/db/package.json          # shared Prisma client wrapper
  - .env.example                      # DATABASE_URL, NEXTAUTH_SECRET, NEXTAUTH_URL
  - README.md
repo.runtimes:       ["node@20.14.0"]
repo.os:             "linux"
repo.readme_excerpt: >
  "Install with `pnpm install` at root. Build all packages with
  `pnpm turbo build`. Start dev servers with `pnpm turbo dev`."
repo.prior_failures: []
repo.package_manager: "pnpm"
repo.entry_point:    null
repo.env_vars_present: ["NODE_ENV"]
```

**User objective:** "Get the full monorepo running in development mode"

**Correct planner output**
```json
[
  {
    "title": "Install all workspace dependencies at root",
    "command": "pnpm install",
    "confidence": 0.97,
    "estimated_seconds": 45
  },
  {
    "title": "Create local env file from example",
    "command": "cp .env.example .env",
    "confidence": 0.95,
    "estimated_seconds": 2
  },
  {
    "title": "Confirm required env vars are populated",
    "command": null,
    "confidence": 1.0,
    "estimated_seconds": 10,
    "clarification_needed": "DATABASE_URL, NEXTAUTH_SECRET, and NEXTAUTH_URL are required but not set. Please provide values — DATABASE_URL must point to a running Postgres instance."
  },
  {
    "title": "Build all shared packages before apps",
    "command": "pnpm turbo build --filter=./packages/*",
    "confidence": 0.88,
    "estimated_seconds": 60
  },
  {
    "title": "Generate Prisma client from schema",
    "command": "pnpm --filter=@repo/api prisma generate",
    "confidence": 0.91,
    "estimated_seconds": 10
  },
  {
    "title": "Run Prisma database migrations",
    "command": "pnpm --filter=@repo/api prisma migrate deploy",
    "confidence": 0.75,
    "estimated_seconds": 15
  },
  {
    "title": "Start all dev servers via Turborepo",
    "command": "pnpm turbo dev",
    "confidence": 0.90,
    "estimated_seconds": 8
  }
]
```

**Key monorepo-specific reasoning:**

- **Root install is the only install.** `pnpm install` at the workspace root
  handles all packages via hoisting. Never propose `cd apps/web && pnpm install`
  as a separate step — that defeats workspace deduplication.
- **Packages before apps.** `turbo build --filter=./packages/*` ensures the
  shared UI/config/db packages are compiled before `apps/web` or `apps/api`
  try to import them. Skipping this causes "module not found" errors at
  dev-server startup.
- **Use `--filter` for app-specific commands.** Prisma CLI lives in `apps/api`,
  not at the root. `pnpm --filter=@repo/api prisma generate` runs it in the
  right workspace without changing directories.
- **`turbo dev` last.** Turborepo's pipeline parallelises the dev servers and
  respects the dependency graph declared in `turbo.json`. Do not propose
  `cd apps/web && next dev` manually — it misses the API server.
- **`entry_point` is null** — expected for monorepos. Do not lower confidence
  across the board; instead, use `turbo.json` pipeline keys as the signal for
  what commands are available.

---

<!-- ref:docker -->
### Example 5 — Docker-First Repo (no local runtime required)

Some repos are designed to run entirely inside containers — there is no
local Python/Node runtime to manage and no `requirements.txt` to install
into the host. The planner must detect this and **never** propose bare
`pip install` or `npm install` on the host. All work happens via
`docker compose`.

**Inspector state**
```yaml
repo.detected_files:
  - docker-compose.yml                # services: web, worker, db, redis, nginx
  - docker-compose.override.yml.example
  - Dockerfile                        # FROM python:3.12-slim; COPY . /app; RUN pip install ...
  - Dockerfile.worker
  - nginx/nginx.conf
  - .env.example                      # POSTGRES_PASSWORD, SECRET_KEY, REDIS_URL, DEBUG
  - Makefile                          # targets: up, down, logs, migrate, shell, test
  - README.md
  - src/manage.py                     # Django entry point inside container
  - src/requirements.txt              # only used inside Docker build — NOT for host install
repo.runtimes:       ["docker@26.1.0", "docker-compose@2.27.0"]
repo.os:             "linux"
repo.readme_excerpt: >
  "This project runs entirely in Docker. Do NOT install Python locally.
  Copy .env.example to .env, then run `make up` to start all services.
  Run `make migrate` to apply DB migrations inside the container."
repo.prior_failures: []
repo.package_manager: null
repo.entry_point:    null
repo.env_vars_present: ["USER", "HOME"]
```

**User objective:** "Get the app running locally"

**Correct planner output**
```json
[
  {
    "title": "Verify Docker daemon is running",
    "command": "docker info",
    "confidence": 0.97,
    "estimated_seconds": 3
  },
  {
    "title": "Create local env file from example",
    "command": "cp .env.example.example .env",
    "confidence": 0.92,
    "estimated_seconds": 2
  },
  {
    "title": "Confirm required env vars are populated",
    "command": null,
    "confidence": 1.0,
    "estimated_seconds": 10,
    "clarification_needed": "POSTGRES_PASSWORD and SECRET_KEY are required in .env but have no defaults. Please set them before building."
  },
  {
    "title": "Inspect Makefile targets before running",
    "command": "cat Makefile",
    "confidence": 0.97,
    "estimated_seconds": 2
  },
  {
    "title": "Build all Docker images",
    "command": "docker compose build",
    "confidence": 0.93,
    "estimated_seconds": 180
  },
  {
    "title": "Start all services in detached mode",
    "command": "docker compose up -d",
    "confidence": 0.93,
    "estimated_seconds": 20
  },
  {
    "title": "Run database migrations inside container",
    "command": "docker compose exec web python manage.py migrate",
    "confidence": 0.85,
    "estimated_seconds": 15
  },
  {
    "title": "Tail logs to verify all services started",
    "command": "docker compose logs --tail=50 --follow",
    "confidence": 0.95,
    "estimated_seconds": 10
  }
]
```

**Key Docker-first reasoning:**

- **Never propose host-level `pip install`.** `requirements.txt` is present
  but is consumed by the `Dockerfile` build, not the host. Proposing
  `pip install -r requirements.txt` on the host is both wrong and potentially
  pollutes the system Python. The README's explicit warning is the signal —
  but also infer this any time `repo.runtimes` lists only `docker` and no
  language runtime.
- **`docker info` first.** If the daemon is not running, nothing else works.
  Costs 3 seconds and saves the confusion of "docker: command not found" or
  "cannot connect to Docker daemon" on a step much later.
- **Read the Makefile before using it.** `make up` is mentioned in the README
  but `cat Makefile` is proposed first (confidence 0.97) to confirm the
  target exists and see if it wraps additional logic. A Makefile target named
  `up` might do `docker compose build` + `up` together — if so, merge those
  two steps.
- **Migrations via `docker compose exec`.** The app runs inside the container,
  so Django management commands run there too: `docker compose exec web python
  manage.py migrate`, not `python manage.py migrate` on the host.
- **`entry_point` is null and `package_manager` is null** — expected. Do not
  flag these as missing. When `repo.runtimes` includes only `docker`, null
  values for runtime-specific fields are correct, not gaps.
- **`docker compose up -d` then logs** — always propose following logs after
  starting services in detached mode. Silent failures (missing env var,
  port collision, migration error) are only visible in logs.


---

<!-- ref:inference-table -->
### Package → Command inference table

When `repo.detected_files` contains these files or `requirements.txt` /
`package.json` lists these packages, infer the corresponding idioms:

| Detected file / package | Inferred stack | Key commands to consider |
|---|---|---|
| `package-lock.json` | Node + npm | `npm install`, `npm run build`, `npm test`, `npm start` |
| `yarn.lock` | Node + Yarn | `yarn install`, `yarn build`, `yarn test`, `yarn start` |
| `pnpm-lock.yaml` | Node + pnpm | `pnpm install`, `pnpm build`, `pnpm test` |
| `Pipfile.lock` | Python + pipenv | `pipenv install`, `pipenv run …` |
| `poetry.lock` | Python + Poetry | `poetry install`, `poetry run …`, `poetry build` |
| `pyproject.toml` (no lock) | Python (modern) | `pip install -e .` or `pip install .` |
| `requirements.txt` | Python + pip | `pip install -r requirements.txt` |
| `Cargo.lock` | Rust | `cargo build --release`, `cargo test`, `cargo run` |
| `go.sum` | Go | `go mod download`, `go build ./…`, `go test ./…` |
| `Gemfile.lock` | Ruby | `bundle install`, `rails server`, `rake db:migrate` |
| `composer.lock` | PHP | `composer install`, `php artisan migrate`, `php artisan serve` |
| `build.gradle` / `pom.xml` | Java/Kotlin | `./gradlew build`, `mvn package`, `java -jar target/…` |
| `Makefile` | Any | `make`, `make install`, `make test` — read targets first with `cat Makefile` |
| `Brewfile` | macOS + Homebrew | `brew bundle install` — installs all declared formulae and casks |
| `Brewfile.lock.json` | macOS + Homebrew | confirms exact versions locked; pair with `brew bundle install --no-upgrade` |
| `.nvmrc` / `.node-version` | Node version pin | `nvm use` (macOS/Linux) or `fnm use` — always do this before `npm install` |
| `.python-version` | pyenv version pin | `pyenv install` then `pyenv local` — do this before `pip install` |
| `.tool-versions` | asdf multi-runtime | `asdf install` — installs all runtimes declared in file |
| `Dockerfile` | Container | `docker build -t name .`, `docker run …` |
| `docker-compose.yml` / `compose.yaml` | Multi-container | `docker compose up -d`, `docker compose logs`, `docker compose down` |
| `torch` in requirements | PyTorch / ML | verify CUDA, then train/eval scripts |
| `tensorflow` in requirements | TF / ML | verify GPU with `tf.config.list_physical_devices('GPU')` |
| `transformers` in requirements | HuggingFace | check `HF_TOKEN`, model download step required |
| `accelerate` in requirements | Multi-GPU / HF | `accelerate config` before training |
| `wandb` in requirements | Experiment tracking | `WANDB_API_KEY` env var required; add `wandb login` step |
| `alembic` in requirements | SQL migrations | `alembic upgrade head` (not `python manage.py migrate`) |
| `prisma` in package.json | Prisma ORM | `npx prisma migrate deploy`, `npx prisma generate` |
| `jest` in package.json | JS test runner | `npm test` or `npx jest` |
| `pytest` in requirements | Python tests | `pytest` or `python -m pytest` |
| `uvicorn` in requirements | FastAPI / ASGI | `uvicorn main:app --reload` |
| `gunicorn` in requirements | Production WSGI | `gunicorn app:app -w 4` |
| `celery` in requirements | Task queue | `celery -A app worker -l info` (separate process) |
| `.env.example` (any stack) | Env config needed | `cp .env.example .env` + surface missing vars |

---
