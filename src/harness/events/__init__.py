"""Canonical engineering events (FR-021/022).

Schema-versioned envelopes on a durable event stream. The shape is designed so a
future continuous-query consumer (Drasi-class) can subscribe without redesign.
Publishing transport lives in adapters; this module owns the contract only.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


class EventType(StrEnum):
    PULL_REQUEST_CHANGED = "PullRequestChanged"
    COMMIT_CREATED = "CommitCreated"
    REVIEW_RECEIVED = "ReviewReceived"
    REVIEW_COMPLETED = "ReviewCompleted"
    REVIEW_DEGRADED = "ReviewDegraded"
    BUILD_COMPLETED = "BuildCompleted"
    TEST_COMPLETED = "TestCompleted"
    DEPLOYMENT_COMPLETED = "DeploymentCompleted"
    FINDING_CREATED = "FindingCreated"
    FINDING_RESOLVED = "FindingResolved"
    FINDING_REOPENED = "FindingReopened"
    FINDING_WAIVED = "FindingWaived"
    RUNTIME_SIGNAL_OBSERVED = "RuntimeSignalObserved"
    INCIDENT_OPENED = "IncidentOpened"
    SPECIFICATION_CHANGED = "SpecificationChanged"


SCHEMA_VERSION = 1


class CanonicalEvent(BaseModel):
    """Versioned envelope. ``data`` carries the event-type-specific payload."""

    id: str = Field(min_length=1)  # unique event id (uuid-style string)
    event_type: EventType
    schema_version: int = SCHEMA_VERSION
    source: str = Field(min_length=1)  # emitting component, e.g. "controlplane.webhooks"
    subject: str = Field(min_length=1)  # primary entity ref, e.g. "repos/foo/pulls/12"
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    data: dict[str, object] = Field(default_factory=dict)


class PullRequestChangedData(BaseModel):
    change_id: str = Field(min_length=1, alias="changeId")
    head_sha: str = Field(min_length=1, alias="headSha")
    base_sha: str | None = Field(default=None, alias="baseSha")
    repo_id: str = Field(min_length=1, alias="repoId")
    provider: str = Field(min_length=1)
    pr_number: int | None = Field(default=None, alias="prNumber")
    classification: str = Field(min_length=1)


class CommitCreatedData(BaseModel):
    change_id: str = Field(min_length=1, alias="changeId")
    head_sha: str = Field(min_length=1, alias="headSha")
    base_sha: str | None = Field(default=None, alias="baseSha")
    repo_id: str = Field(min_length=1, alias="repoId")
    provider: str = Field(min_length=1)
    classification: str = Field(min_length=1)


class ReviewReceivedData(BaseModel):
    change_id: str = Field(min_length=1, alias="changeId")
    repo_id: str = Field(min_length=1, alias="repoId")
    head_sha: str = Field(min_length=1, alias="headSha")
    provider: str = Field(min_length=1)


class ReviewCompletedData(BaseModel):
    run_id: str = Field(min_length=1, alias="runId")
    change_id: str = Field(min_length=1, alias="changeId")
    head_sha: str = Field(min_length=1, alias="headSha")
    findings_count: int = Field(ge=0, alias="findingsCount")


class ReviewDegradedData(BaseModel):
    run_id: str = Field(min_length=1, alias="runId")
    change_id: str = Field(min_length=1, alias="changeId")
    head_sha: str = Field(min_length=1, alias="headSha")
    reasons: list[str] = Field(min_length=1)


class FindingCreatedData(BaseModel):
    finding_id: str = Field(min_length=1, alias="findingId")
    change_id: str = Field(min_length=1, alias="changeId")
    repo_id: str = Field(min_length=1, alias="repoId")
    dedup_key: str = Field(min_length=1, alias="dedupKey")
    status: str = Field(min_length=1)


class FindingResolvedData(BaseModel):
    finding_id: str = Field(min_length=1, alias="findingId")
    change_id: str = Field(min_length=1, alias="changeId")
    repo_id: str = Field(min_length=1, alias="repoId")
    dedup_key: str = Field(min_length=1, alias="dedupKey")
    previous_status: str = Field(min_length=1, alias="previousStatus")
    resolution: str = Field(min_length=1)


class FindingReopenedData(BaseModel):
    finding_id: str = Field(min_length=1, alias="findingId")
    change_id: str = Field(min_length=1, alias="changeId")
    repo_id: str = Field(min_length=1, alias="repoId")
    dedup_key: str = Field(min_length=1, alias="dedupKey")
    reopened_from: str = Field(min_length=1, alias="reopenedFrom")


class FindingWaivedData(BaseModel):
    finding_id: str = Field(min_length=1, alias="findingId")
    change_id: str = Field(min_length=1, alias="changeId")
    repo_id: str = Field(min_length=1, alias="repoId")
    dedup_key: str = Field(min_length=1, alias="dedupKey")
    approver: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    decided_at: datetime = Field(alias="decidedAt")


SendEvent = Callable[[CanonicalEvent], Awaitable[None] | None]


class EventEmitter:
    def __init__(self, send: SendEvent) -> None:
        self._send = send

    async def _emit(self, *, event_type: EventType, source: str, subject: str, payload: BaseModel) -> CanonicalEvent:
        event = CanonicalEvent(
            id=str(uuid4()),
            event_type=event_type,
            source=source,
            subject=subject,
            data=payload.model_dump(mode="json", by_alias=True),
        )
        sent = self._send(event)
        if sent is not None:
            await sent
        return event

    async def pull_request_changed(
        self, *, source: str, subject: str, payload: PullRequestChangedData
    ) -> CanonicalEvent:
        return await self._emit(
            event_type=EventType.PULL_REQUEST_CHANGED,
            source=source,
            subject=subject,
            payload=payload,
        )

    async def commit_created(self, *, source: str, subject: str, payload: CommitCreatedData) -> CanonicalEvent:
        return await self._emit(
            event_type=EventType.COMMIT_CREATED,
            source=source,
            subject=subject,
            payload=payload,
        )

    async def review_received(self, *, source: str, subject: str, payload: ReviewReceivedData) -> CanonicalEvent:
        return await self._emit(
            event_type=EventType.REVIEW_RECEIVED,
            source=source,
            subject=subject,
            payload=payload,
        )

    async def review_completed(self, *, source: str, subject: str, payload: ReviewCompletedData) -> CanonicalEvent:
        return await self._emit(
            event_type=EventType.REVIEW_COMPLETED,
            source=source,
            subject=subject,
            payload=payload,
        )

    async def review_degraded(self, *, source: str, subject: str, payload: ReviewDegradedData) -> CanonicalEvent:
        return await self._emit(
            event_type=EventType.REVIEW_DEGRADED,
            source=source,
            subject=subject,
            payload=payload,
        )

    async def finding_created(self, *, source: str, subject: str, payload: FindingCreatedData) -> CanonicalEvent:
        return await self._emit(
            event_type=EventType.FINDING_CREATED,
            source=source,
            subject=subject,
            payload=payload,
        )

    async def finding_resolved(self, *, source: str, subject: str, payload: FindingResolvedData) -> CanonicalEvent:
        return await self._emit(
            event_type=EventType.FINDING_RESOLVED,
            source=source,
            subject=subject,
            payload=payload,
        )

    async def finding_reopened(self, *, source: str, subject: str, payload: FindingReopenedData) -> CanonicalEvent:
        return await self._emit(
            event_type=EventType.FINDING_REOPENED,
            source=source,
            subject=subject,
            payload=payload,
        )

    async def finding_waived(self, *, source: str, subject: str, payload: FindingWaivedData) -> CanonicalEvent:
        return await self._emit(
            event_type=EventType.FINDING_WAIVED,
            source=source,
            subject=subject,
            payload=payload,
        )


def export_json_schema() -> dict[str, object]:
    """JSON Schema for the envelope contract (contracts/events.v1.schema.json)."""
    schema: dict[str, object] = CanonicalEvent.model_json_schema()
    schema["description"] = f"Canonical engineering event envelope v{SCHEMA_VERSION}"
    return schema


__all__ = [
    "CanonicalEvent",
    "CommitCreatedData",
    "EventEmitter",
    "EventType",
    "FindingCreatedData",
    "FindingReopenedData",
    "FindingResolvedData",
    "FindingWaivedData",
    "PullRequestChangedData",
    "ReviewCompletedData",
    "ReviewDegradedData",
    "ReviewReceivedData",
    "SCHEMA_VERSION",
    "export_json_schema",
]
