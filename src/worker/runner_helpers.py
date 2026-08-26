"""Small helpers extracted from the review runner."""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable

from graph import SymbolIndex, build_symbol_index
from harness.models import Change, ReviewMetadata, RiskLevel, RiskSignal
from lenses import lens_not_flagged, lens_version
from providers.azuredevops import LinkedWorkItemCompleteness
from worker.budget import RunBudget
from worker.depth import coerce_risk_signals

logger = logging.getLogger("worker.runner")


def budget_from_env() -> RunBudget:
    return RunBudget(
        max_input_tokens=int(os.environ.get("HARNESS_BUDGET_INPUT_TOKENS", "200000")),
        max_output_tokens=int(os.environ.get("HARNESS_BUDGET_OUTPUT_TOKENS", "20000")),
        max_compute_ms=int(os.environ.get("HARNESS_BUDGET_COMPUTE_MS", "240000")),
    )


def load_symbol_index() -> tuple[SymbolIndex | None, str | None]:
    repo_root = os.environ.get("HARNESS_REPO_ROOT")
    if not repo_root:
        return None, None
    try:
        return build_symbol_index(repo_root), None
    except (OSError, ValueError) as exc:
        logger.warning("symbol index unavailable for %s: %s", repo_root, exc)
        return None, "symbol_index_unavailable"


def risk_signals_from_event(raw: object) -> tuple[RiskSignal, ...]:
    return coerce_risk_signals(raw)


async def fetch_workitem_completeness(adapter: object, change: Change) -> str:
    """Best-effort linked-work-item summary for adapters that expose it."""

    fetcher = getattr(adapter, "fetch_linked_workitem_completeness", None)
    if not callable(fetcher):
        return ""
    try:
        raw_result = fetcher(change)
        if isinstance(raw_result, LinkedWorkItemCompleteness):
            result = raw_result
        elif isinstance(raw_result, Awaitable):
            awaited_result = await raw_result
            if not isinstance(awaited_result, LinkedWorkItemCompleteness):
                return ""
            result = awaited_result
        else:
            return ""
    except Exception as exc:  # noqa: BLE001 - enrichment failure degrades visibly, never blocks review
        logger.warning("work-item completeness fetch failed: %s", exc)
        return ""
    total = len(result.open_states) + (1 if result.complete else 0)
    if total == 0:
        return ""
    done = total - len(result.open_states)
    suffix = "" if result.complete else f" (open: {', '.join(sorted(set(result.open_states)))})"
    return f"Linked work items: {done}/{total} complete{suffix}"


def lens_scope_notes(lens_name: str) -> tuple[str, ...]:
    return lens_not_flagged(lens_name)


def effective_risk_level(risk_floor: RiskLevel, risk_level: RiskLevel) -> RiskLevel:
    order = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2, RiskLevel.CRITICAL: 3}
    return risk_floor if order[risk_floor] >= order[risk_level] else risk_level


def build_review_metadata(
    *,
    declared_lenses: tuple[str, ...],
    executed_lenses: list[str],
    unavailable_lenses: list[str],
    prompt_version: str,
) -> ReviewMetadata:
    return ReviewMetadata(
        declared_lenses=declared_lenses,
        executed_lenses=tuple(dict.fromkeys(executed_lenses)),
        unavailable_lenses=tuple(dict.fromkeys(unavailable_lenses)),
        lens_versions={name: lens_version(name) for name in declared_lenses},
        prompt_version=prompt_version,
    )
