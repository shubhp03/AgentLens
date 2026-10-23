# One image for all Python services; compose `command` selects the entrypoint.
# TODO(OPS-06): pin the uv image tag, commit uv.lock, build with --frozen.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock* ./
COPY agent agent
COPY tools tools
COPY evals evals
COPY gateway gateway
RUN uv sync --all-packages --no-dev
ENV PATH="/app/.venv/bin:$PATH"
