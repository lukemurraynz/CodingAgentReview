"""Persistence helpers: run/finding state store operations (FR-006)."""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Protocol

from harness.models import Finding, ReviewRun, RunStatus
from providers.base import PROMPT_VERSION
from worker.exploitability import metadata as exploitability_metadata
from worker.suppression import not_flagged as finding_not_flagged
from worker.suppression import reported_by as finding_reported_by

logger = logging.getLogger("worker.persistence")


class RunRepository(Protocol):
    async def put_run(self, run: ReviewRun) -> None: ...

    async def put_finding(self, finding: Finding) -> None: ...

    async def query_runs_for_change(self, change_id: str) -> list[dict[str, object]]: ...


def make_repository() -> RunRepository | None:
    """Build the concrete repository when Cosmos is configured."""
    if not os.environ.get("HARNESS_COSMOS_ENDPOINT"):
        return None
    from harness.repository import HarnessRepository

    return HarnessRepository(database=os.environ.get("HARNESS_COSMOS_DATABASE", "harness"))


async def persist(
    run: ReviewRun,
    findings: list[Finding],
    *,
    repository: RunRepository | None = None,
) -> None:
    """Persist run + findings. Skips honestly when no state store configured."""
    if repository is None:
        logger.warning("no HARNESS_COSMOS_ENDPOINT — run state NOT persisted")
        return
    from harness.redaction import redact_text

    for f in findings:
        f.title, _ = redact_text(f.title)
        f.detail, _ = redact_text(f.detail or "")
    await repository.put_run(run)
    await _persist_run_metadata(repository, run, findings)
    for f in findings:
        await repository.put_finding(f)


async def mark_superseded_runs(
    repository: RunRepository | None,
    *,
    change_id: str,
    current_head_sha: str,
) -> None:
    """Mark older runs for the same change as superseded."""
    if repository is None:
        return
    for raw_run in await _load_runs_for_change(repository, change_id):
        prior_run = ReviewRun.model_validate(raw_run)
        if prior_run.head_sha == current_head_sha or prior_run.status == RunStatus.SUPERSEDED:
            continue
        prior_run.status = RunStatus.SUPERSEDED
        prior_run.completed_at = prior_run.completed_at or datetime.now(UTC)
        await repository.put_run(prior_run)


async def _load_runs_for_change(repository: RunRepository, change_id: str) -> list[dict[str, object]]:
    runs = await repository.query_runs_for_change(change_id)
    if runs:
        return runs
    session_factory = getattr(repository, "_session", None)
    if session_factory is None:
        return []
    async with session_factory() as (_, run_container):
        return [
            item
            async for item in run_container.query_items(
                query="SELECT * FROM c WHERE c.change_id = @cid",
                parameters=[{"name": "@cid", "value": change_id}],
                partition_key=change_id,
            )
        ]


def _run_metadata(run: ReviewRun, findings: list[Finding]) -> dict[str, object]:
    metadata = getattr(run, "_review_metadata", {})
    prompt_version = metadata.get("prompt_version", PROMPT_VERSION) if isinstance(metadata, dict) else PROMPT_VERSION
    return {
        "prompt_version": prompt_version,
        "lens_versions": _lens_versions(run),
        "declared_lenses": _declared_lenses(run),
        "executed_lenses": _executed_lenses(run),
        "reported_lenses": _reported_lenses(run),
        "unavailable_lenses": _unavailable_lenses(run),
        "not_flagged": _not_flagged(findings),
        "finding_sources": _finding_sources(findings),
        "exploitability": exploitability_metadata(findings),
    }


def _lens_versions(run: ReviewRun) -> dict[str, str]:
    metadata = getattr(run, "_review_metadata", {})
    lens_versions = metadata.get("lens_versions", {}) if isinstance(metadata, dict) else {}
    return dict(lens_versions) if isinstance(lens_versions, dict) else {}


def _declared_lenses(run: ReviewRun) -> list[str]:
    metadata = getattr(run, "_review_metadata", {})
    declared = metadata.get("declared_lenses", ()) if isinstance(metadata, dict) else ()
    return list(declared) if isinstance(declared, (list, tuple)) else []


def _executed_lenses(run: ReviewRun) -> list[str]:
    metadata = getattr(run, "_review_metadata", {})
    executed = metadata.get("executed_lenses", ()) if isinstance(metadata, dict) else ()
    if isinstance(executed, (list, tuple)):
        return list(executed)
    return [result.lens for result in run.lens_results]


def _reported_lenses(run: ReviewRun) -> list[str]:
    metadata = getattr(run, "_review_metadata", {})
    executed = metadata.get("executed_lenses", ()) if isinstance(metadata, dict) else ()
    unavailable = metadata.get("unavailable_lenses", ()) if isinstance(metadata, dict) else ()
    if isinstance(executed, (list, tuple)) and isinstance(unavailable, (list, tuple)):
        return list(dict.fromkeys([*executed, *unavailable]))
    return [result.lens for result in run.lens_results]


def _unavailable_lenses(run: ReviewRun) -> list[str]:
    metadata = getattr(run, "_review_metadata", {})
    unavailable = metadata.get("unavailable_lenses", ()) if isinstance(metadata, dict) else ()
    if isinstance(unavailable, (list, tuple)):
        return list(unavailable)
    return [result.lens for result in run.lens_results if result.status.name == "SKIPPED_POLICY"]


def _not_flagged(findings: list[Finding]) -> list[str]:
    notes: list[str] = []
    for finding in findings:
        notes.extend(str(note) for note in finding_not_flagged(finding))
    return list(dict.fromkeys(notes))


def _finding_sources(findings: list[Finding]) -> dict[str, list[str]]:
    sources: dict[str, list[str]] = {}
    for finding in findings:
        reported_by = finding_reported_by(finding)
        if reported_by:
            sources[finding.id] = list(reported_by)
    return sources


async def _persist_run_metadata(repository: RunRepository, run: ReviewRun, findings: list[Finding]) -> None:
    metadata = _run_metadata(run, findings)
    put_metadata = getattr(repository, "put_run_metadata", None)
    if callable(put_metadata):
        result = put_metadata(run.id, metadata)
        if isinstance(result, Awaitable):
            await result
            return
    session_factory = getattr(repository, "_session", None)
    if session_factory is None:
        return
    async with session_factory() as (_, run_container):
        stored = await run_container.read_item(item=run.id, partition_key=run.change_id)
        stored["review_metadata"] = metadata
        await run_container.upsert_item(stored)
