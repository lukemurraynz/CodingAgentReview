"""Typed auto-fix session outputs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

type FixStatus = Literal["fixed", "budget_exhausted", "model_declined", "invalid_patch"]
type AttemptStatus = Literal["fixed", "validation_failed", "model_declined", "invalid_patch"]


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Validator outcome for one proposed patch application."""

    passed: bool
    summary: str
    details: str = ""


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    """One bounded model attempt within a fix session."""

    attempt: int
    status: AttemptStatus
    patch_summary: str
    validator_result: ValidationResult | None = None
    note: str = ""


@dataclass(frozen=True, slots=True)
class FixProposal:
    """Final session result with explicit, never-silent termination."""

    iterations: tuple[AttemptRecord, ...] = field(default_factory=tuple)
    status: FixStatus = "model_declined"
    final_diff: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)
