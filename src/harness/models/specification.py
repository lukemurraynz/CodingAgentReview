"""Centrally governed specifications and mechanically-checkable invariants."""

from enum import StrEnum

from pydantic import BaseModel, Field


class Invariant(BaseModel):
    id: str = Field(min_length=1)
    description: str = ""
    applies_to: list[str] = Field(min_length=1)  # resource types / scopes


class Specification(BaseModel):
    id: str = Field(min_length=1)
    version: int = 1
    title: str = Field(min_length=1)
    content: str = ""
    invariants: list[Invariant] = Field(default_factory=list)


class LensName(StrEnum):
    CORRECTNESS = "correctness"
    SECURITY = "security"
    ARCHITECTURE = "architecture"
    TEST_QUALITY = "test_quality"
    STRUCTURAL = "structural"
    PRODUCTION_VALIDATION = "production_validation"
