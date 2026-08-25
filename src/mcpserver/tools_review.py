"""Pre-commit review tool implementation."""

from __future__ import annotations

import os

MAX_SYNC_DIFF_LINES = 500


async def review_validate_change(*, repo_id: str, diff: str) -> dict[str, object]:
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
        repo_id=repo_id or os.environ.get("HARNESS_DEFAULT_REPO", "adhoc"),
        files=files,
    )

    results: list[dict[str, object]] = []
    for name in ("structural", "production_validation"):
        findings = await LENS_REGISTRY[name].run(ctx)
        for finding in findings:
            results.append(
                {
                    "lens": name,
                    "severity": finding.severity.value,
                    "title": finding.title,
                    "path": finding.evidence[0].path,
                    "line": finding.evidence[0].line_start,
                    "rule": finding.evidence[0].rule_id,
                }
            )
    for lens in (CorrectnessLens(), SecurityLens()):
        try:
            produced = await lens.run(ctx)
        except Exception as exc:  # noqa: BLE001
            results.append({"lens": lens.name, "severity": "info", "skipped": str(exc)})
            continue
        for finding in produced:
            results.append(
                {
                    "lens": lens.name,
                    "severity": finding.severity.value,
                    "title": finding.title,
                    "path": finding.evidence[0].path,
                    "line": finding.evidence[0].line_start,
                }
            )

    blocking = [result for result in results if result.get("severity") == "blocker"]
    return {"mode": "sync", "findingCount": len(results), "blocking": len(blocking), "findings": results}
