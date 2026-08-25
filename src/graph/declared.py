"""Load declared specifications and review rules with error reporting as data.

Review rules live in ``.harness/rules/*.md`` and use Markdown with YAML frontmatter:

---
id: no-sync-db-calls
severity: major  # blocker|major|high|medium|minor|low|info; major->high, minor->low
applies_to: ["src/db/**", "!src/db/migrations/**"]
lens: security   # any LensName value or "general"
---
Never introduce synchronous database calls in request handlers.

The body becomes ``ReviewRule.instruction`` and must be non-empty.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from graph.scanner import ArtifactKind, DiscoveredArtifact
from harness.models import Specification
from harness.models.specification import ReviewRule


@dataclass(frozen=True, slots=True)
class SpecificationLoadError:
    """Non-fatal issue encountered while loading one declared spec artifact."""

    path: str
    message: str


@dataclass(frozen=True, slots=True)
class LoadedSpecification:
    """One parsed specification or a structured load failure."""

    source_path: str
    specification: Specification | None
    errors: tuple[SpecificationLoadError, ...] = ()


@dataclass(frozen=True, slots=True)
class RuleLoadError:
    """Non-fatal issue encountered while loading one declared review rule."""

    path: str
    message: str


@dataclass(frozen=True, slots=True)
class LoadedRule:
    """One parsed review rule or a structured load failure."""

    source_path: str
    rule: ReviewRule | None
    errors: tuple[RuleLoadError, ...] = ()


def load_specifications(artifacts: tuple[DiscoveredArtifact, ...]) -> tuple[LoadedSpecification, ...]:
    """Parse supported JSON spec artifacts without raising on malformed input."""

    loaded: list[LoadedSpecification] = []
    for artifact in artifacts:
        if artifact.kind not in {ArtifactKind.SPECIFICATION, ArtifactKind.MANIFEST}:
            continue
        loaded.extend(_load_artifact(artifact))
    return tuple(loaded)


def load_rules(artifacts: tuple[DiscoveredArtifact, ...]) -> tuple[LoadedRule, ...]:
    """Parse supported Markdown rule artifacts without raising on malformed input."""

    loaded = [_load_rule_artifact(artifact) for artifact in artifacts if artifact.kind is ArtifactKind.RULE]
    return _mark_duplicate_rule_ids(tuple(loaded))


def _load_artifact(artifact: DiscoveredArtifact) -> list[LoadedSpecification]:
    path = Path(artifact.repository_root, artifact.path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [_error_result(artifact.path, f"invalid JSON: {exc.msg}")]

    entries = _payload_entries(payload, artifact.kind)
    if isinstance(entries, SpecificationLoadError):
        return [_error_result(artifact.path, entries.message)]

    return [_validate_entry(artifact.path, index, entry) for index, entry in enumerate(entries)]


def _payload_entries(payload: Any, kind: ArtifactKind) -> list[dict[str, Any]] | SpecificationLoadError:
    match kind:
        case ArtifactKind.SPECIFICATION:
            if isinstance(payload, dict):
                return [payload]
            return SpecificationLoadError(path="", message="single specification file must contain one JSON object")
        case ArtifactKind.MANIFEST:
            if isinstance(payload, dict) and isinstance(payload.get("specifications"), list):
                values = payload["specifications"]
            elif isinstance(payload, list):
                values = payload
            else:
                return SpecificationLoadError(
                    path="",
                    message="manifest must be a JSON array or an object with a 'specifications' array",
                )
            if not all(isinstance(value, dict) for value in values):
                return SpecificationLoadError(path="", message="manifest entries must all be JSON objects")
            return values
        case ArtifactKind.RULE:
            raise AssertionError("rule artifacts are handled by load_rules")
        case unreachable:
            raise AssertionError(f"unsupported artifact kind: {unreachable}")


def _validate_entry(path: str, index: int, payload: dict[str, Any]) -> LoadedSpecification:
    try:
        specification = Specification.model_validate(payload)
    except ValidationError as exc:
        location = f"{path}#{index}"
        return LoadedSpecification(
            source_path=location,
            specification=None,
            errors=tuple(
                SpecificationLoadError(path=location, message=_format_validation_error(error))
                for error in exc.errors()
            ),
        )
    return LoadedSpecification(source_path=f"{path}#{index}", specification=specification)


def _error_result(path: str, message: str) -> LoadedSpecification:
    return LoadedSpecification(
        source_path=path,
        specification=None,
        errors=(SpecificationLoadError(path=path, message=message),),
    )


def _format_validation_error(error: Mapping[str, Any]) -> str:
    location = ".".join(str(part) for part in error.get("loc", ())) or "payload"
    return f"{location}: {error.get('msg', 'invalid value')}"


def _load_rule_artifact(artifact: DiscoveredArtifact) -> LoadedRule:
    path = Path(artifact.repository_root, artifact.path)
    content = path.read_text(encoding="utf-8")
    split = _split_rule_frontmatter(content)
    if isinstance(split, RuleLoadError):
        return _rule_error_result(artifact.path, split.message)

    frontmatter, body = split
    if not isinstance(frontmatter, Mapping):
        return _rule_error_result(artifact.path, "rule frontmatter must be a YAML mapping")

    payload = dict(frontmatter)
    payload["instruction"] = body
    try:
        rule = ReviewRule.model_validate(payload)
    except ValidationError as exc:
        return LoadedRule(
            source_path=artifact.path,
            rule=None,
            errors=tuple(
                RuleLoadError(path=artifact.path, message=_format_validation_error(error))
                for error in exc.errors()
            ),
        )
    return LoadedRule(source_path=artifact.path, rule=rule)


def _split_rule_frontmatter(content: str) -> tuple[Mapping[str, Any], str] | RuleLoadError:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return RuleLoadError(path="", message="rule file must start with YAML frontmatter delimited by ---")

    closing_index = next((index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None)
    if closing_index is None:
        return RuleLoadError(path="", message="rule file frontmatter is missing a closing --- delimiter")

    frontmatter_text = "\n".join(lines[1:closing_index])
    body = "\n".join(lines[closing_index + 1 :]).strip()
    if not body:
        return RuleLoadError(path="", message="rule instruction body must not be empty")

    try:
        parsed = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError:
        return RuleLoadError(path="", message="rule frontmatter contains invalid YAML")
    if parsed is None:
        return RuleLoadError(path="", message="rule frontmatter must not be empty")
    return parsed, body


def _mark_duplicate_rule_ids(loaded: tuple[LoadedRule, ...]) -> tuple[LoadedRule, ...]:
    counts: dict[str, int] = {}
    for item in loaded:
        if item.rule is None:
            continue
        counts[item.rule.id] = counts.get(item.rule.id, 0) + 1

    normalized: list[LoadedRule] = []
    for item in loaded:
        if item.rule is None:
            normalized.append(item)
            continue
        if counts.get(item.rule.id, 0) == 1:
            normalized.append(item)
            continue
        normalized.append(
            LoadedRule(
                source_path=item.source_path,
                rule=None,
                errors=(RuleLoadError(path=item.source_path, message=f"duplicate rule id: {item.rule.id}"),),
            )
        )
    return tuple(normalized)


def _rule_error_result(path: str, message: str) -> LoadedRule:
    return LoadedRule(source_path=path, rule=None, errors=(RuleLoadError(path=path, message=message),))
