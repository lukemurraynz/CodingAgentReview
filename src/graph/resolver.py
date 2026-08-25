"""Pure graph merge + spec/rule applicability resolution.

``build_lens_briefs`` is the hand-off seam for lens prompt builders: callers can append the
returned per-lens text to a lens prompt without this module knowing about prompt assembly.
Wiring that seam into workers/lenses is intentionally out of scope here.
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import PurePosixPath
from typing import Final

from graph.declared import LoadedRule, LoadedSpecification
from harness.models import RiskSignal, Specification
from harness.models.enums import Severity
from harness.models.specification import ReviewRule


@dataclass(frozen=True, slots=True)
class SpecificationGraph:
    """Merged declared specifications indexed for deterministic applicability checks."""

    specifications: tuple[LoadedSpecification, ...]
    rules: tuple[LoadedRule, ...] = ()


@dataclass(frozen=True, slots=True)
class ApplicableSpecification:
    """One spec plus the deterministic evidence for why it matched a change."""

    source_path: str
    specification: Specification
    matched_paths: tuple[str, ...]
    matched_resource_types: tuple[str, ...]
    matched_invariants: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GroundedRiskDriver:
    """Risk signal with explicit applicable-spec evidence attached."""

    signal_name: str
    level: str
    detail: str
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ApplicableRule:
    """One review rule plus deterministic evidence for why it matched a change."""

    source_path: str
    rule: ReviewRule
    matched_paths: tuple[str, ...]
    match_reason: str


_RESOURCE_HINTS: Final[tuple[tuple[str, str], ...]] = (
    ("api", "api"),
    ("routes", "api"),
    ("auth", "identity"),
    ("identity", "identity"),
    ("permissions", "identity"),
    ("infra", "environment"),
    ("deployment", "deployment"),
    ("deployments", "deployment"),
    ("service", "service"),
    ("services", "service"),
    ("worker", "service"),
    ("agent", "agent"),
    ("agents", "agent"),
    ("mcpserver", "agent"),
)

_RULE_SEVERITY_ORDER: Final[dict[Severity, int]] = {
    Severity.BLOCKER: 0,
    Severity.HIGH: 1,
    Severity.MEDIUM: 2,
    Severity.LOW: 3,
    Severity.INFO: 4,
}


def merge_specifications(
    loaded: tuple[LoadedSpecification, ...], loaded_rules: tuple[LoadedRule, ...] = ()
) -> SpecificationGraph:
    """Keep valid specifications in deterministic source order for later resolution."""

    valid = tuple(item for item in loaded if item.specification is not None)
    valid_rules = tuple(item for item in loaded_rules if item.rule is not None)
    return SpecificationGraph(specifications=valid, rules=valid_rules)


def resolve_applicable_specifications(
    graph: SpecificationGraph,
    *,
    changed_paths: tuple[str, ...] | list[str],
    resource_types: tuple[str, ...] | list[str] = (),
    risk_signals: tuple[RiskSignal, ...] | list[RiskSignal] = (),
) -> tuple[ApplicableSpecification, ...]:
    """Resolve specs for a change using explicit globs and semantic resource hints."""

    del risk_signals  # ponytail: resolver stays deterministic; grounding uses signals separately.
    normalized_paths = tuple(_normalize_path(path) for path in changed_paths)
    affected_resource_types = frozenset(resource_types) | frozenset(
        resource_type for path in normalized_paths for resource_type in _infer_resource_types(path)
    )

    applicable: list[ApplicableSpecification] = []
    for item in graph.specifications:
        specification = item.specification
        assert specification is not None
        matched_paths = tuple(
            path
            for path in normalized_paths
            if any(_matches_pattern(path, pattern) for pattern in specification.path_patterns)
        )
        spec_resource_types = frozenset(specification.applies_to)
        invariant_matches = tuple(
            invariant.id
            for invariant in specification.invariants
            if affected_resource_types.intersection(invariant.applies_to)
        )
        matched_resource_types = tuple(sorted(affected_resource_types.intersection(spec_resource_types)))
        if not matched_paths and not matched_resource_types and not invariant_matches:
            continue
        applicable.append(
            ApplicableSpecification(
                source_path=item.source_path,
                specification=specification,
                matched_paths=tuple(sorted(matched_paths)),
                matched_resource_types=matched_resource_types,
                matched_invariants=tuple(sorted(invariant_matches)),
            )
        )

    return tuple(sorted(applicable, key=lambda item: (item.specification.id, item.source_path)))


def ground_risk_drivers(
    applicable_specifications: tuple[ApplicableSpecification, ...] | list[ApplicableSpecification],
    risk_signals: tuple[RiskSignal, ...] | list[RiskSignal],
) -> tuple[GroundedRiskDriver, ...]:
    """Attach applicable-spec evidence to each risk signal for later explanation queries."""

    evidence = tuple(
        f"{item.specification.id}@{item.source_path}"
        if not item.matched_invariants
        else f"{item.specification.id}@{item.source_path}[{','.join(item.matched_invariants)}]"
        for item in applicable_specifications
    )
    if not evidence:
        return ()
    return tuple(
        GroundedRiskDriver(
            signal_name=signal.name,
            level=signal.level.value,
            detail=signal.detail,
            evidence=evidence,
        )
        for signal in risk_signals
    )


def resolve_applicable_rules(
    graph: SpecificationGraph, *, changed_paths: tuple[str, ...] | list[str]
) -> tuple[ApplicableRule, ...]:
    """Resolve repo-local review rules against changed paths with glob include/exclude semantics."""

    normalized_paths = tuple(_normalize_path(path) for path in changed_paths)
    applicable: list[ApplicableRule] = []
    for item in graph.rules:
        rule = item.rule
        assert rule is not None
        matched_paths = tuple(path for path in normalized_paths if _path_matches_rule(path, rule.applies_to))
        if not matched_paths:
            continue
        applicable.append(
            ApplicableRule(
                source_path=item.source_path,
                rule=rule,
                matched_paths=tuple(sorted(matched_paths)),
                match_reason=f"matched paths: {', '.join(sorted(matched_paths))}",
            )
        )

    return tuple(sorted(applicable, key=_rule_sort_key))


def build_lens_briefs(applicable_rules: tuple[ApplicableRule, ...] | list[ApplicableRule]) -> dict[str, str]:
    """Group rule instructions by target lens so prompt builders can append the returned briefs."""

    grouped: dict[str, list[str]] = {}
    for item in applicable_rules:
        entry = f"[{item.rule.severity.value}] {item.rule.id}: {item.rule.instruction}"
        grouped.setdefault(item.rule.lens, []).append(entry)
    return {lens: "\n".join(entries) for lens, entries in sorted(grouped.items())}


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def _matches_pattern(path: str, pattern: str) -> bool:
    normalized_pattern = pattern.lstrip("./")
    return fnmatchcase(path, normalized_pattern) or fnmatchcase(
        path,
        normalized_pattern.replace("/**/", "/"),
    )


def _infer_resource_types(path: str) -> frozenset[str]:
    parts = {part.lower() for part in PurePosixPath(path).parts}
    inferred = {resource_type for hint, resource_type in _RESOURCE_HINTS if hint in parts}
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in {".bicep", ".tf"}:
        inferred.add("environment")
    return frozenset(inferred)


def _path_matches_rule(path: str, patterns: list[str]) -> bool:
    included = [pattern for pattern in patterns if not pattern.startswith("!")]
    excluded = [pattern[1:] for pattern in patterns if pattern.startswith("!")]
    return any(_matches_pattern(path, pattern) for pattern in included) and not any(
        _matches_pattern(path, pattern) for pattern in excluded
    )


def _rule_sort_key(item: ApplicableRule) -> tuple[int, str, str]:
    return (_RULE_SEVERITY_ORDER[item.rule.severity], item.rule.id, item.source_path)
