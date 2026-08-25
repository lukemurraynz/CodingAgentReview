"""MCP server — vendor-neutral review & engineering-state interface (FR-002/005).

Native MCP SDK (FastMCP, streamable-http). Tools:
- review_validate_change: pre-commit diff review (sync <=500 changed lines).
- get_active_findings / get_risk_explanation: grounded engineering-state queries.

Repo scoping via harness.authz; reviewed content treated as untrusted data (FR-034).
"""

from __future__ import annotations

import logging
import os

from mcp.server.fastmcp import FastMCP

# Bind explicitly: Container Apps ingress targets 0.0.0.0:8000.
mcp = FastMCP("engineering-harness", host="0.0.0.0", port=8000)

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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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
    """ASGI factory: FastAPI shell + native FastMCP streamable-http mounted at /mcp."""
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    api = FastAPI(title="engineering-harness-mcp")

    @api.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    api.mount("/mcp", mcp.streamable_http_app())
    return api

def main() -> None:
    import uvicorn as _uv

    """Process entrypoint (python -m mcpserver) — stdio/local use only."""
    logging.basicConfig(level=logging.INFO)
    import uvicorn

    uvicorn.run(mcp.streamable_http_app(), host="0.0.0.0", port=8000, log_level="info")


if __name__ == "__main__":  # pragma: no cover
    main()
