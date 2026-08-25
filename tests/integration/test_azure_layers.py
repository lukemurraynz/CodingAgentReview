"""Integration tests for Azure-backed layers.

These run ONLY when real service endpoints are configured (env vars). Without
them they are skipped — no emulators are simulated locally (Real Integration
Policy: fail fast / skip honestly rather than fake success).
"""

import asyncio
import os

import pytest

from harness.queue import QUEUE_NAME, ReviewQueueConsumer, ReviewQueuePublisher
from harness.repository import HarnessRepository

has_servicebus = bool(os.environ.get("HARNESS_SERVICEBUS_NS"))
has_cosmos = bool(os.environ.get("HARNESS_COSMOS_ENDPOINT"))


@pytest.mark.skipif(not has_cosmos, reason="HARNESS_COSMOS_ENDPOINT not configured")
async def test_finding_round_trip() -> None:
    from harness.models import Evidence, Finding, FindingCategory, Severity

    repo = HarnessRepository()
    finding = Finding(
        id="it-f1",
        change_id="it-c1",
        repo_id="org/it-repo",
        category=FindingCategory.CORRECTNESS,
        severity=Severity.MEDIUM,
        title="integration seeded finding",
        evidence=[Evidence(path="src/app.py", line_start=1)],
        dedup_key="v1:integration",
    )
    await repo.put_finding(finding)
    loaded = await repo.get_findings("org/it-repo")
    assert any(f.id == "it-f1" for f in loaded)


@pytest.mark.skipif(not has_servicebus, reason="HARNESS_SERVICEBUS_NS not configured")
def test_queue_send_receive_round_trip() -> None:
    """T013 live proof: a message we send is received intact and completed (at-least-once)."""
    assert QUEUE_NAME == "review-requests"
    received: list[object] = []

    async def handler(payload: object) -> None:
        received.append(payload)

    async def scenario() -> None:
        consumer = ReviewQueueConsumer()
        loop = asyncio.create_task(consumer.receive(handler, drain_seconds=25))
        await asyncio.sleep(3)  # let the receiver connect before sending
        await ReviewQueuePublisher().enqueue({"changeId": "it-c1", "headSha": "abc"})
        for _ in range(30):
            if received:
                break
            await asyncio.sleep(0.5)
        loop.cancel()
        await asyncio.gather(loop, return_exceptions=True)

    asyncio.run(scenario())
    assert any(isinstance(p, dict) and p.get("changeId") == "it-c1" for p in received)
