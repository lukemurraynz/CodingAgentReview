"""Risk-proportionate review-depth policy (FR-012)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from harness.models import ChangeClassification, RiskLevel, RiskSignal

DETERMINISTIC_LENSES = ("structural", "production_validation")
STANDARD_LENSES = DETERMINISTIC_LENSES + ("correctness",)
ESCALATED_LENSES = STANDARD_LENSES + ("security",)

_DOC_SUFFIXES = {".md", ".rst", ".txt", ".adoc"}
_LOCKFILES = {"uv.lock", "poetry.lock", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "Cargo.lock"}
_GENERATED_MARKERS = ("/generated/", "/dist/", ".generated.", "_generated.")
_IDENTITY_MARKERS = ("auth", "identity", "permission", "rbac", "oauth", "claims", "policy", "role")
_PROD_INFRA_MARKERS = (
    "infra/",
    "deploy",
    "k8s",
    "helm",
    "bicep",
    ".tf",
    ".tfvars",
    "production",
    "prod",
)
_LEVEL_ORDER = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2, RiskLevel.CRITICAL: 3}


@dataclass(frozen=True, slots=True)
class ReviewDepthPolicy:
    risk_floor: RiskLevel
    risk_level: RiskLevel
    lenses: tuple[str, ...]
    acknowledgement_required: bool = False


def coerce_risk_signals(raw: object) -> tuple[RiskSignal, ...]:
    """Accept worker event payloads containing dict risk signals or model instances."""
    if not isinstance(raw, list):
        return ()
    signals: list[RiskSignal] = []
    for item in raw:
        if isinstance(item, RiskSignal):
            signals.append(item)
        elif isinstance(item, dict):
            signals.append(RiskSignal.model_validate(item))
    return tuple(signals)


def classify_review_depth(
    *,
    classification: str | ChangeClassification,
    changed_paths: Sequence[str],
    risk_signals: Sequence[RiskSignal] = (),
) -> ReviewDepthPolicy:
    """Select lenses and risk level from diff stats plus upstream risk signals."""
    diff_floor = _diff_risk_floor(_normalize_classification(classification), changed_paths)
    explicit_level = _max_signal_level(risk_signals)
    effective_level = _max_level(diff_floor, explicit_level)
    acknowledgement_required = effective_level == RiskLevel.CRITICAL
    if effective_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
        return ReviewDepthPolicy(diff_floor, effective_level, ESCALATED_LENSES, acknowledgement_required)
    if effective_level == RiskLevel.LOW:
        return ReviewDepthPolicy(diff_floor, effective_level, DETERMINISTIC_LENSES)
    return ReviewDepthPolicy(diff_floor, effective_level, STANDARD_LENSES)


def _normalize_classification(classification: str | ChangeClassification) -> ChangeClassification:
    if isinstance(classification, ChangeClassification):
        return classification
    try:
        return ChangeClassification(classification)
    except ValueError:
        return ChangeClassification.CODE


def _max_signal_level(risk_signals: Sequence[RiskSignal]) -> RiskLevel | None:
    if not risk_signals:
        return None
    return max(risk_signals, key=lambda signal: _LEVEL_ORDER[signal.level]).level


def _max_level(*levels: RiskLevel | None) -> RiskLevel:
    concrete_levels = [level for level in levels if level is not None]
    if not concrete_levels:
        return RiskLevel.LOW
    return max(concrete_levels, key=lambda level: _LEVEL_ORDER[level])


def _diff_risk_floor(classification: ChangeClassification, changed_paths: Sequence[str]) -> RiskLevel:
    if _touches_high_risk_paths(changed_paths):
        return RiskLevel.HIGH
    if _docs_only(classification, changed_paths):
        return RiskLevel.LOW
    return RiskLevel.MEDIUM


def _docs_only(classification: ChangeClassification, changed_paths: Sequence[str]) -> bool:
    if classification in {
        ChangeClassification.DOCS,
        ChangeClassification.GENERATED,
        ChangeClassification.LOCKFILE,
    }:
        return True
    if not changed_paths:
        return False
    return all(_is_doc_like_path(path) or _is_generated_path(path) or _is_lockfile_path(path) for path in changed_paths)


def _touches_high_risk_paths(changed_paths: Iterable[str]) -> bool:
    return any(_is_identity_path(path) or _is_production_or_infra_path(path) for path in changed_paths)


def _is_doc_like_path(path: str) -> bool:
    lower_path = path.lower()
    pure_path = PurePosixPath(lower_path)
    return lower_path.startswith("docs/") or pure_path.suffix in _DOC_SUFFIXES or pure_path.name.startswith("readme")


def _is_generated_path(path: str) -> bool:
    lower_path = path.lower()
    return any(marker in lower_path for marker in _GENERATED_MARKERS)


def _is_lockfile_path(path: str) -> bool:
    return PurePosixPath(path).name in _LOCKFILES


def _is_identity_path(path: str) -> bool:
    lower_path = path.lower()
    return any(marker in lower_path for marker in _IDENTITY_MARKERS)


def _is_production_or_infra_path(path: str) -> bool:
    lower_path = path.lower()
    return any(marker in lower_path for marker in _PROD_INFRA_MARKERS)
