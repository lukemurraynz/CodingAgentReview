"""Worker runner end-to-end seams with local fakes."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from harness.models import Evidence, Finding, FindingCategory, LensStatus, ReviewRun, RunStatus, Severity
from lenses.llm import LensUnavailable
from providers.base import PROMPT_VERSION
from providers.formatting import build_comment_body
from worker import runner
from worker.suppression import reported_by


def _event_payload(*, include_classification: bool = False) -> dict[str, object]:
    payload: dict[str, object] = {
        "changeId": "change-1",
        "headSha": "abc123",
        "repoId": "org/repo",
        "provider": "github",
    }
    if include_classification:
        payload["classification"] = "docs"
    return payload


class FakeAdapter:
    def __init__(self, *, diff: str = "", post_error: Exception | None = None) -> None:
        self.diff = diff
        self.post_error = post_error
        self.posted: list[tuple[object, list[Finding], object]] = []
        self.summaries: list[tuple[object, list[Finding], object]] = []

    async def fetch_diff(self, change):
        return self.diff

    async def post_annotations(self, change, findings, report=None):
        if self.post_error is not None:
            raise self.post_error
        self.posted.append((change, findings, report))

    async def upsert_summary_comment(self, change, findings, report=None):
        if self.post_error is not None:
            raise self.post_error
        self.summaries.append((change, findings, report))


class FakeLens:
    def __init__(self, name: str, *, findings: list[Finding] | None = None, error: Exception | None = None, usage=None):
        self.name = name
        self._findings = findings or []
        self._error = error
        self.last_usage = usage
        self.seen_symbol_index = None

    async def run(self, ctx):
        self.seen_symbol_index = ctx.symbol_index
        if self._error is not None:
            raise self._error
        return list(self._findings)


@dataclass
class FakeRepository:
    runs: list[ReviewRun] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    historical_runs: list[dict[str, object]] = field(default_factory=list)
    run_metadata: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    async def put_run(self, run: ReviewRun) -> None:
        self.runs.append(run.model_copy(deep=True))

    async def put_finding(self, finding: Finding) -> None:
        self.findings.append(finding.model_copy(deep=True))

    async def query_runs_for_change(self, change_id: str) -> list[dict[str, object]]:
        del change_id
        return list(self.historical_runs)

    async def put_run_metadata(self, run_id: str, metadata: dict[str, object]) -> None:
        self.run_metadata.append((run_id, metadata))


def _make_finding(severity: Severity = Severity.HIGH) -> Finding:
    return Finding(
        id=f"f-{severity.value}",
        change_id="change-1",
        repo_id="org/repo",
        category=FindingCategory.SECURITY,
        severity=severity,
        title="bad thing",
        detail="detail",
        evidence=[Evidence(path="src/app.py", line_start=3, rule_id="structural.rule")],
        dedup_key=f"dedup-{severity.value}",
        status="confirmed",
    )


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in (
        "HARNESS_DRY_RUN",
        "HARNESS_BUDGET_INPUT_TOKENS",
        "HARNESS_BUDGET_OUTPUT_TOKENS",
        "HARNESS_BUDGET_COMPUTE_MS",
        "HARNESS_COSMOS_ENDPOINT",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture()
def fake_state(monkeypatch):
    repository = FakeRepository()
    adapter = FakeAdapter(
        diff="diff --git a/src/app.py b/src/app.py\n+++ b/src/app.py\n@@ -0,0 +1 @@\n+print('x')"
    )
    monkeypatch.setitem(runner.ADAPTERS, "github", adapter)
    monkeypatch.setattr(runner, "_make_repository", lambda: repository)
    monkeypatch.setattr(
        runner,
        "LLM_LENSES",
        {},
    )
    monkeypatch.setattr(
        runner,
        "LENS_REGISTRY",
        {
            "structural": FakeLens("structural"),
            "production_validation": FakeLens("production_validation"),
            "correctness": FakeLens("correctness"),
            "security": FakeLens("security"),
        },
    )
    return repository, adapter


async def test_happy_path_persists_and_posts(fake_state, monkeypatch):
    repository, adapter = fake_state
    finding = _make_finding()
    monkeypatch.setattr(
        runner,
        "LENS_REGISTRY",
        {
            "structural": FakeLens("structural", findings=[finding]),
            "production_validation": FakeLens("production_validation"),
        },
    )

    run = await runner.execute_review(_event_payload(include_classification=True))
    assert run.status == RunStatus.COMPLETED
    assert repository.findings and repository.findings[0].title == "bad thing"
    assert adapter.posted
    assert adapter.summaries
    _, posted_findings, report = adapter.posted[0]
    assert posted_findings[0].severity == Severity.HIGH
    assert report.gate.status == "block"


async def test_dry_run_skips_annotation_post(fake_state, monkeypatch):
    _, adapter = fake_state
    monkeypatch.setenv("HARNESS_DRY_RUN", "1")
    run = await runner.execute_review(_event_payload())
    assert run.status == RunStatus.COMPLETED
    assert adapter.posted == []


async def test_budget_env_marks_explicit_degraded(fake_state, monkeypatch):
    repository, _ = fake_state
    monkeypatch.setenv("HARNESS_BUDGET_INPUT_TOKENS", "1")
    monkeypatch.setattr(
        runner,
        "LENS_REGISTRY",
        {
            "structural": FakeLens("structural", usage={"input_tokens": 5, "output_tokens": 0}),
            "production_validation": FakeLens("production_validation"),
        },
    )
    run = await runner.execute_review(_event_payload())
    assert run.status == RunStatus.DEGRADED
    assert run.coverage == "partial_explicit"
    assert "budget_exhausted" in run.degraded_reasons
    assert any(result.status == LensStatus.SKIPPED_BUDGET for result in repository.runs[-1].lens_results)


async def test_consecutive_failures_trigger_stop_condition(fake_state, monkeypatch):
    repository, _ = fake_state
    error = RuntimeError("boom")
    monkeypatch.setattr(
        runner,
        "LENS_REGISTRY",
        {
            "structural": FakeLens("structural", error=error),
            "production_validation": FakeLens("production_validation", error=error),
            "correctness": FakeLens("correctness", error=error),
            "security": FakeLens("security"),
        },
    )
    run = await runner.execute_review(_event_payload())
    assert run.status == RunStatus.DEGRADED
    assert any(reason.startswith("stop_condition:consecutive_failures") for reason in run.degraded_reasons)
    failed = [result for result in repository.runs[-1].lens_results if result.status == LensStatus.FAILED]
    assert len(failed) == 3


async def test_annotation_failure_degrades_run(fake_state):
    repository, adapter = fake_state
    adapter.post_error = RuntimeError("post failed")
    run = await runner.execute_review(_event_payload())
    assert run.status == RunStatus.DEGRADED
    assert "annotation_failed:github" in run.degraded_reasons
    assert repository.runs[-1].status == RunStatus.DEGRADED


async def test_unavailable_lens_forces_degraded_coverage(fake_state, monkeypatch):
    repository, adapter = fake_state
    monkeypatch.setattr(
        runner,
        "LENS_REGISTRY",
        {
            "structural": FakeLens("structural"),
            "production_validation": FakeLens("production_validation", error=LensUnavailable("model off")),
        },
    )
    run = await runner.execute_review(_event_payload(include_classification=True))
    assert run.status == RunStatus.DEGRADED
    assert run.coverage == "partial_explicit"
    assert "model_unavailable:production_validation" in run.degraded_reasons
    assert adapter.summaries[0][2].gate.status == "degraded"
    assert adapter.summaries[0][2].lens_coverage == "lenses: 2 declared, 2 reported, 1 unavailable"


async def test_duplicate_findings_merge_contributing_lenses(fake_state, monkeypatch):
    repository, adapter = fake_state
    shared = _make_finding()
    sibling = shared.model_copy(deep=True)
    sibling.id = "f-high-2"
    sibling.evidence[0].rule_id = "security.llm"
    monkeypatch.setattr(
        runner,
        "LENS_REGISTRY",
        {
            "structural": FakeLens("structural", findings=[shared]),
            "production_validation": FakeLens("production_validation", findings=[sibling]),
        },
    )
    run = await runner.execute_review(_event_payload(include_classification=True))
    del run
    summary_findings = adapter.summaries[0][1]
    assert len(summary_findings) == 1
    assert reported_by(summary_findings[0]) == ("structural", "security")


async def test_run_stores_review_metadata_on_model(fake_state, monkeypatch):
    del monkeypatch
    repository, _ = fake_state

    run = await runner.execute_review(_event_payload())

    assert run.review_metadata is not None
    assert repository.runs[-1].review_metadata is not None


async def test_run_metadata_stamps_versions_and_prompt_version(fake_state, monkeypatch):
    repository, _ = fake_state
    monkeypatch.setattr(
        runner,
        "LENS_REGISTRY",
        {
            "structural": FakeLens("structural"),
            "production_validation": FakeLens("production_validation"),
        },
    )
    await runner.execute_review(_event_payload(include_classification=True))
    _, metadata = repository.run_metadata[-1]
    assert metadata["prompt_version"] == PROMPT_VERSION
    assert metadata["lens_versions"] == {"structural": "1", "production_validation": "1"}
    assert metadata["declared_lenses"] == ["structural", "production_validation"]


async def test_summary_report_includes_scope_honesty(fake_state, monkeypatch):
    _, adapter = fake_state
    finding = _make_finding()
    monkeypatch.setattr(
        runner,
        "LENS_REGISTRY",
        {
            "structural": FakeLens("structural", findings=[finding]),
            "production_validation": FakeLens("production_validation"),
        },
    )
    await runner.execute_review(_event_payload(include_classification=True))
    report = adapter.summaries[0][2]
    body = build_comment_body(adapter.summaries[0][1], report)
    assert "### Verdict" in body
    assert "### Deliberately not flagged" in body
    assert report.not_flagged


async def test_only_blocker_and_high_post_inline_annotations(fake_state, monkeypatch):
    _, adapter = fake_state
    high = _make_finding(Severity.HIGH)
    medium = _make_finding(Severity.MEDIUM)
    medium.id = "f-medium"
    monkeypatch.setattr(
        runner,
        "LENS_REGISTRY",
        {
            "structural": FakeLens("structural", findings=[high, medium]),
            "production_validation": FakeLens("production_validation"),
        },
    )
    await runner.execute_review(_event_payload(include_classification=True))
    assert len(adapter.posted[0][1]) == 1
    assert adapter.posted[0][1][0].severity == Severity.HIGH


async def test_superseded_run_marking(fake_state):
    repository, _ = fake_state
    previous = ReviewRun(id="run-old", change_id="change-1", head_sha="oldsha", status=RunStatus.RUNNING)
    repository.historical_runs = [previous.model_dump(mode="json")]
    await runner.execute_review(_event_payload())
    assert repository.runs[0].status == RunStatus.SUPERSEDED


async def test_budget_env_output_tokens_can_exhaust(fake_state, monkeypatch):
    monkeypatch.setenv("HARNESS_BUDGET_OUTPUT_TOKENS", "1")
    monkeypatch.setattr(
        runner,
        "LENS_REGISTRY",
        {
            "structural": FakeLens("structural", usage={"input_tokens": 0, "output_tokens": 5}),
            "production_validation": FakeLens("production_validation"),
        },
    )
    run = await runner.execute_review(_event_payload())
    assert run.status == RunStatus.DEGRADED
    assert "budget_exhausted" in run.degraded_reasons


async def test_budget_env_compute_ms_can_exhaust(fake_state, monkeypatch):
    monkeypatch.setenv("HARNESS_BUDGET_COMPUTE_MS", "1")
    monkeypatch.setattr(
        runner,
        "LENS_REGISTRY",
        {
            "structural": FakeLens("structural", usage={"input_tokens": 0, "output_tokens": 0}),
            "production_validation": FakeLens("production_validation"),
        },
    )
    clock = iter([0.0, 0.01, 0.02, 0.03])
    import worker.runner_lenses as runner_lenses

    monkeypatch.setattr(runner_lenses.time, "monotonic", lambda: next(clock, 0.03))
    run = await runner.execute_review(_event_payload())
    assert run.status == RunStatus.DEGRADED
    assert "budget_exhausted" in run.degraded_reasons


async def test_runner_builds_symbol_index_once_and_attaches_it(fake_state, monkeypatch, tmp_path):
    repository, _ = fake_state
    (tmp_path / "startup.py").write_text(
        "from di import container\nclass RepoService:\n    pass\n\ncontainer.add_singleton(RepoService, RepoService)\n",
        encoding="utf-8",
    )
    structural = FakeLens("structural")
    production_validation = FakeLens("production_validation")
    monkeypatch.setenv("HARNESS_REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(
        runner,
        "LENS_REGISTRY",
        {
            "structural": structural,
            "production_validation": production_validation,
        },
    )

    run = await runner.execute_review(_event_payload(include_classification=True))

    assert run.status == RunStatus.COMPLETED
    assert structural.seen_symbol_index is not None
    assert production_validation.seen_symbol_index is not None
    assert structural.seen_symbol_index is production_validation.seen_symbol_index
    assert structural.seen_symbol_index.registrations["RepoService"][0] == ("startup.py", 5)
    assert "symbol_index_unavailable" not in run.degraded_reasons


async def test_runner_degrades_when_symbol_index_root_is_unreadable(fake_state, monkeypatch):
    monkeypatch.setenv("HARNESS_REPO_ROOT", "does-not-exist")

    run = await runner.execute_review(_event_payload(include_classification=True))

    assert run.status == RunStatus.DEGRADED
    assert "symbol_index_unavailable" in run.degraded_reasons
