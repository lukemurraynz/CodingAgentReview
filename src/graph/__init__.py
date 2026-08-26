"""Specification and review-rule discovery + applicability helpers (FR-008, FR-033)."""

from .declared import (
    LoadedRule,
    LoadedSpecification,
    RuleLoadError,
    SpecificationLoadError,
    load_rules,
    load_specifications,
)
from .resolver import (
    ApplicableRule,
    ApplicableSpecification,
    GroundedRiskDriver,
    SpecificationGraph,
    build_lens_briefs,
    ground_risk_drivers,
    merge_specifications,
    resolve_applicable_rules,
    resolve_applicable_specifications,
)
from .scanner import ArtifactKind, DiscoveredArtifact, discover_specification_artifacts
from .symbols import (
    REGISTRATION_PATTERN,
    InvocationStat,
    ParseIssue,
    SymbolIndex,
    build_symbol_index,
)

__all__ = [
    "ApplicableSpecification",
    "ApplicableRule",
    "ArtifactKind",
    "build_symbol_index",
    "build_lens_briefs",
    "DiscoveredArtifact",
    "GroundedRiskDriver",
    "InvocationStat",
    "LoadedRule",
    "LoadedSpecification",
    "ParseIssue",
    "REGISTRATION_PATTERN",
    "RuleLoadError",
    "SpecificationGraph",
    "SpecificationLoadError",
    "SymbolIndex",
    "discover_specification_artifacts",
    "ground_risk_drivers",
    "load_rules",
    "load_specifications",
    "merge_specifications",
    "resolve_applicable_rules",
    "resolve_applicable_specifications",
]
