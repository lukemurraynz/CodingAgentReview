import pytest

from harness.events import (
    EventEmitter,
    EventType,
    FindingCreatedData,
    FindingResolvedData,
    ReviewDegradedData,
)


@pytest.mark.asyncio
async def test_finding_created_payload_matches_model_shape() -> None:
    captured = []
    emitter = EventEmitter(lambda event: captured.append(event))

    payload = FindingCreatedData(
        findingId="f1",
        changeId="c1",
        repoId="org/repo",
        dedupKey="v1:x",
        status="confirmed",
    )
    event = await emitter.finding_created(source="test", subject="repos/org/repo/findings/f1", payload=payload)

    assert event.event_type == EventType.FINDING_CREATED
    assert captured[0].data == payload.model_dump(mode="json", by_alias=True)


@pytest.mark.asyncio
async def test_review_degraded_payload_matches_model_shape() -> None:
    captured = []
    emitter = EventEmitter(lambda event: captured.append(event))

    payload = ReviewDegradedData(runId="r1", changeId="c1", headSha="abc", reasons=["budget"])
    event = await emitter.review_degraded(source="test", subject="runs/r1", payload=payload)

    assert event.event_type == EventType.REVIEW_DEGRADED
    assert captured[0].data == payload.model_dump(mode="json", by_alias=True)


@pytest.mark.asyncio
async def test_finding_resolved_payload_matches_model_shape() -> None:
    captured = []
    emitter = EventEmitter(lambda event: captured.append(event))

    payload = FindingResolvedData(
        findingId="f1",
        changeId="c1",
        repoId="org/repo",
        dedupKey="v1:x",
        previousStatus="confirmed",
        resolution="stale",
    )
    event = await emitter.finding_resolved(source="test", subject="repos/org/repo/findings/f1", payload=payload)

    assert event.event_type == EventType.FINDING_RESOLVED
    assert captured[0].data == payload.model_dump(mode="json", by_alias=True)
