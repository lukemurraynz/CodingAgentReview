"""Review lens registry (FR-006/007).

Deterministic lenses ship here. Callers that want the full default set can use
``all_lenses()`` / ``LENS_REGISTRY`` without reaching into worker wiring.
"""

from .architecture import ArchitectureLens
from .base import Lens, LensContext, LensFile
from .production_validation import ProductionValidationLens
from .structural import StructuralLens
from .test_quality import TestQualityLens

_DEFAULT_VERSION = "0"

_LENS_VERSIONS: dict[str, str] = {
    StructuralLens.name: "1",
    ProductionValidationLens.name: "1",
    ArchitectureLens.name: "1",
    TestQualityLens.name: "1",
    "correctness": "1",
    "security": "1",
}

_LENS_NOT_FLAGGED: dict[str, tuple[str, ...]] = {
    StructuralLens.name: (
        "Does not validate business correctness or external runtime behavior.",
    ),
    ProductionValidationLens.name: (
        "Does not exercise live deployments or runtime infrastructure state.",
    ),
    ArchitectureLens.name: (
        "Does not prove low-level correctness for individual code paths.",
    ),
    TestQualityLens.name: (
        "Does not execute tests or verify non-test production code directly.",
    ),
    "correctness": (
        "Does not validate unchanged code or behavior outside reviewed diff lines.",
        "Does not execute the application or external integrations.",
    ),
    "security": (
        "Does not scan repositories or deployed environments outside reviewed diff lines.",
        "Does not validate operational security controls at runtime.",
    ),
}

__all__ = [
    "LENS_REGISTRY",
    "lens_not_flagged",
    "lens_version",
    "all_lenses",
    "ArchitectureLens",
    "Lens",
    "LensContext",
    "LensFile",
    "ProductionValidationLens",
    "StructuralLens",
    "TestQualityLens",
]


def all_lenses() -> dict[str, Lens]:
    """Return the default deterministic lens set for worker/MCP callers."""
    registry: dict[str, Lens] = {
        StructuralLens.name: StructuralLens(),
        ProductionValidationLens.name: ProductionValidationLens(),
        ArchitectureLens.name: ArchitectureLens(),
        TestQualityLens.name: TestQualityLens(),
    }
    for name, lens in registry.items():
        lens.version = lens_version(name)
        lens.not_flagged = lens_not_flagged(name)
    return registry


def lens_version(name: str) -> str:
    """Return the configured version stamp for a lens entry."""
    return _LENS_VERSIONS.get(name, _DEFAULT_VERSION)


def lens_not_flagged(name: str) -> tuple[str, ...]:
    """Return scope-honesty notes for a lens entry."""
    return _LENS_NOT_FLAGGED.get(name, ())


LENS_REGISTRY: dict[str, Lens] = all_lenses()
