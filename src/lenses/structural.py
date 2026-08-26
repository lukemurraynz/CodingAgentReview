"""Structural quality lens — deterministic port of the thermo-nuclear methodology.

Source methodology: thermo-nuclear-code-quality-review skill (fake-wiring scan
classes, 1k-line growth rule). Findings anchor ONLY to added lines and cite
exact matched evidence; unsupported suspicions are never emitted.
"""

from __future__ import annotations

import re
from typing import Final

from harness.dedup import make_key
from harness.models import Evidence, Finding, FindingCategory, Severity

from ._line_mapping import actual_lineno, mapped_lines
from .base import Lens, LensContext, LensFile

_LINE_COUNT_LIMIT: Final = 1000

_NOOP_CLASS = re.compile(r"class\s+(_*\w*Noop\w*(?:Subscriber|Handler|Client|Listener|Detector))\b")
_NOOP_INSTANTIATION = re.compile(r"\b(_*\w*Noop\w*(?:Subscriber|Handler|Client|Listener|Detector))\s*\(")
_BROAD_EXCEPT = re.compile(r"except\s+(?:Exception\b|BaseException\b|:)")
_LOG_CALL = re.compile(r"\b(logging\.|\w*logger\.log(?:ger)?\.|_?logger\.)", re.IGNORECASE)
_SILENT_CONTINUE = re.compile(r"^\s*(continue|return\b)", re.MULTILINE)
_VERB_OF_WORK = re.compile(r"def\s+(process|handle|run|sync|deploy|validate|execute|publish)\w*", re.IGNORECASE)
_FABRICATED_RETURN = re.compile(r"return\s+(['\"](?:complete|ok|success|done)['\"]|\b200\b|\bTrue\b)")
_FABRICATED_ASSIGN = re.compile(
    r"^\s*(\w+)\s*=\s*(['\"](?:complete|ok|success|done)['\"]|\b200\b|\bTrue\b)\s*(?:#.*)?$",
    re.MULTILINE,
)
_EFFECT_CALL = re.compile(r"(await\s+\w+\(|\w+\([^)]*\))")
_OPTIONAL_NOOP_DEFAULT = re.compile(
    r"=\s*_*\w*Noop\w*(?:Subscriber|Handler|Client|Listener|Detector)?\s*\(\)|or\s+_?\w*Noop\w*\(\)"
)


def _added_blocks(content: str, added: frozenset[int]) -> list[tuple[int, list[str]]]:
    """Contiguous runs of added lines as (start_lineno, lines)."""
    blocks: list[tuple[int, list[str]]] = []
    current: list[str] = []
    start = 0
    for lineno, line in enumerate(content.splitlines(), start=1):
        if lineno in added:
            if not current:
                start = lineno
            current.append(line)
        elif current:
            blocks.append((start, current))
            current = []
    if current:
        blocks.append((start, current))
    return blocks


def _added_blocks_with_map(
    content: str, added: frozenset[int], line_map: tuple[int, ...]
) -> list[tuple[int, list[str], list[int]]]:
    """Contiguous runs of added lines with compact and actual line numbers."""
    blocks: list[tuple[int, list[str], list[int]]] = []
    current_lines: list[str] = []
    current_actuals: list[int] = []
    start = 0
    for compact_lineno, line in mapped_lines(content, line_map):
        if compact_lineno in added:
            if not current_lines:
                start = compact_lineno
            current_lines.append(line)
            current_actuals.append(line_map[compact_lineno - 1] if line_map else compact_lineno)
        elif current_lines:
            blocks.append((start, current_lines, current_actuals))
            current_lines = []
            current_actuals = []
    if current_lines:
        blocks.append((start, current_lines, current_actuals))
    return blocks


def _find_block_span(lines: list[str], idx: int) -> tuple[int, int]:
    """Return (start_idx, end_idx_exclusive) of the indented block starting at idx."""
    base_indent = len(lines[idx]) - len(lines[idx].lstrip())
    end = len(lines)
    for j in range(idx + 1, len(lines)):
        line = lines[j]
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= base_indent:
            end = j
            break
        end = j + 1
    return idx, end


class StructuralLens(Lens):
    name = "structural"
    version = "1"
    not_flagged = (
        "Does not validate business correctness or external runtime behavior.",
    )

    async def run(self, ctx: LensContext) -> list[Finding]:
        findings: list[Finding] = []
        for lf in ctx.files:
            findings.extend(self._scan_file(ctx, lf))
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
            category=FindingCategory.STRUCTURAL,
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

    def _scan_file(self, ctx: LensContext, lf: LensFile) -> list[Finding]:
        findings: list[Finding] = []
        path = lf.path
        content = lf.content
        added = lf.added_lines
        lines = content.splitlines()

        # Rule: file-growth (whole-file metric; anchored at line 1)
        if len(lines) > _LINE_COUNT_LIMIT:
            findings.append(
                self._finding(
                    ctx, path, content, 1,
                    "structural.file-growth",
                    Severity.MEDIUM,
                    f"file grew past {_LINE_COUNT_LIMIT} lines ({len(lines)})",
                    "Prefer extracting helpers/submodules over letting a file sprawl "
                    "(thermo-nuclear 1k-line rule).",
                    {"line_count": float(len(lines))},
                )
            )

        for _start, block, actuals in _added_blocks_with_map(content, added, lf.line_map):
            text = "\n".join(block)

            # Rule: noop-wiring — instantiation of a *Noop* class in production code
            for m in _NOOP_INSTANTIATION.finditer(text):
                lineno = actuals[text[: m.start()].count("\n")]
                findings.append(
                    self._finding(
                        ctx, path, content, lineno,
                        "structural.noop-wiring",
                        Severity.BLOCKER,
                        f"no-op component '{m.group(1)}' wired in production path",
                        f"'{m.group(1)}' instantiated on an added line. No-op components "
                        "silently drop events/work while appearing functional.",
                    )
                )

            # Rule: silent-fallback — broad except + log + continue/degraded return
            for i, block_line in enumerate(block):
                stripped = block_line.strip()
                if _BROAD_EXCEPT.search(stripped):
                    s, e = _find_block_span(block, i)
                    body = "\n".join(block[s:e])
                    if _LOG_CALL.search(body) and (
                        _SILENT_CONTINUE.search(body) or not re.search(r"\braise\b", body)
                    ):
                        lineno = actuals[i]
                        findings.append(
                            self._finding(
                                ctx, path, content, lineno,
                                "structural.silent-fallback",
                                Severity.HIGH,
                                "broad exception logged then swallowed",
                                "Broad except logs a warning but continues with partial/no data. "
                                "Error must propagate or be handled explicitly.",
                            )
                        )

            # Rule: fabricated-status — verb-of-work returning hardcoded success,
            # directly (return "ok") or via a variable assigned a success literal.
            for i, block_line in enumerate(block):
                if _VERB_OF_WORK.search(block_line):
                    s, e = _find_block_span(block, i)
                    body = "\n".join(block[s:e])
                    # Exclude the def signature itself from effect detection —
                    # parameter lists look like calls to a naive matcher.
                    body_no_sig = "\n".join(body.splitlines()[1:])
                    returns = _FABRICATED_RETURN.findall(body)
                    for assign in _FABRICATED_ASSIGN.finditer(body):
                        var = assign.group(1)
                        if re.search(rf"return\s+{re.escape(var)}\b", body):
                            returns.append(assign.group(2))
                    observable = [
                        m.group(0)
                        for m in _EFFECT_CALL.finditer(body_no_sig)
                        if not _FABRICATED_RETURN.search(m.group(0))
                    ]
                    if returns and not observable:
                        lineno = actuals[i]
                        findings.append(
                            self._finding(
                                ctx, path, content, lineno,
                                "structural.fabricated-status",
                                Severity.HIGH,
                                "work-method returns fabricated success without observable effect",
                                f"Method reports success ({', '.join(sorted(set(returns)))}) but its "
                                "body performs no observable operation.",
                            )
                        )

            # Rule: optional-noop-default — fallback construction in signatures/bodies
            for m in _OPTIONAL_NOOP_DEFAULT.finditer(text):
                lineno = actuals[text[: m.start()].count("\n")]
                findings.append(
                    self._finding(
                        ctx, path, content, lineno,
                        "structural.optional-noop-default",
                        Severity.MEDIUM,
                        "optional dependency defaults to a no-op implementation",
                        "Production wiring must inject a real dependency; defaulting to a "
                        "no-op hides unwired paths.",
                    )
                )

        # Rule: noop-class definition itself is suspicious only if instantiated elsewhere;
        # definitions alone are not flagged here (evidence-grounded discipline).
        for m in _NOOP_CLASS.finditer(content):
            cls_name = m.group(1)
            uses_elsewhere = sum(
                len(_NOOP_INSTANTIATION.findall(other.content))
                for other in ctx.files
                if other.content != content
            )
            if uses_elsewhere == 0:
                continue
            lineno = actual_lineno(content, m.start(), lf.line_map)
            if lineno in added:
                findings.append(
                    self._finding(
                        ctx, path, content, lineno,
                        "structural.noop-wiring",
                        Severity.BLOCKER,
                        f"no-op component class '{cls_name}' defined and used in production paths",
                        f"'{cls_name}' is instantiated outside tests in provided change context.",
                    )
                )

        return findings
