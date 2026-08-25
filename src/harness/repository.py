"""Cosmos DB repository for findings/runs/risk state (FR-018).

Partition keys carry engagement/repo isolation (spec Assumptions). Health probe
budgets are generous — Cosmos SDK first-call includes endpoint discovery and
token acquisition (known-pitfall: default health-check timeouts break cold start).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from harness.models import Finding, ReviewRun

if TYPE_CHECKING:
    from azure.cosmos.aio import ContainerProxy


def _endpoint() -> str | None:
    return os.environ.get("HARNESS_COSMOS_ENDPOINT")


class HarnessRepository:
    """Document repository. Endpoint comes from env; identity from Entra (FR-019)."""

    def __init__(self, database: str = "harness") -> None:
        self.database_name = database

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[tuple[ContainerProxy, ContainerProxy]]:
        endpoint = _endpoint()
        if not endpoint:
            raise RuntimeError("HARNESS_COSMOS_ENDPOINT not configured — state store unavailable")
        # Lazy import: azure extra must be installed for this code path.
        from azure.cosmos.aio import CosmosClient
        from azure.identity.aio import DefaultAzureCredential

        client = CosmosClient(endpoint, credential=DefaultAzureCredential())
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

    async def put_finding(self, finding: Finding) -> None:
        async with self._session() as (findings, _):
            await findings.upsert_item(
                finding.model_dump(mode="json") | {"id": finding.id, "pk": finding.repo_id}
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
