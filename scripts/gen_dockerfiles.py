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
RUN uv export --frozen --no-hashes --no-emit-project {extra_flags} -o /tmp/requirements.txt \\
    && uv pip install --system --no-cache -r /tmp/requirements.txt \\
    && uv pip install --system --no-cache --no-deps .
"""

SERVICES = {
    "controlplane": {
        "extras": ["azure", "api", "mcp"],
        "tail": 'EXPOSE 8000\nCMD ["uvicorn", "controlplane:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]\n',
    },
    # Plain FastAPI JSON-RPC (MCP wire protocol) — no MCP SDK dependency.
    "mcpserver": {
        "extras": ["azure", "api"],
        "tail": 'EXPOSE 8000\nCMD ["uvicorn", "mcpserver:create_mcp_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]\n',
    },
    "worker": {
        "extras": ["azure"],
        "tail": 'CMD ["python", "-m", "worker.runner"]\n',
    },
}

root = Path(__file__).resolve().parent.parent / "docker"
for svc, cfg in SERVICES.items():
    flags = " ".join(f"--extra {e}" for e in cfg["extras"])
    text = TEMPLATE.format(extra_flags=flags) + "\n" + cfg["tail"]
    (root / f"{svc}.Dockerfile").write_text(text, encoding="utf8")
    print(f"wrote docker/{svc}.Dockerfile")
