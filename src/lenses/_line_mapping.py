"""Helpers for mapping compact diff content back to real new-file line numbers."""

from __future__ import annotations

from collections.abc import Sequence


def mapped_lines(content: str, line_map: Sequence[int]) -> list[tuple[int, str]]:
    """Return (actual_lineno, line_text) pairs for compact diff content."""
    lines = content.splitlines()
    if line_map and len(line_map) == len(lines):
        return list(zip(line_map, lines, strict=True))
    return list(enumerate(lines, start=1))


def actual_lineno(content: str, position: int, line_map: Sequence[int]) -> int:
    """Translate a string offset into the original new-file line number."""
    compact_lineno = content[:position].count("\n") + 1
    if line_map and 0 < compact_lineno <= len(line_map):
        return line_map[compact_lineno - 1]
    return compact_lineno
