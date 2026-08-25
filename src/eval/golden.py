"""Golden-set loading and recall scoring for harvested real-diff cases."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field, TypeAdapter


class ExpectedFinding(BaseModel):
    path: str
    title_regex: str
    line: int | None = None
    severity: str | None = None


class GoldenCase(BaseModel):
    id: str
    diff: str
    expected: list[ExpectedFinding]
    root: Path = Field(exclude=True)


class GoldenFinding(BaseModel):
    path: str
    title: str
    line: int | None = None
    severity: str | None = None


def load_findings_payload(payload: str) -> list[GoldenFinding]:
    return TypeAdapter(list[GoldenFinding]).validate_json(payload)


@dataclass(frozen=True, slots=True)
class MatchedFinding:
    expected: ExpectedFinding
    actual: GoldenFinding


@dataclass(frozen=True, slots=True)
class RecallScore:
    matched: list[MatchedFinding]
    unmatched: list[GoldenFinding]
    missed: list[ExpectedFinding]

    @property
    def recall(self) -> float:
        total_expected = len(self.matched) + len(self.missed)
        if total_expected == 0:
            return 1.0
        return round(len(self.matched) / total_expected, 4)


@dataclass(frozen=True, slots=True)
class GoldenReplayCaseResult:
    case: GoldenCase
    score: RecallScore


@dataclass(frozen=True, slots=True)
class GoldenReplayResult:
    cases: tuple[GoldenReplayCaseResult, ...]

    @property
    def matched_count(self) -> int:
        return sum(len(case.score.matched) for case in self.cases)

    @property
    def missed_count(self) -> int:
        return sum(len(case.score.missed) for case in self.cases)

    @property
    def unmatched_count(self) -> int:
        return sum(len(case.score.unmatched) for case in self.cases)

    @property
    def total_expected(self) -> int:
        return sum(len(case.case.expected) for case in self.cases)

    @property
    def recall(self) -> float:
        if self.total_expected == 0:
            return 1.0
        return round(self.matched_count / self.total_expected, 4)


def load_golden(root: Path) -> list[GoldenCase]:
    """Load benchmark/golden/<id>/{diff.patch,expected.json} cases."""
    cases: list[GoldenCase] = []
    for case_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        diff_path = case_dir / "diff.patch"
        expected_path = case_dir / "expected.json"
        if not diff_path.exists() or not expected_path.exists():
            raise ValueError(f"golden case missing required files: {case_dir}")
        cases.append(
            GoldenCase(
                id=case_dir.name,
                diff=diff_path.read_text(encoding="utf8"),
                expected=_expected_list_from_json(expected_path.read_text(encoding="utf8")),
                root=case_dir,
            )
        )
    return cases


def _expected_list_from_json(raw: str) -> list[ExpectedFinding]:
    return TypeAdapter(list[ExpectedFinding]).validate_json(raw)


def _matches(expected: ExpectedFinding, actual: GoldenFinding) -> bool:
    if expected.path != actual.path:
        return False
    if expected.line is not None and expected.line != actual.line:
        return False
    if expected.severity is not None and expected.severity != actual.severity:
        return False
    return bool(re.search(expected.title_regex, actual.title))


def score_against_expected(findings: list[GoldenFinding], expected: list[ExpectedFinding]) -> RecallScore:
    """Greedy one-to-one matching for expected descriptors against actual findings."""
    remaining = list(findings)
    matched: list[MatchedFinding] = []
    missed: list[ExpectedFinding] = []
    for exp in expected:
        hit_index = next((index for index, actual in enumerate(remaining) if _matches(exp, actual)), None)
        if hit_index is None:
            missed.append(exp)
            continue
        actual = remaining.pop(hit_index)
        matched.append(MatchedFinding(expected=exp, actual=actual))
    return RecallScore(matched=matched, unmatched=remaining, missed=missed)


def replay_golden(
    cases: list[GoldenCase],
    findings_source: Callable[[GoldenCase], list[GoldenFinding]],
) -> GoldenReplayResult:
    """Replay golden cases through a pluggable findings-source callable."""
    return GoldenReplayResult(
        cases=tuple(
            GoldenReplayCaseResult(case=case, score=score_against_expected(findings_source(case), case.expected))
            for case in cases
        )
    )
