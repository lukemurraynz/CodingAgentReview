"""Gate-zero baseline (SC-004): run corpus through the production review path, score.

Usage: uv run python scripts/run_baseline.py [--gate] [--scores-out PATH]
  --gate  exit non-zero when results violate SC-004 thresholds (CI regression gate).

Production fidelity: each case runs the deterministic lenses (structural,
production_validation) over a synthesized whole-file diff FIRST, and their
findings are combined with the LLM lens output before scoring — matching how
the worker reviews real changes.
Requires: az login (Entra), Foundry deployed (infra/modules/foundry.bicep).
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from eval.score import dump_scores, load_cases, sc004_gate_failures, score_review, summarize  # noqa: E402
from harness.models import Finding  # noqa: E402
from lenses import LensContext, LensFile  # noqa: E402
from lenses.diffparse import parse_unified_diff  # noqa: E402
from lenses.llm import _SYSTEM, CorrectnessLens, LensUnavailable, SecurityLens  # noqa: E402

# Production fidelity: the benchmark uses the exact lens prompts the worker uses.
SYSTEM = _SYSTEM

ROOT = Path(__file__).resolve().parent.parent
ENDPOINT = os.environ.get(
    "HARNESS_FOUNDRY_ENDPOINT", "https://fnd-harness-dev-lm.services.ai.azure.com/models"
)
DEPLOYMENT = os.environ.get("HARNESS_FOUNDRY_DEPLOYMENT", "gpt-4.1-mini")


def _as_diff(code: str, path: str = "case.py") -> str:
    """Synthesize a whole-file unified diff so deterministic lenses can parse it."""
    lines = code.splitlines()
    body = "".join(f"+{line}\n" for line in lines) or "+\n"
    return (
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
        f"@@ -0,0 +1,{max(len(lines), 1)} @@\n{body}"
    )


async def _deterministic_findings(code: str) -> list[str]:
    """Run the deterministic lenses exactly as production does; return finding summaries."""
    from lenses import LensContext, LensFile, ProductionValidationLens, StructuralLens

    diff = _as_diff(code)
    files = parse_unified_diff(diff)
    ctx = LensContext(
        change_id="baseline",
        repo_id="org/baseline",
        files=[
            LensFile(path=f.path, content=f.content, added_lines=frozenset(range(1, len(f.content.splitlines()) + 2)))
            for f in files
        ],
    )
    findings: list[str] = []
    for lens in (StructuralLens(), ProductionValidationLens()):
        for finding in await lens.run(ctx):
            evidence = finding.evidence[0] if finding.evidence else None
            loc = f"{evidence.path}:{evidence.line_start}" if evidence else "?"
            findings.append(f"{finding.severity.value}: {finding.title} ({loc})")
    return findings


def _lens_context(code: str) -> LensContext:
    parsed = parse_unified_diff(_as_diff(code))
    files = [
        LensFile(
            path=file.path,
            content=file.content,
            added_lines=file.added_lines,
            line_map=file.line_map,
            patch_lines=file.patch_lines,
            deleted=file.deleted,
            old_path=file.old_path,
        )
        for file in parsed
    ]
    return LensContext(
        change_id="baseline",
        repo_id="org/baseline",
        files=files,
        model_deployment=DEPLOYMENT,
    )


def _render_findings(findings: list[Finding]) -> list[str]:
    rendered: list[str] = []
    for finding in findings:
        evidence = finding.evidence[0] if finding.evidence else None
        location = f" ({evidence.path}:{evidence.line_start})" if evidence else ""
        rendered.append(f"{finding.severity.value}: {finding.title} — {finding.detail}{location}")
    return rendered


def _make_model_client(client):
    async def _invoke(*, system_prompt: str, user_prompt: str, deployment: str, lens: str) -> str:
        del lens
        try:
            resp = await client.complete(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model=deployment,
                temperature=0.0,
                max_tokens=800,
            )
        except Exception as exc:  # noqa: BLE001
            if "content_filter" in str(exc):
                return "[BLOCKED_BY_CONTENT_FILTER]"
            raise
        return resp.choices[0].message.content or ""

    return _invoke


async def review(code: str, client) -> str:
    ctx = _lens_context(code)
    llm_ctx = LensContext(
        change_id=ctx.change_id,
        repo_id=ctx.repo_id,
        files=ctx.files,
        model_client=_make_model_client(client),
        model_deployment=ctx.model_deployment,
    )
    rendered: list[str] = []
    for lens in (CorrectnessLens(), SecurityLens()):
        try:
            findings = await lens.run(llm_ctx)
        except LensUnavailable:
            findings = []
        rendered.extend(_render_findings(findings))
    rendered.extend(await _deterministic_findings(code))
    return "\n".join(rendered)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate", action="store_true", help="exit non-zero on SC-004 violations")
    parser.add_argument("--scores-out", type=Path, help="optional path to persist per-case score records as JSON")
    args = parser.parse_args()
    from azure.ai.inference.aio import ChatCompletionsClient
    from azure.identity.aio import DefaultAzureCredential

    from harness.credentials import ScopedAsyncCredential

    credential = ScopedAsyncCredential(DefaultAzureCredential())
    client = ChatCompletionsClient(endpoint=ENDPOINT, credential=credential)
    try:
        cases = load_cases(ROOT / "benchmark" / "cases")
        scores = []
        for case, folder in cases:
            code_file = folder / ("code.py" if case.category == "clean" else "defect.py")
            code = code_file.read_text(encoding="utf8")
            combined = await review(code, client)
            scores.append(score_review(case, combined))
            print(f"{case.category:>22}/{case.id}: {scores[-1].kind}")
    finally:
        await client.close()
        await credential.close()

    summary = summarize(scores)
    if args.scores_out is not None:
        args.scores_out.write_text(json.dumps(dump_scores(scores), indent=2), encoding="utf8")
    md = "# Benchmark Baseline (SC-004 gate-zero)\n\n```\n" + summary.to_markdown() + "\n```\n"
    md += f"\nEndpoint: {ENDPOINT} | Model: {DEPLOYMENT}\n"
    (ROOT / "docs" / "benchmark-baseline.md").write_text(md, encoding="utf8")
    print("\n" + summary.to_markdown())

    if args.gate:
        failures = sc004_gate_failures(summary)
        if failures:
            print("\nSC-004 GATE FAILED:\n- " + "\n- ".join(failures))
            sys.exit(1)
        print("\nSC-004 gate passed.")


if __name__ == "__main__":
    asyncio.run(main())
