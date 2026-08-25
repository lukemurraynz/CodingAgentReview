"""Repository discovery for declared specification artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from os import PathLike
from pathlib import Path
from typing import Final


class ArtifactKind(StrEnum):
    SPECIFICATION = "specification"
    MANIFEST = "manifest"
    RULE = "rule"


@dataclass(frozen=True, slots=True)
class DiscoveredArtifact:
    """A machine-readable specification artifact discovered in a repository."""

    repository_root: str
    path: str
    kind: ArtifactKind


_SCAN_PATTERNS: Final[tuple[tuple[str, ArtifactKind], ...]] = (
    ("specs/**/*.spec.json", ArtifactKind.SPECIFICATION),
    ("specs/**/specification.json", ArtifactKind.SPECIFICATION),
    ("specs/**/specifications.json", ArtifactKind.MANIFEST),
    (".harness/specifications.json", ArtifactKind.MANIFEST),
    (".harness/rules/*.md", ArtifactKind.RULE),
)


def discover_specification_artifacts(repository_root: str | PathLike[str]) -> tuple[DiscoveredArtifact, ...]:
    """Return supported spec artifacts below a repository root, sorted by relative path."""

    root = Path(repository_root)
    if not root.is_dir():
        raise ValueError(f"repository root does not exist: {root}")

    discovered: dict[str, DiscoveredArtifact] = {}
    root_text = root.resolve().as_posix()
    for pattern, kind in _SCAN_PATTERNS:
        for candidate in root.glob(pattern):
            if not candidate.is_file():
                continue
            relative = candidate.relative_to(root).as_posix()
            discovered.setdefault(
                relative,
                DiscoveredArtifact(repository_root=root_text, path=relative, kind=kind),
            )

    return tuple(discovered[path] for path in sorted(discovered))
