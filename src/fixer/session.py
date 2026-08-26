"""Bounded auto-fix loop over review findings."""

from __future__ import annotations

import difflib
import inspect
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from harness.models import Finding
from lenses.diffparse import parse_unified_diff

from .models import AttemptRecord, FixProposal, FixStatus, ValidationResult

_HUNK_HEADER = re.compile(r"^@@ -(?P<old>\d+)(?:,(?P<old_count>\d+))? \+(?P<new>\d+)(?:,(?P<new_count>\d+))? @@")
_SYSTEM_PROMPT = (
    "You propose minimal unified diffs that fix the reported findings. "
    "Hard rules: touch ONLY files shown in FILE blocks; change only the lines the "
    "finding requires — no refactors, no renames, no new dependencies, no drive-by "
    "formatting; preserve all unrelated code exactly. If a finding cannot be fixed "
    "safely, return an empty diff rather than a speculative one. "
    "Return ONLY the unified diff — no prose before or after."
)
_MAX_ITERATIONS_ENV = "HARNESS_FIX_MAX_ITERATIONS"


class FixSessionError(RuntimeError):
    """Explicit session wiring/configuration failure."""


class _SupportsComplete(Protocol):
    async def complete(self, *, system_prompt: str, user_prompt: str) -> object: ...


class FixValidator(Protocol):
    def __call__(self, diff_text: str, files: Mapping[str, str]) -> object: ...


@dataclass(slots=True)
class _WorkingFile:
    path: str
    original_lines: tuple[str, ...]
    current_lines: list[str]
    original_hunk: str


class FixSession:
    """Run a bounded, explicit fix proposal loop over an in-memory patch state."""

    def __init__(
        self,
        *,
        diff_text: str,
        findings: Sequence[Finding],
        model_client: _SupportsComplete | object,
        validators: Sequence[FixValidator],
        ci_failure_log_text: str | None = None,
        max_iterations: int = 3,
    ) -> None:
        parsed_files = parse_unified_diff(diff_text)
        if not parsed_files:
            raise FixSessionError("diff_text must describe at least one changed file")
        self._findings = tuple(findings)
        self._model_client = model_client
        self._validators = tuple(validators)
        self._ci_failure_log_text = ci_failure_log_text or ""
        self._max_iterations = self._resolve_max_iterations(max_iterations)
        self._files = {
            parsed.path: _WorkingFile(
                path=parsed.path,
                original_lines=tuple(parsed.content.splitlines()),
                current_lines=parsed.content.splitlines(),
                original_hunk="\n".join(parsed.patch_lines),
            )
            for parsed in parsed_files
        }
        self._allowed_paths = frozenset(self._files)

    async def run(self) -> FixProposal:
        """Execute bounded fix iterations until validators pass or an explicit stop condition fires."""
        attempts: list[AttemptRecord] = []
        saw_invalid_patch = False
        applied_patch = False
        notes: list[str] = []
        for attempt in range(1, self._max_iterations + 1):
            patch_text = await self._request_patch(attempts)
            if not patch_text.strip():
                attempts.append(
                    AttemptRecord(
                        attempt=attempt,
                        status="model_declined",
                        patch_summary="no patch",
                        note="model returned no patch",
                    )
                )
                notes.append("model_declined: no patch returned")
                return self._proposal(attempts, "model_declined", notes)
            parsed_patch = self._parse_patch(patch_text)
            if isinstance(parsed_patch, str):
                saw_invalid_patch = True
                attempts.append(
                    AttemptRecord(
                        attempt=attempt,
                        status="invalid_patch",
                        patch_summary=self._summarize_patch(patch_text),
                        note=parsed_patch,
                    )
                )
                continue
            updated_files = {path: list(file.current_lines) for path, file in self._files.items()}
            apply_error = self._apply_patch(parsed_patch, updated_files)
            if apply_error is not None:
                saw_invalid_patch = True
                attempts.append(
                    AttemptRecord(
                        attempt=attempt,
                        status="invalid_patch",
                        patch_summary=self._summarize_patch(patch_text),
                        note=apply_error,
                    )
                )
                continue
            for path, lines in updated_files.items():
                self._files[path].current_lines = lines
            applied_patch = True
            validator_result = await self._run_validators()
            status = "fixed" if validator_result.passed else "validation_failed"
            attempts.append(
                AttemptRecord(
                    attempt=attempt,
                    status=status,
                    patch_summary=self._summarize_patch(patch_text),
                    validator_result=validator_result,
                )
            )
            if validator_result.passed:
                notes.append(f"fixed: validators passed on attempt {attempt}")
                return self._proposal(attempts, "fixed", notes)
        if saw_invalid_patch and not applied_patch:
            notes.append("invalid_patch: iteration budget exhausted without an applicable patch")
            return self._proposal(attempts, "invalid_patch", notes)
        notes.append(f"degraded: budget exhausted after {self._max_iterations} attempt(s)")
        return self._proposal(attempts, "budget_exhausted", notes)

    @staticmethod
    def _resolve_max_iterations(default: int) -> int:
        raw = os.environ.get(_MAX_ITERATIONS_ENV)
        value = int(raw) if raw is not None else default
        if value < 1:
            raise ValueError(f"{_MAX_ITERATIONS_ENV} must be >= 1")
        return value

    async def _request_patch(self, attempts: Sequence[AttemptRecord]) -> str:
        prompt = self._build_prompt(attempts)
        call = self._model_client if callable(self._model_client) else getattr(self._model_client, "complete", None)
        if not callable(call):
            raise FixSessionError("model_client must be callable or expose complete()")
        result = call(system_prompt=_SYSTEM_PROMPT, user_prompt=prompt)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, str):
            return result
        if isinstance(result, Mapping):
            text = result.get("text", "")
            return text if isinstance(text, str) else ""
        text = getattr(result, "text", "")
        return text if isinstance(text, str) else ""

    def _proposal(self, attempts: Sequence[AttemptRecord], status: FixStatus, notes: Sequence[str]) -> FixProposal:
        return FixProposal(
            iterations=tuple(attempts),
            status=status,
            final_diff=self._render_diff(),
            notes=tuple(notes),
        )

    def _build_prompt(self, attempts: Sequence[AttemptRecord]) -> str:
        relevant_paths = {
            evidence.path
            for finding in self._findings
            for evidence in finding.evidence
            if evidence.path in self._allowed_paths
        }
        selected_paths = sorted(relevant_paths or self._allowed_paths)
        findings_block = "\n".join(
            f"- {finding.severity.value} {finding.title}: {finding.detail}" for finding in self._findings
        ) or "- no findings supplied"
        history_block = "\n".join(
            f"- attempt {attempt.attempt}: {attempt.status} ({self._attempt_outcome(attempt)})"
            for attempt in attempts
        ) or "- none"
        file_blocks = []
        for path in selected_paths:
            working = self._files[path]
            snippet = "\n".join(
                f"{index}: {line}" for index, line in enumerate(working.current_lines, start=1)
            ) or "<empty>"
            original_hunk = working.original_hunk or "<no original hunk available>"
            file_blocks.append(f"FILE {path}\nCURRENT SNIPPET\n{snippet}\nORIGINAL HUNK\n{original_hunk}")
        ci_log = self._ci_failure_log_text.strip() or "<none>"
        return (
            "Caller already sanitized any sensitive material; preserve that contract and do not invent secrets.\n"
            f"Allowed files: {', '.join(selected_paths)}\n"
            f"Max attempts: {self._max_iterations}\n"
            f"Findings:\n{findings_block}\n"
            f"CI failure log:\n{ci_log}\n"
            f"Prior attempt outcomes:\n{history_block}\n"
            f"Patch only the allowed files using unified diff against the CURRENT SNIPPET line numbers.\n\n"
            + "\n\n".join(file_blocks)
        )

    @staticmethod
    def _attempt_outcome(attempt: AttemptRecord) -> str:
        if attempt.note:
            return attempt.note
        if attempt.validator_result is not None:
            return attempt.validator_result.summary
        return "no validator"

    async def _run_validators(self) -> ValidationResult:
        summaries: list[str] = []
        for validator in self._validators:
            result = validator(self._render_diff(), self._current_files_text())
            if inspect.isawaitable(result):
                result = await result
            if not isinstance(result, ValidationResult):
                raise FixSessionError("validators must return ValidationResult")
            summaries.append(result.summary)
            if not result.passed:
                return ValidationResult(False, result.summary, details=result.details)
        return ValidationResult(True, "; ".join(summaries) or "validators passed")

    def _current_files_text(self) -> dict[str, str]:
        return {path: "\n".join(file.current_lines) for path, file in self._files.items()}

    def _parse_patch(self, patch_text: str) -> list[tuple[str, list[list[str]]]] | str:
        if patch_text.strip() and not parse_unified_diff(patch_text):
            return "patch failed structural parse"
        parsed: list[tuple[str, list[list[str]]]] = []
        current_path: str | None = None
        current_hunks: list[list[str]] = []
        current_lines: list[str] | None = None
        for raw in patch_text.splitlines():
            if raw.startswith("--- "):
                continue
            if raw.startswith("+++ "):
                raw_path = raw[4:].strip()
                current_path = raw_path[2:] if raw_path.startswith(("a/", "b/")) else raw_path
                if current_path == "/dev/null":
                    current_path = None
                current_hunks = []
                parsed.append((current_path or "", current_hunks))
                current_lines = None
                continue
            if _HUNK_HEADER.match(raw):
                if current_path is None:
                    return "patch hunk missing target file"
                current_lines = [raw]
                current_hunks.append(current_lines)
                continue
            if current_lines is not None:
                current_lines.append(raw)
        if not parsed:
            return "patch missing file headers"
        paths = {path for path, _ in parsed}
        if "" in paths:
            return "patch targets /dev/null, unsupported for fixer session"
        if not paths.issubset(self._allowed_paths):
            illegal = ", ".join(sorted(paths - self._allowed_paths))
            return f"patch touches files outside original diff: {illegal}"
        return parsed

    def _apply_patch(
        self,
        patch_files: Sequence[tuple[str, list[list[str]]]],
        updated_files: dict[str, list[str]],
    ) -> str | None:
        for path, hunks in patch_files:
            original = updated_files[path]
            rebuilt: list[str] = []
            last_index = 0
            for hunk in hunks:
                header = _HUNK_HEADER.match(hunk[0])
                if header is None:
                    return f"malformed hunk header for {path}"
                start_index = max(int(header.group("old")) - 1, 0)
                if start_index > len(original):
                    return f"hunk start out of range for {path}"
                rebuilt.extend(original[last_index:start_index])
                cursor = start_index
                for line in hunk[1:]:
                    marker = line[:1]
                    payload = line[1:]
                    if marker == " ":
                        if cursor >= len(original) or original[cursor] != payload:
                            return f"context mismatch while applying patch to {path}"
                        rebuilt.append(payload)
                        cursor += 1
                    elif marker == "-":
                        if cursor >= len(original) or original[cursor] != payload:
                            return f"deletion mismatch while applying patch to {path}"
                        cursor += 1
                    elif marker == "+":
                        rebuilt.append(payload)
                    else:
                        return f"unsupported patch line in {path}: {line}"
                last_index = cursor
            rebuilt.extend(original[last_index:])
            updated_files[path] = rebuilt
        return None

    def _render_diff(self) -> str:
        chunks: list[str] = []
        for path, working in self._files.items():
            diff_lines = list(
                difflib.unified_diff(
                    working.original_lines,
                    tuple(working.current_lines),
                    fromfile=f"a/{path}",
                    tofile=f"b/{path}",
                    lineterm="",
                )
            )
            if diff_lines:
                chunks.append("\n".join(diff_lines))
        return "\n".join(chunks)

    @staticmethod
    def _summarize_patch(patch_text: str) -> str:
        files = sum(1 for line in patch_text.splitlines() if line.startswith("+++ "))
        adds = sum(1 for line in patch_text.splitlines() if line.startswith("+") and not line.startswith("+++"))
        removes = sum(1 for line in patch_text.splitlines() if line.startswith("-") and not line.startswith("---"))
        return f"files={files} +{adds}/-{removes}"


def create_fix_session(
    *,
    diff_text: str,
    findings: Sequence[Finding],
    model_client: _SupportsComplete | object,
    validators: Sequence[FixValidator],
    ci_failure_log_text: str | None = None,
    max_iterations: int = 3,
) -> FixSession:
    """Create a FixSession for future worker/MCP wiring.

    Future wiring points: a worker post-review hook can pass the change diff, persisted findings,
    redacted CI log text, and repo-scoped validators; an MCP remediation tool can inject the same
    sanitized inputs plus a model client bound to the caller's policy envelope.
    """
    return FixSession(
        diff_text=diff_text,
        findings=findings,
        model_client=model_client,
        validators=validators,
        ci_failure_log_text=ci_failure_log_text,
        max_iterations=max_iterations,
    )
