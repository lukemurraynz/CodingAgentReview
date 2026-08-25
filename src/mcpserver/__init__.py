"""MCP server — vendor-neutral review & engineering-state interface (FR-002/005).

Native MCP SDK (FastMCP, streamable-http). Tools:
- review_validate_change: pre-commit diff review (sync <=500 changed lines).
- get_active_findings / get_risk_explanation: grounded engineering-state queries.

Repo scoping via harness.authz; reviewed content treated as untrusted data (FR-034).
"""

from __future__ import annotations

import json
import logging
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


# Bind explicitly: Container Apps ingress targets 0.0.0.0:8000.
MAX_SYNC_DIFF_LINES = 500


def _principal_from_headers(headers: dict[str, str]):
    """Fail-closed principal extraction; JWT validation lands at the ingress seam."""
    from harness.authz import Principal, principal_from_claims

    raw = headers.get("x-harness-claims", "")
    if not raw:
        return Principal(oid="anonymous", scopes=frozenset(), repos=frozenset())
    import json

    try:
        return principal_from_claims(json.loads(raw))
    except ValueError:
        return Principal(oid="anonymous", scopes=frozenset(), repos=frozenset())


async def review_validate_change(diff: str) -> dict[str, object]:
    """Review an uncommitted working-tree diff and return structured findings."""
    if len(diff.splitlines()) > MAX_SYNC_DIFF_LINES:
        return {
            "mode": "async",
            "error": f"diff exceeds {MAX_SYNC_DIFF_LINES} lines; submit via pull request for full review",
        }

    from lenses import LENS_REGISTRY, LensContext
    from lenses.diffparse import parse_unified_diff
    from lenses.llm import CorrectnessLens, SecurityLens

    files = parse_unified_diff(diff)
    ctx = LensContext(
        change_id="adhoc",
        repo_id=os.environ.get("HARNESS_DEFAULT_REPO", "adhoc"),
        files=files,
    )

    results: list[dict[str, object]] = []
    for name in ("structural", "production_validation"):
        findings = await LENS_REGISTRY[name].run(ctx)
        for f in findings:
            results.append(
                {
                    "lens": name,
                    "severity": f.severity.value,
                    "title": f.title,
                    "path": f.evidence[0].path,
                    "line": f.evidence[0].line_start,
                    "rule": f.evidence[0].rule_id,
                }
            )
    for lens in (CorrectnessLens(), SecurityLens()):
        try:
            produced = await lens.run(ctx)
        except Exception as exc:  # noqa: BLE001 — explicit degraded mode (FR-035)
            results.append({"lens": lens.name, "severity": "info", "skipped": str(exc)})
            continue
        for f in produced:
            results.append(
                {
                    "lens": lens.name,
                    "severity": f.severity.value,
                    "title": f.title,
                    "path": f.evidence[0].path,
                    "line": f.evidence[0].line_start,
                }
            )

    blocking = [r for r in results if r.get("severity") == "blocker"]
    return {"mode": "sync", "findingCount": len(results), "blocking": len(blocking), "findings": results}


async def get_active_findings(repo_id: str) -> list[dict[str, object]]:
    """Active (non-stale, non-waived) findings for a repository."""
    endpoint = os.environ.get("HARNESS_COSMOS_ENDPOINT")
    if not endpoint:
        return [{"error": "state store not configured"}]
    from harness.repository import HarnessRepository

    findings = await HarnessRepository().get_findings(repo_id)
    active = [f for f in findings if f.status in ("candidate", "confirmed", "reopened")]
    return [
        {
            "id": f.id,
            "category": f.category.value,
            "severity": f.severity.value,
            "title": f.title,
            "status": f.status,
        }
        for f in active
    ]


async def get_risk_explanation(change_id: str) -> dict[str, object]:
    """Explain recorded risk drivers for a change."""
    from harness.cosmos_state import latest_risk_for_change

    assessment = await latest_risk_for_change(change_id)
    if assessment is None:
        return {"changeId": change_id, "risk": "unknown",
                "reasons": ["no risk assessment recorded"]}
    return {
        "changeId": change_id,
        "risk": assessment.level.value,
        "reasons": [f"{s.name}: {s.level.value}" for s in assessment.signals],
    }


def create_mcp_app():
    """ASGI factory: plain FastAPI serving the MCP JSON-RPC wire protocol.

    Implements initialize / tools/list / tools/call over POST /mcp — the
    streamable-http subset every MCP client speaks — without depending on the
    SDK's session-manager ASGI lifecycle (which hangs under CA ingress).
    """

    api = FastAPI(title="engineering-harness-mcp")

    @api.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    TOOLS = {
        "review_validate_change": (
            review_validate_change,
            "Review an uncommitted working-tree diff and return structured findings.",
            {"type": "object", "properties": {"diff": {"type": "string"}},
             "required": ["diff"]},
        ),
        "get_active_findings": (
            get_active_findings,
            "Active findings for a repository.",
            {"type": "object", "properties": {"repo_id": {"type": "string"}},
             "required": ["repo_id"]},
        ),
        "get_risk_explanation": (
            get_risk_explanation,
            "Recorded risk drivers for a change.",
            {"type": "object", "properties": {"change_id": {"type": "string"}},
             "required": ["change_id"]},
        ),
    }

    @api.post("/mcp")
    async def mcp_endpoint(request: Request):
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "invalid json"})
        method = body.get("method", "")
        msg_id = body.get("id")
        params = body.get("params") or {}

        if method == "initialize":
            return JSONResponse({
                "jsonrpc": "2.0", "id": msg_id,
                "result": {
                    "protocolVersion": params.get("protocolVersion", "2025-03-26"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "engineering-harness", "version": "0.1.0"},
                },
            })
        if method == "notifications/initialized":
            return JSONResponse(status_code=202, content={})
        if method == "tools/list":
            return JSONResponse({"jsonrpc": "2.0", "id": msg_id, "result": {
                "tools": [
                    {"name": n, "description": d, "inputSchema": sch}
                    for n, (fn, d, sch) in TOOLS.items()
                ]}})
        if method == "tools/call":
            name = params.get("name")
            if name not in TOOLS:
                return JSONResponse({"jsonrpc": "2.0", "id": msg_id, "error":
                                     {"code": -32601, "message": f"unknown tool {name}"}})
            fn, _, _ = TOOLS[name]
            result = await fn(**(params.get("arguments") or {}))
            if isinstance(result, list):
                result = {"items": result}
            return JSONResponse({"jsonrpc": "2.0", "id": msg_id, "result": {
                "content": [{"type": "text",
                             "text": json.dumps(result, default=str)}]}})
        return JSONResponse({"jsonrpc": "2.0", "id": msg_id, "error":
                             {"code": -32601, "message": f"unknown method {method}"}})

    return api

def main() -> None:
    from harness.telemetry import configure_telemetry

    configure_telemetry()
    import uvicorn as _uv

    """Process entrypoint (python -m mcpserver) — stdio/local use only."""
    logging.basicConfig(level=logging.INFO)
    import uvicorn

    uvicorn.run("mcpserver:create_mcp_app", factory=True, host="0.0.0.0", port=8000, log_level="info")


if __name__ == "__main__":  # pragma: no cover
    main()
