# Generates the three service Dockerfiles with deterministic lockfile installs.
from pathlib import Path

TEMPLATE = """FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \\
    PIP_NO_CACHE_DIR=1 \\
    UV_SYSTEM_PYTHON=1

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src/ ./src/

# Deterministic deps: export from committed lockfile, install app without deps.
RUN {extra_flags} -o /tmp/requirements.txt \\
    && uv pip install --system --no-cache -r /tmp/requirements.txt \\
    && uv pip install --system --no-cache --no-deps .
"""

SERVICES = {
    "controlplane": ("azure api mcp", 'EXPOSE 8000\nCMD ["uvicorn", "controlplane:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]\n'),
    "mcpserver": ("azure api mcp", 'EXPOSE 8000\nCMD ["uvicorn", "mcpserver:create_mcp_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]\n'),
    "worker": ("azure", 'CMD ["python", "-m", "worker.runner"]\n'),
}

root = Path(__file__).resolve().parent.parent / "docker"
for svc, (extras, tail) in SERVICES.items():
    flags = " ".join(f"--extra {e}" for e in extras.split())
    (root / f"{svc}.Dockerfile").write_text(
        TEMPLATE.format(extras=extras, extra_flags=f"uv export --frozen --no-hashes --no-emit-project {flags}"),
        encoding="utf8",
    )
    print(f"wrote docker/{svc}.Dockerfile")
