"""Per-review cost accounting with explicit degradation (FR-035).

Budgets are configured per deployment; exhausting one marks the run degraded
with explicit reasons — never silent truncation.
"""

from enum import StrEnum

from harness.models.run import RunStatus


class BudgetExhausted(RuntimeError):
    pass


class DegradedReason(StrEnum):
    BUDGET_EXHAUSTED = "budget_exhausted"
    LENS_FAILED = "lens_failed"
    DIFF_TOO_LARGE = "diff_too_large"
    MODEL_UNAVAILABLE = "model_unavailable"


class RunBudget:
    """Token/compute ledger for a single ReviewRun.

    Stop-condition semantics ported from Claude Code's tokenBudget.ts:
    - diminishing returns: >=3 continuation checks with the last two
      per-check deltas each below DIMINISHING_THRESHOLD tokens
    - soft landing: past COMPLETION_PCT the caller should nudge wrap-up,
      not hard-cut.
    """

    COMPLETION_PCT = 0.9
    DIMINISHING_THRESHOLD_TOKENS = 500
    DIMINISHING_MIN_CHECKS = 3

    def __init__(self, max_input_tokens: int, max_output_tokens: int, max_compute_ms: int) -> None:
        self._limits = (max_input_tokens, max_output_tokens, max_compute_ms)
        self.input_tokens = 0
        self.output_tokens = 0
        self.compute_ms = 0
        self.exhausted = False
        self._checks = 0
        self._last_delta = 0
        self._prev_delta = 0

    def record(self, *, input_tokens: int = 0, output_tokens: int = 0, compute_ms: int = 0) -> None:
        prev_total = self.input_tokens + self.output_tokens
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.compute_ms += compute_ms
        actuals = (self.input_tokens, self.output_tokens, self.compute_ms)
        if any(a > limit for a, limit in zip(actuals, self._limits, strict=True)):
            self.exhausted = True
        new_total = self.input_tokens + self.output_tokens
        self._prev_delta, self._last_delta = self._last_delta, new_total - prev_total

    def is_diminishing(self) -> bool:
        """No-progress detection: enough checks and two consecutive tiny deltas."""
        return (
            self._checks >= self.DIMINISHING_MIN_CHECKS
            and self._last_delta < self.DIMINISHING_THRESHOLD_TOKENS
            and self._prev_delta < self.DIMINISHING_THRESHOLD_TOKENS
        )

    def mark_check(self) -> None:
        self._checks += 1

    def pct_used(self) -> float:
        """Headline usage vs the input-token budget (matches nudge semantics)."""
        return self.input_tokens / max(self._limits[0], 1)

    def nudge_message(self) -> str | None:
        """Soft-landing directive once past the completion threshold."""
        if not self.exhausted and self.pct_used() >= self.COMPLETION_PCT:
            return (
                f"Budget {int(self.pct_used() * 100)}% used "
                f"({self.input_tokens + self.output_tokens} tokens). Wrap up: finish "
                "current lens and report partial coverage explicitly."
            )
        return None

    def ensure_available(self) -> None:
        if self.exhausted:
            raise BudgetExhausted("review budget exhausted; remaining lenses must be skipped")

    def apply_to(
        self,
        run_status: RunStatus,
        degraded_reasons: list[str],
        coverage: str,
    ) -> tuple[RunStatus, list[str], str]:
        """Fold budget state into run outcome. Degradation is always explicit."""
        reasons = list(degraded_reasons)
        coverage_out = coverage
        status_out = run_status
        if self.exhausted:
            if DegradedReason.BUDGET_EXHAUSTED.value not in reasons:
                reasons.append(DegradedReason.BUDGET_EXHAUSTED.value)
            coverage_out = "partial_explicit"
            if status_out == RunStatus.COMPLETED:
                status_out = RunStatus.DEGRADED
        return status_out, reasons, coverage_out
