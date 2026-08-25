FROM python:3.14-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_SYSTEM_PYTHON=1

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml ./
COPY src/ ./src/

RUN uv pip install --system --no-cache ".[azure,api]"

EXPOSE 8000
CMD ["uvicorn", "controlplane:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
