"""Review worker: consume queue → run lenses → persist → annotate (FR-006..018).

Failure attribution (FR-024) and stop conditions (FR-025) at lens granularity.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

from harness.models import (
    Change,
    ChangeType,
    Finding,
    GitProvider,
    ReviewRun,
    RiskLevel,
    RunStatus,
    Severity,
)
from harness.queue import QUEUE_NAME, ReviewQueueConsumer
from harness.telemetry import configure_telemetry
from lenses import LENS_REGISTRY, LensContext
from lenses.diffparse import parse_unified_diff
from providers.azuredevops import AzureDevOpsAdapter
from providers.base import PROMPT_VERSION, AnnotationReport
from providers.github import GitHubAdapter
from worker.depth import classify_review_depth
from worker.exploitability import annotate_security_findings, build_provider_findings, effective_severity
from worker.gate import build_annotation_report
from worker.llm_review import (
    build_trusted_symbol_index_context,
    coerce_applicable_rules,
    compose_rule_aware_client,
    resolve_rule_briefs,
)
from worker.persistence import make_repository as _make_repository
from worker.persistence import mark_superseded_runs as _mark_superseded_runs
from worker.persistence import persist
from worker.runner_helpers import (
    budget_from_env,
    build_review_metadata,
    effective_risk_level,
    fetch_workitem_completeness,
    load_symbol_index,
    risk_signals_from_event,
)
from worker.runner_lenses import LLM_LENSES as DEFAULT_LLM_LENSES
from worker.runner_lenses import execute_lenses
from worker.second_opinion import apply_second_opinions
from worker.suppression import merge_duplicate_findings

ADAPTERS = {
    GitHubAdapter.name: GitHubAdapter(),
    AzureDevOpsAdapter.name: AzureDevOpsAdapter(),
}

logger = logging.getLogger("worker.runner")
LLM_LENSES = DEFAULT_LLM_LENSES
_INLINE_ANNOTATION_SEVERITIES = {Severity.BLOCKER, Severity.HIGH}
_DEFAULT_SECOND_OPINION_LIMIT = 5


async def execute_review(event_data: dict[str, object]) -> ReviewRun:
    """Run the full review pipeline for one enqueued change event."""
    change_id = str(event_data.get("changeId", ""))
    head_sha = str(event_data.get("headSha", ""))
    repo_id = str(event_data.get("repoId", ""))
    provider_name = str(event_data.get("provider", "github"))
    pr_number_raw = event_data.get("prNumber")
    base_sha = str(event_data.get("baseSha") or "")
    classification = str(event_data.get("classification") or "code")
    risk_signals = risk_signals_from_event(event_data.get("riskSignals"))
    acknowledged = bool(event_data.get("riskAcknowledged", False))

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
    repository = _make_repository()
    await _mark_superseded_runs(repository, change_id=change_id, current_head_sha=head_sha)

    # Fetch diff (deterministic gate — runs even without model access).
    diff_text = await adapter.fetch_diff(change)
    lens_files = parse_unified_diff(diff_text)
    depth_policy = classify_review_depth(
        classification=classification,
        changed_paths=[lens_file.path for lens_file in lens_files] or change.changed_files,
        risk_signals=risk_signals,
    )
    budget = budget_from_env()
    base_model_client = event_data.get("modelClient")
    model_deployment = str(event_data.get("modelDeployment") or "")
    symbol_index, symbol_index_reason = load_symbol_index()
    degraded_reasons: list[str] = [] if symbol_index_reason is None else [symbol_index_reason]
    rule_briefs = resolve_rule_briefs(
        changed_paths=tuple(lens_file.path for lens_file in lens_files),
        applicable_rules=coerce_applicable_rules(event_data.get("applicableRules")),
    )
    model_client = compose_rule_aware_client(
        base_model_client,
        rule_briefs,
        trusted_user_context=build_trusted_symbol_index_context(symbol_index),
    )

    lens_outcome = await execute_lenses(
        run=run,
        lens_names=depth_policy.lenses,
        change_id=change_id,
        repo_id=repo_id,
        files=lens_files,
        model_client=model_client,
        model_deployment=model_deployment,
        symbol_index=symbol_index,
        budget=budget,
        registry=LENS_REGISTRY,
        llm_lenses=LLM_LENSES,
    )
    findings = lens_outcome.findings
    degraded_reasons.extend(lens_outcome.degraded_reasons)
    executed_lenses = lens_outcome.executed_lenses
    unavailable_lenses = lens_outcome.unavailable_lenses
    scope_notes = lens_outcome.scope_notes

    run.input_tokens = budget.input_tokens
    run.output_tokens = budget.output_tokens
    run.compute_ms = budget.compute_ms
    findings = merge_duplicate_findings(findings)
    findings = annotate_security_findings(findings, lens_files, diff_text)
    opinion_outcome = await apply_second_opinions(
        findings,
        ctx=LensContext(
            change_id=change_id,
            repo_id=repo_id,
            files=lens_files,
            model_client=model_client,
            model_deployment=model_deployment,
            symbol_index=symbol_index,
        ),
        budget=budget,
        base_client=base_model_client,
        lens_briefs=rule_briefs,
        run=run,
        limit=int(os.environ.get("HARNESS_SECOND_OPINION_LIMIT", str(_DEFAULT_SECOND_OPINION_LIMIT))),
    )
    findings = opinion_outcome.findings
    degraded_reasons.extend(opinion_outcome.degraded_reasons)
    run.input_tokens = budget.input_tokens
    run.output_tokens = budget.output_tokens
    run.compute_ms = budget.compute_ms
    run.review_metadata = build_review_metadata(
        declared_lenses=depth_policy.lenses,
        executed_lenses=executed_lenses,
        unavailable_lenses=unavailable_lenses,
        prompt_version=PROMPT_VERSION,
    )
    base_status = RunStatus.DEGRADED if degraded_reasons else RunStatus.COMPLETED
    if unavailable_lenses:
        base_status = RunStatus.DEGRADED
    base_coverage = "partial_explicit" if degraded_reasons or unavailable_lenses else "full"
    run.status, degraded_reasons, coverage = budget.apply_to(
        base_status,
        degraded_reasons,
        base_coverage,
    )
    run.coverage = coverage
    run.degraded_reasons = degraded_reasons
    run.completed_at = datetime.now(UTC)

    await persist(run, findings, repository=repository)
    wi_completeness = await fetch_workitem_completeness(adapter, change)
    report = _build_report(
        run,
        findings,
        depth_policy.risk_level,
        depth_policy.risk_floor,
        depth_policy.lenses,
        tuple(dict.fromkeys(executed_lenses)),
        tuple(dict.fromkeys(unavailable_lenses)),
        tuple(dict.fromkeys(scope_notes)),
        acknowledged=acknowledged,
        workitem_completeness=wi_completeness,
    )

    if not os.environ.get("HARNESS_DRY_RUN"):
        try:
            provider_findings = build_provider_findings(findings)
            inline_findings = [
                finding for finding in provider_findings if effective_severity(finding) in _INLINE_ANNOTATION_SEVERITIES
            ]
            await adapter.post_annotations(change, inline_findings, report)
            await adapter.upsert_summary_comment(change, provider_findings, report)
        except Exception as exc:  # noqa: BLE001 - projection failure stays visible (F1)
            logger.error("annotation posting failed: %s", exc)
            run.degraded_reasons.append(f"annotation_failed:{change.provider.value}")
            run.coverage = "partial_explicit"
            if run.status == RunStatus.COMPLETED:
                run.status = RunStatus.DEGRADED
            await persist(run, findings, repository=repository)

    return run


def _build_report(
    run: ReviewRun,
    findings: list[Finding],
    risk_level: RiskLevel,
    risk_floor: RiskLevel,
    declared_lenses: tuple[str, ...],
    executed_lenses: tuple[str, ...],
    unavailable_lenses: tuple[str, ...],
    scope_notes: tuple[str, ...],
    *,
    acknowledged: bool,
    workitem_completeness: str = "",
) -> AnnotationReport:
    return build_annotation_report(
        run,
        findings,
        risk_level=effective_risk_level(risk_floor, risk_level),
        risk_floor=risk_floor,
        declared_lenses=declared_lenses,
        executed_lenses=executed_lenses,
        unavailable_lenses=unavailable_lenses,
        scope_notes=scope_notes,
        acknowledged=acknowledged,
        workitem_completeness=workitem_completeness,
    )


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

    configure_telemetry()
    logger.info("worker consuming queue %s", QUEUE_NAME)
    await consumer.receive(handler)


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    import asyncio

    asyncio.run(main_loop())
