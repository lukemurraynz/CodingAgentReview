"""Finding aggregate: durable issue with lifecycle state and immutable waiver."""

from datetime import UTC, datetime

from pydantic import BaseModel, Field, model_validator

from .enums import FindingCategory, Severity


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Evidence(BaseModel):
    """Grounds a finding in the diff (FR-010). Unsupported assertions are marked as such."""

    path: str
    line_start: int | None = None
    line_end: int | None = None
    rule_id: str | None = None
    metrics: dict[str, float] = Field(default_factory=dict)
    note: str | None = None
    unsupported_assertion: bool = False


class Waiver(BaseModel):
    """Immutable once written (FR-016)."""

    approver: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    decided_at: datetime = Field(default_factory=_utcnow)


class Finding(BaseModel):
    id: str = Field(min_length=1)
    change_id: str = Field(min_length=1)
    repo_id: str = Field(min_length=1)
    category: FindingCategory
    severity: Severity
    title: str = Field(min_length=1)
    detail: str = ""
    evidence: list[Evidence] = Field(min_length=1)
    dedup_key: str = Field(min_length=1)
    status: str = "candidate"  # constrained by harness.lifecycle; string to avoid circular import
    waiver: Waiver | None = None
    reopened_from: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    @model_validator(mode="after")
    def _waiver_status_consistency(self) -> "Finding":
        # A waiver is created when a finding is waived, then retained immutably as
        # audit history if the issue later reopens/resolves/stales.
        if self.status == "waived" and self.waiver is None:
            raise ValueError("waived findings require a waiver record")
        if self.waiver is not None and self.status in {"candidate", "confirmed"}:
            raise ValueError("waiver record is only valid on waived or post-waiver findings")
        return self

    @model_validator(mode="after")
    def _reopen_provenance(self) -> "Finding":
        if self.status == "reopened" and self.reopened_from is None:
            raise ValueError("reopened findings must reference the original finding id")
        if self.status != "reopened":
            self.reopened_from = None
        return self
