"""Ported stop-condition semantics (Claude Code tokenBudget.ts -> RunBudget)."""

import pytest

from harness.models import RunStatus
from worker.budget import BudgetExhausted, RunBudget


def _budget() -> RunBudget:
    return RunBudget(max_input_tokens=100_000, max_output_tokens=100_000, max_compute_ms=600_000)


class TestDiminishingReturns:
    def test_not_diminishing_with_only_two_checks(self):
        b = _budget()
        b.record(input_tokens=10)
        b.mark_check()
        b.record(input_tokens=10)
        b.mark_check()
        assert not b.is_diminishing()

    def test_not_diminishing_when_progress_is_made(self):
        b = _budget()
        b.record(input_tokens=2000)
        b.mark_check()
        b.record(input_tokens=3000)
        b.mark_check()
        b.record(input_tokens=3000)
        b.mark_check()
        assert not b.is_diminishing()

    def test_diminishing_after_three_tiny_deltas(self):
        b = _budget()
        b.record(input_tokens=2000)
        b.mark_check()
        b.record(input_tokens=100)
        b.mark_check()
        b.record(input_tokens=100)
        b.mark_check()
        assert b.is_diminishing()

    def test_large_delta_resets_verdict(self):
        b = _budget()
        b.record(input_tokens=100)
        b.mark_check()
        b.record(input_tokens=100)
        b.mark_check()
        b.record(input_tokens=5000)
        b.mark_check()
        assert not b.is_diminishing()


class TestSoftLandingNudge:
    def test_no_nudge_when_low_usage(self):
        b = _budget()
        b.record(input_tokens=100)
        assert b.nudge_message() is None

    def test_nudge_past_ninety_pct(self):
        b = RunBudget(max_input_tokens=1000, max_output_tokens=1000, max_compute_ms=10_000)
        b.record(input_tokens=950)
        msg = b.nudge_message()
        assert msg is not None
        assert "95%" in msg

    def test_exhausted_budget_still_raises(self):
        b = RunBudget(100, 100, 100)
        b.record(input_tokens=150)
        with pytest.raises(BudgetExhausted):
            b.ensure_available()


class TestDegradationReasons:
    def test_runner_skip_reason_emitted(self):
        b = _budget()
        status, reasons, coverage = b.apply_to(
            RunStatus.DEGRADED,
            ["diminishing_returns:correctness"],
            "partial_explicit",
        )
        assert "diminishing_returns:correctness" in reasons
        assert coverage == "partial_explicit"

    def test_delta_tracking(self):
        b = _budget()
        b.record(input_tokens=500)
        b.record(input_tokens=700)
        assert b._last_delta == 700
        assert b._prev_delta == 500
