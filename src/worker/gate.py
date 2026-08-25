"""Merge-gate verdict derivation (FR-013)."""

from __future__ import annotations

from dataclasses import dataclass

from harness.models import Finding, ReviewRun, RiskLevel, Severity
from providers.base import AnnotationReport, GateVerdict
from worker.exploitability import effective_severity
from worker.suppression import not_flagged as finding_not_flagged

_BLOCKING_SEVERITIES = {Severity.BLOCKER, Severity.HIGH}
_NON_BLOCKING_STATUSES = {"resolved", "waived"}


@dataclass(frozen=True, slots=True)
class CoverageSummary:
    declared: int
    reported: int
    unavailable: int

    def as_line(self) -> str:
        return (
            f"lenses: {self.declared} declared, {self.reported} reported, "
            f"{self.unavailable} unavailable"
        )


def derive_gate_verdict(
    findings: list[Finding],
    *,
    risk_floor: RiskLevel,
    risk_level: RiskLevel,
    coverage: CoverageSummary | None = None,
    acknowledged: bool = False,
) -> GateVerdict:
    """Block on blocker/high findings and on unacknowledged critical risk."""
    effective_risk = _max_risk_level(risk_floor, risk_level)
    blocking_findings = sum(
        1
        for finding in findings
        if effective_severity(finding) in _BLOCKING_SEVERITIES and finding.status not in _NON_BLOCKING_STATUSES
    )
    acknowledgement_required = effective_risk == RiskLevel.CRITICAL
    if blocking_findings:
        return GateVerdict(
            status="block",
            blocking_findings=blocking_findings,
            risk_floor=risk_floor,
            risk_level=effective_risk,
            reason=f"{blocking_findings} blocking finding(s)",
            acknowledgement_required=acknowledgement_required,
            acknowledged=acknowledged,
        )
    if coverage is not None and coverage.unavailable > 0:
        return GateVerdict(
            status="degraded",
            blocking_findings=0,
            risk_floor=risk_floor,
            risk_level=effective_risk,
            reason="partial lens coverage",
            acknowledgement_required=acknowledgement_required,
            acknowledged=acknowledged,
        )
    if acknowledgement_required and not acknowledged:
        return GateVerdict(
            status="block",
            blocking_findings=0,
            risk_floor=risk_floor,
            risk_level=effective_risk,
            reason="critical-risk acknowledgement required",
            acknowledgement_required=True,
            acknowledged=False,
        )
    return GateVerdict(
        status="pass",
        blocking_findings=0,
        risk_floor=risk_floor,
        risk_level=effective_risk,
        reason="no blocking findings",
        acknowledgement_required=acknowledgement_required,
        acknowledged=acknowledged,
    )


def build_annotation_report(
    run: ReviewRun,
    findings: list[Finding],
    *,
    risk_floor: RiskLevel,
    risk_level: RiskLevel,
    declared_lenses: tuple[str, ...],
    executed_lenses: tuple[str, ...],
    unavailable_lenses: tuple[str, ...],
    scope_notes: tuple[str, ...],
    acknowledged: bool = False,
    workitem_completeness: str = "",
) -> AnnotationReport:
    """Shape the worker outcome for provider projection."""
    coverage = summarize_coverage(declared_lenses, executed_lenses, unavailable_lenses)
    return AnnotationReport(
        gate=derive_gate_verdict(
            findings,
            risk_floor=risk_floor,
            risk_level=risk_level,
            coverage=coverage,
            acknowledged=acknowledged,
        ),
        run_status=run.status,
        coverage=run.coverage,
        lens_coverage=coverage.as_line(),
        not_flagged=_collect_scope_notes(findings, scope_notes),
        degraded_reasons=tuple(run.degraded_reasons),
        workitem_completeness=workitem_completeness,
    )


def summarize_coverage(
    declared_lenses: tuple[str, ...],
    executed_lenses: tuple[str, ...],
    unavailable_lenses: tuple[str, ...],
) -> CoverageSummary:
    """Count declared/reported/unavailable lenses for provider reporting."""
    return CoverageSummary(
        declared=len(declared_lenses),
        reported=len(executed_lenses) + len(unavailable_lenses),
        unavailable=len(unavailable_lenses),
    )


def _collect_scope_notes(findings: list[Finding], scope_notes: tuple[str, ...]) -> tuple[str, ...]:
    notes = list(scope_notes)
    for finding in findings:
        notes.extend(finding_not_flagged(finding))
    return tuple(dict.fromkeys(notes))


def _max_risk_level(left: RiskLevel, right: RiskLevel) -> RiskLevel:
    order = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2, RiskLevel.CRITICAL: 3}
    return left if order[left] >= order[right] else right
