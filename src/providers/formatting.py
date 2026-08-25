"""Shared provider-side annotation/comment formatting."""

from __future__ import annotations

from harness.models import Finding, RunStatus
from harness.redaction import redact_text
from worker.suppression import reported_by

from .base import AnnotationReport

_SEVERITY_ORDER = ("blocker", "high", "medium", "low", "info")
SUMMARY_MARKER = "<!-- harness-review-summary -->"


def build_findings_table(findings: list[Finding]) -> list[str]:
    """Compact markdown table for the summary comment."""
    lines = ["| Severity | Lenses | Path | Title |", "| --- | --- | --- | --- |"]
    for finding in _sorted_findings(findings):
        evidence = finding.evidence[0]
        location = evidence.path
        if evidence.line_start:
            location += f":{evidence.line_start}"
        lenses = ", ".join(reported_by(finding)) or "-"
        title, _ = redact_text(finding.title)
        lines.append(f"| {finding.severity.value.upper()} | {lenses} | `{location}` | {title} |")
    return lines


def build_comment_body(
    findings: list[Finding],
    report: AnnotationReport | None = None,
    *,
    include_marker: bool = True,
) -> str:
    """Human-readable provider projection with explicit gate verdict."""
    lines = ["## Automated review findings", ""]
    if include_marker:
        lines.insert(0, SUMMARY_MARKER)
    if report is not None:
        lines.append(f"- Gate verdict: **{report.gate.status.upper()}** — {report.gate.reason}")
        lines.append(f"- Risk floor: **{report.gate.risk_floor.value}**")
        lines.append(f"- Risk level: **{report.gate.risk_level.value}**")
        lines.append(f"- Blocking findings: **{report.gate.blocking_findings}**")
        if report.lens_coverage:
            lines.append(f"- {report.lens_coverage}")
        if report.workitem_completeness:
            lines.append(f"- {report.workitem_completeness}")
        if report.gate.acknowledgement_required:
            state = "received" if report.gate.acknowledged else "required"
            lines.append(f"- Critical-risk acknowledgement: **{state}**")
        if report.run_status != RunStatus.COMPLETED or report.coverage != "full":
            reasons = ", ".join(report.degraded_reasons) or "explicitly degraded"
            lines.append(
                f"- Run status: **{report.run_status.value}** ({report.coverage}; reasons: {reasons})"
            )
        lines.append("")

    grouped: dict[str, list[Finding]] = {}
    for finding in findings:
        grouped.setdefault(finding.severity.value, []).append(finding)

    if not findings:
        lines.append("No findings.")
        _append_scope_honesty(lines, report)
        return "\n".join(lines)

    lines.append("### Findings")
    lines.append("")
    lines.extend(build_findings_table(findings))
    lines.append("")
    for severity in _SEVERITY_ORDER:
        for finding in grouped.get(severity, []):
            title, _ = redact_text(finding.title)
            detail, _ = redact_text(finding.detail or "")
            if detail:
                lines.append(f"- **{title}**: {detail}")
    _append_scope_honesty(lines, report)
    return "\n".join(lines)


def _append_scope_honesty(lines: list[str], report: AnnotationReport | None) -> None:
    if report is None:
        return
    lines.append("")
    lines.append("### Verdict")
    lines.append(f"- **{report.gate.status.upper()}** with **{report.gate.blocking_findings}** blocker(s)")
    lines.append("")
    lines.append("### Deliberately not flagged")
    if not report.not_flagged:
        lines.append("- None declared.")
        return
    for note in report.not_flagged:
        lines.append(f"- {note}")


def _sorted_findings(findings: list[Finding]) -> list[Finding]:
    order = {severity: index for index, severity in enumerate(_SEVERITY_ORDER)}
    return sorted(
        findings,
        key=lambda finding: (
            order.get(finding.severity.value, len(_SEVERITY_ORDER)),
            finding.evidence[0].path,
            finding.evidence[0].line_start or 0,
            finding.title.lower(),
        ),
    )
