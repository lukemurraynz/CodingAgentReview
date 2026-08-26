"""T006: domain model validation contracts."""

import pytest
from pydantic import ValidationError

from harness.models import (
    Change,
    ChangeType,
    Evidence,
    ExploitabilityMetadata,
    Finding,
    FindingCategory,
    GitProvider,
    LensResult,
    LensStatus,
    ReviewMetadata,
    ReviewRun,
    RiskAssessment,
    RiskLevel,
    RiskSignal,
    RunStatus,
    Severity,
)


def _change(**kwargs) -> Change:
    return Change(
        id="c1",
        provider=GitProvider.GITHUB,
        repo_id="org/repo",
        change_type=ChangeType.PULL_REQUEST,
        head_sha="abc123",
        **kwargs,
    )


class TestChange:
    def test_pr_requires_base_and_number(self):
        with pytest.raises(ValidationError, match="pr_number and base_sha"):
            _change()

    def test_pr_valid_with_fields(self):
        c = _change(base_sha="def456", pr_number=12)
        assert c.pr_number == 12

    def test_commit_without_pr_ok(self):
        c = Change(
            id="c2",
            provider=GitProvider.AZURE_DEVOPS,
            repo_id="proj/repo",
            change_type=ChangeType.COMMIT,
            head_sha="abc",
        )
        assert c.change_type == ChangeType.COMMIT


class TestLensResult:
    def test_failed_requires_error(self):
        with pytest.raises(ValidationError, match="must record their error"):
            LensResult(lens="security", status=LensStatus.FAILED)

    def test_completed_without_error_ok(self):
        r = LensResult(lens="security", status=LensStatus.COMPLETED)
        assert r.error is None


class TestReviewRunDegraded:
    def test_degraded_requires_reasons(self):
        with pytest.raises(ValidationError, match="explicit reasons"):
            ReviewRun(id="r1", change_id="c1", head_sha="a", status=RunStatus.DEGRADED)

    def test_degraded_explicit_ok(self):
        run = ReviewRun(
            id="r1",
            change_id="c1",
            head_sha="a",
            status=RunStatus.DEGRADED,
            coverage="partial_explicit",
            degraded_reasons=["budget_exhausted"],
        )
        assert run.coverage == "partial_explicit"

    def test_reasons_require_degraded_coverage(self):
        with pytest.raises(ValidationError):
            ReviewRun(id="r1", change_id="c1", head_sha="a", degraded_reasons=["budget_exhausted"])

    def test_review_metadata_round_trips(self):
        run = ReviewRun(
            id="r1",
            change_id="c1",
            head_sha="a",
            review_metadata=ReviewMetadata(
                declared_lenses=("security",),
                executed_lenses=("security",),
                unavailable_lenses=(),
                lens_versions={"security": "1"},
                prompt_version="v1",
            ),
        )
        assert run.review_metadata is not None
        assert run.review_metadata.declared_lenses == ("security",)


class TestRiskAssessment:
    def test_level_equals_max_signal(self):
        ra = RiskAssessment(
            repo_id="org/repo",
            change_id="c1",
            level=RiskLevel.CRITICAL,
            signals=[
                RiskSignal(name="boundary", level=RiskLevel.MEDIUM),
                RiskSignal(name="prod_target", level=RiskLevel.CRITICAL),
            ],
        )
        assert ra.level == RiskLevel.CRITICAL

    def test_level_must_match_signals(self):
        with pytest.raises(ValidationError, match="does not match"):
            RiskAssessment(
                repo_id="org/repo",
                change_id="c1",
                level=RiskLevel.LOW,
                signals=[RiskSignal(name="boundary", level=RiskLevel.HIGH)],
            )

    def test_empty_signals_any_level(self):
        ra = RiskAssessment(repo_id="org/repo", change_id="c1", level=RiskLevel.LOW)
        assert ra.signals == []


class TestFindingWaiverConsistency:
    def test_waived_requires_record(self):
        with pytest.raises(ValidationError, match="require a waiver"):
            Finding(
                id="f1",
                change_id="c1",
                repo_id="org/repo",
                category=FindingCategory.STRUCTURAL,
                severity=Severity.BLOCKER,
                title="t",
                evidence=[Evidence(path="a.py")],
                dedup_key="v1:x",
                status="waived",
            )

    def test_nonwaived_forbids_record(self):
        from harness.models import Waiver

        with pytest.raises(ValidationError, match="only valid on waived"):
            Finding(
                id="f1",
                change_id="c1",
                repo_id="org/repo",
                category=FindingCategory.STRUCTURAL,
                severity=Severity.BLOCKER,
                title="t",
                evidence=[Evidence(path="a.py")],
                dedup_key="v1:x",
                status="confirmed",
                waiver=Waiver(approver="x", rationale="y"),
            )

    def test_metadata_fields_default_to_empty_values(self):
        finding = Finding(
            id="f1",
            change_id="c1",
            repo_id="org/repo",
            category=FindingCategory.STRUCTURAL,
            severity=Severity.BLOCKER,
            title="t",
            evidence=[Evidence(path="a.py")],
            dedup_key="v1:x",
        )
        assert finding.reported_by == ()
        assert finding.not_flagged == ()
        assert finding.exploitability is None
        assert finding.second_opinion_lens is None

    def test_exploitability_metadata_round_trips(self):
        finding = Finding(
            id="f1",
            change_id="c1",
            repo_id="org/repo",
            category=FindingCategory.SECURITY,
            severity=Severity.HIGH,
            title="t",
            evidence=[Evidence(path="a.py")],
            dedup_key="v1:x",
            exploitability=ExploitabilityMetadata(
                level="reachable",
                reasons=("r1",),
                confidence=0.5,
            ),
        )
        assert finding.exploitability is not None
        assert finding.exploitability.level == "reachable"
