"""Test-quality lens — deterministic heuristics for FR-007 / T061.

Heuristics stay diff-scoped and intentionally conservative:

- changed production file with no corresponding changed test file
- test-only changes that remove more assertions than they add
- deleted test files
- added skip/xfail/disabled-test markers

These signals flag review hotspots; they do not prove correctness or intent.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Final

from harness.dedup import make_key
from harness.models import Evidence, Finding, FindingCategory, Severity

from .base import Lens, LensContext, LensFile

_ASSERT_LINE: Final = re.compile(r"\b(assert\b|pytest\.raises\(|self\.assert\w+\()")
_SKIP_LINE: Final = re.compile(r"pytest\.mark\.(?:skip|skipif|xfail)\b|pytest\.skip\(|@unittest\.skip|self\.skipTest\(")


def _is_test_path(path: str) -> bool:
    name = PurePosixPath(path).name.lower()
    parts = {part.lower() for part in PurePosixPath(path).parts}
    return "tests" in parts or name.startswith("test_") or name.endswith("_test.py")


def _normalized_stem(path: str) -> str:
    stem = PurePosixPath(path).stem.lower()
    if stem.startswith("test_"):
        stem = stem[5:]
    if stem.endswith("_test"):
        stem = stem[:-5]
    return stem


def _corresponds(prod_path: str, test_path: str) -> bool:
    prod_stem = _normalized_stem(prod_path)
    test_stem = _normalized_stem(test_path)
    if prod_stem and prod_stem == test_stem:
        return True
    prod_parts = {part.lower() for part in PurePosixPath(prod_path).parts}
    test_parts = {part.lower() for part in PurePosixPath(test_path).parts}
    return len(prod_parts.intersection(test_parts)) > 1


def _first_added_line(lf: LensFile) -> int:
    if not lf.added_lines:
        return 1
    compact = min(lf.added_lines)
    if lf.line_map and compact <= len(lf.line_map):
        return lf.line_map[compact - 1]
    return compact


class TestQualityLens(Lens):
    name = "test_quality"

    async def run(self, ctx: LensContext) -> list[Finding]:
        findings: list[Finding] = []
        test_files = [lf for lf in ctx.files if _is_test_path(lf.path)]
        prod_files = [lf for lf in ctx.files if not _is_test_path(lf.path)]

        findings.extend(self._missing_tests(ctx, prod_files, test_files))
        findings.extend(self._test_only_weakening(ctx, prod_files, test_files))
        findings.extend(self._deleted_tests(ctx, test_files))
        for lf in test_files:
            findings.extend(self._skip_markers(ctx, lf))

        findings.sort(key=lambda f: (f.evidence[0].path, f.evidence[0].line_start or 0, f.title))
        return findings

    def _finding(
        self,
        ctx: LensContext,
        lf: LensFile,
        lineno: int,
        rule: str,
        severity: Severity,
        title: str,
        detail: str,
        metrics: dict[str, float] | None = None,
    ) -> Finding:
        return Finding(
            id=f"{self.name}:{rule}:{lf.path}:{lineno}",
            change_id=ctx.change_id,
            repo_id=ctx.repo_id,
            category=FindingCategory.TEST_QUALITY,
            severity=severity,
            title=title,
            detail=detail,
            evidence=[
                Evidence(
                    path=lf.path,
                    line_start=lineno,
                    line_end=lineno,
                    rule_id=rule,
                    metrics=metrics or {},
                )
            ],
            dedup_key=make_key(lf.path, lf.content),
        )

    def _missing_tests(self, ctx: LensContext, prod_files: list[LensFile], test_files: list[LensFile]) -> list[Finding]:
        findings: list[Finding] = []
        for lf in prod_files:
            if not lf.added_lines:
                continue
            if any(_corresponds(lf.path, test_file.path) for test_file in test_files if not test_file.deleted):
                continue
            findings.append(
                self._finding(
                    ctx,
                    lf,
                    _first_added_line(lf),
                    "test-quality.missing-corresponding-tests",
                    Severity.HIGH,
                    "production change has no corresponding test-file change",
                    f"{lf.path} changed without a matching updated test file in this diff.",
                )
            )
        return findings

    def _test_only_weakening(
        self, ctx: LensContext, prod_files: list[LensFile], test_files: list[LensFile]
    ) -> list[Finding]:
        if prod_files or not test_files:
            return []
        removed_asserts = 0
        added_asserts = 0
        anchor_file = test_files[0]
        for lf in test_files:
            for patch_line in lf.patch_lines:
                if patch_line.startswith("+++") or patch_line.startswith("---") or patch_line.startswith("@@"):
                    continue
                if patch_line.startswith("+") and _ASSERT_LINE.search(patch_line[1:]):
                    added_asserts += 1
                elif patch_line.startswith("-") and _ASSERT_LINE.search(patch_line[1:]):
                    removed_asserts += 1
        if removed_asserts <= added_asserts:
            return []
        return [
            self._finding(
                ctx,
                anchor_file,
                _first_added_line(anchor_file),
                "test-quality.test-only-weakening",
                Severity.HIGH,
                "test-only change weakens assertion coverage",
                "Tests changed without production changes and removed more assertions than they added.",
                {"removed_assertions": float(removed_asserts), "added_assertions": float(added_asserts)},
            )
        ]

    def _deleted_tests(self, ctx: LensContext, test_files: list[LensFile]) -> list[Finding]:
        findings: list[Finding] = []
        for lf in test_files:
            if not lf.deleted:
                continue
            findings.append(
                self._finding(
                    ctx,
                    lf,
                    1,
                    "test-quality.deleted-tests",
                    Severity.HIGH,
                    "test file deleted in change",
                    f"{lf.path} is deleted in this diff; confirm coverage is intentionally removed.",
                )
            )
        return findings

    def _skip_markers(self, ctx: LensContext, lf: LensFile) -> list[Finding]:
        findings: list[Finding] = []
        for compact_lineno, line in enumerate(lf.content.splitlines(), start=1):
            if compact_lineno not in lf.added_lines or not _SKIP_LINE.search(line):
                continue
            actual = lf.line_map[compact_lineno - 1] if lf.line_map else compact_lineno
            findings.append(
                self._finding(
                    ctx,
                    lf,
                    actual,
                    "test-quality.disabled-test-added",
                    Severity.MEDIUM,
                    "skip/xfail marker added to test",
                    "Added test line disables execution via skip/xfail; confirm this is temporary and justified.",
                )
            )
        return findings
