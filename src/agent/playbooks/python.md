# Python Repo Specialist Playbook

## Allowed Setup Paths
- `requirements.txt`: create `.venv`, then install with `.venv/bin/python -m pip install -r requirements.txt`.
- `pyproject.toml` or `setup.py`: create `.venv`, then install with `.venv/bin/python -m pip install -e .`.
- `environment.yml`: use `conda env create -f environment.yml`.
- `Dockerfile` on diffusion or multi-service repos: build a local image with `docker build -t <repo-slug> .`.
- `Makefile`: run only a known bounded target (`setup`, `install`, `init`, or `bootstrap`).

## Verification
- Verify `.venv/bin/python` exists after venv creation.
- Verify project pip is available after pip install.
- Verify declared setup files still exist for conda, Docker, or Makefile steps.

## Guardrails
- Do not auto-run service launch, destructive cleanup, or broad shell scripts.
- Keep suggestions to the smallest safe setup path.
