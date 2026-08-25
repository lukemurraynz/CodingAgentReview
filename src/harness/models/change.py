"""Change under review and its classification."""

from datetime import UTC, datetime

from pydantic import BaseModel, Field, model_validator

from .enums import ChangeClassification, ChangeType, GitProvider


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Change(BaseModel):
    id: str = Field(min_length=1)
    provider: GitProvider
    repo_id: str = Field(min_length=1)
    change_type: ChangeType
    head_sha: str = Field(min_length=1)
    base_sha: str | None = None
    pr_number: int | None = None
    changed_files: list[str] = Field(default_factory=list)
    classification: ChangeClassification = ChangeClassification.CODE
    created_at: datetime = Field(default_factory=_utcnow)

    @model_validator(mode="after")
    def _pr_fields_consistent(self) -> "Change":
        if self.change_type == ChangeType.PULL_REQUEST:
            if self.pr_number is None or self.base_sha is None:
                raise ValueError("pull-request changes require pr_number and base_sha")
        return self
