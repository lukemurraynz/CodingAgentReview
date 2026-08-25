FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_SYSTEM_PYTHON=1

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src/ ./src/

# Deterministic deps: export from committed lockfile, install app without deps.
RUN uv export --frozen --no-hashes --no-emit-project --extra azure --extra api --extra mcp -o /tmp/requirements.txt \
    && uv pip install --system --no-cache -r /tmp/requirements.txt \
    && uv pip install --system --no-cache --no-deps .

EXPOSE 8000
CMD ["uvicorn", "controlplane:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
