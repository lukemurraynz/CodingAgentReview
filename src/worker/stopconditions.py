"""Stop-condition helpers (FR-025)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

MAX_CONSECUTIVE_FAILURES: Final = 3


@dataclass(frozen=True, slots=True)
class StopDecision:
    should_stop: bool
    reason: str = ""


def evaluate_consecutive_failures(
    consecutive_failures: int,
    *,
    limit: int = MAX_CONSECUTIVE_FAILURES,
) -> StopDecision:
    """Escalate once repeated failures show retrying is pointless."""
    if consecutive_failures < limit:
        return StopDecision(False)
    return StopDecision(True, f"stop_condition:consecutive_failures:{consecutive_failures}")
