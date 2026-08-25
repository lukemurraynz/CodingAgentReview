"""Lens contract (FR-006/007): composable, independently runnable review lenses.

Lenses are pure functions of a LensContext — no network, no filesystem,
deterministic for identical input. LLM-backed lenses receive model access via
the worker's injected client; deterministic lenses ignore it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from harness.models import Finding


@dataclass(frozen=True)
class LensFile:
    path: str
    content: str
    added_lines: frozenset[int] = field(default_factory=frozenset)


@dataclass(frozen=True)
class LensContext:
    change_id: str
    repo_id: str
    files: Sequence[LensFile]
    # Model client for LLM lenses (azure openai-compatible async client or None).
    # Deterministic lenses never touch it. None ⇒ LLM lenses must raise.
    model_client: object | None = None
    model_deployment: str = ""


@runtime_checkable
class Lens(Protocol):
    name: str

    def run(self, ctx: LensContext) -> Awaitable[list[Finding]]: ...
