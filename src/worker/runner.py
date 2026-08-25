"""Review worker: consume queue → run lenses → persist → annotate (FR-006..018).

Failure attribution (FR-024) and stop conditions (FR-025) at lens granularity.
"""

from __future__ import annotations

import logging
import os
import time

from harness.models import (
    Change,
    ChangeType,
    Finding,
    GitProvider,
    LensResult,
    LensStatus,
    ReviewRun,
    RunStatus,
)
from harness.queue import QUEUE_NAME, ReviewQueueConsumer
from lenses import LENS_REGISTRY, LensContext
from lenses.diffparse import parse_unified_diff
from lenses.llm import CorrectnessLens, LensUnavailable, SecurityLens
from providers.azuredevops import AzureDevOpsAdapter
from providers.github import GitHubAdapter

logger = logging.getLogger("worker.runner")

ADAPTERS = {
    GitHubAdapter.name: GitHubAdapter(),
    AzureDevOpsAdapter.name: AzureDevOpsAdapter(),
}

MAX_CONSECUTIVE_FAILURES = 3

LLM_LENSES = {"correctness": CorrectnessLens(), "security": SecurityLens()}


class StopConditionTriggered(RuntimeError):
    """Raised when retry/oscillation policy demands escalation (FR-025)."""


def _lens_order(classification: str) -> list[str]:
    """Risk-proportionate lens selection (FR-012). Docs/generated skip deep review."""
    if classification in ("docs", "generated", "lockfile"):
        return []
    return ["structural", "production_validation", "correctness", "security"]


async def execute_review(event_data: dict[str, object]) -> ReviewRun:
    """Run the full review pipeline for one enqueued change event."""
    change_id = str(event_data.get("changeId", ""))
    head_sha = str(event_data.get("headSha", ""))
    repo_id = str(event_data.get("repoId", ""))
    provider_name = str(event_data.get("provider", "github"))
    pr_number_raw = event_data.get("prNumber")
    base_sha = str(event_data.get("baseSha") or "")
    classification = str(event_data.get("classification") or "code")

    adapter = ADAPTERS[provider_name]
    change = Change(
        id=change_id,
        provider=GitProvider(provider_name),
        repo_id=repo_id,
        change_type=ChangeType.PULL_REQUEST if pr_number_raw else ChangeType.COMMIT,
        head_sha=head_sha,
        base_sha=base_sha or None,
        pr_number=int(pr_number_raw) if isinstance(pr_number_raw, int) else None,
    )

    run = ReviewRun(id=f"run-{change_id}@{head_sha[:12]}", change_id=change_id, head_sha=head_sha)
    run.status = RunStatus.RUNNING

    # Fetch diff (deterministic gate — runs even without model access).
    diff_text = await adapter.fetch_diff(change)
    lens_files = parse_unified_diff(diff_text)

    budget = _budget_from_env()
    consecutive_failures = 0
    findings: list[Finding] = []
    degraded_reasons: list[str] = []

    for name in _lens_order(classification):
        if budget.exhausted:
            run.lens_results.append(
                LensResult(lens=name, status=LensStatus.SKIPPED_BUDGET, error=None)
            )
            continue
        if budget.is_diminishing():
            # FR-025 diminishing-returns stop (ported from Claude Code tokenBudget.ts):
            # two consecutive sub-threshold deltas means remaining lenses add no signal.
            degraded_reasons.append(f"diminishing_returns:{name}")
            run.lens_results.append(
                LensResult(lens=name, status=LensStatus.SKIPPED_BUDGET)
            )
            continue
        lens = LENS_REGISTRY.get(name) or LLM_LENSES.get(name)
        if lens is None:
            continue
        started = time.monotonic()
        try:
            ctx = LensContext(
                change_id=change_id, repo_id=repo_id, files=lens_files
            )
            produced = await lens.run(ctx)
            duration_ms = int((time.monotonic() - started) * 1000)
            usage = getattr(lens, "last_usage", None)
            if usage:
                budget.record(
                    input_tokens=int(usage.get("input_tokens", 0)),
                    output_tokens=int(usage.get("output_tokens", 0)),
                    compute_ms=duration_ms,
                )
            findings.extend(produced)
            run.lens_results.append(
                LensResult(
                    lens=name,
                    status=LensStatus.COMPLETED,
                    findings_count=len(produced),
                    duration_ms=duration_ms,
                )
            )
            consecutive_failures = 0
        except LensUnavailable as exc:
            degraded_reasons.append(f"model_unavailable:{name}")
            run.lens_results.append(
                LensResult(
                    lens=name,
                    status=LensStatus.SKIPPED_POLICY,
                    error=str(exc),
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
            )
        except Exception as exc:  # noqa: BLE001 — isolated by design (FR-009)
            duration_ms = int((time.monotonic() - started) * 1000)
            reason = classify_failure(exc)
            logger.warning("lens %s failed (%s): %s", name, reason, exc)
            run.lens_results.append(
                LensResult(
                    lens=name,
                    status=LensStatus.FAILED,
                    error=f"{reason}: {exc}",
                    duration_ms=duration_ms,
                )
            )
            degraded_reasons.append(f"{reason}:{name}")
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                raise StopConditionTriggered(
                    f"{consecutive_failures} consecutive lens failures; escalating"
                ) from exc
        finally:
            budget.mark_check()

    run.status, degraded_reasons, coverage = budget.apply_to(
        RunStatus.COMPLETED if not any(
            r.status == LensStatus.FAILED for r in run.lens_results
        ) else RunStatus.DEGRADED,
        degraded_reasons,
        "full",
    )
    run.coverage = coverage
    run.degraded_reasons = degraded_reasons
    run.completed_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)

    await persist(run, findings)

    if not os.environ.get("HARNESS_DRY_RUN"):
        try:
            await adapter.post_annotations(change, findings)
        except Exception as exc:  # noqa: BLE001 - projection failure stays visible (F1)
            logger.error("annotation posting failed: %s", exc)
            run.degraded_reasons.append(f"annotation_failed:{change.provider.value}")
            run.coverage = "partial_explicit"
            if run.status == RunStatus.COMPLETED:
                run.status = RunStatus.DEGRADED

    return run


def _budget_from_env():  # type: ignore[no-untyped-def]
    from worker.budget import RunBudget

    return RunBudget(
        max_input_tokens=int(os.environ.get("HARNESS_BUDGET_INPUT_TOKENS", "200000")),
        max_output_tokens=int(os.environ.get("HARNESS_BUDGET_OUTPUT_TOKENS", "20000")),
        max_compute_ms=int(os.environ.get("HARNESS_BUDGET_COMPUTE_MS", "240000")),
    )


def classify_failure(exc: Exception) -> str:
    """FR-024 failure attribution (V1 lens-level subset)."""
    text = f"{type(exc).__name__}: {exc}".lower()
    if "timeout" in text or "timed out" in text or "connection" in text:
        return "dependency_error"
    if "rate" in text and ("limit" in text or "429" in text):
        return "dependency_error"
    if "auth" in text or "401" in text or "403" in text or "permission" in text:
        return "environment_error"
    if isinstance(exc, StopConditionTriggered):
        return "agent_error"
    return "product_defect"


async def persist(run: ReviewRun, findings: list[Finding]) -> None:
    """Persist run + findings. Skips honestly when no state store configured."""
    from harness.repository import HarnessRepository

    if not os.environ.get("HARNESS_COSMOS_ENDPOINT"):
        logger.warning("no HARNESS_COSMOS_ENDPOINT — run state NOT persisted")
        return
    from harness.redaction import redact_text

    for f in findings:
        f.title, _ = redact_text(f.title)
        f.detail, _ = redact_text(f.detail or "")
    repo = HarnessRepository(database=os.environ.get("HARNESS_COSMOS_DATABASE", "harness"))
    await repo.put_run(run)
    for f in findings:
        await repo.put_finding(f)


async def main_loop() -> None:  # pragma: no cover - process entrypoint
    """Entry mode selection:
    - HARNESS_JOB_MODE=drain → consume for DRAIN_SECONDS then exit cleanly
      (matches Container Apps Job scheduled executions).
    - default → continuous consumption (long-running service mode).
    """
    consumer = ReviewQueueConsumer()

    async def handler(payload: dict[str, object]) -> None:
        data = payload.get("data")
        if isinstance(data, dict):
            await execute_review(data)

    mode = os.environ.get("HARNESS_JOB_MODE", "service")
    if mode == "drain":
        seconds = int(os.environ.get("HARNESS_DRAIN_SECONDS", "50"))
        logger.info("worker draining queue %s for %ss", QUEUE_NAME, seconds)
        import asyncio

        run_task = asyncio.create_task(consumer.receive(handler))
        done, _ = await asyncio.wait({run_task}, timeout=seconds)
        if not done:
            run_task.cancel()
            logger.info("drain window elapsed; exiting cleanly")
        return

    logger.info("worker consuming queue %s", QUEUE_NAME)
    await consumer.receive(handler)


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    import asyncio

    asyncio.run(main_loop())
