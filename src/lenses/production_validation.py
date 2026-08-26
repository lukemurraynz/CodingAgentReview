"""Production-validation lens — deterministic heuristic port of the silent-failure taxonomy.

Source methodology: production-validation skill (defined ≠ wired ≠ invoked ≠
working ≠ observable) + portable static_gate heuristics. These are static
heuristics; full declared-vs-deployed-vs-observed verification arrives with the
engineering graph (FR-033) in a later milestone — documented ceiling.
"""

from __future__ import annotations

import re

from graph.symbols import REGISTRATION_REGEX
from harness.dedup import make_key
from harness.models import Evidence, Finding, FindingCategory, Severity

from .base import Lens, LensContext, LensFile

_INVOCATION = re.compile(r"\b(\w+)\s*\(")
_SUCCESS_LITERAL = re.compile(
    r"return\s+(?:['\"](?:complete|ok|success|done)['\"]|\bTrue\b|\b200\b)"
)
# Success marker anywhere in an except body — covers `return {'status': 'ok'}` dicts too.
_SUCCESS_MARKER = re.compile(r"['\"](?:complete[d]?|ok|success(?:ful)?|done)['\"]", re.IGNORECASE)
_EXPLICIT_FAILURE_MARKER = re.compile(r"['\"]success['\"]\s*:\s*False", re.IGNORECASE)
_AWAIT_OR_CALL = re.compile(r"\b(?:await\s+\w+[\w.]*(?:\([^)]*\))?|\w+[\w.]*\s*\([^)]*\))")
_FALLBACK_BRANCH = re.compile(r"(?:if\s+\w+\s+is\s+None\s*:|except\b)")
_TELEMETRY_CALL = re.compile(
    r"\b(logger|logging|\w*_?logger|meter|telemetry|track_|record_|emit_)\b", re.IGNORECASE
)


def _all_invocations(ctx: LensContext) -> set[str]:
    """Names invoked anywhere in the change context."""
    invoked: set[str] = set()
    for lf in ctx.files:
        for m in _INVOCATION.finditer(lf.content):
            invoked.add(m.group(1))
    return invoked


def _finding(
    ctx: LensContext,
    path: str,
    content: str,
    lineno: int,
    rule: str,
    severity: Severity,
    title: str,
    detail: str,
) -> Finding:
    return Finding(
        id=f"{ctx.change_id}:{rule}:{path}:{lineno}",
        change_id=ctx.change_id,
        repo_id=ctx.repo_id,
        category=FindingCategory.PRODUCTION_VALIDATION,
        severity=severity,
        title=title,
        detail=detail,
        evidence=[Evidence(path=path, line_start=lineno, line_end=lineno, rule_id=rule)],
        dedup_key=make_key(path, content),
    )


def _line_of(content: str, pos: int) -> int:
    return content[:pos].count("\n") + 1


class ProductionValidationLens(Lens):
    name = "production_validation"
    version = "1"
    not_flagged = (
        "Does not exercise live deployments or runtime infrastructure state.",
    )

    async def run(self, ctx: LensContext) -> list[Finding]:
        findings: list[Finding] = []
        invoked = _all_invocations(ctx)

        for lf in ctx.files:
            lines = lf.content.splitlines()
            findings.extend(self._registered_not_invoked(ctx, lf, lines, invoked))
            findings.extend(self._swallowed_failure(ctx, lf))
            findings.extend(self._missing_telemetry_fallback(ctx, lf))

        findings.sort(key=lambda f: (f.evidence[0].path, f.evidence[0].line_start or 0, f.title))
        return findings

    def _registered_not_invoked(
        self, ctx: LensContext, lf: LensFile, lines: list[str], invoked: set[str]
    ) -> list[Finding]:
        """Flag DI registrations with no callers, using repo-wide counts when a trusted symbol index exists.

        This closes the earlier diff-local ceiling without changing behavior for callers that do not supply a
        trusted symbol index.
        """

        del lines
        out: list[Finding] = []
        for m in REGISTRATION_REGEX.finditer(lf.content):
            registered_name = m.group(1) or m.group(2)
            if not registered_name:
                continue
            referenced = False
            if ctx.symbol_index is not None:
                referenced = ctx.symbol_index.is_referenced_elsewhere(registered_name)
            else:
                # Invoked = constructed/called somewhere OTHER than this registration line.
                other_calls = sum(
                    len(re.findall(rf"\b{re.escape(registered_name)}\s*\(", other.content))
                    for other in ctx.files
                )
                this_line_calls = len(re.findall(rf"\b{re.escape(registered_name)}\s*\(", m.group(0)))
                referenced = other_calls - this_line_calls > 0 or registered_name in invoked
            if not referenced:
                lineno = _line_of(lf.content, m.start())
                out.append(
                    _finding(
                        ctx,
                        lf.path,
                        lf.content,
                        lineno,
                        "prodval.registered-not-invoked",
                        Severity.HIGH,
                        f"'{registered_name}' registered but never invoked",
                        "Component is wired into the container but no code path calls it — "
                        "defined ≠ invoked (silent-failure taxonomy).",
                    )
                )
        return out

    def _swallowed_failure(self, ctx: LensContext, lf) -> list[Finding]:
        out: list[Finding] = []
        content = lf.content
        # Block-aware scan: find except blocks whose body returns success literals.
        lines = content.splitlines()
        i = 0
        while i < len(lines):
            if re.match(r"^\s*except\b", lines[i]):
                base_indent = len(lines[i]) - len(lines[i].lstrip())
                j = i + 1
                body: list[str] = []
                while j < len(lines):
                    line = lines[j]
                    if not line.strip():
                        body.append(line)
                        j += 1
                        continue
                    indent = len(line) - len(line.lstrip())
                    if indent <= base_indent:
                        break
                    body.append(line)
                    j += 1
                body_text = "\n".join(body)
                if (
                    (_SUCCESS_LITERAL.search(body_text) or _SUCCESS_MARKER.search(body_text))
                    and not _EXPLICIT_FAILURE_MARKER.search(body_text)
                    and not re.search(r"\braise\b", body_text)
                    and _AWAIT_OR_CALL.search("\n".join(lines[max(0, i - 6):i]))
                ):
                    out.append(
                        _finding(
                            ctx, lf.path, content, i + 1,
                            "prodval.swallowed-final-failure",
                            Severity.HIGH,
                            "failure swallowed; success returned after attempted operation",
                            "except-path returns a success literal while the operation result "
                            "is discarded — working ≠ reported (taxonomy).",
                        )
                    )
                i = j
            else:
                i += 1
        return out

    def _missing_telemetry_fallback(self, ctx: LensContext, lf) -> list[Finding]:
        out: list[Finding] = []
        lines = lf.content.splitlines()
        for i, line in enumerate(lines):
            if _FALLBACK_BRANCH.search(line):
                s = i + 1
                e = min(len(lines), s + 12)
                branch = "\n".join(lines[s:e])
                primary_window = "\n".join(lines[max(0, i - 10):i])
                fallback_has_tel = bool(_TELEMETRY_CALL.search(branch))
                primary_has_tel = bool(_TELEMETRY_CALL.search(primary_window))
                if primary_has_tel and not fallback_has_tel and branch.strip():
                    out.append(
                        _finding(
                            ctx, lf.path, lf.content, i + 1,
                            "prodval.missing-telemetry-fallback",
                            Severity.MEDIUM,
                            "fallback branch emits no telemetry while primary path does",
                            "Observable gap: degraded/fallback execution is invisible to "
                            "dashboards (observable ≠ working).",
                        )
                    )
        return out
