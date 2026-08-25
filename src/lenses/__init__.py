"""Review lens registry (FR-006/007). Deterministic lenses ship here; LLM-backed
lenses register from the worker once model access is configured."""

from .base import Lens, LensContext, LensFile
from .production_validation import ProductionValidationLens
from .structural import StructuralLens

__all__ = [
    "LENS_REGISTRY",
    "Lens",
    "LensContext",
    "LensFile",
    "ProductionValidationLens",
    "StructuralLens",
]

LENS_REGISTRY: dict[str, Lens] = {
    StructuralLens.name: StructuralLens(),
    ProductionValidationLens.name: ProductionValidationLens(),
}
