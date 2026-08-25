"""Centrally governed specifications, review rules, and mechanically-checkable invariants."""

from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import Severity


class Invariant(BaseModel):
    id: str = Field(min_length=1)
    description: str = ""
    applies_to: list[str] = Field(min_length=1)  # resource types / scopes

    @field_validator("applies_to")
    @classmethod
    def _normalize_applies_to(cls, values: list[str]) -> list[str]:
        return _normalize_tags(values)


class Specification(BaseModel):
    id: str = Field(min_length=1)
    version: int = 1
    title: str = Field(min_length=1)
    content: str = ""
    applies_to: list[str] = Field(default_factory=list)
    path_patterns: list[str] = Field(default_factory=list)
    invariants: list[Invariant] = Field(default_factory=list)

    @field_validator("applies_to", "path_patterns")
    @classmethod
    def _normalize_values(cls, values: list[str]) -> list[str]:
        return _normalize_tags(values)


class LensName(StrEnum):
    CORRECTNESS = "correctness"
    SECURITY = "security"
    ARCHITECTURE = "architecture"
    TEST_QUALITY = "test_quality"
    STRUCTURAL = "structural"
    PRODUCTION_VALIDATION = "production_validation"


_RULE_SEVERITY_ALIASES: Final[dict[str, Severity]] = {
    "blocker": Severity.BLOCKER,
    "major": Severity.HIGH,
    "high": Severity.HIGH,
    "medium": Severity.MEDIUM,
    "minor": Severity.LOW,
    "low": Severity.LOW,
    "info": Severity.INFO,
}


class ReviewRule(BaseModel):
    """Repository-local natural-language review rule resolved into lens briefs."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    severity: Severity
    applies_to: list[str] = Field(min_length=1)
    lens: str = Field(min_length=1)
    instruction: str = Field(min_length=1)

    @field_validator("severity", mode="before")
    @classmethod
    def _normalize_severity(cls, value: Severity | str) -> Severity:
        if isinstance(value, Severity):
            return value
        normalized = str(value).strip().lower()
        severity = _RULE_SEVERITY_ALIASES.get(normalized)
        if severity is None:
            msg = "severity must be one of blocker, major, high, medium, minor, low, info"
            raise ValueError(msg)
        return severity

    @field_validator("applies_to")
    @classmethod
    def _normalize_globs(cls, values: list[str]) -> list[str]:
        normalized = _normalize_rule_globs(values)
        if not any(not value.startswith("!") for value in normalized):
            msg = "at least one non-negated applies_to glob is required"
            raise ValueError(msg)
        return normalized

    @field_validator("lens")
    @classmethod
    def _normalize_lens(cls, value: str) -> str:
        normalized = value.strip().lower()
        allowed = {lens.value for lens in LensName} | {"general"}
        if normalized not in allowed:
            msg = f"lens must be one of {', '.join(sorted(allowed))}"
            raise ValueError(msg)
        return normalized

    @field_validator("instruction")
    @classmethod
    def _normalize_instruction(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("instruction body must not be empty")
        return normalized


def _normalize_tags(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        stripped = value.strip()
        if stripped and stripped not in normalized:
            normalized.append(stripped)
    return normalized


def _normalize_rule_globs(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        stripped = value.strip()
        negated = stripped.startswith("!")
        pattern = stripped[1:] if negated else stripped
        if not pattern:
            raise ValueError("glob pattern must not be empty")
        if "\\" in pattern:
            raise ValueError("glob pattern must use '/' separators")
        normalized_value = f"!{pattern}" if negated else pattern
        if normalized_value not in normalized:
            normalized.append(normalized_value)
    return normalized
