"""Failure attribution helpers (FR-024)."""

from __future__ import annotations


def classify_failure(exc: Exception) -> str:
    """Map a lens failure to the V1 attribution bucket."""
    text = f"{type(exc).__name__}: {exc}".lower()
    if "timeout" in text or "timed out" in text or "connection" in text:
        return "dependency_error"
    if "rate" in text and ("limit" in text or "429" in text):
        return "dependency_error"
    if "auth" in text or "401" in text or "403" in text or "permission" in text:
        return "environment_error"
    return "product_defect"
