"""Offline auto-fix loop tests with fake model clients and validators."""

from __future__ import annotations

import pytest

from fixer import FixSession, ValidationResult
from harness.models import Evidence, Finding, FindingCategory, Severity


def _finding(path: str = "src/app.py") -> Finding:
    return Finding(
        id="f1",
        change_id="c1",
        repo_id="org/repo",
        category=FindingCategory.CORRECTNESS,
        severity=Severity.HIGH,
        title="Fix bug",
        detail="return value should be corrected",
        evidence=[Evidence(path=path, line_start=2, line_end=2, rule_id="correctness")],
        dedup_key=f"{path}:2:Fix bug",
    )


def _diff() -> str:
    return "\n".join(
        [
            "diff --git a/src/app.py b/src/app.py",
            "--- a/src/app.py",
            "+++ b/src/app.py",
            "@@ -1,2 +1,2 @@",
            " def run():",
            "-    return 'bad'",
            "+    return 'bad'",
        ]
    )


class _FakeModel:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.prompts: list[dict[str, str]] = []

    async def complete(self, *, system_prompt: str, user_prompt: str) -> str:
        self.prompts.append({"system": system_prompt, "user": user_prompt})
        return self._responses.pop(0) if self._responses else ""


def _validator_outcomes(results: list[ValidationResult]):
    pending = list(results)

    def _validate(_diff_text: str, files: dict[str, str]) -> ValidationResult:
        assert "src/app.py" in files
        return pending.pop(0)

    return _validate


@pytest.mark.asyncio
async def test_fix_session_happy_path_in_two_iterations() -> None:
    model = _FakeModel(
        [
            "\n".join(
                [
                    "--- a/src/app.py",
                    "+++ b/src/app.py",
                    "@@ -1,2 +1,2 @@",
                    " def run():",
                    "-    return 'bad'",
                    "+    return 'still_bad'",
                ]
            ),
            "\n".join(
                [
                    "--- a/src/app.py",
                    "+++ b/src/app.py",
                    "@@ -1,2 +1,2 @@",
                    " def run():",
                    "-    return 'still_bad'",
                    "+    return 'good'",
                ]
            ),
        ]
    )
    validator = _validator_outcomes(
        [
            ValidationResult(False, "tests still failing"),
            ValidationResult(True, "tests green"),
        ]
    )
    proposal = await FixSession(
        diff_text=_diff(),
        findings=[_finding()],
        model_client=model,
        validators=[validator],
    ).run()
    assert proposal.status == "fixed"
    assert len(proposal.iterations) == 2
    assert proposal.iterations[0].status == "validation_failed"
    assert proposal.iterations[1].status == "fixed"
    assert proposal.iterations[1].validator_result == ValidationResult(True, "tests green")
    assert "+    return 'good'" in proposal.final_diff
    assert "attempt 1: validation_failed" in model.prompts[1]["user"]


@pytest.mark.asyncio
async def test_fix_session_budget_exhausted_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESS_FIX_MAX_ITERATIONS", "2")
    model = _FakeModel(
        [
            "\n".join([
                "--- a/src/app.py",
                "+++ b/src/app.py",
                "@@ -1,2 +1,2 @@",
                " def run():",
                "-    return 'bad'",
                "+    return 'attempt_one'",
            ]),
            "\n".join([
                "--- a/src/app.py",
                "+++ b/src/app.py",
                "@@ -1,2 +1,2 @@",
                " def run():",
                "-    return 'attempt_one'",
                "+    return 'attempt_two'",
            ]),
        ]
    )
    validator = _validator_outcomes(
        [
            ValidationResult(False, "tests red"),
            ValidationResult(False, "tests still red"),
        ]
    )
    proposal = await FixSession(
        diff_text=_diff(),
        findings=[_finding()],
        model_client=model,
        validators=[validator],
    ).run()
    assert proposal.status == "budget_exhausted"
    assert proposal.notes == ("degraded: budget exhausted after 2 attempt(s)",)
    assert [record.attempt for record in proposal.iterations] == [1, 2]


@pytest.mark.asyncio
async def test_invalid_patch_records_retries_and_terminal_status() -> None:
    model = _FakeModel(["not a diff", "still not a diff"])
    proposal = await FixSession(
        diff_text=_diff(),
        findings=[_finding()],
        model_client=model,
        validators=[lambda *_: ValidationResult(True, "unused")],
        max_iterations=2,
    ).run()
    assert proposal.status == "invalid_patch"
    assert [record.status for record in proposal.iterations] == ["invalid_patch", "invalid_patch"]
    assert proposal.iterations[0].note == "patch failed structural parse"


@pytest.mark.asyncio
async def test_out_of_scope_file_patch_is_rejected() -> None:
    model = _FakeModel(
        [
            "\n".join(
                [
                    "--- a/src/other.py",
                    "+++ b/src/other.py",
                    "@@ -1 +1 @@",
                    "-value = 1",
                    "+value = 2",
                ]
            )
        ]
    )
    proposal = await FixSession(
        diff_text=_diff(),
        findings=[_finding()],
        model_client=model,
        validators=[lambda *_: ValidationResult(True, "unused")],
        max_iterations=1,
    ).run()
    assert proposal.status == "invalid_patch"
    assert proposal.iterations[0].note == "patch touches files outside original diff: src/other.py"


@pytest.mark.asyncio
async def test_model_returning_empty_patch_declines() -> None:
    proposal = await FixSession(
        diff_text=_diff(),
        findings=[_finding()],
        model_client=_FakeModel([""]),
        validators=[lambda *_: ValidationResult(True, "unused")],
    ).run()
    assert proposal.status == "model_declined"
    assert proposal.iterations[0].status == "model_declined"
    assert proposal.final_diff == ""


@pytest.mark.asyncio
async def test_iteration_records_capture_patch_summary_and_validator_result() -> None:
    model = _FakeModel(
        [
            "\n".join(
                [
                    "--- a/src/app.py",
                    "+++ b/src/app.py",
                    "@@ -1,2 +1,2 @@",
                    " def run():",
                    "-    return 'bad'",
                    "+    return 'good'",
                ]
            )
        ]
    )
    proposal = await FixSession(
        diff_text=_diff(),
        findings=[_finding()],
        model_client=model,
        validators=[lambda *_: ValidationResult(True, "ci green")],
        ci_failure_log_text="Traceback redacted",
        max_iterations=1,
    ).run()
    record = proposal.iterations[0]
    assert record.attempt == 1
    assert record.patch_summary == "files=1 +1/-1"
    assert record.validator_result == ValidationResult(True, "ci green")
    assert proposal.notes == ("fixed: validators passed on attempt 1",)
