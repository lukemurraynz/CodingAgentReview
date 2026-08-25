"""Cosmos-backed state queries shared by MCP state tools."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harness.models import RiskAssessment


async def latest_risk_for_change(change_id: str) -> RiskAssessment | None:
    """Most recent RiskAssessment recorded for a change, if any."""
    if not os.environ.get("HARNESS_COSMOS_ENDPOINT"):
        return None
    from azure.cosmos.aio import CosmosClient
    from azure.identity.aio import DefaultAzureCredential

    endpoint = os.environ["HARNESS_COSMOS_ENDPOINT"]
    database = os.environ.get("HARNESS_COSMOS_DATABASE", "harness")
    client = CosmosClient(endpoint, credential=DefaultAzureCredential())
    try:
        container = client.get_database_client(database).get_container_client("changes")
        items = container.query_items(
            query="SELECT * FROM c WHERE c.changeId = @cid ORDER BY c.computedAt DESC OFFSET 0 LIMIT 1",
            parameters=[{"name": "@cid", "value": change_id}],
            enable_cross_partition_query=True,
        )
        async for item in items:
            from harness.models import RiskAssessment

            return RiskAssessment.model_validate(item)
        return None
    finally:
        await client.close()
