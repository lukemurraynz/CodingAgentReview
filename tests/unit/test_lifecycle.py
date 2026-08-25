"""T007 regression: lifecycle transitions re-run pydantic validators."""

import pytest

from harness.lifecycle import LifecycleError, can_transition, transition
from harness.models import Evidence, Finding, FindingCategory, Severity, Waiver


def _finding(status: str = "candidate", **kwargs) -> Finding:
    return Finding(
        id="f1",
        change_id="c1",
        repo_id="org/repo",
        category=FindingCategory.SECURITY,
        severity=Severity.HIGH,
        title="hardcoded secret",
        evidence=[Evidence(path="src/app.py", line_start=1)],
        dedup_key="v1:x",
        status=status,
        **kwargs,
    )


class TestLegalTransitions:
    @pytest.mark.parametrize(
        ("frm", "to"),
        [
            ("candidate", "confirmed"),
            ("candidate", "stale"),
            ("confirmed", "resolved"),
            ("resolved", "reopened"),
            ("stale", "reopened"),
        ],
    )
    def test_allowed(self, frm, to):
        f = _finding(frm)
        out = transition(f, to)
        assert out.status == to
        assert can_transition(f, to)

    def test_confirm_then_waive_with_record(self):
        waiver = Waiver(approver="lead", rationale="accepted")
        out = transition(_finding("confirmed"), "waived", waiver=waiver)
        assert out.status == "waived"
        assert out.waiver is not None
        assert out.waiver.approver == "lead"


class TestIllegalTransitions:
    @pytest.mark.parametrize(
        ("frm", "to"),
        [
            ("candidate", "waived"),  # must be confirmed first
            ("candidate", "resolved"),
            ("resolved", "confirmed"),  # no backwards transitions
        ],
    )
    def test_rejected(self, frm, to):
        with pytest.raises(LifecycleError, match="illegal transition"):
            transition(_finding(frm), to)

    def test_waived_cannot_go_back_to_confirmed(self):
        waived = transition(
            _finding("confirmed"), "waived", waiver=Waiver(approver="lead", rationale="ok")
        )
        with pytest.raises(LifecycleError, match="illegal transition"):
            transition(waived, "confirmed")

    def test_waive_without_record_rejected(self):
        # confirmed -> waived requires a waiver record argument
        with pytest.raises(LifecycleError, match="requires a waiver record"):
            transition(_finding("confirmed"), "waived")


class TestReopenProvenance:
    def test_reopen_requires_origin(self):
        resolved = transition(_finding("candidate"), "confirmed")
        resolved = transition(resolved, "resolved")
        reopened = transition(resolved, "reopened")
        assert reopened.reopened_from == "f1"

    def test_reopen_from_explicit_origin(self):
        other = transition(_finding("candidate"), "confirmed")
        other = transition(other, "resolved")
        reopened = transition(other, "reopened", reopened_from="f999")
        assert reopened.reopened_from == "f999"


def test_updated_at_advances():
    import time

    f = _finding("candidate")
    before = f.updated_at
    time.sleep(0.01)
    out = transition(f, "confirmed")
    assert out.updated_at > before
