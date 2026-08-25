"""Unified-diff parsing into lens-ready files.

Reconstructs the new-side view per path: context+added lines with real new-file
line numbers; removed lines excluded. added_lines = compact content line numbers
introduced by this change. Deleted files retain patch metadata so deterministic
lenses can reason about removed tests without fabricating full file contents.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .base import LensFile

_HUNK_HEADER = re.compile(r"^@@ -(?P<old>\d+)(?:,\d+)? \+(?P<new>\d+)(?:,\d+)? @@")


@dataclass(slots=True)
class _ParsedFile:
    path: str
    old_path: str | None = None
    deleted: bool = False
    new_lines: dict[int, str] = field(default_factory=dict)
    added_actual_lines: set[int] = field(default_factory=set)
    patch_lines: list[str] = field(default_factory=list)


def _normalize_path(raw: str) -> str | None:
    stripped = raw.strip()
    if stripped == "/dev/null":
        return None
    if stripped.startswith("a/") or stripped.startswith("b/"):
        return stripped[2:]
    return stripped


def _ensure_file(
    files: dict[str, _ParsedFile],
    order: list[str],
    path: str,
    old_path: str | None,
    deleted: bool,
) -> _ParsedFile:
    parsed = files.get(path)
    if parsed is None:
        parsed = _ParsedFile(path=path, old_path=old_path, deleted=deleted)
        files[path] = parsed
        order.append(path)
    else:
        parsed.old_path = old_path or parsed.old_path
        parsed.deleted = parsed.deleted or deleted
    return parsed


def parse_unified_diff(diff: str) -> list[LensFile]:
    """Parse unified diff text into lens-ready files without raising on malformed hunks."""
    files: dict[str, _ParsedFile] = {}
    order: list[str] = []
    current: _ParsedFile | None = None
    current_old_path: str | None = None
    pending_new_path: str | None = None
    in_hunk = False
    new_lineno = 0

    for raw in diff.splitlines():
        if raw.startswith("diff --git "):
            current = None
            current_old_path = None
            pending_new_path = None
            in_hunk = False
            parts = raw.split()
            if len(parts) >= 4:
                current_old_path = _normalize_path(parts[2])
                pending_new_path = _normalize_path(parts[3])
            continue

        if raw.startswith("Binary files ") or raw == "GIT binary patch":
            current = None
            in_hunk = False
            continue

        if raw.startswith("--- "):
            current_old_path = _normalize_path(raw[4:])
            continue

        if raw.startswith("+++ "):
            new_path = _normalize_path(raw[4:])
            path = new_path or current_old_path or pending_new_path
            if path is None:
                current = None
                continue
            current = _ensure_file(
                files,
                order,
                path=path,
                old_path=current_old_path,
                deleted=new_path is None,
            )
            in_hunk = False
            continue

        hunk = _HUNK_HEADER.match(raw)
        if hunk:
            if current is None and pending_new_path is not None:
                current = _ensure_file(
                    files,
                    order,
                    path=pending_new_path,
                    old_path=current_old_path,
                    deleted=False,
                )
            if current is None:
                in_hunk = False
                continue
            new_lineno = int(hunk.group("new"))
            current.patch_lines.append(raw)
            in_hunk = True
            continue

        if not in_hunk or current is None:
            continue

        if raw.startswith("\\ No newline at end of file"):
            continue

        marker = raw[:1]
        if marker == "+":
            current.new_lines[new_lineno] = raw[1:]
            current.added_actual_lines.add(new_lineno)
            current.patch_lines.append(raw)
            new_lineno += 1
            continue
        if marker == "-":
            current.patch_lines.append(raw)
            continue
        if marker == " ":
            current.new_lines[new_lineno] = raw[1:]
            current.patch_lines.append(raw)
            new_lineno += 1
            continue

    parsed_files: list[LensFile] = []
    for path in order:
        parsed = files[path]
        sorted_actuals = sorted(parsed.new_lines)
        compact_lines = [parsed.new_lines[actual] for actual in sorted_actuals]
        line_map = tuple(sorted_actuals)
        added_lines = frozenset(
            index for index, actual in enumerate(sorted_actuals, start=1) if actual in parsed.added_actual_lines
        )

        if not compact_lines and not parsed.deleted:
            continue

        parsed_files.append(
            LensFile(
                path=parsed.path,
                old_path=parsed.old_path,
                deleted=parsed.deleted,
                content="\n".join(compact_lines),
                added_lines=added_lines,
                line_map=line_map,
                patch_lines=tuple(parsed.patch_lines),
            )
        )
    return parsed_files
