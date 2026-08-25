"""Risk assessment model (FR-011)."""

from datetime import UTC, datetime

from pydantic import BaseModel, Field, model_validator

from .enums import RiskLevel


def _utcnow() -> datetime:
    return datetime.now(UTC)


_LEVEL_ORDER: dict[str, int] = {lv.value: i for i, lv in enumerate(RiskLevel)}


class RiskSignal(BaseModel):
    """One evaluated signal contributing to change risk."""

    name: str = Field(min_length=1)  # e.g. "security_boundary_changed"
    level: RiskLevel
    detail: str = ""


class RiskAssessment(BaseModel):
    """Per-change risk vector with provenance; queryable state, not a log line."""

    repo_id: str = Field(min_length=1)
    change_id: str = Field(min_length=1)
    level: RiskLevel
    signals: list[RiskSignal] = Field(default_factory=list)
    computed_at: datetime = Field(default_factory=_utcnow)

    @model_validator(mode="after")
    def _level_matches_signals(self) -> "RiskAssessment":
        # Level must be derivable from signals: it equals the max signal level.
        if not self.signals:
            return self
        expected = max(self.signals, key=lambda s: _LEVEL_ORDER[s.level.value]).level
        if self.level != expected:
            raise ValueError(f"level {self.level} does not match max signal level {expected}")
        return self
