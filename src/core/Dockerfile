# Test runner for lila-core. Built from the repo root so the uv workspace resolves:
#   docker build -f src/core/Dockerfile .
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Workspace members must be present for `uv sync` to resolve the lockfile.
COPY pyproject.toml uv.lock ./
COPY src/core/pyproject.toml src/core/
COPY src/core/src/lila/__init__.py src/core/src/lila/
COPY experiments/loop_workflow_comparison/pyproject.toml experiments/loop_workflow_comparison/

RUN uv sync --package lila-core --group dev --frozen --no-install-project

COPY src/core/ src/core/

RUN uv sync --package lila-core --group dev --frozen

WORKDIR /app/src/core

# Unit tests by default; the integ target overrides this.
CMD ["uv", "run", "--no-sync", "pytest", "tst", "-q"]
