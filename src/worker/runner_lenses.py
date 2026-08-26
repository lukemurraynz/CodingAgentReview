"""Lens execution loop extracted from the worker runner."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass

from graph import SymbolIndex
from harness.models import Finding, LensResult, LensStatus, ReviewRun
from lenses import LensContext, LensFile
from lenses.base import Lens
from lenses.llm import CorrectnessLens, LensUnavailable, SecurityLens
from worker.attribution import classify_failure
from worker.budget import RunBudget
from worker.runner_helpers import lens_scope_notes
from worker.second_opinion import mark_second_opinion_candidate
from worker.stopconditions import MAX_CONSECUTIVE_FAILURES, evaluate_consecutive_failures
from worker.suppression import attach_scope

logger = logging.getLogger("worker.runner")

LLM_LENSES = {"correctness": CorrectnessLens(), "security": SecurityLens()}


@dataclass(frozen=True, slots=True)
class LensExecutionOutcome:
    findings: list[Finding]
    degraded_reasons: list[str]
    executed_lenses: list[str]
    unavailable_lenses: list[str]
    scope_notes: list[str]


async def execute_lenses(
    *,
    run: ReviewRun,
    lens_names: tuple[str, ...],
    change_id: str,
    repo_id: str,
    files: list[LensFile],
    model_client: object | None,
    model_deployment: str,
    symbol_index: SymbolIndex | None,
    budget: RunBudget,
    registry: Mapping[str, Lens],
    llm_lenses: Mapping[str, Lens],
) -> LensExecutionOutcome:
    """Execute the declared lenses with budget and degradation tracking."""

    consecutive_failures = 0
    findings: list[Finding] = []
    degraded_reasons: list[str] = []
    executed_lenses: list[str] = []
    unavailable_lenses: list[str] = []
    scope_notes: list[str] = []

    for name in lens_names:
        scope_notes.extend(lens_scope_notes(name))
        if budget.exhausted:
            run.lens_results.append(LensResult(lens=name, status=LensStatus.SKIPPED_BUDGET, error=None))
            continue
        if budget.is_diminishing():
            degraded_reasons.append(f"diminishing_returns:{name}")
            run.lens_results.append(LensResult(lens=name, status=LensStatus.SKIPPED_BUDGET))
            continue
        lens = registry.get(name) or llm_lenses.get(name)
        if lens is None:
            unavailable_lenses.append(name)
            degraded_reasons.append(f"lens_missing:{name}")
            continue
        started = time.monotonic()
        try:
            ctx = LensContext(
                change_id=change_id,
                repo_id=repo_id,
                files=files,
                model_client=model_client,
                model_deployment=model_deployment,
                symbol_index=symbol_index,
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
            produced_findings = [attach_scope(finding, lens_scope_notes(name)) for finding in produced]
            if name in llm_lenses:
                produced_findings = [mark_second_opinion_candidate(finding, name) for finding in produced_findings]
            findings.extend(produced_findings)
            run.lens_results.append(
                LensResult(
                    lens=name,
                    status=LensStatus.COMPLETED,
                    findings_count=len(produced),
                    duration_ms=duration_ms,
                )
            )
            executed_lenses.append(name)
            consecutive_failures = 0
        except LensUnavailable as exc:
            unavailable_lenses.append(name)
            degraded_reasons.append(f"model_unavailable:{name}")
            run.lens_results.append(
                LensResult(
                    lens=name,
                    status=LensStatus.SKIPPED_POLICY,
                    error=str(exc),
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
            )
        except Exception as exc:  # noqa: BLE001 - isolated by design (FR-009)
            duration_ms = int((time.monotonic() - started) * 1000)
            reason = classify_failure(exc)
            logger.warning("lens %s failed (%s): %s", name, reason, exc)
            run.lens_results.append(
                LensResult(lens=name, status=LensStatus.FAILED, error=f"{reason}: {exc}", duration_ms=duration_ms)
            )
            executed_lenses.append(name)
            degraded_reasons.append(f"{reason}:{name}")
            consecutive_failures += 1
            stop = evaluate_consecutive_failures(consecutive_failures, limit=MAX_CONSECUTIVE_FAILURES)
            if stop.should_stop:
                degraded_reasons.append(stop.reason)
                break
        finally:
            budget.mark_check()

    return LensExecutionOutcome(
        findings=findings,
        degraded_reasons=degraded_reasons,
        executed_lenses=executed_lenses,
        unavailable_lenses=unavailable_lenses,
        scope_notes=scope_notes,
    )
