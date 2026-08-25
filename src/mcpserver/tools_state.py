"""Engineering-state query tools for the MCP host."""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass
from importlib import import_module
from typing import Any

from harness.cosmos_state import latest_risk_for_change
from harness.models import Finding
from harness.repository import HarnessRepository


@dataclass(frozen=True, slots=True)
class RelatedAnchor:
    repo_id: str
    change_id: str
    finding_ids: tuple[str, ...]
    dedup_keys: frozenset[str]
    files: frozenset[str]


class StateQueryService:
    def __init__(self, *, repository: HarnessRepository | None = None) -> None:
        database = os.environ.get("HARNESS_COSMOS_DATABASE", "harness")
        self._repository = repository or HarnessRepository(database=database)

    async def get_active_findings(self, repo_id: str) -> list[dict[str, object]]:
        findings = await self._repository.get_findings(repo_id)
        active = [finding for finding in findings if finding.status in {"candidate", "confirmed", "reopened"}]
        return [
            {
                "id": finding.id,
                "changeId": finding.change_id,
                "category": finding.category.value,
                "severity": finding.severity.value,
                "title": finding.title,
                "status": finding.status,
            }
            for finding in active
        ]

    async def get_risk_explanation(self, change_id: str) -> dict[str, object]:
        assessment = await latest_risk_for_change(change_id)
        if assessment is None:
            return {"changeId": change_id, "risk": "unknown", "reasons": ["no risk assessment recorded"]}
        return {
            "changeId": change_id,
            "repoId": assessment.repo_id,
            "risk": assessment.level.value,
            "reasons": [f"{signal.name}: {signal.level.value}" for signal in assessment.signals],
        }

    async def repo_id_for_change(self, change_id: str) -> str | None:
        matches = await self._query_findings(
            "SELECT VALUE c.repo_id FROM c WHERE c.change_id = @value OFFSET 0 LIMIT 1",
            [{"name": "@value", "value": change_id}],
        )
        if matches and isinstance(matches[0], str):
            return matches[0]
        assessment = await latest_risk_for_change(change_id)
        return None if assessment is None else assessment.repo_id

    async def repo_id_for_finding(self, finding_id: str) -> str | None:
        matches = await self._query_findings(
            "SELECT VALUE c.repo_id FROM c WHERE c.id = @value OFFSET 0 LIMIT 1",
            [{"name": "@value", "value": finding_id}],
        )
        if matches and isinstance(matches[0], str):
            return matches[0]
        return None

    async def get_related_changes(
        self,
        *,
        change_id: str | None = None,
        finding_id: str | None = None,
    ) -> list[dict[str, object]]:
        if bool(change_id) == bool(finding_id):
            raise ValueError("provide exactly one of change_id or finding_id")
        anchor = await self._load_anchor(change_id=change_id, finding_id=finding_id)
        if anchor is None:
            return []
        findings = await self._repository.get_findings(anchor.repo_id)
        grouped: dict[str, list[Finding]] = defaultdict(list)
        for finding in findings:
            if finding.change_id == anchor.change_id:
                continue
            if finding.status not in {"candidate", "confirmed", "reopened", "resolved"}:
                continue
            grouped[finding.change_id].append(finding)

        related: list[dict[str, object]] = []
        for related_change_id, group in grouped.items():
            shared_dedup = sorted({finding.dedup_key for finding in group if finding.dedup_key in anchor.dedup_keys})
            overlapping_files = sorted(
                {
                    evidence.path
                    for finding in group
                    for evidence in finding.evidence
                    if evidence.path in anchor.files
                }
            )
            if not shared_dedup and not overlapping_files:
                continue
            related.append(
                {
                    "changeId": related_change_id,
                    "repoId": anchor.repo_id,
                    "findingIds": sorted(finding.id for finding in group),
                    "sharedDedupeKeys": shared_dedup,
                    "overlappingFiles": overlapping_files,
                }
            )
        return sorted(related, key=lambda item: str(item["changeId"]))

    async def _load_anchor(self, *, change_id: str | None, finding_id: str | None) -> RelatedAnchor | None:
        if change_id:
            rows = await self._query_findings(
                "SELECT * FROM c WHERE c.change_id = @value",
                [{"name": "@value", "value": change_id}],
            )
        else:
            rows = await self._query_findings(
                "SELECT * FROM c WHERE c.id = @value",
                [{"name": "@value", "value": finding_id}],
            )
        findings = [Finding.model_validate(row) for row in rows if isinstance(row, dict)]
        if not findings:
            return None
        return RelatedAnchor(
            repo_id=findings[0].repo_id,
            change_id=findings[0].change_id,
            finding_ids=tuple(sorted(finding.id for finding in findings)),
            dedup_keys=frozenset(finding.dedup_key for finding in findings),
            files=frozenset(evidence.path for finding in findings for evidence in finding.evidence),
        )

    async def _query_findings(self, query: str, parameters: list[dict[str, Any]]) -> list[Any]:
        if not os.environ.get("HARNESS_COSMOS_ENDPOINT"):
            return []
        cosmos_module = import_module("azure.cosmos.aio")
        identity_module = import_module("azure.identity.aio")

        endpoint = os.environ["HARNESS_COSMOS_ENDPOINT"]
        database = os.environ.get("HARNESS_COSMOS_DATABASE", "harness")
        client = cosmos_module.CosmosClient(
            endpoint,
            credential=identity_module.DefaultAzureCredential(),
        )
        try:
            container = client.get_database_client(database).get_container_client("findings")
            iterator = container.query_items(
                query=query,
                parameters=parameters,
                enable_cross_partition_query=True,
            )
            return [item async for item in iterator]
        finally:
            await client.close()
