"""Lens contract (FR-006/007): composable, independently runnable review lenses.

Lenses are pure functions of a LensContext — no network, no filesystem,
deterministic for identical input. LLM-backed lenses receive model access via
the worker's injected client; deterministic lenses ignore it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from harness.models import Finding

if TYPE_CHECKING:
    from graph.symbols import SymbolIndex
else:
    SymbolIndex = Any


@dataclass(frozen=True)
class LensFile:
    path: str
    content: str
    added_lines: frozenset[int] = field(default_factory=frozenset)
    line_map: tuple[int, ...] = ()
    patch_lines: tuple[str, ...] = ()
    deleted: bool = False
    old_path: str | None = None


@dataclass(frozen=True)
class LensContext:
    change_id: str
    repo_id: str
    files: Sequence[LensFile]
    # Model client for LLM lenses (azure openai-compatible async client or None).
    # Deterministic lenses never touch it. None ⇒ LLM lenses must raise.
    model_client: object | None = None
    model_deployment: str = ""
    symbol_index: SymbolIndex | None = None


@runtime_checkable
class Lens(Protocol):
    name: str
    version: str
    not_flagged: tuple[str, ...]

    def run(self, ctx: LensContext) -> Awaitable[list[Finding]]: ...
