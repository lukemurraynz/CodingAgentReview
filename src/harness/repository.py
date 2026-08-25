"""Cosmos DB repository for findings/runs/risk state (FR-018).

Partition keys carry engagement/repo isolation (spec Assumptions). Health probe
budgets are generous — Cosmos SDK first-call includes endpoint discovery and
token acquisition (known-pitfall: default health-check timeouts break cold start).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any, Protocol

from harness.events import (
    EventEmitter,
    FindingCreatedData,
    FindingReopenedData,
    FindingResolvedData,
)
from harness.lifecycle import apply
from harness.models import Finding, ReviewRun
from harness.redaction import redact_text


def _endpoint() -> str | None:
    return os.environ.get("HARNESS_COSMOS_ENDPOINT")


class WaiverImmutableError(ValueError):
    pass


class FindingStore(Protocol):
    async def put_finding(self, finding: Finding) -> None: ...

    async def get_findings(self, repo_id: str) -> list[Finding]: ...


@dataclass(slots=True)
class ReconcileResult:
    persisted: list[Finding] = field(default_factory=list)
    created: list[Finding] = field(default_factory=list)
    deduped: list[Finding] = field(default_factory=list)
    reopened: list[Finding] = field(default_factory=list)
    stale: list[Finding] = field(default_factory=list)


_ACTIVE_FINDING_STATUSES = {"candidate", "confirmed", "reopened"}


def ensure_waiver_immutable(existing: Finding | None, finding: Finding) -> None:
    if existing is None or existing.waiver is None:
        return
    if finding.waiver is None:
        raise WaiverImmutableError("waiver record is immutable once written")
    if finding.waiver.model_dump(mode="json") != existing.waiver.model_dump(mode="json"):
        raise WaiverImmutableError("waiver record is immutable once written")


def _candidate_for_change(candidate: Finding, *, repo_id: str, change_id: str) -> Finding:
    payload = candidate.model_dump(mode="python") | {"repo_id": repo_id, "change_id": change_id}
    return Finding.model_validate(payload)


def _merge_finding(existing: Finding, candidate: Finding) -> Finding:
    merged = existing.model_dump(mode="python")
    incoming = candidate.model_dump(mode="python")
    for field_name in ("change_id", "repo_id", "category", "severity", "title", "detail", "evidence", "dedup_key"):
        merged[field_name] = incoming[field_name]
    return Finding.model_validate(merged)


def _prefer_existing(a: Finding, b: Finding) -> Finding:
    a_active = a.status in _ACTIVE_FINDING_STATUSES
    b_active = b.status in _ACTIVE_FINDING_STATUSES
    if a_active != b_active:
        return a if a_active else b
    return a if a.updated_at >= b.updated_at else b


def _redact_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    return redact_text(value)[0]


def _sanitize_finding(finding: Finding) -> Finding:
    payload = finding.model_dump(mode="python")
    payload["title"] = redact_text(finding.title)[0]
    payload["detail"] = redact_text(finding.detail)[0]
    payload["evidence"] = [
        evidence.model_copy(update={"note": _redact_optional_text(evidence.note)}).model_dump(mode="python")
        for evidence in finding.evidence
    ]
    return Finding.model_validate(payload)


async def reconcile_findings(
    store: FindingStore,
    *,
    repo_id: str,
    change_id: str,
    candidate_findings: list[Finding],
    emitter: EventEmitter | None = None,
    event_source: str = "harness.repository",
) -> ReconcileResult:
    """Persist a reviewed change against existing findings.

    Incoming findings are review candidates. Reconciliation confirms new findings,
    deduplicates against active findings, reopens previously stale/resolved/waived
    findings when the same dedup key returns, and stales active findings missing
    from the latest reviewed change.
    """
    existing_findings = await store.get_findings(repo_id)
    existing_by_key: dict[str, Finding] = {}
    for finding in existing_findings:
        current = existing_by_key.get(finding.dedup_key)
        existing_by_key[finding.dedup_key] = finding if current is None else _prefer_existing(current, finding)

    deduped_candidates: dict[str, Finding] = {}
    for candidate in candidate_findings:
        normalized = _candidate_for_change(candidate, repo_id=repo_id, change_id=change_id)
        deduped_candidates.setdefault(normalized.dedup_key, normalized)

    seen_keys = set(deduped_candidates)
    result = ReconcileResult()

    for dedup_key, candidate in deduped_candidates.items():
        existing = existing_by_key.get(dedup_key)
        if existing is None:
            confirmed = apply(candidate, "confirmed") if candidate.status == "candidate" else candidate
            await store.put_finding(confirmed)
            result.persisted.append(confirmed)
            result.created.append(confirmed)
            if emitter is not None:
                await emitter.finding_created(
                    source=event_source,
                    subject=f"repos/{repo_id}/findings/{confirmed.id}",
                    payload=FindingCreatedData.model_validate(
                        {
                            "findingId": confirmed.id,
                            "changeId": confirmed.change_id,
                            "repoId": confirmed.repo_id,
                            "dedupKey": confirmed.dedup_key,
                            "status": confirmed.status,
                        }
                    ),
                )
            continue

        if existing.status in _ACTIVE_FINDING_STATUSES:
            current = apply(existing, "confirmed") if existing.status == "candidate" else existing
            merged = _merge_finding(current, candidate)
            await store.put_finding(merged)
            result.persisted.append(merged)
            result.deduped.append(merged)
            continue

        reopened = apply(existing, "reopened", reopened_from=existing.reopened_from or existing.id)
        reopened = _merge_finding(reopened, candidate)
        await store.put_finding(reopened)
        result.persisted.append(reopened)
        result.reopened.append(reopened)
        if emitter is not None:
            await emitter.finding_reopened(
                source=event_source,
                subject=f"repos/{repo_id}/findings/{reopened.id}",
                payload=FindingReopenedData.model_validate(
                    {
                        "findingId": reopened.id,
                        "changeId": reopened.change_id,
                        "repoId": reopened.repo_id,
                        "dedupKey": reopened.dedup_key,
                        "reopenedFrom": reopened.reopened_from or reopened.id,
                    }
                ),
            )

    for existing in existing_findings:
        if existing.status not in _ACTIVE_FINDING_STATUSES:
            continue
        if existing.dedup_key in seen_keys:
            continue
        stale = apply(existing, "stale")
        await store.put_finding(stale)
        result.persisted.append(stale)
        result.stale.append(stale)
        if emitter is not None:
            await emitter.finding_resolved(
                source=event_source,
                subject=f"repos/{repo_id}/findings/{stale.id}",
                payload=FindingResolvedData.model_validate(
                    {
                        "findingId": stale.id,
                        "changeId": stale.change_id,
                        "repoId": stale.repo_id,
                        "dedupKey": stale.dedup_key,
                        "previousStatus": existing.status,
                        "resolution": "stale",
                    }
                ),
            )

    return result


class HarnessRepository:
    """Document repository. Endpoint comes from env; identity from Entra (FR-019)."""

    def __init__(self, database: str = "harness") -> None:
        self.database_name = database

    def _client_kwargs(self) -> dict[str, int]:
        timeout = self.request_timeout_seconds()
        return {"timeout": timeout, "connection_timeout": timeout}

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[tuple[Any, Any]]:
        endpoint = _endpoint()
        if not endpoint:
            raise RuntimeError("HARNESS_COSMOS_ENDPOINT not configured — state store unavailable")
        # Lazy import: azure extra must be installed for this code path.
        cosmos_module = import_module("azure.cosmos.aio")
        identity_module = import_module("azure.identity.aio")

        client = cosmos_module.CosmosClient(
            endpoint,
            credential=identity_module.DefaultAzureCredential(),
            **self._client_kwargs(),
        )
        try:
            db = client.get_database_client(self.database_name)
            yield db.get_container_client("findings"), db.get_container_client("reviewRuns")
        finally:
            await client.close()

    @staticmethod
    def request_timeout_seconds() -> int:
        """Cold-start budget for probes (known-pitfall binding): ≥10s request timeout."""
        return 10

    @staticmethod
    def probe_cancellation_seconds() -> int:
        return 8

    async def healthcheck(self) -> None:
        async with self._session() as (_, runs):
            iterator = runs.query_items(
                query="SELECT VALUE 1 FROM c OFFSET 0 LIMIT 1",
                partition_key="__healthcheck__",
            )
            async for _ in iterator:
                break

    async def get_finding(self, repo_id: str, finding_id: str) -> Finding | None:
        async with self._session() as (findings, _):
            try:
                item = await findings.read_item(item=finding_id, partition_key=repo_id)
            except Exception as exc:  # azure extra stays lazy
                if exc.__class__.__name__ == "CosmosResourceNotFoundError":
                    return None
                raise
            return Finding.model_validate(item)

    async def put_finding(self, finding: Finding) -> None:
        sanitized = _sanitize_finding(finding)
        existing = await self.get_finding(sanitized.repo_id, sanitized.id)
        ensure_waiver_immutable(existing, sanitized)
        async with self._session() as (findings, _):
            await findings.upsert_item(
                sanitized.model_dump(mode="json") | {"id": sanitized.id, "pk": sanitized.repo_id}
            )

    async def get_findings(self, repo_id: str) -> list[Finding]:
        async with self._session() as (findings, _):
            items = findings.read_all_items(partition_key=repo_id)
            return [Finding.model_validate(i) async for i in items]

    async def put_run(self, run: ReviewRun) -> None:
        async with self._session() as (_, runs):
            await runs.upsert_item(
                run.model_dump(mode="json") | {"id": run.id, "pk": run.change_id}
            )

    async def query_runs_for_change(self, change_id: str) -> list[dict[str, Any]]:
        async with self._session() as (_, runs):
            return [
                item
                async for item in runs.query_items(
                    query="SELECT * FROM c WHERE c.changeId = @cid",
                    parameters=[{"name": "@cid", "value": change_id}],
                    partition_key=change_id,
                )
            ]

    async def reconcile_findings(
        self,
        *,
        repo_id: str,
        change_id: str,
        candidate_findings: list[Finding],
        emitter: EventEmitter | None = None,
        event_source: str = "harness.repository",
    ) -> ReconcileResult:
        return await reconcile_findings(
            self,
            repo_id=repo_id,
            change_id=change_id,
            candidate_findings=candidate_findings,
            emitter=emitter,
            event_source=event_source,
        )
