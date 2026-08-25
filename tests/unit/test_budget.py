"""T018: cost ledger + explicit degradation (FR-035)."""

import pytest

from harness.models.run import RunStatus
from worker.budget import BudgetExhausted, RunBudget


def test_under_budget_stays_completed():
    b = RunBudget(max_input_tokens=1000, max_output_tokens=1000, max_compute_ms=10_000)
    b.record(input_tokens=100, output_tokens=50, compute_ms=500)
    b.ensure_available()
    status, reasons, coverage = b.apply_to(RunStatus.COMPLETED, [], "full")
    assert (status, reasons, coverage) == (RunStatus.COMPLETED, [], "full")


def test_exhaustion_flags_and_raises():
    b = RunBudget(max_input_tokens=100, max_output_tokens=100, max_compute_ms=100)
    b.record(input_tokens=150)
    with pytest.raises(BudgetExhausted):
        b.ensure_available()
    status, reasons, coverage = b.apply_to(RunStatus.COMPLETED, [], "full")
    assert status == RunStatus.DEGRADED
    assert reasons == ["budget_exhausted"]
    assert coverage == "partial_explicit"


def test_existing_reasons_preserved():
    b = RunBudget(100, 100, 100)
    b.record(compute_ms=200)
    _, reasons, _ = b.apply_to(RunStatus.DEGRADED, ["lens_failed"], "partial_explicit")
    assert set(reasons) == {"lens_failed", "budget_exhausted"}


def test_boundary_not_exhausted():
    b = RunBudget(100, 100, 100)
    b.record(input_tokens=100)  # exactly at limit is not over it
    b.ensure_available()
