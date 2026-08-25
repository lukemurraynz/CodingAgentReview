"""Canonical engineering events (FR-021/022).

Schema-versioned envelopes on a durable event stream. The shape is designed so a
future continuous-query consumer (Drasi-class) can subscribe without redesign.
Publishing transport lives in adapters; this module owns the contract only.
"""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class EventType(StrEnum):
    PULL_REQUEST_CHANGED = "PullRequestChanged"
    COMMIT_CREATED = "CommitCreated"
    BUILD_COMPLETED = "BuildCompleted"
    TEST_COMPLETED = "TestCompleted"
    DEPLOYMENT_COMPLETED = "DeploymentCompleted"
    FINDING_CREATED = "FindingCreated"
    FINDING_RESOLVED = "FindingResolved"
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


def export_json_schema() -> dict[str, object]:
    """JSON Schema for the envelope contract (contracts/events.v1.schema.json)."""
    schema: dict[str, object] = CanonicalEvent.model_json_schema()
    schema["description"] = f"Canonical engineering event envelope v{SCHEMA_VERSION}"
    return schema
