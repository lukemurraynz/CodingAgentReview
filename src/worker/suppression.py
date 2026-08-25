"""Run-local cross-lens finding suppression and metadata helpers."""

from __future__ import annotations

import re
from collections.abc import Iterable

from harness.models import Finding

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def merge_duplicate_findings(findings: Iterable[Finding]) -> list[Finding]:
    """Merge same-run duplicate findings while keeping contributing lens names."""
    merged: list[Finding] = []
    seen: dict[tuple[str, int, str], int] = {}
    for finding in findings:
        evidence = finding.evidence[0]
        line = evidence.line_start or 0
        normalized_title = _normalize_title(finding.title)
        key = (evidence.path.lower(), line, normalized_title)
        existing_index = seen.get(key)
        if existing_index is None:
            merged.append(_copy_with_metadata(finding, reported_by=_reported_by(finding)))
            seen[key] = len(merged) - 1
            continue
        merged[existing_index] = _merge_pair(merged[existing_index], finding)
    return merged


def _merge_pair(existing: Finding, incoming: Finding) -> Finding:
    merged_lenses = tuple(dict.fromkeys((*reported_by(existing), *_reported_by(incoming))))
    merged_evidence = list(existing.evidence)
    incoming_evidence = incoming.evidence[0]
    if not any(
        evidence.path == incoming_evidence.path
        and evidence.line_start == incoming_evidence.line_start
        and evidence.rule_id == incoming_evidence.rule_id
        for evidence in merged_evidence
    ):
        merged_evidence.append(incoming_evidence)
    merged = _copy_with_metadata(existing, reported_by=merged_lenses)
    merged.evidence = merged_evidence
    return merged


def _reported_by(finding: Finding) -> tuple[str, ...]:
    existing = reported_by(finding)
    if existing:
        return existing
    rule_id = finding.evidence[0].rule_id or ""
    lens_name, _, _ = rule_id.partition(".")
    return (lens_name,) if lens_name else ()


def attach_scope(finding: Finding, not_flagged: tuple[str, ...]) -> Finding:
    """Return a copy carrying scope-honesty notes out-of-band."""
    return _copy_with_metadata(finding, not_flagged=not_flagged)


def reported_by(finding: Finding) -> tuple[str, ...]:
    """Read merged lens names without changing the public Finding model."""
    value = getattr(finding, "_reported_by", ())
    return value if isinstance(value, tuple) else ()


def not_flagged(finding: Finding) -> tuple[str, ...]:
    """Read scope-honesty notes without changing the public Finding model."""
    value = getattr(finding, "_not_flagged", ())
    return value if isinstance(value, tuple) else ()


def _normalize_title(title: str) -> str:
    tokens = _TOKEN_RE.findall(title.lower())
    return " ".join(tokens)


def _copy_with_metadata(
    finding: Finding,
    *,
    reported_by: tuple[str, ...] | None = None,
    not_flagged: tuple[str, ...] | None = None,
) -> Finding:
    copied = finding.model_copy(deep=True)
    if reported_by is not None:
        object.__setattr__(copied, "_reported_by", reported_by)
    elif hasattr(finding, "_reported_by"):
        object.__setattr__(copied, "_reported_by", object.__getattribute__(finding, "_reported_by"))
    if not_flagged is not None:
        object.__setattr__(copied, "_not_flagged", not_flagged)
    elif hasattr(finding, "_not_flagged"):
        object.__setattr__(copied, "_not_flagged", object.__getattribute__(finding, "_not_flagged"))
    if hasattr(finding, "_exploitability"):
        object.__setattr__(copied, "_exploitability", object.__getattribute__(finding, "_exploitability"))
    if hasattr(finding, "_second_opinion_lens"):
        object.__setattr__(copied, "_second_opinion_lens", object.__getattribute__(finding, "_second_opinion_lens"))
    return copied
