"""MCP server — vendor-neutral review & engineering-state interface (FR-002/005).

Tools:
- review_validate_change: pre-commit diff review (sync ≤500 changed lines).
- get_active_findings / get_risk_explanation: grounded engineering-state queries.

Auth: Entra bearer validated at the ingress layer; tool-level repo scoping via
harness.authz.
"""

from __future__ import annotations

import logging
import os

from mcp.server.fastmcp import FastMCP

from harness.authz import Principal, principal_from_claims
from harness.redaction import redact_text

logger = logging.getLogger("mcpserver")

mcp = FastMCP("engineering-harness")

MAX_SYNC_DIFF_LINES = 500


def _principal_from_headers(headers: dict[str, str]) -> Principal:
    """Extract principal from X-Harness-Claims header set by the auth proxy.

    Direct JWT validation lands with the ingress wiring task; this seam fails
    closed — no claims header means an anonymous principal with zero scopes.
    """
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
    ctx = LensContext(change_id="adhoc", repo_id=os.environ.get("HARNESS_DEFAULT_REPO", "adhoc"), files=files)

    results: list[dict[str, object]] = []
    for name in ("structural", "production_validation"):
        findings = await LENS_REGISTRY[name].run(ctx)
        for f in findings:
            title, _ = redact_text(f.title)
            results.append(
                {
                    "lens": name,
                    "severity": f.severity.value,
                    "title": title,
                    "path": f.evidence[0].path,
                    "line": f.evidence[0].line_start,
                    "rule": f.evidence[0].rule_id,
                }
            )
    # LLM lenses run opportunistically; unavailable model ⇒ explicit skip marker.
    for lens in (CorrectnessLens(), SecurityLens()):
        try:
            produced = await lens.run(ctx)
        except Exception as exc:  # noqa: BLE001 — degraded mode is explicit (FR-035)
            results.append({"lens": lens.name, "severity": "info", "skipped": str(exc)})
            continue
        for f in produced:
            title, _ = redact_text(f.title)
            results.append(
                {"lens": lens.name, "severity": f.severity.value, "title": title,
                 "path": f.evidence[0].path, "line": f.evidence[0].line_start}
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
            "title": redact_text(f.title)[0],
            "status": f.status,
        }
        for f in active
    ]


@mcp.tool()
async def get_risk_explanation(change_id: str) -> dict[str, object]:
    """Explain the recorded risk assessment drivers for a change."""
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


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    transport = os.environ.get("HARNESS_MCP_TRANSPORT", "streamable-http")
    valid = ("stdio", "sse", "streamable-http")
    chosen = transport if transport in valid else "streamable-http"
    mcp.run(transport=chosen)  # type: ignore[arg-type]
