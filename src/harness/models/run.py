"""ReviewRun aggregate: one pipeline execution against a Change."""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    DEGRADED = "degraded"  # partial coverage, explicitly reported (FR-035)
    FAILED = "failed"
    SUPERSEDED = "superseded"  # newer head SHA arrived mid-run


class LensStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"  # isolated failure; must not corrupt the run (FR-009)
    SKIPPED_BUDGET = "skipped_budget"
    SKIPPED_POLICY = "skipped_policy"


class LensResult(BaseModel):
    lens: str = Field(min_length=1)
    status: LensStatus
    findings_count: int = 0
    error: str | None = None
    duration_ms: int | None = None

    @model_validator(mode="after")
    def _failed_requires_error(self) -> "LensResult":
        if self.status == LensStatus.FAILED and not self.error:
            raise ValueError("failed lens results must record their error")
        return self


class ReviewRun(BaseModel):
    """Coverage is full or explicitly degraded — never silently truncated (FR-035)."""

    id: str = Field(min_length=1)
    change_id: str = Field(min_length=1)
    head_sha: str = Field(min_length=1)
    status: RunStatus = RunStatus.QUEUED
    lens_results: list[LensResult] = Field(default_factory=list)
    coverage: str = "full"  # "full" | "partial_explicit"
    degraded_reasons: list[str] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    compute_ms: int = 0
    started_at: datetime = Field(default_factory=_utcnow)
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def _degraded_requires_reasons(self) -> "ReviewRun":
        if self.status == RunStatus.DEGRADED and not self.degraded_reasons:
            raise ValueError("degraded runs must record explicit reasons")
        if self.coverage == "full" and self.degraded_reasons:
            raise ValueError("degraded_reasons require coverage='partial_explicit'")
        if self.coverage == "partial_explicit" and self.status not in (
            RunStatus.DEGRADED,
            RunStatus.COMPLETED,
        ):
            raise ValueError("partial coverage requires a DEGRADED/COMPLETED run")
        return self
