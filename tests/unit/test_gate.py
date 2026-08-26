"""Merge-gate verdict derivation."""

from harness.models import Evidence, Finding, FindingCategory, ReviewRun, RiskLevel, RunStatus, Severity, Waiver
from providers.formatting import build_comment_body
from worker.gate import build_annotation_report, derive_gate_verdict


def _finding(severity: Severity, status: str = "confirmed") -> Finding:
    waiver = Waiver(approver="lead", rationale="accepted") if status == "waived" else None
    return Finding(
        id=f"f-{severity.value}-{status}",
        change_id="c1",
        repo_id="org/repo",
        category=FindingCategory.SECURITY,
        severity=severity,
        title="t",
        evidence=[Evidence(path="src/a.py", line_start=1)],
        dedup_key=f"d-{severity.value}-{status}",
        status=status,
        waiver=waiver,
    )


def test_block_on_high_or_blocker_findings():
    verdict = derive_gate_verdict(
        [_finding(Severity.HIGH), _finding(Severity.LOW)],
        risk_floor=RiskLevel.MEDIUM,
        risk_level=RiskLevel.MEDIUM,
    )
    assert verdict.status == "block"
    assert verdict.blocking_findings == 1


def test_ignore_waived_or_resolved_findings():
    verdict = derive_gate_verdict(
        [_finding(Severity.BLOCKER, status="waived"), _finding(Severity.HIGH, status="resolved")],
        risk_floor=RiskLevel.MEDIUM,
        risk_level=RiskLevel.MEDIUM,
    )
    assert verdict.status == "pass"
    assert verdict.blocking_findings == 0


def test_critical_risk_requires_acknowledgement_even_without_findings():
    verdict = derive_gate_verdict([], risk_floor=RiskLevel.MEDIUM, risk_level=RiskLevel.CRITICAL, acknowledged=False)
    assert verdict.status == "block"
    assert verdict.reason == "critical-risk acknowledgement required"


def test_critical_risk_passes_once_acknowledged():
    verdict = derive_gate_verdict([], risk_floor=RiskLevel.MEDIUM, risk_level=RiskLevel.CRITICAL, acknowledged=True)
    assert verdict.status == "pass"
    assert verdict.acknowledgement_required is True
    assert verdict.acknowledged is True


def test_annotation_report_carries_gate_and_degrade():
    run = ReviewRun(
        id="r1",
        change_id="c1",
        head_sha="abc",
        status=RunStatus.DEGRADED,
        coverage="partial_explicit",
        degraded_reasons=["budget_exhausted"],
    )
    report = build_annotation_report(
        run,
        [_finding(Severity.HIGH)],
        risk_floor=RiskLevel.HIGH,
        risk_level=RiskLevel.HIGH,
        declared_lenses=("structural",),
        executed_lenses=("structural",),
        unavailable_lenses=(),
        scope_notes=(),
    )
    assert report.gate.status == "block"
    assert report.auto_fix_available is True
    assert report.run_status == RunStatus.DEGRADED
    assert report.degraded_reasons == ("budget_exhausted",)


def test_partial_coverage_forces_degraded_gate_and_scope_note_section():
    run = ReviewRun(
        id="r1",
        change_id="c1",
        head_sha="abc",
        status=RunStatus.DEGRADED,
        coverage="partial_explicit",
        degraded_reasons=["model_unavailable:security"],
    )
    report = build_annotation_report(
        run,
        [],
        risk_floor=RiskLevel.MEDIUM,
        risk_level=RiskLevel.MEDIUM,
        declared_lenses=("structural", "security"),
        executed_lenses=("structural",),
        unavailable_lenses=("security",),
        scope_notes=("Security lens does not validate runtime config.",),
    )
    assert report.gate.status == "degraded"
    assert report.lens_coverage == "lenses: 2 declared, 2 reported, 1 unavailable"
    assert report.not_flagged == ("Security lens does not validate runtime config.",)


def test_risk_floor_wins_when_reported_risk_is_lower():
    verdict = derive_gate_verdict([], risk_floor=RiskLevel.HIGH, risk_level=RiskLevel.LOW)
    assert verdict.risk_floor == RiskLevel.HIGH
    assert verdict.risk_level == RiskLevel.HIGH


def test_summary_comment_includes_auto_fix_offer_for_blocking_findings():
    run = ReviewRun(id="r1", change_id="c1", head_sha="abc", status=RunStatus.COMPLETED)
    report = build_annotation_report(
        run,
        [_finding(Severity.HIGH)],
        risk_floor=RiskLevel.HIGH,
        risk_level=RiskLevel.HIGH,
        declared_lenses=("security",),
        executed_lenses=("security",),
        unavailable_lenses=(),
        scope_notes=(),
    )

    body = build_comment_body([_finding(Severity.HIGH)], report)

    assert "### Auto-fix available" in body
    assert "invoke `fix.propose` via MCP" in body


def test_summary_comment_omits_auto_fix_offer_without_blocking_findings():
    run = ReviewRun(id="r1", change_id="c1", head_sha="abc", status=RunStatus.COMPLETED)
    report = build_annotation_report(
        run,
        [_finding(Severity.MEDIUM)],
        risk_floor=RiskLevel.MEDIUM,
        risk_level=RiskLevel.MEDIUM,
        declared_lenses=("security",),
        executed_lenses=("security",),
        unavailable_lenses=(),
        scope_notes=(),
    )

    body = build_comment_body([_finding(Severity.MEDIUM)], report)

    assert "### Auto-fix available" not in body
