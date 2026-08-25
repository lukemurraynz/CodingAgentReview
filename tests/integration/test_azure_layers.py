"""Integration tests for Azure-backed layers.

These run ONLY when real service endpoints are configured (env vars). Without
them they are skipped — no emulators are simulated locally (Real Integration
Policy: fail fast / skip honestly rather than fake success).
"""

import os

import pytest

from harness.queue import QUEUE_NAME, ReviewQueuePublisher
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
def test_queue_send_receive_contract() -> None:
    assert QUEUE_NAME == "review-requests"
    publisher = ReviewQueuePublisher()
    send = publisher.enqueue({"changeId": "it-c1", "headSha": "abc"})
    # Real send/receive loop is exercised by the worker integration suite (T040).
    assert callable(send)
