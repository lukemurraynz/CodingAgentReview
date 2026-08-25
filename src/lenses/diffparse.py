"""Unified-diff parsing into lens-ready files.

Reconstructs the new-side view per path: context+added lines with real new-file
line numbers; removed lines excluded. added_lines = lines introduced by this
change (true '+' lines). This is a diff-scope reconstruction, not full-file —
documented ceiling until full-content fetch lands with graph work.
"""

from __future__ import annotations

from dataclasses import dataclass

from .base import LensFile


@dataclass
class _HunkState:
    path: str
    new_lineno: int


def parse_unified_diff(diff: str) -> list[LensFile]:
    """Parse unified diff text → one LensFile per touched path."""
    files: dict[str, dict[int, str]] = {}
    added: dict[str, set[int]] = {}
    order: list[str] = []

    current_path: str | None = None
    new_lineno = 0
    for raw in diff.splitlines():
        if raw.startswith("+++ b/"):
            current_path = raw[6:].strip()
            if current_path not in files:
                files[current_path] = {}
                added[current_path] = set()
                order.append(current_path)
            continue
        if raw.startswith("--- ") or raw.startswith("diff --git") or raw.startswith("index "):
            continue
        if raw.startswith("@@"):
            # @@ -a,b +c,d @@
            plus = raw.split("+", 1)[1] if "+" in raw else ""
            num = plus.split(",", 1)[0].split(" ", 1)[0]
            new_lineno = int(num) if num else 1
            continue
        if current_path is None:
            continue
        if raw.startswith("+"):
            files[current_path][new_lineno] = raw[1:]
            added[current_path].add(new_lineno)
            new_lineno += 1
        elif raw.startswith("-"):
            continue
        else:
            files[current_path][new_lineno] = raw[1:] if raw.startswith(" ") else raw
            new_lineno += 1

    out: list[LensFile] = []
    for path in order:
        content_map = files[path]
        if not content_map:
            continue
        lines = [content_map[n] for n in sorted(content_map)]
        out.append(
            LensFile(
                path=path,
                content="\n".join(lines),
                added_lines=frozenset(added[path]),
            )
        )
    return out
