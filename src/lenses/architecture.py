"""Architecture lens — deterministic heuristics for FR-007 / T060.

The lens stays evidence-first: it flags only what the diff shows. Heuristics are
honest ceilings, not architecture proof:

- layering violations: low-level modules importing from entrypoint/UI/test layers
- god-files: one file changed heavily enough to dominate the diff or exceed a size ceiling
- public API changes without version/doc hint: exported surface changed without docs/version updates
- cross-cutting concerns in wrong layer: logging/env/http concern added inside domain/model layers

Thresholds are intentionally small and commented inline so reviewers can tune
them once real benchmark data exists.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Final

from harness.dedup import make_key
from harness.models import Evidence, Finding, FindingCategory, Severity

from ._line_mapping import actual_lineno, mapped_lines
from .base import Lens, LensContext, LensFile

# ponytail: 400 lines is a reviewability heuristic, not a correctness boundary.
_GOD_FILE_LINE_LIMIT: Final = 400
# ponytail: 120 changed lines in one file is enough to dominate most reviews.
_GOD_FILE_ADDED_LIMIT: Final = 120
# ponytail: >60% of all added lines in one file means the change is concentrated there.
_GOD_FILE_SHARE_LIMIT: Final = 0.6
# ponytail: tiny diffs always have 100% share; require a meaningful review-sized diff first.
_GOD_FILE_MIN_TOTAL_ADDED: Final = 40

_ENTRYPOINT_PARTS: Final = {"api", "app", "controlplane", "mcpserver", "runner", "main", "cli", "ui"}
_LOW_LEVEL_PARTS: Final = {"domain", "model", "models", "entity", "core", "service", "services", "lenses"}
_DOC_HINT_PARTS: Final = {"docs", "doc", "changelog", "version", "release_notes"}
_EXPORT_LINE = re.compile(
    r"^(?:class|def)\s+([A-Z][A-Za-z0-9_]*|[a-z][A-Za-z0-9_]*)|^([A-Z][A-Z0-9_]+)\s*=",
    re.MULTILINE,
)
_VERSION_HINT = re.compile(r"\b(v\d+|version|deprecated|breaking|compat)\b", re.IGNORECASE)
_ENTRYPOINT_IMPORT = re.compile(
    r"^(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))",
    re.MULTILINE,
)
_CROSS_CUTTING = re.compile(
    r"\b(?:logging\.|getLogger\(|logger\.|os\.environ|os\.getenv|requests\.|httpx\.|FastAPI\b|APIRouter\b)"
)


def _path_parts(path: str) -> tuple[str, ...]:
    return tuple(part.lower() for part in PurePosixPath(path).parts)


def _is_public_api_file(path: str) -> bool:
    parts = _path_parts(path)
    return "api" in parts or PurePosixPath(path).name == "__init__.py"


def _is_docs_or_version_file(path: str) -> bool:
    parts = _path_parts(path)
    return any(part in _DOC_HINT_PARTS for part in parts)


def _is_low_level_file(path: str) -> bool:
    parts = _path_parts(path)
    return any(part in _LOW_LEVEL_PARTS for part in parts)


def _new_public_symbols(lf: LensFile) -> list[tuple[int, str]]:
    symbols: list[tuple[int, str]] = []
    for compact_lineno, line in mapped_lines(lf.content, lf.line_map):
        if compact_lineno not in lf.added_lines:
            continue
        if line.startswith("_"):
            continue
        match = _EXPORT_LINE.match(line)
        if not match:
            continue
        symbol = match.group(1) or match.group(2)
        if symbol:
            actual = lf.line_map[compact_lineno - 1] if lf.line_map else compact_lineno
            symbols.append((actual, symbol))
    return symbols


class ArchitectureLens(Lens):
    name = "architecture"

    async def run(self, ctx: LensContext) -> list[Finding]:
        findings: list[Finding] = []
        total_added = sum(len(lf.added_lines) for lf in ctx.files)
        docs_touched = any(_is_docs_or_version_file(lf.path) for lf in ctx.files)

        for lf in ctx.files:
            findings.extend(self._layering_findings(ctx, lf))
            findings.extend(self._god_file_findings(ctx, lf, total_added))
            if _is_public_api_file(lf.path) and not docs_touched:
                findings.extend(self._public_api_findings(ctx, lf))
            if _is_low_level_file(lf.path):
                findings.extend(self._cross_cutting_findings(ctx, lf))

        findings.sort(key=lambda f: (f.evidence[0].path, f.evidence[0].line_start or 0, f.title))
        return findings

    def _finding(
        self,
        ctx: LensContext,
        path: str,
        content: str,
        lineno: int,
        rule: str,
        severity: Severity,
        title: str,
        detail: str,
        metrics: dict[str, float] | None = None,
    ) -> Finding:
        return Finding(
            id=f"{self.name}:{rule}:{path}:{lineno}",
            change_id=ctx.change_id,
            repo_id=ctx.repo_id,
            category=FindingCategory.ARCHITECTURE,
            severity=severity,
            title=title,
            detail=detail,
            evidence=[
                Evidence(
                    path=path,
                    line_start=lineno,
                    line_end=lineno,
                    rule_id=rule,
                    metrics=metrics or {},
                )
            ],
            dedup_key=make_key(path, content),
        )

    def _layering_findings(self, ctx: LensContext, lf: LensFile) -> list[Finding]:
        findings: list[Finding] = []
        if not _is_low_level_file(lf.path):
            return findings
        for match in _ENTRYPOINT_IMPORT.finditer(lf.content):
            lineno = actual_lineno(lf.content, match.start(), lf.line_map)
            compact_lineno = lf.content[: match.start()].count("\n") + 1
            if compact_lineno not in lf.added_lines:
                continue
            imported = match.group(1) or match.group(2) or ""
            imported_parts = {part.lower() for part in imported.split(".")}
            if imported_parts.intersection(_ENTRYPOINT_PARTS):
                findings.append(
                    self._finding(
                        ctx,
                        lf.path,
                        lf.content,
                        lineno,
                        "architecture.layering-violation",
                        Severity.HIGH,
                        "low-level module now depends on entrypoint layer",
                        f"Import '{imported}' pulls app/API concerns into {lf.path}; prefer dependency inversion.",
                    )
                )
        return findings

    def _god_file_findings(self, ctx: LensContext, lf: LensFile, total_added: int) -> list[Finding]:
        line_count = len(lf.content.splitlines())
        added_count = len(lf.added_lines)
        if added_count == 0:
            return []
        share = added_count / total_added if total_added else 0.0
        share_triggered = total_added >= _GOD_FILE_MIN_TOTAL_ADDED and share > _GOD_FILE_SHARE_LIMIT
        if line_count <= _GOD_FILE_LINE_LIMIT and added_count < _GOD_FILE_ADDED_LIMIT and not share_triggered:
            return []
        severity = (
            Severity.HIGH
            if added_count >= _GOD_FILE_ADDED_LIMIT or share_triggered
            else Severity.MEDIUM
        )
        return [
            self._finding(
                ctx,
                lf.path,
                lf.content,
                1,
                "architecture.god-file",
                severity,
                "change is concentrated in one large file",
                "Large, concentrated edits in one file usually hide multiple responsibilities; "
                "consider extracting a seam.",
                {
                    "line_count": float(line_count),
                    "added_lines": float(added_count),
                    "change_share": round(share, 3),
                },
            )
        ]

    def _public_api_findings(self, ctx: LensContext, lf: LensFile) -> list[Finding]:
        findings: list[Finding] = []
        if _VERSION_HINT.search("\n".join(lf.patch_lines)):
            return findings
        for lineno, symbol in _new_public_symbols(lf):
            findings.append(
                self._finding(
                    ctx,
                    lf.path,
                    lf.content,
                    lineno,
                    "architecture.public-api-change",
                    Severity.MEDIUM,
                    "public API surface changed without versioning/docs hint",
                    f"Export '{symbol}' was added in {lf.path}, but the diff contains no doc/version "
                    "compatibility note.",
                )
            )
        return findings

    def _cross_cutting_findings(self, ctx: LensContext, lf: LensFile) -> list[Finding]:
        findings: list[Finding] = []
        for compact_lineno, line in mapped_lines(lf.content, lf.line_map):
            if compact_lineno not in lf.added_lines:
                continue
            if not _CROSS_CUTTING.search(line):
                continue
            actual = lf.line_map[compact_lineno - 1] if lf.line_map else compact_lineno
            findings.append(
                self._finding(
                    ctx,
                    lf.path,
                    lf.content,
                    actual,
                    "architecture.cross-cutting-layer",
                    Severity.MEDIUM,
                    "cross-cutting concern added in low-level layer",
                    f"Added line in {lf.path} wires logging/env/http concern directly into a low-level module.",
                )
            )
        return findings
